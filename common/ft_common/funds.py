"""Taiwan mutual funds (境內 / 境外 / 期信基金): daily NAV from official open data, kept in SQLite.

Sources (CSV, no key, refreshed at most every FUND_REFRESH_HOURS):
- 境內基金淨值  投信投顧公會   https://www.sitca.org.tw/MemberK0000/F/03/nav.csv
    日期, 基金統編, 基金代號, 基金名稱, 基金淨值, 幣別, 公司名稱, 受益憑證代號
- 境外基金淨值  集保 TDCC      https://opendata.tdcc.com.tw/getOD.ashx?id=3-4
    日期, 基金代碼, 基金名稱, ISINCODE, 基金淨值(金額), 計價幣別, 境外基金機構
- 期信基金淨值  集保 TDCC      https://opendata.tdcc.com.tw/getOD.ashx?id=5-4
    淨值日期, 基金代碼, 基金名稱, 淨值, 幣別, 期信機構
Column names are matched loosely (several candidates each) because these CSVs change headers.

The feeds only carry recent days, so every refresh upserts into /data/funds.sqlite and the
history grows from the day you install. For longer history on offshore funds we try
yfinance (Morningstar-sourced, looked up by ISIN) as a best-effort fallback.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import market
from .twhttp import decode_tw, fetch

log = logging.getLogger("ft.funds")
DATA = Path(os.environ.get("FT_DATA", "/data"))
DB = DATA / "funds.sqlite"
REFRESH_H = float(os.environ.get("FUND_REFRESH_HOURS", "6"))

SOURCES = {
    "境內": "https://www.sitca.org.tw/MemberK0000/F/03/nav.csv",
    "境外": "https://opendata.tdcc.com.tw/getOD.ashx?id=3-4",
    "期信": "https://opendata.tdcc.com.tw/getOD.ashx?id=5-4",
}
COLS = {
    "date": ("日期", "淨值日期", "資料日期", "NAV日期"),
    "code": ("基金代號", "基金代碼", "代號"),
    "tax_id": ("基金統編", "統一編號"),
    "isin": ("ISINCODE", "ISIN", "ISIN代碼", "受益憑證代號"),
    "name": ("基金名稱", "基金中文名稱", "名稱"),
    "nav": ("基金淨值", "基金淨值(金額)", "淨值", "單位淨值"),
    "currency": ("幣別", "計價幣別", "幣別代號"),
    "company": ("公司名稱", "境外基金機構", "期信機構", "總代理人", "投信公司"),
}

_lock = threading.Lock()
_refreshing = threading.Lock()


def _conn() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS funds(id TEXT PRIMARY KEY, region TEXT, code TEXT, isin TEXT, tax_id TEXT,
        name TEXT, company TEXT, currency TEXT, yf_symbol TEXT, updated TEXT);
    CREATE TABLE IF NOT EXISTS navs(id TEXT, date TEXT, nav REAL, source TEXT, PRIMARY KEY(id, date));
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE INDEX IF NOT EXISTS navs_id ON navs(id, date);
    """)
    return c


def _pick(row: dict, key: str) -> str:
    for c in COLS[key]:
        if c in row and str(row[c]).strip():
            return str(row[c]).strip()
    # loose: header contains the candidate
    for c in COLS[key]:
        for k, v in row.items():
            if k and c in k and str(v).strip():
                return str(v).strip()
    return ""


def _date(s: str) -> str | None:
    d = re.sub(r"\D", "", s or "")
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    if len(d) == 7:  # ROC 1151016
        return f"{int(d[:3]) + 1911}-{d[3:5]}-{d[5:]}"
    return None


def parse_csv(text: str, region: str) -> list[dict]:
    rows = []
    rd = csv.DictReader(io.StringIO(text))
    rd.fieldnames = [(f or "").strip().lstrip("﻿") for f in (rd.fieldnames or [])]
    for r in rd:
        name = _pick(r, "name")
        nav = _pick(r, "nav").replace(",", "")
        date = _date(_pick(r, "date"))
        try:
            navf = float(nav)
        except ValueError:
            continue
        if not name or not date or navf <= 0:
            continue
        code, isin, tax_id = _pick(r, "code"), _pick(r, "isin"), _pick(r, "tax_id")
        if region == "境外":
            fid = f"O:{isin or code}"
        elif region == "境內":
            fid = f"D:{tax_id or isin or code}"
        else:
            fid = f"F:{code or isin}"
        if fid.endswith(":"):
            continue
        rows.append({"id": fid, "region": region, "code": code, "isin": isin if region != "境內" or isin else "",
                     "tax_id": tax_id, "name": name, "company": _pick(r, "company"),
                     "currency": _pick(r, "currency") or ("TWD" if region == "境內" else ""), "date": date, "nav": navf})
    return rows


