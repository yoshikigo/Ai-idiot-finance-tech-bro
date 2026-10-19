"""Real-world event layer: God's Eye View (github.com/bilawalsidhu/gods-eye-view) → markets.

God's Eye View runs as its own service (Vite dev server, 127.0.0.1:4173) and exposes a
stateless MCP endpoint at /mcp (one JSON-RPC message per POST, loopback only). We call its
tools for market-relevant regions on a schedule, store every snapshot in SQLite, and derive:

  digest(regions, as_of)   → Chinese text for LLMs (TradingAgents tool + context, TimesFM report, ASK)
  events(regions, days)    → notable events (big quakes, cyclones, unusual military / shipping
                             activity) for chart markers and notes
  series(region, metric)   → daily indicator series (TimesFM 3.0 past-only covariates)

Everything is point-in-time: snapshots are only ever "what the feeds said at ts", so a
backtest at an earlier as_of only sees what had been collected by then.
FT_FAKE_DATA=1 → synthetic tool results (tests / offline demo).
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import logging
import os
import random
import sqlite3
import statistics
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("ft.world")
GEV_URL = os.environ.get("GEV_URL", "http://127.0.0.1:4173").rstrip("/")
GEV_PUBLIC_URL = os.environ.get("GEV_PUBLIC_URL", "").rstrip("/")  # e.g. https://myhost.<tailnet>.ts.net:4173
DATA = Path(os.environ.get("FT_DATA", "/data"))
DB = DATA / "world.sqlite"
FAKE = os.environ.get("FT_FAKE_DATA", "") in ("1", "true", "yes")
INTERVAL_MIN = float(os.environ.get("FT_WORLD_INTERVAL_MIN", "60"))
WORLD = [-180, -89.9, 180, 89.9]

# ---------------------------------------------------------------- what we watch
# check = (key, 中文標籤, tool, args builder(bbox) → dict, metric: "total" | "max_mag")
CHECKS = {
    "quake4": ("地震 M4+", "get_earthquakes", lambda b: {"area": {"bbox": b}, "min_magnitude": 4, "limit": 10}, "max_mag"),
    "quake5": ("地震 M5+", "get_earthquakes", lambda b: {"area": {"bbox": b}, "min_magnitude": 5, "limit": 10}, "max_mag"),
    "quake6": ("地震 M6+", "get_earthquakes", lambda b: {"min_magnitude": 6, "limit": 10}, "max_mag"),
    "mil_air": ("軍機", "aircraft_in_area", lambda b: {"area": {"bbox": b}, "military": True, "limit": 20}, "total"),
    "vessels": ("船舶", "vessels_in_area", lambda b: {"area": {"bbox": b}, "limit": 20}, "total"),
    "cyclones": ("熱帶氣旋（NHC）", "get_cyclones", lambda b: {"area": {"bbox": b}}, "total"),
    "fires": ("野火範圍", "get_fire_perimeters", lambda b: {"area": {"bbox": b}, "limit": 10}, "total"),
    "launches": ("火箭發射（30 天）", "get_recent_launches", lambda b: {"limit": 10}, "total"),
}

REGIONS = {
    "taiwan": {"name": "台灣／台灣海峽", "bbox": [117.0, 21.0, 123.5, 26.5], "checks": ["quake4", "mil_air", "vessels"],
               "why": "台股、半導體供應鏈、兩岸軍事風險"},
    "japan": {"name": "日本", "bbox": [129.0, 30.0, 146.0, 46.0], "checks": ["quake5"],
              "why": "半導體材料／設備、日股、汽車供應鏈"},
    "south_china_sea": {"name": "南海", "bbox": [105.0, 5.0, 121.0, 23.0], "checks": ["mil_air", "vessels"],
                        "why": "亞洲航運主幹道、區域軍事風險"},
    "hormuz": {"name": "荷莫茲海峽", "bbox": [54.0, 24.0, 58.5, 27.5], "checks": ["vessels", "mil_air"],
               "why": "全球約五分之一石油運輸、油價"},
    "red_sea": {"name": "紅海／曼德海峽", "bbox": [32.0, 12.0, 44.0, 30.0], "checks": ["vessels", "mil_air"],
                "why": "蘇伊士航線、貨櫃運價、歐亞航運"},
    "malacca": {"name": "麻六甲海峽", "bbox": [95.0, 0.0, 105.0, 7.0], "checks": ["vessels"],
                "why": "亞洲能源與貨櫃航線"},
    "panama": {"name": "巴拿馬運河", "bbox": [-80.5, 8.5, -79.0, 9.6], "checks": ["vessels"],
               "why": "美東／亞洲航線、穀物與 LNG"},
    "gulf_mexico": {"name": "美國墨西哥灣", "bbox": [-98.0, 18.0, -80.0, 31.0], "checks": ["cyclones"],
                    "why": "美國煉油與天然氣產能、颶風季"},
    "us_west": {"name": "美國西岸", "bbox": [-125.0, 32.0, -114.0, 42.0], "checks": ["quake4", "fires"],
                "why": "科技業、加州電力與保險"},
    "global": {"name": "全球", "bbox": WORLD, "checks": ["quake6", "launches"],
               "why": "重大地震、太空產業"},
}

SECTOR_REGIONS = {
    "semis": ["taiwan", "japan", "south_china_sea"],
    "shipping": ["red_sea", "hormuz", "malacca", "panama", "south_china_sea"],
    "energy": ["hormuz", "red_sea", "gulf_mexico"],
    "tw": ["taiwan", "south_china_sea"],
    "us_tech": ["us_west", "taiwan"],
    "space": ["global"],
}
SEMIS_US = {"TSM", "NVDA", "AMD", "ASML", "AVGO", "QCOM", "MU", "INTC", "AMAT", "LRCX", "KLAC", "SOXX", "SMH", "^SOX", "TXN", "ARM"}
ENERGY_US = {"XOM", "CVX", "COP", "OXY", "SLB", "XLE", "USO", "CL=F", "BZ=F", "NG=F", "RB=F", "HO=F", "BP", "SHEL"}
SHIP_US = {"ZIM", "MATX", "DAC", "GSL", "BDRY", "FRO", "STNG"}
SPACE_US = {"RKLB", "ASTS", "LMT", "NOC", "BA", "IRDM", "PL"}
TW_SHIPPING = {"2603", "2609", "2615", "2605", "2606", "2612", "2637", "2617", "2618", "2610"}
TW_ENERGY = {"6505", "1301", "1326", "1303", "2002", "9937", "8926"}


def regions_for(sym: str | None) -> list[str]:
    """Which regions matter for a security (always ends with 'global')."""
    out: list[str] = []
    s = (sym or "").upper()
    code = s.split(".")[0]
    if s.endswith((".TW", ".TWO")) or s in ("^TWII", "^TWOII"):
        out += SECTOR_REGIONS["tw"]
        industry = ""
        try:
            from . import twmarket
            sec = twmarket.lookup(code)
            industry = (sec or {}).get("industry", "") or ""
        except Exception:  # noqa: BLE001
            pass
        if code in TW_SHIPPING or "航運" in industry:
            out += SECTOR_REGIONS["shipping"]
        if code in TW_ENERGY or "油電" in industry or "塑膠" in industry:
            out += SECTOR_REGIONS["energy"]
        if "半導體" in industry or "電子" in industry or code in ("0050", "006208", "2330", "2454", "2303", "3711"):
            out += SECTOR_REGIONS["semis"]
    elif s in SEMIS_US:
        out += SECTOR_REGIONS["semis"] + ["us_west"]
    elif s in ENERGY_US:
        out += SECTOR_REGIONS["energy"]
    elif s in SHIP_US:
        out += SECTOR_REGIONS["shipping"]
    elif s in SPACE_US:
        out += SECTOR_REGIONS["space"]
    elif s in ("^GSPC", "^IXIC", "^DJI", "SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "META", "AMZN"):
        out += SECTOR_REGIONS["us_tech"]
    elif s in ("GC=F", "SI=F", "BTC-USD", "ETH-USD", "TWD=X", "JPY=X", "^VIX"):
        out += ["taiwan", "hormuz", "red_sea"]
    out.append("global")
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- MCP client
_ids = itertools.count(1)


class GevError(RuntimeError):
    pass


def mcp(method: str, params: dict | None = None, timeout: float = 60) -> dict:
    body = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params or {}}
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as c:
            r = c.post(f"{GEV_URL}/mcp", json=body, headers={"MCP-Protocol-Version": "2025-06-18",
                                                              "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise GevError(f"連不到 God's Eye View（{GEV_URL}）：{e}") from e
    if r.status_code == 403:
        raise GevError("God's Eye View 拒絕 MCP 請求（只接受本機直連；FT 容器需 Network=host、GEV_URL 用 127.0.0.1）")
    if r.status_code >= 400:
        raise GevError(f"God's Eye View MCP HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    if "error" in d:
        raise GevError(f"MCP error: {d['error'].get('message')}")
    return d["result"]


def call_tool(name: str, args: dict, timeout: float = 60) -> dict:
    """→ {'ok', 'summary', 'data'}; tool-level failures are ok=False, not exceptions."""
    if FAKE:
        return _fake_tool(name, args)
    res = mcp("tools/call", {"name": name, "arguments": args}, timeout)
    texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
    return {"ok": not res.get("isError"), "summary": texts[0] if texts else "", "data": res.get("structuredContent") or {}}


def list_tools() -> list[dict]:
    if FAKE:
        return [{"name": n} for n in sorted({c[1] for c in CHECKS.values()})]
    return mcp("tools/list").get("tools", [])


def health() -> dict:
    try:
        n = len(list_tools())
        return {"ok": True, "url": GEV_URL, "tools": n, "public_url": GEV_PUBLIC_URL or None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "url": GEV_URL, "error": str(e)[:200], "public_url": GEV_PUBLIC_URL or None}


# ---------------------------------------------------------------- store
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=DELETE")  # external drives (exFAT/NTFS): no WAL shared memory
    c.executescript("""
    CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, ts TEXT, region TEXT, chk TEXT, ok INTEGER,
        summary TEXT, total REAL, metric REAL, rows TEXT, view_url TEXT);
    CREATE INDEX IF NOT EXISTS snap_rc ON snapshots(region, chk, ts);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT, date TEXT, region TEXT, kind TEXT,
        severity REAL, title TEXT, detail TEXT, UNIQUE(date, region, kind));
    """)
    return c


def _metric(kind: str, data: dict):
    rows = data.get("rows") or data.get("storms") or []
    total = data.get("total")
    if total is None:
        total = len(rows)
    if kind == "max_mag":
        mags = [r.get("magnitude") for r in rows if isinstance(r, dict) and r.get("magnitude") is not None]
        return float(total), (max(mags) if mags else 0.0)
    return float(total), float(total)


def collect_once(regions: list[str] | None = None) -> dict:
    """Query every region's checks once and store the snapshots. Returns per-check status."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out = {}
    rows_to_store = []
    for rid in regions or list(REGIONS):
        reg = REGIONS[rid]
        for ck in reg["checks"]:
            label, tool, argf, kind = CHECKS[ck]
            try:
                r = call_tool(tool, argf(reg["bbox"]), timeout=60)
            except GevError as e:
                out[f"{rid}.{ck}"] = f"error: {e}"
                if "連不到" in str(e):
                    return {"error": str(e), "partial": out}
                continue
            total, metric = _metric(kind, r["data"]) if r["ok"] else (None, None)
            view = (r["data"].get("view") or {}).get("url") if r["ok"] else None
            rows = (r["data"].get("rows") or r["data"].get("storms") or [])[:5] if r["ok"] else []
            rows_to_store.append((now, rid, ck, int(r["ok"]), r["summary"][:1000], total, metric,
                                  json.dumps(rows, ensure_ascii=False)[:4000], view))
            out[f"{rid}.{ck}"] = r["summary"][:120] if r["ok"] else f"unavailable: {r['summary'][:100]}"
            if not FAKE:
                time.sleep(0.5)
    with _lock, _conn() as c:
        c.executemany("INSERT INTO snapshots(ts,region,chk,ok,summary,total,metric,rows,view_url) VALUES(?,?,?,?,?,?,?,?,?)",
                      rows_to_store)
    detect_events(now)
    return out


