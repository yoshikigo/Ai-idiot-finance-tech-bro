"""A small, safe expression language over panels — FinLab's `data.get(...)` + pandas, without exec.

    營收年增率 > 20
    new_high(ma(月營收, 3), 12)            # 近 3 月平均營收創 12 個月新高
    收盤價 > ma(收盤價, 60) & 投信買賣超 > 0
    rank(本益比) < 0.3                      # 本益比最低 30%

Monthly / quarterly fields keep their own frequency until they meet a daily one, so `shift(月營收, 12)`
means "12 months ago" and `rise(每股盈餘, 2)` means "two consecutive quarters of growth".
Only whitelisted AST nodes, names and functions are accepted.
"""
from __future__ import annotations

import ast

import numpy as np
import pandas as pd

from . import fields as F


class ExprError(ValueError):
    pass


class P:
    """Panel with a frequency (D, M, Q)."""
    __slots__ = ("df", "freq")

    def __init__(self, df: pd.DataFrame, freq: str):
        self.df, self.freq = df, freq

    def daily(self, cal) -> pd.DataFrame:
        if self.freq == "D":
            return self.df.reindex(cal)
        return F.to_daily(self.df, self.freq, cal)


FREQ_RANK = {"D": 0, "M": 1, "Q": 2}


def _align(a, b, cal):
    if isinstance(a, P) and isinstance(b, P):
        if a.freq == b.freq and a.freq != "D":
            idx = a.df.index.union(b.df.index)
            cols = a.df.columns.union(b.df.columns)
            return a.df.reindex(index=idx, columns=cols), b.df.reindex(index=idx, columns=cols), a.freq
        da, db = a.daily(cal), b.daily(cal)
        cols = da.columns.union(db.columns)
        return da.reindex(columns=cols), db.reindex(columns=cols), "D"
    if isinstance(a, P):
        return a.df, b, a.freq
    if isinstance(b, P):
        return a, b.df, b.freq
    return a, b, None


def _n(x, name, lo=1, hi=2000) -> int:
    if isinstance(x, P) or not isinstance(x, (int, float)) or int(x) != x or not lo <= x <= hi:
        raise ExprError(f"{name} 的期數要是 {lo}–{hi} 的整數")
    return int(x)


def _p(x, name) -> P:
    if not isinstance(x, P):
        raise ExprError(f"{name} 的第一個參數要是欄位或運算式")
    return x


def _rsi(df: pd.DataFrame, n: int) -> pd.DataFrame:
    d = df.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _bool(df: pd.DataFrame) -> pd.DataFrame:
    return df.fillna(False).astype(bool) if df.dtypes.eq(object).any() else df.astype(float).fillna(0).astype(bool)


