"""組合配置（skfolio）— 持股體檢、定期定額金額分配。

Everything runs locally on daily prices converted to TWD; holdings never leave this module (no LLM, no notes,
no agent API). Methods are the ones that do *not* need a forecast of expected returns, which is what makes
mean-variance weights jump around on small estimation errors:

  equal        等權重
  inverse_vol  反波動率：每檔權重 ∝ 1 / 波動
  risk_parity  風險平價：每檔貢獻一樣多的波動（skfolio RiskBudgeting, variance）
  hrp          階層風險平價（skfolio HierarchicalRiskParity）：先依相關性分群，再在群間、群內分配風險
  min_cvar     最小 CVaR：歷史上最差 5% 日子的平均損失最小（skfolio MeanRisk, minimize CVaR）

Every method is also run out of sample (skfolio WalkForward: fit on the past year, hold for a quarter, roll),
so the comparison shows what each rule would actually have delivered, not how well it fits the past.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from . import dca

METHODS = {
    "current": "目前權重",
    "equal": "等權重",
    "inverse_vol": "反波動率",
    "risk_parity": "風險平價",
    "hrp": "階層風險平價（HRP）",
    "min_cvar": "最小 CVaR",
}
MIN_DAYS = 250
TRAIN, TEST = 252, 63


# ---------------------------------------------------------------- data
def returns_twd(symbols: list[str], years: float = 3.0) -> tuple[pd.DataFrame, dict[str, str]]:
    """Daily simple returns in TWD on a common weekday calendar (markets closed → 0 return that day)."""
    start = dt.date.today() - dt.timedelta(days=int(365.25 * years) + 15)
    cols, skipped = {}, {}
    for s in dict.fromkeys(symbols):
        try:
            px = dca.prices(s, start)
            if len(px) < 30:
                skipped[s] = "歷史價格不足"
                continue
            rate = dca.twd_rate(dca.currency_of(s), start)
            if rate is not None and len(rate):
                r = rate.copy()
                r.index = pd.DatetimeIndex(r.index).tz_localize(None).normalize()
                r = r[~r.index.duplicated(keep="last")].sort_index()
                px = px * r.reindex(px.index, method="ffill").bfill()
            cols[s] = px
        except Exception as e:  # noqa: BLE001
            skipped[s] = f"{type(e).__name__}: {str(e)[:60]}"
    if not cols:
        return pd.DataFrame(), skipped
    cal = pd.bdate_range(max(v.index[0] for v in cols.values()), max(v.index[-1] for v in cols.values()))
    P = pd.DataFrame({k: v.reindex(cal, method="ffill") for k, v in cols.items()}).dropna()
    R = P.pct_change(fill_method=None).iloc[1:].fillna(0.0)
    R = R.clip(-0.5, 0.5)
    return R, skipped


# ---------------------------------------------------------------- models
def _model(method: str, max_weight: float | None = None):
    from skfolio import RiskMeasure
    from skfolio.optimization import EqualWeighted, HierarchicalRiskParity, InverseVolatility, MeanRisk, \
        ObjectiveFunction, RiskBudgeting
    mw = max_weight if max_weight and max_weight < 1 else None
    if method == "equal":
        return EqualWeighted()
    if method == "inverse_vol":
        return InverseVolatility()
    if method == "risk_parity":
        return RiskBudgeting(risk_measure=RiskMeasure.VARIANCE, **({"max_weights": mw} if mw else {}))
    if method == "hrp":
        return HierarchicalRiskParity(**({"max_weights": mw} if mw else {}))
    if method == "min_cvar":
        return MeanRisk(objective_function=ObjectiveFunction.MINIMIZE_RISK, risk_measure=RiskMeasure.CVAR,
                        **({"max_weights": mw} if mw else {}))
    raise ValueError(f"不支援的方法 {method}")


def fit_weights(R: pd.DataFrame, method: str, max_weight: float | None = None) -> pd.Series:
    if R.shape[1] == 1:
        return pd.Series([1.0], index=R.columns)
    m = _model(method, max_weight)
    m.fit(R.values)
    w = pd.Series(np.asarray(m.weights_, dtype=float), index=R.columns).clip(lower=0)
    return w / w.sum() if w.sum() > 0 else pd.Series(1 / R.shape[1], index=R.columns)


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {}
    eq = (1 + r).cumprod()
    yrs = max((r.index[-1] - r.index[0]).days / 365.25, 1 / 365)
    vol = float(r.std() * math.sqrt(252))
    cagr = float(eq.iloc[-1] ** (1 / yrs) - 1) if eq.iloc[-1] > 0 else -1.0
    q = r.quantile(0.05)
    return {"cagr": round(cagr * 100, 2), "vol": round(vol * 100, 2), "sharpe": round(cagr / vol, 2) if vol else None,
            "mdd": round(float((eq / eq.cummax() - 1).min()) * 100, 2), "cvar95": round(float(r[r <= q].mean()) * 100, 2),
            "from": r.index[0].date().isoformat(), "to": r.index[-1].date().isoformat()}


def risk_contrib(R: pd.DataFrame, w: pd.Series) -> pd.Series:
    """Share of the portfolio's variance each holding is responsible for (sums to 100%)."""
    S = R.cov().values * 252
    wv = w.reindex(R.columns).fillna(0).values
    tot = float(wv @ S @ wv)
    if tot <= 0:
        return pd.Series(0.0, index=R.columns)
    return pd.Series(wv * (S @ wv) / tot * 100, index=R.columns)


