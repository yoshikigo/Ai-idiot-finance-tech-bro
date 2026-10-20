"""台灣總經：景氣對策信號、經濟成長率、CPI／核心 CPI、失業率、重貼現率、M1B／M2、隔夜拆款利率。

Sources (all official, no key):
- 國發會「景氣指標及燈號」 (data.gov.tw dataset 6099): a ZIP of CSVs; the download URL is resolved from the
  data.gov.tw v2 API each day, so a re-upload doesn't break us. 燈號 is derived from the score with NDC's bands.
- 主計總處 SDMX-JSON (nstatdb.dgbas.gov.tw): GDP by expenditure (A018102060, quarterly; 經濟成長率 = real
  chain-linked GDP vs. the same quarter a year earlier), CPI basic groups (A030101019, monthly).
  Core CPI and unemployment tables are looked up by series *name* across candidate tables, because the table
  codes and filter syntax aren't documented; `diag()` reports what was found where.
- 中央銀行 web tables (cbc.gov.tw lp-*.html): 重貼現率 changes, M2 / M1B 年增率, 金融業隔夜拆款利率.
- 勞動部 每月國內主要經濟指標 (data.gov.tw 13228) as the unemployment fallback (it stopped at 2026-05; the date
  is shown so staleness is visible).
"""
from __future__ import annotations

import csv
import datetime as dt
import html as htmllib
import io
import json
import math
import re
import time
import zipfile

import pandas as pd

from . import market

DATAGOV = "https://data.gov.tw/api/v2/rest/dataset/{}"
SDMX = "https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?sdmx/{}"
CBC = "https://www.cbc.gov.tw/tw/lp-{}-1.html"
HEADERS = {"User-Agent": "fin-terminal (python-httpx)", "Accept": "*/*", "Accept-Language": "zh-TW,zh;q=0.9"}
TTL = 6 * 3600

# id → (中文名, 單位, 分組, 說明, 頻率)
SERIES = {
    "SIGNAL": ("景氣對策信號（分數）", "分", "景氣", "國發會；9–16 藍燈、17–22 黃藍燈、23–31 綠燈、32–37 黃紅燈、38–45 紅燈", "M"),
    "LEADING": ("領先指標綜合指數", "", "景氣", "國發會；領先景氣約 3–6 個月", "M"),
    "COINCIDENT": ("同時指標綜合指數", "", "景氣", "國發會；反映當下景氣", "M"),
    "GDP": ("經濟成長率", "%", "景氣", "主計總處；實質 GDP（連鎖實質值）較上年同季", "Q"),
    "CPI": ("CPI 年增率", "%", "物價", "主計總處；消費者物價總指數較上年同月", "M"),
    "CORE": ("核心 CPI 年增率", "%", "物價", "主計總處；不含蔬果及能源", "M"),
    "UNEMP": ("失業率", "%", "就業", "主計總處人力資源調查（季節未調整）", "M"),
    "DISCOUNT": ("央行重貼現率", "%", "貨幣與利率", "中央銀行；每季理監事會決定", "D"),
    "OVERNIGHT": ("金融業隔夜拆款利率", "%", "貨幣與利率", "中央銀行；每日加權平均", "D"),
    "M1B": ("M1B 年增率", "%", "貨幣與利率", "中央銀行；活期性存款＋通貨，反映股市資金動能", "M"),
    "M2": ("M2 年增率", "%", "貨幣與利率", "中央銀行；廣義貨幣供給", "M"),
}
GROUPS = ["景氣", "物價", "就業", "貨幣與利率"]
STEP = {"DISCOUNT"}  # changes only on decision dates → draw as steps
LIGHTS = [(38, "紅燈", "#ff4d4f"), (32, "黃紅燈", "#ff9f40"), (23, "綠燈", "#3ad67a"), (17, "黃藍燈", "#d6c43a"), (0, "藍燈", "#4ba3ff")]

_diag: dict[str, str] = {}
_neg: dict[str, tuple[float, str]] = {}  # failed source → (until, why): don't retry a dead source for 30 min


def light(score: float | None) -> dict | None:
    if score is None or not math.isfinite(score):
        return None
    for lo, name, color in LIGHTS:
        if score >= lo:
            return {"name": name, "color": color}
    return None


def _get(url: str, timeout: float = 40, ttl: float = 12 * 3600) -> bytes:
    from .twhttp import fetch
    return fetch(url, timeout=timeout, ttl=ttl, disk=True, headers=HEADERS)


