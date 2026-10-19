"""Field catalog → panels (date × code), point-in-time.

Daily fields are indexed by trading date (published after that day's close; the backtest trades on the
next day). Monthly / quarterly fields are indexed by their **legal publication deadline** (月營收：次月
10 日；財報：5/15、8/14、11/14、3/31), so a backtest can never see a number before it was public.
"""
from __future__ import annotations

import os
import pickle
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import store


@dataclass(frozen=True)
class Field:
    name: str          # 中文名稱 (also the identifier used in expressions)
    dataset: str       # store table / derived group
    col: str
    freq: str          # D / M / Q
    unit: str
    desc: str
    aliases: tuple = ()


DATASETS = {
    "prices": {"name": "股價與成交", "freq": "日", "source": "Yahoo Finance（還原與未還原 OHLCV）", "table": "prices",
               "note": "還原股價用於報酬計算（除權息不會被當成下跌）；成交金額為收盤價×成交量估算"},
    "valuation": {"name": "本益比、殖利率、股價淨值比", "freq": "日", "table": "valuation",
                  "source": "證交所 BWIBBU_d、櫃買 本益比查詢"},
    "insti": {"name": "三大法人買賣超", "freq": "日", "table": "insti", "source": "證交所 T86、櫃買 三大法人日報"},
    "margin": {"name": "融資融券", "freq": "日", "table": "margin", "source": "證交所 MI_MARGN、櫃買 融資融券餘額"},
    "revenue": {"name": "月營收", "freq": "月", "table": "revenue", "source": "公開資訊觀測站 t21sc03（上市、上櫃、KY）",
                "note": "以法定公布期限（次月 10 日）對齊，避免前視偏差"},
    "financials": {"name": "財務報表（季）", "freq": "季", "table": "income_ytd", "source": "公開資訊觀測站 t163sb04／t163sb05 彙總表",
                   "note": "損益表為累計數，已換算單季；以法定期限（5/15、8/14、11/14、3/31）對齊"},
}

FIELDS: list[Field] = [
    Field("收盤價", "prices", "close", "D", "元", "當日收盤價（未還原）", ("close",)),
    Field("開盤價", "prices", "open", "D", "元", "當日開盤價（未還原）", ("open",)),
    Field("最高價", "prices", "high", "D", "元", "當日最高價", ("high",)),
    Field("最低價", "prices", "low", "D", "元", "當日最低價", ("low",)),
    Field("還原收盤價", "prices", "adj", "D", "元", "除權息還原後收盤價（回測報酬用這個）", ("adj_close",)),
    Field("成交量", "prices", "volume", "D", "張", "成交股數 ÷ 1000", ("volume",)),
    Field("成交金額", "prices", "value", "D", "百萬元", "收盤價 × 成交量（估算）", ("turnover",)),
    Field("本益比", "valuation", "pe", "D", "倍", "股價 ÷ 近四季 EPS（虧損時無值）", ("pe",)),
    Field("殖利率", "valuation", "dy", "D", "%", "近一年現金股利 ÷ 股價", ("dividend_yield",)),
    Field("股價淨值比", "valuation", "pb", "D", "倍", "股價 ÷ 每股淨值", ("pb",)),
    Field("外資買賣超", "insti", "f_net", "D", "張", "外陸資（不含外資自營商）買賣超", ("foreign",)),
    Field("投信買賣超", "insti", "t_net", "D", "張", "投信買賣超", ("trust",)),
    Field("自營商買賣超", "insti", "d_net", "D", "張", "自營商（自行買賣＋避險）買賣超", ("dealer",)),
    Field("三大法人買賣超", "insti", "all_net", "D", "張", "三大法人合計買賣超", ("institutional",)),
    Field("融資餘額", "margin", "margin", "D", "張", "融資今日餘額", ("margin_balance",)),
    Field("融券餘額", "margin", "short", "D", "張", "融券今日餘額", ("short_balance",)),
    Field("券資比", "margin", "short_ratio", "D", "%", "融券餘額 ÷ 融資餘額", ("short_margin_ratio",)),
    Field("月營收", "revenue", "revenue", "M", "千元", "當月營業收入", ("revenue",)),
    Field("營收月增率", "revenue", "mom", "M", "%", "與上月比較", ("revenue_mom",)),
    Field("營收年增率", "revenue", "yoy", "M", "%", "與去年同月比較", ("revenue_yoy",)),
    Field("累計營收年增率", "revenue", "cum_yoy", "M", "%", "今年累計與去年同期比較", ("revenue_cum_yoy",)),
    Field("營業收入", "financials", "revenue", "Q", "千元", "單季營業收入", ("q_revenue",)),
    Field("營業毛利", "financials", "gross", "Q", "千元", "單季營業毛利", ("gross_profit",)),
    Field("營業利益", "financials", "op_income", "Q", "千元", "單季營業利益", ("operating_income",)),
    Field("稅後淨利", "financials", "net_income", "Q", "千元", "單季本期淨利", ("net_income",)),
    Field("每股盈餘", "financials", "eps", "Q", "元", "單季基本 EPS", ("eps",)),
    Field("近四季每股盈餘", "financials", "eps_ttm", "Q", "元", "近四季 EPS 合計", ("eps_ttm",)),
    Field("毛利率", "financials", "gross_margin", "Q", "%", "單季營業毛利 ÷ 營業收入", ("gross_margin",)),
    Field("營業利益率", "financials", "op_margin", "Q", "%", "單季營業利益 ÷ 營業收入", ("operating_margin",)),
    Field("淨利率", "financials", "net_margin", "Q", "%", "單季稅後淨利 ÷ 營業收入", ("net_margin",)),
    Field("ROE", "financials", "roe", "Q", "%", "近四季母公司淨利 ÷ 期末母公司權益", ("roe", "股東權益報酬率")),
    Field("負債比率", "financials", "debt_ratio", "Q", "%", "負債總計 ÷ 資產總計", ("debt_ratio",)),
    Field("股本", "financials", "capital", "Q", "千元", "普通股股本", ("capital",)),
    Field("市值", "derived", "mktcap", "D", "億元", "收盤價 × 股本 ÷ 面額 10 元", ("market_cap",)),
]
BY_NAME: dict[str, Field] = {}
for _f in FIELDS:
    BY_NAME[_f.name] = _f
    for _a in _f.aliases:
        BY_NAME[_a] = _f

