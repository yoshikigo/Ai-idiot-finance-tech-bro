"""重大事件：偵測、合併、保留使用者修改、API。"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))


def _series(moves: dict, n=300, start="2025-10-01"):
    days = pd.bdate_range(start, periods=n)
    r = pd.Series(0.3 * ((-1) ** pd.Series(range(n))).values, index=days)  # calm ±0.3 %
    for d, x in moves.items():
        r[pd.Timestamp(d)] = x
    return pd.DataFrame({"Close": 100 * (1 + r / 100).cumprod()})


@pytest.fixture()
def ev(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import events
    data = {"^TWII": _series({"2026-04-06": -7.0, "2026-04-07": -4.5}),
            "^GSPC": _series({"2026-04-03": -4.8, "2026-04-09": 9.5}),
            "^IXIC": _series({}), "^SOX": _series({"2026-04-03": -7.6})}
    monkeypatch.setattr(events.market, "history", lambda sym, period, interval: data[sym])
    from ft_common import fred
    monkeypatch.setattr(fred, "fed_decision", lambda d: None)  # offline
    monkeypatch.setattr(events, "macro_actual", lambda k, ref: None)
    monkeypatch.setattr(events.twmarket, "news_between", lambda q, a, b, n: [
        {"title": f"關稅衝擊全球股市 {a}", "publisher": "測試社", "link": "https://example.com/a", "time": a}])
    return events


def test_detect_merges_us_and_taiwan_reaction(ev):
    found = ev.detect()
    apr = [e for e in found if e["date"].startswith("2026-04")]
    # US 4/3 (Fri) + Taiwan 4/6–4/7 (Mon–Tue) + rebound 4/9: all within 3 days of each other → one event
    assert len(apr) == 1
    e = apr[0]
    assert e["date"] == "2026-04-03" and set(e["moves"]) == {"^TWII", "^GSPC", "^SOX"}
    assert e["moves"]["^TWII"]["pct"] == -7.0 and e["moves"]["^GSPC"]["pct"] == 9.5  # strongest daily move per index
    assert all(not x["date"].startswith("2025-1") for x in found)  # calm months: nothing


def test_refresh_keeps_edits_and_hidden(ev):
    ev.refresh()
    rows = ev.list_events(since="2026-01-01", world_too=False)
    mv = next(e for e in rows if e["kind"] == "move")
    assert mv["title_final"].startswith(("S&P", "加權", "費半"))
    assert any(e["kind"] == "fomc" and e["date"] == "2026-04-29" for e in rows)
    assert any(e["upcoming"] for e in ev.list_events(since="2026-01-01", until="2027-01-31", world_too=False)) is (dt.date.today().isoformat() < "2026-12-09")
    ev.edit(mv["id"], title="對等關稅股災", summary="自己寫的")
    ev.refresh()
    again = next(e for e in ev.list_events(since="2026-01-01", world_too=False) if e["id"] == mv["id"])
    assert again["title_final"] == "對等關稅股災" and again["summary_final"] == "自己寫的"
    ev.delete(mv["id"])
    assert mv["id"] not in {e["id"] for e in ev.list_events(since="2026-01-01", world_too=False)}
    ev.refresh()  # re-detection must not resurrect a hidden event
    assert mv["id"] not in {e["id"] for e in ev.list_events(since="2026-01-01", world_too=False)}


def test_enrich_headlines_and_ai(ev, monkeypatch):
    ev.refresh()
    eid = next(e["id"] for e in ev.list_events(since="2026-01-01", world_too=False) if e["kind"] == "move")
    seen = {}

    def fake_ask(llm, system, user, **kw):
        seen["user"] = user
        return json.dumps({"title": "關稅引爆全球股災", "summary": "標題顯示關稅升級，台美股重挫後反彈。"}, ensure_ascii=False)
    from ft_common import ollama
    monkeypatch.setattr(ollama, "ask", fake_ask)
    e = ev.enrich(eid, {"model": "m"})
    assert e["headlines"][0]["title"].startswith("關稅衝擊") and "加權" in seen["user"] and "關稅衝擊" in seen["user"]
    e = ev._public(e)
    assert e["title_final"] == "關稅引爆全球股災" and e["ai_model"] == "m"
    assert eid not in ev.pending()
    m = ev.markers("2026-01-01")
    assert any(x["title"] == "關稅引爆全球股災" for x in m) and all(x["kind"] != "world" for x in m)


def test_manual_events(ev):
    eid = ev.add("2026-11-03", "美國期中選舉", "結果公布前後波動")
    rows = ev.list_events(since="2026-10-01", until="2026-12-31", world_too=False)
    m = next(e for e in rows if e["id"] == eid)
    assert m["kind"] == "manual" and m["title_final"] == "美國期中選舉"
    with pytest.raises(ValueError):
        ev.add("2026-13-40", "壞日期")
    assert ev.delete(eid) and eid not in {e["id"] for e in ev.list_events(since="2026-10-01", world_too=False)}


def test_event_api(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    monkeypatch.setenv("FT_FAKE_DATA", "1")
    from ft_common import market, twmarket
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    sys.path.insert(0, str(ROOT / "terminal"))
    import importlib
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    H = {"x-ft": "1"}
    assert c.post("/api/events/refresh").status_code == 403
    assert c.post("/api/events/refresh", headers=H).json()["detected"] >= 1
    d = c.get("/api/events", params={"since": "2025-01-01"}).json()
    assert d["events"] and d["stats"]["moves"] >= 1
    r = c.post("/api/events", json={"date": "2026-10-20", "title": "輝達 GTC"}, headers=H).json()
    assert c.put(f"/api/events/{r['id']}", json={"title": "輝達 GTC 柏林"}, headers=H).status_code == 200
    mk = c.get("/api/events/markers", params={"since": "2026-10-01", "until": "2026-12-31"}).json()
    assert any(x["title"] == "輝達 GTC 柏林" for x in mk)
    assert c.delete(f"/api/events/{r['id']}", headers=H).status_code == 200
    assert c.put("/api/events/manual:nope", json={"title": "x"}, headers=H).status_code == 404
    t = c.get("/api/agent/events").text
    assert "市場重大事件" in t


def test_history_events_fold_detected_moves(ev):
    r = ev.refresh("2y")
    assert r["history"] == len(ev.HISTORY)
    rows = {e["id"]: e for e in ev.list_events(since="2020-01-01", world_too=False)}
    # 2025-10-01 series start → only the in-range built-ins get index moves, all are listed
    assert all(f"hist:{d}" in rows for d, _, _ in ev.HISTORY)
    assert rows["hist:2025-04-07"]["title_final"].startswith("台股")
    ev.edit("hist:2025-04-07", title="我的標題")
    ev.refresh("2y")
    assert {e["id"]: e for e in ev.list_events(since="2020-01-01", world_too=False)}["hist:2025-04-07"]["title_final"] == "我的標題"


def test_history_folds_nearby_detection(ev, monkeypatch):
    monkeypatch.setattr(ev, "HISTORY", [("2026-04-03", "測試大事", "說明")])
    ev.refresh("2y")
    rows = ev.list_events(since="2026-01-01", world_too=False)
    assert not any(e["kind"] == "move" and e["date"].startswith("2026-04") for e in rows)  # folded into the built-in
    h = next(e for e in rows if e["id"] == "hist:2026-04-03")
    assert h["moves"]["^TWII"]["pct"] == -7.0 and h["moves"]["^GSPC"]["pct"] == 9.5
    m = ev.markers("2026-01-01", None, "^TWII")
    assert any(x["kind"] == "history" and x["label"] for x in m)


def test_geo_threads(ev, monkeypatch):
    ev.refresh("2y")
    us = ev.list_events("2025-01-01", "2027-02-01", ["uscn"], world_too=False)
    ids = {e["id"] for e in us}
    assert {"uscn:2026-09-23", "uscn:2027-01-10", "hist:2025-10-30", "hist:2025-05-12"} <= ids  # thread pulls in history
    assert not any(e["kind"] == "drill" for e in us)
    up = next(e for e in us if e["id"] == "uscn:2027-01-10")
    assert up["upcoming"] and "休戰" in up["title_final"]
    dr = ev.list_events("2026-01-01", "2026-12-31", ["drill"], world_too=False)
    assert any("利劍" in e["title_final"] and "與那國" in e["summary_final"] for e in dr)
    # curated text is resynced on refresh, a user edit still wins
    ev.edit("drill:2026-10-19", title="我的標題")
    ev.refresh("2y")
    assert next(e for e in ev.list_events("2026-10-01", "2026-10-31", ["drill"], world_too=False))["title_final"] == "我的標題"
    # headlines enrichment (no AI renaming for curated threads)
    called = []
    monkeypatch.setattr("ft_common.ollama.ask", lambda *a, **k: called.append(1) or '{"title":"x"}', raising=False)
    e = ev.enrich("uscn:2026-09-24", {"model": "m"})
    assert e["headlines"] and not called and e["auto_title"].startswith("習近平")
    assert "uscn:2026-09-24" not in ev.pending() and "uscn:2027-01-10" not in ev.pending()
    t = ev.geo_text(("uscn",))
    assert "【中美談判】" in t and "2027-01-10（即將）" in t and "川習釜山會" in t and "軍演" not in t
    mk = ev.markers("2026-01-01", "2026-12-31", "^TWII")
    assert {"uscn", "drill"} <= {m["kind"] for m in mk}
    assert not any(m["kind"] == "drill" for m in ev.markers("2020-01-01", "2026-12-31", "^TWII"))  # long chart


def test_geo_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import events, market, twmarket
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    monkeypatch.setattr(events, "load_closes", lambda period: {})
    monkeypatch.setattr(events, "macro_actual", lambda k, ref: None)
    from ft_common import fred
    monkeypatch.setattr(fred, "fed_decision", lambda d: None)
    events.refresh("2y")
    sys.path.insert(0, str(ROOT / "terminal"))
    import importlib

    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    t = c.get("/api/agent/geo", params={"kind": "drill"}).text
    assert "利劍" in t and "中美談判" not in t
    assert "中美談判" in c.get("/api/agent/geo").text
    assert "美中關稅休戰到期" in c.get("/api/agent/calendar", params={"days": 120}).text
