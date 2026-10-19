"""Laya engine tests.

Pure-Python parts always run. The end-to-end part runs the real @receptron/laya package through the
Node sidecar against a stand-in ONNX bundle (tests/make_fake_laya.py); it is skipped unless
LAYA_TEST_MODEL_DIR (the fake bundle) and LAYA_SIDECAR (server.mjs with node_modules) are set.
"""
import datetime as dt
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "engines/laya"))


@pytest.fixture
def W(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("FT_FAKE_DATA", "1")
    monkeypatch.setenv("LAYA_PRELOAD", "0")
    if os.environ.get("LAYA_TEST_MODEL_DIR"):
        monkeypatch.setenv("LAYA_MODEL_DIR", os.environ["LAYA_TEST_MODEL_DIR"])
    import ft_common.engine as eng
    import ft_common.market as market
    import ft_common.notes as notes
    importlib.reload(market)
    importlib.reload(eng)
    importlib.reload(notes)
    import worker
    w = importlib.reload(worker)
    monkeypatch.setattr(w, "timesfm_line", lambda sym, h: None)  # no TimesFM engine in tests
    yield w
    if w.SC.proc and w.SC.proc.poll() is None:
        w.SC.proc.kill()


def _df(n=300, drift=0.001, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    idx = pd.bdate_range(end="2026-10-16", periods=n)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": rng.integers(1e6, 2e6, n)}, index=idx)


def test_technicals_basic(W):
    t = W.technicals(_df())
    assert 0 <= t["rsi"] <= 100
    assert t["hi52"] <= 0 <= t["lo52"]
    assert t["dd60"] <= 0 and t["vol20"] > 0
    up = W.technicals(pd.DataFrame({"Close": np.linspace(50, 100, 300), "Volume": np.ones(300)}, index=pd.bdate_range(end="2026-10-16", periods=300)))
    assert up["rsi"] > 90 and up["ret"]["20d"] > 0


def test_questions_shape(W):
    q = W.questions(20)
    assert list(q["action"]["criteria"]) == ["buy", "hold", "sell", "short", "wait"]
    assert len(q["outlook"]["criteria"]) == 5 and q["up"]["type"] == q["drop"]["type"] == "noul"
    assert "8%" in q["drop"]["instructions"]
    assert "15%" in W.questions(60)["outlook"]["criteria"][0]


def test_state_is_english_and_ordered(W):
    st, facts = W.build_state("2330.TW", W.today(), 20)
    keys = list(st)
    assert keys[:4] == ["instrument", "as_of", "decision_horizon", "price"]  # truncation keeps the start
    assert all(ord(ch) < 0x3000 for ch in json.dumps(st, ensure_ascii=False)), "state must stay English for the English checkpoint"
    assert "RSI14" in st["momentum"] and "next 20 trading days" == st["decision_horizon"]
    assert facts["thresholds"] == (3.0, 8.0)


def test_past_date_skips_news_and_timesfm(W, monkeypatch):
    called = []
    monkeypatch.setattr(W, "timesfm_line", lambda s, h: called.append(1) or "x")
    st, _ = W.build_state("2330.TW", (dt.date.today() - dt.timedelta(days=30)).isoformat(), 20)
    assert not called and "recent_headlines" not in st


def test_agent_lines_pick_latest_per_engine(W, tmp_path):
    d = tmp_path / "data/tradingagents/jobs"
    d.mkdir(parents=True)
    day = W.today()
    for i, sig in enumerate(["Underweight", "Overweight"]):
        (d / f"ta{i}.json").write_text(json.dumps({"id": f"ta{i}", "status": "done", "created": day,
                                                    "result": {"signal": sig, "meta": {"date": day, "ticker": "2330.TW", "signal": sig}}}))
    h = tmp_path / "data/hedgefund/jobs"
    h.mkdir(parents=True)
    (h / "hf.json").write_text(json.dumps({"id": "hf", "status": "done", "created": day,
                                          "result": {"meta": {"date": day, "signals": {"2330.TW": "看多", "AAPL": "看空"}}}}))
    lines = W.agent_lines("2330.TW", day)
    assert any(x.startswith("TradingAgents") for x in lines)
    assert any("bullish" in x for x in lines)
    assert len([x for x in lines if x.startswith("TradingAgents")]) == 1


def test_world_lines_english(W, tmp_path):
    from ft_common import world
    world = importlib.reload(world)
    with world._conn() as c:
        c.execute("INSERT INTO events(ts,date,region,kind,severity,title,detail) VALUES(?,?,?,?,?,?,?)",
                  (W.today() + "T01:00:00+00:00", W.today(), "taiwan", "quake", 6.8, "台灣 M6.8 地震", ""))
    lines = W.world_lines("2330.TW", W.today())
    assert lines == [f"{W.today()} Taiwan Strait: M6.8 earthquake"]


def test_notes_direction_understands_laya_labels():
    from ft_common import notes
    assert notes.direction("買進") == 1 and notes.direction("放空") == -1 and notes.direction("賣出") == -1
    assert notes.direction("觀望") == 0 and notes.direction("持有") == 0


def _fake_decision(W):
    probs = {"buy": .41, "hold": .22, "sell": .12, "short": .05, "wait": .20}
    return {"symbol": "2330.TW", "name": "台積電", "as_of": "2026-10-16", "horizon": 20, "last": 1000.0,
            "action": {"choice": "buy", "label": "買進", "probabilities": probs, "labels": {k: v[1] for k, v in W.ACTIONS.items()},
                       "answer_confidence": .41, "confidence": .1},
            "net": .24, "outlook": {"score": 2.3, "probabilities": [.05, .15, .4, .3, .1], "labels": W.OUTLOOK_ZH, "bins": list("abcde")},
            "p_up": .63, "p_drop": .18, "drop_threshold": 8.0, "tokens": 900, "truncated": False, "ms": 120,
            "state": {"instrument": "x"}, "facts": {"agents": ["TradingAgents (2026-10-15): Overweight"], "world": [], "timesfm": None},
            "model": "Laya"}


def test_report(W):
    r = W.report(_fake_decision(W))
    assert r["signal"] == "買進" and r["summary"].startswith("2330.TW 台積電 20日：買進 41%")
    assert "多空淨值 +24%" in r["markdown"] and "不是**統計上的漲跌機率" in r["markdown"]
    assert r["decision"]["action"]["probabilities"]["short"] == .05 and "state" not in r["decision"]
    assert r["tables"][0]["rows"][0] == ["買進", "41.0%"]


@pytest.mark.skipif(not (os.environ.get("LAYA_TEST_MODEL_DIR") and os.environ.get("LAYA_SIDECAR") and shutil.which("node")),
                    reason="needs node, the sidecar with node_modules and a (fake) Laya bundle")
def test_end_to_end_through_node_sidecar(W):
    W.SC.load()
    assert W.SC.state["status"] == "ready" and W.SC.state["fake"]
    d = W.decide("2330.TW", W.today(), 20)
    p = d["action"]["probabilities"]
    assert set(p) == set(W.ACTIONS) and abs(sum(p.values()) - 1) < 2e-3
    assert d["action"]["label"] == W.ACTIONS[d["action"]["choice"]][1]
    assert abs(sum(d["outlook"]["probabilities"]) - 1) < 2e-3 and 0 <= d["p_up"] <= 1 and 0 <= d["p_drop"] <= 1
    assert d["tokens"] > 100 and "假模型" in d["model"]
    d5 = W.decide("2330.TW", W.today(), 5)
    assert d5["outlook"]["bins"][0] == "< -4%"
    # job path + Obsidian note
    from ft_common.engine import Ctx, Job
    j = Job("laya", {"ticker": "2330", "horizon": "60", "date": "today"}, None)
    res = W.run(Ctx(j, W.engine))
    assert res["decision"]["horizon"] == 60 and res["signal"] in {v[1] for v in W.ACTIONS.values()}