def _baseline(c, region, ck, before_ts, days=14):
    since = (dt.datetime.fromisoformat(before_ts) - dt.timedelta(days=days)).isoformat()
    vals = [v for (v,) in c.execute("SELECT metric FROM snapshots WHERE region=? AND chk=? AND ok=1 AND ts>=? AND ts<?",
                                    (region, ck, since, before_ts)) if v is not None]
    return (statistics.median(vals), len(vals)) if vals else (None, 0)


def detect_events(ts: str):
    """Turn the newest snapshot of each check into notable events (deduplicated per day)."""
    day = ts[:10]
    found = []
    with _lock, _conn() as c:
        for rid, reg in REGIONS.items():
            for ck in reg["checks"]:
                row = c.execute("SELECT summary,total,metric,rows FROM snapshots WHERE region=? AND chk=? AND ts=? AND ok=1",
                                (rid, ck, ts)).fetchone()
                if not row:
                    continue
                summary, total, metric, rows = row
                rows = json.loads(rows or "[]")
                if ck.startswith("quake") and metric:
                    thr = 7.0 if rid == "global" else 6.0 if rid in ("taiwan", "japan") else 6.5
                    if metric >= thr:
                        top = rows[0] if rows else {}
                        found.append((rid, "quake", metric, f"{REGIONS[rid]['name']} M{metric:.1f} 地震",
                                      f"{top.get('place') or ''} {top.get('time') or ''}".strip()))
                elif ck == "cyclones" and total:
                    found.append((rid, "cyclone", total, f"{REGIONS[rid]['name']} {int(total)} 個熱帶氣旋", summary[:200]))
                elif ck == "fires" and total and total >= 5:
                    found.append((rid, "fires", total, f"{REGIONS[rid]['name']} {int(total)} 處野火", summary[:200]))
                elif ck in ("mil_air", "vessels"):
                    base, n = _baseline(c, rid, ck, ts)
                    if base is None or n < 24:  # need ~a day of hourly history first
                        continue
                    if ck == "mil_air" and total >= max(5, 2 * base):
                        found.append((rid, "military", total / max(base, 1), f"{REGIONS[rid]['name']} 軍機活動 {int(total)} 架（平常約 {base:.0f}）", summary[:200]))
                    if ck == "vessels" and base >= 10 and total <= 0.5 * base:
                        found.append((rid, "shipping", base / max(total, 1), f"{REGIONS[rid]['name']} 船舶數驟降 {int(total)}（平常約 {base:.0f}）", summary[:200]))
        for rid, kind, sev, title, detail in found:
            c.execute("""INSERT INTO events(ts,date,region,kind,severity,title,detail) VALUES(?,?,?,?,?,?,?)
                         ON CONFLICT(date,region,kind) DO UPDATE SET severity=MAX(severity, excluded.severity),
                           title=CASE WHEN excluded.severity>severity THEN excluded.title ELSE title END,
                           detail=CASE WHEN excluded.severity>severity THEN excluded.detail ELSE detail END""",
                      (ts, day, rid, kind, sev, title, detail))
    return found


