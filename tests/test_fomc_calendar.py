"""FOMC 文件（聲明解析、逐句差異、SEP 中位數與前次、紀要、AI 只存一次）與美國數據公布日事件。"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
H = {"x-ft": "1"}

SEP_HTML = """<html><body><div id="article"><table>
<tr><th>Variable</th><th>Median<sup>1</sup></th><th></th><th></th><th></th><th></th><th>Central Tendency</th></tr>
<tr><th></th><th>2026</th><th>2027</th><th>2028</th><th>2029</th><th>Longer run</th><th>2026</th><th>2027</th><th>2028</th><th>2029</th><th>Longer run</th></tr>
<tr><td>Change in real GDP</td><td>2.3</td><td>2.4</td><td>2.2</td><td>2.1</td><td>2.0</td><td>2.1–2.5</td></tr>
<tr><td>June projection</td><td>2.2</td><td>2.3</td><td>2.2</td><td></td><td>2.0</td></tr>
<tr><td>Core PCE inflation<sup>4</sup></td><td>3.4</td><td>2.5</td><td>2.2</td><td>2.0</td><td></td></tr>
<tr><td>June projection</td><td>3.3</td><td>2.5</td><td>2.1</td><td></td><td></td></tr>
<tr><td>Memo: Projected appropriate policy path</td></tr>
<tr><td>Federal funds rate</td><td>4.1</td><td>4.1</td><td>3.9</td><td>3.6</td><td>3.2</td></tr>
<tr><td>June projection</td><td>3.8</td><td>3.6</td><td>3.4</td><td></td><td>3.1</td></tr>
</table></div></body></html>"""


def test_parse_sep_and_statement():
    from ft_common import fomc
    s = fomc.parse_sep(SEP_HTML)
    assert s["years"] == ["2026", "2027", "2028", "2029", "Longer run"]
    assert s["median"]["gdp"] == [2.3, 2.4, 2.2, 2.1, 2.0] and s["median"]["core"][4] is None
    assert s["median"]["ffr"][0] == 4.1 and s["previous"]["ffr"][:3] == [3.8, 3.6, 3.4] and s["previous_label"] == "June projection"
    html = ("<html><div id='article'><p>Release Date: September 16, 2026</p><p>Recent indicators suggest that economic activity "
            "has been expanding at a solid pace. Inflation remains elevated.</p><p>Voting for the monetary policy action were A, B.</p>"
            "<p>For media inquiries, please email x@y</p><p>Implementation Note issued September 16, 2026</p></div></html>")
    t = fomc.parse_statement(html)
    assert "solid pace" in t and "Voting for" in t and "media" not in t and "Implementation Note" not in t
    d = fomc.diff("Growth is moderate. Inflation remains elevated.", "Growth is solid. Inflation remains elevated.")
    assert [x["op"] for x in d] == ["del", "add", "same"]


@pytest.fixture()
def C(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import events, fomc, fred, market, twmarket
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    monkeypatch.setattr(fred, "fed_decision", lambda d: None)
    monkeypatch.setattr(events, "load_closes", lambda period: {})
    monkeypatch.setattr(events, "macro_actual", lambda k, ref: "CPI 年增 2.9%（前月 3.0%）" if k == "cpi" else None)
    sys.path.insert(0, str(ROOT / "terminal"))
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    return fomc, events, TestClient(appmod.app)


def test_fomc_bundle_api_and_ai(C, monkeypatch):
    fomc, events, c = C
    b = c.get("/api/fomc/2026-09-16").json()
    assert b["statement"]["url"].endswith("monetary20260916a.htm") and b["previous_date"] == "2026-07-29"
    assert b["changes"] >= 1 and any(x["op"] == "add" for x in b["diff"])
    assert b["sep"]["median"]["ffr"] and b["previous_sep_date"] == "2026-06-17" and b["labels"]["ffr"].startswith("聯邦基金")
    assert b["minutes"] is None and b["minutes_due"] == "2026-10-07"   # demo: minutes not published
    assert c.get("/api/fomc/2026-09-15").status_code == 404
    assert c.get("/api/fomc/sep/latest").json()["date"] <= "2026-12-31"
    from ft_common import ollama
    calls = []
    monkeypatch.setattr(ollama, "ask", lambda llm, system, user, **kw: calls.append(user) or "偏鷹：通膨措辭轉強")
    assert c.post("/api/fomc/2026-09-16/ai").status_code == 403
    r = c.post("/api/fomc/2026-09-16/ai", headers=H).json()
    assert r["text"].startswith("偏鷹") and "+ " in calls[0]
    c.post("/api/fomc/2026-09-16/ai", headers=H)
    assert len(calls) == 1  # stored, not asked again
    assert c.post("/api/fomc/2026-09-16/ai?kind=minutes", headers=H).status_code == 422
    t = c.get("/api/agent/fomc", params={"date": "2026-09-16"}).text
    assert "措辭變化" in t and "AI 解讀" in t and "聯邦基金利率" in t


def test_us_release_events(C):
    fomc, events, c = C
    events.refresh("2y")
    rows = {e["id"]: e for e in events.list_events("2025-01-01", "2026-12-31", ["macro"], world_too=False)}
    assert rows["macro:cpi:2026-10-14"]["title_final"] == "美國 CPI（9 月）"
    assert "CPI 年增 2.9%" in rows["macro:cpi:2026-10-14"]["summary_final"] or rows["macro:cpi:2026-10-14"]["date"] > "2026-10-20"
    assert rows["macro:nfp:2025-11-20"]["title_final"] == "美國非農就業（9 月）" and "macro:nfp:2025-11-07" not in rows
    assert "macro:pce:2026-10-29" in rows
    mk = events.markers("2026-08-01", "2026-10-31", "^GSPC")
    assert any(m["kind"] == "macro" for m in mk)
    assert not any(m["kind"] == "macro" for m in events.markers("2024-01-01", "2026-10-31", "^GSPC"))  # long chart: hidden
    t = c.get("/api/agent/calendar", params={"days": 60}).text
    assert "重要日程" in t
