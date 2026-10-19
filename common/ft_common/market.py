"""Market data via yfinance (free, no key; works for US + Taiwan `.TW` / `.TWO`).

FT_FAKE_DATA=1 switches to deterministic synthetic data (offline demo / tests).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import os
import threading
import time
from typing import Any

import numpy as np
import pandas as pd

FAKE = os.environ.get("FT_FAKE_DATA", "") in ("1", "true", "yes")

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def cached(key: str, ttl: float, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
        if len(_cache) > 2000:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:500]:
                _cache.pop(k, None)
    return val


def _yf():
    import yfinance as yf  # imported lazily so FAKE mode never needs network
    return yf


def _f(x):
    try:
        if x is None:
            return None
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- fake data
def _seed(sym: str) -> int:
    return int(hashlib.md5(sym.encode()).hexdigest()[:8], 16)


def _fake_history(sym: str, start: dt.date, end: dt.date, interval: str = "1d") -> pd.DataFrame:
    rng = np.random.default_rng(_seed(sym))
    days = pd.bdate_range(dt.date(2015, 1, 1), dt.date.today())
    base = 20 + (_seed(sym) % 900)
    if sym[:2] == "00":  # Taiwan ETFs trade at 10–150
        base = 12 + (_seed(sym) % 90)
    rets = rng.normal(0.0004, 0.018, len(days))
    close = base * np.exp(np.cumsum(rets))
    openp = close * (1 + rng.normal(0, 0.004, len(days)))
    high = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, 0.006, len(days))))
    low = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, 0.006, len(days))))
    vol = rng.integers(2_000_000, 40_000_000, len(days))
    df = pd.DataFrame({"Open": openp, "High": high, "Low": low, "Close": close, "Volume": vol}, index=days)
    df.index.name = "Date"
    df = df[(df.index.date >= start) & (df.index.date <= end)]
    if interval in ("5m", "15m", "30m", "60m", "1h"):
        last = df.tail(5 if interval != "5m" else 1)
        rows = []
        step = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "1h": 60}[interval]
        for d, r in last.iterrows():
            n = 390 // step
            path = r.Open + np.cumsum(rng.normal(0, r.Open * 0.002, n))
            for i in range(n):
                t = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30 + i * step)
                p = path[i]
                rows.append((t, p, p * 1.001, p * 0.999, p, int(r.Volume / n)))
        df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
    return df


# ---------------------------------------------------------------- history
PERIOD_DAYS = {"1d": 1, "5d": 5, "1mo": 31, "3mo": 92, "6mo": 183, "ytd": None, "1y": 366,
               "2y": 731, "5y": 1827, "10y": 3653, "max": 10000}


def history(sym: str, period: str = "1y", interval: str = "1d", start: str | None = None,
            end: str | None = None) -> pd.DataFrame:
    """OHLCV DataFrame indexed by timestamp (adjusted prices)."""
    key = f"h:{sym}:{period}:{interval}:{start}:{end}"
    ttl = 30 if interval.endswith("m") else 300

    def load():
        if FAKE:
            e = dt.date.fromisoformat(end) if end else dt.date.today()
            if start:
                s = dt.date.fromisoformat(start)
            else:
                days = PERIOD_DAYS.get(period) or (e - dt.date(e.year, 1, 1)).days
                s = e - dt.timedelta(days=days + (3 if period in ("1d", "5d") else 0))
            return _fake_history(sym, s, e, interval)
        t = _yf().Ticker(sym)
        if start:
            df = t.history(start=start, end=end, interval=interval, auto_adjust=True)
        else:
            df = t.history(period=period, interval=interval, auto_adjust=True)
        return clean_ohlcv(df) if len(df) else df

    return cached(key, ttl, load)


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Yahoo sometimes returns a half-filled bar (today's TW row with Volume but Close=NaN, or a NaN Open):
    drop rows without a close, fill the other prices from the close and missing volume with 0."""
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]).copy()
    for c in ("Open", "High", "Low"):
        df[c] = df[c].fillna(df["Close"])
    df["Volume"] = df["Volume"].fillna(0)
    return df