def walk_forward(R: pd.DataFrame, method: str, fixed: pd.Series | None = None, max_weight: float | None = None) -> pd.Series:
    """Out-of-sample daily returns: fit on the last TRAIN days, hold TEST days, roll. fixed = always these weights
    (the same test days, so it is comparable)."""
    out = []
    i = TRAIN
    while i < len(R):
        tr, te = R.iloc[i - TRAIN:i], R.iloc[i:i + TEST]
        w = fixed.reindex(R.columns).fillna(0) if fixed is not None else fit_weights(tr, method, max_weight)
        # buy-and-hold within the quarter (weights drift), rebalanced at each step
        cum = (1 + te).cumprod()
        val = cum.values @ w.values
        val = np.concatenate([[float(w.sum())], val])
        out.append(pd.Series(val[1:] / val[:-1] - 1, index=te.index))
        i += TEST
    return pd.concat(out) if out else pd.Series(dtype=float)


def compare(R: pd.DataFrame, current: pd.Series | None, methods=None, max_weight: float | None = None) -> list[dict]:
    methods = methods or [m for m in METHODS if m != "current" or current is not None]
    rows = []
    for m in methods:
        try:
            w = current.reindex(R.columns).fillna(0) if m == "current" else fit_weights(R, m, max_weight)
            w = w / w.sum() if w.sum() > 0 else w
            ins = (R @ w)
            oos = walk_forward(R, m, fixed=w if m == "current" else None, max_weight=max_weight) if len(R) > TRAIN + 20 else pd.Series(dtype=float)
            rc = risk_contrib(R, w)
            rows.append({"method": m, "label": METHODS[m], "weights": {k: round(float(v) * 100, 2) for k, v in w.items()},
                         "risk": {k: round(float(v), 1) for k, v in rc.items()}, "in_sample": stats(ins), "oos": stats(oos)})
        except Exception as e:  # noqa: BLE001
            rows.append({"method": m, "label": METHODS[m], "error": f"{type(e).__name__}: {str(e)[:120]}"})
    return rows