def _num(x) -> float | None:
    """'r 1.73', '  p     57,311', '…', '-' → float or None (r＝修正、p＝初步)."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(float(x)) else None
    t = re.sub(r"[^0-9.\-]", "", str(x).replace(",", ""))
    if t in ("", "-", ".", "-."):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _month(x) -> pd.Timestamp | None:
    t = str(x).strip()
    m = re.match(r"^(\d{2,3})年\s*(\d{1,2})月", t)
    if m:
        return pd.Timestamp(int(m.group(1)) + 1911, int(m.group(2)), 1)
    m = re.match(r"^(\d{4})\D?(\d{1,2})(?:\D|$)", t) or re.match(r"^(\d{4})(\d{2})$", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1)
    return None


def _day(x) -> pd.Timestamp | None:
    m = re.match(r"^(\d{4})\D(\d{1,2})\D(\d{1,2})", str(x).strip())
    return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _series(d: dict) -> pd.Series:
    s = pd.Series(d, dtype=float).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


# ------------------------------------------------------------------ 國發會 景氣指標及燈號 (ZIP of CSVs)
def parse_ndc_csv(text: str) -> dict[str, pd.Series]:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    hi = next((k for k, r in enumerate(rows[:10]) if r and re.search(r"date|日期|年月|時間", r[0], re.I)), None)
    if hi is None:
        return {}
    head = [c.strip() for c in rows[hi]]
    out: dict[str, pd.Series] = {}

    def col(j):
        return _series({t: _num(r[j]) for r in rows[hi + 1:] if len(r) > j and (t := _month(r[0])) is not None})
    for j, name in enumerate(head[1:], 1):
        n = name.replace(" ", "")
        key = None
        if "綜合判斷分數" in n or ("景氣對策信號" in n and "燈" not in n) or n in ("對策信號(分)", "景氣對策信號(分)"):
            key = "SIGNAL"
        elif "領先指標綜合指數" in n and "不含趨勢" not in n:
            key = "LEADING"
        elif "同時指標綜合指數" in n and "不含趨勢" not in n:
            key = "COINCIDENT"
        if key and key not in out:
            s = col(j)
            if key == "SIGNAL":
                s = s[(s >= 9) & (s <= 45)]
            if len(s):
                out[key] = s
    return out


def ndc() -> dict[str, pd.Series]:
    def load():
        meta = json.loads(_get(DATAGOV.format(6099), ttl=24 * 3600))["result"]
        urls = [r.get("resourceDownloadUrl") for r in meta.get("distribution", []) if r.get("resourceDownloadUrl")]
        if not urls:
            raise RuntimeError("data.gov.tw 6099 沒有下載網址")
        body = _get(urls[0], timeout=60)
        out: dict[str, pd.Series] = {}
        names = []
        from .twhttp import decode_tw
        if body[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(body))
            for n in z.namelist():
                names.append(n)
                if n.lower().endswith(".csv"):
                    for k, s in parse_ndc_csv(decode_tw(z.read(n))).items():
                        if k not in out or len(s) > len(out[k]):
                            out[k] = s
        else:
            out = parse_ndc_csv(decode_tw(body))
        _diag["ndc"] = f"ZIP 內 {len(names)} 檔（{', '.join(names[:8])}）→ 找到 {', '.join(out) or '無'}"
        return out
    return _guard("ndc", load)


# ------------------------------------------------------------------ 主計總處 SDMX-JSON
def parse_sdmx(d: dict) -> list[tuple[list[str], pd.Series]]:
    """[(series dimension value names, Series by period start)] — monthly 'YYYY-Mn', quarterly 'YYYY-Qn';
    annual 'YYYY' periods are skipped."""
    data = d.get("data", d)
    st = data.get("structure", {})
    dims = st.get("dimensions", {})
    sdims = dims.get("series", [])
    obs_vals = (dims.get("observation") or [{}])[0].get("values", [])
    times = []
    for v in obs_vals:
        pid = str(v.get("id", ""))
        m = re.match(r"^(\d{4})-?M(\d{1,2})$", pid)
        q = re.match(r"^(\d{4})-?Q([1-4])$", pid)
        times.append(pd.Timestamp(int(m.group(1)), int(m.group(2)), 1) if m else
                     pd.Timestamp(int(q.group(1)), 3 * int(q.group(2)) - 2, 1) if q else None)
    out = []
    for key, ser in (data.get("dataSets") or [{}])[0].get("series", {}).items():
        idx = [int(x) for x in key.split(":")]
        names = []
        for dim, k in zip(sdims, idx):
            vals = dim.get("values", [])
            names.append(str(vals[k].get("name", "")) if k < len(vals) else "")
        pts = {}
        for o, v in ser.get("observations", {}).items():
            o = int(o)
            if o < len(times) and times[o] is not None:
                pts[times[o]] = _num(v[0] if isinstance(v, list) and v else v)
        s = _series(pts)
        if len(s):
            out.append((names, s))
    return out


SDMX_VARIANTS = ["", "&startTime=2000-M1", "/all", "/1.M&startTime=2000-M1", "/1.Q&startTime=2000-Q1"]


def sdmx(funid: str) -> list[tuple[list[str], pd.Series]]:
    """Fetch one table, trying the plain query first and then filter forms (some tables answer 參數錯誤 bare)."""
    def load():
        why = []
        for v in SDMX_VARIANTS:
            try:
                body = _get(SDMX.format(funid + v), timeout=90)
            except Exception as e:  # noqa: BLE001
                why.append(f"{v or '基本'}: {type(e).__name__}")
                continue
            if body.lstrip()[:1] != b"{":
                why.append(f"{v or '基本'}: {body[:30].decode('utf-8', 'replace')}")
                continue
            got = parse_sdmx(json.loads(body))
            if got:
                _diag[f"sdmx:{funid}"] = f"OK（{v or '基本查詢'}，{len(got)} 個序列）"
                return got
            why.append(f"{v or '基本'}: 0 序列")
        raise RuntimeError(f"主計總處 {funid}：" + "；".join(why))
    return _guard(f"sdmx:{funid}", load)


def _pick(tables: list[str], want, label: str) -> pd.Series:
    """First series (across candidate tables) whose dimension names satisfy want(names)."""
    errs = []
    for f in tables:
        try:
            got = sdmx(f)
        except Exception as e:  # noqa: BLE001
            errs.append(str(e)[:120])
            continue
        hits = [(n, s) for n, s in got if want(n)]
        if hits:
            n, s = max(hits, key=lambda x: len(x[1]))
            _diag[label] = f"{f}：{' / '.join(n)}"
            return s
        errs.append(f"{f} 沒有符合的序列")
    raise RuntimeError("；".join(errs) or "沒有來源")


def _yoy(s: pd.Series, lag: int) -> pd.Series:
    s = s.sort_index()
    return ((s / s.shift(lag) - 1) * 100).dropna()


def gdp() -> pd.Series:
    def want(n):
        j = " ".join(n)
        return "連鎖實質" in j and re.search(r"GDP|國內生產毛額", j) and "GNI" not in j and "平均每人" not in j
    lvl = _pick(["A018102060"], want, "gdp")
    full = pd.date_range(lvl.index.min(), lvl.index.max(), freq="QS")
    return _yoy(lvl.reindex(full), 4)


def _monthly_yoy(lvl: pd.Series) -> pd.Series:
    full = pd.date_range(lvl.index.min(), lvl.index.max(), freq="MS")
    return _yoy(lvl.reindex(full), 12)


CPI_TABLES = ["A030101019", "A030101015"]


def cpi() -> pd.Series:
    return _monthly_yoy(_pick(CPI_TABLES, lambda n: bool(n) and n[-1].replace(" ", "") == "總指數", "cpi"))


def core() -> pd.Series:
    return _monthly_yoy(_pick(CPI_TABLES, lambda n: any(re.search(r"不含(新鮮)?蔬果.*能源|核心", x) for x in n), "core"))


UNEMP_TABLES = ["A046101010", "A046101020", "A046102010"]


def unemp() -> pd.Series:
    def want(n):  # the overall rate, not a breakdown by sex / age / education
        return any("失業率" in x for x in n) and not any(re.search(r"男|女|歲|國中|高中|高職|大專|大學|研究所|地區|縣|市", x)
                                                         for x in n)
    try:
        return _pick(UNEMP_TABLES, want, "unemp")
    except Exception as e:  # noqa: BLE001
        first = str(e)
    # fallback: 勞動部 每月國內主要經濟指標 (last 13 months only)
    def load():
        meta = json.loads(_get(DATAGOV.format(13228), ttl=24 * 3600))["result"]
        url = next(r["resourceDownloadUrl"] for r in meta["distribution"] if r.get("resourceFormat", "").upper() == "JSON")
        rows = json.loads(_get(url))
        s = _series({t: _num(r.get("失業率（百分比）")) for r in rows if (t := _month(r.get("日期（月別）", ""))) is not None})
        _diag["unemp"] = f"勞動部 13228（主計總處表格找不到：{first[:100]}）"
        return s
    return _guard("mol13228", load)


# ------------------------------------------------------------------ 中央銀行 web tables
def parse_cbc_table(text: str) -> tuple[str, list[list[str]]]:
    title = re.search(r"<title>(.*?)</title>", text, re.S)
    rows = []
    m = re.search(r"<table.*?</table>", text, re.S)
    if m:
        for r in re.findall(r"<tr.*?</tr>", m.group(0), re.S):
            cells = [htmllib.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)]
            if cells:
                rows.append(cells)
    return (htmllib.unescape(title.group(1)).strip() if title else ""), rows


def cbc_page(pid: int, must: str) -> list[list[str]]:
    """Rows of the first table of lp-<pid>, verified by the page title containing `must`."""
    from .twhttp import decode_tw
    title, rows = parse_cbc_table(decode_tw(_get(CBC.format(pid), ttl=3 * 3600)))
    if must not in title.replace(" ", ""):
        raise RuntimeError(f"央行 lp-{pid} 不是「{must}」（{title[-30:]}）")
    return rows[1:]


def discount() -> pd.Series:
    return _guard("cbc:discount", lambda: _series({t: _num(r[1]) for r in cbc_page(640, "重貼現率")
                                                  if len(r) > 1 and (t := _day(r[0])) is not None}))


def overnight() -> pd.Series:
    return _guard("cbc:overnight", lambda: _series({t: _num(r[1]) for r in cbc_page(641, "拆款")
                                                   if len(r) > 1 and (t := _day(r[0])) is not None}))


def m2() -> pd.Series:
    return _guard("cbc:m2", lambda: _series({t: _num(r[1]) for r in cbc_page(643, "M2")
                                            if len(r) > 1 and (t := _month(r[0])) is not None}))


M1B_PAGES = [642, 644, 645, 646, 639]


def m1b() -> pd.Series:
    def load():
        errs = []
        for pid in M1B_PAGES:
            try:
                rows = cbc_page(pid, "M1B")
            except Exception as e:  # noqa: BLE001
                errs.append(str(e)[:60])
                continue
            _diag["m1b"] = f"央行 lp-{pid}"
            return _series({t: _num(r[1]) for r in rows if len(r) > 1 and (t := _month(r[0])) is not None})
        raise RuntimeError("央行網站找不到 M1B 年增率頁面（" + "；".join(errs[:3]) + "）")
    return _guard("cbc:m1b", load)


# ------------------------------------------------------------------ public API
def _guard(key: str, load):
    """Cache 6 h; after a failure, remember it for 30 min so the page doesn't wait on a dead source each time."""
    neg = _neg.get(key)
    if neg and time.time() < neg[0]:
        raise RuntimeError(neg[1])
    try:
        return market.cached(f"twmacro:{key}", TTL, load)
    except Exception as e:  # noqa: BLE001
        why = str(e)[:300] or type(e).__name__
        _neg[key] = (time.time() + 1800, why)
        _diag[key] = "失敗：" + why
        raise


