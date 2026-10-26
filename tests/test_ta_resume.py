"""TradingAgents: reuse finished analyst reports after a failed run (needs tradingagents, e.g. the image)."""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("tradingagents")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"engines/tradingagents/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


resume = _load("resume")
limits = _load("limits")


def test_store_roundtrip_expiry_and_clear(tmp_path):
    st = resume.Store(tmp_path, "2330.TW", "2026-10-26", "Traditional Chinese", hours=12)
    st.save("news", "新聞報告", "job1")
    st.save("fundamentals", "  ", "job1")  # empty reports are not saved
    assert set(st.load()) == {"news"} and st.load()["news"]["job"] == "job1"
    old = resume.Store(tmp_path, "2330.TW", "2026-10-26", "Traditional Chinese", hours=0)
    time.sleep(0.01)
    assert old.load() == {}
    assert resume.Store(tmp_path, "2330.TW", "2026-10-27", "Traditional Chinese").load() == {}
    st.clear()
    assert st.load() == {}


class _Job:
    pipe_rev = 0
    progress = 0
    stage = ""


def test_reused_and_saving_nodes_inside_the_real_analyst_subgraph(tmp_path):
    import tradingagents.graph.setup as setup
    from langchain_core.messages import AIMessage, HumanMessage
    from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS

    lines = []
    limits.install(limits.DEFAULT_CAPS, lines.append, {}, timeout=5)
    # stand-ins for the model-backed analysts so nothing calls Ollama
    setup.create_market_analyst = lambda llm: (lambda s: {"messages": [AIMessage(content="技術報告")], "market_report": "技術報告"})
    st = resume.Store(tmp_path, "2330.TW", "2026-10-26", "zh")
    st.save("news", "上次的新聞報告", "old-job")
    pipe = limits.Pipeline(_Job(), ["market", "news"])
    resume.install(st, st.load(), lines.append, pipe, job_id="new-job")

    state = {"messages": [HumanMessage(content="2330.TW")], "company_of_interest": "2330.TW", "trade_date": "2026-10-26"}
    news = setup._analyst_graph(ANALYST_NODE_SPECS["news"], setup.create_news_analyst(None), 3).invoke(state)
    assert news == {"news_report": "上次的新聞報告"}
    node = [n for st_ in pipe.job.pipeline for n in st_["nodes"] if n["label"] == "新聞分析師"][0]
    assert node["status"] == "done" and node["reused"]
    assert any("沿用" in x for x in lines)

    market = setup._analyst_graph(ANALYST_NODE_SPECS["market"], setup.create_market_analyst(None), 3).invoke(state)
    assert market == {"market_report": "技術報告"}
    assert st.load()["market"]["job"] == "new-job"  # saved the moment it was written