def download(symbols: list[str], start: str, end: str | None = None) -> dict[str, pd.DataFrame]:
    """Daily adjusted OHLCV for many symbols → {symbol: df}. Used by Qlib / FinRL."""
    out: dict[str, pd.DataFrame] = {}
    if FAKE:
        e = dt.date.fromisoformat(end) if end else dt.date.today()
        for s in symbols:
            out[s] = _fake_history(s, dt.date.fromisoformat(start), e)
        return out
    data = _yf().download(symbols, start=start, end=end, auto_adjust=True, group_by="ticker",
                          progress=False, threads=True)
    for s in symbols:
        try:
            df = data[s] if isinstance(data.columns, pd.MultiIndex) else data
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if len(df):
                out[s] = df
        except KeyError:
            continue
    return out


# ---------------------------------------------------------------- quotes
def quotes(symbols: list[str]) -> list[dict]:
    symbols = [s for s in dict.fromkeys(symbols) if s]
    # Taiwan listings + TAIEX/TPEx index: real-time from TWSE MIS (Chinese names, volume in lots)
    tw = [s for s in symbols if s.endswith((".TW", ".TWO")) or s in ("^TWII", "^TWOII")]
    mis: dict = {}
    if tw and not FAKE:
        try:
            from . import twmarket
            mis = cached("mis:" + ",".join(sorted(tw)), 5, lambda: twmarket.realtime(tw))
        except Exception:  # noqa: BLE001
            mis = {}

    def one(sym):
        if sym in mis:
            return mis[sym]

        def load():
            df = history(sym, "5d", "1d")
            if df is None or len(df) == 0:
                return {"symbol": sym, "error": "no data"}
            last = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2]) if len(df) > 1 else last
            row = {"symbol": sym, "last": last, "prev": prev, "chg": last - prev,
                   "pct": (last / prev - 1) * 100 if prev else 0.0,
                   "high": _f(df["High"].iloc[-1]), "low": _f(df["Low"].iloc[-1]),
                   "volume": _f(df["Volume"].iloc[-1]),
                   "time": pd.Timestamp(df.index[-1]).isoformat()}
            if not FAKE:
                try:
                    fi = _yf().Ticker(sym).fast_info
                    lp = _f(fi.get("lastPrice") if hasattr(fi, "get") else fi.last_price)
                    pc = _f(fi.get("previousClose") if hasattr(fi, "get") else fi.previous_close)
                    if lp:
                        row["last"] = lp
                        if pc:
                            row["prev"], row["chg"], row["pct"] = pc, lp - pc, (lp / pc - 1) * 100
                    row["currency"] = fi.get("currency") if hasattr(fi, "get") else None
                except Exception:
                    pass
            return row
        try:
            return cached(f"q:{sym}", 15, load)
        except Exception as e:  # noqa: BLE001
            return {"symbol": sym, "error": str(e)[:120]}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(one, symbols))


# ---------------------------------------------------------------- reference
INFO_KEYS = ["longName", "shortName", "symbol", "exchange", "currency", "quoteType", "sector", "industry",
             "country", "city", "website", "fullTimeEmployees", "longBusinessSummary", "marketCap",
             "enterpriseValue", "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
             "pegRatio", "trailingEps", "forwardEps", "dividendYield", "payoutRatio", "beta",
             "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage", "twoHundredDayAverage",
             "averageVolume", "sharesOutstanding", "floatShares", "heldPercentInsiders",
             "heldPercentInstitutions", "profitMargins", "grossMargins", "operatingMargins",
             "returnOnEquity", "returnOnAssets", "revenueGrowth", "earningsGrowth", "totalRevenue",
             "totalCash", "totalDebt", "debtToEquity", "currentRatio", "freeCashflow",
             "recommendationKey", "targetMeanPrice", "numberOfAnalystOpinions"]


def info(sym: str) -> dict:
    def load():
        if FAKE:
            rng = np.random.default_rng(_seed(sym))
            return {"symbol": sym, "longName": f"{sym} Demo Corp.", "sector": "Technology",
                    "industry": "Semiconductors", "country": "Taiwan" if ".TW" in sym else "United States",
                    "currency": "TWD" if ".TW" in sym else "USD", "marketCap": float(rng.integers(1, 3000)) * 1e9,
                    "trailingPE": float(rng.uniform(8, 45)), "forwardPE": float(rng.uniform(8, 35)),
                    "priceToBook": float(rng.uniform(1, 12)), "beta": float(rng.uniform(0.5, 1.8)),
                    "dividendYield": float(rng.uniform(0, 4)), "profitMargins": float(rng.uniform(0.05, 0.4)),
                    "grossMargins": float(rng.uniform(0.2, 0.7)), "returnOnEquity": float(rng.uniform(0.05, 0.4)),
                    "revenueGrowth": float(rng.uniform(-0.1, 0.4)), "debtToEquity": float(rng.uniform(5, 120)),
                    "longBusinessSummary": "Synthetic company used in FT_FAKE_DATA demo mode.",
                    "fullTimeEmployees": int(rng.integers(100, 80000)), "exchange": "DEMO",
                    "quoteType": "EQUITY"}
        raw = _yf().Ticker(sym).get_info() or {}
        return {k: raw.get(k) for k in INFO_KEYS if raw.get(k) is not None}
    return cached(f"i:{sym}", 3600, load)


