"""Per-node output caps, request timeout and retry policy for TradingAgents on a local model.

Why: on a local 27B model the Bull/Bear researchers sometimes never stop writing (8–29k tokens, even
with thinking off), which runs into the 15-minute proxy timeout, and the OpenAI SDK then *retries* the
same prompt twice more — one runaway node costs 45 minutes. Upstream only has one global
`max_tokens`, but analyst reports legitimately need more room than a debate turn, so we cap each
node separately:

  report   技術／情緒／新聞／基本面分析師（每次呼叫，含工具迴圈）     default 8192
  debate   多方／空方研究員                                          default 3072
  risk     積極／中立／保守風控                                      default 2048
  trader   交易員                                                    default 3072
  manager  研究經理、投資組合經理（結構化輸出）                       default 4096

Wire format: langchain-openai ≥0.3 renames `max_tokens` to `max_completion_tokens`, which older Ollama
OpenAI-compat servers ignore (only `max_tokens` → num_predict is honoured). We send both.

A node that hits its cap ends with finish_reason=length: the text so far is kept, the run goes on,
and the job log says which node was cut. Retries default to 0 and the request timeout to 600 s, so a
stuck call fails once, quickly, instead of three times at the proxy limit.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler

DEFAULT_CAPS = {"report": 8192, "debate": 3072, "risk": 2048, "trader": 3072, "manager": 4096}
FACTORIES = {
    "create_market_analyst": ("report", "技術分析師"),
    "create_sentiment_analyst": ("report", "情緒分析師"),
    "create_news_analyst": ("report", "新聞分析師"),
    "create_fundamentals_analyst": ("report", "基本面分析師"),
    "create_bull_researcher": ("debate", "多方研究員"),
    "create_bear_researcher": ("debate", "空方研究員"),
    "create_research_manager": ("manager", "研究經理"),
    "create_trader": ("trader", "交易員"),
    "create_aggressive_debator": ("risk", "積極風控"),
    "create_neutral_debator": ("risk", "中立風控"),
    "create_conservative_debator": ("risk", "保守風控"),
    "create_portfolio_manager": ("manager", "投資組合經理"),
}


def caps_from(params: dict) -> dict[str, int]:
    """Job params → caps (env FT_TA_MAX_TOKENS_<GROUP> overrides the defaults; job fields override env)."""
    caps = {g: int(os.environ.get(f"FT_TA_MAX_TOKENS_{g.upper()}", v)) for g, v in DEFAULT_CAPS.items()}
    if params.get("max_tokens_debate"):
        caps["debate"] = int(params["max_tokens_debate"])
        caps["risk"] = min(caps["risk"], caps["debate"])
        caps["trader"] = int(params["max_tokens_debate"])
    if params.get("max_tokens_report"):
        caps["report"] = int(params["max_tokens_report"])
    return {g: max(256, min(v, 32768)) for g, v in caps.items()}


STAGES = [("分析師", ("技術分析師", "情緒分析師", "新聞分析師", "基本面分析師")),
          ("多空辯論", ("多方研究員", "空方研究員", "研究經理")),
          ("交易員", ("交易員",)),
          ("風控", ("積極風控", "保守風控", "中立風控")),
          ("經理", ("投資組合經理",))]
ANALYST_LABEL = {"market": "技術分析師", "social": "情緒分析師", "news": "新聞分析師", "fundamentals": "基本面分析師"}


class Pipeline:
    """Live per-node status for the job page: who is running, for how long, and whether it is the model
    (LLM 生成中) or a tool / data fetch (工具／資料抓取中) that is taking the time."""

    def __init__(self, job, analysts: list[str], on_change: Callable[[], None] | None = None):
        self.job = job
        self.on_change = on_change
        want = {ANALYST_LABEL.get(a, a) for a in analysts}
        job.pipeline = [{"stage": st, "nodes": [{"label": n, "status": "pending", "calls": 0, "out": 0, "inflight": 0}
                                                for n in nodes if st != "分析師" or n in want]} for st, nodes in STAGES]
        self.total = sum(len(s["nodes"]) for s in job.pipeline)
        self._touch()

    def _node(self, label):
        for st in self.job.pipeline:
            for n in st["nodes"]:
                if n["label"] == label:
                    return st["stage"], n
        return None, None

    def _touch(self):
        self.job.pipe_rev += 1
        done = sum(1 for st in self.job.pipeline for n in st["nodes"] if n["status"] in ("done", "cut"))
        self.job.progress = max(self.job.progress, 10 + 85 * done / max(self.total, 1))
        running = [f"{st['stage']} › {n['label']}（{'LLM 生成中' if n['inflight'] else '工具／資料抓取中'}）"
                   for st in self.job.pipeline for n in st["nodes"] if n["status"] == "running"]
        if running:
            self.job.stage = "、".join(running)[:160]
        if self.on_change:
            self.on_change()

    def start(self, label):
        stage, n = self._node(label)
        if not n:
            return
        now = time.time()
        if stage != "分析師":  # sequential part of the graph: whatever ran before has finished
            for st in self.job.pipeline:
                for m in st["nodes"]:
                    if m is not n and m["status"] == "running" and not m["inflight"]:
                        m["status"], m["ended"] = "done", now
        n.setdefault("started", now)
        n.update(status="running", call_started=now, inflight=n["inflight"] + 1)
        n.pop("ended", None)
        self._touch()

    def end(self, label, out: int, sec: float, cut: bool, tool_calls: bool):
        _, n = self._node(label)
        if not n:
            return
        now = time.time()
        n["inflight"] = max(0, n["inflight"] - 1)
        n["calls"] += 1
        n["out"] += int(out or 0)
        n["last_sec"] = round(sec, 1)
        n["waiting_since"] = now
        if cut:
            n["cut"] = n.get("cut", 0) + 1
        if not tool_calls and not n["inflight"]:
            n["status"], n["ended"] = ("cut" if cut else "done"), now
        self._touch()

    def reused(self, label, note: str):
        """An analyst that answered with the report saved by an earlier, failed run."""
        _, n = self._node(label)
        if n:
            now = time.time()
            n.update(status="done", note=note, reused=True, started=now, ended=now, inflight=0)
            self._touch()

    def error(self, label, err: str):
        _, n = self._node(label)
        if n:
            n.update(status="error", note=err[:160], ended=time.time(), inflight=0)
            self._touch()

    def finish(self, ok: bool):
        now = time.time()
        for st in self.job.pipeline:
            for n in st["nodes"]:
                if n["status"] == "running":
                    n["status"], n["ended"] = ("done" if ok else "error"), now
                    n["inflight"] = 0
        self._touch()


class NodeTracker(BaseCallbackHandler):
    """Logs one line per LLM call: node, output tokens, seconds, and whether the cap cut it."""

    def __init__(self, label: str, cap: int, log: Callable[[str], None], stats: dict, pipe: "Pipeline | None" = None):
        self.label, self.cap, self.log, self.stats, self.pipe = label, cap, log, stats, pipe
        self._t: dict[Any, float] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
        self._t[run_id] = time.time()
        if self.pipe:
            self.pipe.start(self.label)

    def on_llm_end(self, response, *, run_id, **kw):
        sec = time.time() - self._t.pop(run_id, time.time())
        gen = (response.generations or [[None]])[0][0]
        info = (getattr(gen, "generation_info", None) or {})
        finish = info.get("finish_reason")
        usage = ((response.llm_output or {}).get("token_usage") or {})
        msg = getattr(gen, "message", None)
        um = getattr(msg, "usage_metadata", None) or {}
        out = usage.get("completion_tokens") or um.get("output_tokens")
        inp = usage.get("prompt_tokens") or um.get("input_tokens")
        cut = finish == "length"
        s = self.stats.setdefault(self.label, {"calls": 0, "out": 0, "sec": 0.0, "cut": 0})
        s["calls"] += 1
        s["out"] += int(out or 0)
        s["sec"] += sec
        s["cut"] += int(cut)
        self.log(f"⏱ {self.label}：輸入 {inp or '?'} / 輸出 {out or '?'} tokens，{sec:.0f} 秒"
                 + (f"　⚠️ 達上限 {self.cap} 被截斷（保留已寫的內容，繼續下一步）" if cut else ""))
        if self.pipe:
            self.pipe.end(self.label, int(out or 0), sec, cut, bool(getattr(msg, "tool_calls", None)))

    def on_llm_error(self, error, *, run_id, **kw):
        sec = time.time() - self._t.pop(run_id, time.time())
        self.log(f"✗ {self.label}：{type(error).__name__} 於 {sec:.0f} 秒（不重試）")
        if self.pipe:
            self.pipe.error(self.label, f"{type(error).__name__} 於 {sec:.0f} 秒")


def _capped(llm, cap: int, label: str, log, stats, presence_penalty: float | None, pipe=None):
    if not hasattr(llm, "model_copy"):
        return llm
    upd: dict[str, Any] = {"max_tokens": cap,
                           "callbacks": list(getattr(llm, "callbacks", None) or []) + [NodeTracker(label, cap, log, stats, pipe)]}
    if presence_penalty is not None:
        upd["presence_penalty"] = presence_penalty
    return llm.model_copy(update=upd)


_ORIG: dict[str, Callable] = {}


def _patch_payload():
    """Mirror max_completion_tokens into max_tokens on every OpenAI-compatible request (Ollama reads max_tokens)."""
    from tradingagents.llm_clients import openai_client as oc
    cls = oc.NormalizedChatOpenAI
    if getattr(cls, "_ft_max_tokens_patch", False):
        return
    orig = cls._get_request_payload

    def payload(self, input_, *, stop=None, **kwargs):
        p = orig(self, input_, stop=stop, **kwargs)
        mt = p.get("max_completion_tokens") or p.get("max_tokens")
        if mt:
            p["max_tokens"] = mt
            p["max_completion_tokens"] = mt
        return p
    cls._get_request_payload = payload
    cls._ft_max_tokens_patch = True


def install(caps: dict[str, int], log: Callable[[str], None], stats: dict, *, timeout: float = 600,
            presence_penalty: float | None = None, pipe: "Pipeline | None" = None):
    """Patch the agent factories used by GraphSetup and the tier-client factory used by the graph."""
    import tradingagents.graph.setup as setup
    import tradingagents.graph.trading_graph as tg

    _patch_payload()
    for name, (group, label) in FACTORIES.items():
        orig = _ORIG.setdefault(name, getattr(setup, name))
        pp = presence_penalty if group in ("debate", "risk", "trader") else None

        def make(orig=orig, group=group, label=label, pp=pp):
            def factory(llm, *a, **k):
                return orig(_capped(llm, caps[group], label, log, stats, pp, pipe), *a, **k)
            factory.__name__ = orig.__name__
            return factory
        setattr(setup, name, make())

    orig_tier = _ORIG.setdefault("create_tier_client", tg.create_tier_client)

    def tier(config, tier_name, **extra):
        extra.setdefault("timeout", timeout)
        return orig_tier(config, tier_name, **extra)
    tg.create_tier_client = tier


def summary(stats: dict) -> str:
    if not stats:
        return ""
    rows = ["| 節點 | 呼叫 | 輸出 tokens | 秒 | 被截斷 |", "|---|---:|---:|---:|---:|"]
    for k, s in stats.items():
        rows.append(f"| {k} | {s['calls']} | {s['out']:,} | {s['sec']:.0f} | {s['cut'] or ''} |")
    return "\n".join(rows)