def raw(sid: str) -> pd.Series:
    if sid not in SERIES:
        raise KeyError(sid)
    if market.FAKE:
        return _fake(sid)
    if sid in ("SIGNAL", "LEADING", "COINCIDENT"):
        got = ndc()
        if sid not in got:
            raise RuntimeError(f"國發會資料裡找不到「{SERIES[sid][0]}」欄位")
        return got[sid]
    return {"GDP": gdp, "CPI": cpi, "CORE": core, "UNEMP": unemp, "DISCOUNT": discount, "OVERNIGHT": overnight,
            "M1B": m1b, "M2": m2}[sid]()


def _ago(s: pd.Series, days: int):
    p = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
    return float(p.iloc[-1]) if len(p) else None


def _fmt_date(sid: str, t: pd.Timestamp) -> str:
    f = SERIES[sid][4]
    return f"{t.year}Q{(t.month - 1) // 3 + 1}" if f == "Q" else t.strftime("%Y-%m") if f == "M" else t.strftime("%Y-%m-%d")


def snapshot(ids: list[str] | None = None) -> list[dict]:
    out = []
    for sid in ids or list(SERIES):
        name, unit, group, note, freq = SERIES[sid]
        base = {"id": sid, "name": name, "unit": unit, "group": group, "note": note, "freq": freq}
        try:
            s = raw(sid)
        except Exception as e:  # noqa: BLE001
            out.append({**base, "error": str(e)[:200]})
            continue
        if not len(s):
            out.append({**base, "error": "沒有資料"})
            continue
        last = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) > 1 else None
        y1 = _ago(s, 360)
        tail = s[s.index >= s.index[-1] - pd.Timedelta(days=1100)]
        spark = [round(float(x), 4) for x in tail.iloc[::max(1, len(tail) // 60)]]
        row = {**base, "date": _fmt_date(sid, s.index[-1]), "value": last, "prev": prev,
               "chg": None if prev is None else last - prev, "y1": y1, "spark": spark}
        if sid == "SIGNAL":
            row["light"] = light(last)
            row["prev_light"] = light(prev)
        if sid == "DISCOUNT":
            row["changed"] = s.index[-1].strftime("%Y-%m-%d")
            row["date"] = dt.date.today().isoformat()
        out.append(row)
    return out


def history(sid: str, years: float = 10) -> dict:
    s = raw(sid)
    if sid in STEP and len(s):  # extend the last decision to today so the step reaches the right edge
        s = pd.concat([s, pd.Series([float(s.iloc[-1])], index=[pd.Timestamp(dt.date.today())])])
        s = s[~s.index.duplicated(keep="first")]
    if years and years < 200 and len(s):
        cut = s.index[-1] - pd.Timedelta(days=int(365.25 * years))
        before = s[s.index < cut]
        s = s[s.index >= cut]
        if sid in STEP and len(before):  # keep the level that was in force at the left edge
            s = pd.concat([pd.Series([float(before.iloc[-1])], index=[cut]), s])
    name, unit, group, note, freq = SERIES[sid]
    pts = [{"time": t.strftime("%Y-%m-%d"), "value": round(float(v), 4)} for t, v in s.items() if math.isfinite(v)]
    if sid == "SIGNAL":
        for p in pts:
            p["color"] = (light(p["value"]) or {}).get("color")
    return {"id": sid, "name": name, "unit": unit, "note": note, "freq": freq, "step": sid in STEP, "points": pts}


def text(ids: list[str] | None = None) -> str:
    rows = snapshot(ids)
    lines = ["【台灣總經】（國發會、主計總處、中央銀行）"]
    for g in GROUPS:
        for r in [x for x in rows if x["group"] == g]:
            if "value" not in r:
                lines.append(f"- {r['name']}：抓不到（{r['error'][:60]}）")
                continue
            u = r["unit"] if r["unit"] == "%" else (" " + r["unit"] if r["unit"] else "")
            extra = ""
            if r.get("light"):
                extra = f"，{r['light']['name']}" + (f"（前月 {r['prev_light']['name']}）" if r.get("prev_light") else "")
            if r["id"] == "DISCOUNT":
                lines.append(f"- {r['name']}：{r['value']:.3f}%（{r['changed']} 起；前一次 {r['prev']:.3f}%）" if r["prev"] is not None
                             else f"- {r['name']}：{r['value']:.3f}%（{r['changed']} 起）")
                continue
            prev = "—" if r["prev"] is None else f"{r['prev']:.2f}"
            y1 = "—" if r["y1"] is None else f"{r['y1']:.2f}"
            lines.append(f"- {r['name']}：{r['value']:.2f}{u}{extra}（{r['date']}；前期 {prev}，一年前 {y1}）")
    return "\n".join(lines)


def diag() -> dict:
    """Which source each series came from (or why it failed) — for debugging on the host."""
    snap = {r["id"]: r.get("error") or f"OK {r['date']}" for r in snapshot()}
    return {"series": snap, "sources": dict(_diag)}


def _fake(sid: str) -> pd.Series:
    import numpy as np
    rng = np.random.default_rng(sum(map(ord, sid)))
    today = pd.Timestamp(dt.date.today())
    if sid == "DISCOUNT":
        return pd.Series([1.125, 1.375, 1.5, 1.625, 1.75, 1.875, 2.0],
                         index=pd.to_datetime(["2020-03-20", "2022-03-18", "2022-06-17", "2022-09-23", "2022-12-16", "2023-03-24", "2024-03-22"]))
    if sid == "OVERNIGHT":
        idx = pd.bdate_range(end=today, periods=40)
        return pd.Series(0.81 + rng.normal(0, 0.002, len(idx)), index=idx).round(3)
    freq = "QS" if sid == "GDP" else "MS"
    idx = pd.date_range(end=today - pd.Timedelta(days=40), periods=40 if sid == "GDP" else 120, freq=freq)
    base, sd = {"SIGNAL": (27, 3), "LEADING": (100, 0.6), "COINCIDENT": (100, 0.6), "GDP": (4, 1.5), "CPI": (2, 0.3),
                "CORE": (1.8, 0.2), "UNEMP": (3.4, 0.05), "M1B": (5, 0.8), "M2": (5.5, 0.4)}[sid]
    walk = np.cumsum(rng.normal(0, sd * 0.3, len(idx)))
    vals = base + walk - walk.mean()
    if sid == "SIGNAL":
        vals = np.clip(np.round(vals), 9, 45)
    return pd.Series(vals, index=idx)
