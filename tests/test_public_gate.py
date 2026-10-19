"""公開網址（Cloudflare Tunnel）：未登入什麼都拿不到、agent API 與改設定只限本機、不能註冊；本機行為不變。"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "terminal"))


@pytest.fixture()
def C(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    monkeypatch.setenv("FT_FAKE_DATA", "1")
    monkeypatch.setenv("FT_PUBLIC_HOSTS", "fin-terminal.example.com")
    from ft_common import market, twmarket
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    import auth
    importlib.reload(auth)
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    auth.create_user("alice", "correct-horse-1")
    return TestClient(appmod.app), TestClient(appmod.app, base_url="https://fin-terminal.example.com")


def test_local_unchanged(C):
    local, _ = C
    assert local.get("/api/settings").status_code == 200
    assert local.get("/api/agent/market").status_code == 200
    assert local.get("/api/auth/me").json()["public"] is False


def test_public_requires_login(C):
    _, pub = C
    H = {"x-ft": "1"}
    assert pub.get("/").status_code == 200 and pub.get("/static/app.js").status_code == 200
    me = pub.get("/api/auth/me").json()
    assert me["public"] is True and me["signup"] is False
    for path in ("/api/settings", "/api/quotes?s=2330.TW", "/api/events", "/api/macro", "/api/engines"):
        assert pub.get(path).status_code == 401, path
    assert pub.post("/api/ask", json={"q": "hi"}, headers=H).status_code == 401
    assert pub.get("/api/agent/market").status_code == 404
    assert pub.get("/docs").status_code == 404 and pub.get("/openapi.json").status_code == 404
    assert pub.post("/api/auth/register", json={"username": "evil", "password": "12345678x"}, headers=H).status_code == 403
    # Cloudflare header alone also counts as public (even on the local hostname)
    local, _ = C
    assert local.get("/api/settings", headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 401


def test_public_after_login(C):
    _, pub = C
    H = {"x-ft": "1"}
    assert pub.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-1"}, headers=H).status_code == 200
    assert pub.get("/api/settings").status_code == 200
    assert pub.get("/api/quotes", params={"s": "2330.TW"}).status_code == 200
    assert pub.put("/api/settings", json={"ollama_url": "http://169.254.169.254"}, headers=H).status_code == 403
    assert pub.get("/api/agent/market").status_code == 404
    assert pub.get("/api/settings").headers.get("x-frame-options") == "DENY"