# ---------------------------------------------------------------- read side
def _as_of_ts(as_of: str | None) -> str:
    if not as_of:
        return "9999"
    if len(as_of) == 10:
        return as_of + "T23:59:59+99"
    return as_of


def latest(regions: list[str], as_of: str | None = None) -> dict:
    """{region: {check: {ts, ok, summary, total, metric, base, base_n, view_url, last_ok?}}} newest snapshot ≤ as_of."""
    cut = _as_of_ts(as_of)
    cols = ("ts", "ok", "summary", "total", "metric", "view_url")
    out: dict = {}
    with _conn() as c:
        for rid in regions:
            if rid not in REGIONS:
                continue
            out[rid] = {}
            for ck in REGIONS[rid]["checks"]:
                row = c.execute(f"SELECT {','.join(cols)} FROM snapshots WHERE region=? AND chk=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                                (rid, ck, cut)).fetchone()
                if not row:
                    continue
                d = dict(zip(cols, row))
                d["ok"] = bool(d["ok"])
                if d["ok"]:
                    d["base"], d["base_n"] = _baseline(c, rid, ck, d["ts"])
                else:
                    last = c.execute(f"SELECT {','.join(cols)} FROM snapshots WHERE region=? AND chk=? AND ts<=? AND ok=1 "
                                     "ORDER BY ts DESC LIMIT 1", (rid, ck, cut)).fetchone()
                    d["last_ok"] = dict(zip(cols, last)) if last else None
                out[rid][ck] = d
    return out


def events(regions: list[str] | None = None, days: int = 30, as_of: str | None = None, since: str | None = None) -> list[dict]:
    end = (as_of or dt.date.today().isoformat())[:10]
    start = since or (dt.date.fromisoformat(end) - dt.timedelta(days=days)).isoformat()
    q = "SELECT date,region,kind,severity,title,detail FROM events WHERE date>=? AND date<=?"
    args: list = [start, end]
    if regions:
        q += f" AND region IN ({','.join('?' * len(regions))})"
        args += regions
    with _conn() as c:
        rows = c.execute(q + " ORDER BY date DESC, severity DESC", args).fetchall()
    return [dict(zip(("date", "region", "kind", "severity", "title", "detail"), r)) | {"region_name": REGIONS.get(r[1], {}).get("name", r[1])}
            for r in rows]


def series(region: str, ck: str, start: str | None = None, end: str | None = None) -> dict[str, float]:
    """Daily series of a check's metric (max for quakes, mean for counts) → {YYYY-MM-DD: value}."""
    kind = CHECKS[ck][3]
    agg = "MAX" if kind == "max_mag" else "AVG"
    q = f"SELECT substr(ts,1,10) d, {agg}(metric) FROM snapshots WHERE region=? AND chk=? AND ok=1"
    args: list = [region, ck]
    if start:
        q += " AND ts>=?"
        args.append(start)
    if end:
        q += " AND ts<=?"
        args.append(_as_of_ts(end))
    with _conn() as c:
        return {d: float(v) for d, v in c.execute(q + " GROUP BY d ORDER BY d", args) if v is not None}


def status() -> dict:
    with _conn() as c:
        n, first, last = c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM snapshots").fetchone()
        ok = c.execute("SELECT COUNT(*) FROM snapshots WHERE ok=1 AND ts=(SELECT MAX(ts) FROM snapshots)").fetchone()[0]
        tot = c.execute("SELECT COUNT(*) FROM snapshots WHERE ts=(SELECT MAX(ts) FROM snapshots)").fetchone()[0]
        ev = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        days = c.execute("SELECT COUNT(DISTINCT substr(ts,1,10)) FROM snapshots WHERE ok=1").fetchone()[0]
    return {"snapshots": n, "first": first, "last": last, "last_ok": f"{ok}/{tot}", "events": ev, "days": days,
            "interval_min": INTERVAL_MIN, "gev": health()}


def _fmt_check(ck: str, d: dict) -> str:
    label = CHECKS[ck][0]
    when = (d.get("ts") or "")[:16].replace("T", " ")
    if not d.get("ok"):
        last = d.get("last_ok")
        tail = f"；上次成功 {last['ts'][:16].replace('T', ' ')}：{last['summary']}" if last else ""
        return f"- {label}：目前無法取得（{d.get('summary', '')[:80]}）{tail}"
    trend = ""
    if d.get("base") is not None and d.get("base_n", 0) >= 6 and CHECKS[ck][3] == "total":
        b = d["base"]
        trend = f"（近期中位數 {b:.0f}{'，明顯偏高' if b and d['metric'] >= 1.8 * b and d['metric'] >= 4 else '，明顯偏低' if b >= 6 and d['metric'] <= 0.5 * b else ''}）"
    return f"- {label}｜{when} UTC｜{d.get('summary', '')}{trend}"


def digest(regions: list[str], as_of: str | None = None, days: int = 7, max_chars: int = 3500) -> str:
    """Traditional-Chinese text for LLM prompts; facts only, each line timestamped."""
    regions = [r for r in regions if r in REGIONS] or ["global"]
    try:
        snap = latest(regions, as_of)
        evs = events(regions, days=days, as_of=as_of)
    except sqlite3.Error as e:
        return f"（世界事件資料庫無法讀取：{e}）"
    if not any(snap.values()):
        return ("（God's Eye View 世界事件：尚無資料——收集器還沒跑過或服務未啟動。"
                "這次分析沒有即時世界事件可用，不要假設有或沒有發生任何事件。）")
    lines = [f"【現實世界即時訊號｜God's Eye View 公開資料{'，截至 ' + as_of[:10] if as_of else ''}】",
             "來源：USGS 地震、ADS-B 軍機（adsb.lol）、AIS 船舶（AISStream）、NOAA NHC 氣旋、WFIGS 野火、Launch Library 2。"
             "只代表這些感測資料當下的狀態，不是新聞；數量偏離平常才有意義。"]
    for rid in regions:
        if not snap.get(rid):
            continue
        reg = REGIONS[rid]
        lines.append(f"▍{reg['name']}（{reg['why']}）")
        for ck, d in snap[rid].items():
            lines.append(_fmt_check(ck, d))
    if evs:
        lines.append(f"▍近 {days} 天值得注意的事件")
        for e in evs[:12]:
            lines.append(f"- {e['date']} {e['title']}" + (f"（{e['detail'][:100]}）" if e.get("detail") else ""))
    elif any(d.get("ok") or d.get("last_ok") for r in snap.values() for d in r.values()):
        lines.append(f"▍近 {days} 天：這些區域沒有偵測到超過門檻的事件（大地震、氣旋、軍機或航運異常）。")
    else:
        lines.append("▍目前所有來源都無法取得資料，無法判斷有沒有事件——不要據此推論世界很平靜。")
    lines.append("限制：西太平洋颱風不在 NHC 資料內；船舶需要 AISStream 金鑰；軍機只含有開 ADS-B 的機。")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 20] + "\n…（截斷）"