def _upsert(rows: list[dict], source: str):
    now = dt.datetime.now().isoformat(timespec="seconds")
    with _lock, _conn() as c:
        latest: dict[str, dict] = {}
        for r in rows:
            if r["id"] not in latest or r["date"] > latest[r["id"]]["date"]:
                latest[r["id"]] = r
        c.executemany("""INSERT INTO funds(id,region,code,isin,tax_id,name,company,currency,updated)
                         VALUES(?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET name=excluded.name, company=excluded.company,
                           currency=excluded.currency, code=excluded.code,
                           isin=COALESCE(NULLIF(excluded.isin,''), funds.isin), updated=excluded.updated""",
                      [(r["id"], r["region"], r["code"], r["isin"], r["tax_id"], r["name"], r["company"],
                        r["currency"], now) for r in latest.values()])
        c.executemany("INSERT OR REPLACE INTO navs(id,date,nav,source) VALUES(?,?,?,?)",
                      [(r["id"], r["date"], r["nav"], source) for r in rows])


def refresh(force: bool = False) -> dict:
    """Download the three feeds if stale. Returns {region: rows|error}."""
    if not _refreshing.acquire(blocking=False):
        return {"busy": True}
    try:
        with _conn() as c:
            row = c.execute("SELECT v FROM meta WHERE k='refreshed'").fetchone()
        if row and not force and time.time() - float(row[0]) < REFRESH_H * 3600:
            return {"fresh": True}
        res = {}
        if market.FAKE:
            _upsert(_fake_rows(), "fake")
            res["fake"] = True
        else:
            for region, url in SOURCES.items():
                try:
                    rows = parse_csv(decode_tw(fetch(url, timeout=120)), region)
                    _upsert(rows, region)
                    res[region] = len(rows)
                except Exception as e:  # noqa: BLE001
                    log.warning("fund feed %s failed: %s", region, e)
                    res[region] = f"error: {e}"
        with _lock, _conn() as c:
            c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('refreshed',?)", (str(time.time()),))
            c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('result',?)", (str(res),))
        return res
    finally:
        _refreshing.release()


def ensure():
    """Make sure we have data; refresh in background if stale."""
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
    if n == 0:
        refresh()
    else:
        threading.Thread(target=refresh, daemon=True).start()


def status() -> dict:
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
        by = dict(c.execute("SELECT region, COUNT(*) FROM funds GROUP BY region").fetchall())
        r = dict(c.execute("SELECT k, v FROM meta").fetchall())
        span = c.execute("SELECT MIN(date), MAX(date) FROM navs").fetchone()
    return {"funds": n, "by_region": by, "nav_span": span,
            "refreshed": dt.datetime.fromtimestamp(float(r["refreshed"])).isoformat(timespec="minutes") if r.get("refreshed") else None,
            "last_result": r.get("result")}


def _latest_two(c, fid):
    return c.execute("SELECT date, nav FROM navs WHERE id=? ORDER BY date DESC LIMIT 2", (fid,)).fetchall()


def _summary(c, f: tuple) -> dict:
    fid, region, code, isin, tax_id, name, company, currency = f[:8]
    two = _latest_two(c, fid)
    nav = two[0][1] if two else None
    chg = (two[0][1] / two[1][1] - 1) * 100 if len(two) == 2 and two[1][1] else None
    out = {"id": fid, "region": region, "code": code, "isin": isin, "name": name, "company": company,
           "currency": currency, "nav": nav, "date": two[0][0] if two else None, "pct": chg}
    if two:
        d0 = dt.date.fromisoformat(two[0][0])
        for key, days in (("r1m", 30), ("r1y", 365)):
            prior = c.execute("SELECT nav FROM navs WHERE id=? AND date<=? ORDER BY date DESC LIMIT 1",
                              (fid, (d0 - dt.timedelta(days=days)).isoformat())).fetchone()
            out[key] = round((nav / prior[0] - 1) * 100, 2) if prior and prior[0] else None
        out["n_navs"] = c.execute("SELECT COUNT(*) FROM navs WHERE id=?", (fid,)).fetchone()[0]
    return out


