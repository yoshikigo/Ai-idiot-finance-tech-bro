"""定期定額（dollar-cost averaging）：排程、用歷史價格模擬每一期買進、XIRR、和單筆投入比較。

Used by 我的持股（the user's own plans, computed on the fly from their settings — nothing is stored but the plan)
and by the 定期定額回測 side tab / agent API (hypothetical plans over past prices).

Prices are Yahoo's dividend/split-adjusted closes (≈ total return with dividends reinvested); funds use the
official NAV (distributing share classes therefore understate total return). A purchase executes at the close of
the first trading day on or after the scheduled date, like Taiwanese brokers' 定期定額 (扣款日遇假日順延).
"""
from __future__ import annotations

import calendar
import datetime as dt
import math

import pandas as pd

from . import market

FREQS = {"monthly": "每月", "weekly": "每週", "daily": "每個交易日"}
WEEKDAYS = "一二三四五六日"
MAX_YEARS = 30


# ------------------------------------------------------------------ schedule
def parse_days(freq: str, days) -> list[int]:
    if isinstance(days, str):
        days = [x for x in days.replace("，", ",").replace("、", ",").replace(" ", ",").split(",") if x.strip()]
    try:
        out = sorted({int(x) for x in (days or [])})
    except (TypeError, ValueError) as e:
        raise ValueError("扣款日要是數字，例如 6,16,26") from e
    if freq == "monthly":
        out = [d for d in out if 1 <= d <= 31] or [6]
        if len(out) > 6:
            raise ValueError("每月最多 6 個扣款日")
    elif freq == "weekly":
        out = [d for d in out if 1 <= d <= 5] or [1]
    else:
        out = []
    return out


def schedule(start: dt.date, end: dt.date, freq: str, days: list[int]) -> list[dt.date]:
    """Scheduled debit dates (before holiday roll-forward)."""
    out: list[dt.date] = []
    if freq == "monthly":
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            last = calendar.monthrange(y, m)[1]
            for d in days:
                x = dt.date(y, m, min(d, last))
                if start <= x <= end:
                    out.append(x)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif freq == "weekly":
        d = start
        while d <= end:
            if d.isoweekday() in days:
                out.append(d)
            d += dt.timedelta(days=1)
    elif freq == "daily":
        out = [x.date() for x in pd.bdate_range(start, end)]
    else:
        raise ValueError("頻率只能是 monthly / weekly / daily")
    return sorted(set(out))


def describe(freq: str, days: list[int], amount: float) -> str:
    if freq == "monthly":
        return f"每月 {'、'.join(str(d) for d in days)} 日 {amount:,.0f} 元"
    if freq == "weekly":
        return f"每週{'、'.join(WEEKDAYS[d - 1] for d in days)} {amount:,.0f} 元"
    return f"每個交易日 {amount:,.0f} 元"


# ------------------------------------------------------------------ prices
def currency_of(sym: str) -> str:
    if sym.startswith("FUND:"):
        try:
            from . import funds
            return (funds.get(sym[5:]) or {}).get("currency") or "TWD"
        except Exception:  # noqa: BLE001
            return "TWD"
    if sym.endswith((".TW", ".TWO")) or sym in ("^TWII", "^TWOII"):
        return "TWD"
    if sym.endswith(".T"):
        return "JPY"
    if sym.endswith((".KS", ".KQ")):
        return "KRW"
    if sym.endswith(".HK"):
        return "HKD"
    return "USD"


def prices(sym: str, start: dt.date) -> pd.Series:
    """Daily close (adjusted) or fund NAV from `start` (a little earlier, so the first debit has a price)."""
    if sym.startswith("FUND:"):
        from . import funds
        pts = funds.history(sym[5:])["points"]
        s = pd.Series([float(p[1]) for p in pts], index=pd.to_datetime([p[0] for p in pts]))
    else:
        df = market.history(sym, start=(start - dt.timedelta(days=10)).isoformat())
        if df is None or not len(df):
            return pd.Series(dtype=float)
        s = df["Close"].astype(float)
        s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    s = s.dropna()
    s = s[s > 0]
    return s[~s.index.duplicated(keep="last")].sort_index()