# ---------------------------------------------------------------- 持股體檢
def health(holdings: list[dict], years: float = 3.0, max_weight: float | None = None) -> dict:
    """holdings: [{symbol, value_twd, name}] (portfolio() rows)."""
    vals = {h["symbol"]: float(h["value_twd"]) for h in holdings if h.get("value_twd")}
    names = {h["symbol"]: h.get("name") or h["symbol"] for h in holdings}
    if len(vals) < 2:
        raise ValueError("至少要有兩檔有市值的持股才能做組合體檢")
    R, skipped = returns_twd(list(vals), years)
    if R.shape[1] < 2 or len(R) < MIN_DAYS:
        raise ValueError(f"共同的歷史資料不足（{len(R)} 天，至少 {MIN_DAYS} 天）" + (f"；略過：{skipped}" if skipped else ""))
    cur = pd.Series({k: v for k, v in vals.items() if k in R.columns})
    cur = cur / cur.sum()
    rc = risk_contrib(R, cur)
    corr = R.corr()
    rows = compare(R, cur, max_weight=max_weight)
    notes = []
    gap = (rc - cur * 100).sort_values(ascending=False)
    top = gap.index[0]
    if gap.iloc[0] > 10:
        notes.append(f"{names.get(top, top)} 佔資金 {cur[top] * 100:.0f}%，卻貢獻 {rc[top]:.0f}% 的組合波動。")
    pairs = [(a, b, corr.loc[a, b]) for i, a in enumerate(corr.columns) for b in corr.columns[i + 1:]]
    hi = [p for p in pairs if p[2] > 0.8]
    if hi:
        a, b, c = max(hi, key=lambda p: p[2])
        notes.append(f"{names.get(a, a)} 和 {names.get(b, b)} 的日報酬相關係數 {c:.2f}，分散效果有限。")
    cu = next((r for r in rows if r["method"] == "current"), {})
    best = [r for r in rows if r.get("oos") and r["method"] != "current"]
    if cu.get("oos") and best:
        lo = min(best, key=lambda r: r["oos"]["vol"])
        if lo["oos"]["vol"] < cu["oos"]["vol"] * 0.85:
            notes.append(f"樣本外比較：{lo['label']} 的年化波動 {lo['oos']['vol']}%，目前權重是 {cu['oos']['vol']}%；"
                         f"年化報酬 {lo['oos']['cagr']}% vs {cu['oos']['cagr']}%。")
    return {"symbols": list(R.columns), "names": {k: names.get(k, k) for k in R.columns},
            "current": {k: round(float(v) * 100, 2) for k, v in cur.items()}, "risk": {k: round(float(v), 1) for k, v in rc.items()},
            "corr": [[round(float(x), 2) for x in row] for row in corr.values], "methods": rows, "notes": notes,
            "skipped": skipped, "days": len(R), "from": R.index[0].date().isoformat(), "to": R.index[-1].date().isoformat(),
            "method_note": "權重依過去約 3 年的日報酬（換算台幣）計算；樣本外＝每季用前一年資料重算權重、持有一季，滾動串起來。"
                           "不含交易成本與稅；過去的波動與相關性不保證未來。"}


# ---------------------------------------------------------------- 定期定額金額分配
def dca_split(symbols: list[str], total: float, method: str = "risk_parity", years: float = 3.0,
              step: float = 100.0, max_weight: float | None = None) -> dict:
    total = float(total)
    if not 100 <= total <= 1e8:
        raise ValueError("每期總金額要介於 100 元到 1 億元")
    R, skipped = returns_twd(symbols, years)
    if R.shape[1] < 2 or len(R) < 120:
        raise ValueError("至少要兩個有足夠歷史價格的標的" + (f"；略過：{skipped}" if skipped else ""))
    w = fit_weights(R, method, max_weight) if method != "equal" else pd.Series(1 / R.shape[1], index=R.columns)
    amt = (w * total / step).round() * step
    diff = total - amt.sum()  # rounding leftovers go to the largest weight
    amt[w.idxmax()] += diff
    rc = risk_contrib(R, w)
    return {"method": method, "label": METHODS.get(method, method), "total": total,
            "rows": [{"symbol": s, "weight": round(float(w[s]) * 100, 2), "amount": float(amt[s]), "risk": round(float(rc[s]), 1),
                      "vol": round(float(R[s].std() * math.sqrt(252) * 100), 1)} for s in R.columns],
            "skipped": skipped, "days": len(R)}
