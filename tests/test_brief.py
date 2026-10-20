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


def test_batch_sections(br, tmp_path, monkeypatch):
    from ft_common import fx, ledger, ollama
    from ft_common.research import store
    store.upsert("valuation", [("2330", f"2026-{m:02d}-15", 15 + m, 2.0, 5 + m / 10) for m in range(1, 11)])
    store.upsert("income_ytd", [("2330", "2026Q2", "2026-08-14", 1.6e9, 0.95e9, 0.8e9, 0, 0.6e9, 0.6e9, 24.5),
                                ("2330", "2026Q3", "2026-11-14", 2.5e9, 1.5e9, 1.2e9, 0, 1e9, 1e9, 38.0)])
    v = br.valuation_section("2330.TW", "2330", "2026-10-15")
    assert "本益比 25" in v and "2026Q2" in v and "2026Q3" not in v  # Q3 not yet available on 10/15
    idx = pd.bdate_range("2025-01-01", "2026-10-19")
    monkeypatch.setattr(fx, "series", lambda code, period="1y": pd.Series(30.0 + pd.RangeIndex(len(idx)) / 100, index=idx))
    f = br.fx_section("2026-06-30")
    assert "2026-06-30" in f and "USD" in f and "1 年" in f and "2026-07" not in f
    with ledger._conn() as c:
        for i, (d, eng) in enumerate((("2026-10-10", "tradingagents"), ("2026-10-12", "timesfm"), ("2026-10-18", "hedgefund"))):
            c.execute("INSERT INTO analyses(id, engine, job_id, created, date, ticker, name, signal, summary) VALUES(?,?,?,?,?,?,?,?,?)",
                      (f"a{i}", eng, f"j{i}", d, d, "2330.TW", "台積電", "Buy", f"結論 {eng}"))
    o = br.others_section("2330.TW", "2026-10-15", exclude_engine="tradingagents")
    assert "結論 timesfm" in o and "hedgefund" not in o and "tradingagents" not in o  # no future calls, no self
    sc = br.supply_section("2330.TW")
    assert "環節" in sc and "上游" in sc
    etf = br.etf_section("0056.TW", None)
    assert "折溢價" in etf and "成分股" in etf and "配息" in etf
    fo = br.fomc_section("2026-10-15")
    assert "FOMC（2026-09-16）" in fo and "措辭變化" in fo
    full = br.build("2330.TW", exclude=frozenset({"world"}), engine="tradingagents")
    assert {"fx", "valuation", "supply", "fomc"} <= set(full["parts"]) and "world" not in full["parts"]
    assert "others" not in full["parts"] or "tradingagents" not in full["text"]
    e = br.build("0056.TW")
    assert "etf" in e["parts"] and "revenue" not in e["parts"]
    monkeypatch.setattr(ollama, "ask", lambda *a, **k: '{"headlines": ["TSMC earnings call upbeat"]}')
    assert br.tw_headlines_en("2330.TW") == ["TSMC earnings call upbeat"]
    en = br.facts_en("2330.TW")
    assert "taiwan_dollar" in en and "valuation_tw" in en


def test_enrich_excludes_what_engines_have(br, monkeypatch):
    sys.path.insert(0, str(ROOT / "terminal"))
    import importlib

    import app as appmod
    importlib.reload(appmod)
    got = {}
    monkeypatch.setattr(appmod.brief, "build", lambda s, d=None, **k: got.update(k) or {"text": "T", "parts": []})
    appmod.enrich_job("tradingagents", {"params": {"ticker": "2330.TW"}})
    assert got["exclude"] == {"world", "news_us"} and got["engine"] == "tradingagents"
    appmod.enrich_job("fingpt", {"params": {"ticker": "2330.TW"}})
    assert got["exclude"] == {"news", "news_us"}