def news(sym: str, limit: int = 20) -> list[dict]:
    def load():
        if FAKE:
            rng = np.random.default_rng(_seed(sym) + dt.date.today().toordinal())
            heads = ["beats quarterly revenue estimates", "announces new AI accelerator", "faces supply chain delays",
                     "shares slip after guidance cut", "expands capacity in Arizona", "signs multiyear cloud deal",
                     "analysts raise price target", "regulators open inquiry", "launches buyback program",
                     "CEO comments on demand outlook"]
            out = []
            for i in range(min(limit, 12)):
                t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=int(rng.integers(1, 24 * 20)))
                h = heads[int(rng.integers(0, len(heads)))]
                out.append({"title": f"{sym} {h}", "publisher": "Demo Wire", "link": "",
                            "time": t.isoformat(), "summary": f"Synthetic headline: {sym} {h}."})
            return sorted(out, key=lambda x: x["time"], reverse=True)
        out = _yf_news(sym, limit)
        if not out and sym in NEWS_PROXY:          # Yahoo has no news feed for most indices / FX / futures
            out = _yf_news(NEWS_PROXY[sym][0], limit)
        if len(out) < 3:                           # last resort: Google News in English
            out += [x for x in news_en(news_query_en(sym), limit) if x["title"] not in {o["title"] for o in out}]
        return out[:limit]
    return cached(f"n:{sym}:{limit}", 600, load)


NEWS_PROXY = {  # symbol → (ETF whose Yahoo news covers it, English search words)
    "^GSPC": ("SPY", "S&P 500 stocks"), "^DJI": ("DIA", "Dow Jones stocks"), "^IXIC": ("QQQ", "Nasdaq stocks"),
    "^NDX": ("QQQ", "Nasdaq 100"), "^SOX": ("SOXX", "semiconductor stocks"), "^RUT": ("IWM", "Russell 2000"),
    "^VIX": ("VIXY", "VIX volatility"), "^TWII": ("EWT", "Taiwan stocks TAIEX"), "^TWOII": ("EWT", "Taiwan stocks"),
    "^N225": ("EWJ", "Nikkei 225"), "^HSI": ("FXI", "Hang Seng"), "000001.SS": ("FXI", "Shanghai Composite"),
    "^KS11": ("EWY", "KOSPI Korea stocks"), "^STOXX50E": ("FEZ", "Euro Stoxx 50"), "^GDAXI": ("EWG", "DAX Germany stocks"),
    "^FTSE": ("EWU", "FTSE 100"), "^FCHI": ("EWQ", "CAC 40"), "^BSESN": ("INDA", "Sensex India stocks"),
    "^AXJO": ("EWA", "ASX 200"), "^TNX": ("IEF", "10-year Treasury yield"), "^TYX": ("TLT", "30-year Treasury yield"),
    "^FVX": ("IEF", "5-year Treasury yield"), "^IRX": ("SHY", "Treasury bills"),
    "TWD=X": ("EWT", "Taiwan dollar"), "JPY=X": ("FXY", "Japanese yen"), "EURUSD=X": ("FXE", "euro dollar"),
    "DX-Y.NYB": ("UUP", "US dollar index"), "CL=F": ("USO", "crude oil prices"), "BZ=F": ("BNO", "Brent crude"),
    "NG=F": ("UNG", "natural gas prices"), "GC=F": ("GLD", "gold prices"), "SI=F": ("SLV", "silver prices"),
    "HG=F": ("CPER", "copper prices"), "BTC-USD": ("IBIT", "bitcoin"), "ETH-USD": ("ETHA", "ethereum"),
}


