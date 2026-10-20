"""FinRL worker — deep RL stock-trading agent (Stable-Baselines3) on FinRL's StockTradingEnv.

Train on history, then trade the most recent out-of-sample window and compare
with an equal-weight buy & hold. RL itself has no LLM; local Ollama explains
the result.
"""
from __future__ import annotations

import datetime as dt
import os

import numpy as np
import pandas as pd

from ft_common import market, ollama
from ft_common.engine import Ctx, Engine, child_main, line_chart, run_isolated, serve, split_tickers, table

ALGOS = [{"value": a, "label": a.upper()} for a in ("ppo", "a2c", "sac", "td3", "ddpg")]

SCHEMA = [
    {"name": "tickers", "label": "投資組合標的", "type": "tickers", "required": True,
     "default": "2330.TW, 2317.TW, 2454.TW, 2308.TW, 2881.TW", "placeholder": "2330.TW, 2317.TW, …"},
    {"name": "years", "label": "訓練資料年數", "type": "int", "default": 5, "min": 2, "max": 15},
    {"name": "trade_months", "label": "回測（交易）月數", "type": "int", "default": 12, "min": 3, "max": 36},
    {"name": "algo", "label": "演算法", "type": "select", "default": "ppo", "options": ALGOS},
    {"name": "timesteps", "label": "訓練步數", "type": "int", "default": 30000, "min": 2000, "max": 1000000},
    {"name": "initial_amount", "label": "初始資金", "type": "int", "default": 1000000},
    {"name": "language", "label": "解讀語言", "type": "select", "default": "Traditional Chinese",
     "options": [{"value": "Traditional Chinese", "label": "繁體中文"}, {"value": "English", "label": "English"}]},
    {"name": "ft_context", "label": "加入 FT 市場背景（台股中文新聞、籌碼、月營收、產業強弱、台灣／美國總經、重大事件與日程）", "type": "bool", "default": True},
]


def _stats(series: pd.Series) -> dict:
    r = series.pct_change().dropna()
    total = series.iloc[-1] / series.iloc[0] - 1
    ann = (1 + total) ** (252 / max(len(r), 1)) - 1
    sharpe = (r.mean() / r.std() * np.sqrt(252)) if r.std() else 0.0
    dd = (series / series.cummax() - 1).min()
    return {"total": total, "ann": ann, "sharpe": sharpe, "mdd": dd}


