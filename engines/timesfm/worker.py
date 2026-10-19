"""TimesFM (google-research/timesfm) worker — zero-shot price / NAV trend forecasts with quantile bands.

Two ways in:
  GET  /forecast?symbol=2330.TW&horizon=30&model=2.5   fast, synchronous (chart overlay in the terminal)
  POST /jobs                                           full report: forecast + backtest + Ollama commentary
Models: TimesFM 2.5 (200M, Apache-2.0 weights, default) or TimesFM 3.0 (330M, weights under
timesfm-non-commercial-license-v1.0 — personal / non-commercial use only). Runs on CPU by default.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import threading
import time

import numpy as np
import pandas as pd
from fastapi import HTTPException

from ft_common import market, ollama, twmarket
from ft_common.engine import Ctx, Engine, jsonable, serve, split_tickers, table

MODELS = {
    "2.5": os.environ.get("TIMESFM_WEIGHTS_25", "google/timesfm-2.5-200m-pytorch"),
    "3.0": os.environ.get("TIMESFM_WEIGHTS_30", "google/timesfm-3.0-pytorch"),
}
DEVICE = os.environ.get("TIMESFM_DEVICE", "cpu")
MAX_CONTEXT = int(os.environ.get("TIMESFM_MAX_CONTEXT", "2048"))
MAX_HORIZON = 256
FUND = "FUND:"

SCHEMA = [
    {"name": "ticker", "label": "標的（股票／ETF／指數／匯率／基金 FUND:id）", "type": "ticker", "required": True,
     "placeholder": "2330.TW / 0056.TW / ^TWII / FUND:D:12345678"},
    {"name": "horizon", "label": "預測天數（交易日）", "type": "int", "default": 30, "min": 5, "max": MAX_HORIZON},
    {"name": "context", "label": "使用歷史天數", "type": "int", "default": 1024, "min": 128, "max": MAX_CONTEXT},
    {"name": "model", "label": "模型", "type": "select", "default": "2.5",
     "options": [{"value": "2.5", "label": "TimesFM 2.5（200M，Apache-2.0）"},
                 {"value": "3.0", "label": "TimesFM 3.0（330M，權重限非商業用途）"}]},
    {"name": "backtest", "label": "回測（用前一段預測最近一段，看準不準）", "type": "bool", "default": True},
    {"name": "world_covariates", "label": "用世界事件指標當共變量（只限 TimesFM 3.0，需累積 ≥64 個交易日）", "type": "bool", "default": False},
    {"name": "language", "label": "解讀語言", "type": "select", "default": "Traditional Chinese",
     "options": [{"value": "Traditional Chinese", "label": "繁體中文"}, {"value": "English", "label": "English"}]},
]

_lock = threading.Lock()
_infer = threading.Lock()  # one forward pass at a time (shared model object)
_models: dict = {}


# ---------------------------------------------------------------- model
def load(ver: str):
    """Returns f(series: np.ndarray, horizon) -> (median[h], deciles[h, 9]). Cached per version."""
    ver = ver if ver in MODELS else "2.5"
    with _lock:
        if ver in _models:
            return _models[ver]
        import torch
        torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))
        t0 = time.time()
        if ver == "2.5":
            import timesfm
            if os.environ.get("TIMESFM_RANDOM_INIT") == "1":  # tests only: real code path, random weights
                m = timesfm.TimesFM_2p5_200M_torch()
            else:
                m = timesfm.TimesFM_2p5_200M_torch.from_pretrained(MODELS["2.5"])
            m.compile(timesfm.ForecastConfig(
                max_context=MAX_CONTEXT, max_horizon=MAX_HORIZON, normalize_inputs=True,
                use_continuous_quantile_head=True, force_flip_invariance=True, infer_is_positive=True,
                fix_quantile_crossing=True))

            def f(series, h, cov=None):  # 2.5 covariates need XReg (JAX); not used here
                point, quant = m.forecast(horizon=h, inputs=[series.astype(np.float32)])
                q = np.asarray(quant[0])[:, 1:10]  # [:,0] is the mean; 1..9 = P10..P90
                return np.asarray(q[:, 4]), q
        else:
            from timesfm3.torch import timesfm3_forecaster as f3
            fc = f3.TimesFM3Forecaster(f3._ModelConfig(checkpoint_path=MODELS["3.0"], device=DEVICE))

            def f(series, h, cov=None):
                o = fc.predict(series.astype(np.float32), horizon=h, return_quantiles=True, make_positive=True,
                               past_only_covariates=None if cov is None else cov.astype(np.float32))
                return np.asarray(o.forecast), np.asarray(o.quantiles)
        print(f"TimesFM {ver} loaded in {time.time() - t0:.1f}s", flush=True)
        _models[ver] = f
        return f


# ---------------------------------------------------------------- data
def series_for(sym: str, context: int) -> tuple[pd.Series, str, str]:
    """Daily close (or fund NAV) series, display name, frequency label."""
    if sym.startswith(FUND):
        from ft_common import funds
        fid = sym[len(FUND):]
        f = funds.get(fid)
        if not f:
            raise ValueError(f"查無基金 {fid}")
        pts = funds.history(fid)["points"]
        s = pd.Series([p[1] for p in pts], index=pd.to_datetime([p[0] for p in pts]))
        return s.tail(context), f["name"], "B"
    years = max(2, math.ceil(context / 240) + 1)
    df = market.history(sym, start=(dt.date.today() - dt.timedelta(days=int(365.25 * years))).isoformat())
    if df is None or len(df) == 0:
        raise ValueError(f"{sym} 沒有歷史資料")
    s = df["Close"].dropna()
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    s = s[~s.index.duplicated(keep="last")]
    name = twmarket.zh_name(sym) or market.info(sym).get("shortName") or sym if not market.FAKE else (twmarket.zh_name(sym) or sym)
    freq = "D" if sym.endswith("-USD") else "B"
    return s.tail(context), name, freq


def future_dates(last: pd.Timestamp, h: int, freq: str) -> list[str]:
    rng = pd.date_range(last + pd.Timedelta(days=1), periods=h, freq=freq)
    return [d.strftime("%Y-%m-%d") for d in rng]


def world_covariates(sym: str, idx: pd.DatetimeIndex, max_cov: int = 6):
    """Past-only covariates from God's Eye View daily indicators, aligned to the price index.
    Returns (matrix[k, n] or None, start_date or None, info text)."""
    from ft_common import world
    cols, names = [], []
    for rid in world.regions_for(sym):
        for ck in world.REGIONS[rid]["checks"]:
            if ck == "launches":
                continue
            ser = world.series(rid, ck)
            if len(ser) < 5:
                continue
            x = pd.Series(ser)
            x.index = pd.to_datetime(x.index)
            cols.append(x)
            names.append(f"{world.REGIONS[rid]['name']}·{world.CHECKS[ck][0]}")
    if not cols:
        return None, None, "還沒有世界事件歷史（收集器每小時一次，要累積幾個月才有用）"
    start = max(c.index.min() for c in cols)
    window = idx[idx >= start]
    if len(window) < 64:
        return None, None, f"世界事件指標只累積了 {len(window)} 個交易日（從 {start:%Y-%m-%d}），至少要 64 個；這次不用共變量"
    mat = []
    for c in cols[:max_cov]:
        a = c.reindex(c.index.union(window)).ffill().reindex(window).bfill().astype(float)
        sd = a.std() or 1.0
        mat.append(((a - a.mean()) / sd).values)
    return np.vstack(mat), window[0], f"使用 {len(mat)} 個世界指標共變量（{'、'.join(names[:max_cov])}），{len(window)} 個交易日"


def forecast(sym: str, horizon: int = 30, model: str = "2.5", context: int = 1024, backtest: bool = False,
             use_world: bool = False) -> dict:
    horizon = max(1, min(int(horizon), MAX_HORIZON))
    context = max(64, min(int(context), MAX_CONTEXT))
    s, name, freq = series_for(sym, context)
    if len(s) < 64:
        raise ValueError(f"{sym} 歷史資料只有 {len(s)} 筆，至少要 64 筆")
    f = load(model)
    cov, cov_info = None, None
    if use_world:
        if model != "3.0":
            cov_info = "共變量只支援 TimesFM 3.0；這次沒用"
        else:
            cov, start, cov_info = world_covariates(sym, s.index)
            if cov is not None:
                s = s[s.index >= start]
    with _infer:
        med, q = f(s.values, horizon, cov)
    dates = future_dates(s.index[-1], horizon, freq)
    last = float(s.iloc[-1])
    out = {
        "symbol": sym, "name": name, "model": f"TimesFM {model}", "horizon": horizon, "context": len(s),
        "last_date": s.index[-1].strftime("%Y-%m-%d"), "last": last,
        "history": [[d.strftime("%Y-%m-%d"), round(float(v), 6)] for d, v in s.tail(260).items()],
        "covariates": cov_info,
        "forecast": [{"date": d, "median": float(med[i]), "p10": float(q[i, 0]), "p25": float(np.interp(0.25, np.linspace(.1, .9, 9), q[i])),
                      "p75": float(np.interp(0.75, np.linspace(.1, .9, 9), q[i])), "p90": float(q[i, 8])}
                     for i, d in enumerate(dates)],
    }
    end = out["forecast"][-1]
    # probability the price ends above today's level, from the decile curve
    deciles = q[-1]
    p_up = 1 - float(np.interp(last, deciles, np.linspace(.1, .9, 9), left=0.05, right=0.95)) if deciles[0] != deciles[-1] else 0.5
    out["summary"] = {"median_end": end["median"], "change_pct": (end["median"] / last - 1) * 100,
                      "p10_end": end["p10"], "p90_end": end["p90"], "p_up": round(p_up, 3),
                      "band_pct": (end["p90"] - end["p10"]) / last * 100}
    if backtest and len(s) > horizon + 64:
        hist = s.iloc[:-horizon]
        actual = s.iloc[-horizon:]
        with _infer:
            bm, bq = f(hist.values, horizon, None if cov is None else cov[:, :-horizon])
        err = np.abs(bm - actual.values) / np.abs(actual.values)
        inside = np.mean((actual.values >= bq[:, 0]) & (actual.values <= bq[:, 8]))
        dir_pred = np.sign(bm[-1] - hist.iloc[-1])
        dir_act = np.sign(actual.iloc[-1] - hist.iloc[-1])
        naive = np.abs(hist.iloc[-1] - actual.values) / np.abs(actual.values)  # "tomorrow = today"
        out["backtest"] = {"from": hist.index[-1].strftime("%Y-%m-%d"), "mape": float(err.mean() * 100),
                           "naive_mape": float(naive.mean() * 100), "coverage80": float(inside * 100),
                           "direction_hit": bool(dir_pred == dir_act),
                           "points": [{"date": d.strftime("%Y-%m-%d"), "actual": float(a), "median": float(m_),
                                       "p10": float(bq[i, 0]), "p90": float(bq[i, 8])}
                                      for i, (d, a, m_) in enumerate(zip(actual.index, actual.values, bm))]}
    return out


_cache: dict = {}


def forecast_cached(sym, horizon, model, context, backtest=False):
    key = (sym, int(horizon), model, int(context), bool(backtest), dt.date.today().isoformat(), int(time.time() // 900))
    if key not in _cache:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = forecast(sym, horizon, model, context, backtest)
    return _cache[key]


# ---------------------------------------------------------------- job (report)
def run(ctx: Ctx) -> dict:
    p = ctx.params
    t = str(p["ticker"]).strip()
    sym = t if t.upper().startswith(FUND) else split_tickers(t)[0]
    if sym.upper().startswith(FUND):
        sym = FUND + sym[len(FUND):]
    model = p.get("model") or "2.5"
    horizon = int(p.get("horizon") or 30)
    ctx.step(10, f"讀取 {sym} 歷史資料")
    ctx.step(30, f"載入 TimesFM {model}（第一次會從 Hugging Face 下載權重）")
    load(model)
    ctx.step(55, f"預測未來 {horizon} 個交易日" + ("＋回測" if p.get("backtest", True) else ""))
    r = forecast(sym, horizon, model, int(p.get("context") or 1024), bool(p.get("backtest", True)),
                 bool(p.get("world_covariates")))
    sm, bt = r["summary"], r.get("backtest")
    if r.get("covariates"):
        ctx.log(f"共變量：{r['covariates']}")

    ctx.step(75, "讀取預測期間要注意的現實世界訊號（God's Eye View）")
    world_info = None
    try:
        from ft_common import world
        regions = world.regions_for(sym)
        bt_from = (bt or {}).get("from")
        world_info = {"regions": regions, "digest": world.digest(regions), "compact": world.compact(regions),
                      "events": world.events(regions, days=30),
                      "backtest_events": world.events(regions, since=bt_from, as_of=r["last_date"]) if bt_from else []}
    except Exception as e:  # noqa: BLE001
        ctx.log(f"世界訊號無法讀取，略過：{e}")
    fmt = lambda v: f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}"  # noqa: E731

    ctx.step(85, "Ollama 解讀")
    llm = ollama.resolve(ctx.llm, "timesfm")
    facts = (f"標的 {sym} {r['name']}，最新 {r['last_date']} 收盤 {fmt(r['last'])}。TimesFM {model} 以 {r['context']} 筆歷史預測 {horizon} 個交易日後："
             f"中位數 {fmt(sm['median_end'])}（{sm['change_pct']:+.2f}%），80% 區間 {fmt(sm['p10_end'])} ~ {fmt(sm['p90_end'])}"
             f"（寬度 {sm['band_pct']:.1f}%），收高於現價機率約 {sm['p_up'] * 100:.0f}%。")
    if bt:
        facts += (f"\n回測（以 {bt['from']} 前的資料預測之後 {horizon} 日）：MAPE {bt['mape']:.2f}%（單純用最後價格 {bt['naive_mape']:.2f}%），"
                  f"實際落在 80% 區間的比例 {bt['coverage80']:.0f}%，方向{'正確' if bt['direction_hit'] else '錯誤'}。")
    if r.get("covariates"):
        facts += f"\n共變量：{r['covariates']}。"
    if world_info:
        facts += "\n\n" + world_info["compact"]
        if world_info["backtest_events"]:
            facts += "\n回測期間發生的事件：" + "；".join(f"{e['date']} {e['title']}" for e in world_info["backtest_events"][:6])
    commentary = ollama.ask(llm, "You explain statistical time-series forecasts to a retail investor. TimesFM is a "
                                 "zero-shot foundation model that only sees past prices — no news, fundamentals or events "
                                 "(unless world covariates are stated as used). "
                                 "Explain the median path and the uncertainty band, what the backtest says about reliability "
                                 "(compare with the naive baseline), and clearly state the limitations. If real-world sensor "
                                 "signals (God's Eye View) are given, say which of them could break the statistical forecast "
                                 "(e.g. a big earthquake near fabs, unusual military activity, shipping disruption) — only "
                                 "from the facts given, never invent events. Do not give trading advice. Concise markdown, "
                                 "4–8 bullet points.", facts, language=p.get("language"))

    hist = r["history"][-160:]
    fc = r["forecast"]
    charts = [{"title": f"{sym} {r['name']} · TimesFM {model} 預測（{horizon} 日，80% 區間）", "type": "line", "series": [
        {"name": "歷史", "data": hist, "color": "#8a939c"},
        {"name": "預測中位數", "data": [[hist[-1][0], r["last"]]] + [[x["date"], x["median"]] for x in fc], "color": "#ffa028", "style": "dashed"},
        {"name": "P90", "data": [[x["date"], x["p90"]] for x in fc], "color": "#4ba3ff", "style": "dotted"},
        {"name": "P10", "data": [[x["date"], x["p10"]] for x in fc], "color": "#4ba3ff", "style": "dotted"},
    ]}]
    tables = [table("預測摘要", ["項目", "數值"], [
        ["最新收盤", f"{fmt(r['last'])}（{r['last_date']}）"], [f"{horizon} 日後中位數", f"{fmt(sm['median_end'])}（{sm['change_pct']:+.2f}%）"],
        ["80% 區間", f"{fmt(sm['p10_end'])} ~ {fmt(sm['p90_end'])}"], ["收高於現價機率", f"{sm['p_up'] * 100:.0f}%"],
        ["模型／歷史筆數", f"TimesFM {model} / {r['context']}"]])]
    if bt:
        charts.append({"title": f"回測：{bt['from']} 之後實際 vs 預測", "type": "line", "series": [
            {"name": "實際", "data": [[x["date"], x["actual"]] for x in bt["points"]], "color": "#e6e6e6"},
            {"name": "預測中位數", "data": [[x["date"], x["median"]] for x in bt["points"]], "color": "#ffa028", "style": "dashed"},
            {"name": "P90", "data": [[x["date"], x["p90"]] for x in bt["points"]], "color": "#4ba3ff", "style": "dotted"},
            {"name": "P10", "data": [[x["date"], x["p10"]] for x in bt["points"]], "color": "#4ba3ff", "style": "dotted"}]})
        tables.append(table("回測", ["指標", "TimesFM", "基準（價格不變）"], [
            ["MAPE", f"{bt['mape']:.2f}%", f"{bt['naive_mape']:.2f}%"],
            ["實際落在 80% 區間", f"{bt['coverage80']:.0f}%", "—"], ["方向", "正確" if bt["direction_hit"] else "錯誤", "—"]]))
    tables.append(table("逐日預測", ["日期", "P10", "中位數", "P90"],
                        [[x["date"], fmt(x["p10"]), fmt(x["median"]), fmt(x["p90"])] for x in fc]))
    sig = "看多" if sm["change_pct"] > 1 and sm["p_up"] > 0.55 else "看空" if sm["change_pct"] < -1 and sm["p_up"] < 0.45 else "中性"
    md = (f"# TimesFM {model} · {sym} {r['name']}\n\n{horizon} 個交易日後中位數 **{fmt(sm['median_end'])}（{sm['change_pct']:+.2f}%）**，"
          f"80% 區間 {fmt(sm['p10_end'])} ~ {fmt(sm['p90_end'])}，收高於現價機率約 {sm['p_up'] * 100:.0f}%"
          + (f"；回測 MAPE {bt['mape']:.2f}%（基準 {bt['naive_mape']:.2f}%）" if bt else "") + f"\n\n## 🤖 解讀\n\n{commentary}\n\n"
          "> TimesFM 只看過去的價格序列，不知道新聞、財報與事件；區間代表統計上的不確定性，不是保證。\n")
    if r.get("covariates"):
        md += f"\n**共變量**：{r['covariates']}\n"
    if world_info:
        evs = world_info["events"]
        md += "\n## 🌐 現實世界訊號（God's Eye View）\n\n"
        md += ("近 30 天相關區域的事件：\n" + "\n".join(f"- {e['date']} {e['title']}" for e in evs[:10]) + "\n"
               if evs else "近 30 天相關區域沒有超過門檻的事件。\n")
        md += "\n<details><summary>完整世界訊號</summary>\n\n" + world_info["digest"] + "\n</details>\n"
    fc_end = fc[-1]["date"]
    return {"summary": f"{sym} {horizon}日 {sm['change_pct']:+.1f}%（{sm['p_up'] * 100:.0f}% 收高）", "signal": sig,
            "markdown": md, "charts": charts, "tables": tables, "world": world_info, "last": r["last"],
            "forecast_summary": {**sm, "horizon_end": fc_end, "last": r["last"], "last_date": r["last_date"]}}


engine = Engine("timesfm", "TimesFM 趨勢預測",
                "google-research/timesfm：Google 的時間序列基礎模型，零樣本預測股價／ETF／基金淨值的未來走勢與 80% 區間，附回測與本機 Ollama 解讀。",
                SCHEMA, run, repo="https://github.com/google-research/timesfm", version=os.environ.get("UPSTREAM_REF", ""))
app = engine.app


@app.get("/forecast")
def forecast_api(symbol: str, horizon: int = 30, model: str = "2.5", context: int = 1024, backtest: bool = False):
    try:
        return jsonable(forecast_cached(symbol, horizon, model, context, backtest))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.on_event("startup")
def _preload():
    if os.environ.get("TIMESFM_PRELOAD", "1") == "1":
        threading.Thread(target=lambda: _safe_load("2.5"), daemon=True).start()


def _safe_load(v):
    try:
        load(v)
    except Exception as e:  # noqa: BLE001
        print("TimesFM preload failed:", e, flush=True)


if __name__ == "__main__":
    serve(engine, 8136)
