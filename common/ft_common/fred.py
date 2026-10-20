"""FRED (St. Louis Fed) — US macro series, no API key needed (fredgraph.csv).

Used for the 總經 monitor, chart overlays (symbol "FRED:<id>"), ASK context on macro questions, and to label
past FOMC events with what the Fed actually did (target range before vs after).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import os
import time

import pandas as pd

from . import market

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# id → (中文名, 單位, 轉換, 分組, 說明)   轉換：level 原值、yoy 年增率 %、diff 期增減
SERIES = {
    "DFEDTARU": ("Fed 利率目標上限", "%", "level", "利率", "聯邦基金利率目標區間上限"),
    "DFF": ("有效聯邦基金利率", "%", "level", "利率", "實際隔夜拆款利率"),
    "DGS2": ("美債 2 年殖利率", "%", "level", "利率", ""),
    "DGS10": ("美債 10 年殖利率", "%", "level", "利率", ""),
    "T10Y2Y": ("10 年 − 2 年利差", "百分點", "level", "利率", "負值＝殖利率曲線倒掛"),
    "T10Y3M": ("10 年 − 3 個月利差", "百分點", "level", "利率", "負值＝倒掛，常被視為衰退前兆"),
    "MORTGAGE30US": ("30 年房貸利率", "%", "level", "利率", ""),
    "CPIAUCSL": ("CPI 年增率", "%", "yoy", "通膨", "消費者物價指數"),
    "CPILFESL": ("核心 CPI 年增率", "%", "yoy", "通膨", "不含食品與能源"),
    "PCEPILFE": ("核心 PCE 年增率", "%", "yoy", "通膨", "Fed 主要參考的通膨指標"),
    "UNRATE": ("失業率", "%", "level", "就業", ""),
    "PAYEMS": ("非農就業月增", "千人", "diff", "就業", ""),
    "ICSA": ("初領失業救濟金", "人", "level", "就業", "每週"),
    "A191RL1Q225SBEA": ("實質 GDP 季增年率", "%", "level", "景氣", ""),
    "UMCSENT": ("密大消費者信心", "", "level", "景氣", ""),
    "RECPROUSM156N": ("衰退機率（平滑）", "%", "level", "景氣", ""),
    "BAMLH0A0HYM2": ("高收益債利差", "百分點", "level", "金融情勢", "利差擴大＝信用風險升高"),
    "VIXCLS": ("VIX 恐慌指數", "", "level", "金融情勢", ""),
    "DTWEXBGS": ("美元指數（廣義）", "", "level", "金融情勢", ""),
    "M2SL": ("M2 年增率", "%", "yoy", "金融情勢", "貨幣供給"),
    "DCOILWTICO": ("WTI 原油", "美元", "level", "商品", ""),
    "NETLIQ": ("Fed 淨流動性", "十億美元", "level", "流動性",
               "Fed 資產負債表 − 財政部 TGA − 隔夜逆回購；上升＝市場資金變多，常與美股同向"),
    "WALCL": ("Fed 資產負債表", "十億美元", "level", "流動性", "總資產（每週三）；擴表＝QE、縮表＝QT"),
    "WTREGEN": ("財政部 TGA 帳戶", "十億美元", "level", "流動性", "財政部在 Fed 的存款；上升＝從市場抽走資金"),
    "RRPONTSYD": ("隔夜逆回購（RRP）", "十億美元", "level", "流動性", "停在 Fed 的閒置資金；下降＝資金回到市場"),
}
GROUPS = ["流動性", "利率", "通膨", "就業", "景氣", "金融情勢", "商品"]
LIQUIDITY = ["NETLIQ", "WALCL", "WTREGEN", "RRPONTSYD"]
SCALE = {"WALCL": 1 / 1000}  # millions → billions (TGA and RRP are already in billions)


def net_liquidity() -> pd.Series:
    """Weekly (Wednesday, the H.4.1 date): WALCL − TGA − RRP, in billions of USD."""
    bs = raw("WALCL") * SCALE["WALCL"]
    tga, rrp = raw("WTREGEN"), raw("RRPONTSYD")
    idx = bs.index[bs.index >= max(tga.index.min(), rrp.index.min())]
    asof = lambda s: s.reindex(s.index.union(idx)).ffill().reindex(idx)  # noqa: E731
    return (bs.reindex(idx) - asof(tga) - asof(rrp)).dropna()


API_URL = "https://api.stlouisfed.org/fred/series/observations"
START = "1990-01-01"  # bounded request: the full daily history makes fredgraph.csv slow enough to time out
FRED_HEADERS = {"User-Agent": "fin-terminal (python-httpx)", "Accept": "*/*", "Accept-Language": "en"}
_csv_down = {"until": 0.0, "why": ""}  # after a failure skip fredgraph.csv for 10 min, so 21 series don't each wait 60 s


def raw(series_id: str) -> pd.Series:
    """History since 1990 as a float Series indexed by date (cached 6 h). "NETLIQ" is derived (net_liquidity).

    fredgraph.csv (no key) first; when it fails and FT_FRED_API_KEY is set (free key from
    https://fredaccount.stlouisfed.org/apikeys), the official API. Errors name both attempts."""
    if series_id == "NETLIQ":
        return market.cached("fred:NETLIQ", 6 * 3600, net_liquidity)

    def load():
        if market.FAKE:
            return _fake(series_id)
        errs = []
        s, why = _from_csv(series_id)
        if s is not None:
            return s
        errs.append(f"fredgraph.csv {why}")
        key = os.environ.get("FT_FRED_API_KEY", "").strip()
        if not key:
            errs.append("可設定 FT_FRED_API_KEY 改走官方 API")
            raise RuntimeError("；".join(errs))
        try:
            from .twhttp import fetch
            body = fetch(API_URL, params={"series_id": series_id, "api_key": key, "file_type": "json",
                                          "observation_start": START}, timeout=60, headers=FRED_HEADERS)
            s = parse_api(json.loads(body))
            if len(s):
                return s
            errs.append("FRED API 沒有資料")
        except Exception as e:  # noqa: BLE001
            errs.append(f"FRED API {_why(e)}")
        raise RuntimeError("；".join(errs))
    return market.cached(f"fred:{series_id}", 6 * 3600, load)


def _from_csv(series_id: str):
    """(series, None) or (None, reason). After a failure, skip fredgraph.csv for 10 min so the 21 series of the
    總經 page don't each wait out a 60 s timeout."""
    if time.time() < _csv_down["until"]:
        return None, f"{_csv_down['why']}（10 分鐘內不重試）"
    from .twhttp import fetch
    try:
        # Plain, honest client headers. FRED's CDN stalls requests whose User-Agent claims to be Chrome but whose TLS
        # handshake is Python's (a bare httpx GET answers in 0.4 s, the browser-looking one times out).
        # No disk cache: a block page must not stick for 6 h.
        body = fetch(CSV_URL, params={"id": series_id, "cosd": START}, timeout=60, headers=FRED_HEADERS)
        s = parse_csv(body.decode("utf-8-sig"))
        if len(s):
            return s, None
        why = "回傳空的或不是 CSV（可能被擋）"
    except Exception as e:  # noqa: BLE001
        why = _why(e)
    _csv_down.update(until=time.time() + 600, why=why)
    return None, why


def _why(e: Exception) -> str:
    code = getattr(getattr(e, "response", None), "status_code", None)
    return f"HTTP {code}" if code else f"{type(e).__name__}: {str(e)[:80]}"


def parse_api(d: dict) -> pd.Series:
    obs = d.get("observations") or []
    s = pd.Series({o["date"]: o["value"] for o in obs}, dtype=object)
    s = pd.to_numeric(s.replace(".", None), errors="coerce")
    s.index = pd.to_datetime(s.index, errors="coerce")
    return s[s.index.notna()].dropna().astype(float)


def parse_csv(text: str) -> pd.Series:
    if text.lstrip()[:1] == "<":  # an HTML block / error page instead of the CSV
        return pd.Series(dtype=float)
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2:
        return pd.Series(dtype=float)
    s = pd.to_numeric(df.iloc[:, 1].replace(".", None), errors="coerce")
    s.index = pd.to_datetime(df.iloc[:, 0], errors="coerce")
    return s[s.index.notna()].dropna().astype(float)


def transformed(series_id: str) -> pd.Series:
    s = raw(series_id) * SCALE.get(series_id, 1)
    how = SERIES.get(series_id, ("", "", "level"))[2]
    if how == "yoy":
        gap = pd.Series(s.index[-30:]).diff().dt.days.median() if len(s) > 2 else 30
        lag = 12 if gap >= 25 else 4 if gap >= 80 else 52 if gap >= 6 else 252
        if gap >= 80:
            lag = 4
        s = (s / s.shift(lag) - 1) * 100
    elif how == "diff":
        s = s.diff()
    return s.dropna()


def _ago(s: pd.Series, days: int):
    t = s.index[-1] - pd.Timedelta(days=days)
    prior = s[s.index <= t]
    return float(prior.iloc[-1]) if len(prior) else None


def snapshot(ids: list[str] | None = None) -> list[dict]:
    out = []
    for sid in ids or list(SERIES):
        name, unit, how, group, note = SERIES.get(sid, (sid, "", "level", "", ""))
        try:
            s = transformed(sid)
        except Exception as e:  # noqa: BLE001
            out.append({"id": sid, "name": name, "group": group, "error": str(e)[:120]})
            continue
        if not len(s):
            out.append({"id": sid, "name": name, "group": group, "error": "沒有資料"})
            continue
        last = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) > 1 else None
        y1 = _ago(s, 365)
        spark = [round(float(x), 4) for x in s[s.index >= s.index[-1] - pd.Timedelta(days=730)].iloc[::max(1, len(s[s.index >= s.index[-1] - pd.Timedelta(days=730)]) // 60)]]
        out.append({"id": sid, "name": name, "unit": unit, "group": group, "note": note, "how": how,
                    "date": s.index[-1].strftime("%Y-%m-%d"), "value": last, "prev": prev,
                    "chg": None if prev is None else last - prev, "y1": y1, "chg_1y": None if y1 is None else last - y1,
                    "spark": spark})
    return out


def history(series_id: str, years: float = 10) -> list[dict]:
    s = transformed(series_id)
    if years and years < 200:
        s = s[s.index >= s.index[-1] - pd.Timedelta(days=int(365.25 * years))]
    return [{"time": t.strftime("%Y-%m-%d"), "value": round(float(v), 4)} for t, v in s.items() if not math.isnan(v)]


def text(ids: list[str] | None = None) -> str:
    rows = snapshot(ids)
    lines = ["【美國總經（FRED）】"]
    for g in GROUPS:
        for r in [x for x in rows if x.get("group") == g and "value" in x]:
            fmt = (lambda v: f"{v:,.0f}") if abs(r["value"]) >= 1000 else (lambda v: f"{v:.2f}")
            lines.append(f"- {r['name']}：{fmt(r['value'])}{r['unit'] if r['unit'] in ('%',) else (' ' + r['unit'] if r['unit'] else '')}"
                         f"（{r['date']}；前期 {fmt(r['prev']) if r['prev'] is not None else '—'}，一年前 {fmt(r['y1']) if r['y1'] is not None else '—'}）")
    return "\n".join(lines)


def fed_decision(day: str) -> dict | None:
    """What the Fed did at the meeting ending `day`: compare the target range the day before vs. two days after."""
    try:
        up, lo = raw("DFEDTARU"), raw("DFEDTARL")
    except Exception:  # noqa: BLE001
        return None
    d = pd.Timestamp(day)
    b_up, a_up = up[up.index < d], up[up.index > d]
    b_lo, a_lo = lo[lo.index < d], lo[lo.index > d]
    if not len(b_up) or not len(a_up) or not len(a_lo) or a_up.index[0] - d > pd.Timedelta(days=4):
        return None
    before, after, after_lo = float(b_up.iloc[-1]), float(a_up.iloc[0]), float(a_lo.iloc[0])
    before_lo = float(b_lo.iloc[-1]) if len(b_lo) else before - 0.25
    bp = round((after - before) * 100)
    if bp == 0:
        verb = "維持利率不變"
    else:
        steps = abs(bp) / 25
        size = f"{steps:g} 碼" if steps == int(steps) else f"{abs(bp)} 個基點"
        verb = f"{'升息' if bp > 0 else '降息'} {size}"
    return {"bp": bp, "upper": after, "lower": after_lo, "before_upper": before, "before_lower": before_lo,
            "title": f"Fed {verb}（{after_lo:.2f}%–{after:.2f}%）", "date": day}


def _fake(sid: str) -> pd.Series:
    import numpy as np
    rng = np.random.default_rng(abs(hash(sid)) % 2 ** 32)
    idx = pd.date_range(dt.date.today() - dt.timedelta(days=3650), dt.date.today(), freq="MS" if sid in (
        "CPIAUCSL", "CPILFESL", "PCEPILFE", "UNRATE", "PAYEMS", "UMCSENT", "RECPROUSM156N", "M2SL") else "W")
    base = {"DFEDTARU": 4.5, "DFEDTARL": 4.25, "DFF": 4.3, "DGS2": 3.8, "DGS10": 4.2, "T10Y2Y": 0.4, "T10Y3M": 0.1,
            "CPIAUCSL": 300, "CPILFESL": 310, "PCEPILFE": 120, "UNRATE": 4.1, "PAYEMS": 158000, "ICSA": 220000,
            "M2SL": 21000, "VIXCLS": 17, "BAMLH0A0HYM2": 3.2, "WALCL": 6.6e6, "WTREGEN": 800, "RRPONTSYD": 150,
            "DFEDTARL": 4.25}.get(sid, 100)
    if sid in ("DFEDTARU", "DFEDTARL"):
        steps = np.cumsum(rng.choice([0, 0, 0, 0.25, -0.25], len(idx)))
        return pd.Series(np.clip(base + steps * 0.2, 0.25, 6), index=idx).round(2)
    drift = 0.002 if sid in ("CPIAUCSL", "CPILFESL", "PCEPILFE", "M2SL", "PAYEMS") else 0
    vals = base * np.cumprod(1 + drift + rng.normal(0, 0.01, len(idx)))
    return pd.Series(vals, index=idx)


def liquidity_text() -> str:
    """Net liquidity level, 4/13/52-week changes, and how its 13-week change has tracked the S&P 500 (2 years)."""
    nl = transformed("NETLIQ")
    if not len(nl):
        return "【Fed 淨流動性】沒有資料"
    last = float(nl.iloc[-1])

    def chg(weeks):
        p = nl[nl.index <= nl.index[-1] - pd.Timedelta(weeks=weeks)]
        return last - float(p.iloc[-1]) if len(p) else None
    parts = [f"{label} {v:+,.0f}" for label, v in (("4 週", chg(4)), ("13 週", chg(13)), ("52 週", chg(52))) if v is not None]
    comp = []
    for sid in ("WALCL", "WTREGEN", "RRPONTSYD"):
        try:
            x = transformed(sid)
            comp.append(f"{SERIES[sid][0]} {float(x.iloc[-1]):,.0f}")
        except Exception:  # noqa: BLE001
            pass
    corr = None
    try:
        spx = market.history("^GSPC", "2y", "1d")["Close"].astype(float)
        spx.index = pd.DatetimeIndex(spx.index).tz_localize(None).normalize()
        w = nl[nl.index >= nl.index[-1] - pd.Timedelta(days=730)]
        sp = spx.reindex(spx.index.union(w.index)).ffill().reindex(w.index)
        a, b = w.diff(13).dropna(), sp.pct_change(13).dropna()
        j = a.index.intersection(b.index)
        if len(j) > 20:
            corr = float(a[j].corr(b[j]))
    except Exception:  # noqa: BLE001
        pass
    return (f"【Fed 淨流動性（FRED，{nl.index[-1]:%Y-%m-%d}）】{last:,.0f} 十億美元（{'、'.join(parts)}）；"
            f"組成：{'、'.join(comp)}（淨流動性＝資產負債表 − TGA − 逆回購）"
            + (f"；近兩年 13 週變化與 S&P 500 13 週報酬的相關係數 {corr:+.2f}" if corr is not None else ""))
