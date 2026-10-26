"""TradingAgents (TauricResearch) worker — multi-agent LLM trading firm on local Ollama."""
from __future__ import annotations

import os

from ft_common import ollama
from ft_common.engine import Ctx, Engine, serve, split_tickers, today

import limits
import resume
import world_hook

ANALYSTS = [
    {"value": "market", "label": "技術面 Market"},
    {"value": "social", "label": "情緒面 Social"},
    {"value": "news", "label": "新聞面 News"},
    {"value": "fundamentals", "label": "基本面 Fundamentals"},
]

SCHEMA = [
    {"name": "ticker", "label": "標的", "type": "ticker", "required": True, "placeholder": "NVDA / 2330.TW"},
    {"name": "date", "label": "分析日期", "type": "date", "default": "today"},
    {"name": "analysts", "label": "分析師團隊", "type": "multi", "options": ANALYSTS,
     "default": ["market", "social", "news", "fundamentals"]},
    {"name": "debate_rounds", "label": "多空辯論回合", "type": "int", "default": 1, "min": 1, "max": 5},
    {"name": "risk_rounds", "label": "風控討論回合", "type": "int", "default": 1, "min": 1, "max": 5},
    {"name": "language", "label": "報告語言", "type": "select", "default": "Traditional Chinese",
     "options": [{"value": "Traditional Chinese", "label": "繁體中文"}, {"value": "English", "label": "English"}]},
    {"name": "world", "label": "加入現實世界訊號（God's Eye View：地震、軍機、航運…）", "type": "bool", "default": True},
    {"name": "max_tokens_debate", "label": "研究員／交易員每次輸出上限（tokens，防止辯論寫不停）", "type": "int",
     "default": limits.DEFAULT_CAPS["debate"], "min": 512, "max": 16384},
    {"name": "max_tokens_report", "label": "分析師報告每次輸出上限（tokens）", "type": "int",
     "default": limits.DEFAULT_CAPS["report"], "min": 1024, "max": 32768},
    {"name": "resume", "label": "沿用上次失敗時已完成的分析師報告（同標的、同日期、12 小時內；只重跑沒完成的）", "type": "bool", "default": True},
    {"name": "ft_context", "label": "加入 FT 市場背景（台股中文新聞、籌碼、月營收、產業強弱、台灣／美國總經、重大事件與日程）", "type": "bool", "default": True},
]

SECTIONS = [
    ("final_trade_decision", "🧭 投資組合經理｜最終決策"),
    ("trader_investment_plan", "💼 交易員｜交易計畫"),
    ("investment_plan", "🔬 研究經理｜投資計畫"),
    ("market_report", "📈 技術分析師"),
    ("sentiment_report", "💬 情緒分析師"),
    ("news_report", "📰 新聞分析師"),
    ("fundamentals_report", "🏦 基本面分析師"),
]


