"""Give each TradingAgents role only the part of the FT 市場背景 it uses.

Before: the whole FT 市場背景 (up to ~6000 Chinese characters), the God's Eye View block and the review
lessons were appended to the instrument context, which *every* agent re-reads on *every* call — four
analysts with their tool rounds, bull/bear, research manager, trader, three risk debators and the
portfolio manager: 20–30 calls each re-reading the same 5k tokens. On a local 27B model that prefill alone
is minutes, and it pushes single calls past the request timeout.

Now (the idea from Blave Agent: load a reference only where it is needed):
  技術分析師   籌碼、產業強弱、重大事件與日程
  情緒分析師   中文新聞、籌碼
  新聞分析師   中文新聞、台灣／美國總經、FOMC、匯率、重大事件、世界訊號
  基本面分析師 月營收、官方估值與財報、ETF 資料、供應鏈、產業強弱、SEC 申報
  研究員、研究經理、交易員、投資組合經理   其他 AI 的結論＋過去檢討（事實已在分析師報告裡）
  三位風控     其他 AI 的結論＋世界訊號＋過去檢討
A section whose analyst was not selected goes to the research manager / trader / portfolio manager
instead, so nothing the terminal collected is dropped.

The state's instrument_context stays the plain ticker identity; each agent module's
get_instrument_context_from_state is swapped for one that appends that role's text.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable

ANALYST_SECTIONS = {
    "market": ("flows", "sector", "events"),
    "social": ("news", "flows"),
    "news": ("news", "tw_macro", "us_macro", "fomc", "fx", "events", "world"),
    "fundamentals": ("revenue", "valuation", "etf", "supply", "sector", "sec"),
}
LATE_SECTIONS = ("others",)
# role → agent modules that read the instrument context
ROLE_MODULES = {
    "market": ["tradingagents.agents.analysts.market_analyst"],
    "social": ["tradingagents.agents.analysts.sentiment_analyst"],
    "news": ["tradingagents.agents.analysts.news_analyst"],
    "fundamentals": ["tradingagents.agents.analysts.fundamentals_analyst"],
    "researcher": ["tradingagents.agents.researchers.bull_researcher", "tradingagents.agents.researchers.bear_researcher"],
    "manager": ["tradingagents.agents.managers.research_manager", "tradingagents.agents.trader.trader",
                "tradingagents.agents.managers.portfolio_manager"],
    "risk": ["tradingagents.agents.risk_mgmt.aggressive_debator", "tradingagents.agents.risk_mgmt.neutral_debator",
             "tradingagents.agents.risk_mgmt.conservative_debator"],
}
ROLE_LABEL = {"market": "技術分析師", "social": "情緒分析師", "news": "新聞分析師", "fundamentals": "基本面分析師",
              "researcher": "多空研究員", "manager": "研究經理／交易員／投資組合經理", "risk": "風控"}
MAX_ROLE_CHARS = 4500
_ORIG: dict[str, Callable] = {}


def _join(head: str, sections: dict[str, str], keys, cap: int = MAX_ROLE_CHARS) -> str:
    body = "\n\n".join(sections[k] for k in dict.fromkeys(keys) if sections.get(k))
    if not body:
        return ""
    if len(body) > cap:
        body = body[:cap].rsplit("\n", 1)[0] + "\n…（已截斷）"
    return f"{head}\n\n{body}" if head else body


def role_texts(head: str, sections: dict[str, str], analysts, *, world: str = "", lessons: str = "",
               rules: str = "") -> dict[str, str]:
    """→ {role: extra text appended to that role's instrument context}."""
    sections = dict(sections or {})
    if world and not sections.get("world"):
        sections["world"] = world
    chosen = [a for a in analysts if a in ANALYST_SECTIONS]
    covered = {k for a in chosen for k in ANALYST_SECTIONS[a]}
    leftover = [k for k in sections if k not in covered and k not in LATE_SECTIONS and k != "world"]
    out = {a: _join(head, sections, ANALYST_SECTIONS[a]) for a in chosen}
    les = ("LESSONS FROM REVIEWING YOUR PAST CALLS AGAINST ACTUAL PRICES (FT review system; avoid repeating these mistakes):\n"
           + lessons) if lessons else ""
    late = _join(head, sections, LATE_SECTIONS)
    out["researcher"] = "\n\n".join(x for x in (late, les) if x)
    # the research manager, trader and portfolio manager write the conclusions → they also get the writing rules
    out["manager"] = "\n\n".join(x for x in (_join(head, sections, (*LATE_SECTIONS, *leftover)), les, rules.strip()) if x)
    out["risk"] = "\n\n".join(x for x in (late, sections.get("world", ""), les) if x)
    return out


def install(texts: dict[str, str] | None):
    """Patch (or, with None, restore) get_instrument_context_from_state in every agent module."""
    for role, mods in ROLE_MODULES.items():
        extra = (texts or {}).get(role, "")
        for name in mods:
            try:
                mod = importlib.import_module(name)
            except ImportError:
                continue
            orig = _ORIG.setdefault(name, getattr(mod, "get_instrument_context_from_state", None))
            if orig is None:
                continue
            if texts is None or not extra:
                mod.get_instrument_context_from_state = orig
                continue

            def ctx(state, _orig=orig, _extra=extra):
                return f"{_orig(state)}\n\n{_extra}"
            mod.get_instrument_context_from_state = ctx


def summary(texts: dict[str, str]) -> str:
    return "、".join(f"{ROLE_LABEL.get(r, r)} {len(t)} 字" for r, t in texts.items() if t)
