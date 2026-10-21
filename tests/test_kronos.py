"""Kronos engine: sampled-path bands, backtest, report, API, ledger scoring, terminal wiring.

The model is replaced by a deterministic path generator (no torch needed). When torch and the upstream
Kronos source are available (KRONOS_SRC=/path/to/Kronos), one test runs the real code path with tiny
random weights (KRONOS_RANDOM_INIT=1).
"""
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))


@pytest.fixture
def K(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("FT_FAKE_DATA", "1")
    monkeypatch.setenv("KRONOS_PRELOAD", "0")
    import ft_common.engine as eng
    import ft_common.market as market
    import ft_common.notes as notes
    importlib.reload(market)
    importlib.reload(eng)
    importlib.reload(notes)
    spec = importlib.util.spec_from_file_location("kronos_worker", ROOT / "engines" / "kronos" / "worker.py")
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    return w


def _fake_paths(drift=0.002):
    def f(pred, df, x_ts, y_ts, h, n, T=1.0, top_p=0.9, seed=0):
        last = float(df["close"].iloc[-1])
        out = np.zeros((n, h, 6))
        for k in range(n):
            g = np.cumprod(np.full(h, 1 + drift + (k - n / 2) * 0.001))
            c = last * g
            out[k, :, 0], out[k, :, 3] = c / 1.002, c
            out[k, :, 1], out[k, :, 2] = c * 1.01, c * 0.985
            out[k, :, 4] = 1e6
        return out
    return f


def test_forecast_band_and_backtest(K, monkeypatch):
    monkeypatch.setattr(K, "load", lambda name: object())
    monkeypatch.setattr(K, "sample_paths", _fake_paths())
    r = K.forecast("2330.TW", 20, "small", 300, 20, backtest=True)
    sm = r["summary"]
    assert len(r["forecast"]) == 20 and r["samples"] == 20 and r["model"].startswith("Kronos-small")
    assert sm["p10_end"] < sm["median_end"] < sm["p90_end"] and 0 <= sm["p_up"] <= 1
    for x in r["forecast"]:  # median-path candles are valid bars
        assert x["low"] <= min(x["open"], x["close"]) <= max(x["open"], x["close"]) <= x["high"]
    assert r["forecast"][0]["date"] > r["last_date"]
    bt = r["backtest"]
    assert {"mape", "naive_mape", "coverage80", "direction_hit"} <= set(bt) and len(bt["points"]) == 20
    assert r["context"] == 300 and len(r["history"][0]) == 5


def test_job_report_and_api(K, monkeypatch):
    monkeypatch.setattr(K, "load", lambda name: object())
    monkeypatch.setattr(K, "sample_paths", _fake_paths())
    seen = {}
    monkeypatch.setattr(K.ollama, "ask", lambda llm, sys_, user, language=None: seen.setdefault("u", user) and "- 解讀")
    ctx = type("C", (), {"params": {"ticker": "2330.TW", "horizon": 10, "samples": 8, "backtest": True},
                         "llm": {"context": "【FT 市場背景】測試", "context_parts": "台灣總經"},
                         "step": lambda self, *a: None, "log": lambda self, *a: None})()
    out = K.run(ctx)
    fs = out["forecast_summary"]
    assert fs["horizon_end"] and fs["last"] and out["signal"] in ("看多", "看空", "中性")
    assert "Kronos" in out["markdown"] and "FT 市場背景" in seen["u"] and "逐日預測 K 棒（中位路徑）" in [t["title"] for t in out["tables"]]
    from fastapi.testclient import TestClient
    c = TestClient(K.app)
    d = c.get("/forecast", params={"symbol": "0050.TW", "horizon": 5, "samples": 6}).json()
    assert len(d["forecast"]) == 5 and d["summary"]["band_pct"] >= 0
    assert c.get("/meta").json()["name"] == "kronos"


def test_ledger_scores_kronos_like_timesfm(K, tmp_path):
    from ft_common import ledger, notes
    job = {"id": "k1", "engine": "kronos", "status": "done", "params": {"ticker": "2330.TW", "date": "2026-10-01"},
           "created": "2026-10-01T10:00:00", "finished": "2026-10-01T10:01:00",
           "result": {"signal": "看多", "summary": "2330.TW 20日 +3%", "last": 100.0,
                      "forecast_summary": {"horizon_end": "2026-10-29", "median_end": 103.0, "p10_end": 95.0, "p90_end": 110.0,
                                           "change_pct": 3.0, "p_up": 0.7, "last": 100.0, "last_date": "2026-10-01"}}}
    meta = notes.job_meta(job)
    assert meta["horizon_end"] == "2026-10-29" and meta["forecast_p90"] == 110.0
    job["result"]["meta"] = meta
    rows = ledger._rows_from_job(job)  # noqa: SLF001
    assert rows[0]["dir"] == 1 and json.loads(rows[0]["fc"])["forecast_p10"] == 95.0
    assert "kronos" in ledger.ENGINES and notes.ENGINE_LABEL["kronos"] == "Kronos"


def test_terminal_wiring(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    sys.path.insert(0, str(ROOT / "terminal"))
    import app as appmod
    importlib.reload(appmod)
    assert appmod.ENGINES["kronos"].endswith(":8139") and "kronos" in appmod.CTX_ENGINES
    monkeypatch.setattr(appmod.brief, "build", lambda s, d=None, **k: {"text": f"CTX {s}", "parts": ["fx"]})
    b = appmod.enrich_job("kronos", {"params": {"ticker": "2330.TW"}})
    assert b["llm"]["context"] == "CTX 2330.TW"


@pytest.mark.skipif(not os.environ.get("KRONOS_SRC"), reason="set KRONOS_SRC to an upstream Kronos checkout (needs torch)")
def test_real_kronos_code_random_weights(K, monkeypatch):
    pytest.importorskip("torch")
    sys.path.insert(0, os.environ["KRONOS_SRC"])
    monkeypatch.setenv("KRONOS_RANDOM_INIT", "1")
    r = K.forecast("2330.TW", 10, "small", 200, 6, backtest=True)
    assert len(r["forecast"]) == 10 and r["summary"]["p10_end"] <= r["summary"]["p90_end"] and "backtest" in r