def run(ctx: Ctx) -> dict:
    p = ctx.params
    ticker = split_tickers(p["ticker"])[0]
    date = p.get("date") or today()
    if date == "today":
        date = today()
    analysts = p.get("analysts") or [a["value"] for a in ANALYSTS]
    deep = ollama.resolve(ctx.llm, "tradingagents_deep")
    quick = ollama.resolve(ctx.llm, "tradingagents_quick")

    home = ctx.dir / "home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["OLLAMA_BASE_URL"] = f"{deep['url']}/v1"

    ctx.step(5, "載入 TradingAgents")
    from tradingagents.default_config import build_default_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    cfg = build_default_config()
    cfg.update({
        "llm_provider": "ollama",
        "backend_url": f"{deep['url']}/v1",
        "deep_think_llm": deep["model"],
        "quick_think_llm": quick["model"],
        "max_debate_rounds": int(p.get("debate_rounds") or 1),
        "max_risk_discuss_rounds": int(p.get("risk_rounds") or 1),
        "output_language": p.get("language") or "Traditional Chinese",
        "results_dir": str(ctx.dir / "results"),
        "data_cache_dir": str(ctx.dir / "cache"),
        "memory_log_path": str(ctx.dir / "memory" / "trading_memory.md"),
        "temperature": float(deep.get("temperature", 0.2)),
        # a timed-out local call is not worth repeating: fail once instead of 3× the proxy timeout
        "llm_max_retries": int(os.environ.get("FT_TA_RETRIES", "0")),
    })
    caps = limits.caps_from(p)
    cfg["max_tokens"] = caps["report"]  # fallback for any call not covered by a per-node cap
    stats: dict = {}
    pipe = limits.Pipeline(ctx.job, analysts)
    pp = os.environ.get("FT_TA_PRESENCE_PENALTY", "")
    limits.install(caps, ctx.log, stats, timeout=float(os.environ.get("FT_TA_TIMEOUT", "600")),
                   presence_penalty=float(pp) if pp else None, pipe=pipe)
    store = resume.Store(ctx.dir, ticker, date, cfg["output_language"],
                         hours=float(os.environ.get("FT_TA_RESUME_HOURS", "12")))
    use_resume = p.get("resume", True) not in (False, "false", "0", 0)
    reuse = {k: v for k, v in store.load().items() if k in analysts} if use_resume else {}
    resume.install(store, reuse, ctx.log, pipe, job_id=str(getattr(ctx.job, "id", "")))
    if reuse:
        names = [lab for _, (k, _, lab) in resume.ANALYSTS.items() if k in reuse]
        ctx.log(f"↺ 沿用上次失敗前已完成的報告：{'、'.join(names)}；只重跑其餘分析師")
    ctx.log("輸出上限：" + "、".join(f"{k} {v}" for k, v in caps.items())
            + f"；逾時 {os.environ.get('FT_TA_TIMEOUT', '600')} 秒、重試 {cfg['llm_max_retries']} 次")
    if deep.get("model") != quick.get("model"):
        cfg["quick_think_provider"] = "ollama"
        cfg["quick_think_backend_url"] = f"{quick['url']}/v1"
    ctx.log(f"deep={deep['model']}  quick={quick['model']}  endpoint={cfg['backend_url']}")
    ctx.log(f"analysts={','.join(analysts)}  date={date}")

    use_world = p.get("world", True) not in (False, "false", "0", 0)
    world_info = None
    graph_cls = TradingAgentsGraph
    world_hook.install(use_world)
    if use_world:
        ctx.step(8, "讀取現實世界訊號（God's Eye View）")
        from ft_common import world
        regions = world.regions_for(ticker)
        as_of = world_hook.as_of_for(date)
        try:
            compact = world.compact(regions, as_of)
            world_info = {"regions": regions, "digest": world.digest(regions, as_of),
                          "events": world.events(regions, days=7, as_of=as_of)}
            graph_cls = world_hook.graph_class(TradingAgentsGraph, compact)
            ctx.log(f"世界訊號區域：{', '.join(regions)}；事件 {len(world_info['events'])} 筆；新聞分析師多了 get_world_events 工具")
        except Exception as e:  # noqa: BLE001
            ctx.log(f"世界訊號無法讀取，略過：{e}")

    ft_ctx = (ctx.llm or {}).get("context") or ""
    if ft_ctx:  # FT 市場背景: Chinese news, flows, revenue, sector, macro, events — every agent reads it
        graph_cls = world_hook.graph_class(graph_cls, ft_ctx)
        ctx.log(f"帶入 FT 市場背景：{(ctx.llm or {}).get('context_parts') or ''}（{len(ft_ctx)} 字）")
    lessons = (ctx.llm or {}).get("lessons") or ""
    if lessons:  # 檢討回饋：appended to the instrument context every agent reads
        graph_cls = world_hook.graph_class(graph_cls, "LESSONS FROM REVIEWING YOUR PAST CALLS AGAINST ACTUAL PRICES "
                                                      "(FT review system; avoid repeating these mistakes):\n" + lessons)
        ctx.log(f"帶入過去檢討 {sum(1 for x in lessons.splitlines() if x.startswith('- '))} 條（分析紀錄頁可關閉）")
    ctx.step(10, f"多代理人分析 {ticker}（分析師 → 多空辯論 → 交易員 → 風控 → 經理）")
    asset = "crypto" if ticker.endswith("-USD") else "stock"
    try:
        with ctx.capture():
            ta = graph_cls(selected_analysts=tuple(analysts), debug=True, config=cfg)
            state, rating = ta.propagate(ticker, date, asset_type=asset)
    except BaseException:
        pipe.finish(False)
        raise
    pipe.finish(True)
    store.clear()  # finished: the next run is a new analysis, not a replay

    ctx.step(95, "整理報告")
    md = [f"# {ticker} · {date} · 評等：**{rating}**\n"]
    for key, title in SECTIONS:
        v = state.get(key)
        if v:
            md.append(f"## {title}\n\n{v}\n")
    debate = state.get("investment_debate_state") or {}
    if debate.get("bull_history") or debate.get("bear_history"):
        md.append("## 🐂🐻 多空辯論紀錄\n")
        if debate.get("bull_history"):
            md.append(f"### 多方\n\n{debate['bull_history']}\n")
        if debate.get("bear_history"):
            md.append(f"### 空方\n\n{debate['bear_history']}\n")
    risk = state.get("risk_debate_state") or {}
    if risk.get("history"):
        md.append(f"## ⚖️ 風控討論\n\n{risk['history']}\n")

    if reuse:
        md.insert(1, "> ↺ 沿用上次（失敗前）已完成的報告：" + "、".join(
            lab for _, (k, _, lab) in resume.ANALYSTS.items() if k in reuse) + "。\n")
    if stats:
        cut = [k for k, v in stats.items() if v["cut"]]
        if cut:
            md.insert(1, f"> ⚠️ {'、'.join(cut)} 的輸出達到上限被截斷（內容保留，流程照常完成）。若常發生，可在參數調高上限。\n")
        md.append("<details><summary>⏱ 各節點耗時與輸出量</summary>\n\n" + limits.summary(stats) + "\n</details>\n")
    if ft_ctx:
        md.append("\n<details><summary>分析時帶入的 FT 市場背景</summary>\n\n```\n" + ft_ctx + "\n```\n</details>\n")
    if world_info:
        md.append("<details><summary>🌐 分析時提供給代理人的現實世界訊號（God's Eye View）</summary>\n\n"
                  + world_info["digest"] + "\n</details>\n")
    decision = state.get("final_trade_decision") or ""
    return {"summary": f"{ticker} {date} → {rating}", "signal": str(rating),
            "markdown": "\n".join(md), "headline": decision[:400], "world": world_info, "node_stats": stats}


engine = Engine("tradingagents", "TradingAgents 多代理人交易公司",
                "TauricResearch/TradingAgents：技術/情緒/新聞/基本面分析師 → 多空研究員辯論 → 交易員 → 風控團隊 → 投資組合經理，全部跑在本機 Ollama。",
                SCHEMA, run, repo="https://github.com/TauricResearch/TradingAgents",
                version=os.environ.get("UPSTREAM_REF", ""))
app = engine.app

if __name__ == "__main__":
    serve(engine, 8131)
