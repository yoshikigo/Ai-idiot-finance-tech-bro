"""Is the backtest believable? — checks borrowed from Blave Agent's backtest chain, adapted to a stock-picking
portfolio (conditions + rank, rebalanced on a schedule).

  random_test   隨機選股檢定：at every rebalance draw the same number of stocks at random from the stocks that
                were eligible that day (same universe, same liquidity floor, same costs), many times. Gives the
                「隨機選股基準」(median and 5–95% band of CAGR) and a permutation p-value: the share of random
                portfolios whose Sharpe is at least the strategy's. p > 0.05 → the picks may be no better than luck.
                (This is the portfolio form of Blave's MCPT, which permutes one asset's returns under fixed
                positions — a long-only multi-stock portfolio's Sharpe does not change when its own days are
                shuffled, so the null here is "random stocks", not "random days".)
  split         樣本內／樣本外：the strategy's first 70% vs last 30% of the period (does it fade?).
  scan          參數掃描：vary one or two numbers of the strategy (a threshold in a condition, a window, 前 N 檔)
                on a 5×5 grid → Sharpe heatmap, neighbourhood average ("plateau": is the chosen point in a broad
                good region or a lone spike?), best point on the grid edge (= the range may be too narrow).
  walk_forward  樣本外前進：every year from year 4 on, pick the grid point with the best Sharpe on all data
                *before* that year and hold it for that year; chain the out-of-sample years.

All of it runs on a fast vectorised simulator (close-to-close returns from the day after the signal, equal weight
capped by max_weight, costs charged on turnover). It ignores stop-loss / take-profit and the entry day's
open-to-close move, so its numbers differ slightly from the full backtest — but the strategy and every random /
grid portfolio go through the same simulator, so the comparisons are like for like.
"""
from __future__ import annotations

import math
import re
import time

import numpy as np
import pandas as pd

from . import backtest as B, expr, fields as F

NUM = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w.])")
MULT = (0.6, 0.8, 1.0, 1.2, 1.4)