def search(q: str, region: str = "", limit: int = 40) -> list[dict]:
    ensure_quick()
    terms = [t for t in re.split(r"\s+", q.strip()) if t]
    if not terms:
        return []
    where = " AND ".join(["(name LIKE ? OR company LIKE ? OR code LIKE ? OR isin LIKE ?)"] * len(terms))
    args = []
    for t in terms:
        args += [f"%{t}%"] * 4
    if region:
        where += " AND region=?"
        args.append(region)
    with _conn() as c:
        rows = c.execute(f"SELECT id,region,code,isin,tax_id,name,company,currency FROM funds WHERE {where} "
                         f"ORDER BY LENGTH(name) LIMIT ?", (*args, limit)).fetchall()
        return [_summary(c, r) for r in rows]


def get(fid: str) -> dict | None:
    ensure_quick()
    with _conn() as c:
        f = c.execute("SELECT id,region,code,isin,tax_id,name,company,currency,yf_symbol FROM funds WHERE id=?",
                      (fid,)).fetchone()
        if not f:
            return None
        s = _summary(c, f)
        s["tax_id"], s["yf_symbol"] = f[4], f[8]
        return s


def isin_check_digit(body: str) -> str:
    """ISO 6166 check digit (Luhn over the base-36 digits)."""
    digits = "".join(str(int(ch, 36)) for ch in body.upper())
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def fund_isin(f: dict) -> str | None:
    """Offshore funds carry an ISIN; domestic ones only their SITCA code (e.g. T3604Y → TW000T3604Y3)."""
    if f.get("isin"):
        return f["isin"]
    code = (f.get("code") or "").strip().upper()
    if f.get("region") == "境內" and re.fullmatch(r"[A-Z0-9]{4,9}", code):
        body = "TW" + code.rjust(9, "0")
        return body + isin_check_digit(body)
    return None


def backfill(fid: str, force: bool = False) -> int:
    """Pull long NAV history (Yahoo/Morningstar via ISIN) into the local table once; official values win."""
    f = get(fid)
    isin = fund_isin(f or {})
    if not f or not isin or market.FAKE:
        return 0
    with _conn() as c:
        n, = c.execute("SELECT COUNT(*) FROM navs WHERE id=?", (fid,)).fetchone()
        sym = (c.execute("SELECT yf_symbol FROM funds WHERE id=?", (fid,)).fetchone() or [None])[0] or ""
    if n >= 60 and not force:
        return 0
    if sym.startswith("-:") and dt.date.fromisoformat(sym[2:]) > dt.date.today() - dt.timedelta(days=7) and not force:
        return 0  # not on Yahoo, checked within a week
    ext = _yf_history(fid, isin)
    if not ext:
        return 0
    with _lock, _conn() as c:
        c.executemany("INSERT OR IGNORE INTO navs(id,date,nav,source) VALUES(?,?,?,'yahoo')", [(fid, d, v) for d, v in ext])
        c.commit()
    return len(ext)


def history(fid: str, days: int = 3650) -> dict:
    """{'source', 'points':[[date, nav]...]} — local snapshots, extended once via Yahoo/Morningstar by ISIN."""
    f = get(fid)
    if not f:
        return {"points": [], "source": None}
    try:
        market.cached(f"fundbf:{fid}", 12 * 3600, lambda: backfill(fid))
    except Exception as e:  # noqa: BLE001
        log.info("fund backfill failed for %s: %s", fid, e)
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with _conn() as c:
        rows = c.execute("SELECT date, nav, source FROM navs WHERE id=? AND date>=? ORDER BY date", (fid, since)).fetchall()
    srcs = {r[2] or "official" for r in rows}
    source = ("Yahoo/Morningstar 歷史 + 官方淨值" if "yahoo" in srcs and len(srcs) > 1 else
              "Yahoo/Morningstar 歷史" if "yahoo" in srcs else "官方淨值（本機累積）")
    return {"points": [[d, v] for d, v, _ in rows], "source": source, "isin": fund_isin(f)}