_mem: dict[tuple, pd.DataFrame] = {}
_mlock = threading.Lock()
CACHE = store.ROOT / "cache"


def _cache(key: tuple, build):
    ver = "|".join(store.version(t) for t in key[0].split("+"))
    k = (*key, ver)
    with _mlock:
        if k in _mem:
            return _mem[k]
    f = CACHE / ("_".join(map(str, key)).replace("+", "-") + ".pkl")
    df = None
    if f.exists():
        try:
            with open(f, "rb") as fh:
                v, df = pickle.load(fh)
            if v != ver:
                df = None
        except Exception:  # noqa: BLE001
            df = None
    if df is None:
        df = build()
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump((ver, df), fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, f)
    with _mlock:
        for old in [x for x in _mem if x[:-1] == key]:
            _mem.pop(old, None)
        _mem[k] = df
        if len(_mem) > 60:
            _mem.pop(next(iter(_mem)))
    return df


def _wide(table: str, cols: list[str], key: str = "date") -> dict[str, pd.DataFrame]:
    with store.conn() as c:
        df = pd.read_sql_query(f"SELECT code, {key}, {', '.join(cols)} FROM {table}", c)
    out = {}
    if df.empty:
        return {col: pd.DataFrame(dtype="float32") for col in cols}
    df[key] = pd.to_datetime(df[key])
    df = df.drop_duplicates([key, "code"], keep="last").set_index([key, "code"])
    for col in cols:
        out[col] = df[col].astype("float32").unstack("code").sort_index()
    return out


def calendar() -> pd.DatetimeIndex:
    def build():
        with store.conn() as c:
            rows = c.execute("SELECT date, COUNT(*) n FROM prices GROUP BY date").fetchall()
        if not rows:
            return pd.DatetimeIndex([])
        mx = max(n for _, n in rows)
        return pd.DatetimeIndex(sorted(pd.Timestamp(d) for d, n in rows if n >= max(1, mx * 0.2)))
    return _cache(("prices", "calendar"), build)


def _price_all() -> dict[str, pd.DataFrame]:
    """One read of the prices table → every price panel (built once per data version, then from disk)."""
    def build():
        w = _wide("prices", ["open", "high", "low", "close", "adj", "volume"])
        if w["close"].empty:
            return {k: pd.DataFrame(dtype="float32") for k in ("open", "high", "low", "close", "adj", "volume", "value", "adj_open")}
        w["value"] = (w["close"] * w["volume"] / 1e6).astype("float32")
        w["adj_open"] = (w["open"] * w["adj"] / w["close"]).astype("float32")
        w["volume"] = (w["volume"] / 1000).astype("float32")
        return w
    return _cache(("prices", "all"), build)


def _prices(col: str) -> pd.DataFrame:
    return _price_all()[col]


def _daily(table: str, col: str) -> pd.DataFrame:
    def build():
        if col == "short_ratio":
            w = _wide("margin", ["margin", "short"])
            return (w["short"] / w["margin"].replace(0, np.nan) * 100).astype("float32")
        return _wide(table, [col])[col]
    return _cache((table, col), build)


def _revenue(col: str) -> pd.DataFrame:
    def build():
        return _wide("revenue", [col], key="avail")[col]
    return _cache(("revenue", col), build)


