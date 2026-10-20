"""定期定額：排程（月底、假日順延）、單位數與手續費、匯率換算、XIRR、單筆比較、持股計畫、回測 API。"""
import datetime as dt
import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
H = {"x-ft": "1"}


def test_schedule_and_days():
    from ft_common import dca
    s = dca.schedule(dt.date(2026, 1, 1), dt.date(2026, 3, 31), "monthly", [6, 31])
    assert [d.isoformat() for d in s] == ["2026-01-06", "2026-01-31", "2026-02-06", "2026-02-28", "2026-03-06", "2026-03-31"]
    w = dca.schedule(dt.date(2026, 10, 1), dt.date(2026, 10, 15), "weekly", [3])
    assert [d.isoformat() for d in w] == ["2026-10-07", "2026-10-14"]
    assert dca.parse_days("monthly", "6，16、26") == [6, 16, 26]
    assert dca.describe("weekly", [1, 5], 1000) == "每週一、五 1,000 元"
    with pytest.raises(ValueError):
        dca.parse_days("monthly", "a")


def test_xirr():
    from ft_common import dca
    r = dca.xirr([(dt.date(2025, 1, 1), -100), (dt.date(2026, 1, 1), 110)])
    assert r == pytest.approx(10.0, abs=0.05)
    assert dca.xirr([(dt.date(2025, 1, 1), -100)]) is None


def _px(vals, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=len(vals))
    return pd.Series(vals, index=idx, dtype=float)


def test_simulate_roll_forward_fees_and_fx(monkeypatch):
    from ft_common import dca
    px = _px([10.0] * 5 + [20.0] * 40)   # 2026-01-01 Thu …
    monkeypatch.setattr(dca, "prices", lambda sym, start: px)
    monkeypatch.setattr(dca.dt, "date", dt.date)
    r = dca.simulate("2330.TW", 1000, "monthly", "3", "2026-01-01", "2026-02-27", fee_pct=0.1425, min_fee=1)
    # 2026-01-03 is a Saturday → executes Mon 01-05 at 10; 02-03 at 20
    assert [x["date"] for x in r["lots"]] == ["2026-01-05", "2026-02-03"]
    fee = max(1000 * 0.001425, 1)
    assert r["units"] == pytest.approx((1000 - fee) / 10 + (1000 - fee) / 20)
    assert r["invested"] == 2000 and r["value"] == pytest.approx(r["units"] * 20)
    assert r["lump"]["value"] > r["value"]  # rising market: lump sum on day one wins
    # USD asset: TWD amount converted at that day's rate
    monkeypatch.setattr(dca, "twd_rate", lambda cur, start: _px([30.0] * 45))
    u = dca.simulate("VOO", 3000, "monthly", "3", "2026-01-01", "2026-01-31", fee_pct=0, min_fee=0)
    assert u["currency"] == "USD" and u["units"] == pytest.approx(3000 / 30 / 10) and u["value"] == pytest.approx(u["units"] * 20 * 30)


def test_api_backtest_and_plans(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import market, twmarket
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    sys.path.insert(0, str(ROOT / "terminal"))
    import auth
    importlib.reload(auth)
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    d = c.post("/api/dca/backtest", json={"symbols": "0050, BTC-USD", "amount": 5000, "freq": "monthly", "days": "6,16",
                                          "start": "2023-01-01"}).json()
    ok = [r for r in d["results"] if "error" not in r]
    assert len(ok) == 2 and ok[0]["symbol"] == "0050.TW" and ok[0]["n"] > 60 and ok[0]["curve"]
    assert ok[1]["currency"] == "USD" and "定期定額回測" in d["text"]
    from ft_common import ollama
    seen = {}
    monkeypatch.setattr(ollama, "ask", lambda llm, system, user, **kw: seen.setdefault("u", user) and "評估：0050 較穩")
    assert c.post("/api/dca/ai", json={"text": d["text"]}).json()["text"].startswith("評估")
    assert "0050.TW" in seen["u"] and c.post("/api/dca/ai", json={}).status_code == 400
    t = c.get("/api/agent/dca", params={"q": "0050", "years": 3}).text
    assert "0050.TW" in t and "年化" in t
    # plans are private and need a login
    assert c.get("/api/me/dca").status_code == 401
    auth.create_user("alice", "correct-horse-1")
    c.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-1"}, headers=H)
    assert c.post("/api/me/dca", json={"symbol": "0056.TW", "amount": 3000, "freq": "monthly", "days": "6", "start": "2024-01-01"}).status_code == 403
    pid = c.post("/api/me/dca", json={"symbol": "0056.TW", "amount": 3000, "freq": "monthly", "days": "6,26", "start": "2024-01-01"},
                 headers=H).json()["id"]
    assert c.post("/api/me/dca", json={"symbol": "0056.TW", "amount": 0, "freq": "monthly", "days": "6", "start": "2024-01-01"},
                  headers=H).status_code == 400
    p = c.get("/api/me/dca").json()
    assert p["plans"][0]["plan"] == "每月 6、26 日 3,000 元" and p["plans"][0]["n"] > 20 and p["total"]["monthly"] == 6000
    assert c.put(f"/api/me/dca/{pid}", json={"symbol": "0056.TW", "amount": 5000, "freq": "weekly", "days": "1", "start": "2026-01-01"},
                 headers=H).status_code == 200
    assert c.get("/api/me/dca").json()["plans"][0]["plan"] == "每週一 5,000 元"
    assert c.delete(f"/api/me/dca/{pid}", headers=H).status_code == 200 and c.get("/api/me/dca").json()["plans"] == []