def twd_rate(cur: str, start: dt.date) -> pd.Series | None:
    """TWD per 1 unit of `cur`, daily; None for TWD."""
    if cur == "TWD":
        return None
    from . import fx
    years = (dt.date.today() - start).days / 365.25
    period = "2y" if years < 1.9 else "5y" if years < 4.9 else "10y" if years < 9.9 else "max"
    if cur not in fx.CCY:
        raise ValueError(f"不支援 {cur} 換算台幣")
    return fx.series(cur, "10y" if period == "max" else period)


# ------------------------------------------------------------------ maths
def xirr(flows: list[tuple[dt.date, float]]) -> float | None:
    """Annualised internal rate of return (%), flows: negative = money in, positive = value out."""
    if len(flows) < 2 or not any(v > 0 for _, v in flows) or not any(v < 0 for _, v in flows):
        return None
    t0 = min(d for d, _ in flows)
    ts = [((d - t0).days / 365.0, v) for d, v in flows]
    if max(t for t, _ in ts) < 1 / 365:
        return None

    def npv(r):
        return sum(v / (1 + r) ** t for t, v in ts)
    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f = npv(mid)
        if abs(f) < 1e-7:
            break
        if f_lo * f < 0:
            hi, f_hi = mid, f
        else:
            lo, f_lo = mid, f
    return (lo + hi) / 2 * 100


def _asof(s: pd.Series | None, t: pd.Timestamp) -> float:
    if s is None:
        return 1.0
    prior = s[s.index <= t]
    return float(prior.iloc[-1]) if len(prior) else float(s.iloc[0])