def _fin_all() -> dict[str, pd.DataFrame]:
    """Quarterly fields indexed by deadline; income converted from YTD to single quarter."""
    def build():
        with store.conn() as c:
            inc = pd.read_sql_query("SELECT * FROM income_ytd", c)
            bal = pd.read_sql_query("SELECT * FROM balance", c)
        out: dict[str, pd.DataFrame] = {}
        if inc.empty and bal.empty:
            return out
        if not inc.empty:
            inc["year"] = inc["period"].str[:4].astype(int)
            inc["q"] = inc["period"].str[-1].astype(int)
            inc = inc.sort_values(["code", "year", "q"])
            for col in ("revenue", "gross", "op_income", "net_income", "net_parent", "eps"):
                prev = inc.groupby(["code", "year"])[col].shift(1)
                prevq = inc.groupby(["code", "year"])["q"].shift(1)
                single = np.where(inc["q"] == 1, inc[col], np.where(prevq == inc["q"] - 1, inc[col] - prev, np.nan))
                inc[col + "_q"] = single
            inc["avail"] = pd.to_datetime(inc["avail"])
            piv = lambda col: inc.pivot_table(index="avail", columns="code", values=col, aggfunc="last").sort_index()  # noqa: E731
            q = {k: piv(k + "_q") for k in ("revenue", "gross", "op_income", "net_income", "net_parent", "eps")}
            out["revenue"], out["gross"], out["op_income"], out["net_income"], out["eps"] = (
                q["revenue"], q["gross"], q["op_income"], q["net_income"], q["eps"])
            rev = q["revenue"].where(q["revenue"] > 0)
            out["gross_margin"] = q["gross"] / rev * 100
            out["op_margin"] = q["op_income"] / rev * 100
            out["net_margin"] = q["net_income"] / rev * 100
            out["eps_ttm"] = q["eps"].rolling(4, min_periods=4).sum()
            np_ttm = q["net_parent"].fillna(q["net_income"]).rolling(4, min_periods=4).sum()
            out["_np_ttm"] = np_ttm
        if not bal.empty:
            bal["avail"] = pd.to_datetime(bal["avail"])
            pb = lambda col: bal.pivot_table(index="avail", columns="code", values=col, aggfunc="last").sort_index()  # noqa: E731
            assets, liab, eqp, eq = pb("assets"), pb("liabilities"), pb("equity_parent"), pb("equity")
            out["debt_ratio"] = liab / assets.where(assets > 0) * 100
            out["capital"] = pb("capital")
            if "_np_ttm" in out:
                e = eqp.fillna(eq).reindex_like(out["_np_ttm"])
                out["roe"] = out["_np_ttm"] / e.where(e > 0) * 100
        out.pop("_np_ttm", None)
        return {k: v.astype("float32") for k, v in out.items()}
    return _cache(("income_ytd+balance", "fin"), build)


def raw(field: Field) -> pd.DataFrame:
    """Native-frequency frame (index = trade date or publication deadline)."""
    if field.dataset == "prices":
        return _prices(field.col)
    if field.dataset in ("valuation", "insti", "margin"):
        return _daily(field.dataset, field.col)
    if field.dataset == "revenue":
        return _revenue(field.col)
    if field.dataset == "financials":
        return _fin_all().get(field.col, pd.DataFrame(dtype="float32"))
    if field.col == "mktcap":
        cap = to_daily(_fin_all().get("capital", pd.DataFrame(dtype="float32")), "Q")
        close = _prices("close")
        return (close * cap.reindex_like(close) / 1e6).astype("float32")
    raise KeyError(field.name)


FFILL_LIMIT = {"M": 70, "Q": 200, "D": 5}


def to_daily(df: pd.DataFrame, freq: str, cal: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    cal = calendar() if cal is None else cal
    if df.empty:
        return pd.DataFrame(index=cal, dtype="float32")
    if freq == "D":
        return df.reindex(cal).ffill(limit=FFILL_LIMIT["D"])
    idx = df.index.union(cal)
    return df.reindex(idx).ffill(limit=FFILL_LIMIT[freq]).reindex(cal)


def first_valid(field: Field, share: float = 0.2) -> str | None:
    df = raw(field)
    if df.empty:
        return None
    n = df.notna().sum(axis=1)
    ok = n[n >= max(1, n.max() * share)]
    return ok.index[0].date().isoformat() if len(ok) else None


def catalog() -> dict:
    cov = store.coverage()
    tab = {"prices": "prices", "valuation": "valuation", "insti": "insti", "margin": "margin", "revenue": "revenue",
           "financials": "income_ytd"}
    ds = []
    for k, d in DATASETS.items():
        c = cov.get(tab[k], {})
        log = cov.get("_log", {}).get(tab[k], {})
        if k == "financials":
            log = {**log, **{f"balance_{s}": v for s, v in cov.get("_log", {}).get("balance", {}).items()}}
        ds.append({"id": k, **d, "rows": c.get("rows", 0), "codes": c.get("codes", 0), "first": c.get("first"),
                   "last": c.get("last"), "fetch": log,
                   "fields": [{"name": f.name, "unit": f.unit, "desc": f.desc, "freq": f.freq, "alias": f.aliases[0] if f.aliases else ""}
                              for f in FIELDS if f.dataset == k or (k == "prices" and f.dataset == "derived")]})
    total_rows = sum(cov.get(t, {}).get("rows", 0) for t in store.TABLE_COLS)
    return {"datasets": ds, "fields": len(FIELDS), "rows": total_rows, "errors": cov.get("_errors", [])}
