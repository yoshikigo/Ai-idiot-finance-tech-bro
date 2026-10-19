"""A yfinance-backed DataClient for ai-hedge-fund v2 (no Financial Datasets key needed).

Implements hedge_fund.data.protocol.DataClient structurally. Financial metrics
are built from yfinance annual statements (+ a TTM row from the last 4
quarters). Point-in-time is approximated: a period is treated as filed
FILING_LAG days after its end. Works for US and Taiwan (.TW/.TWO) tickers.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from ft_common import market
from hedge_fund.data.models import CompanyFacts, CompanyNews, FinancialMetrics, Price

FILING_LAG = {"annual": 75, "ttm": 45}


def _sec_filed(ticker: str, end: pd.Timestamp) -> dt.date | None:
    """Actual SEC filing date for US issuers (point-in-time); None → caller keeps the +75/45-day estimate."""
    if ticker.endswith((".TW", ".TWO")) or ticker.startswith("^"):
        return None
    try:
        from ft_common import edgar
        if not edgar.enabled():
            return None
        d = edgar.filed_date(ticker, end.strftime("%Y-%m-%d"))
        return dt.date.fromisoformat(d) if d else None
    except Exception:  # noqa: BLE001
        return None


def _g(df: pd.DataFrame | None, col, *names):
    if df is None or len(df) == 0 or col not in df.columns:
        return None
    for n in names:
        if n in df.index:
            v = df.at[n, col]
            try:
                v = float(v)
                if not math.isnan(v):
                    return v
            except (TypeError, ValueError):
                pass
    return None


def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _growth(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


class YFinanceDataClient:
    def __init__(self):
        self._stm: dict[str, dict] = {}

    # ------------------------------------------------------------------ prices
    def get_prices(self, ticker, start_date, end_date, **kwargs) -> list[Price]:
        df = market.history(ticker, start=start_date, end=(dt.date.fromisoformat(end_date) + dt.timedelta(days=1)).isoformat())
        return [Price(open=r.Open, close=r.Close, high=r.High, low=r.Low, volume=int(r.Volume or 0),
                      time=pd.Timestamp(i).strftime("%Y-%m-%d")) for i, r in df.iterrows()]

    # ------------------------------------------------------------------ statements
    def _statements(self, ticker):
        if ticker in self._stm:
            return self._stm[ticker]
        if market.FAKE:
            s = _fake_statements(ticker)
        else:
            import yfinance as yf
            t = yf.Ticker(ticker)
            s = {"inc": t.income_stmt, "bal": t.balance_sheet, "cf": t.cashflow,
                 "qinc": t.quarterly_income_stmt, "qbal": t.quarterly_balance_sheet, "qcf": t.quarterly_cashflow}
        self._stm[ticker] = s
        return s

    def _price_on(self, ticker, day: dt.date):
        df = market.history(ticker, start=(day - dt.timedelta(days=10)).isoformat(),
                            end=(day + dt.timedelta(days=1)).isoformat())
        return float(df["Close"].iloc[-1]) if len(df) else None

    def _row(self, ticker, period, end: pd.Timestamp, inc, bal, cf, icol, bcol, ccol, prev: dict | None):
        rev = _g(inc, icol, "Total Revenue", "Operating Revenue")
        gp = _g(inc, icol, "Gross Profit")
        op = _g(inc, icol, "Operating Income")
        ni = _g(inc, icol, "Net Income", "Net Income Common Stockholders")
        ebitda = _g(inc, icol, "EBITDA", "Normalized EBITDA")
        eps = _g(inc, icol, "Diluted EPS", "Basic EPS")
        interest = _g(inc, icol, "Interest Expense")
        eq = _g(bal, bcol, "Stockholders Equity", "Common Stock Equity")
        ta = _g(bal, bcol, "Total Assets")
        debt = _g(bal, bcol, "Total Debt")
        ca = _g(bal, bcol, "Current Assets")
        cl = _g(bal, bcol, "Current Liabilities")
        cash = _g(bal, bcol, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
        inv = _g(bal, bcol, "Inventory")
        shares = _g(bal, bcol, "Ordinary Shares Number", "Share Issued")
        ocf = _g(cf, ccol, "Operating Cash Flow")
        fcf = _g(cf, ccol, "Free Cash Flow")
        div = _g(cf, ccol, "Cash Dividends Paid", "Common Stock Dividend Paid")

        filed = (end + pd.Timedelta(days=FILING_LAG[period])).date()
        sec = _sec_filed(ticker, end)
        if sec:
            filed = sec
        px = self._price_on(ticker, min(filed, dt.date.today()))
        mcap = px * shares if px and shares else None
        ev = mcap + (debt or 0) - (cash or 0) if mcap else None
        if eps is None and ni is not None and shares:
            eps = ni / shares
        m = FinancialMetrics(
            ticker=ticker, report_period=end.strftime("%Y-%m-%d"), period=period, currency=None,
            filing_date=filed.isoformat(), market_cap=mcap, enterprise_value=ev,
            price_to_earnings_ratio=_div(mcap, ni) if ni and ni > 0 else None,
            price_to_book_ratio=_div(mcap, eq), price_to_sales_ratio=_div(mcap, rev),
            enterprise_value_to_ebitda_ratio=_div(ev, ebitda), enterprise_value_to_revenue_ratio=_div(ev, rev),
            free_cash_flow_yield=_div(fcf, mcap),
            gross_margin=_div(gp, rev), operating_margin=_div(op, rev), net_margin=_div(ni, rev),
            return_on_equity=_div(ni, eq), return_on_assets=_div(ni, ta),
            return_on_invested_capital=_div(op * 0.79 if op else None, (eq or 0) + (debt or 0) - (cash or 0) or None),
            asset_turnover=_div(rev, ta), inventory_turnover=_div(rev, inv),
            current_ratio=_div(ca, cl), quick_ratio=_div((ca or 0) - (inv or 0) if ca else None, cl),
            cash_ratio=_div(cash, cl), operating_cash_flow_ratio=_div(ocf, cl),
            debt_to_equity=_div(debt, eq), debt_to_assets=_div(debt, ta),
            interest_coverage=_div(op, abs(interest)) if interest else None,
            payout_ratio=_div(abs(div), ni) if div and ni else None,
            earnings_per_share=eps, book_value_per_share=_div(eq, shares),
            free_cash_flow_per_share=_div(fcf, shares),
        )
        cur = {"rev": rev, "ni": ni, "eq": eq, "eps": eps, "fcf": fcf, "op": op, "ebitda": ebitda}
        if prev:
            m.revenue_growth = _growth(rev, prev.get("rev"))
            m.earnings_growth = _growth(ni, prev.get("ni"))
            m.book_value_growth = _growth(eq, prev.get("eq"))
            m.earnings_per_share_growth = _growth(eps, prev.get("eps"))
            m.free_cash_flow_growth = _growth(fcf, prev.get("fcf"))
            m.operating_income_growth = _growth(op, prev.get("op"))
            m.ebitda_growth = _growth(ebitda, prev.get("ebitda"))
        return m, cur

    def get_financial_metrics(self, ticker, end_date, period="ttm", limit=10) -> list[FinancialMetrics]:
        s = self._statements(ticker)
        inc, bal, cf = s["inc"], s["bal"], s["cf"]
        if inc is None or len(inc) == 0:
            return []
        cols = sorted(inc.columns)  # oldest → newest
        rows: list[FinancialMetrics] = []
        prev = None
        for c in cols:
            bc = c if bal is not None and c in bal.columns else (bal.columns[0] if bal is not None and len(bal.columns) else c)
            cc = c if cf is not None and c in cf.columns else (cf.columns[0] if cf is not None and len(cf.columns) else c)
            m, cur = self._row(ticker, "annual", pd.Timestamp(c), inc, bal, cf, c, bc, cc, prev)
            prev = cur
            if m.net_margin is not None or m.gross_margin is not None:
                rows.append(m)
        # TTM row from the last four quarters
        qi, qb, qc = s.get("qinc"), s.get("qbal"), s.get("qcf")
        if qi is not None and len(qi.columns) >= 4:
            qcols = sorted(qi.columns)[-4:]
            ttm_inc = qi[qcols].sum(axis=1, min_count=4).to_frame("ttm")
            eps_cols = [r for r in ("Diluted EPS", "Basic EPS") if r in qi.index]
            for r in eps_cols:
                ttm_inc.at[r, "ttm"] = qi.loc[r, qcols].sum(min_count=4)
            ttm_cf = qc[[c for c in qcols if c in qc.columns]].sum(axis=1, min_count=4).to_frame("ttm") if qc is not None and len(qc.columns) else None
            last = qcols[-1]
            b = qb[[last]].rename(columns={last: "ttm"}) if qb is not None and last in qb.columns else None
            if pd.Timestamp(last) > pd.Timestamp(cols[-1]):
                m, _ = self._row(ticker, "ttm", pd.Timestamp(last), ttm_inc, b, ttm_cf, "ttm", "ttm", "ttm", prev)
                rows.append(m)
        cutoff = dt.date.fromisoformat(end_date)
        rows = [r for r in rows if r.filing_date and dt.date.fromisoformat(r.filing_date) <= cutoff]
        rows.sort(key=lambda r: r.report_period, reverse=True)  # newest first
        return rows[:limit]

    # ------------------------------------------------------------------ the rest
    def get_company_facts(self, ticker) -> CompanyFacts | None:
        i = market.info(ticker)
        return CompanyFacts(ticker=ticker, name=i.get("longName") or i.get("shortName"), sector=i.get("sector"),
                            industry=i.get("industry"), exchange=i.get("exchange"),
                            location=", ".join(x for x in (i.get("city"), i.get("country")) if x) or None,
                            market_cap=i.get("marketCap"))

    def get_news(self, ticker, end_date, start_date=None, limit=1000) -> list[CompanyNews]:
        out = []
        for n in market.news(ticker, min(limit, 50)):
            out.append(CompanyNews(ticker=ticker, title=n["title"], source=n.get("publisher") or "",
                                   date=(n.get("time") or "")[:10] or None, url=n.get("link")))
        return out

    def get_insider_trades(self, ticker, end_date, start_date=None, limit=1000):
        return []

    def get_earnings(self, ticker):
        return None

    def get_earnings_history(self, ticker, limit=12):
        return []

    def get_market_cap(self, ticker, end_date):
        return market.info(ticker).get("marketCap")


def _fake_statements(ticker):
    rng = np.random.default_rng(market._seed(ticker))
    years = [pd.Timestamp(f"{dt.date.today().year - i - 1}-12-31") for i in range(5)]
    quarters = [pd.Timestamp(dt.date.today().replace(day=1)) - pd.offsets.QuarterEnd(i + 1) for i in range(5)]

    def mk(cols, scale):
        base = rng.uniform(5, 60) * 1e9 * scale
        rev = np.array([base * (1.08 ** -i) for i in range(len(cols))])
        d = {"Total Revenue": rev, "Gross Profit": rev * 0.55, "Operating Income": rev * 0.3,
             "Net Income": rev * 0.22, "EBITDA": rev * 0.4, "Diluted EPS": rev * 0.22 / 5e9,
             "Interest Expense": rev * 0.01}
        return pd.DataFrame(d, index=cols).T

    def bal(cols):
        eq = rng.uniform(20, 200) * 1e9
        d = {"Stockholders Equity": [eq] * len(cols), "Total Assets": [eq * 1.8] * len(cols),
             "Total Debt": [eq * 0.3] * len(cols), "Current Assets": [eq * 0.6] * len(cols),
             "Current Liabilities": [eq * 0.3] * len(cols), "Cash And Cash Equivalents": [eq * 0.2] * len(cols),
             "Ordinary Shares Number": [5e9] * len(cols)}
        return pd.DataFrame(d, index=cols).T

    def cfl(cols, scale):
        v = rng.uniform(5, 30) * 1e9 * scale
        return pd.DataFrame({"Operating Cash Flow": [v] * len(cols), "Free Cash Flow": [v * 0.7] * len(cols)}, index=cols).T

    return {"inc": mk(years, 1), "bal": bal(years), "cf": cfl(years, 1),
            "qinc": mk(quarters, 0.25), "qbal": bal(quarters), "qcf": cfl(quarters, 0.25)}
