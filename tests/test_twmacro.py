"""台灣總經：國發會 CSV、主計總處 SDMX-JSON、央行網頁表格、勞動部備援、API 與 agent。"""
import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))

NDC_CSV = ("﻿Date,景氣對策信號(分),景氣對策信號(燈號),領先指標綜合指數(點),領先指標不含趨勢指數(點),同時指標綜合指數(點)\n"
           "202606,38,紅燈,105.1,101.2,108.0\n202607,40,紅燈,105.6,101.5,108.4\n202608,36,黃紅燈,105.9,101.6,108.7\n")

SDMX_GDP = {"data": {"structure": {"dimensions": {
    "series": [{"id": "fldid", "values": [{"id": "1", "name": "當期價格(新臺幣百萬元)"}, {"id": "2", "name": "連鎖實質值(2021為參考年_新臺幣百萬元)"}]},
               {"id": "code1", "values": [{"id": "1", "name": "1.民間消費"}, {"id": "9", "name": "9.GDP : 1+2+3+4-5"}, {"id": "17", "name": "11.GNI"}]}],
    "observation": [{"id": "ym", "values": [{"id": f"{y}-Q{q}"} for y in (2024, 2025) for q in (1, 2, 3, 4)]}]}},
    "dataSets": [{"series": {
        "0:1": {"observations": {str(k): [1000 + k] for k in range(8)}},
        "1:1": {"observations": {str(k): [v] for k, v in enumerate([100, 100, 100, 100, 104, 105, 102, 110])}},
        "1:2": {"observations": {str(k): [999] for k in range(8)}}}}]}}

SDMX_CPI = {"data": {"structure": {"dimensions": {
    "series": [{"id": "code", "values": [{"id": "1", "name": "總指數"}, {"id": "2", "name": "一.食物類"}]}],
    "observation": [{"id": "ym", "values": [{"id": "2024"}] + [{"id": f"{y}-M{m}"} for y in (2024, 2025) for m in range(1, 13)]}]}},
    "dataSets": [{"series": {"0:0": {"observations": {"0": [99.0], **{str(k + 1): [100 + k * 0.2] for k in range(24)}}},
                             "0:1": {"observations": {"1": [50]}}}}]}}

CBC_HTML = """<html><head><title>中央銀行-中文版-首頁資訊-重要指標-重貼現率</title></head><body>
<table><tr><th>調整日期</th><th>重貼現率</th><th>擔保放款融通利率</th></tr>
<tr><td>2024/3/22</td><td>2</td><td>2.375</td></tr><tr><td>2023/3/24</td><td>1.875</td><td>2.25</td></tr></table></body></html>"""
CBC_M2 = """<html><head><title>重要指標-貨幣總計數M2年增率</title></head><body><table>
<tr><th>標題(日期)</th><th>年增率</th></tr><tr><td>2026.08</td><td>6.79</td></tr><tr><td>2026.07</td><td>7.42</td></tr></table></body></html>"""


def test_parse_helpers():
    from ft_common import twmacro as tm
    assert tm._num("r  1.73") == 1.73 and tm._num("  p     57,311") == 57311 and tm._num("…") is None
    assert tm._month("202605") == pd.Timestamp(2026, 5, 1) and tm._month("2026.08") == pd.Timestamp(2026, 8, 1)
    assert tm._month("115年3月") == pd.Timestamp(2026, 3, 1) and tm._day("2024/3/22") == pd.Timestamp(2024, 3, 22)
    assert tm.light(40)["name"] == "紅燈" and tm.light(32)["name"] == "黃紅燈" and tm.light(31)["name"] == "綠燈"
    assert tm.light(17)["name"] == "黃藍燈" and tm.light(16)["name"] == "藍燈" and tm.light(None) is None


def test_ndc_csv_and_zip(monkeypatch):
    from ft_common import market, twmacro as tm
    got = tm.parse_ndc_csv(NDC_CSV)
    assert list(got["SIGNAL"]) == [38, 40, 36] and got["LEADING"].iloc[-1] == 105.9 and got["COINCIDENT"].iloc[0] == 108.0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.csv", "name\n")
        z.writestr("景氣指標與燈號.csv", NDC_CSV.encode("cp950", errors="ignore"))
    meta = {"result": {"distribution": [{"resourceDownloadUrl": "https://ws.ndc.gov.tw/Download.ashx?u=x"}]}}
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(tm, "_get", lambda url, **kw: json.dumps(meta).encode() if "data.gov.tw" in url else buf.getvalue())
    market._cache.clear(); tm._neg.clear()
    assert tm.raw("SIGNAL").iloc[-1] == 36
    row = tm.snapshot(["SIGNAL"])[0]
    assert row["light"]["name"] == "黃紅燈" and row["prev_light"]["name"] == "紅燈" and row["date"] == "2026-08"