def _yf_history(fid: str, isin: str) -> list[tuple[str, float]]:
    import yfinance as yf
    with _conn() as c:
        sym = (c.execute("SELECT yf_symbol FROM funds WHERE id=?", (fid,)).fetchone() or [None])[0]
    if not sym or sym.startswith("-:"):
        res = yf.Search(isin, max_results=3, news_count=0).quotes or []
        sym = next((r["symbol"] for r in res if r.get("quoteType") in ("MUTUALFUND", "ETF")), None)
        with _lock, _conn() as c:
            c.execute("UPDATE funds SET yf_symbol=? WHERE id=?", (sym or f"-:{dt.date.today().isoformat()}", fid))
            c.commit()
        if not sym:
            return []
    h = yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=False)
    return [(str(i.date()), float(v)) for i, v in h["Close"].dropna().items()]


_pf_q: list[str] = []
_pf_lock = threading.Lock()
_pf_running = False


def prefetch(ids: list[str]):
    """Backfill history for search results in the background (one fund at a time, politely)."""
    global _pf_running
    if market.FAKE:
        return
    with _pf_lock:
        for i in ids:
            if i not in _pf_q:
                _pf_q.append(i)
        if _pf_running:
            return
        _pf_running = True

    def worker():
        global _pf_running
        while True:
            with _pf_lock:
                if not _pf_q:
                    _pf_running = False
                    return
                fid = _pf_q.pop(0)
            try:
                market.cached(f"fundbf:{fid}", 12 * 3600, lambda: backfill(fid))
            except Exception as e:  # noqa: BLE001
                log.info("prefetch %s: %s", fid, e)
            time.sleep(1.5)
    threading.Thread(target=worker, daemon=True, name="fund-prefetch").start()


def returns(points: list[list]) -> dict:
    if not points:
        return {}
    last_d = dt.date.fromisoformat(points[-1][0])
    last = points[-1][1]

    def at(days=None, ytd=False):
        target = dt.date(last_d.year, 1, 1) - dt.timedelta(days=1) if ytd else last_d - dt.timedelta(days=days)
        prior = [p for p in points if dt.date.fromisoformat(p[0]) <= target]
        if not prior and ytd:  # history starts in the first days of the year
            prior = [p for p in points if dt.date.fromisoformat(p[0]) <= dt.date(last_d.year, 1, 10)][:1]
        if not prior:
            return None
        return (last / prior[-1][1] - 1) * 100

    out = {"1週": at(7), "1月": at(30), "3月": at(91), "6月": at(182), "今年": at(ytd=True), "1年": at(365),
           "3年": at(365 * 3)}
    return {k: round(v, 2) for k, v in out.items() if v is not None}


_quick_done = False


def ensure_quick():
    global _quick_done
    if not _quick_done:
        _quick_done = True
        try:
            ensure()
        except Exception as e:  # noqa: BLE001
            log.warning("fund ensure failed: %s", e)


def _fake_rows() -> list[dict]:
    import numpy as np
    base = [("境內", "D:12345678", "AL01", "", "安聯台灣科技基金", "安聯投信", "TWD", 210.0),
            ("境內", "D:23456789", "UI01", "", "統一黑馬基金", "統一投信", "TWD", 95.0),
            ("境內", "D:34567890", "YT01", "", "野村優質基金-累積型新臺幣計價", "野村投信", "TWD", 120.0),
            ("境內", "D:45678901", "DIM01", "", "兆豐人民幣貨幣市場基金", "兆豐投信", "CNY", 13.7),
            ("境外", "O:LU0011963245", "AB01", "LU0011963245", "安聯收益成長基金-AM穩定月收類股(美元)", "安聯", "USD", 8.5),
            ("境外", "O:LU0072462186", "BR01", "LU0072462186", "貝萊德世界科技基金 A2 美元", "貝萊德", "USD", 98.0),
            ("境外", "O:IE00B11XZ103", "PM01", "IE00B11XZ103", "PIMCO 全球投資級別債券基金-E級類別(累積)", "品浩太平洋", "USD", 17.0),
            ("期信", "F:FUT01", "FUT01", "", "元大道瓊白銀ER期貨信託基金", "元大期信", "TWD", 25.0)]
    rows = []
    today = dt.date.today()
    for region, fid, code, isin, name, comp, cur, nav0 in base:
        rng = np.random.default_rng(market._seed(fid))
        path = nav0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, 400)))
        for i in range(400):
            d = today - dt.timedelta(days=400 - i)
            if d.weekday() >= 5:
                continue
            rows.append({"id": fid, "region": region, "code": code, "isin": isin, "tax_id": fid[2:] if region == "境內" else "",
                         "name": name, "company": comp, "currency": cur, "date": d.isoformat(), "nav": round(float(path[i]), 4)})
    return rows
