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
    assert tm.sdmx("A030101015") and "startTime" in seen[1] and "/1.M" in seen[1]


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
    with pytest.raises(Exception):
        tm.raw("M1B")  # no statistical-database answer → explicit error (M2 still falls back to the front-page table)
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


MONEY = {"meta": {"filename": "EF15M01en"}, "data": {"dataSets": [
    ["2025M08", "1000", "3.0", "5", "-", "200000", "4.1", "288000", "5.20", "662000", "5.50"],
    ["2026M07", "1100", "3.5", "5", "-", "210000", "4.6", "305000", "7.10", "703000", "7.42"],
    ["2026M08", "1110", "3.6", "6", "-", "212000", "4.8", "307508", "6.65", "706780", "6.79"]]}}


def test_money_from_cbc_api(monkeypatch):
    from ft_common import market, twmacro as tm
    d = tm.parse_money(MONEY)
    assert d["M1B"].iloc[-1] == 6.65 and d["M2"].iloc[-1] == 6.79 and d["M1B"].index[-1] == pd.Timestamp(2026, 8, 1)
    bad = {"data": {"dataSets": [[f"2026M0{i}", "9", "1", "1", "1", "9", "1", "1", "1", "5", "1"] for i in range(1, 8)]}}
    with pytest.raises(RuntimeError):
        tm.parse_money(bad)  # columns not in M1A < M1B < M2 order → refuse rather than show the wrong series
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(tm, "_get", lambda url, **kw: json.dumps(MONEY).encode())
    market._cache.clear(); tm._neg.clear()
    assert tm.raw("M1B").iloc[-1] == 6.65 and tm.raw("M2").iloc[-1] == 6.79 and "EF15M01en" in tm._diag["money"]


def test_cpi_skips_zero_padded_tables(monkeypatch):
    from ft_common import market, twmacro as tm
    old = {"data": {"structure": {"dimensions": {"series": [{"values": [{"name": "總指數"}]}],
           "observation": [{"values": [{"id": f"{y}-M{m}"} for y in (2021, 2022, 2023) for m in range(1, 13)]}]}},
           "dataSets": [{"series": {"0": {"observations": {str(k): [100 + k if k < 24 else 0] for k in range(36)}}}}]}}
    new = {"data": {"structure": {"dimensions": {"series": [{"values": [{"name": "總指數"}]}],
           "observation": [{"values": [{"id": f"{y}-M{m}"} for y in (2024, 2025, 2026) for m in range(1, 13)]}]}},
           "dataSets": [{"series": {"0": {"observations": {str(k): [100 + k * 0.2] for k in range(33)}}}}]}}
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(tm, "_get", lambda url, **kw: json.dumps(old if "A030101019" in url else new).encode())
    market._cache.clear(); tm._neg.clear()
    c = tm.raw("CPI")
    assert c.index[-1] == pd.Timestamp(2026, 9, 1) and (c > -50).all()  # never the -100% of a zero-padded table


def _one(name, vals):
    return {"data": {"structure": {"dimensions": {
        "series": [{"id": "code", "values": [{"id": "1", "name": name}]}],
        "observation": [{"id": "ym", "values": [{"id": f"{y}-M{m}"} for y in (2024, 2025) for m in range(1, 13)]}]}},
        "dataSets": [{"series": {"0": {"observations": {str(k): [v] for k, v in enumerate(vals)}}}}]}}


def test_core_cpi_walks_first_dimension_codes(monkeypatch):
    """Real API: bare query of a big table → only 1 series; '/1.M' → code 1; codes 2, 3 … hold the rest."""
    from ft_common import market, twmacro as tm
    tables = {"1": _one("總指數(不含食物)", [100 + k * 0.3 for k in range(24)]),
              "2": _one("總指數(不含新鮮蔬果及能源)", [100 + k * 0.1 for k in range(24)])}
    seen = []

    def fake(url, **kw):
        seen.append(url)
        if "A030103015" not in url:
            return "參數錯誤(Parameter error)".encode()
        tail = url.split("A030103015", 1)[1]
        if tail == "":
            return json.dumps(tables["1"]).encode()
        code = tail[1:].split(".", 1)[0]
        if tail.startswith(f"/{code}.M&") and code in tables:
            return json.dumps(tables[code]).encode()
        return "參數錯誤(Parameter error)".encode()
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(tm, "_get", fake)
    market._cache.clear(); tm._neg.clear()
    s = tm.core()
    assert abs(s.iloc[-1] - (1.2 / 101.1 * 100)) < 0.01
    assert "代碼 1–2" in tm._diag["sdmx:A030103015"]


def test_pick_error_lists_series_names(monkeypatch):
    from ft_common import market, twmacro as tm
    monkeypatch.setattr(market, "FAKE", False)
    monkeypatch.setattr(tm, "_get", lambda url, **kw: json.dumps(SDMX_CPI).encode())
    market._cache.clear(); tm._neg.clear()
    with pytest.raises(RuntimeError, match="總指數"):
        tm.core()
