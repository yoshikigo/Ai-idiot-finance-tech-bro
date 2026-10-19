"""供應鏈地圖、今日焦點、我的持股（登入與隔離）— offline, demo data."""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "terminal"))


@pytest.fixture(scope="module", autouse=True)
def fake_data():
    from ft_common import market, twmarket
    old = (market.FAKE, twmarket.FAKE, os.environ.get("FT_FAKE_DATA"))
    market.FAKE = twmarket.FAKE = True
    os.environ["FT_FAKE_DATA"] = "1"
    yield
    market.FAKE, twmarket.FAKE = old[0], old[1]
    if old[2] is None:
        os.environ.pop("FT_FAKE_DATA", None)
    else:
        os.environ["FT_FAKE_DATA"] = old[2]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("ftdata")
    os.environ.update(FT_DATA=str(d), FT_WORLD="0")
    import auth
    importlib.reload(auth)
    auth.DB = d / "users.sqlite"
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
    import app as appmod
    from fastapi.testclient import TestClient
    return TestClient(appmod.app), auth


def test_supplychain_data_is_consistent():
    from ft_common import supplychain as sc
    assert sc.validate() == []
    for k in sc.NETWORKS:
        L = sc.layout(k)
        col = {n["id"]: n["col"] for n in L["nodes"]}
        assert all(col[a] < col[b] for a, b in L["edges"]), k  # arrows always point downstream (left → right)
        assert L["rows"] <= 7


def test_find_and_tagging():
    from ft_common import supplychain as sc
    assert sc.find("CoWoS")["node"] == "cowos"
    assert sc.find("奇鋐")["node"] == "thermal"
    hit = sc.find("2345")
    assert hit["node"] == "switch" and hit["symbol"].startswith("2345.")
    assert set(sc.tag_text("台積電（2330）擴充 CoWoS，欣興 ABF 載板漲價")) >= {"cowos", "abf", "foundry"}
    assert sc.tag_text("今天天氣很好") == []


def test_sc_endpoints(client):
    c, _ = client
    nets = c.get("/api/sc/networks").json()
    assert any(n["id"] == "ai-datacenter" for n in nets)
    L = c.get("/api/sc/network", params={"id": "semi"}).json()
    assert L["cols"] >= 3 and L["edges"]
    node = c.get("/api/sc/node", params={"id": "cowos"}).json()
    assert {"foundry", "abf", "hbm"} <= {u["id"] for u in node["upstream"]} and node["companies"]
    st = c.get("/api/sc/stats", params={"net": "network"}).json()
    sw = st["nodes"]["switch"]
    assert sw["pct"] is not None and sw["companies"][0]["last"] is not None
    assert "2330.TW" in c.get("/api/sc/index").json()
    assert c.get("/api/sc/network", params={"id": "nope"}).status_code == 404


def test_focus(client):
    c, _ = client
    d = c.get("/api/focus").json()
    assert d["themes"] and d["gainers"] and d["institutional"]["rows"]
    assert d["themes"] == sorted(d["themes"], key=lambda t: -t["pct"])
    assert all(m["mkt"] == "TW" for m in d["gainers"])
    from ft_common import focus
    txt = focus.ai_context(d)
    assert "供應鏈題材" in txt and "三大法人" in txt


def test_bfi_parser():
    from ft_common import focus
    d = {"stat": "OK", "date": "20261019", "fields": ["單位名稱", "買進金額", "賣出金額", "買賣差額"],
         "data": [["自營商(自行買賣)", "9,400,000,000", "8,000,000,000", "1,400,000,000"],
                  ["投信", "49,300,000,000", "26,300,000,000", "23,000,000,000"],
                  ["外資及陸資(不含外資自營商)", "274,200,000,000", "350,900,000,000", "-76,700,000,000"],
                  ["合計", "1", "1", "-52,300,000,000"]]}
    r = focus.parse_bfi(d)
    assert r["date"] == "2026-10-19"
    by = {x["name"]: x for x in r["rows"]}
    assert by["外資"]["net"] == pytest.approx(-767) and by["投信"]["net"] == pytest.approx(230)
    assert focus.parse_bfi({"stat": "很抱歉，沒有符合條件的資料!"}) is None


H = {"x-ft": "1"}


def test_holdings_need_login_and_csrf_header(client):
    c, _ = client
    c.cookies.clear()
    assert c.get("/api/me/holdings").status_code == 401
    assert c.post("/api/auth/register", json={"username": "alice", "password": "pass-word-1"}).status_code == 403  # no X-FT
    assert c.post("/api/auth/register", json={"username": "a", "password": "pass-word-1"}, headers=H).status_code == 400
    assert c.post("/api/auth/register", json={"username": "alice", "password": "short"}, headers=H).status_code == 400


