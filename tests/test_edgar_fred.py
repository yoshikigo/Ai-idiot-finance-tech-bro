"""SEC EDGAR（XBRL 財報、申報清單、申報日）與 FRED（總經、Fed 決策標註）— 離線 fixture。"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))

TICKERS = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
           "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"}}


def _fact(start, end, val, form, filed, fy, fp):
    return {"start": start, "end": end, "val": val, "accn": "x", "fy": fy, "fp": fp, "form": form, "filed": filed}


FACTS = {"cik": 1045810, "entityName": "NVIDIA CORP", "facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [
        _fact("2024-01-29", "2025-01-26", 130497e6, "10-K", "2025-02-26", 2025, "FY"),
        _fact("2024-01-29", "2025-01-26", 130497e6, "10-K", "2026-02-25", 2026, "FY"),   # comparative in next 10-K
        _fact("2023-01-30", "2024-01-28", 60922e6, "10-K", "2024-02-21", 2024, "FY"),
        _fact("2025-01-27", "2025-04-27", 44062e6, "10-Q", "2025-05-28", 2026, "Q1"),
        _fact("2025-01-27", "2025-07-27", 90805e6, "10-Q", "2025-08-27", 2026, "Q2"),     # 6-month YTD → not a quarter
        _fact("2025-04-28", "2025-07-27", 46743e6, "10-Q", "2025-08-27", 2026, "Q2")]}},
    "NetIncomeLoss": {"units": {"USD": [_fact("2024-01-29", "2025-01-26", 72880e6, "10-K", "2025-02-26", 2025, "FY")]}},
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [_fact("2024-01-29", "2025-01-26", 64089e6, "10-K", "2025-02-26", 2025, "FY")]}},
    "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [_fact("2024-01-29", "2025-01-26", 3236e6, "10-K", "2025-02-26", 2025, "FY")]}},
    "EarningsPerShareDiluted": {"units": {"USD/shares": [_fact("2024-01-29", "2025-01-26", 2.94, "10-K", "2025-02-26", 2025, "FY")]}},
    "Assets": {"units": {"USD": [{"end": "2025-01-26", "val": 111601e6, "form": "10-K", "filed": "2025-02-26", "fy": 2025, "fp": "FY"}]}},
}}}
SUBS = {"cik": "1045810", "name": "NVIDIA CORP", "filings": {"recent": {
    "accessionNumber": ["0001045810-25-000230", "0001045810-25-000228", "0001045810-25-000220"],
    "filingDate": ["2025-08-29", "2025-08-27", "2025-08-27"], "reportDate": ["", "2025-07-27", "2025-08-27"],
    "form": ["4", "10-Q", "8-K"], "primaryDocument": ["xslF345X05/wk-form4.xml", "nvda-20250727.htm", "nvda-20250827.htm"],
    "primaryDocDescription": ["", "10-Q", "8-K"], "items": ["", "", "2.02,9.01"]}}}


@pytest.fixture()
def E(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    monkeypatch.setenv("FT_SEC_USER_AGENT", "FT-Terminal test@example.com")
    from ft_common import edgar, market
    monkeypatch.setattr(market, "FAKE", False)
    calls = []

    def fake_get(url, ttl, name):
        calls.append(url)
        return TICKERS if "company_tickers" in url else FACTS if "companyfacts" in url else SUBS if "submissions" in url else None
    monkeypatch.setattr(edgar, "_get_json", fake_get)
    edgar._calls = calls
    return edgar


def test_needs_contact_user_agent(monkeypatch):
    from ft_common import edgar
    monkeypatch.delenv("FT_SEC_USER_AGENT", raising=False)
    with pytest.raises(edgar.NotConfigured):
        edgar.user_agent()
    monkeypatch.setenv("FT_SEC_USER_AGENT", "FT-Terminal")  # no e-mail → still refused
    assert not edgar.enabled()


def test_cik_and_statements(E):
    assert E.cik_of("NVDA") == 1045810 and E.cik_of("BRK.B") == 1067983
    assert E.cik_of("2330.TW") is None and E.cik_of("^GSPC") is None
    st = E.statements("NVDA", "annual")
    assert st["periods"][0] == {"end": "2025-01-26", "filed": "2025-02-26", "form": "10-K", "fp": "FY"}  # first filing, not the comparative
    rows = {r["label"]: r["values"] for r in st["rows"]}
    assert rows["營業收入"][:2] == [130497e6, 60922e6] and rows["每股盈餘（稀釋）"][0] == 2.94
    assert rows["自由現金流（計算）"][0] == pytest.approx(64089e6 - 3236e6)
    q = E.statements("NVDA", "quarterly")
    qrev = dict(zip([p["end"] for p in q["periods"]], next(r["values"] for r in q["rows"] if r["label"] == "營業收入")))
    assert qrev["2025-07-27"] == 46743e6  # the 3-month figure, not the 6-month YTD


def test_filed_date_and_filings(E):
    assert E.filed_date("NVDA", "2025-01-26") == "2025-02-26"
    assert E.filed_date("NVDA", "2025-01-28") == "2025-02-26"   # a couple of days off is fine
    assert E.filed_date("NVDA", "2020-01-26") is None
    f = E.recent_filings("NVDA")
    assert [x["form"] for x in f] == ["4", "10-Q", "8-K"]
    assert f[2]["items"][0] == {"code": "2.02", "zh": "營運成果與財務狀況（財報）"}
    assert f[1]["url"] == "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000228/nvda-20250727.htm"
    assert "內部人持股變動" in E.filings_text("NVDA")


def test_fred_csv_and_transforms(monkeypatch):
    from ft_common import fred
    s = fred.parse_csv("observation_date,DGS10\n2026-10-15,4.01\n2026-10-16,.\n2026-10-17,4.05\n")
    assert list(s.values) == [4.01, 4.05]
    s = fred.parse_csv("DATE,UNRATE\n2026-08-01,4.3\n2026-09-01,4.4\n")  # older header still works
    assert s.iloc[-1] == 4.4
    idx = pd.date_range("2024-01-01", periods=30, freq="MS")
    cpi = pd.Series([100 * 1.003 ** i for i in range(30)], index=idx)
    monkeypatch.setattr(fred, "raw", lambda sid: cpi)
    yoy = fred.transformed("CPIAUCSL")
    assert yoy.iloc[-1] == pytest.approx((1.003 ** 12 - 1) * 100)
    snap = fred.snapshot(["CPIAUCSL"])[0]
    assert snap["name"] == "CPI 年增率" and snap["value"] == pytest.approx(3.66, abs=0.01) and snap["spark"]


def test_fed_decision_labels(monkeypatch):
    from ft_common import fred
    days = pd.date_range("2026-09-10", "2026-09-25", freq="D")
    up = pd.Series([4.50 if d <= pd.Timestamp("2026-09-16") else 4.25 for d in days], index=days)
    lo = up - 0.25
    monkeypatch.setattr(fred, "raw", lambda sid: up if sid == "DFEDTARU" else lo)
    d = fred.fed_decision("2026-09-16")
    assert d["bp"] == -25 and d["title"] == "Fed 降息 1 碼（4.00%–4.25%）" and d["before_lower"] == 4.25
    flat = pd.Series([4.25] * len(days), index=days)
    monkeypatch.setattr(fred, "raw", lambda sid: flat if sid == "DFEDTARU" else flat - 0.25)
    assert fred.fed_decision("2026-09-16")["title"].startswith("Fed 維持利率不變")
    assert fred.fed_decision("2026-12-09") is None  # not happened yet


def test_fomc_events_get_the_actual_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    from ft_common import events, fred
    monkeypatch.setattr(events, "load_closes", lambda period: {})
    monkeypatch.setattr(events, "HISTORY", [])
    monkeypatch.setattr(events, "FOMC", ["2026-09-16", "2099-12-09"])
    monkeypatch.setattr(fred, "fed_decision", lambda d: {"bp": -25, "upper": 4.25, "lower": 4.0, "before_upper": 4.5,
                                                         "before_lower": 4.25, "title": "Fed 降息 1 碼（4.00%–4.25%）"})
    events.refresh("2y")
    rows = {e["id"]: e for e in events.list_events(since="2026-01-01", until="2100-01-01", world_too=False)}
    assert rows["fomc:2026-09-16"]["title_final"] == "Fed 降息 1 碼（4.00%–4.25%）"
    assert "4.25%–4.50% → 4.00%–4.25%" in rows["fomc:2026-09-16"]["summary_final"]
    assert rows["fomc:2099-12-09"]["title_final"] == "Fed 利率決策（FOMC）" and rows["fomc:2099-12-09"]["upcoming"]


def test_fred_falls_back_to_api_key_and_explains_failures(monkeypatch):
    from ft_common import fred, market, twhttp
    monkeypatch.setattr(market, "FAKE", False)
    calls = []

    def fake_fetch(url, params=None, headers=None, **kw):
        assert "Chrome" not in headers["User-Agent"]  # a browser UA from Python gets tarpitted by FRED's CDN
        calls.append((url, dict(params)))
        if "fredgraph" in url:
            return b"<html>Access Denied</html>"  # blocked → HTML, not CSV
        return json.dumps({"observations": [{"date": "2026-10-15", "value": "4.01"}, {"date": "2026-10-16", "value": "."},
                                            {"date": "2026-10-17", "value": "4.05"}]}).encode()
    monkeypatch.setattr(twhttp, "fetch", fake_fetch)
    monkeypatch.delenv("FT_FRED_API_KEY", raising=False)
    monkeypatch.setattr(fred, "_csv_down", {"until": 0.0, "why": ""})
    with pytest.raises(RuntimeError, match="FT_FRED_API_KEY"):
        fred.raw("ZZTEST1")
    assert calls[0][1]["cosd"] == fred.START
    n = len(calls)
    with pytest.raises(RuntimeError, match="10 分鐘內不重試"):
        fred.raw("ZZTEST3")
    assert len(calls) == n  # breaker: no second 60 s wait
    monkeypatch.setenv("FT_FRED_API_KEY", "k")
    s = fred.raw("ZZTEST2")
    assert list(s.values) == [4.01, 4.05] and calls[-1][1]["api_key"] == "k"
