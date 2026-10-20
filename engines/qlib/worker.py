"""Microsoft Qlib worker — Alpha158 factors + LightGBM/Ridge, TopK-Dropout backtest.

Data: yfinance daily OHLCV → Qlib .bin format (own dumper, same layout as
scripts/dump_bin.py). Qlib itself has no LLM; the local Ollama model writes
the commentary on the results.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from ft_common import market, ollama, twmarket
from ft_common.engine import Ctx, Engine, child_main, line_chart, run_isolated, serve, split_tickers, table

TW50 = ("2330 2317 2454 2308 2382 2881 2882 2891 2303 3711 2412 2886 2884 1216 2885 3231 2357 2892 2345 6669 "
        "2880 3008 2883 2890 5880 2887 2002 1303 1301 2207 3045 2603 4904 3034 2379 2395 1101 6505 2912 5871 "
        "3661 2301 2327 3017 2059 4938 2615 1326 3037 2408").split()
US50 = ("AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B JPM LLY V UNH XOM MA COST HD PG JNJ NFLX ORCL WMT BAC "
        "ABBV CRM AMD KO PEP MRK CVX ADBE TMO LIN MCD CSCO ACN ABT WFC DIS INTU QCOM TXN IBM CAT GE AMAT NOW PM "
        "ISRG PLTR").split()
TWETF = ("0050 006208 0056 00878 00919 00929 00713 00940 00692 00850 00881 00891 00892 00757 00662 00646 00830 "
         "00895 00900 00915 00918 00927 00934 00936 00939 00946").split()
TWETF_BOND = "00679B 00687B 00720B 00751B 00772B 00937B 00933B 00950B".split()
UNIVERSES = {
    "tw50": ([f"{s}.TW" for s in TW50], "0050.TW", 0.001425, 0.004425),   # 手續費 0.1425% + 證交稅 0.3%
    "us50": (US50, "SPY", 0.0005, 0.0005),
    # ETF 輪動：手續費 0.1425%，ETF 證交稅 0.1%（債券 ETF 暫免，這裡保守一起算 0.1%）
    "twetf": ([f"{s}.TW" for s in TWETF] + [f"{s}.TWO" for s in TWETF_BOND], "0050.TW", 0.001425, 0.002425),
}

SCHEMA = [
    {"name": "universe", "label": "股票池", "type": "select", "default": "tw50",
     "options": [{"value": "tw50", "label": "台灣 50（0050 成分）"}, {"value": "us50", "label": "美股 50 大"},
                 {"value": "twetf", "label": "台股熱門 ETF 輪動（含債券 ETF）"},
                 {"value": "custom", "label": "自訂"}]},
    {"name": "tickers", "label": "自訂股票池（選「自訂」時）", "type": "tickers", "placeholder": "2330.TW, 2317.TW, …"},
    {"name": "benchmark", "label": "自訂基準", "type": "ticker", "placeholder": "0050.TW / SPY"},
    {"name": "years", "label": "資料年數", "type": "int", "default": 6, "min": 3, "max": 15},
    {"name": "model", "label": "模型", "type": "select", "default": "lgb",
     "options": [{"value": "lgb", "label": "LightGBM（Alpha158）"}, {"value": "linear", "label": "Ridge 線性（Alpha158）"}]},
    {"name": "topk", "label": "TopK 持股", "type": "int", "default": 10, "min": 1, "max": 50},
    {"name": "n_drop", "label": "每日換股數 n_drop", "type": "int", "default": 2, "min": 0, "max": 20},
    {"name": "refresh", "label": "重新下載資料", "type": "bool", "default": False},
    {"name": "language", "label": "解讀語言", "type": "select", "default": "Traditional Chinese",
     "options": [{"value": "Traditional Chinese", "label": "繁體中文"}, {"value": "English", "label": "English"}]},
    {"name": "ft_context", "label": "加入 FT 市場背景（台股中文新聞、籌碼、月營收、產業強弱、台灣／美國總經、重大事件與日程）", "type": "bool", "default": True},
]


def qsym(s: str) -> str:
    if s.endswith(".TW"):
        return "TW" + s[:-3]
    if s.endswith(".TWO"):
        return "TWO" + s[:-4]
    return s.replace("^", "IDX_").replace(".", "_").replace("-", "_").upper()


def dump_qlib(frames: dict[str, pd.DataFrame], bench: str, out: Path):
    """Write Qlib provider layout: calendars/day.txt, instruments/all.txt, features/<sym>/<f>.day.bin."""
    if out.exists():
        shutil.rmtree(out)
    (out / "calendars").mkdir(parents=True)
    (out / "instruments").mkdir()
    cal = sorted(set().union(*[set(pd.DatetimeIndex(df.index).tz_localize(None).normalize()) for df in frames.values()]))
    cal_idx = {d: i for i, d in enumerate(cal)}
    (out / "calendars" / "day.txt").write_text("\n".join(d.strftime("%Y-%m-%d") for d in cal) + "\n")
    inst = []
    for sym, df in frames.items():
        df = df.copy()
        df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
        df = df[~df.index.duplicated()].sort_index()
        full = df.reindex(cal[cal_idx[df.index[0]]: cal_idx[df.index[-1]] + 1])
        f = pd.DataFrame({"open": full.Open, "high": full.High, "low": full.Low, "close": full.Close,
                          "volume": full.Volume})
        f["vwap"] = (full.High + full.Low + full.Close) / 3
        f["factor"] = 1.0
        f["change"] = f["close"].pct_change()
        d = out / "features" / qsym(sym).lower()
        d.mkdir(parents=True)
        start = cal_idx[df.index[0]]
        for col in f.columns:
            np.hstack([[start], f[col].to_numpy(dtype=np.float64)]).astype("<f").tofile(d / f"{col}.day.bin")
        if sym != bench:
            inst.append(f"{qsym(sym)}\t{df.index[0]:%Y-%m-%d}\t{df.index[-1]:%Y-%m-%d}")
    (out / "instruments" / "all.txt").write_text("\n".join(inst) + "\n")
    return cal


def compute(ctx) -> dict:
    p = ctx.params
    uni = p.get("universe") or "tw50"
    if uni == "custom":
        syms = split_tickers(p.get("tickers"))
        if len(syms) < 5:
            raise ValueError("自訂股票池至少要 5 檔")
        bench = split_tickers(p.get("benchmark") or ("0050.TW" if syms[0].endswith(".TW") else "SPY"))[0]
        open_cost = close_cost = 0.0015
    else:
        syms, bench, open_cost, close_cost = UNIVERSES[uni]
    years = int(p.get("years") or 6)
    topk = int(p.get("topk") or 10)
    n_drop = int(p.get("n_drop") if p.get("n_drop") is not None else 2)
    lang = p.get("language") or "Traditional Chinese"
    llm = ollama.resolve(ctx.llm, "qlib")

    key = hashlib.md5(json.dumps([sorted(syms), bench, years]).encode()).hexdigest()[:10]
    qdir = ctx.dir / "qlib_data" / key
    stamp = qdir / ".stamp"
    fresh = stamp.exists() and stamp.read_text() == dt.date.today().isoformat()
    if p.get("refresh") or not fresh:
        ctx.step(5, f"下載 {len(syms)} 檔日線（yfinance）")
        start = (dt.date.today() - dt.timedelta(days=int(365.25 * years))).isoformat()
        frames = market.download(syms + [bench], start)
        missing = [s for s in syms if s not in frames]
        if missing:
            ctx.log(f"無資料略過：{', '.join(missing)}")
        if bench not in frames:
            raise RuntimeError(f"基準 {bench} 無資料")
        ctx.step(15, "轉成 Qlib .bin 格式")
        dump_qlib(frames, bench, qdir)
        stamp.write_text(dt.date.today().isoformat())
    else:
        ctx.log("使用今天已下載的資料快取")

    ctx.step(20, "初始化 Qlib")
    with ctx.capture():
        import qlib
        from qlib.constant import REG_US
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
        qlib.init(provider_uri=str(qdir), region=REG_US, kernels=1, joblib_backend="threading",
                  exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                               "kwargs": {"uri": "file:" + str(ctx.dir / "mlruns"), "default_exp_name": "fin-terminal"}})
        from qlib.data import D
        cal = [pd.Timestamp(d) for d in D.calendar(freq="day")]
        last = cal[-1]
        test_start = last - pd.DateOffset(years=1)
        valid_start = test_start - pd.DateOffset(years=1)
        data_start = cal[0] + pd.DateOffset(days=90)  # leave room for 60-day rolling factors
        fmt = lambda d: pd.Timestamp(d).strftime("%Y-%m-%d")  # noqa: E731
        seg = {"train": (fmt(data_start), fmt(valid_start - pd.Timedelta(days=1))),
               "valid": (fmt(valid_start), fmt(test_start - pd.Timedelta(days=1))),
               "test": (fmt(test_start), fmt(last))}
        ctx.log(f"區段：{seg}")

        ctx.step(25, "計算 Alpha158 因子")
        from qlib.contrib.data.handler import Alpha158
        from qlib.data.dataset import DatasetH
        from qlib.data.dataset.handler import DataHandlerLP
        kw = dict(instruments="all", start_time=seg["train"][0], end_time=seg["test"][1],
                  fit_start_time=seg["train"][0], fit_end_time=seg["train"][1])
        if p.get("model") == "linear":
            kw["infer_processors"] = [{"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
                                      {"class": "Fillna", "kwargs": {"fields_group": "feature"}}]
            kw["learn_processors"] = [{"class": "DropnaLabel"}, {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}}]
        handler = Alpha158(**kw)
        ds = DatasetH(handler, segments=seg)

        ctx.step(45, "訓練模型")
        if p.get("model") == "linear":
            from qlib.contrib.model.linear import LinearModel
            model = LinearModel(estimator="ridge", alpha=0.05)
            mname = "Ridge"
        else:
            from qlib.contrib.model.gbdt import LGBModel
            model = LGBModel(loss="mse", learning_rate=0.05, num_leaves=63, max_depth=8, colsample_bytree=0.8,
                             subsample=0.8, lambda_l1=1.0, lambda_l2=1.0, num_threads=os.cpu_count() or 4,
                             num_boost_round=400, early_stopping_rounds=40, verbose=-1)
            mname = "LightGBM"
        model.fit(ds)

        ctx.step(65, "預測與 IC 分析")
        pred = model.predict(ds, segment="test")
        if isinstance(pred, pd.DataFrame):
            pred = pred.iloc[:, 0]
        label = ds.prepare("test", col_set="label", data_key=DataHandlerLP.DK_R).iloc[:, 0]
        df = pd.concat([pred.rename("score"), label.rename("label")], axis=1).dropna()
        ic = df.groupby(level="datetime").apply(lambda g: g["score"].corr(g["label"]))
        ric = df.groupby(level="datetime").apply(lambda g: g["score"].corr(g["label"], method="spearman"))

        ctx.step(75, f"TopK-Dropout 回測（topk={topk}, n_drop={n_drop}）")
        from qlib.backtest import backtest, executor
        from qlib.contrib.evaluate import risk_analysis
        from qlib.contrib.strategy import TopkDropoutStrategy
        strat = TopkDropoutStrategy(signal=pred, topk=topk, n_drop=n_drop)
        exe = executor.SimulatorExecutor(time_per_step="day", generate_portfolio_metrics=True)
        pm, _ = backtest(executor=exe, strategy=strat, start_time=seg["test"][0], end_time=fmt(cal[-2]),
                         account=10_000_000, benchmark=qsym(bench),
                         exchange_kwargs={"freq": "day", "limit_threshold": None, "deal_price": "close",
                                          "open_cost": open_cost, "close_cost": close_cost, "min_cost": 0})
        report = pm["1day"][0]
        excess = risk_analysis(report["return"] - report["bench"] - report["cost"], freq="day")["risk"]
        strat_r = risk_analysis(report["return"] - report["cost"], freq="day")["risk"]
        bench_r = risk_analysis(report["bench"], freq="day")["risk"]

    ctx.step(88, "整理結果")
    back = {qsym(s): s for s in syms}
    latest_day = pred.index.get_level_values("datetime").max()
    latest = pred.xs(latest_day, level="datetime").sort_values(ascending=False)
    def nm(k):
        s_ = back.get(k, k)
        try:
            return twmarket.zh_name(s_) or ""
        except Exception:  # noqa: BLE001
            return ""
    picks = [[i + 1, back.get(k, k), nm(k), f"{v:+.4f}"] for i, (k, v) in enumerate(latest.head(max(topk, 10)).items())]
    bottom = [[back.get(k, k), nm(k), f"{v:+.4f}"] for k, v in latest.tail(5).items()]

    nav = (1 + report["return"] - report["cost"]).cumprod()
    bnav = (1 + report["bench"]).cumprod()
    metrics = [
        ["策略 年化報酬", f"{strat_r['annualized_return']:.2%}"], ["策略 最大回撤", f"{strat_r['max_drawdown']:.2%}"],
        ["策略 IR", f"{strat_r['information_ratio']:.2f}"],
        [f"基準 {bench} 年化報酬", f"{bench_r['annualized_return']:.2%}"],
        ["超額 年化報酬", f"{excess['annualized_return']:.2%}"], ["超額 IR", f"{excess['information_ratio']:.2f}"],
        ["超額 最大回撤", f"{excess['max_drawdown']:.2%}"],
        ["IC 平均", f"{ic.mean():.4f}"], ["ICIR", f"{ic.mean() / ic.std():.3f}" if ic.std() else "—"],
        ["Rank IC 平均", f"{ric.mean():.4f}"], ["測試期", f"{seg['test'][0]} ~ {seg['test'][1]}"],
        ["樣本股數", str(len(syms))],
    ]

    ctx.step(92, "Ollama 解讀結果")
    commentary = ollama.ask(
        llm, "You are a quantitative researcher reviewing a Qlib backtest. Explain what the numbers mean "
             "(IC, ICIR, excess return, drawdown), whether the signal looks robust or overfit, and how to read "
             "today's ranking. Note limitations: survivorship bias (current constituents), yfinance data, "
             "one-year test window. Be concise and use markdown.",
        f"模型 {mname} / Alpha158 / 股票池 {uni} / 基準 {bench}\n指標：\n" +
        "\n".join(f"- {a}: {b}" for a, b in metrics) +
        f"\n\n{latest_day:%Y-%m-%d} 預測分數前段：{', '.join(f'{r[1]} {r[2]}' for r in picks[:topk])}\n後段：{', '.join(f'{r[0]} {r[1]}' for r in bottom)}"
        + (("\n\n" + llm["context"]) if llm.get("context") else ""),
        language=lang)

    md = (f"# Qlib · {mname} + Alpha158 · {uni.upper()}\n\n"
          f"超額年化 **{excess['annualized_return']:.2%}**，IR **{excess['information_ratio']:.2f}**，"
          f"IC **{ic.mean():.4f}**（測試期 {seg['test'][0]} ~ {seg['test'][1]}）\n\n## 🤖 解讀\n\n{commentary}\n")
    return {"summary": f"{uni} {mname}: 超額 {excess['annualized_return']:.1%} / IC {ic.mean():.3f}",
            "markdown": md,
            "charts": [line_chart("淨值：策略 vs 基準", {"策略": list(zip(nav.index.strftime("%Y-%m-%d"), nav.values)),
                                                    bench: list(zip(bnav.index.strftime("%Y-%m-%d"), bnav.values))}),
                       line_chart("每日 IC（20 日平均）", {"IC": list(zip(ic.index.strftime("%Y-%m-%d"), ic.rolling(20).mean().fillna(0).values))})],
            "tables": [table("績效指標", ["指標", "數值"], metrics),
                       table(f"{latest_day:%Y-%m-%d} 預測排名（越高越看好）", ["#", "標的", "名稱", "分數"], picks),
                       table("分數最低 5 檔", ["標的", "名稱", "分數"], bottom)]}


def run(ctx: Ctx) -> dict:
    return run_isolated(ctx, __file__)


engine = Engine("qlib", "Qlib 量化因子",
                "microsoft/qlib：Alpha158 因子 + LightGBM / Ridge 預測次日報酬，TopK-Dropout 策略回測（含台股手續費與證交稅），再由本機 Ollama 解讀。",
                SCHEMA, run, repo="https://github.com/microsoft/qlib", version=os.environ.get("UPSTREAM_REF", ""))
app = engine.app

if __name__ == "__main__":
    child_main(compute)
    serve(engine, 8133)