def test_users_only_see_their_own_holdings(client):
    c, auth = client
    c.cookies.clear()
    r = c.post("/api/auth/register", json={"username": "alice", "password": "alice-secret-1"}, headers=H)
    assert r.status_code == 200
    ck = r.headers["set-cookie"].lower()
    assert "httponly" in ck and "samesite=strict" in ck
    hid = c.post("/api/me/holdings", json={"symbol": "2330.TW", "shares": 1000, "cost": 500, "opened": "2025-01-02",
                                            "note": "長抱"}, headers=H).json()["id"]
    c.post("/api/me/holdings", json={"symbol": "NVDA", "shares": 10, "cost": 100}, headers=H)
    p = c.get("/api/me/holdings").json()
    assert p["user"] == "alice" and len(p["rows"]) == 2
    tsmc = next(x for x in p["rows"] if x["symbol"] == "2330.TW")
    assert tsmc["currency"] == "TWD" and tsmc["value"] == pytest.approx(tsmc["last"] * 1000)
    assert "晶圓代工" in tsmc["chains"] and p["exposure"]
    assert p["total"]["value"] > 0 and abs(sum(x["weight"] for x in p["rows"] if x["weight"]) - 100) < 1e-6
    alice_cookie = dict(c.cookies)

    # bob signs up in a fresh browser: sees nothing of alice's, cannot edit or delete her rows
    c.cookies.clear()
    assert c.post("/api/auth/register", json={"username": "bob", "password": "bob-secret-12"}, headers=H).status_code == 200
    assert c.get("/api/me/holdings").json()["rows"] == []
    assert c.put(f"/api/me/holdings/{hid}", json={"symbol": "2330.TW", "shares": 1, "cost": 1}, headers=H).status_code == 404
    assert c.delete(f"/api/me/holdings/{hid}", headers=H).status_code == 404
    assert "2330" not in c.get("/api/me/holdings.csv").text
    assert c.post("/api/auth/register", json={"username": "ALICE", "password": "whatever-123"}, headers=H).status_code == 409

    # alice's data intact; her session cookie works only for her
    c.cookies.clear()
    c.cookies.update(alice_cookie)
    rows = c.get("/api/me/holdings").json()["rows"]
    assert len(rows) == 2 and rows[0]["shares"] == 1000
    assert c.put(f"/api/me/holdings/{hid}", json={"symbol": "2330.TW", "shares": 2000, "cost": 480}, headers=H).status_code == 200
    assert "2330.TW,2000.0,480.0" in c.get("/api/me/holdings.csv").text

    # the database never holds the password or the raw session token
    raw = auth.DB.read_bytes()
    assert b"alice-secret-1" not in raw and alice_cookie["ft_session"].encode() not in raw


def test_login_lockout_logout_and_password_change(client):
    c, _ = client
    c.cookies.clear()
    assert c.post("/api/auth/login", json={"username": "alice", "password": "nope-nope"}, headers=H).status_code == 401
    assert c.post("/api/auth/login", json={"username": "ghost", "password": "nope-nope"}, headers=H).status_code == 401
    r = c.post("/api/auth/login", json={"username": "alice", "password": "alice-secret-1"}, headers=H)
    assert r.status_code == 200 and c.get("/api/auth/me").json()["user"]["username"] == "alice"
    old = dict(c.cookies)
    assert c.post("/api/auth/password", json={"old": "alice-secret-1", "new": "alice-secret-2"}, headers=H).status_code == 200
    assert c.get("/api/me/holdings").status_code == 200  # this browser got a fresh session
    c.cookies.clear()
    c.cookies.update(old)
    assert c.get("/api/me/holdings").status_code == 401  # other sessions were signed out
    c.cookies.clear()
    c.post("/api/auth/login", json={"username": "alice", "password": "alice-secret-2"}, headers=H)
    assert c.post("/api/auth/logout", headers=H).status_code == 200
    c.cookies.clear()
    for _ in range(5):
        c.post("/api/auth/login", json={"username": "bob", "password": "wrong-wrong"}, headers=H)
    assert c.post("/api/auth/login", json={"username": "bob", "password": "bob-secret-12"}, headers=H).status_code == 429


def test_delete_account_removes_holdings(client):
    c, auth = client
    c.cookies.clear()
    c.post("/api/auth/register", json={"username": "carol", "password": "carol-secret"}, headers=H)
    c.post("/api/me/holdings", json={"symbol": "0050.TW", "shares": 100, "cost": 150}, headers=H)
    assert c.post("/api/auth/delete", json={"password": "bad"}, headers=H).status_code == 401
    assert c.post("/api/auth/delete", json={"password": "carol-secret"}, headers=H).status_code == 200
    import sqlite3
    with sqlite3.connect(auth.DB) as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE username='carol'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM holdings WHERE symbol='0050.TW'").fetchone()[0] == 0


def test_agent_focus_and_chain(client):
    c, _ = client
    t = c.get("/api/agent/focus").text
    assert "供應鏈題材" in t and "三大法人" in t
    assert "AI 先進封裝" in c.get("/api/agent/chain").text
    t = c.get("/api/agent/chain", params={"q": "奇鋐", "plain": 1}).text
    assert "散熱" in t and "3017" in t and "上游" in t
    assert c.get("/api/agent/chain", params={"q": "zzzz-none"}).status_code == 404


def test_us_comparison_in_stats():
    from ft_common import supplychain as sc
    st = sc.stats(["cowos", "gpu", "abf"])["nodes"]
    assert {c["symbol"] for c in st["cowos"]["us_companies"]} >= {"AMKR", "INTC"} and st["cowos"]["us_pct"] is not None
    assert st["gpu"]["tw_pct"] is None and st["gpu"]["us_pct"] is not None  # a US node compares with itself only
    assert st["abf"]["us_companies"] == [] and st["abf"]["us_pct"] is None   # no US ABF maker: no fake comparison
    syms = [c["symbol"] for c in sc.us_group("optics")]
    assert syms.count("COHR") == 1 and "LITE" in syms