FUNCS = {
    "ma": (lambda x, n: P(_p(x, "ma").df.rolling(_n(n, "ma"), min_periods=_n(n, "ma")).mean(), x.freq), "移動平均", "ma(收盤價, 20)"),
    "ema": (lambda x, n: P(_p(x, "ema").df.ewm(span=_n(n, "ema"), adjust=False, min_periods=_n(n, "ema")).mean(), x.freq), "指數移動平均", "ema(收盤價, 12)"),
    "sum": (lambda x, n: P(_p(x, "sum").df.rolling(_n(n, "sum"), min_periods=_n(n, "sum")).sum(), x.freq), "加總 n 期", "sum(投信買賣超, 5)"),
    "std": (lambda x, n: P(_p(x, "std").df.rolling(_n(n, "std", 2), min_periods=_n(n, "std", 2)).std(), x.freq), "標準差", "std(收盤價, 20)"),
    "highest": (lambda x, n: P(_p(x, "highest").df.rolling(_n(n, "highest"), min_periods=1).max(), x.freq), "n 期最高", "highest(收盤價, 60)"),
    "lowest": (lambda x, n: P(_p(x, "lowest").df.rolling(_n(n, "lowest"), min_periods=1).min(), x.freq), "n 期最低", "lowest(收盤價, 60)"),
    "shift": (lambda x, n=1: P(_p(x, "shift").df.shift(_n(n, "shift", 1, 500)), x.freq), "n 期前的值", "shift(月營收, 12)"),
    "diff": (lambda x, n=1: P(_p(x, "diff").df.diff(_n(n, "diff", 1, 500)), x.freq), "與 n 期前的差", "diff(融資餘額, 5)"),
    "pct": (lambda x, n=1: P((_p(x, "pct").df / x.df.shift(_n(n, "pct", 1, 500)) - 1) * 100, x.freq), "n 期漲跌幅（%）", "pct(收盤價, 20)"),
    "rsi": (lambda x, n=14: P(_rsi(_p(x, "rsi").df, _n(n, "rsi", 2, 200)), x.freq), "RSI", "rsi(收盤價, 14)"),
    "rank": (lambda x: P(_p(x, "rank").df.rank(axis=1, pct=True), x.freq), "當期橫向百分位（0–1，越大越前面）", "rank(殖利率) > 0.8"),
    "zscore": (lambda x: P(_p(x, "zscore").df.sub(x.df.mean(axis=1), axis=0).div(x.df.std(axis=1).replace(0, np.nan), axis=0), x.freq), "當期橫向標準化", "zscore(ROE)"),
    "abs": (lambda x: P(_p(x, "abs").df.abs(), x.freq), "絕對值", "abs(營收月增率)"),
    "log": (lambda x: P(np.log(_p(x, "log").df.where(x.df > 0)), x.freq), "自然對數", "log(市值)"),
    "rise": (lambda x, n=1: P(_cons(_p(x, "rise").df, _n(n, "rise", 1, 60), 1), x.freq), "連續 n 期成長", "rise(月營收, 3)"),
    "fall": (lambda x, n=1: P(_cons(_p(x, "fall").df, _n(n, "fall", 1, 60), -1), x.freq), "連續 n 期衰退", "fall(融資餘額, 5)"),
    "new_high": (lambda x, n: P(x.df >= _p(x, "new_high").df.rolling(_n(n, "new_high", 2), min_periods=_n(n, "new_high", 2)).max(), x.freq), "創 n 期新高", "new_high(收盤價, 250)"),
    "new_low": (lambda x, n: P(x.df <= _p(x, "new_low").df.rolling(_n(n, "new_low", 2), min_periods=_n(n, "new_low", 2)).min(), x.freq), "創 n 期新低", "new_low(收盤價, 60)"),
    "count": (lambda x, n: P(_bool(_p(x, "count").df).astype(float).rolling(_n(n, "count"), min_periods=1).sum(), x.freq), "n 期內成立次數", "count(投信買賣超 > 0, 10) >= 7"),
    "always": (lambda x, n: P(_bool(_p(x, "always").df).astype(float).rolling(_n(n, "always"), min_periods=_n(n, "always")).min() > 0, x.freq), "連續 n 期都成立", "always(外資買賣超 > 0, 5)"),
    "ever": (lambda x, n: P(_bool(_p(x, "ever").df).astype(float).rolling(_n(n, "ever"), min_periods=1).max() > 0, x.freq), "n 期內曾經成立", "ever(new_high(收盤價, 250), 20)"),
}
ALIASES = {"均線": "ma", "平均": "ma", "mean": "ma", "加總": "sum", "標準差": "std", "最高": "highest", "最低": "lowest",
           "前期": "shift", "差": "diff", "漲跌幅": "pct", "排名": "rank", "連續成長": "rise", "連續衰退": "fall",
           "創新高": "new_high", "創新低": "new_low", "次數": "count", "持續": "always", "曾經": "ever", "max": "highest",
           "min": "lowest"}


def _cons(df: pd.DataFrame, n: int, sign: int) -> pd.DataFrame:
    d = df.diff()
    ok = (d > 0) if sign > 0 else (d < 0)
    return ok.astype(float).rolling(n, min_periods=n).min() > 0


def cross(a: P, b, up: bool, cal) -> P:
    x, y, _ = _align(a, b, cal)
    if isinstance(y, pd.DataFrame):
        prev = (x.shift(1) <= y.shift(1)) if up else (x.shift(1) >= y.shift(1))
    else:
        prev = (x.shift(1) <= y) if up else (x.shift(1) >= y)
    now = (x > y) if up else (x < y)
    return P(prev & now, a.freq if isinstance(a, P) and isinstance(b, (int, float)) else "D")


_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / (b.replace(0, np.nan) if isinstance(b, pd.DataFrame) else (b or np.nan)),
        ast.BitAnd: lambda a, b: _bool(a) & _bool(b) if isinstance(a, pd.DataFrame) else a & b,
        ast.BitOr: lambda a, b: _bool(a) | _bool(b) if isinstance(a, pd.DataFrame) else a | b}