def compact(regions: list[str], as_of: str | None = None, days: int = 7, max_chars: int = 1200) -> str:
    """Short version for every agent prompt: only abnormal readings and events."""
    regions = [r for r in regions if r in REGIONS] or ["global"]
    try:
        snap = latest(regions, as_of)
        evs = events(regions, days=days, as_of=as_of)
    except sqlite3.Error:
        return ""
    if not any(snap.values()):
        return "現實世界訊號（God's Eye View）：目前沒有資料，不要假設有或沒有事件發生。"
    flags = []
    for rid, checks in snap.items():
        for ck, d in checks.items():
            if not d.get("ok"):
                continue
            m, b = d.get("metric") or 0, d.get("base")
            if ck.startswith("quake") and m >= 5:
                flags.append(f"{REGIONS[rid]['name']}：{d['summary']}")
            elif CHECKS[ck][3] == "total" and b is not None and d.get("base_n", 0) >= 6:
                if m >= max(4, 1.8 * b) or (b >= 6 and m <= 0.5 * b):
                    flags.append(f"{REGIONS[rid]['name']} {CHECKS[ck][0]} {m:.0f}（平常約 {b:.0f}）")
            elif ck == "cyclones" and m:
                flags.append(f"{REGIONS[rid]['name']}：{d['summary']}")
    names = "、".join(REGIONS[r]["name"] for r in regions)
    lines = [f"現實世界訊號（God's Eye View 公開感測資料：地震、軍機 ADS-B、船舶 AIS、氣旋；檢查範圍：{names}）："]
    lines += [f"- {e['date']} {e['title']}" for e in evs[:6]]
    lines += [f"- 目前：{f}" for f in flags[:6]]
    if len(lines) == 1:
        lines.append(f"- 近 {days} 天沒有超過門檻的異常。這不是新聞，只代表這些感測資料沒有異常。")
    lines.append("需要細節可用工具 get_world_events。")
    text = "\n".join(lines)
    return text[:max_chars]


