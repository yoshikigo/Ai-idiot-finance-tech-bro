"""TradingAgents output caps / retry policy (runs only where tradingagents is installed, e.g. the ft-tradingagents image)."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("tradingagents")
# load by path: engines/*/worker.py share module names, so keep engines/tradingagents off sys.path
_spec = importlib.util.spec_from_file_location("ta_limits", ROOT / "engines/tradingagents/limits.py")
limits = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(limits)


def test_caps_from_params_and_env(monkeypatch):
    monkeypatch.setenv("FT_TA_MAX_TOKENS_MANAGER", "5000")
    c = limits.caps_from({"max_tokens_debate": 2048, "max_tokens_report": 6000})
    assert c == {"report": 6000, "debate": 2048, "risk": 2048, "trader": 2048, "manager": 5000}
    assert limits.caps_from({})["debate"] == 3072
    assert limits.caps_from({"max_tokens_debate": 10})["debate"] == 256  # floor


def test_payload_sends_max_tokens_for_ollama():
    from tradingagents.llm_clients import openai_client as oc
    limits._patch_payload()
    llm = oc.NormalizedChatOpenAI(model="m", api_key="x", base_url="http://127.0.0.1:1/v1", max_tokens=1234)
    p = llm._get_request_payload([("user", "hi")])
    assert p["max_tokens"] == 1234 and p["max_completion_tokens"] == 1234


def test_install_patches_every_node_factory():
    import tradingagents.graph.setup as setup
    stats, lines = {}, []
    limits.install({"report": 100, "debate": 200, "risk": 300, "trader": 400, "manager": 500}, lines.append, stats, timeout=5)
    for name in limits.FACTORIES:
        assert getattr(setup, name).__name__ == limits._ORIG[name].__name__
        assert getattr(setup, name) is not limits._ORIG[name]


def test_pipeline_tracks_running_node_and_tool_wait():
    class J:
        pipeline, pipe_rev, progress, stage = None, 0, 10.0, ""
    j = J()
    p = limits.Pipeline(j, ["market", "news"])
    assert [n["label"] for n in j.pipeline[0]["nodes"]] == ["技術分析師", "新聞分析師"]
    p.start("技術分析師")
    assert "LLM 生成中" in j.stage
    p.end("技術分析師", 120, 3.0, False, tool_calls=True)   # asked for a tool → still running (fetching data)
    assert j.pipeline[0]["nodes"][0]["status"] == "running" and "工具／資料抓取中" in j.stage
    p.end("新聞分析師", 0, 0, False, False)  # unknown order is tolerated
    p.start("技術分析師"); p.end("技術分析師", 900, 20.0, False, tool_calls=False)
    assert j.pipeline[0]["nodes"][0]["status"] == "done" and j.pipeline[0]["nodes"][0]["out"] == 1020
    p.start("多方研究員")
    assert j.pipeline[1]["nodes"][0]["status"] == "running"
    p.end("多方研究員", 3072, 90.0, True, False)
    assert j.pipeline[1]["nodes"][0]["status"] == "cut"
    p.start("空方研究員"); p.error("空方研究員", "Timeout")
    assert j.pipeline[1]["nodes"][1]["status"] == "error" and j.progress > 10
