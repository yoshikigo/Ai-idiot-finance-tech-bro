"""Kronos (shiyu-coder/Kronos, MIT) worker — a foundation model for financial candlesticks (K-lines).

Kronos tokenizes OHLCV bars and generates the next bars autoregressively, so it forecasts whole candles
(open / high / low / close / volume), not just a close-price line. We draw N sampled paths in one batch and
report the median path plus the P10–P90 band and P(close higher) from those samples.

  GET  /forecast?symbol=2330.TW&horizon=20&model=small&samples=16   synchronous (chart overlay, Laya)
  POST /jobs                                                       full report: forecast + backtest + Ollama

Models (Hugging Face, NeoQuasar): Kronos-mini 4.1M (context 2048), Kronos-small 24.7M and Kronos-base 102.3M
(context 512). Runs on CPU; weights download on first use into /data/hf.
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

MODELS = {  # name → (model repo, tokenizer repo, max context, label)
    "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048, "Kronos-mini（4.1M）"),
    "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512, "Kronos-small（24.7M）"),
    "base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512, "Kronos-base（102.3M）"),
}
DEFAULT_MODEL = os.environ.get("KRONOS_MODEL", "small")
DEVICE = os.environ.get("KRONOS_DEVICE", "cpu")
MAX_HORIZON = 120
COLS = ["open", "high", "low", "close", "volume", "amount"]

SCHEMA = [
    {"name": "ticker", "label": "標的（股票／ETF／指數／匯率／加密貨幣）", "type": "ticker", "required": True,
     "placeholder": "2330.TW / 0050.TW / ^TWII / NVDA / BTC-USD"},
    {"name": "horizon", "label": "預測天數（交易日）", "type": "int", "default": 20, "min": 5, "max": MAX_HORIZON},
    {"name": "lookback", "label": "使用歷史 K 棒數", "type": "int", "default": 400, "min": 64, "max": 2048},
    {"name": "model", "label": "模型", "type": "select", "default": DEFAULT_MODEL,
     "options": [{"value": k, "label": v[3]} for k, v in MODELS.items()]},
    {"name": "samples", "label": "抽樣路徑數（越多區間越穩，越慢）", "type": "int", "default": 24, "min": 4, "max": 64},
    {"name": "backtest", "label": "回測（用前一段預測最近一段，看準不準）", "type": "bool", "default": True},
    {"name": "language", "label": "解讀語言", "type": "select", "default": "Traditional Chinese",
     "options": [{"value": "Traditional Chinese", "label": "繁體中文"}, {"value": "English", "label": "English"}]},
    {"name": "ft_context", "label": "加入 FT 市場背景（台股中文新聞、籌碼、月營收、產業強弱、台灣／美國總經、重大事件與日程）", "type": "bool", "default": True},
]

_lock = threading.Lock()
_infer = threading.Lock()
_predictors: dict = {}


# ---------------------------------------------------------------- model
def load(name: str):
    """KronosPredictor for a model name (cached)."""
    name = name if name in MODELS else "small"
    with _lock:
        if name in _predictors:
            return _predictors[name]
        import torch
        torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))
        from model import Kronos, KronosPredictor, KronosTokenizer  # the upstream package (PYTHONPATH=/opt/Kronos)
        repo, tok_repo, ctx_len, _ = MODELS[name]
        t0 = time.time()
        if os.environ.get("KRONOS_RANDOM_INIT") == "1":  # tests only: real code path, tiny random weights
            tok = KronosTokenizer(d_in=6, d_model=32, n_heads=2, ff_dim=64, n_enc_layers=1, n_dec_layers=1, ffn_dropout_p=0,
                                  attn_dropout_p=0, resid_dropout_p=0, s1_bits=4, s2_bits=4, beta=0.05, gamma0=1.0, gamma=1.1,
                                  zeta=0.05, group_size=4)
            mdl = Kronos(s1_bits=4, s2_bits=4, n_layers=1, d_model=32, n_heads=2, ff_dim=64, ffn_dropout_p=0, attn_dropout_p=0,
                         resid_dropout_p=0, token_dropout_p=0, learn_te=True)
        else:
            tok = KronosTokenizer.from_pretrained(tok_repo)
            mdl = Kronos.from_pretrained(repo)
        tok.eval()
        mdl.eval()
        pred = KronosPredictor(mdl, tok, device=DEVICE, max_context=ctx_len)
        print(f"Kronos {name} loaded in {time.time() - t0:.1f}s", flush=True)
        _predictors[name] = pred
        return pred


def sample_paths(pred, df: pd.DataFrame, x_ts: pd.Series, y_ts: pd.Series, h: int, n: int,
                 T: float = 1.0, top_p: float = 0.9, seed: int = 0) -> np.ndarray:
    """n sampled future paths → array [n, h, 6] (open, high, low, close, volume, amount).
    One batched call: the same history repeated n times, each sampled once."""
    import torch
    torch.manual_seed(seed)
    np.random.seed(seed)
    with _infer:
        outs = pred.predict_batch([df] * n, [x_ts] * n, [y_ts] * n, pred_len=h, T=T, top_p=top_p, sample_count=1, verbose=False)
    return np.stack([o[COLS].values for o in outs])


# ---------------------------------------------------------------- data
def ohlcv(sym: str, lookback: int) -> tuple[pd.DataFrame, str, str]:
    """Daily OHLCV with Kronos column names, display name, future-date frequency."""
    years = max(2, math.ceil(lookback / 240) + 1)
    df = market.history(sym, start=(dt.date.today() - dt.timedelta(days=int(365.25 * years))).isoformat())
    if df is None or len(df) == 0:
        raise ValueError(f"{sym} 沒有歷史資料")
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")]
    df["close"] = df["close"].astype(float)
    for c in ("open", "high", "low"):
        df[c] = df[c].astype(float).fillna(df["close"])
    df["volume"] = df["volume"].astype(float).fillna(0.0)
    df = df.dropna(subset=["close"])
    df["amount"] = df["volume"] * df[["open", "high", "low", "close"]].mean(axis=1)
    name = twmarket.zh_name(sym) or (sym if market.FAKE else (market.info(sym).get("shortName") or sym))
    return df.tail(lookback), name, ("D" if sym.endswith("-USD") else "B")


def future_index(last: pd.Timestamp, h: int, freq: str) -> pd.DatetimeIndex:
    return pd.date_range(last + pd.Timedelta(days=1), periods=h, freq=freq)


def _band(paths: np.ndarray) -> dict:
    close = paths[:, :, 3]
    q = np.percentile(close, [10, 25, 50, 75, 90], axis=0)
    med = np.median(paths, axis=0)  # candle path: per-step median of each column, then made a valid bar
    o, c = med[:, 0], med[:, 3]
    hi = np.maximum(med[:, 1], np.maximum(o, c))
    lo = np.minimum(med[:, 2], np.minimum(o, c))
    return {"q": q, "open": o, "high": hi, "low": lo, "close": q[2], "volume": med[:, 4]}


def forecast(sym: str, horizon: int = 20, model: str = DEFAULT_MODEL, lookback: int = 400, samples: int = 24,
             backtest: bool = False) -> dict:
    horizon = max(1, min(int(horizon), MAX_HORIZON))
    model = model if model in MODELS else "small"
    ctx_len = MODELS[model][2]
    lookback = max(64, min(int(lookback), ctx_len))
    samples = max(2, min(int(samples), 64))
    df, name, freq = ohlcv(sym, lookback + (horizon if backtest else 0))
    if len(df) < 64:
        raise ValueError(f"{sym} 歷史資料只有 {len(df)} 筆，至少要 64 筆")
    pred = load(model)
    hist = df.tail(lookback)
    fut = future_index(hist.index[-1], horizon, freq)
    paths = sample_paths(pred, hist[COLS], pd.Series(hist.index), pd.Series(fut), horizon, samples)
    b = _band(paths)
    last = float(hist["close"].iloc[-1])
    end_close = paths[:, -1, 3]
    out = {
        "symbol": sym, "name": name, "model": MODELS[model][3], "model_key": model, "horizon": horizon,
        "context": len(hist), "samples": samples, "last_date": hist.index[-1].strftime("%Y-%m-%d"), "last": last,
        "history": [[t.strftime("%Y-%m-%d"), round(float(r.open), 6), round(float(r.high), 6), round(float(r.low), 6),
                     round(float(r.close), 6)] for t, r in hist.tail(160).iterrows()],
        "forecast": [{"date": d.strftime("%Y-%m-%d"), "open": float(b["open"][i]), "high": float(b["high"][i]),
                      "low": float(b["low"][i]), "close": float(b["close"][i]), "median": float(b["q"][2][i]),
                      "p10": float(b["q"][0][i]), "p25": float(b["q"][1][i]), "p75": float(b["q"][3][i]),
                      "p90": float(b["q"][4][i]), "volume": float(b["volume"][i])} for i, d in enumerate(fut)],
    }
    e = out["forecast"][-1]
    out["summary"] = {"median_end": e["median"], "change_pct": (e["median"] / last - 1) * 100, "p10_end": e["p10"],
                      "p90_end": e["p90"], "p_up": round(float(np.mean(end_close > last)), 3),
                      "band_pct": (e["p90"] - e["p10"]) / last * 100,
                      "path_high_pct": (float(np.max(b["high"])) / last - 1) * 100,
                      "path_low_pct": (float(np.min(b["low"])) / last - 1) * 100}
    if backtest and len(df) >= lookback + horizon:
        past = df.iloc[-(lookback + horizon):-horizon]
        actual = df.iloc[-horizon:]
        bp = sample_paths(pred, past[COLS], pd.Series(past.index), pd.Series(actual.index), horizon, samples, seed=1)
        bq = np.percentile(bp[:, :, 3], [10, 50, 90], axis=0)
        a = actual["close"].values
        start = float(past["close"].iloc[-1])
        out["backtest"] = {
            "from": past.index[-1].strftime("%Y-%m-%d"), "mape": float(np.mean(np.abs(bq[1] - a) / np.abs(a)) * 100),
            "naive_mape": float(np.mean(np.abs(start - a) / np.abs(a)) * 100),
            "coverage80": float(np.mean((a >= bq[0]) & (a <= bq[2])) * 100),
            "direction_hit": bool(np.sign(bq[1][-1] - start) == np.sign(a[-1] - start)),
            "points": [{"date": t.strftime("%Y-%m-%d"), "actual": float(a[i]), "median": float(bq[1][i]),
                        "p10": float(bq[0][i]), "p90": float(bq[2][i])} for i, t in enumerate(actual.index)]}
    return out


_cache: dict = {}


def forecast_cached(sym, horizon, model, lookback, samples, backtest=False):
    key = (sym, int(horizon), model, int(lookback), int(samples), bool(backtest), int(time.time() // 900))
    if key not in _cache:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = forecast(sym, horizon, model, lookback, samples, backtest)
    return _cache[key]


# ---------------------------------------------------------------- job (report)
def run(ctx: Ctx) -> dict:
    p = ctx.params
    sym = split_tickers(p["ticker"])[0]
    model = p.get("model") or DEFAULT_MODEL
    horizon = int(p.get("horizon") or 20)
    samples = int(p.get("samples") or 24)
    ctx.step(10, f"讀取 {sym} 日 K 線")
    ctx.step(25, f"載入 {MODELS.get(model, MODELS['small'])[3]}（第一次會從 Hugging Face 下載權重）")
    load(model)
    ctx.step(45, f"抽樣 {samples} 條未來 {horizon} 根 K 棒的路徑" + ("＋回測" if p.get("backtest", True) else ""))
    r = forecast(sym, horizon, model, int(p.get("lookback") or 400), samples, bool(p.get("backtest", True)))
    sm, bt = r["summary"], r.get("backtest")
    fmt = lambda v: f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}"  # noqa: E731

    ctx.step(80, "Ollama 解讀")
    llm = ollama.resolve(ctx.llm, "kronos")
    facts = (f"標的 {sym} {r['name']}，最新 {r['last_date']} 收盤 {fmt(r['last'])}。{r['model']} 以 {r['context']} 根日 K 棒，"
             f"抽樣 {r['samples']} 條路徑預測 {horizon} 個交易日後：收盤中位數 {fmt(sm['median_end'])}（{sm['change_pct']:+.2f}%），"
             f"80% 區間 {fmt(sm['p10_end'])} ~ {fmt(sm['p90_end'])}（寬度 {sm['band_pct']:.1f}%），"
             f"{sm['p_up'] * 100:.0f}% 的路徑收高於現價；中位路徑期間最高 {sm['path_high_pct']:+.1f}%、最低 {sm['path_low_pct']:+.1f}%。")
    if bt:
        facts += (f"\n回測（以 {bt['from']} 前的 K 棒預測之後 {horizon} 日）：MAPE {bt['mape']:.2f}%（價格不變基準 {bt['naive_mape']:.2f}%），"
                  f"實際落在 80% 區間 {bt['coverage80']:.0f}%，方向{'正確' if bt['direction_hit'] else '錯誤'}。")
    if llm.get("context"):
        facts += "\n\n" + llm["context"]
        ctx.log(f"帶入 FT 市場背景：{llm.get('context_parts') or ''}")
    commentary = ollama.ask(llm, "You explain candlestick (K-line) foundation-model forecasts to a retail investor. Kronos is "
                                 "pre-trained on OHLCV bars from 45+ exchanges and generates future bars by sampling; it sees only "
                                 "past prices and volume — no news, fundamentals or events. Explain the median path, the spread of "
                                 "the sampled paths (uncertainty), what the backtest says about reliability versus the naive "
                                 "'price unchanged' baseline, and the limitations. If market context is given, say which facts "
                                 "could break the statistical forecast — only from the facts given, never invent. No trading "
                                 "advice. Concise markdown, 4–8 bullet points.", facts, language=p.get("language"))

    hist = r["history"]
    fc = r["forecast"]
    charts = [{"title": f"{sym} {r['name']} · {r['model']} 預測（{horizon} 日，{r['samples']} 條路徑的 80% 區間）", "type": "line", "series": [
        {"name": "歷史收盤", "data": [[x[0], x[4]] for x in hist], "color": "#8a939c"},
        {"name": "預測中位數", "data": [[hist[-1][0], r["last"]]] + [[x["date"], x["median"]] for x in fc], "color": "#c084fc", "style": "dashed"},
        {"name": "P90", "data": [[x["date"], x["p90"]] for x in fc], "color": "#4ba3ff", "style": "dotted"},
        {"name": "P10", "data": [[x["date"], x["p10"]] for x in fc], "color": "#4ba3ff", "style": "dotted"},
    ]}]
    tables = [table("預測摘要", ["項目", "數值"], [
        ["最新收盤", f"{fmt(r['last'])}（{r['last_date']}）"],
        [f"{horizon} 日後收盤中位數", f"{fmt(sm['median_end'])}（{sm['change_pct']:+.2f}%）"],
        ["80% 區間", f"{fmt(sm['p10_end'])} ~ {fmt(sm['p90_end'])}"], ["收高於現價的路徑比例", f"{sm['p_up'] * 100:.0f}%"],
        ["中位路徑最高／最低", f"{sm['path_high_pct']:+.1f}% ／ {sm['path_low_pct']:+.1f}%"],
        ["模型／K 棒數／路徑數", f"{r['model']} / {r['context']} / {r['samples']}"]])]
    if bt:
        charts.append({"title": f"回測：{bt['from']} 之後實際 vs 預測", "type": "line", "series": [
            {"name": "實際", "data": [[x["date"], x["actual"]] for x in bt["points"]], "color": "#e6e6e6"},
            {"name": "預測中位數", "data": [[x["date"], x["median"]] for x in bt["points"]], "color": "#c084fc", "style": "dashed"},
            {"name": "P90", "data": [[x["date"], x["p90"]] for x in bt["points"]], "color": "#4ba3ff", "style": "dotted"},
            {"name": "P10", "data": [[x["date"], x["p10"]] for x in bt["points"]], "color": "#4ba3ff", "style": "dotted"}]})
        tables.append(table("回測", ["指標", "Kronos", "基準（價格不變）"], [
            ["MAPE", f"{bt['mape']:.2f}%", f"{bt['naive_mape']:.2f}%"],
            ["實際落在 80% 區間", f"{bt['coverage80']:.0f}%", "—"], ["方向", "正確" if bt["direction_hit"] else "錯誤", "—"]]))
    tables.append(table("逐日預測 K 棒（中位路徑）", ["日期", "開", "高", "低", "收", "P10", "P90"],
                        [[x["date"], fmt(x["open"]), fmt(x["high"]), fmt(x["low"]), fmt(x["close"]), fmt(x["p10"]), fmt(x["p90"])]
                         for x in fc]))
    sig = "看多" if sm["change_pct"] > 1 and sm["p_up"] > 0.55 else "看空" if sm["change_pct"] < -1 and sm["p_up"] < 0.45 else "中性"
    md = (f"# {r['model']} · {sym} {r['name']}\n\n{horizon} 個交易日後收盤中位數 **{fmt(sm['median_end'])}（{sm['change_pct']:+.2f}%）**，"
          f"80% 區間 {fmt(sm['p10_end'])} ~ {fmt(sm['p90_end'])}，{sm['p_up'] * 100:.0f}% 的路徑收高"
          + (f"；回測 MAPE {bt['mape']:.2f}%（基準 {bt['naive_mape']:.2f}%）" if bt else "") + f"\n\n## 🤖 解讀\n\n{commentary}\n\n"
          "> Kronos 只看過去的 K 線（開高低收量），不知道新聞、財報與事件；區間來自抽樣路徑的分散程度，不是保證。\n")
    if llm.get("context"):
        md += "\n<details><summary>解讀時帶入的 FT 市場背景</summary>\n\n```\n" + llm["context"] + "\n```\n</details>\n"
    return {"summary": f"{sym} {horizon}日 {sm['change_pct']:+.1f}%（{sm['p_up'] * 100:.0f}% 路徑收高）", "signal": sig,
            "markdown": md, "charts": charts, "tables": tables, "last": r["last"],
            "forecast_summary": {**sm, "horizon_end": fc[-1]["date"], "last": r["last"], "last_date": r["last_date"]}}


engine = Engine("kronos", "Kronos K 線預測",
                "shiyu-coder/Kronos：金融 K 線基礎模型（45+ 交易所資料預訓練），抽樣產生未來的開高低收量，給出中位路徑、80% 區間與收高比例，附回測與本機 Ollama 解讀。CPU 執行。",
                SCHEMA, run, repo="https://github.com/shiyu-coder/Kronos", version=os.environ.get("UPSTREAM_REF", ""))
app = engine.app


@app.get("/forecast")
def forecast_api(symbol: str, horizon: int = 20, model: str = DEFAULT_MODEL, lookback: int = 400, samples: int = 16,
                 backtest: bool = False):
    try:
        return jsonable(forecast_cached(symbol, horizon, model, lookback, samples, backtest))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.on_event("startup")
def _preload():
    if os.environ.get("KRONOS_PRELOAD", "1") == "1":
        threading.Thread(target=_safe_load, daemon=True).start()


def _safe_load():
    try:
        load(DEFAULT_MODEL)
    except Exception as e:  # noqa: BLE001
        print("Kronos preload failed:", e, flush=True)


if __name__ == "__main__":
    serve(engine, 8139)
