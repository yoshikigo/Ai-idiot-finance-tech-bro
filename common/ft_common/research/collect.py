"""Keep the research database current and backfill history politely.

Order of work each loop (one HTTP request at a time, per-host pacing):
  1. prices      yfinance, raw + adjusted OHLCV for every listed/OTC stock & ETF (daily incremental,
                 full re-download for a symbol when its adjustment factor changed = new dividend/split)
  2. today's daily datasets (valuation, 三大法人, 融資融券) for 上市 + 上櫃 once published
  3. 月營收 for the last two months, 財報 for the last two quarters (until published)
  4. backfill, newest first: 月營收 / 財報 back to FT_RESEARCH_START (default 2013-01),
     daily datasets back FT_RESEARCH_DAILY_YEARS (default 5) years.

Failures are logged in fetch_log and retried later (errors after 2 h, "not yet published" after 6 h,
holidays never). Everything runs in one background thread inside ft-research.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time

import numpy as np
import pandas as pd

from .. import market, twmarket
from . import sources, store

log = logging.getLogger("ft.research")
TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Taipei, no DST
FAKE = market.FAKE
START = dt.date.fromisoformat(os.environ.get("FT_RESEARCH_START", "2013-01-01"))
DAILY_YEARS = float(os.environ.get("FT_RESEARCH_DAILY_YEARS", "5"))
BACKFILL = os.environ.get("FT_RESEARCH_BACKFILL", "1") not in ("0", "false", "no")
PACE = {"twse": 3.5, "tpex": 2.5, "mops": 4.5}
MARKETS = ("上市", "上櫃")
DAILY = {"valuation": (sources.get_valuation, "valuation"), "insti": (sources.get_insti, "insti"),
         "margin": (sources.get_margin, "margin")}
# when each daily dataset is normally out (Taipei time)
READY = {"valuation": (17, 30), "insti": (17, 0), "margin": (21, 45)}

state = {"running": False, "task": "", "prices_task": "", "done": 0, "last_error": "", "started": None, "prices_at": None, "paused": False}
_last_hit: dict[str, float] = {}
_stop = threading.Event()


def now_tw() -> dt.datetime:
    return dt.datetime.now(TZ)


def _pace(host: str):
    wait = PACE[host] - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        _stop.wait(wait)
    _last_hit[host] = time.time()


# ---------------------------------------------------------------- universe
_uni: tuple[float, dict] = (0.0, {})


def universe() -> dict[str, dict]:
    """code → security info (listed + OTC stocks and ETFs). Cached 10 minutes."""
    global _uni
    if time.time() - _uni[0] < 600 and _uni[1]:
        return _uni[1]
    sec = twmarket.securities()
    out = {c: s for c, s in sec.items() if s.get("market") in MARKETS and s.get("kind") in ("股票", "ETF", "特別股", "存託憑證")}
    if FAKE:
        out.update(fake_extra())
    _uni = (time.time(), out)
    return out


def fake_extra() -> dict[str, dict]:
    inds = ["半導體業", "電子零組件業", "電腦及週邊設備業", "光電業", "通信網路業", "航運業", "鋼鐵工業", "生技醫療業",
            "食品工業", "金融保險業", "建材營造業", "塑膠工業"]
    return {f"9{i:03d}": {"code": f"9{i:03d}", "name": f"示範{i:02d}", "market": "上市" if i % 3 else "上櫃",
                          "industry": inds[i % len(inds)], "kind": "股票", "symbol": f"9{i:03d}.{'TW' if i % 3 else 'TWO'}"}
            for i in range(1, 91)}


# ---------------------------------------------------------------- prices (yfinance)
def _download(symbols: list[str], start: str, end: str | None = None) -> dict[str, pd.DataFrame]:
    if FAKE:
        e = dt.date.fromisoformat(end) if end else dt.date.today()
        out = {}
        for s in symbols:
            df = market._fake_history(s, dt.date.fromisoformat(start), e)
            df["Adj Close"] = df["Close"]
            out[s] = df
        return out
    import yfinance as yf
    raw = yf.download(symbols, start=start, end=end, auto_adjust=False, group_by="ticker", threads=True,
                      progress=False, timeout=60)
    out = {}
    if raw is None or raw.empty:
        return out
    for s in symbols:
        try:
            df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.dropna(how="all")
        if len(df) and "Close" in df:
            out[s] = df
    return out


def _price_rows(code: str, df: pd.DataFrame) -> list[tuple]:
    rows = []
    adj = df["Adj Close"] if "Adj Close" in df else df["Close"]
    for d, o, h, lo, c, a, v in zip(df.index, df["Open"], df["High"], df["Low"], df["Close"], adj, df["Volume"]):
        if c != c or c is None:
            continue
        rows.append((code, pd.Timestamp(d).date().isoformat(), float(o), float(h), float(lo), float(c), float(a), float(v or 0)))
    return rows


def update_prices(full: bool = False, chunk: int = 80) -> int:
    uni = universe()
    with store.conn() as c:
        have = dict(c.execute("SELECT code, MAX(date) FROM prices GROUP BY code").fetchall())
        ratios = {}
        if have and not full:
            for code, d, cl, ad in c.execute("SELECT p.code,p.date,p.close,p.adj FROM prices p JOIN "
                                             "(SELECT code, MAX(date) d FROM prices GROUP BY code) m ON p.code=m.code AND p.date=m.d"):
                ratios[code] = (d, ad / cl if cl else 1.0)
    todo_full = [code for code in uni if full or code not in have]
    todo_inc = [code for code in uni if code in have and not full]
    n = 0
    for group, start in ((todo_full, START.isoformat()),
                         (todo_inc, (dt.date.today() - dt.timedelta(days=12)).isoformat())):
        for i in range(0, len(group), chunk):
            if _stop.is_set():
                return n
            part = group[i:i + chunk]
            state["prices_task"] = f"股價{'完整下載' if start == START.isoformat() else '更新'} {i + len(part)}/{len(group)}"
            syms = {uni[c]["symbol"]: c for c in part}
            try:
                got = _download(list(syms), start)
            except Exception as e:  # noqa: BLE001
                state["last_error"] = f"yfinance: {e}"
                log.warning("yfinance chunk failed: %s", e)
                continue
            refetch = []
            for sym, df in got.items():
                code = syms[sym]
                rows = _price_rows(code, df)
                if code in ratios and rows:  # dividend/split since last time → adjusted history changed
                    d0, r0 = ratios[code]
                    same = [r for r in rows if r[1] == d0]
                    if same and same[0][5] and abs(same[0][6] / same[0][5] - r0) > 1e-3 * max(r0, 1e-9):
                        refetch.append(sym)
                        continue
                store.upsert("prices", rows)
                n += len(rows)
            if refetch:
                for sym, df in _download(refetch, START.isoformat()).items():
                    store.upsert("prices", _price_rows(syms[sym], df))
            if not FAKE:
                _stop.wait(2)
    store.bump_version()
    state["prices_at"] = dt.datetime.now().isoformat(timespec="seconds")
    state["prices_task"] = ""
    return n


# ---------------------------------------------------------------- one fetch
def _write_daily(dataset: str, d: dt.date, data: dict[str, dict]) -> int:
    ds = d.isoformat()
    if dataset == "valuation":
        rows = [(c, ds, v["pe"], v["dy"], v["pb"]) for c, v in data.items()]
    elif dataset == "insti":
        rows = [(c, ds, v["foreign"], v["trust"], v["dealer"], v["total"]) for c, v in data.items()]
    else:
        rows = [(c, ds, v["margin"], v["short"]) for c, v in data.items()]
    store.upsert(dataset, rows)
    return len(rows)


def fetch_daily(dataset: str, market_: str, d: dt.date) -> str:
    fn = DAILY[dataset][0]
    _pace("twse" if market_ == "上市" else "tpex")
    key = d.isoformat()
    try:
        data = fn(market_, d)
        n = _write_daily(dataset, d, data)
        store.log_fetch(dataset, market_, key, "ok" if n else "nodata", n)
        return "ok"
    except sources.NoData as e:
        store.log_fetch(dataset, market_, key, "nodata", 0, str(e))
        return "nodata"
    except Exception as e:  # noqa: BLE001
        store.log_fetch(dataset, market_, key, "error", 0, f"{type(e).__name__}: {e}")
        state["last_error"] = f"{dataset} {market_} {key}: {e}"
        return "error"


def fetch_disposal(market_: str, y: int, m: int) -> str:
    """處置股 announced in one month (both markets keep a date-range query)."""
    key = f"{y}-{m:02d}"
    a = dt.date(y, m, 1)
    b = min((a + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1), dt.date.today())
    _pace("twse" if market_ == "上市" else "tpex")
    try:
        rows = sources.get_disposal(market_, a, b)
        store.upsert("disposal", [(c, s0.isoformat(), e0.isoformat(), market_, why) for c, s0, e0, why in rows])
        store.log_fetch("disposal", market_, key, "ok" if rows else "nodata", len(rows))
        return "ok"
    except sources.NoData as e:
        store.log_fetch("disposal", market_, key, "nodata", 0, str(e))
        return "nodata"
    except Exception as e:  # noqa: BLE001
        store.log_fetch("disposal", market_, key, "error", 0, f"{type(e).__name__}: {e}")
        state["last_error"] = f"處置股 {market_} {key}: {e}"
        return "error"


def _disposal_due(st: tuple | None, recent: bool) -> bool:
    """Recent months (still filling up) twice a day; past months once (errors retried like the rest)."""
    if st is None:
        return True
    status, tries, ts = st
    age = (dt.datetime.now() - dt.datetime.fromisoformat(ts)).total_seconds() if ts else 1e9
    if status in ("ok", "nodata"):
        return recent and age > 12 * 3600
    return tries < 8 and age > 2 * 3600 * min(tries, 4)


def fetch_revenue(market_: str, y: int, m: int) -> str:
    key = f"{y}-{m:02d}"
    _pace("mops")
    try:
        data = sources.get_revenue(market_, y, m)
        av = sources.revenue_available(y, m).isoformat()
        store.upsert("revenue", [(c, key, av, v["revenue"], v["mom"], v["yoy"], v["cum"], v["cum_yoy"]) for c, v in data.items()])
        store.log_fetch("revenue", market_, key, "ok", len(data))
        return "ok"
    except sources.NoData as e:
        store.log_fetch("revenue", market_, key, "nodata", 0, str(e))
        return "nodata"
    except Exception as e:  # noqa: BLE001
        store.log_fetch("revenue", market_, key, "error", 0, f"{type(e).__name__}: {e}")
        state["last_error"] = f"revenue {market_} {key}: {e}"
        return "error"


def fetch_statement(kind: str, market_: str, y: int, q: int) -> str:
    key = f"{y}Q{q}"
    ds = "income_ytd" if kind == "income" else "balance"
    _pace("mops")
    try:
        data = sources.get_statement(kind, market_, y, q)
        av = sources.statement_available(y, q).isoformat()
        cols = store.TABLE_COLS[ds][1:]
        store.upsert(ds, [(c, key, av, *[v.get(k) for k in cols]) for c, v in data.items()])
        store.log_fetch(ds, market_, key, "ok", len(data))
        return "ok"
    except sources.NoData as e:
        store.log_fetch(ds, market_, key, "nodata", 0, str(e))
        return "nodata"
    except Exception as e:  # noqa: BLE001
        store.log_fetch(ds, market_, key, "error", 0, f"{type(e).__name__}: {e}")
        state["last_error"] = f"{ds} {market_} {key}: {e}"
        return "error"


# ---------------------------------------------------------------- planning
def _due(st: tuple | None, today_key: bool, periodic: bool = False, early: bool = False) -> bool:
    """periodic=True for 月營收／財報: a past period never legitimately has "no data" (unlike a holiday).
    early=True: the period's legal deadline (+3 days) hasn't passed, so an "ok" fetch is only the companies that
    filed so far — refresh it twice a day until the deadline instead of freezing a partial period forever."""
    if st is None:
        return True
    status, tries, ts = st
    age = (dt.datetime.now() - dt.datetime.fromisoformat(ts)).total_seconds() if ts else 1e9
    if status == "ok":
        return early and age > 12 * 3600
    if status == "nodata":  # daily: recent = maybe not published yet, old = a holiday
        if periodic:
            return age > (1800 if today_key else 24 * 3600) and tries < 20
        return today_key and age > 1800
    return tries < 8 and age > 2 * 3600 * min(tries, 4)


def months_back(n_from: dt.date, until: dt.date):
    y, m = n_from.year, n_from.month
    while (y, m) >= (until.year, until.month):
        yield y, m
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)


def quarters_back(n_from: dt.date, until: dt.date):
    y, q = n_from.year, (n_from.month - 1) // 3 + 1
    while (y, q) >= (until.year, (until.month - 1) // 3 + 1):
        yield y, q
        y, q = (y - 1, 4) if q == 1 else (y, q - 1)


def plan(limit: int = 1) -> list[tuple]:
    """Next tasks, most urgent first."""
    t = now_tw()
    today = t.date()
    tasks: list[tuple] = []
    logs = {ds: store.fetch_state(ds) for ds in ("valuation", "insti", "margin", "revenue", "income_ytd", "balance", "disposal")}
    # recent daily data (last 7 weekdays; today only once it is normally published)
    for back in range(0, 10):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        for ds in DAILY:
            if back == 0 and (t.hour, t.minute) < READY[ds]:
                continue
            for mk in MARKETS:
                if _due(logs[ds].get((mk, d.isoformat())), back <= 2):
                    tasks.append(("daily", ds, mk, d))
        if len(tasks) >= limit:
            return tasks[:limit]
    # latest revenue months / statement quarters
    for y, m in list(months_back(today.replace(day=1) - dt.timedelta(days=1), today - dt.timedelta(days=70))):
        for mk in MARKETS:
            if _due(logs["revenue"].get((mk, f"{y}-{m:02d}")), True, True,
                    today <= sources.revenue_available(y, m) + dt.timedelta(days=3)):
                tasks.append(("revenue", mk, y, m))
    for y, q in list(quarters_back(today - dt.timedelta(days=45), today - dt.timedelta(days=200))):
        early = today <= sources.statement_available(y, q) + dt.timedelta(days=3)
        for kind, ds in (("income", "income_ytd"), ("balance", "balance")):
            for mk in MARKETS:
                if _due(logs[ds].get((mk, f"{y}Q{q}")), True, True, early):
                    tasks.append(("stmt", kind, mk, y, q))
    for y, m in list(months_back(today, today.replace(day=1) - dt.timedelta(days=1))):  # 處置股: this & last month
        for mk in MARKETS:
            if _disposal_due(logs["disposal"].get((mk, f"{y}-{m:02d}")), True):
                tasks.append(("disposal", mk, y, m))
    if len(tasks) >= limit or not BACKFILL:
        return tasks[:limit]
    # backfill, newest first
    for y, m in months_back(today - dt.timedelta(days=70), START):
        for mk in MARKETS:
            if _due(logs["revenue"].get((mk, f"{y}-{m:02d}")), False, True):
                tasks.append(("revenue", mk, y, m))
                if len(tasks) >= limit:
                    return tasks
    for y, q in quarters_back(today - dt.timedelta(days=200), START):
        for kind, ds in (("income", "income_ytd"), ("balance", "balance")):
            for mk in MARKETS:
                if _due(logs[ds].get((mk, f"{y}Q{q}")), False, True):
                    tasks.append(("stmt", kind, mk, y, q))
                    if len(tasks) >= limit:
                        return tasks
    for y, m in months_back(today.replace(day=1) - dt.timedelta(days=32), START):
        for mk in MARKETS:
            if _disposal_due(logs["disposal"].get((mk, f"{y}-{m:02d}")), False):
                tasks.append(("disposal", mk, y, m))
                if len(tasks) >= limit:
                    return tasks
    stop = today - dt.timedelta(days=int(365.25 * DAILY_YEARS))
    d = today - dt.timedelta(days=10)
    while d >= stop:
        if d.weekday() < 5:
            for ds in DAILY:
                for mk in MARKETS:
                    if _due(logs[ds].get((mk, d.isoformat())), False):
                        tasks.append(("daily", ds, mk, d))
                        if len(tasks) >= limit:
                            return tasks
        d -= dt.timedelta(days=1)
    return tasks[:limit]


def run_task(task: tuple) -> str:
    kind = task[0]
    if kind == "daily":
        _, ds, mk, d = task
        state["task"] = f"{ds} {mk} {d}"
        return fetch_daily(ds, mk, d)
    if kind == "disposal":
        _, mk, y, m = task
        state["task"] = f"處置股 {mk} {y}-{m:02d}"
        return fetch_disposal(mk, y, m)
    if kind == "revenue":
        _, mk, y, m = task
        state["task"] = f"月營收 {mk} {y}-{m:02d}"
        return fetch_revenue(mk, y, m)
    _, k, mk, y, q = task
    state["task"] = f"財報 {k} {mk} {y}Q{q}"
    return fetch_statement(k, mk, y, q)


def warm():
    """Build the big price panels now (background) so the first backtest of the day is fast."""
    from . import fields
    try:
        state["prices_task"] = "整理股價面板"
        fields.calendar()
        fields._price_all()
    except Exception as e:  # noqa: BLE001
        log.warning("warm-up failed: %s", e)


def prices_loop():
    """Prices (yfinance) run in their own thread so the official sources do not wait for a 20-minute download."""
    last = None
    while not _stop.is_set():
        t = now_tw()
        if not state["paused"] and (last is None or (t.date() != last and t.hour >= 15)):
            try:
                state["prices_task"] = "股價下載中"
                update_prices()
                last = t.date()
                warm()
                state["prices_task"] = ""
            except Exception as e:  # noqa: BLE001
                log.exception("prices")
                state["last_error"] = f"股價：{type(e).__name__}: {e}"
                _stop.wait(600)
                continue
        _stop.wait(300)


def loop_forever():
    state.update(running=True, started=dt.datetime.now().isoformat(timespec="seconds"))
    if FAKE:
        seed_fake()
    else:
        threading.Thread(target=prices_loop, daemon=True, name="research-prices").start()
        # ≤ -7 logged a blocked/changed MOPS page as "no data" and never retried it: retry those now
        with store.conn() as c:
            c.execute("UPDATE fetch_log SET ts='2000-01-01T00:00:00' WHERE status='nodata' "
                      "AND dataset IN ('revenue','income_ytd','balance') AND tries < 20")
            c.commit()
    last_bump, pending = time.time(), 0
    while not _stop.is_set():
        if state["paused"]:
            _stop.wait(30)
            continue
        try:
            tasks = [] if FAKE else plan(1)
            if not tasks:
                if pending:
                    store.bump_version()
                    pending = 0
                state["task"] = "閒置（資料已是最新）"
                _stop.wait(300)
                continue
            res = run_task(tasks[0])
            state["done"] += 1
            if res == "ok":
                pending += 1
            # publish soon: monthly/quarterly data right away, daily data at least every 2 minutes
            if pending and (tasks[0][0] in ("revenue", "stmt", "disposal") or time.time() - last_bump > 120):
                store.bump_version()
                last_bump, pending = time.time(), 0
        except Exception as e:  # noqa: BLE001
            log.exception("research collector")
            state["last_error"] = f"{type(e).__name__}: {e}"
            _stop.wait(60)
    state["running"] = False


def progress() -> dict:
    """Per-dataset fetch counts, for messages like「月營收還沒有資料：已抓 3 批、失敗 12 批」."""
    out = {}
    with store.conn() as c:
        for ds, st, n in c.execute("SELECT dataset, status, COUNT(*) FROM fetch_log GROUP BY dataset, status"):
            out.setdefault(ds, {})[st] = n
        last_err = dict(c.execute("SELECT dataset, error FROM fetch_log WHERE status='error' ORDER BY ts").fetchall())
    for ds in out:
        if ds in last_err:
            out[ds]["last_error"] = last_err[ds]
    return out


# ---------------------------------------------------------------- demo data (FT_FAKE_DATA=1)
def seed_fake(start: dt.date = dt.date(2016, 1, 1)):
    with store.conn() as c:
        if c.execute("SELECT COUNT(*) FROM prices").fetchone()[0]:
            return
    state["task"] = "產生示範資料"
    uni = universe()
    update_prices(full=True)
    days = pd.bdate_range(max(start, dt.date.today() - dt.timedelta(days=int(365.25 * DAILY_YEARS))), dt.date.today())
    val, ins, mar, rev, inc, bal = [], [], [], [], [], []
    for code, s in uni.items():
        if s.get("kind") != "股票":
            continue
        r = np.random.default_rng(market._seed(code))
        pe0, dy0, pb0 = r.uniform(8, 35), r.uniform(0.5, 7), r.uniform(0.7, 6)
        drift = r.normal(0, 0.002, len(days)).cumsum()
        for i, d in enumerate(days):
            ds = d.date().isoformat()
            f = np.exp(drift[i])
            val.append((code, ds, round(pe0 * f, 2), round(dy0 / f, 2), round(pb0 * f, 2)))
            fn = float(r.normal(0, 800))
            tn = float(r.normal(0, 150))
            dn = float(r.normal(0, 200))
            ins.append((code, ds, fn, tn, dn, fn + tn + dn))
            mar.append((code, ds, float(abs(r.normal(5000, 1500))), float(abs(r.normal(400, 150)))))
        base = r.uniform(5e5, 5e7)
        g = r.normal(0.08, 0.15)
        prev = {}
        for y in range(start.year, dt.date.today().year + 1):
            for m in range(1, 13):
                av = sources.revenue_available(y, m)
                if av > dt.date.today():
                    break
                v = base * (1 + g) ** (y - start.year + m / 12) * r.uniform(0.85, 1.15)
                ly = prev.get((y - 1, m))
                pm = prev.get((y, m - 1)) if m > 1 else prev.get((y - 1, 12))
                prev[(y, m)] = v
                rev.append((code, f"{y}-{m:02d}", av.isoformat(), v, (v / pm - 1) * 100 if pm else None,
                            (v / ly - 1) * 100 if ly else None, None, None))
            for q in range(1, 5):
                av = sources.statement_available(y, q)
                if av > dt.date.today():
                    break
                ytd_rev = sum(prev.get((y, m), 0) for m in range(1, q * 3 + 1))
                gm, om = r.uniform(0.15, 0.55), r.uniform(0.05, 0.3)
                ni = ytd_rev * om * 0.8
                cap = r.uniform(1e6, 3e7)
                inc.append((code, f"{y}Q{q}", av.isoformat(), ytd_rev, ytd_rev * gm, ytd_rev * om, ni * 1.2, ni, ni * 0.98,
                            ni / (cap / 10)))
                assets = ytd_rev * r.uniform(1.5, 4)
                liab = assets * r.uniform(0.2, 0.7)
                bal.append((code, f"{y}Q{q}", av.isoformat(), assets, liab, assets - liab, (assets - liab) * 0.97, cap))
    store.upsert("valuation", val)
    store.upsert("insti", ins)
    store.upsert("margin", mar)
    store.upsert("revenue", rev)
    store.upsert("income_ytd", inc)
    store.upsert("balance", bal)
    dis = []
    for code in [c for c, x in uni.items() if x.get("kind") == "股票"][:: 9]:
        r = np.random.default_rng(market._seed(code) + 7)
        for _ in range(2):
            a = days[int(r.integers(30, len(days) - 15))].date()
            dis.append((code, a.isoformat(), (a + dt.timedelta(days=13)).isoformat(), "上市" if code[-1] in "12345" else "上櫃", "示範處置"))
    store.upsert("disposal", dis)
    for ds in ("valuation", "insti", "margin", "revenue", "income_ytd", "balance", "disposal"):
        store.log_fetch(ds, "示範", "seed", "ok", 1)
    store.bump_version()
    state["task"] = "示範資料就緒"