def simulate(sym: str, amount: float, freq: str = "monthly", days=None, start: str | dt.date | None = None,
             end: str | dt.date | None = None, fee_pct: float = 0.1425, min_fee: float = 1.0, lots_out: int = 12) -> dict:
    """Run a 定期定額 plan over historical prices. Amounts, fees and values are in TWD."""
    sym = sym.strip()
    if not sym:
        raise ValueError("請輸入標的")
    amount = float(amount)
    if not 1 <= amount <= 1e8:
        raise ValueError("每期金額要介於 1 到 1 億元")
    fee_pct, min_fee = max(0.0, float(fee_pct or 0)), max(0.0, float(min_fee or 0))
    today = dt.date.today()
    start = dt.date.fromisoformat(str(start)[:10]) if start else today.replace(year=today.year - 5)
    end = min(dt.date.fromisoformat(str(end)[:10]), today) if end else today
    if start > end:
        raise ValueError("開始日期晚於結束日期")
    if (end - start).days > 365.25 * MAX_YEARS:
        raise ValueError(f"最長 {MAX_YEARS} 年")
    dl = parse_days(freq, days)
    px = prices(sym, start)
    if len(px) < 2:
        raise ValueError(f"{sym} 沒有足夠的歷史價格")
    cur = currency_of(sym)
    rate = twd_rate(cur, start)
    first_px = px.index[0].date()
    sched = [d for d in schedule(max(start, first_px), end, freq, dl)]
    idx = px.index
    lots = []
    for d in sched:
        k = idx.searchsorted(pd.Timestamp(d))
        if k >= len(idx):
            break  # not traded yet (future / today before close)
        t = idx[k]
        if t.date() > end and end < today:
            break
        p = float(px.iloc[k])
        r = _asof(rate, t)
        fee = max(amount * fee_pct / 100, min_fee) if fee_pct or min_fee else 0.0
        fee = min(fee, amount)
        units = (amount - fee) / r / p
        lots.append({"scheduled": d.isoformat(), "date": t.strftime("%Y-%m-%d"), "price": p, "rate": r,
                     "amount": amount, "fee": fee, "units": units})
    if not lots:
        raise ValueError("這段期間還沒有任何一期扣款")
    last_t = idx[-1] if end >= today else idx[idx.searchsorted(pd.Timestamp(end), side="right") - 1]
    last_p = float(px[px.index <= last_t].iloc[-1])
    last_r = _asof(rate, last_t)
    units = sum(x["units"] for x in lots)
    invested = sum(x["amount"] for x in lots)
    fees = sum(x["fee"] for x in lots)
    value = units * last_p * last_r
    # daily curve from the first purchase
    lot_s = pd.Series({pd.Timestamp(x["date"]): 0.0 for x in lots})
    cu, ci = pd.Series(0.0, index=px.index), pd.Series(0.0, index=px.index)
    for x in lots:
        cu[pd.Timestamp(x["date"])] += x["units"]
        ci[pd.Timestamp(x["date"])] += x["amount"]
    window = px.index[(px.index >= lot_s.index.min()) & (px.index <= last_t)]
    cu, ci = cu.cumsum().reindex(window), ci.cumsum().reindex(window)
    rates = pd.Series([_asof(rate, t) for t in window], index=window) if rate is not None else 1.0
    val = cu * px.reindex(window) * rates
    ratio = (val / ci).replace([math.inf, -math.inf], float("nan")).dropna()
    mdd = float(((ratio / ratio.cummax()) - 1).min() * 100) if len(ratio) else None
    step = max(1, len(window) // 400)
    curve = [{"time": t.strftime("%Y-%m-%d"), "value": round(float(v), 2), "invested": round(float(c), 2)}
             for t, v, c in zip(window[::step], val.iloc[::step], ci.iloc[::step])]
    if curve and curve[-1]["time"] != window[-1].strftime("%Y-%m-%d"):
        curve.append({"time": window[-1].strftime("%Y-%m-%d"), "value": round(value, 2), "invested": round(invested, 2)})
    flows = [(dt.date.fromisoformat(x["date"]), -x["amount"]) for x in lots] + [(last_t.date(), value)]
    # lump sum: everything on the first debit day
    l0 = lots[0]
    lump_units = (invested - (max(invested * fee_pct / 100, min_fee) if fee_pct or min_fee else 0)) / l0["rate"] / l0["price"]
    lump_value = lump_units * last_p * last_r
    lump_x = xirr([(dt.date.fromisoformat(l0["date"]), -invested), (last_t.date(), lump_value)])
    nxt = next((d for d in schedule(today, today + dt.timedelta(days=40), freq, dl) if d > today), None) if end >= today else None
    return {
        "symbol": sym, "currency": cur, "plan": describe(freq, dl, amount), "freq": freq, "days": dl,
        "start": lots[0]["date"], "end": last_t.strftime("%Y-%m-%d"), "n": len(lots), "invested": invested, "fees": fees,
        "units": units, "avg_cost": sum((x["amount"] - x["fee"]) / x["rate"] for x in lots) / units if units else None,
        "last_price": last_p, "last_rate": last_r, "value": value, "pnl": value - invested,
        "ret": (value / invested - 1) * 100 if invested else None, "xirr": xirr(flows), "mdd": mdd,
        "lump": {"value": lump_value, "ret": (lump_value / invested - 1) * 100 if invested else None, "xirr": lump_x},
        "next": nxt.isoformat() if nxt else None, "lots": lots[-lots_out:] if lots_out else lots, "curve": curve,
        "price_note": "基金淨值（配息型不含配息）" if sym.startswith("FUND:") else "還原權息收盤價（股利再投入）",
    }


def text(results: list[dict]) -> str:
    lines = ["【定期定額回測】（金額為台幣；價格為還原權息，含手續費；不含稅）"]
    for r in results:
        if "error" in r:
            lines.append(f"- {r['symbol']}：{r['error']}")
            continue
        f = lambda x: "—" if x is None else f"{x:+.2f}%"  # noqa: E731
        lines.append(f"- {r['symbol']}（{r['plan']}，{r['start']}～{r['end']}，{r['n']} 期）：投入 {r['invested']:,.0f}、"
                     f"市值 {r['value']:,.0f}、報酬 {f(r['ret'])}、年化 {f(r['xirr'])}、最大回撤 {f(r['mdd'])}；"
                     f"同金額第一期單筆投入：報酬 {f(r['lump']['ret'])}、年化 {f(r['lump']['xirr'])}")
    return "\n".join(lines)