def compute(ctx) -> dict:
    p = ctx.params
    tickers = split_tickers(p.get("tickers"))
    if len(tickers) < 2:
        raise ValueError("至少需要 2 檔標的")
    years = int(p.get("years") or 5)
    months = int(p.get("trade_months") or 12)
    algo = (p.get("algo") or "ppo").lower()
    steps = int(p.get("timesteps") or 30000)
    cash = float(p.get("initial_amount") or 1_000_000)
    lang = p.get("language") or "Traditional Chinese"
    llm = ollama.resolve(ctx.llm, "finrl")

    ctx.step(5, f"下載 {len(tickers)} 檔日線（yfinance）")
    start = (dt.date.today() - dt.timedelta(days=int(365.25 * years) + 31 * months)).isoformat()
    frames = market.download(tickers, start)
    missing = [t for t in tickers if t not in frames]
    if missing:
        ctx.log(f"無資料略過：{', '.join(missing)}")
    tickers = [t for t in tickers if t in frames]
    rows = []
    for t, df in frames.items():
        d = df.copy()
        d.index = pd.DatetimeIndex(d.index).tz_localize(None).normalize()
        d = d.reset_index().rename(columns={"Date": "date", "index": "date", "Open": "open", "High": "high",
                                            "Low": "low", "Close": "close", "Volume": "volume"})
        d["tic"] = t
        rows.append(d[["date", "open", "high", "low", "close", "volume", "tic"]])
    raw = pd.concat(rows)
    common = raw.groupby("date")["tic"].nunique()
    common = common[common == len(tickers)].index
    raw = raw[raw.date.isin(common)].sort_values(["date", "tic"]).reset_index(drop=True)
    raw["day"] = raw["date"].dt.dayofweek
    raw["date"] = raw["date"].dt.strftime("%Y-%m-%d")

    ctx.step(15, "FinRL FeatureEngineer：技術指標")
    from finrl import config as fconf
    from finrl.agents.stablebaselines3.models import DRLAgent
    from finrl.meta.env_stock_trading.env_stocktrading import StockTradingEnv
    from finrl.meta.preprocessor.preprocessors import FeatureEngineer, data_split

    fe = FeatureEngineer(use_technical_indicator=True, tech_indicator_list=fconf.INDICATORS,
                         use_vix=False, use_turbulence=False, user_defined_feature=False)
    proc = fe.preprocess_data(raw).ffill().bfill()
    dates = sorted(proc.date.unique())
    split = (pd.Timestamp(dates[-1]) - pd.DateOffset(months=months)).strftime("%Y-%m-%d")
    train = data_split(proc, dates[0], split)
    trade = data_split(proc, split, dates[-1])
    ctx.log(f"訓練 {train.date.min()} ~ {train.date.max()}（{train.index.nunique()} 天）  "
            f"交易 {trade.date.min()} ~ {trade.date.max()}（{trade.index.nunique()} 天）")

    n = len(tickers)
    ind = fconf.INDICATORS
    env_kwargs = dict(hmax=max(1, int(cash / n / float(proc.close.mean()) / 2)), initial_amount=int(cash),
                      num_stock_shares=[0] * n, buy_cost_pct=[0.001425] * n,
                      sell_cost_pct=[0.004425 if t.endswith((".TW", ".TWO")) else 0.001] * n,
                      state_space=1 + 2 * n + len(ind) * n, stock_dim=n, tech_indicator_list=ind,
                      action_space=n, reward_scaling=1e-4, print_verbosity=10_000)
    e_train, _ = StockTradingEnv(df=train, **env_kwargs).get_sb_env()

    ctx.step(25, f"訓練 {algo.upper()}（{steps:,} 步，CPU）")
    agent = DRLAgent(env=e_train)
    kwargs = {"ppo": {"n_steps": 2048, "ent_coef": 0.01, "learning_rate": 2.5e-4, "batch_size": 128},
              "a2c": {"n_steps": 5, "ent_coef": 0.01, "learning_rate": 7e-4},
              "sac": {"batch_size": 128, "buffer_size": 100000, "learning_rate": 1e-4, "learning_starts": 100, "ent_coef": "auto_0.1"},
              "td3": {"batch_size": 100, "buffer_size": 100000, "learning_rate": 1e-3},
              "ddpg": {"batch_size": 128, "buffer_size": 50000, "learning_rate": 1e-3}}[algo]
    model = agent.get_model(algo, model_kwargs=kwargs, verbose=0, seed=42)
    model = DRLAgent.train_model(model=model, tb_log_name=algo, total_timesteps=steps)

    ctx.step(80, "樣本外交易回測")
    e_trade = StockTradingEnv(df=trade, turbulence_threshold=None, risk_indicator_col="close", **env_kwargs)
    acct, actions = DRLAgent.DRL_prediction(model=model, environment=e_trade)
    acct = acct.set_index("date")["account_value"]
    acct.index = acct.index.astype(str)

    close = trade.pivot_table(index="date", columns="tic", values="close")
    bh = (close / close.iloc[0]).mean(axis=1) * cash
    bh.index = bh.index.astype(str)
    s_rl, s_bh = _stats(acct), _stats(bh)

    last_pos = actions.tail(10) if actions is not None else pd.DataFrame()
    act_rows = []
    if len(last_pos):
        for d, r in last_pos.iterrows():
            act_rows.append([str(d)[:10]] + [int(v) for v in r.values])
    metrics = [
        ["RL 總報酬", f"{s_rl['total']:.2%}", f"{s_bh['total']:.2%}"],
        ["年化報酬", f"{s_rl['ann']:.2%}", f"{s_bh['ann']:.2%}"],
        ["Sharpe", f"{s_rl['sharpe']:.2f}", f"{s_bh['sharpe']:.2f}"],
        ["最大回撤", f"{s_rl['mdd']:.2%}", f"{s_bh['mdd']:.2%}"],
        ["期末資產", f"{acct.iloc[-1]:,.0f}", f"{bh.iloc[-1]:,.0f}"],
    ]

    ctx.step(92, "Ollama 解讀結果")
    commentary = ollama.ask(
        llm, "You are reviewing a FinRL deep-reinforcement-learning trading backtest. Compare the RL agent with "
             "the equal-weight buy-and-hold baseline, interpret the recent actions (positive=buy shares, "
             "negative=sell), and flag the usual RL caveats (few training samples, seed sensitivity, overfitting, "
             "costs). Be concise; markdown.",
        f"演算法 {algo.upper()}，標的 {', '.join(tickers)}，訓練步數 {steps}\n" +
        "\n".join(f"- {a}: RL {b} / 買進持有 {c}" for a, b, c in metrics) +
        (f"\n最近動作（股數）欄位 {list(last_pos.columns)}：\n{last_pos.to_string()}" if len(last_pos) else "")
        + (("\n\n" + llm["context"]) if llm.get("context") else ""),
        language=lang)

    md = (f"# FinRL · {algo.upper()} · {len(tickers)} 檔\n\n樣本外 {trade.date.min()} ~ {trade.date.max()}："
          f"RL **{s_rl['total']:.2%}** vs 等權買進持有 **{s_bh['total']:.2%}**（Sharpe {s_rl['sharpe']:.2f} vs {s_bh['sharpe']:.2f}）\n\n"
          f"## 🤖 解讀\n\n{commentary}\n")
    return {"summary": f"{algo.upper()} {s_rl['total']:+.1%} vs B&H {s_bh['total']:+.1%}",
            "signal": "跑贏" if s_rl["total"] > s_bh["total"] else "落後",
            "markdown": md,
            "charts": [line_chart("帳戶價值：RL vs 等權買進持有",
                                  {f"RL {algo.upper()}": list(acct.items()), "買進持有": list(bh.items())})],
            "tables": [table("績效", ["指標", "RL", "買進持有"], metrics),
                       table("最近 10 天動作（股數，+買 −賣）", ["日期"] + [str(c) for c in last_pos.columns], act_rows)]}


def run(ctx: Ctx) -> dict:
    return run_isolated(ctx, __file__)


engine = Engine("finrl", "FinRL 強化學習交易",
                "AI4Finance-Foundation/FinRL：以 Stable-Baselines3（PPO/A2C/SAC/TD3/DDPG）在 FinRL StockTradingEnv 訓練交易代理人，樣本外回測並與等權買進持有比較，再由本機 Ollama 解讀。",
                SCHEMA, run, repo="https://github.com/AI4Finance-Foundation/FinRL", version=os.environ.get("UPSTREAM_REF", ""))
app = engine.app

if __name__ == "__main__":
    child_main(compute)
    serve(engine, 8134)
