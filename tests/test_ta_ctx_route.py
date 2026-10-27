"""TradingAgents: FT 市場背景 split by agent role (needs tradingagents, e.g. the image)."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("tradingagents")
_spec = importlib.util.spec_from_file_location("ctx_route", ROOT / "engines/tradingagents/ctx_route.py")
ctx_route = importlib.util.module_from_spec(_spec)
sys.modules["ctx_route"] = ctx_route
_spec.loader.exec_module(ctx_route)

SECTIONS = {"news": "【中文新聞】台積電法說", "flows": "【籌碼】外資買超", "revenue": "【月營收】年增 30%",
            "valuation": "【估值】本益比 20", "tw_macro": "【台灣總經】景氣燈號 綠燈", "us_macro": "【美國總經】CPI 3%",
            "events": "【大事】FOMC 11/5", "others": "【其他 AI】Kronos 看多", "sector": "【產業】半導體 +1%"}


def test_role_texts_route_sections():
    t = ctx_route.role_texts("【FT 市場背景】", SECTIONS, ["market", "social", "news", "fundamentals"],
                             world="【世界訊號】台海平靜", lessons="- 別追高")
    assert "外資買超" in t["market"] and "法說" not in t["market"] and "月營收" not in t["market"]
    assert "法說" in t["social"] and "CPI" not in t["social"]
    assert "CPI" in t["news"] and "景氣燈號" in t["news"] and "台海" in t["news"] and "月營收" not in t["news"]
    assert "月營收" in t["fundamentals"] and "本益比" in t["fundamentals"] and "法說" not in t["fundamentals"]
    for late in ("researcher", "manager", "risk"):
        assert "Kronos 看多" in t[late] and "別追高" in t[late] and "法說" not in t[late] and "月營收" not in t[late]
    assert "台海" in t["risk"] and "台海" not in t["researcher"]
    # every analyst text is far smaller than the whole background
    whole = sum(len(v) for v in SECTIONS.values())
    assert all(len(t[a]) < whole for a in ("market", "social", "fundamentals"))


def test_unselected_analyst_sections_go_to_managers():
    t = ctx_route.role_texts("H", SECTIONS, ["market"])
    assert "月營收" in t["manager"] and "CPI" in t["manager"] and "法說" in t["manager"]
    assert "news" not in t and "月營收" not in t["market"]


def test_install_patches_each_module_and_restores():
    from tradingagents.agents.analysts import market_analyst, news_analyst
    from tradingagents.agents.managers import portfolio_manager
    state = {"instrument_context": "The instrument to analyze is `2330.TW`.", "company_of_interest": "2330.TW"}
    t = ctx_route.role_texts("H", SECTIONS, ["market", "news"])
    ctx_route.install(t)
    assert "外資買超" in market_analyst.get_instrument_context_from_state(state)
    assert "CPI" in news_analyst.get_instrument_context_from_state(state)
    assert "外資買超" not in news_analyst.get_instrument_context_from_state(state)
    pm = portfolio_manager.get_instrument_context_from_state(state)
    assert pm.startswith("The instrument to analyze") and "Kronos 看多" in pm
    ctx_route.install(None)
    assert market_analyst.get_instrument_context_from_state(state) == state["instrument_context"]


def test_real_market_analyst_prompt_carries_only_its_part():
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.runnables import RunnableLambda
    from tradingagents.agents.analysts.market_analyst import create_market_analyst
    seen = []

    class FakeLLM:
        def bind_tools(self, tools):
            return RunnableLambda(lambda pv: seen.append(pv.to_string()) or AIMessage(content="技術面報告"))
    ctx_route.install(ctx_route.role_texts("H", SECTIONS, ["market", "news"]))
    try:
        node = create_market_analyst(FakeLLM())
        out = node({"messages": [HumanMessage(content="2330.TW")], "company_of_interest": "2330.TW", "trade_date": "2026-10-27",
                    "instrument_context": "The instrument to analyze is `2330.TW`."})
    finally:
        ctx_route.install(None)
    assert out["market_report"] == "技術面報告"
    assert "外資買超" in seen[0] and "CPI" not in seen[0] and "法說" not in seen[0]
