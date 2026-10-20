"""分析工具箱：技術指標與 K 線型態、選擇權（Greeks、max pain、P/C）、研究、財報反應、價值檢查、市場情報、API／agent。
Yahoo 物件用假的（欄位照 yfinance 的格式）。"""
import datetime as dt
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))


def _ohlc(closes, start="2025-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c.shift(1).fillna(c.iloc[0]), "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


def test_technical_signals_and_patterns(monkeypatch):
    from ft_common import market, toolkit
    up = list(np.linspace(100, 200, 300))
    monkeypatch.setattr(market, "history", lambda s, p, i: _ohlc(up))
    r = toolkit.technical("AAPL")
    assert r["data"]["trend"] == "偏多" and r["data"]["rsi"] > 70 and "RSI 超買（≥70）" in r["data"]["signals"]
    assert "創 52 週新高" in r["data"]["signals"] and r["data"]["cloud"].startswith("雲上")
    # bullish engulfing on the last two bars
    df = _ohlc([100.0] * 40)
    df.iloc[-2, df.columns.get_loc("Open")], df.iloc[-2, df.columns.get_loc("Close")] = 101, 99
    df.iloc[-1, df.columns.get_loc("Open")], df.iloc[-1, df.columns.get_loc("Close")] = 98.5, 102
    assert "多頭吞噬" in toolkit.candle_patterns(df)


def test_greeks_and_max_pain():
    from ft_common import toolkit
    g = toolkit.greeks(100, 100, 0.5, 0.04, 0.3, True)
    p = toolkit.greeks(100, 100, 0.5, 0.04, 0.3, False)
    assert 0.55 < g["delta"] < 0.6 and g["delta"] - p["delta"] == pytest.approx(1)  # put-call parity on delta
    assert g["theta"] < 0 and p["gamma"] == pytest.approx(g["gamma"])
    calls = pd.DataFrame({"strike": [90, 100, 110], "openInterest": [10, 100, 500]})
    puts = pd.DataFrame({"strike": [90, 100, 110], "openInterest": [500, 100, 10]})
    assert toolkit.max_pain(calls, puts) == 100


class FakeTicker:
    def __init__(self, sym):
        self.options = ((dt.date.today() + dt.timedelta(days=3)).isoformat(), (dt.date.today() + dt.timedelta(days=31)).isoformat())
        idx = pd.to_datetime(["2026-11-05 16:00", "2026-07-30 16:00", "2026-04-30 16:00"]).tz_localize("America/New_York")
        self._ed = pd.DataFrame({"EPS Estimate": [2.1, 1.9, 1.7], "Reported EPS": [np.nan, 2.0, 1.6], "Surprise(%)": [np.nan, 5.3, -5.9]}, index=idx)
        self.recommendations = pd.DataFrame([{"period": "0m", "strongBuy": 10, "buy": 20, "hold": 5, "sell": 1, "strongSell": 0}])
        self.upgrades_downgrades = pd.DataFrame({"Firm": ["Morgan Stanley"], "ToGrade": ["Overweight"], "FromGrade": ["Equal-Weight"], "Action": ["up"]},
                                                index=pd.to_datetime(["2026-10-01"]))
        self.insider_transactions = pd.DataFrame({"Insider": ["CEO"], "Position": ["Chief Executive Officer"], "Text": ["Sale"], "Shares": [1000], "Start Date": ["2026-09-01"]})
        self.institutional_holders = pd.DataFrame({"Holder": ["Vanguard"], "pctHeld": [0.08]})
        cols = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31"])
        self.financials = pd.DataFrame({cols[0]: [100.0, 60.0, 20.0], cols[1]: [90.0, 50.0, 15.0], cols[2]: [80.0, 44.0, 12.0]},
                                       index=["Total Revenue", "Gross Profit", "Net Income"])
        self.balance_sheet = pd.DataFrame({cols[0]: [200.0, 80.0, 50.0, 30.0, 100.0, 10.0, 120.0], cols[1]: [190.0, 70.0, 50.0, 35.0, 95.0, 10.0, 110.0],
                                           cols[2]: [180.0, 60.0, 50.0, 40.0, 90.0, 10.0, 100.0]},
                                          index=["Total Assets", "Current Assets", "Total Liabilities Net Minority Interest", "Current Liabilities",
                                                 "Stockholders Equity", "Ordinary Shares Number", "Long Term Debt"])
        self.balance_sheet.loc["Long Term Debt"] = [20.0, 25.0, 30.0]
        self.cashflow = pd.DataFrame({cols[0]: [30.0, 25.0], cols[1]: [20.0, 15.0], cols[2]: [18.0, 12.0]}, index=["Operating Cash Flow", "Free Cash Flow"])

    def option_chain(self, exp):
        ks = [90.0, 95.0, 100.0, 105.0, 110.0]
        mk = lambda oi: pd.DataFrame({"strike": ks, "lastPrice": [11, 7, 4, 2, 1], "impliedVolatility": [0.35, 0.32, 0.3, 0.31, 0.33],  # noqa: E731
                                      "openInterest": oi, "volume": [5, 5, 5, 5, 5]})
        return types.SimpleNamespace(calls=mk([10, 20, 100, 300, 500]), puts=mk([500, 300, 100, 20, 10]))

    def get_earnings_dates(self, limit=12):
        return self._ed


@pytest.fixture()
def yf(monkeypatch):
    from ft_common import market, toolkit
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(market, "_yf", lambda: types.SimpleNamespace(Ticker=FakeTicker))
    closes = list(np.linspace(90, 100, 400))
    monkeypatch.setattr(market, "history", lambda s, p, i="1d": _ohlc(closes, "2025-04-01"))
    monkeypatch.setattr(market, "info", lambda s: {"targetMeanPrice": 120, "targetHighPrice": 150, "targetLowPrice": 90, "numberOfAnalystOpinions": 30,
                                                   "recommendationKey": "buy", "currentPrice": 100, "trailingEps": 2.0, "bookValue": 10.0,
                                                   "sharesOutstanding": 10.0, "trailingPE": 50, "priceToBook": 10, "marketCap": 1000, "debtToEquity": 30})
    monkeypatch.setattr(toolkit, "_risk_free", lambda: 0.04)
    return toolkit


def test_options(yf):
    r = yf.options("AAPL")
    assert r["data"]["expiry"] == (dt.date.today() + dt.timedelta(days=31)).isoformat() and r["data"]["atm"] == 100 and r["data"]["max_pain"] == 100
    assert r["data"]["pcr_oi"] == pytest.approx(1.0) and "ATM" in r["text"]
    with pytest.raises(ValueError):
        yf.options("2330.TW")


def test_research_earnings_value(yf):
    t = yf.research("AAPL")["text"]
    assert "目標價" in t and "+20.0%" in t and "Morgan Stanley" in t and "內部人" in t and "Vanguard" in t
    e = yf.earnings("AAPL")
    assert e["data"]["next"] == "2026-11-05" and e["data"]["beats"] == 1 and e["data"]["quarters"] == 2
    v = yf.value("AAPL")["data"]
    assert v["graham"] == pytest.approx((22.5 * 2 * 10) ** 0.5) and v["ncav"] == pytest.approx((80 - 50) / 10)
    assert v["fscore"] >= 6 and v["roe"][0] == pytest.approx(20.0)


def test_api_agent_and_intel(tmp_path, monkeypatch):
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
    r = c.get("/api/kit/tech", params={"s": "台積電"}).json()
    assert r["title"] == "2330.TW 技術面" and "一目均衡表" in r["text"]
    assert c.get("/api/kit/nope", params={"s": "2330"}).status_code == 404
    assert c.get("/api/kit/options", params={"s": "2330"}).status_code == 400
    i = c.get("/api/kit/intel", params={"market": "all"}).json()
    assert "類股輪動" in i["text"] and i["data"]["台股權值"]["rows"]
    t = c.get("/api/agent/kit/tech", params={"q": "2330"}).text
    assert "RSI" in t
    assert "沒有這個工具" in c.get("/api/agent/kit/xx").text
