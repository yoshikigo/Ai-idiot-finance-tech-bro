"""SQLite store for the research database (one file on the external drive, rollback journal for exFAT/NTFS).

Long tables keyed by (code, date|period); panels (date × code DataFrames) are built from them in
fields.py and cached on disk per data version.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DATA = Path(os.environ.get("FT_DATA", "/data"))
ROOT = DATA / "research"
DB = ROOT / "research.sqlite"
_lock = threading.RLock()
_dirty: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, adj REAL, volume REAL,
    PRIMARY KEY(code, date)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS prices_date ON prices(date);
CREATE TABLE IF NOT EXISTS valuation(code TEXT, date TEXT, pe REAL, dy REAL, pb REAL, PRIMARY KEY(code, date)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS insti(code TEXT, date TEXT, f_net REAL, t_net REAL, d_net REAL, all_net REAL,
    PRIMARY KEY(code, date)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS margin(code TEXT, date TEXT, margin REAL, short REAL, PRIMARY KEY(code, date)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS revenue(code TEXT, period TEXT, avail TEXT, revenue REAL, mom REAL, yoy REAL, cum REAL, cum_yoy REAL,
    PRIMARY KEY(code, period)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS income_ytd(code TEXT, period TEXT, avail TEXT, revenue REAL, gross REAL, op_income REAL, pretax REAL,
    net_income REAL, net_parent REAL, eps REAL, PRIMARY KEY(code, period)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS balance(code TEXT, period TEXT, avail TEXT, assets REAL, liabilities REAL, equity REAL,
    equity_parent REAL, capital REAL, PRIMARY KEY(code, period)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS fetch_log(dataset TEXT, market TEXT, key TEXT, status TEXT, rows INTEGER, error TEXT,
    tries INTEGER DEFAULT 0, ts TEXT, PRIMARY KEY(dataset, market, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

TABLE_COLS = {
    "prices": ("open", "high", "low", "close", "adj", "volume"),
    "valuation": ("pe", "dy", "pb"),
    "insti": ("f_net", "t_net", "d_net", "all_net"),
    "margin": ("margin", "short"),
    "revenue": ("avail", "revenue", "mom", "yoy", "cum", "cum_yoy"),
    "income_ytd": ("avail", "revenue", "gross", "op_income", "pretax", "net_income", "net_parent", "eps"),
    "balance": ("avail", "assets", "liabilities", "equity", "equity_parent", "capital"),
}
KEYCOL = {"revenue": "period", "income_ytd": "period", "balance": "period"}


def _connect() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=60, check_same_thread=False)
    c.execute("PRAGMA journal_mode=DELETE")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(SCHEMA)
    return c


_conn: sqlite3.Connection | None = None


@contextmanager
def conn():
    global _conn
    with _lock:
        if _conn is None or not DB.exists():
            _conn = _connect()
        yield _conn


def bump_version():
    """Publish pending writes: panels built from a table are rebuilt when its version changes."""
    stamp = dt.datetime.now().isoformat(timespec="microseconds")
    with conn() as c:
        for t in list(_dirty) + ["_all"]:
            c.execute("INSERT INTO meta(k,v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (f"v:{t}", stamp))
        c.commit()
        _dirty.clear()


def version(table: str = "_all") -> str:
    with conn() as c:
        r = c.execute("SELECT v FROM meta WHERE k=?", (f"v:{table}",)).fetchone()
    return r[0] if r else "0"


def upsert(table: str, rows: list[tuple]):
    """rows = (code, date|period, *TABLE_COLS[table])."""
    if not rows:
        return
    cols = ("code", KEYCOL.get(table, "date")) + TABLE_COLS[table]
    q = (f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
         f"ON CONFLICT(code,{cols[1]}) DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in cols[2:]))
    with conn() as c:
        c.executemany(q, rows)
        c.commit()
        _dirty.add(table)


def log_fetch(dataset: str, market: str, key: str, status: str, rows: int = 0, error: str = ""):
    with conn() as c:
        c.execute("""INSERT INTO fetch_log(dataset,market,key,status,rows,error,tries,ts) VALUES(?,?,?,?,?,?,1,?)
                     ON CONFLICT(dataset,market,key) DO UPDATE SET status=excluded.status, rows=excluded.rows,
                     error=excluded.error, tries=fetch_log.tries+1, ts=excluded.ts""",
                  (dataset, market, key, status, rows, error[:500], dt.datetime.now().isoformat(timespec="seconds")))
        c.commit()


def fetch_state(dataset: str) -> dict[tuple[str, str], tuple[str, int, str]]:
    with conn() as c:
        rows = c.execute("SELECT market,key,status,tries,ts FROM fetch_log WHERE dataset=?", (dataset,)).fetchall()
    return {(m, k): (s, t, ts) for m, k, s, t, ts in rows}


def coverage() -> dict:
    out = {}
    with conn() as c:
        for t in TABLE_COLS:
            k = KEYCOL.get(t, "date")
            n, codes, first, last = c.execute(f"SELECT COUNT(*), COUNT(DISTINCT code), MIN({k}), MAX({k}) FROM {t}").fetchone()
            out[t] = {"rows": n, "codes": codes, "first": first, "last": last}
        for ds, st, n, last in c.execute("SELECT dataset,status,COUNT(*),MAX(ts) FROM fetch_log GROUP BY dataset,status"):
            out.setdefault("_log", {}).setdefault(ds, {})[st] = {"n": n, "last": last}
        errs = c.execute("SELECT dataset,market,key,error,ts FROM fetch_log WHERE status='error' ORDER BY ts DESC LIMIT 12").fetchall()
        out["_errors"] = [dict(zip(("dataset", "market", "key", "error", "ts"), e)) for e in errs]
    return out
