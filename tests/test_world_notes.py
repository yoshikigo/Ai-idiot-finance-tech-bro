"""Offline tests for God's Eye View integration (world.py), Obsidian notes (notes.py) and the host applier.

A tiny in-process HTTP server stands in for the GEV MCP endpoint, so the real JSON-RPC path is exercised.
"""
import datetime as dt
import importlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))


# ------------------------------------------------------------------ fake GEV MCP
class _MCP(BaseHTTPRequestHandler):
    calls: list = []
    military = 3

    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path != "/mcp" or self.headers.get("MCP-Protocol-Version") != "2025-06-18":
            self.send_response(403); self.end_headers(); return
        req = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _MCP.calls.append(req)
        m = req["method"]
        if m == "tools/list":
            res = {"tools": [{"name": n} for n in ("get_earthquakes", "aircraft_in_area", "get_vessels")]}
        elif m == "tools/call":
            name, args = req["params"]["name"], req["params"]["arguments"]
            if name == "get_earthquakes":
                rows = [{"magnitude": 6.8, "place": "花蓮外海", "time": "2026-10-19T01:02:03Z"}] if args.get("minMagnitude", 0) <= 6 else []
                sc = {"total": len(rows), "rows": rows, "view": {"url": "http://localhost:4173/?layer=quakes"}}
                res = {"content": [{"type": "text", "text": f"{len(rows)} earthquakes"}, {"type": "text", "text": json.dumps(sc)}],
                       "structuredContent": sc, "isError": False}
            elif name == "aircraft_in_area":
                sc = {"total": _MCP.military, "rows": [{"callsign": f"RCH{i}"} for i in range(_MCP.military)]}
                res = {"content": [{"type": "text", "text": f"{_MCP.military} military aircraft"}], "structuredContent": sc, "isError": False}
            else:
                res = {"content": [{"type": "text", "text": "AISSTREAM_API_KEY not configured"}], "isError": True}
        else:
            res = {}
        body = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": res}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def gev():
    srv = HTTPServer(("127.0.0.1", 0), _MCP)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def mods(tmp_path, gev, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("GEV_URL", gev)
    monkeypatch.setenv("GEV_PUBLIC_URL", "https://arch.example.ts.net:4173")
    monkeypatch.setenv("FT_PUBLIC_URL", "https://arch.example.ts.net:8130")
    monkeypatch.setenv("FT_OBSIDIAN_FOLDER", "finance")
    monkeypatch.delenv("FT_FAKE_DATA", raising=False)
    from ft_common import notes, world
    world = importlib.reload(world)
    notes = importlib.reload(notes)
    monkeypatch.setattr(world.time, "sleep", lambda s: None)
    _MCP.calls.clear()
    _MCP.military = 3
    return world, notes, tmp_path


# ------------------------------------------------------------------ world
def test_regions_for_taiwan_semis_includes_strait_and_global(mods):
    world, *_ = mods
    rs = world.regions_for("2330.TW")
    assert rs[0] == "taiwan" and "south_china_sea" in rs and rs[-1] == "global"
    assert all(r in world.REGIONS for r in world.regions_for("XOM"))


def test_mcp_protocol_and_tool_parsing(mods):
    world, *_ = mods
    assert world.health()["ok"] and world.health()["tools"] == 3
    r = world.call_tool("get_earthquakes", {"minMagnitude": 4})
    assert r["ok"] and r["data"]["rows"][0]["magnitude"] == 6.8
    bad = world.call_tool("get_vessels", {})
    assert not bad["ok"] and "AISSTREAM" in bad["summary"]


def test_health_reports_unreachable(mods, monkeypatch):
    world, *_ = mods
    monkeypatch.setattr(world, "GEV_URL", "http://127.0.0.1:9")
    h = world.health()
    assert not h["ok"] and "連不到" in h["error"]


def test_collect_detects_quake_and_digest_mentions_it(mods):
    world, *_ = mods
    out = world.collect_once(["taiwan"])
    assert any("unavailable" in v for v in out.values())  # vessels without a key
    evs = world.events(["taiwan"], days=3)
    assert any(e["kind"] == "quake" and "M6.8" in e["title"] for e in evs)
    d = world.digest(["taiwan"])
    assert "M6.8" in d and "目前無法取得" in d  # ships unavailable is stated, not hidden
    c = world.compact(["taiwan"])
    assert len(c) <= 1200 and "get_world_events" in c
    st = world.status()
    assert st["snapshots"] > 0 and st["events"] >= 1 and st["gev"]["ok"]


def test_military_spike_needs_history_then_fires(mods):
    world, *_ = mods
    base = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    with world._conn() as c:
        for i in range(30):
            ts = (base + dt.timedelta(hours=i)).isoformat(timespec="seconds")
            c.execute("INSERT INTO snapshots(ts,region,chk,ok,summary,total,metric,rows,view_url) VALUES(?,?,?,?,?,?,?,?,?)",
                      (ts, "taiwan", "mil_air", 1, "x", 3, 3, "[]", None))
    _MCP.military = 14
    world.collect_once(["taiwan"])
    ev = [e for e in world.events(["taiwan"], days=1) if e["kind"] == "military"]
    assert ev and "14 架" in ev[0]["title"] and "平常約 3" in ev[0]["title"]


def test_as_of_hides_future_snapshots(mods):
    world, *_ = mods
    world.collect_once(["taiwan"])
    assert world.latest(["taiwan"], as_of="2000-01-01") == {} or not any(world.latest(["taiwan"], as_of="2000-01-01").values())
    assert "沒有" in world.digest(["taiwan"], as_of="2000-01-01") or "尚無" in world.digest(["taiwan"], as_of="2000-01-01")


def test_public_view_url_rewrites_loopback(mods):
    world, *_ = mods
    assert world.public_view_url("http://localhost:4173/?layer=quakes") == "https://arch.example.ts.net:4173/?layer=quakes"


# ------------------------------------------------------------------ notes
def _ta_job():
    return {"id": "ta-20261019-db3ece", "engine": "tradingagents", "status": "done",
            "params": {"ticker": "2330.TW", "date": "2026-10-19", "language": "zh-TW"},
            "finished": "2026-10-19T10:00:00", "llm": {"model": "qwen3:32b"},
            "result": {"summary": "最終決策：Overweight", "signal": "Overweight", "last": 1000.0,
                       "markdown": "# TradingAgents 2330.TW\n\n## 交易員\n加碼。\n<details><summary>prompt</summary>secret</details>\n",
                       "tables": [{"title": "分數", "columns": ["a", "b"], "rows": [["x|y", 1]]}],
                       "world": {"digest": "台灣 M6.8 地震\n第二行", "events": [{"title": "台灣 M6.8 地震"}]}}}


def test_job_note_rendering_and_outbox(mods):
    world, notes, tmp = mods
    rel = notes.write_job_note(_ta_job())
    assert rel.startswith("finance/AI 預測/2026-10/2026-10-19 2330.TW") and rel.endswith("(db3ece).md")
    f = notes.OUTBOX / rel
    text = f.read_text()
    assert text.startswith("---\n") and 'type: "ai-forecast"' in text and 'signal: "Overweight"' in text
    assert "\ndate: 2026-10-19\n" in text
    assert "secret" not in text and "> [!info]- 分析時的現實世界訊號" in text and "> 第二行" in text
    assert "https://arch.example.ts.net:8130/#job=tradingagents:ta-20261019-db3ece" in text
    assert "x\\|y" in text  # pipes escaped in tables
    assert (notes.OUTBOX / ".ft-stamp").exists()
    assert (notes.OUTBOX / "finance/AI 預測索引.md").exists()
    assert not list(notes.OUTBOX.rglob("*.part"))


def test_obsidian_disabled_by_env(mods, monkeypatch):
    _, notes, _ = mods
    monkeypatch.setenv("FT_OBSIDIAN", "0")
    assert notes.write_job_note(_ta_job()) is None
    assert notes.write_job_note(_ta_job(), force=True)


def test_direction_parsing(mods):
    _, notes, _ = mods
    assert notes.direction("Overweight") == 1 and notes.direction("SELL") == -1
    assert notes.direction("Hold") == 0 and notes.direction("中性") == 0 and notes.direction(None) is None


def test_review_scores_signal_and_timesfm(mods, monkeypatch):
    _, notes, tmp = mods
    today = dt.date(2026, 10, 19)
    d0 = (today - dt.timedelta(days=10)).isoformat()
    jobs = tmp / "data"
    (jobs / "tradingagents/jobs").mkdir(parents=True)
    (jobs / "timesfm/jobs").mkdir(parents=True)
    ta = {"id": "ta1", "engine": "tradingagents", "status": "done", "created": d0,
          "result": {"meta": {"date": d0, "ticker": "2330.TW", "signal": "Buy", "price_at": 100.0}}}
    tf = {"id": "tf1", "engine": "timesfm", "status": "done", "created": d0,
          "result": {"meta": {"date": d0, "ticker": "0050.TW", "price_at": 50.0, "horizon_end": (today - dt.timedelta(days=1)).isoformat(),
                              "forecast_median": 52.0, "forecast_p10": 49.0, "forecast_p90": 55.0, "forecast_change_pct": 4.0}}}
    # created must fall inside the 365-day window relative to the real today
    for j in (ta, tf):
        j["created"] = dt.date.today().isoformat()
    (jobs / "tradingagents/jobs/ta1.json").write_text(json.dumps(ta))
    (jobs / "timesfm/jobs/tf1.json").write_text(json.dumps(tf))
    monkeypatch.setattr(notes, "price_on", lambda sym, day: 53.0)
    rv = notes.review(now_prices={"2330.TW": 110.0}, today=today)
    by = {r["engine"]: r for r in rv["rows"]}
    assert by["tradingagents"]["hit"] is True and abs(by["tradingagents"]["ret_pct"] - 10) < 1e-9
    assert by["timesfm"]["in_band"] is True and by["timesfm"]["status"] == "已到期"
    assert rv["stats"]["tradingagents"]["scored"] == 1 and rv["stats"]["timesfm"]["band_in"] == 1
    md = notes.render_review(rv)
    assert "各引擎命中率" in md and "✅" in md


def test_world_day_note(mods):
    _, notes, _ = mods
    rel = notes.write_world_day("2026-10-19", "摘要", [{"title": "台灣 M6.8 地震", "detail": "花蓮"}])
    assert rel == "finance/世界事件/2026-10-19.md"
    assert "**台灣 M6.8 地震**" in (notes.OUTBOX / rel).read_text()


# ------------------------------------------------------------------ host applier
def test_applier_waits_for_vault_then_moves(tmp_path):
    outbox, vault = tmp_path / "outbox", tmp_path / "vault"
    (outbox / "finance/AI 預測/2026-10").mkdir(parents=True)
    (outbox / "finance/AI 預測/2026-10/a b.md").write_text("hi")
    (outbox / ".ft-stamp").write_text("x")
    vault.mkdir()
    env = {**os.environ, "FT_OUTBOX": str(outbox), "FT_VAULT": str(vault)}
    sh = str(ROOT / "scripts/ft-obsidian-apply.sh")
    subprocess.run(["bash", sh], env=env, check=True)
    assert (outbox / "finance/AI 預測/2026-10/a b.md").exists()  # no .obsidian → untouched
    (vault / ".obsidian").mkdir()
    subprocess.run(["bash", sh], env=env, check=True)
    assert (vault / "finance/AI 預測/2026-10/a b.md").read_text() == "hi"
    assert not list(outbox.rglob("*.md"))
