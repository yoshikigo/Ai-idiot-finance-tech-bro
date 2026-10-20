"""FT 市場背景：時點正確、各段落、送進各引擎。"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))


@pytest.fixture()
def br(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import brief, events, fred, market, twmacro, twmarket
    from ft_common.research import store
    monkeypatch.setattr(store, "ROOT", tmp_path / "research")
    monkeypatch.setattr(store, "DB", tmp_path / "research" / "r.sqlite")
    monkeypatch.setattr(store, "_conn", None)
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    store.upsert("insti", [("2330", f"2026-10-{d:02d}", 3_000_000 if d != 16 else -1_000_000, 500_000, 0, 0) for d in range(5, 20)])
    store.upsert("margin", [("2330", f"2026-10-{d:02d}", 20000 + d * 100, 300) for d in range(10, 20)])
    store.upsert("revenue", [("2330", "2026-08", "2026-09-10", 3.3e8, 5.0, 40.0, 0, 38.0),
                             ("2330", "2026-09", "2026-10-10", 3.5e8, 6.1, 42.5, 0, 39.0)])
    idx = pd.date_range("2025-01-01", "2026-10-01", freq="MS")
    monkeypatch.setattr(twmacro, "raw", lambda sid: pd.Series(range(len(idx)), index=idx, dtype=float) + (20 if sid == "SIGNAL" else 1))
    monkeypatch.setattr(fred, "transformed", lambda sid: pd.Series(4.0, index=pd.date_range("2025-01-01", "2026-10-19", freq="D")))
    monkeypatch.setattr(twmarket, "news_zh", lambda q, n, days=14: [{"title": "台積電法說會", "time": "2026-10-16", "publisher": "測試"}])
    monkeypatch.setattr(twmarket, "news_between", lambda q, a, b, n: [{"title": f"舊聞 {b}", "time": b}, {"title": "未來的新聞", "time": "2099-01-01"}])
    monkeypatch.setattr(events, "load_closes", lambda period: {})
    monkeypatch.setattr(events, "macro_actual", lambda k, ref: None)
    monkeypatch.setattr(fred, "fed_decision", lambda d: None)
    events.refresh("2y")
    return brief


def test_sections_as_of(br):
    f = br.flows_section("2330", "2026-10-15")
    assert "2026-10-15" in f and "外資 +3,000" in f and "2026-10-19" not in f  # nothing after the base date
    r = br.revenue_section("2330", "2026-10-05")
    assert "2026-08" in r and "2026-09" not in r  # September revenue becomes available on 10/10
    tm = br.tw_macro_section("2025-06-15")
    assert "2025-06" in tm and "2026" not in tm and "M1B − M2" in tm
    ev = br.events_section("2026-10-15")
    assert "2026-10-19" not in ev.split("【接下來")[0]  # 利劍 (10/19) is after the base date
    assert "美日「利劍 27」" not in ev  # unscheduled future events are never shown
    assert "Fed 利率決策" in ev or "美國" in ev  # scheduled calendars may appear as upcoming
    old = br.news_section("2330.TW", "2025-05-01")
    assert "舊聞 2025-05-01" in old and "未來的新聞" not in old


def test_build_live_and_backtest(br):
    live = br.build("2330.TW")
    assert {"news", "flows", "revenue", "tw_macro", "us_macro", "events"} <= set(live["parts"])
    assert live["text"].startswith("【FT 市場背景】") and "台積電法說會" in live["text"]
    bt = br.build("2330.TW", "2025-06-15")
    assert "sector" not in bt["parts"] and bt["skipped"].get("sector")
    us = br.build("NVDA")
    assert "flows" not in us["parts"] and "tw_macro" not in us["parts"] and "us_macro" in us["parts"]
    many = br.build_many(["2330.TW", "2317.TW"])
    assert "2330.TW" in many["by_symbol"] and "籌碼" in many["by_symbol"]["2330.TW"] and "【台灣總經】" in many["text"]
    en = br.facts_en("2330.TW")
    assert "foreign investors net bought" in en["institutional_flows"] and "taiwan_macro" in en and "monthly_revenue" in en


def test_enrich_job(br, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "terminal"))
    import importlib

    import app as appmod
    importlib.reload(appmod)
    monkeypatch.setattr(appmod.brief, "build", lambda s, d=None, **k: {"text": f"CTX {s} {d}", "parts": ["news"]})
    monkeypatch.setattr(appmod.brief, "build_many", lambda ss, d=None, **k: {"text": "MKT", "by_symbol": {s: "X" for s in ss}, "parts": ["events"]})
    monkeypatch.setattr(appmod.context, "resolve", lambda q: {"台積電": "2330.TW"}.get(q, q))
    b = appmod.enrich_job("tradingagents", {"params": {"ticker": "台積電", "date": "2025-06-02"}})
    assert b["llm"]["context"] == "CTX 2330.TW 2025-06-02" and b["llm"]["context_parts"] == "中文新聞"
    b = appmod.enrich_job("hedgefund", {"params": {"tickers": "2330.TW, AAPL"}})
    assert b["llm"]["context"] == "MKT" and set(b["llm"]["context_by"]) == {"2330.TW", "AAPL"}
    b = appmod.enrich_job("tradingagents", {"params": {"ticker": "2330.TW", "ft_context": False}})
    assert "context" not in b["llm"]
    assert "context" not in appmod.enrich_job("laya", {"params": {"ticker": "2330.TW"}})["llm"]


def test_engines_use_context(monkeypatch):
    spec = importlib.util.spec_from_file_location("hf_worker", ROOT / "engines" / "hedgefund" / "worker.py")
    hf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hf)
    seen = {}
    monkeypatch.setattr(hf.ollama, "chat", lambda cfg, msgs, json_mode=False: seen.setdefault("sys", msgs[0]["content"]) and "{}")
    c = hf.OllamaLLM({"model": "m", "context": "MKT-CTX", "context_by": {"2330.TW": "TSMC-CTX", "AAPL": "AAPL-CTX"}}, "English")
    c.complete("You are Buffett.", "Analyze 2330.TW using these numbers")
    assert "MKT-CTX" in seen["sys"] and "TSMC-CTX" in seen["sys"] and "AAPL-CTX" not in seen["sys"]
    for e in ("tradingagents", "hedgefund", "fingpt", "qlib", "finrl", "timesfm"):
        assert '"ft_context"' in (ROOT / "engines" / e / "worker.py").read_text()
    assert '"use_ft"' in (ROOT / "engines" / "laya" / "worker.py").read_text()
