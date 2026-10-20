"""產業分布：Nasdaq 篩選器解析、台股市值計算、API 與 agent。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))

NASDAQ = {"data": {"rows": [
    {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "lastsale": "$250.00", "pctchange": "1.00%", "marketCap": "3,800,000,000,000",
     "sector": "Technology", "industry": "Computer Manufacturing", "country": "United States"},
    {"symbol": "GOOGL", "name": "Alphabet Inc. Class A Common Stock", "lastsale": "$300", "pctchange": "-2.0%", "marketCap": "1800000000000",
     "sector": "Technology", "industry": "Computer Software: Programming Data Processing"},
    {"symbol": "GOOG", "name": "Alphabet Inc. Class C Capital Stock", "lastsale": "$301", "pctchange": "-2.0%", "marketCap": "1700000000000",
     "sector": "Technology", "industry": "Computer Software: Programming Data Processing"},
    {"symbol": "ZIM", "name": "ZIM Integrated Shipping Services Ltd. Ordinary Shares", "lastsale": "$20", "pctchange": "3%", "marketCap": "2400000000",
     "sector": "Industrials", "industry": "Marine Transportation"},
    {"symbol": "ADM", "name": "Archer-Daniels-Midland Company Common Stock", "lastsale": "$60", "pctchange": "0.5%", "marketCap": "29000000000",
     "sector": "Consumer Staples", "industry": "Farming/Seeds/Milling"},
    {"symbol": "JPM", "name": "JP Morgan Chase & Co. Common Stock", "lastsale": "$300", "pctchange": "", "marketCap": "800000000000",
     "sector": "Finance", "industry": "Major Banks"},
    {"symbol": "ABCDW", "name": "ABCD Corp Warrant", "lastsale": "$0.1", "pctchange": "1%", "marketCap": "1000000", "sector": "Finance", "industry": ""},
    {"symbol": "BAC^K", "name": "Bank of America Preferred", "lastsale": "$25", "marketCap": "5000000", "sector": "Finance"},
    {"symbol": "NOCAP", "name": "No Cap Inc", "lastsale": "$1", "marketCap": "", "sector": "Finance"}]}}


def test_parse_nasdaq_groups_and_dedupe():
    from ft_common import sectors
    rows = {r["symbol"]: r for r in sectors.parse_nasdaq(NASDAQ)}
    assert set(rows) == {"AAPL", "GOOGL", "ZIM", "ADM", "JPM"}  # GOOG folded into GOOGL, warrant/preferred/no-cap dropped
    assert rows["AAPL"]["group"] == "科技" and rows["AAPL"]["name"] == "蘋果" and rows["AAPL"]["price"] == 250
    assert rows["ZIM"]["group"] == "運輸航運" and rows["ZIM"]["industry"] == "海運"
    assert rows["ADM"]["group"] == "農業" and rows["JPM"]["group"] == "金融" and rows["JPM"]["pct"] is None


def test_us_breakdown_weights(monkeypatch):
    from ft_common import market, sectors
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(sectors, "_us_nasdaq", lambda: sectors.parse_nasdaq(NASDAQ))
    market._cache.clear(); sectors._neg.clear()
    d = sectors.breakdown("us")
    assert abs(sum(s["weight"] for s in d["sectors"]) - 100) < 1e-6 and "Nasdaq" in d["source"]
    tech = d["sectors"][0]
    assert tech["name"] == "科技" and tech["count"] == 2
    assert tech["pct"] == pytest.approx((3.8e12 * 1 + 1.8e12 * -2) / 5.6e12)  # cap-weighted day change
    assert tech["stocks"][0]["symbol"] == "AAPL" and tech["industries"][0]["name"] == "電腦硬體"
    # Nasdaq down → curated fallback, and the source says so
    monkeypatch.setattr(sectors, "_us_nasdaq", lambda: (_ for _ in ()).throw(RuntimeError("blocked")))
    monkeypatch.setattr(sectors, "_us_curated", lambda: sectors.parse_nasdaq(NASDAQ)[:2])
    market._cache.clear(); sectors._neg.clear()
    assert "代表性大型股" in sectors.breakdown("us")["source"]


def test_tw_shares_and_prices():
    from ft_common import sectors
    sh = sectors.tw_shares([{"公司代號": "2330", "已發行普通股數或TDR原股發行股數": "25,932,733,242"},
                            {"公司代號": "1101", "實收資本額": "75,000,000,000", "普通股每股面額": "新台幣 10.0000元"},
                            {"公司代號": "9999", "實收資本額": "1,000,000", "普通股每股面額": "新台幣 1.0000元"}], "公司代號")
    assert sh == {"2330": 25932733242, "1101": 7.5e9, "9999": 1e6}
    otc = sectors.tw_shares([{"SecuritiesCompanyCode": "6488", "Paidin.Capital.NTDollars": "4,380,000,000"}], "SecuritiesCompanyCode")
    assert otc == {"6488": 4.38e8}
    px = sectors.tw_prices([{"Code": "2330", "ClosingPrice": "1,000.00", "Change": "-20.00"},
                            {"SecuritiesCompanyCode": "6488", "Close": "500", "Change": "+5"},
                            {"Code": "0000", "ClosingPrice": "", "Change": ""}])
    assert px["2330"][0] == 1000 and px["2330"][1] == pytest.approx(-20 / 1020 * 100) and px["6488"][1] == pytest.approx(5 / 495 * 100)
    assert "0000" not in px


def test_api_agent_and_names(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import market, sectors, twmarket
    monkeypatch.setattr(market, "FAKE", True)
    monkeypatch.setattr(twmarket, "FAKE", True)
    market._cache.clear(); sectors._neg.clear()
    sys.path.insert(0, str(ROOT / "terminal"))
    import importlib

    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app)
    us = c.get("/api/sectors", params={"market": "us", "top": 5}).json()
    names = {s["name"] for s in us["sectors"]}
    assert {"科技", "金融", "運輸航運", "農業"} <= names and all(len(s["stocks"]) <= 5 for s in us["sectors"])
    assert all(s["color"].startswith("#") for s in us["sectors"])
    tw = c.get("/api/sectors", params={"market": "tw"}).json()
    assert tw["sectors"][0]["name"] == "半導體業" and tw["unit"] == "兆元" and any(s["name"] == "航運業" for s in tw["sectors"])
    t = c.get("/api/agent/sectors", params={"market": "tw"}).text
    assert "台股產業分布" in t and "半導體業" in t
    rows = c.get("/api/monitor/MAG7").json()["rows"]
    assert next(r for r in rows if r["symbol"] == "AAPL")["name"] == "蘋果"  # US names no longer blank
