"""Condition + rank stock selection and a daily portfolio backtest (FinLab-style report).

Timeline for each rebalance date r:  conditions are evaluated with data available at r's close →
orders execute at the next trading day's **open** (還原開盤價) → held until the next rebalance's
execution day. Costs: 手續費 0.1425% × 折扣 each side, 證交稅 0.3% (ETF 0.1%) on sells, plus slippage.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

import numpy as np
import pandas as pd

from .. import twmarket
from . import collect, expr, fields as F

DEFAULTS = {
    "name": "未命名策略", "description": "",
    "conditions": [], "rank": "", "ascending": False, "top_n": 10,
    "rebalance": "M", "max_weight": 0.2, "stop_loss": None, "take_profit": None,
    "start": "2016-01-01", "end": None, "benchmark": "0050",
    "universe": {"market": "all", "min_value": 20.0, "exclude_finance": False, "include_etf": False, "industries": [],
                 "exclude_disposal": True},
    "fee_discount": 0.6, "slippage": 0.001,
}
REBALANCE = {"D": "每日", "W": "每週", "M": "每月", "Q": "每季", "revenue": "月營收公布後（每月 10 日後）",
             "financial": "財報公布後（5/15、8/14、11/14、3/31 後）"}
FEE, TAX, TAX_ETF = 0.001425, 0.003, 0.001


class SpecError(ValueError):
    pass


def normalize(spec: dict) -> dict:
    s = json.loads(json.dumps(DEFAULTS))
    for k, v in (spec or {}).items():
        if k == "universe" and isinstance(v, dict):
            s["universe"].update(v)
        elif k in s:
            s[k] = v
    if isinstance(s["conditions"], str):
        s["conditions"] = [c.strip() for c in s["conditions"].splitlines() if c.strip()]
    s["conditions"] = [str(c).strip() for c in s["conditions"] if str(c).strip()][:12]
    if not s["conditions"] and not s["rank"]:
        raise SpecError("至少要有一個條件或排名依據")
    for c in s["conditions"] + ([s["rank"]] if s["rank"] else []):
        try:
            expr.parse(c)
        except expr.ExprError as e:
            raise SpecError(f"「{c}」：{e}") from e
    s["top_n"] = max(1, min(int(s["top_n"] or 10), 100))
    s["max_weight"] = max(0.01, min(float(s["max_weight"] or 1), 1.0))
    if s["rebalance"] not in REBALANCE:
        raise SpecError(f"換股頻率要是 {', '.join(REBALANCE)}")
    for k in ("stop_loss", "take_profit"):
        v = s[k]
        s[k] = None if v in (None, "", 0, "0") else abs(float(v)) / (100 if abs(float(v)) > 1 else 1)
    s["benchmark"] = str(s["benchmark"] or "0050").replace(".TWO", "").replace(".TW", "")
    s["fee_discount"] = max(0.0, min(float(s["fee_discount"]), 1.0))
    s["slippage"] = max(0.0, min(float(s["slippage"]), 0.02))
    return s


def spec_hash(spec: dict) -> str:
    return hashlib.sha1(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- universe & dates
def universe_mask(s: dict, cal: pd.DatetimeIndex, codes: pd.Index) -> pd.DataFrame:
    uni = collect.universe()
    u = s["universe"]
    keep = []
    for c in codes:
        info = uni.get(c)
        if not info:
            continue
        if info.get("kind") == "ETF" and not u.get("include_etf"):
            continue
        if info.get("kind") not in ("股票", "ETF"):
            continue
        if u.get("market") in ("上市", "上櫃") and info.get("market") != u["market"]:
            continue
        if u.get("exclude_finance") and info.get("industry") == "金融保險業":
            continue
        if u.get("industries") and info.get("industry") not in u["industries"]:
            continue
        keep.append(c)
    mask = pd.DataFrame(False, index=cal, columns=codes)
    mask[keep] = True
    close = F._prices("close").reindex(index=cal, columns=codes)
    mask &= close.notna()
    mv = float(u.get("min_value") or 0)
    if mv > 0:
        val = F._prices("value").reindex(index=cal, columns=codes).rolling(20, min_periods=10).mean()
        mask &= val >= mv
    if u.get("exclude_disposal", True):
        mask &= ~disposal_mask(cal, codes)
    return mask


def disposal_periods() -> list[tuple[str, str, str]]:
    from . import store
    with store.conn() as c:
        return c.execute("SELECT code, start, end FROM disposal").fetchall()


def disposal_mask(cal: pd.DatetimeIndex, codes: pd.Index) -> pd.DataFrame:
    """True where a stock is under disposition (處置) — from the day before it starts (the announcement is out by
    then and orders go in at the next open) through its last day."""
    arr = np.zeros((len(cal), len(codes)), dtype=bool)
    if not len(cal):
        return pd.DataFrame(arr, index=cal, columns=codes)
    col = {c: i for i, c in enumerate(codes)}
    for code, a, b in disposal_periods():
        j = col.get(code)
        if j is None:
            continue
        i0 = max(int(cal.searchsorted(pd.Timestamp(a))) - 1, 0)
        i1 = int(cal.searchsorted(pd.Timestamp(b), side="right"))
        if i1 > i0:
            arr[i0:i1, j] = True
    return pd.DataFrame(arr, index=cal, columns=codes)


def rebalance_dates(cal: pd.DatetimeIndex, freq: str, start, end) -> list[pd.Timestamp]:
    c = cal[(cal >= start) & (cal <= end)]
    if not len(c):
        return []
    s = pd.Series(c, index=c)
    if freq == "D":
        return list(c)
    if freq == "W":
        return list(s.groupby(c.to_period("W")).max())
    if freq == "M":
        return list(s.groupby(c.to_period("M")).max())
    if freq == "Q":
        return list(s.groupby(c.to_period("Q")).max())
    if freq == "revenue":  # first trading day on/after the 11th (deadline 10th)
        out = []
        for _, g in s.groupby(c.to_period("M")):
            after = g[g.index.day >= 11]
            if len(after):
                out.append(after.iloc[0])
        return out
    if freq == "financial":
        out = []
        for y in range(c[0].year - 1, c[-1].year + 1):
            for d in (dt.date(y, 5, 15), dt.date(y, 8, 14), dt.date(y, 11, 14), dt.date(y, 3, 31)):
                after = c[c > pd.Timestamp(d)]
                if len(after):
                    out.append(after[0])
        return sorted(set(out))
    raise SpecError(freq)


# ---------------------------------------------------------------- selection
def select(s: dict, cal: pd.DatetimeIndex, codes: pd.Index):
    mask = universe_mask(s, cal, codes)
    cond = mask.copy()
    for c in s["conditions"]:
        cond &= expr.to_daily_bool(expr.evaluate(c, cal, codes), cal).reindex(index=cal, columns=codes, fill_value=False)
    score = None
    if s["rank"]:
        score = expr.evaluate(s["rank"], cal, codes).daily(cal).reindex(columns=codes).astype(float)
    return mask, cond, score


def picks_on(day, cond, score, s, liquidity) -> list[str]:
    i = cond.index.get_loc(day)
    return _picks_np(cond.values[i], None if score is None else score.values[i], liquidity.values[i], cond.columns, s)


def _picks_np(ok, sc, liq, cols, s) -> list[str]:
    idx = np.flatnonzero(ok)
    if not len(idx):
        return []
    if sc is not None:
        v = sc[idx].astype(float)
        keep = np.isfinite(v)
        idx, v = idx[keep], v[keep]
        order = np.argsort(v if s["ascending"] else -v, kind="stable")
    else:
        v = np.nan_to_num(liq[idx].astype(float))
        order = np.argsort(-v, kind="stable")
    return [cols[j] for j in idx[order][: s["top_n"]]]


# ---------------------------------------------------------------- simulation
def simulate(s: dict, cal, rebal, cond, score, codes, end=None, fixed: dict | None = None):
    """fixed: {signal date (Timestamp): [codes]} — use these picks instead of re-selecting (forward tracking keeps
    the list it actually issued that day, even if the data behind it is revised later)."""
    O = F._prices("adj_open").reindex(index=cal, columns=codes)
    C = F._prices("adj").reindex(index=cal, columns=codes)
    O = O.fillna(C)
    Cf = C.ffill()
    liquidity = F._prices("value").reindex(index=cal, columns=codes).rolling(20, min_periods=5).mean()
    fee = FEE * s["fee_discount"]
    slip = s["slippage"]
    pos = {c: i for i, c in enumerate(codes)}
    Ov, Cv = O.values, Cf.values
    condv = cond.reindex(index=cal, columns=codes, fill_value=False).values
    scorev = None if score is None else score.reindex(index=cal, columns=codes).values
    liqv = liquidity.values
    rebal_set = {d: True for d in rebal}
    first = cal.get_loc(rebal[0])
    last_i = len(cal) - 1
    end_i = int(cal.searchsorted(end, side="right")) - 1 if end is not None else last_i
    end_i = max(min(end_i, last_i), first)
    cash, shares = 1.0, {}
    entry: dict[str, tuple] = {}
    pending: dict[str, float] | None = None
    blocked: set[str] = set()
    to_exit: set[str] = set()
    equity, dates, trades, n_hold, buys_total = [], [], [], [], 0.0
    picks_hist = []
    for i in range(first, end_i + 1):
        d = cal[i]
        # 1) execute at today's open
        if pending is not None or to_exit:
            px = {c: Ov[i, pos[c]] if np.isfinite(Ov[i, pos[c]]) else Cv[i - 1, pos[c]] for c in set(shares) | set(pending or {})}
            V = cash + sum(sh * px[c] for c, sh in shares.items() if np.isfinite(px[c]))
            target = dict(pending) if pending is not None else {c: shares[c] * px[c] / V for c in shares}
            for c in to_exit:
                target.pop(c, None)
            # sells first
            for c in list(shares):
                p = px[c]
                if not np.isfinite(p):
                    continue
                cur = shares[c] * p
                tgt = target.get(c, 0.0) * V
                if tgt < cur - 1e-9:
                    sell = cur - tgt
                    tax = TAX_ETF if c.startswith("00") else TAX
                    cash += sell * (1 - fee - tax - slip)
                    shares[c] -= sell / p
                    if tgt <= 1e-9:
                        e_d, e_p = entry.pop(c, (d, p))
                        trades.append({"code": c, "entry": e_d.date().isoformat(), "exit": d.date().isoformat(),
                                       "ret": round((p / e_p - 1) * 100, 2), "days": (d - e_d).days,
                                       "reason": "停損" if c in to_exit and c in blocked else "換股"})
                        del shares[c]
            for c, w in target.items():
                p = px.get(c, np.nan)
                if not np.isfinite(p) or p <= 0:
                    continue
                cur = shares.get(c, 0.0) * p
                tgt = w * V
                if tgt > cur + 1e-9:
                    buy = min(tgt - cur, cash / (1 + fee + slip))
                    if buy <= 0:
                        continue
                    cash -= buy * (1 + fee + slip)
                    buys_total += buy
                    shares[c] = shares.get(c, 0.0) + buy / p
                    entry.setdefault(c, (d, p))
            pending, to_exit = None, set()
        # 2) mark to market at close
        val = cash
        for c, sh in shares.items():
            p = Cv[i, pos[c]]
            if np.isfinite(p):
                val += sh * p
        equity.append(val)
        dates.append(d)
        n_hold.append(len(shares))
        # 3) stops (sell next open)
        if s["stop_loss"] or s["take_profit"]:
            for c in shares:
                p = Cv[i, pos[c]]
                e_p = entry.get(c, (d, p))[1]
                if not (np.isfinite(p) and e_p):
                    continue
                r = p / e_p - 1
                if (s["stop_loss"] and r <= -s["stop_loss"]) or (s["take_profit"] and r >= s["take_profit"]):
                    to_exit.add(c)
                    blocked.add(c)
        # 4) rebalance signal at today's close → trade tomorrow
        if d in rebal_set and i < last_i:
            blocked = set()
            sel = fixed[d] if fixed is not None and d in fixed else \
                _picks_np(condv[i], None if scorev is None else scorev[i], liqv[i], codes, s)
            sel = [c for c in sel if c in pos]
            picks_hist.append((d, len(sel)))
            w = min(1.0 / len(sel), s["max_weight"]) if sel else 0
            pending = {c: w for c in sel}
    eq = pd.Series(equity, index=pd.DatetimeIndex(dates))
    return eq, trades, pd.Series(n_hold, index=eq.index), buys_total, picks_hist, shares


# ---------------------------------------------------------------- metrics
def metrics(eq: pd.Series, bench: pd.Series | None, trades: list[dict], buys_total: float) -> dict:
    r = eq.pct_change().dropna()
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    years = days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1
    vol = r.std() * math.sqrt(252)
    sharpe = r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else 0
    down = r[r < 0].std()
    sortino = r.mean() / down * math.sqrt(252) if down and down > 0 else 0
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    trough = dd.idxmin()
    peak = eq.loc[:trough].idxmax()
    rec = eq.loc[trough:]
    rec_d = rec[rec >= eq.loc[peak]].index
    wins = [t for t in trades if t["ret"] > 0]
    m = {"total_return": (eq.iloc[-1] - 1) * 100, "cagr": cagr * 100, "vol": vol * 100, "sharpe": sharpe,
         "sortino": sortino, "mdd": mdd * 100, "mdd_peak": peak.date().isoformat(), "mdd_trough": trough.date().isoformat(),
         "mdd_recover": rec_d[0].date().isoformat() if len(rec_d) else None,
         "calmar": (cagr / abs(mdd)) if mdd < 0 else None, "trades": len(trades),
         "win_rate": len(wins) / len(trades) * 100 if trades else None,
         "avg_trade": float(np.mean([t["ret"] for t in trades])) if trades else None,
         "avg_days": float(np.mean([t["days"] for t in trades])) if trades else None,
         "turnover": buys_total / years if years else None, "years": years,
         "start": eq.index[0].date().isoformat(), "end": eq.index[-1].date().isoformat()}
    if bench is not None and len(bench) > 20:
        b = bench.reindex(eq.index).ffill()
        br = b.pct_change().dropna()
        j = pd.concat([r, br], axis=1, join="inner").dropna()
        if len(j) > 20 and j.iloc[:, 1].var() > 0:
            beta = j.cov().iloc[0, 1] / j.iloc[:, 1].var()
            alpha = (j.iloc[:, 0].mean() - beta * j.iloc[:, 1].mean()) * 252
            m.update(beta=beta, alpha=alpha * 100)
        bcagr = (b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1
        bdd = (b / b.cummax() - 1).min()
        m.update(bench_cagr=bcagr * 100, bench_mdd=bdd * 100, bench_total=(b.iloc[-1] / b.iloc[0] - 1) * 100,
                 bench_sharpe=br.mean() / br.std() * math.sqrt(252) if br.std() > 0 else 0)
    def clean(v):
        if isinstance(v, (float, np.floating)):
            v = float(v)
            return round(v, 4) if math.isfinite(v) else None
        return v
    return {k: clean(v) for k, v in m.items()}


def benchmark(code: str, cal) -> pd.Series | None:
    adj = F._prices("adj")
    if code in adj.columns:
        b = adj[code].reindex(cal).ffill().dropna()
        if len(b) > 20:
            return b
    try:
        from .. import market
        sym = code if "." in code or code.startswith("^") else twmarket.tw_symbol(code)
        h = market.history(sym, start=cal[0].date().isoformat())
        s = h["Close"]
        s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
        return s.reindex(cal).ffill().dropna()
    except Exception:  # noqa: BLE001
        return None


def py(o):
    """numpy → plain Python, NaN/inf → None (JSON-safe)."""
    if isinstance(o, dict):
        return {str(k): py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [py(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def _name(code: str) -> str:
    info = collect.universe().get(code) or {}
    return info.get("name") or code


def _no_data_msg(name: str) -> str:
    f = F.BY_NAME[name]
    table = {"financials": "income_ytd"}.get(f.dataset, f.dataset)
    p = collect.progress().get(table, {})
    st = collect.state
    msg = f"「{name}」還沒有資料。{F.DATASETS.get(f.dataset, {}).get('name', f.dataset)}：已成功 {p.get('ok', 0)} 批"
    if p.get("error"):
        msg += f"、失敗 {p['error']} 批（最近錯誤：{str(p.get('last_error', ''))[:160]}）"
    if p.get("nodata"):
        msg += f"、無資料 {p['nodata']} 批"
    msg += f"。收集器目前：{st.get('task') or '—'}"
    if st.get("prices_task"):
        msg += f"；{st['prices_task']}"
    msg += "。第一次安裝要等背景下載（月營收約 1 小時），進度在「資料庫」頁。"
    return msg


def prepare(spec: dict) -> dict:
    """Everything a run needs before simulating: normalized spec, calendar, effective start/end, selection panels."""
    s = normalize(spec)
    cal = F.calendar()
    if len(cal) < 60:
        raise SpecError("股價資料還沒準備好（資料庫在背景下載中，請看「資料庫」頁的進度）")
    used = sorted({n for c in s["conditions"] + ([s["rank"]] if s["rank"] else []) for n in expr.names(c)})
    # effective start: every dataset the strategy uses must already have coverage
    warns = []
    start = pd.Timestamp(s["start"])
    for n in used:
        fv = F.first_valid(F.BY_NAME[n])
        if fv is None:
            raise SpecError(_no_data_msg(n))
        lag = {"D": 0, "M": 70, "Q": 140}[F.BY_NAME[n].freq]
        fvd = pd.Timestamp(fv) + pd.Timedelta(days=lag + 30)
        if fvd > start:
            warns.append(f"「{n}」資料從 {fv} 開始，回測起點延到 {fvd.date()}")
            start = fvd
    end = pd.Timestamp(s["end"]) if s["end"] else cal[-1]
    codes = F._prices("close").columns
    mask, cond, score = select(s, cal, codes)
    rebal = rebalance_dates(cal, s["rebalance"], start, end)
    if len(rebal) < 2:
        raise SpecError("回測期間太短（換股日少於 2 次）")
    return {"s": s, "cal": cal, "codes": codes, "start": start, "end": end, "warns": warns, "used": used,
            "mask": mask, "cond": cond, "score": score, "rebal": rebal}


def run(spec: dict, today_picks: bool = True) -> dict:
    P = prepare(spec)
    s, cal, codes, end, warns, used = P["s"], P["cal"], P["codes"], P["end"], P["warns"], P["used"]
    cond, score, rebal = P["cond"], P["score"], P["rebal"]
    eq, trades, n_hold, buys_total, picks_hist, final = simulate(s, cal, rebal, cond, score, codes, end)
    bench = benchmark(s["benchmark"], eq.index)
    m = metrics(eq, bench, trades, buys_total)
    yearly = []
    for y, g in eq.groupby(eq.index.year):
        prev = eq[eq.index.year < y]
        base = prev.iloc[-1] if len(prev) else 1.0
        row = {"year": int(y), "ret": round((g.iloc[-1] / base - 1) * 100, 2)}
        if bench is not None:
            bg = bench.reindex(g.index).ffill()
            bp = bench[bench.index.year < y]
            bb = bp.iloc[-1] if len(bp) else bg.iloc[0]
            row["bench"] = round((bg.iloc[-1] / bb - 1) * 100, 2)
        yearly.append(row)
    me = eq.resample("ME").last()
    monthly = (me / me.shift(1).fillna(1.0) - 1) * 100
    dd = eq / eq.cummax() - 1
    step = max(1, len(eq) // 1500)
    curve = [{"t": d.date().isoformat(), "v": round(float(v), 5), "dd": round(float(x) * 100, 3),
              "b": round(float(bench.get(d, np.nan) / bench.iloc[0]), 5) if bench is not None and d in bench.index else None}
             for d, v, x in list(zip(eq.index, eq.values, dd.values))[::step]]
    names = {}
    for t in trades:
        t["name"] = names.setdefault(t["code"], _name(t["code"]))
    out = {"spec": s, "hash": spec_hash(s), "metrics": m, "yearly": yearly,
           "monthly": [{"m": d.strftime("%Y-%m"), "r": round(float(v), 2)} for d, v in monthly.items()],
           "curve": curve, "trades": trades[-400:], "n_trades_total": len(trades), "warnings": warns,
           "holdings": [{"code": c, "name": _name(c)} for c in final],
           "picks_count": [{"t": d.date().isoformat(), "n": n} for d, n in picks_hist],
           "used_fields": used, "data_end": cal[-1].date().isoformat(),
           "disposal": disposal_note(s),
           "notes": ["股票池只含目前仍上市櫃的公司（已下市股票不在資料內）→ 回測報酬會有存活者偏差、偏樂觀。",
                     "月營收、財報以法定公布期限對齊；日資料在當天收盤後才使用，隔日開盤成交。",
                     "報酬用還原股價計算（含股利再投入）；成本含手續費、證交稅與滑價。"]}
    if today_picks:
        out["picks"] = current_picks(s, cal, codes, cond, score, used)
    return py(out)


def disposal_note(s: dict) -> str:
    if not s["universe"].get("exclude_disposal", True):
        return "沒有排除處置股（處置期間的股票實際很難照回測價格買到）"
    try:
        p = disposal_periods()
    except Exception:  # noqa: BLE001
        p = []
    if not p:
        return "處置股資料還沒有（背景收集中），這次回測沒有排除處置股"
    return f"已排除處置期間的股票（處置紀錄 {len(p)} 筆，最早 {min(x[1] for x in p)}）"


def current_picks(s, cal, codes, cond, score, used) -> dict:
    day = cal[-1]
    liquidity = F._prices("value").reindex(index=cal, columns=codes).rolling(20, min_periods=5).mean()
    sel = picks_on(day, cond, score, s, liquidity)
    close = F._prices("close")
    rows = []
    vals = {}
    for n in used[:6]:
        f = F.BY_NAME[n]
        try:
            vals[n] = F.to_daily(F.raw(f), f.freq, cal).loc[day]
        except Exception:  # noqa: BLE001
            continue
    for c in sel:
        r = {"code": c, "name": _name(c), "close": float(close.at[day, c]) if c in close.columns and pd.notna(close.at[day, c]) else None}
        if score is not None:
            v = score.at[day, c]
            r["score"] = round(float(v), 4) if pd.notna(v) else None
        r["values"] = {n: (round(float(v[c]), 3) if c in v.index and pd.notna(v[c]) else None) for n, v in vals.items()}
        info = collect.universe().get(c) or {}
        r["industry"] = info.get("industry")
        r["market"] = info.get("market")
        rows.append(r)
    return {"date": day.date().isoformat(), "n_match": int(cond.loc[day].sum()), "rows": rows}


def screen(spec: dict) -> dict:
    s = normalize(spec)
    cal = F.calendar()
    codes = F._prices("close").columns
    _, cond, score = select(s, cal, codes)
    used = sorted({n for c in s["conditions"] + ([s["rank"]] if s["rank"] else []) for n in expr.names(c)})
    return py({"spec": s, "picks": current_picks(s, cal, codes, cond, score, used)})