def public_view_url(url: str | None) -> str | None:
    """Tool view links point at http://localhost:4173/… → rewrite to the tailnet URL."""
    if not url:
        return None
    if GEV_PUBLIC_URL and url.startswith(("http://localhost:4173", "http://127.0.0.1:4173")):
        return GEV_PUBLIC_URL + url.split("4173", 1)[1]
    return url


# ---------------------------------------------------------------- scheduler
def loop_forever():
    """Background collector for the terminal process."""
    time.sleep(20)
    while True:
        t0 = time.time()
        try:
            r = collect_once()
            if "error" in r:
                log.warning("world collect: %s", r["error"])
        except Exception as e:  # noqa: BLE001
            log.warning("world collect failed: %s", e)
        time.sleep(max(60.0, INTERVAL_MIN * 60 - (time.time() - t0)))


# ---------------------------------------------------------------- fake
def _fake_tool(name: str, args: dict) -> dict:
    rnd = random.Random(f"{name}{json.dumps(args, sort_keys=True)}{dt.datetime.now():%Y%m%d%H}")
    url = "http://localhost:4173/#v=2&lat=23.5&lon=121&alt=900000"
    if name == "get_earthquakes":
        n = rnd.randint(0, 3)
        rows = sorted([{"magnitude": round(rnd.uniform(args.get("min_magnitude", 4), 6.8), 1), "place": "Hualien offshore (demo)",
                        "time": dt.datetime.now(dt.timezone.utc).isoformat(), "lat": 23.9, "lon": 121.8, "depth_km": 15}
                       for _ in range(n)], key=lambda r: -r["magnitude"])
        s = f"{n} earthquakes of M{args.get('min_magnitude', 2.5)}+ in the last 24 hours" + (f"; strongest M{rows[0]['magnitude']}" if rows else "") + "."
        return {"ok": True, "summary": s, "data": {"total": n, "rows": rows, "view": {"url": url}}}
    if name in ("aircraft_in_area", "vessels_in_area"):
        n = rnd.randint(2, 14) if name == "aircraft_in_area" else rnd.randint(20, 80)
        kind = "military aircraft" if name == "aircraft_in_area" else "vessels"
        return {"ok": True, "summary": f"{n} {kind} in the requested box (demo).",
                "data": {"total": n, "rows": [{"id": f"demo{i}"} for i in range(min(n, 5))], "view": {"url": url}}}
    if name == "get_cyclones":
        n = rnd.choice([0, 0, 1])
        return {"ok": True, "summary": f"{n} active tropical cyclone{'s' if n != 1 else ''} (demo).",
                "data": {"storms": [{"name": "DEMO", "wind_kt": 85}] * n, "view": {"url": url}}}
    n = rnd.randint(0, 6)
    return {"ok": True, "summary": f"{n} results for {name} (demo).", "data": {"total": n, "rows": [], "view": {"url": url}}}