def news_query_en(sym: str) -> str:
    if sym in NEWS_PROXY:
        return NEWS_PROXY[sym][1]
    try:
        i = info(sym)
        name = i.get("shortName") or i.get("longName")
    except Exception:  # noqa: BLE001
        name = None
    if name:
        name = name.replace(",", " ").replace(" Inc.", "").replace(" Inc", "").replace(" Corporation", "").replace(" Ltd.", "").strip()
        return f'"{name}"'
    return sym.split(".")[0] + " stock"


def news_en(query: str, limit: int = 20) -> list[dict]:
    """Google News RSS, English edition (US)."""
    if FAKE:
        return []
    from urllib.parse import quote_plus
    from . import twmarket
    from .twhttp import fetch
    try:
        body = fetch(f"https://news.google.com/rss/search?q={quote_plus(query)}+when:7d&hl=en-US&gl=US&ceid=US:en",
                     timeout=15, ttl=600)
        return [{**x, "summary": ""} for x in twmarket._parse_gn(body)[:limit]]  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return []


def _yf_news(sym: str, limit: int) -> list[dict]:
    try:
        items = _yf().Ticker(sym).get_news(count=limit) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in items:
        c = it.get("content") or it
        title = c.get("title")
        if not title:
            continue
        t = c.get("pubDate") or c.get("displayTime")
        if not t and it.get("providerPublishTime"):
            t = dt.datetime.fromtimestamp(it["providerPublishTime"], dt.timezone.utc).isoformat()
        prov = c.get("provider") or {}
        url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or it.get("link")
        out.append({"title": title, "publisher": prov.get("displayName") if isinstance(prov, dict) else it.get("publisher"),
                    "link": url, "time": t, "summary": c.get("summary") or c.get("description") or ""})
    return out


def search(q: str, limit: int = 10) -> list[dict]:
    def load():
        if FAKE:
            return [{"symbol": q.upper(), "name": f"{q.upper()} Demo Corp.", "exchange": "DEMO", "type": "EQUITY"}]
        res = _yf().Search(q, max_results=limit, news_count=0).quotes or []
        return [{"symbol": r.get("symbol"), "name": r.get("shortname") or r.get("longname"),
                 "exchange": r.get("exchDisp") or r.get("exchange"), "type": r.get("quoteType")} for r in res]
    return cached(f"s:{q}", 3600, load)


FA_ROWS = {
    "income": ["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "Diluted EPS", "EBITDA"],
    "balance": ["Total Assets", "Total Liabilities Net Minority Interest", "Stockholders Equity", "Total Debt",
                "Cash And Cash Equivalents", "Ordinary Shares Number"],
    "cashflow": ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Repurchase Of Capital Stock",
                 "Cash Dividends Paid"],
}


def statements(sym: str, freq: str = "yearly") -> dict:
    """Financial statements as {kind: {columns:[dates], rows:[[label, v...]]}}."""
    def load():
        out = {}
        if FAKE:
            rng = np.random.default_rng(_seed(sym))
            cols = [str(dt.date.today().year - i - 1) + "-12-31" for i in range(4)]
            for kind, labels in FA_ROWS.items():
                out[kind] = {"columns": cols, "rows": [[l] + [float(rng.uniform(1, 30)) if "EPS" in l else float(rng.uniform(1, 100)) * 1e9
                                                            for _ in cols] for l in labels]}
            return out
        t = _yf().Ticker(sym)
        src = {"income": t.get_income_stmt(freq=freq), "balance": t.get_balance_sheet(freq=freq),
               "cashflow": t.get_cashflow(freq=freq)}
        for kind, df in src.items():
            if df is None or len(df) == 0:
                continue
            df = df.copy()
            df.index = [_label(i) for i in df.index]
            cols = [str(pd.Timestamp(c).date()) for c in df.columns]
            rows = []
            for l in FA_ROWS[kind]:
                if l in df.index:
                    rows.append([l] + [_f(v) for v in df.loc[l].tolist()])
            out[kind] = {"columns": cols, "rows": rows}
        return out
    return cached(f"fa:{sym}:{freq}", 6 * 3600, load)


def _label(s: str) -> str:
    # yfinance get_* returns CamelCase keys ("TotalRevenue"); normalise to spaced labels
    if " " in s:
        return s
    import re
    s = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return s.replace("E P S", "EPS").replace("E B I T D A", "EBITDA").replace("Ebitda", "EBITDA").replace("Eps", "EPS")


def fmt_big(x) -> str:
    x = _f(x)
    if x is None:
        return "—"
    a = abs(x)
    for d, s in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= d:
            return f"{x / d:.2f}{s}"
    return f"{x:.2f}"