# ---------------------------------------------------------------- fast simulator
class Sim:
    def __init__(self, P: dict):
        self.P = P
        s, cal, codes = P["s"], P["cal"], P["codes"]
        C = F._prices("adj").reindex(index=cal, columns=codes)
        R = C.pct_change(fill_method=None).values
        self.R = np.nan_to_num(R, nan=0.0, posinf=0.0, neginf=0.0)
        self.R[np.abs(self.R) > 0.6] = 0.0  # bad ticks / unadjusted splits must not dominate a random draw
        self.liq = F._prices("value").reindex(index=cal, columns=codes).rolling(20, min_periods=5).mean().values
        self.cal, self.codes, self.s = cal, codes, s
        end_i = int(cal.searchsorted(P["end"], side="right")) - 1
        self.end_i = max(0, min(end_i, len(cal) - 1))
        self.reb_i = [cal.get_loc(d) for d in P["rebal"] if cal.get_loc(d) < self.end_i]
        fee = B.FEE * s["fee_discount"]
        self.buy_cost = fee + s["slippage"]
        self.sell_cost = fee + B.TAX + s["slippage"]
        self.maskv = P["mask"].values

    def picks(self, cond: pd.DataFrame, score: pd.DataFrame | None) -> list[np.ndarray]:
        cv = cond.reindex(index=self.cal, columns=self.codes, fill_value=False).values
        sv = None if score is None else score.reindex(index=self.cal, columns=self.codes).values
        col = {c: j for j, c in enumerate(self.codes)}
        out = []
        for i in self.reb_i:
            sel = B._picks_np(cv[i], None if sv is None else sv[i], self.liq[i], self.codes, self.s)
            out.append(np.array([col[c] for c in sel], dtype=int))
        return out

    def run(self, picks: list[np.ndarray]) -> pd.Series:
        """Daily returns on days base+1 … end (base = first signal day).
        Period k: the signal on day i trades at the next open (day a = i+1; costs charged that day) and the new holdings
        earn close-to-close returns on days a … e, where e is the next signal day (or the end)."""
        base = self.reb_i[0]
        out = np.zeros(self.end_i - base)
        prev: set = set()
        prev_w = 0.0
        for k, i in enumerate(self.reb_i):
            a = i + 1
            e = self.reb_i[k + 1] if k + 1 < len(self.reb_i) else self.end_i
            if a > e:
                continue
            idx = picks[k]
            w = min(1.0 / len(idx), self.s["max_weight"]) if len(idx) else 0.0
            cur = set(idx.tolist())
            cost = w * len(cur - prev) * self.buy_cost + prev_w * len(prev - cur) * self.sell_cost
            if len(idx):  # the new holdings own the execution day too (gap + open→close, approximated by close→close)
                cum = np.cumprod(1.0 + self.R[a:e + 1][:, idx], axis=0)
                val = np.concatenate([[1.0], w * cum.sum(axis=1) + (1 - w * len(idx))])
                r = val[1:] / val[:-1] - 1
            else:
                r = np.zeros(e - a + 1)
            r[0] = (1 + r[0]) * (1 - cost) - 1
            out[a - base - 1: e - base] = r
            prev, prev_w = cur, w
        return pd.Series(out, index=self.cal[base + 1: self.end_i + 1])


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {"cagr": None, "sharpe": None, "mdd": None, "total": None}
    eq = (1 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1 / 365)
    sd = r.std()
    return {"cagr": (eq.iloc[-1] ** (1 / years) - 1) * 100 if eq.iloc[-1] > 0 else -100.0,
            "sharpe": float(r.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0,
            "mdd": float((eq / eq.cummax() - 1).min() * 100), "total": float((eq.iloc[-1] - 1) * 100)}


def _r(x, d=2):
    return None if x is None or not np.isfinite(x) else round(float(x), d)


# ---------------------------------------------------------------- 1) random picks + p-value, 2) split
def random_test(P: dict, sim: Sim, strat_picks: list[np.ndarray], n: int | None = None, seed: int = 7) -> dict:
    periods = len(sim.reb_i)
    n = n or int(min(400, max(60, 40000 // max(periods, 1))))
    rng = np.random.default_rng(seed)
    elig = [np.flatnonzero(sim.maskv[i]) for i in sim.reb_i]
    strat = sim.run(strat_picks)
    st = stats(strat)
    sh, cg, curves = [], [], []
    for _ in range(n):
        picks = [rng.choice(e, size=min(len(p), len(e)), replace=False) if len(e) and len(p) else np.zeros(0, int)
                 for e, p in zip(elig, strat_picks)]
        r = sim.run(picks)
        x = stats(r)
        sh.append(x["sharpe"] if x["sharpe"] is not None else 0.0)
        cg.append(x["cagr"] if x["cagr"] is not None else 0.0)
        curves.append((1 + r).cumprod().values)
    sh, cg = np.array(sh), np.array(cg)
    p = (1 + int((sh >= (st["sharpe"] or 0)).sum())) / (n + 1)
    C = np.vstack(curves)
    step = max(1, C.shape[1] // 400)
    idx = strat.index[::step]
    q = np.percentile(C[:, ::step], [5, 50, 95], axis=0)
    seq = (1 + strat).cumprod().values[::step]
    hist_edges = np.linspace(min(sh.min(), st["sharpe"] or 0) - 0.05, max(sh.max(), st["sharpe"] or 0) + 0.05, 25)
    counts, _ = np.histogram(sh, bins=hist_edges)
    return {"n": n, "p_value": round(p, 4), "strategy": {k: _r(v) for k, v in st.items()},
            "random": {"cagr_p5": _r(np.percentile(cg, 5)), "cagr_median": _r(np.median(cg)), "cagr_p95": _r(np.percentile(cg, 95)),
                       "sharpe_median": _r(np.median(sh)), "beat_share": round(float((sh < (st["sharpe"] or 0)).mean()) * 100, 1)},
            "curve": [{"t": d.date().isoformat(), "s": _r(a, 4), "p5": _r(b, 4), "p50": _r(c, 4), "p95": _r(e, 4)}
                      for d, a, b, c, e in zip(idx, seq, q[0], q[1], q[2])],
            "hist": {"edges": [round(float(x), 3) for x in hist_edges], "counts": counts.tolist()},
            "_strat_returns": strat}


def split_test(strat: pd.Series, frac: float = 0.7) -> dict:
    k = int(len(strat) * frac)
    if k < 120 or len(strat) - k < 60:
        return {}
    a, b = stats(strat.iloc[:k]), stats(strat.iloc[k:])
    return {"cut": strat.index[k].date().isoformat(), "in": {x: _r(v) for x, v in a.items()},
            "out": {x: _r(v) for x, v in b.items()},
            "faded": bool(a["sharpe"] is not None and b["sharpe"] is not None and a["sharpe"] > 0 and b["sharpe"] < a["sharpe"] * 0.5)}


def verdict(rt: dict, sp: dict) -> list[str]:
    out = []
    p = rt["p_value"]
    rs, rr = rt["strategy"], rt["random"]
    if p <= 0.05:
        out.append(f"✓ 選股有統計意義：{rt['n']} 組隨機選股中只有 {p * 100:.1f}% 的夏普值不輸策略（p = {p:.3f}）。")
    else:
        out.append(f"✗ 可能只是運氣：{p * 100:.0f}% 的隨機選股夏普值不輸策略（p = {p:.3f}，要 ≤ 0.05 才算有意義）。")
    if rs.get("cagr") is not None and rr.get("cagr_median") is not None:
        out.append(f"年化 {rs['cagr']:+.1f}%，隨機選股中位數 {rr['cagr_median']:+.1f}%（90% 落在 {rr['cagr_p5']:+.1f}%～{rr['cagr_p95']:+.1f}%）。")
    if sp:
        out.append(f"{'✗ 後段明顯變差' if sp['faded'] else '樣本內外'}：{sp['cut']} 前夏普 {sp['in']['sharpe']}、之後 {sp['out']['sharpe']}"
                   f"（年化 {sp['in']['cagr']:+.1f}% → {sp['out']['cagr']:+.1f}%）。")
    return out


# ---------------------------------------------------------------- 3) parameters
def numeric_params(spec: dict) -> list[dict]:
    """Numbers in the strategy that can be scanned: each literal in a condition / the rank, and 前 N 檔."""
    s = B.normalize(spec)
    out = []
    for ci, c in enumerate(s["conditions"]):
        for k, m in enumerate(NUM.finditer(c)):
            v = float(m.group(1))
            out.append({"id": f"c{ci}:{k}", "value": v, "integer": "." not in m.group(1),
                        "label": f"{c[:m.start()]}【{m.group(1)}】{c[m.end():]}"})
    if s["rank"]:
        for k, m in enumerate(NUM.finditer(s["rank"])):
            out.append({"id": f"r:{k}", "value": float(m.group(1)), "integer": "." not in m.group(1),
                        "label": f"排名 {s['rank'][:m.start()]}【{m.group(1)}】{s['rank'][m.end():]}"})
    out.append({"id": "top_n", "value": float(s["top_n"]), "integer": True, "label": f"前 N 檔（目前 {s['top_n']}）"})
    return out


def grid_values(v: float, integer: bool) -> list[float]:
    if v == 0:
        vals = [-2, -1, 0, 1, 2] if integer else [-1.0, -0.5, 0.0, 0.5, 1.0]
    else:
        vals = [v * m for m in MULT]
    if integer:
        vals = sorted({int(round(x)) for x in vals})
        if len(vals) < 5:  # small integers (e.g. 前 3 檔): five consecutive values around it
            lo = max(1, int(v) - 2) if v > 0 else int(v) - 2
            vals = list(range(lo, lo + 5))
    else:
        vals = [round(x, 4) for x in vals]
    return vals


def apply_param(spec: dict, pid: str, value) -> dict:
    s = B.normalize(spec)
    txt = (str(int(value)) if float(value).is_integer() else f"{value:g}")
    if pid == "top_n":
        s["top_n"] = max(1, int(round(value)))
        return s
    kind, k = pid.split(":")
    k = int(k)

    def sub(text):
        ms = list(NUM.finditer(text))
        m = ms[k]
        return text[:m.start()] + txt + text[m.end():]
    if kind == "r":
        s["rank"] = sub(s["rank"])
    else:
        ci = int(kind[1:])
        s["conditions"][ci] = sub(s["conditions"][ci])
    return s


class _Eval:
    """Evaluate conditions once per distinct text (a scan changes one condition at a time)."""

    def __init__(self, P):
        self.P, self.cache = P, {}

    def cond(self, s):
        cal, codes = self.P["cal"], self.P["codes"]
        out = self.P["mask"].copy()
        for c in s["conditions"]:
            if c not in self.cache:
                self.cache[c] = expr.to_daily_bool(expr.evaluate(c, cal, codes), cal).reindex(index=cal, columns=codes, fill_value=False)
            out &= self.cache[c]
        score = None
        if s["rank"]:
            key = "rank:" + s["rank"]
            if key not in self.cache:
                self.cache[key] = expr.evaluate(s["rank"], cal, codes).daily(cal).reindex(columns=codes).astype(float)
            score = self.cache[key]
        return out, score


def scan(spec: dict, params: list[str], P: dict | None = None, sim: Sim | None = None, budget_s: float = 600) -> dict:
    P = P or B.prepare(spec)
    sim = sim or Sim(P)
    s0 = P["s"]
    allp = {p["id"]: p for p in numeric_params(s0)}
    params = [p for p in params if p in allp][:2] or ["top_n"]
    axes = [grid_values(allp[p]["value"], allp[p]["integer"]) for p in params]
    if len(axes) == 1:
        axes.append([None])
    ev = _Eval(P)
    t0 = time.time()
    sh = np.full((len(axes[0]), len(axes[1])), np.nan)
    cg = np.full_like(sh, np.nan)
    rets: dict[tuple, pd.Series] = {}
    for a, va in enumerate(axes[0]):
        for b, vb in enumerate(axes[1]):
            if time.time() - t0 > budget_s:
                break
            s = apply_param(s0, params[0], va)
            if vb is not None:
                s = apply_param(s, params[1], vb)
            try:
                cond, score = ev.cond(s)
            except (expr.ExprError, ValueError):
                continue
            sim.s = s
            r = sim.run(sim.picks(cond, score))
            x = stats(r)
            sh[a, b], cg[a, b] = (x["sharpe"] if x["sharpe"] is not None else np.nan), (x["cagr"] if x["cagr"] is not None else np.nan)
            rets[(a, b)] = r
    sim.s = s0
    cur = (axes[0].index(_closest(axes[0], allp[params[0]]["value"])),
           0 if axes[1] == [None] else axes[1].index(_closest(axes[1], allp[params[1]]["value"])))
    nb = _neighbour_mean(sh)
    best = np.unravel_index(np.nanargmax(sh), sh.shape) if np.isfinite(sh).any() else cur
    plateau_best = np.unravel_index(np.nanargmax(nb), nb.shape) if np.isfinite(nb).any() else cur
    edge = (best[0] in (0, sh.shape[0] - 1)) or (sh.shape[1] > 1 and best[1] in (0, sh.shape[1] - 1))
    cur_sh, cur_nb = sh[cur], nb[cur]
    spike = bool(np.isfinite(cur_sh) and np.isfinite(cur_nb) and cur_sh > 0 and cur_nb < cur_sh * 0.6)
    wf = walk_forward(rets, axes, cur)
    notes = []
    if np.isfinite(cur_sh) and cur_sh <= 0:
        notes.append(f"目前參數的夏普 {cur_sh:.2f} ≤ 0：策略本身沒有賺到風險報酬，參數穩不穩定的意義不大。")
    elif spike:
        notes.append(f"✗ 目前參數像孤立的高點：自己夏普 {cur_sh:.2f}，周圍平均只有 {cur_nb:.2f}，參數稍微變動績效就掉很多（過度調參的跡象）。")
    elif np.isfinite(cur_nb):
        notes.append(f"✓ 目前參數在穩定區域：自己夏普 {cur_sh:.2f}，周圍平均 {cur_nb:.2f}。")
    if edge and np.isfinite(sh[best]) and sh[best] > 0:
        notes.append("最佳點在掃描範圍的邊緣：真正的最佳值可能在範圍外，或這個參數越極端越好（要小心）。")
    if wf.get("oos"):
        o, c = wf["oos"], wf["current"]
        notes.append(f"樣本外前進（每年用之前的資料挑參數）：夏普 {o['sharpe']}、年化 {o['cagr']:+.1f}%；"
                     f"同期間固定用目前參數：夏普 {c['sharpe']}、年化 {c['cagr']:+.1f}%。")
    return {"params": [allp[p] | {"values": ax} for p, ax in zip(params, axes) if ax != [None]],
            "sharpe": [[_r(v) for v in row] for row in sh], "cagr": [[_r(v, 1) for v in row] for row in cg],
            "neighbour": [[_r(v) for v in row] for row in nb], "current": list(map(int, cur)), "best": list(map(int, best)),
            "plateau_best": list(map(int, plateau_best)), "on_edge": bool(edge), "spike": spike,
            "walk_forward": wf, "notes": notes, "seconds": round(time.time() - t0, 1)}


def _closest(vals, v):
    return min(vals, key=lambda x: abs((x if x is not None else 0) - v))


def _neighbour_mean(g: np.ndarray) -> np.ndarray:
    out = np.full_like(g, np.nan)
    for i in range(g.shape[0]):
        for j in range(g.shape[1]):
            w = g[max(0, i - 1):i + 2, max(0, j - 1):j + 2]
            if np.isfinite(w).any():
                out[i, j] = np.nanmean(w)
    return out


# ---------------------------------------------------------------- 4) walk-forward on the scan's return series
def walk_forward(rets: dict[tuple, pd.Series], axes, cur: tuple, min_train_years: int = 3) -> dict:
    if not rets or cur not in rets:
        return {}
    idx = rets[cur].index
    years = sorted(set(idx.year))
    if len(years) < min_train_years + 1:
        return {"note": f"資料只有 {len(years)} 年，至少要 {min_train_years + 1} 年才能做樣本外前進"}
    oos, curp, folds = [], [], []
    for y in years[min_train_years:]:
        tr = {k: r[r.index.year < y] for k, r in rets.items()}
        te = {k: r[r.index.year == y] for k, r in rets.items()}
        sc = {k: stats(v)["sharpe"] for k, v in tr.items()}
        sc = {k: v for k, v in sc.items() if v is not None}
        if not sc:
            continue
        pick = max(sc, key=sc.get)
        oos.append(te[pick])
        curp.append(te[cur])
        lab = [axes[0][pick[0]]] + ([axes[1][pick[1]]] if axes[1] != [None] else [])
        folds.append({"year": int(y), "chosen": lab, "train_sharpe": _r(sc[pick]),
                      "test_ret": _r(((1 + te[pick]).prod() - 1) * 100, 1), "current_ret": _r(((1 + te[cur]).prod() - 1) * 100, 1)})
    if not oos:
        return {}
    o, c = pd.concat(oos), pd.concat(curp)
    return {"folds": folds, "oos": {k: _r(v) for k, v in stats(o).items()}, "current": {k: _r(v) for k, v in stats(c).items()},
            "from": o.index[0].date().isoformat()}


# ---------------------------------------------------------------- entry point
def run(spec: dict, params: list[str] | None = None, n: int | None = None) -> dict:
    t0 = time.time()
    P = B.prepare(spec)
    sim = Sim(P)
    strat_picks = sim.picks(P["cond"], P["score"])
    rt = random_test(P, sim, strat_picks, n=n)
    strat = rt.pop("_strat_returns")
    sp = split_test(strat)
    out = {"spec": P["s"], "random": rt, "split": sp, "verdict": verdict(rt, sp), "params": numeric_params(P["s"]),
           "disposal": B.disposal_note(P["s"]),
           "method": "快速模擬：訊號隔天起以收盤價計報酬、等權重（受單檔上限限制）、換股時依週轉扣手續費／證交稅／滑價；"
                     "不含停損停利與進場當天開盤到收盤的漲跌，所以數字和完整回測略有差異，但策略與隨機／各參數組合用同一套算法，彼此可比。"}
    if params == "auto":  # the first two numbers of the strategy's conditions (前 N 檔 if there are none)
        ps = [p["id"] for p in out["params"]]
        params = [x for x in ps if x != "top_n"][:2] or ["top_n"]
    if params:
        out["scan"] = scan(spec, params, P, sim)
    out["seconds"] = round(time.time() - t0, 1)
    return B.py(out)
