"""分析紀錄：收集、事後核對、統計、檢討與回饋。"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))


def _job(eng, jid, day, ticker, signal, result_extra=None, params=None, llm=None):
    meta = {"date": day, "ticker": ticker, "tickers": [ticker], "name": "", "signal": signal, "price_at": 100.0}
    r = {"summary": f"{ticker} {signal}", "signal": signal, "markdown": f"# 報告\n因為 AI 需求強勁所以{signal}", "meta": meta}
    r.update(result_extra or {})
    return {"id": jid, "engine": eng, "status": "done", "created": f"{day}T09:00:00+08:00", "started": f"{day}T09:00:01+08:00",
            "finished": f"{day}T09:20:00+08:00", "params": params or {"ticker": ticker}, "llm": llm or {"model": "qwen-test"},
            "result": r}


@pytest.fixture()
def L(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import ledger, market
    days = pd.bdate_range("2026-06-01", periods=120)
    up = pd.DataFrame({"Close": [100 + i for i in range(120)]}, index=days)          # steadily rising
    down = pd.DataFrame({"Close": [100 - i * 0.5 for i in range(120)]}, index=days)  # steadily falling
    flat = pd.DataFrame({"Close": [100.0] * 120}, index=days)
    data = {"2330.TW": up, "2317.TW": down, "0050.TW": flat, "NVDA": up, "^GSPC": flat}

    def hist(sym, *a, start=None, end=None, **k):
        df = data[sym]
        return df[(df.index >= pd.Timestamp(start)) & (df.index < pd.Timestamp(end))] if start else df
    monkeypatch.setattr(market, "history", hist)
    monkeypatch.setattr(ledger.notes, "_name", lambda t: {"2330.TW": "台積電", "2317.TW": "鴻海"}.get(t, ""))
    jobs = [
        _job("tradingagents", "J1", "2026-06-05", "2330.TW", "Buy", llm={"model": "qwen-test", "lessons": "- x"}),  # right
        _job("tradingagents", "J2", "2026-06-05", "2317.TW", "Overweight"),                                         # wrong
        _job("tradingagents", "J3", "2026-06-08", "2317.TW", "Sell"),                                               # right
        _job("fingpt", "F1", "2026-06-05", "NVDA", "看多", params={"ticker": "NVDA", "mode": "forecast"}),
        _job("timesfm", "T1", "2026-06-05", "2330.TW", "看多", result_extra={"meta": {
            "date": "2026-06-05", "ticker": "2330.TW", "price_at": 104.0, "signal": "看多", "horizon_end": "2026-07-03",
            "forecast_median": 120.0, "forecast_p10": 110.0, "forecast_p90": 130.0, "forecast_change_pct": 15.0}}),
        _job("hedgefund", "H1", "2026-06-05", "0050.TW", "看多", result_extra={"meta": {
            "date": "2026-06-05", "ticker": None, "signals": {"2330.TW": "看多", "2317.TW": "看空"},
            "prices_at": {"2330.TW": 104.0, "2317.TW": 98.0}}}),
        {"id": "Q1", "engine": "qlib", "status": "done", "created": "2026-06-05T10:00:00", "params": {"universe": "tw50"},
         "result": {"summary": "IC 0.03", "meta": {"date": "2026-06-05", "ticker": None}}},
        {**_job("tradingagents", "J9", "2026-06-05", "2330.TW", "Buy"), "status": "error"},  # failed jobs are ignored
    ]
    for j in jobs:
        d = tmp_path / j["engine"] / "jobs"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{j['id']}.json").write_text(json.dumps(j, ensure_ascii=False))
    return ledger


def test_collect_and_check(L):
    assert L.collect() == 8  # 3 TA + FinGPT + TimesFM + 2 rows for 投資大師 + Qlib (logged, not scored)
    assert L.collect() == 0  # idempotent
    n = L.check(today=dt.date(2026, 11, 30))
    assert n > 0
    rec = {r["id"]: r for r in L.records()}
    j1, j2, j3 = rec["tradingagents:J1"], rec["tradingagents:J2"], rec["tradingagents:J3"]
    assert j1["checks"]["5"]["hit"] == 1 and j1["checks"]["5"]["ret"] > 0 and j1["checks"]["5"]["excess"] > 0
    assert j2["checks"]["20"]["hit"] == 0 and j2["checks"]["20"]["excess"] < 0
    assert j3["checks"]["60"]["hit"] == 1 and j3["lessons_used"] == 0 and j1["lessons_used"] == 1
    t = rec["timesfm:T1"]["checks"]["0"]
    assert t["due"] <= "2026-07-03" and t["in_band"] in (0, 1) and t["err"] is not None
    assert rec["hedgefund:H1:2317.TW"]["dir"] == -1 and rec["hedgefund:H1:2317.TW"]["checks"]["5"]["hit"] == 1
    assert rec["qlib:Q1"]["scored"] is False and rec["qlib:Q1"]["checks"] == {}
    assert "tradingagents:J9" not in rec


def test_not_due_yet(L):
    L.collect()
    L.check(today=dt.date(2026, 6, 9))  # 2 trading days after: nothing can be judged yet
    r = {x["id"]: x for x in L.records()}["tradingagents:J1"]
    assert r["checks"] == {} and set(r["due_est"]) == {"5", "20", "60"}


def test_stats_review_and_lessons(L, monkeypatch):
    L.collect()
    L.check(today=dt.date(2026, 11, 30))
    st = L.stats()["tradingagents"]
    assert st["n"] == 3 and st["h"]["5"]["n"] == 3 and st["h"]["5"]["hits"] == 2
    assert L.due_for_review("tradingagents", min_new=3)
    seen = {}
    from ft_common import ollama

    def fake_ask(llm, system, user, **kw):
        seen["user"] = user
        return json.dumps({"summary": "5 日命中 2/3。", "patterns": ["對鴻海太樂觀"], "lessons": ["下跌趨勢中不要只看 AI 題材就給加碼",
                           "引用價格前先確認趨勢"], "caveats": "樣本只有 3 筆"}, ensure_ascii=False)
    monkeypatch.setattr(ollama, "ask", fake_ask)
    rv = L.write_review("tradingagents", {"model": "m"})
    assert "鴻海" in seen["user"] and "✘" in seen["user"] and "命中 2/3" in seen["user"]
    assert rv["lessons"][0].startswith("下跌趨勢") and "樣本只有 3 筆" in rv["summary"] and rv["n"] == 3
    txt = L.lessons_text("tradingagents")
    assert "- 下跌趨勢中" in txt and "3 筆" in txt
    assert not L.due_for_review("tradingagents", min_new=1)  # just reviewed
    with pytest.raises(ValueError):
        L.write_review("finrl", {"model": "m"})
    assert "AI 分析紀錄" in L.text_report()


def test_lessons_ride_along_to_the_engine(L, tmp_path, monkeypatch):
    L.collect()
    L.check(today=dt.date(2026, 11, 30))
    from ft_common import ollama
    monkeypatch.setattr(ollama, "ask", lambda *a, **k: json.dumps({"summary": "s", "patterns": [], "lessons": ["別追高"]}))
    L.write_review("tradingagents", {"model": "m"})
    monkeypatch.setenv("FT_WORLD", "0")
    sys.path.insert(0, str(ROOT / "terminal"))
    import importlib
    import app as appmod
    importlib.reload(appmod)
    sent = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"id": "x"}

    async def fake_post(self, url, json=None, **k):
        sent.append(json)
        return FakeResp()
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    c.post("/api/engines/tradingagents/jobs", json={"params": {"ticker": "2330"}})
    assert "別追高" in sent[-1]["llm"]["lessons"]
    c.post("/api/engines/timesfm/jobs", json={"params": {"ticker": "2330"}})
    assert "lessons" not in sent[-1]["llm"]  # not an LLM agent
    assert c.put("/api/ledger/feedback", json={"tradingagents": False}, headers={"x-ft": "1"}).json()["tradingagents"] is False
    c.post("/api/engines/tradingagents/jobs", json={"params": {"ticker": "2330"}})
    assert "lessons" not in sent[-1]["llm"]  # switched off
    d = c.get("/api/ledger").json()
    assert d["stats"]["tradingagents"]["n"] == 3 and d["latest"]["tradingagents"]["lessons"] == ["別追高"]
    t = c.get("/api/agent/ledger").text
    assert "TradingAgents" in t and "別追高" in t and "5日命中 2/3" in t