def test_sdmx_gdp_cpi(monkeypatch):
    from ft_common import market, twmacro as tm
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(tm, "_get", lambda url, **kw: json.dumps(SDMX_GDP if "A018102060" in url else SDMX_CPI).encode())
    market._cache.clear(); tm._neg.clear()
    g = tm.raw("GDP")
    assert g.index[0] == pd.Timestamp(2025, 1, 1) and g.iloc[-1] == pytest.approx(10.0) and g.iloc[0] == pytest.approx(4.0)
    c = tm.raw("CPI")  # annual "2024" period skipped; yoy from the monthly index
    assert len(c) == 12 and c.iloc[-1] == pytest.approx(((100 + 23 * 0.2) / (100 + 11 * 0.2) - 1) * 100)
    with pytest.raises(RuntimeError):
        tm.raw("CORE")  # no 不含蔬果及能源 series in either table → explicit error, not a guess
    assert "GDP" in tm._diag["gdp"]


def test_sdmx_tries_filter_variants(monkeypatch):
    from ft_common import market, twmacro as tm
    seen = []

    def fake(url, **kw):
        seen.append(url)
        return "參數錯誤(Parameter error)".encode() if url.endswith("A030101015") else json.dumps(SDMX_CPI).encode()
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(tm, "_get", fake)
    market._cache.clear(); tm._neg.clear()
    assert tm.sdmx("A030101015") and len(seen) == 2 and "startTime" in seen[1]


def test_cbc_tables_and_unemp_fallback(monkeypatch):
    from ft_common import market, twmacro as tm
    mol = [{"日期（月別）": "202604", "失業率（百分比）": "3.30"}, {"日期（月別）": "202605", "失業率（百分比）": "3.27"}]
    meta = {"result": {"distribution": [{"resourceFormat": "JSON", "resourceDownloadUrl": "https://apiservice.mol.gov.tw/x"}]}}

    def fake(url, **kw):
        if "lp-640" in url:
            return CBC_HTML.encode()
        if "lp-643" in url:
            return CBC_M2.encode()
        if "data.gov.tw" in url:
            return json.dumps(meta).encode()
        if "mol.gov.tw" in url:
            return json.dumps(mol).encode()
        return b"<html><title>other</title></html>" if "cbc" in url else "功能代號不存在".encode()
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(tm, "_get", fake)
    market._cache.clear(); tm._neg.clear()
    d = tm.raw("DISCOUNT")
    assert list(d) == [1.875, 2.0]
    row = tm.snapshot(["DISCOUNT"])[0]
    assert row["value"] == 2.0 and row["changed"] == "2024-03-22"
    h = tm.history("DISCOUNT", 1)
    assert h["step"] and h["points"][-1]["value"] == 2.0 and len(h["points"]) == 2  # level at left edge + today
    assert tm.raw("M2").iloc[-1] == 6.79
    with pytest.raises(RuntimeError, match="M1B"):
        tm.raw("M1B")
    u = tm.raw("UNEMP")
    assert u.iloc[-1] == 3.27 and "勞動部" in tm._diag["unemp"]


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
    d = c.get("/api/twmacro").json()
    ids = {r["id"] for r in d["rows"] if "value" in r}
    assert ids == set(appmod.twmacro.SERIES) and d["groups"][0] == "景氣" and len(d["lights"]) == 5
    sig = next(r for r in d["rows"] if r["id"] == "SIGNAL")
    assert 9 <= sig["value"] <= 45 and sig["light"]["name"]
    s = c.get("/api/twmacro/series", params={"id": "SIGNAL", "years": 5}).json()
    assert s["points"] and all(p["color"] for p in s["points"])
    assert c.get("/api/twmacro/series", params={"id": "NOPE"}).status_code == 404
    assert c.get("/api/twmacro/series", params={"id": "DISCOUNT", "years": 3}).json()["step"]
    t = c.get("/api/agent/twmacro").text
    assert "台灣總經" in t and "景氣對策信號" in t and "重貼現率" in t
    assert "series" in c.get("/api/twmacro/diag").json()
