"""台幣匯率：交叉匯率計算、台銀牌告 CSV、API 與 agent。"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))

BOT = ("﻿幣別,匯率,現金,即期,遠期10天,遠期30天,遠期60天,遠期90天,遠期120天,遠期150天,遠期180天,"
       "匯率,現金,即期,遠期10天,遠期30天,遠期60天,遠期90天,遠期120天,遠期150天,遠期180天\n"
       "USD,本行買入,31.795,32.12,32.07,31.99,31.88,31.77,31.66,31.55,31.44,本行賣出,32.465,32.22,32.17,32.09,31.98,31.87,31.76,31.66,31.55\n"
       "JPY,本行買入,0.2051,0.2119,0.2125,0.2133,0.2145,0.2156,0.2167,0.2178,0.2189,本行賣出,0.2179,0.2169,0.2176,0.2184,0.2197,0.2208,0.2219,0.223,0.2242\n"
       "IDR,本行買入,0.00169,0,0,0,0,0,0,0,0,本行賣出,0.00229,0,0,0,0,0,0,0,0\n")


def test_bot_csv():
    from ft_common import fx
    r = fx.parse_bot(BOT)
    assert r["USD"] == {"cash_buy": 31.795, "spot_buy": 32.12, "cash_sell": 32.465, "spot_sell": 32.22}
    assert r["JPY"]["spot_sell"] == 0.2169
    assert r["IDR"]["spot_buy"] is None and r["IDR"]["cash_sell"] == 0.00229  # 0 = 不提供


def test_cross_rate(monkeypatch):
    from ft_common import fx, market
    monkeypatch.setattr(market, "FAKE", False)
    idx = pd.to_datetime(["2026-10-15", "2026-10-16", "2026-10-19"])
    data = {"TWD=X": [32.0, 32.4, 32.5], "JPY=X": [150.0, 151.0, 152.0], "EUR=X": [0.9, 0.92, 0.91]}
    monkeypatch.setattr(market, "history", lambda sym, period, interval: pd.DataFrame({"Close": data[sym]}, index=idx))
    market._cache.clear()
    s = fx.series("JPY", "1mo")
    assert s.iloc[-1] == pytest.approx(32.5 / 152.0)
    assert fx.series("USD", "1mo").iloc[-1] == 32.5
    row = fx.snapshot(["EUR"])[0]
    assert row["twd"] == pytest.approx(32.5 / 0.91) and row["per_twd"] == pytest.approx(0.91 / 32.5)
    assert row["d1"] == pytest.approx(((32.5 / 0.91) / (32.4 / 0.92) - 1) * 100)
    with pytest.raises(KeyError):
        fx.series("XXX")


def test_api_and_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import market, twmarket
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    sys.path.insert(0, str(ROOT / "terminal"))
    import importlib

    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    d = c.get("/api/twfx").json()
    assert len(d["rows"]) >= 30 and d["bot"]["rates"]["USD"]["cash_sell"]
    usd = next(r for r in d["rows"] if r["code"] == "USD")
    assert 25 < usd["twd"] < 40 and usd["spark"] and usd["region"] == "主要"
    s = c.get("/api/twfx/series", params={"ccy": "jpy", "range": "5y"}).json()
    assert s["unit"] == 100 and len(s["points"]) > 1000
    assert c.get("/api/twfx/series", params={"ccy": "XXX"}).status_code == 404
    t = c.get("/api/agent/twfx", params={"q": "USD,JPY"}).text
    assert "新台幣匯率" in t and "100 JPY" in t and "歐元" not in t