_CMP = {ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def parse(text: str) -> ast.Expression:
    t = (text or "").strip().replace("＞", ">").replace("＜", "<").replace("＝", "=").replace("，", ",").replace("（", "(") \
        .replace("）", ")").replace(" and ", " & ").replace(" or ", " | ").replace("且", " & ").replace("或", " | ")
    if not t:
        raise ExprError("空的條件")
    if len(t) > 400:
        raise ExprError("條件太長")
    try:
        tree = ast.parse(t, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"語法錯誤：{e.msg}") from e
    for node in ast.walk(tree):
        ok = isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Name,
                               ast.Constant, ast.Load, ast.And, ast.Or, ast.USub, ast.UAdd, ast.Not, ast.Invert,
                               *(_BIN.keys()), *(_CMP.keys())))
        if not ok:
            raise ExprError(f"不支援的語法：{type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise ExprError("只能用數字常數")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise ExprError("函數呼叫格式錯誤")
            fn = ALIASES.get(node.func.id, node.func.id)
            if fn not in FUNCS and fn not in ("cross_up", "cross_down", "黃金交叉", "死亡交叉"):
                raise ExprError(f"不認得的函數：{node.func.id}")
        if isinstance(node, ast.Name):
            parent_call = False
            for p in ast.walk(tree):
                if isinstance(p, ast.Call) and p.func is node:
                    parent_call = True
            if not parent_call and node.id not in F.BY_NAME:
                raise ExprError(f"不認得的欄位：{node.id}（可用欄位見資料庫目錄）")
    return tree


def names(text: str) -> list[str]:
    tree = parse(text)
    calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and id(n) not in calls:
            nm = F.BY_NAME[n.id].name
            if nm not in out:
                out.append(nm)
    return out


def evaluate(text: str, cal: pd.DatetimeIndex, codes: pd.Index | None = None) -> P:
    tree = parse(text)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            f = F.BY_NAME[n.id]
            df = F.raw(f)
            if codes is not None and not df.empty:
                df = df.reindex(columns=codes)
            return P(df, f.freq)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.USub):
                return P(-v.df, v.freq) if isinstance(v, P) else -v
            if isinstance(n.op, ast.UAdd):
                return v
            return P(~_bool(v.df), v.freq) if isinstance(v, P) else (not v)
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            x, y, fq = _align(a, b, cal)
            r = _BIN[type(n.op)](x, y)
            return P(r, fq) if fq else r
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            acc = vals[0]
            for v in vals[1:]:
                x, y, fq = _align(acc, v, cal)
                r = (_bool(x) & _bool(y)) if isinstance(n.op, ast.And) else (_bool(x) | _bool(y))
                acc = P(r, fq)
            return acc
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            acc = None
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                x, y, fq = _align(left, right, cal)
                r = P(_CMP[type(op)](x, y), fq or "D")
                if isinstance(x, pd.DataFrame):
                    r.df = r.df & x.notna() if not isinstance(y, pd.DataFrame) else r.df & x.notna() & y.notna()
                if acc is None:
                    acc = r
                else:
                    a1, b1, fq2 = _align(acc, r, cal)
                    acc = P(_bool(a1) & _bool(b1), fq2)
                left = right
            return acc
        if isinstance(n, ast.Call):
            name = ALIASES.get(n.func.id, n.func.id)
            args = [ev(a) for a in n.args]
            if name in ("cross_up", "cross_down", "黃金交叉", "死亡交叉"):
                if len(args) != 2:
                    raise ExprError("cross_up(a, b) 需要兩個參數")
                return cross(_p(args[0], name), args[1], name in ("cross_up", "黃金交叉"), cal)
            fn = FUNCS[name][0]
            try:
                return fn(*args)
            except ExprError:
                raise
            except (TypeError, AttributeError) as e:
                raise ExprError(f"{n.func.id} 參數不對（例：{FUNCS[name][2]}）") from e
        raise ExprError(f"不支援：{type(n).__name__}")

    v = ev(tree)
    if not isinstance(v, P):
        raise ExprError("運算式要包含至少一個欄位")
    return v


def to_daily_bool(p: P, cal) -> pd.DataFrame:
    d = p.daily(cal)
    return d.fillna(False).astype(bool) if d.dtypes.eq(bool).all() or d.dtypes.eq(object).any() else d.fillna(0).astype(bool)


def help_table() -> list[dict]:
    return [{"name": k, "desc": v[1], "example": v[2]} for k, v in FUNCS.items()] + [
        {"name": "cross_up / cross_down", "desc": "黃金交叉／死亡交叉（a 由下往上／上往下穿越 b）", "example": "cross_up(ma(收盤價, 5), ma(收盤價, 20))"}]
