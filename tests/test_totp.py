"""兩步驟驗證：RFC 6238 向量、設定→啟用→登入要驗證碼、同一碼不能重用、救援碼一次性、公開網址強制 TOTP。"""
import importlib
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "terminal"))
H = {"x-ft": "1"}


def test_rfc6238_vector():
    import base64

    import auth
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth.totp_code(secret, 59 // 30) == "287082"          # RFC 6238 T=59 → 94287082 (last 6)
    assert auth.totp_code(secret, 1111111109 // 30) == "081804"  # → 07081804


@pytest.fixture()
def C(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    monkeypatch.setenv("FT_FAKE_DATA", "1")
    monkeypatch.setenv("FT_PUBLIC_HOSTS", "fin-terminal.example.com")
    monkeypatch.setenv("FT_PUBLIC_TOTP", "required")
    from ft_common import market, twmarket
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    import auth
    importlib.reload(auth)
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    auth.create_user("alice", "correct-horse-1")
    return auth, TestClient(appmod.app), TestClient(appmod.app, base_url="https://fin-terminal.example.com")


def _login(c, code=""):
    return c.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-1", "code": code}, headers=H)


def test_setup_enable_login_and_replay(C, monkeypatch):
    auth, local, pub = C
    # public URL refuses an account without TOTP
    r = _login(pub)
    assert r.status_code == 403 and "兩步驟" in r.json()["detail"]
    assert _login(local).status_code == 200
    assert local.post("/api/auth/totp/setup", json={"password": "wrong"}, headers=H).status_code == 401
    t = local.post("/api/auth/totp/setup", json={"password": "correct-horse-1"}, headers=H).json()
    assert t["uri"].startswith("otpauth://totp/") and "<svg" in (t["qr_svg"] or "<svg")
    now = int(time.time()) // 30
    assert local.post("/api/auth/totp/enable", json={"code": "000000" if auth.totp_code(t["secret"], now) != "000000" else "111111"}, headers=H).status_code == 400
    r = local.post("/api/auth/totp/enable", json={"code": auth.totp_code(t["secret"], now)}, headers=H).json()
    codes = r["recovery_codes"]
    assert len(codes) == 8
    me = local.get("/api/auth/me").json()
    assert me["user"]["totp"] is True and me["user"]["recovery_left"] == 8
    # login now needs the second factor
    r = _login(pub)
    assert r.status_code == 401 and r.json()["need_totp"]
    # the code used to enable can't be replayed; the next step works
    assert _login(pub, auth.totp_code(t["secret"], now)).status_code == 401
    monkeypatch.setattr(auth.time, "time", lambda: (now + 1) * 30 + 5)
    assert _login(pub, auth.totp_code(t["secret"], now + 1)).status_code == 200
    assert pub.get("/api/settings").status_code == 200
    # recovery code: once
    pub2 = type(pub)(pub.app, base_url="https://fin-terminal.example.com")
    assert _login(pub2, codes[0]).status_code == 200
    assert _login(pub2, codes[0]).status_code == 401
    # can't switch it off over the public URL; can locally with password + code
    assert pub.post("/api/auth/totp/disable", json={"password": "correct-horse-1", "code": codes[1]}, headers=H).status_code == 403
    assert local.post("/api/auth/totp/disable", json={"password": "correct-horse-1", "code": codes[1]}, headers=H).status_code == 200
    assert local.get("/api/auth/me").json()["user"]["totp"] is False
    # secrets / recovery codes are not stored in clear
    import sqlite3
    db = sqlite3.connect(auth.DB).iterdump()
    assert not any(codes[2] in line for line in db)
