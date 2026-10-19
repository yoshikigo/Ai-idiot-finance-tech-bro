"""Offline tests for the Taiwan data parsers (no network).

SITCA fixtures follow TWSEMCPServer's tests/test_etf_unit.py (MIT), which are checked
against the live pages by that project's e2e suite.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("FT_DATA", "/tmp/ft-test-data")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

from ft_common import funds, sitca, twmarket  # noqa: E402

# ------------------------------------------------------------------ ISIN list
ISIN_HTML = """<html><body><table class='h4' align=center cellSpacing=3 cellPadding=2 width=750 border=0>
<tr align=center><td bgcolor=#D5FFD5>有價證券代號及名稱 </td><td bgcolor=#D5FFD5>國際證券辨識號碼(ISIN Code)</td>
<td bgcolor=#D5FFD5>上市日</td><td bgcolor=#D5FFD5>市場別</td><td bgcolor=#D5FFD5>產業別</td><td bgcolor=#D5FFD5>CFICode</td><td bgcolor=#D5FFD5>備註</td></tr>
<tr><td bgcolor=#FAFAD2 colspan=7 ><B> 股票 <B> </td></tr>
<tr><td bgcolor=#FAFAD2>1101　台泥</td><td bgcolor=#FAFAD2>TW0001101004</td><td bgcolor=#FAFAD2>1962/02/09</td><td bgcolor=#FAFAD2>上市</td><td bgcolor=#FAFAD2>水泥工業</td><td bgcolor=#FAFAD2>ESVUFR</td><td bgcolor=#FAFAD2></td></tr>
<tr><td bgcolor=#FAFAD2>2330　台積電</td><td bgcolor=#FAFAD2>TW0002330008</td><td bgcolor=#FAFAD2>1994/09/05</td><td bgcolor=#FAFAD2>上市</td><td bgcolor=#FAFAD2>半導體業</td><td bgcolor=#FAFAD2>ESVUFR</td><td bgcolor=#FAFAD2></td></tr>
<tr><td bgcolor=#FAFAD2 colspan=7 ><B> 上市認購(售)權證 <B> </td></tr>
<tr><td bgcolor=#FAFAD2>030001　台積電凱基58購01</td><td bgcolor=#FAFAD2>TW18Z0300011</td><td bgcolor=#FAFAD2>2026/01/02</td><td bgcolor=#FAFAD2>上市</td><td bgcolor=#FAFAD2></td><td bgcolor=#FAFAD2>RWSCCE</td><td bgcolor=#FAFAD2></td></tr>
<tr><td bgcolor=#FAFAD2 colspan=7 ><B> ETF <B> </td></tr>
<tr><td bgcolor=#FAFAD2>0050　元大台灣50</td><td bgcolor=#FAFAD2>TW0000050004</td><td bgcolor=#FAFAD2>2003/06/30</td><td bgcolor=#FAFAD2>上市</td><td bgcolor=#FAFAD2></td><td bgcolor=#FAFAD2>CEOGEU</td><td bgcolor=#FAFAD2></td></tr>
<tr><td bgcolor=#FAFAD2>00631L　元大台灣50正2</td><td bgcolor=#FAFAD2>TW00000631L9</td><td bgcolor=#FAFAD2>2014/10/31</td><td bgcolor=#FAFAD2>上市</td><td bgcolor=#FAFAD2></td><td bgcolor=#FAFAD2>CEOJLU</td><td bgcolor=#FAFAD2></td></tr>
</table></body></html>"""


def test_isin_parsing_keeps_stocks_and_etfs_skips_warrants():
    d = twmarket.parse_isin(ISIN_HTML, "上市")
    assert set(d) == {"1101", "2330", "0050", "00631L"}
    assert d["2330"]["name"] == "台積電" and d["2330"]["industry"] == "半導體業" and d["2330"]["symbol"] == "2330.TW"
    assert d["0050"]["kind"] == "ETF" and d["0050"]["listed"] == "2003-06-30"
    otc = twmarket.parse_isin(ISIN_HTML.replace("<td bgcolor=#FAFAD2>上市</td>", "<td bgcolor=#FAFAD2>上櫃</td>"), "上櫃")
    assert otc["2330"]["symbol"] == "2330.TWO"


def test_isin_big5_decoding():
    from ft_common.twhttp import decode_tw
    assert "台積電" in decode_tw(ISIN_HTML.encode("cp950"))
    assert "台積電" in decode_tw(("﻿" + ISIN_HTML).encode("utf-8"))


# ------------------------------------------------------------------ MIS
def test_mis_realtime(monkeypatch):
    payload = {"msgArray": [
        {"c": "2330", "n": "台積電", "z": "1010.0000", "y": "1000.0000", "o": "1002.0000", "h": "1015.0000",
         "l": "998.0000", "v": "23456", "u": "1100.0000", "w": "900.0000", "d": "20261019", "t": "13:30:00", "ex": "tse"},
        {"c": "00679B", "n": "元大美債20年", "z": "-", "y": "28.5000", "b": "28.4000_28.3900_", "a": "28.4200_28.4300_",
         "v": "5000", "d": "20261019", "t": "10:01:05", "ex": "otc"},
        {"c": "t00", "n": "發行量加權股價指數", "z": "23456.78", "y": "23400.00", "v": "345678", "d": "20261019", "t": "13:30:00"},
        {"c": "", "n": None, "z": "-"},
    ]}
    seen = {}

    def fake(url, params=None, **kw):
        seen["ex_ch"] = params["ex_ch"]
        return payload
    monkeypatch.setattr(twmarket, "FAKE", False)
    monkeypatch.setattr(twmarket, "fetch_json", fake)
    q = twmarket.realtime(["2330.TW", "00679B.TWO", "^TWII"])
    assert seen["ex_ch"] == "tse_2330.tw|otc_00679b.tw|tse_t00.tw"
    assert q["2330.TW"]["last"] == 1010 and round(q["2330.TW"]["pct"], 2) == 1.0 and q["2330.TW"]["volume"] == 23456000
    assert q["00679B.TWO"]["indicative"] and abs(q["00679B.TWO"]["last"] - 28.41) < 1e-9
    assert q["^TWII"]["last"] == 23456.78 and q["^TWII"]["volume"] == 345678


# ------------------------------------------------------------------ all_etf.txt
ALL_ETF = {"a1": [
    {"msgArray": [
        {"a": "0050", "b": "元大台灣50", "c": 22448000000, "d": 39000000, "e": 113.0, "f": 112.42, "g": 0.52,
         "h": "112.0600", "i": "20261014", "j": "13:31:00", "k": "1"},
        {"a": "00631L", "b": "元大台灣50正2", "c": "1,000,000", "d": "-5000", "e": "300.1", "f": "301.2", "g": "-0.37",
         "h": "未結出", "i": "20261014", "j": "13:31:00", "k": "1"}],
     "refURL": "https://www.yuantaetfs.com", "userDelay": "15000", "rtMessage": "OK", "rtCode": "0000"},
    {"msgArray": [{"a": "00679B", "b": "元大美債20年", "c": "5000000000", "d": "12000000", "e": "28.41", "f": "28.30",
                   "g": "0.39", "h": "28.2500", "i": "20261014", "j": "13:30:05", "k": "3"}]},
]}


def test_all_etf_parsing_and_table(monkeypatch):
    rows = {r["code"]: r for r in twmarket.parse_all_etf(ALL_ETF)}
    assert rows["0050"]["scale"] == 22448000000 * 112.06
    assert rows["00631L"]["prior_nav"] is None and rows["00631L"]["scale"] == 1_000_000 * 301.2
    assert rows["00631L"]["units_chg"] == -5000
    monkeypatch.setattr(twmarket, "etf_nav", lambda: twmarket.parse_all_etf(ALL_ETF))
    monkeypatch.setattr(twmarket, "securities", lambda: {})
    t = twmarket.etf_table("premium")
    assert [r["code"] for r in t["rows"]] == ["0050", "00679B", "00631L"]
    assert twmarket.etf_table(category="債券")["rows"][0]["code"] == "00679B"
    assert twmarket.etf_table(category="槓桿/反向")["count"] == 1


def test_etf_category():
    assert twmarket.etf_category("00679B") == "債券"
    assert twmarket.etf_category("00631L") == "槓桿/反向"
    assert twmarket.etf_category("00878", "國泰永續高股息") == "高股息"
    assert twmarket.etf_category("00981A", "主動統一台股增長") == "主動式"
    assert twmarket.etf_category("0050", "元大台灣50") == "股票型"


# ------------------------------------------------------------------ SITCA
STATEMENT_HEADER = ["序號", "類型代號", "證券代號", "證券簡稱", "基金統編", "基金名稱", "類型", "計價幣別", "基金規模(台幣)"]
HOLD_HEADER = ["基金名稱", "名次", "標的種類", "標的代號 或<br>Bloomberg<br>Isin Code", "標的名稱", "金額", "擔保機構**",
               "次順位債券**", "受益權單位數***", "占基金淨資產價值<br>之比例(%)"]


def _month_page(months, name="ddlYM"):
    opts = "".join(f'<option value="{m}">{m}</option>' for m in months)
    return f'<select name="ctl00$ContentPlaceHolder1${name}" id="x">{opts}</select>'


def _grid(header, rows):
    cells = lambda r, tag: "".join(f"<{tag}>{c}</{tag}>" for c in r)  # noqa: E731
    body = "".join(f"<tr>{cells(r, 'td')}</tr>" for r in rows)
    total = f"<tr>{cells(['合計'] + ['-'] * (len(header) - 1), 'td')}</tr>"
    grid = f'<table id="ctl00_ContentPlaceHolder1_GridView1"><tr>{cells(header, "th")}</tr>{body}{total}</table>'
    return f'<table id="table2"><tr><td><div>{grid}</div></td></tr></table>'


def _holdings_html(funds_):
    head = "".join(f"<td class=DTHeader nowrap align=center>{h}</th>" for h in HOLD_HEADER)
    body = ""
    for name, rows in funds_:
        for i, r in enumerate(rows):
            first = f"<td class='DTeven' rowspan='{len(rows)}'>{name}</td>" if i == 0 else ""
            body += "<tr>" + first + "".join(f"<td class='DTeven'>{c}</td>" for c in r) + "</tr>"
    return f"<table id='outer'><tr><td><table><tr>{head}</tr>{body}</table></td></tr></table>"


def _hold_row(rank, code, name, amount, pct, kind="國內上市"):
    return [rank, kind, code, name, amount, "", "", "0", pct]


def test_sitca_holdings_flow():
    calls = []

    def fake(url, params=None, data=None, method="GET"):
        calls.append((url.rsplit("/", 1)[1], method))
        page = url.rsplit("/", 1)[1]
        if page == "etf_statement.aspx":
            return _month_page(["202609", "202608"]).encode()
        if page == "etf_statement2.aspx":
            assert params["txtemcode"] == "0050,"
            return _grid(STATEMENT_HEADER, [["1", "AH11", "0050", "元大台灣50", "111", "元大台灣卓越50證券投資信託基金", "國內成分", "TWD", "1"]]).encode()
        if page == "IN2629.aspx":
            if method == "GET":
                return ('<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="vs0" />' + _month_page(["202609", "202608"], "ddlQ_YM")).encode()
            if data.get("__EVENTTARGET"):
                assert data["__VIEWSTATE"] == "vs0"
                return ('<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="vs1" />').encode()
            assert data["__VIEWSTATE"] == "vs1" and data["ctl00$ContentPlaceHolder1$ddlQ_Class"] == "AH11"
            return _holdings_html([
                ("別檔基金", [_hold_row("1", "2317", "鴻海", "1", "1")]),
                ("元大台灣卓越50證券投資信託基金 (本基金之配息來源可能為收益平準金)", [
                    _hold_row("1", "2330", "台積電", "1,367,244,000,000", "57.35"),
                    _hold_row("2", "2317", "鴻海", "100,000,000,000", "4.40")]),
            ]).encode()
        raise AssertionError(page)

    sitca.set_fetcher(fake)
    try:
        h = sitca.etf_holdings("0050")
    finally:
        sitca.set_fetcher(None)
    assert h["period"] == "202609" and h["total_pct"] == 61.75
    assert [i["name"] for i in h["items"]] == ["台積電", "鴻海"] and h["items"][0]["amount"] == 1.367244e12
    assert sitca.fund_name_key("國泰…基金-新台幣(配息)") == "國泰…基金"


def test_sitca_query_rows_drops_total_and_detects_layout_change():
    sitca.set_fetcher(lambda url, params=None, data=None, method="GET":
                      (_grid(["序號", "證券代號"], [["1", "0050"]]) if "2.aspx" in url else "<html/>").encode())
    try:
        assert sitca.query_rows("etf_info2.aspx", "202609") == [["序號", "證券代號"], ["1", "0050"]]
        try:
            sitca.available_months("etf_info.aspx")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "改版" in str(e)
    finally:
        sitca.set_fetcher(None)


# ------------------------------------------------------------------ funds CSV
DOMESTIC = ("﻿日期,基金統編,基金代號,基金名稱,基金淨值,幣別,公司名稱,受益憑證代號\n"
            "20261016,12345678,DIM01,兆豐人民幣貨幣市場基金,13.706,CNY,兆豐投信,T0118Y\n"
            "20261015,12345678,DIM01,兆豐人民幣貨幣市場基金,13.705,CNY,兆豐投信,T0118Y\n"
            "20261016,87654321,AL01,安聯台灣科技基金,\"1,234.56\",TWD,安聯投信,T0101A\n"
            "20261016,99999999,XX01,壞資料基金,--,TWD,某投信,T9\n")
OFFSHORE = ("日期,基金代碼,基金名稱,ISINCODE,基金淨值(金額),計價幣別,境外基金機構\n"
            "20261016,AB01,安聯收益成長基金-AM穩定月收類股(美元),LU0820561818,8.51,USD,安聯環球投資\n")
FUTURES = ("淨值日期,基金代碼,基金名稱,淨值,幣別,期信機構\n"
           "1151016,FUT01,元大道瓊白銀ER期貨信託基金,25.12,TWD,元大期信\n")


def test_fund_csv_parsing(tmp_path, monkeypatch):
    d = funds.parse_csv(DOMESTIC.lstrip("﻿"), "境內")
    assert len(d) == 3 and d[0]["id"] == "D:12345678" and d[0]["date"] == "2026-10-16" and d[0]["isin"] == "T0118Y"
    assert d[2]["nav"] == 1234.56
    o = funds.parse_csv(OFFSHORE, "境外")
    assert o[0]["id"] == "O:LU0820561818" and o[0]["currency"] == "USD" and o[0]["company"] == "安聯環球投資"
    f = funds.parse_csv(FUTURES, "期信")
    assert f[0]["id"] == "F:FUT01" and f[0]["date"] == "2026-10-16" and f[0]["nav"] == 25.12
    monkeypatch.setattr(funds, "DATA", tmp_path)
    monkeypatch.setattr(funds, "DB", tmp_path / "f.sqlite")
    monkeypatch.setattr(funds, "_quick_done", True)
    funds._upsert(d, "境內")
    funds._upsert(o, "境外")
    r = funds.search("兆豐 人民幣")
    assert len(r) == 1 and r[0]["nav"] == 13.706 and round(r[0]["pct"], 4) == round((13.706 / 13.705 - 1) * 100, 4)
    assert funds.search("LU0820561818")[0]["region"] == "境外"
    h = funds.history("D:12345678")
    assert h["points"] == [["2026-10-15", 13.705], ["2026-10-16", 13.706]]
    assert funds.returns([["2026-01-01", 10.0], ["2026-10-16", 11.0]])["今年"] == 10.0


# ------------------------------------------------------------------ Google News RSS
def test_google_news_rss(monkeypatch):
    rss = """<?xml version="1.0"?><rss><channel>
    <item><title>台積電法說會 毛利率創高 - 經濟日報</title><link>https://news.google.com/x</link>
    <pubDate>Mon, 19 Oct 2026 02:00:00 GMT</pubDate><source url="https://money.udn.com">經濟日報</source></item>
    <item><title>外資買超台積電 - 鉅亨網</title><link>https://news.google.com/y</link>
    <pubDate>Sun, 18 Oct 2026 02:00:00 GMT</pubDate><source url="https://news.cnyes.com">鉅亨網</source></item>
    </channel></rss>"""
    monkeypatch.setattr(twmarket, "FAKE", False)
    monkeypatch.setattr(twmarket, "fetch", lambda url, **kw: rss.encode())
    n = twmarket.news_zh("台積電 2330-test")
    assert n[0]["title"] == "台積電法說會 毛利率創高" and n[0]["publisher"] == "經濟日報"
    assert n[0]["time"].startswith("2026-10-19")


def test_domestic_fund_isin_from_sitca_code():
    # 安聯台灣科技基金 T3604Y → TW000T3604Y3 (as listed by cnyes); Apple as a known control
    assert funds.isin_check_digit("US037833100") == "5"
    assert funds.fund_isin({"region": "境內", "code": "T3604Y"}) == "TW000T3604Y3"
    assert funds.fund_isin({"region": "境外", "code": "x", "isin": "LU0011111111"}) == "LU0011111111"
    assert funds.fund_isin({"region": "期信", "code": ""}) is None


def test_domestic_fund_history_backfilled_once(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    f = importlib.reload(funds)
    monkeypatch.setattr(f.market, "FAKE", False)
    monkeypatch.setattr(f, "ensure_quick", lambda: None)
    monkeypatch.setattr(f, "refresh", lambda *a, **k: {})
    f._upsert([{"id": "D:UNITTEST01", "region": "境內", "code": "T3604Y", "isin": "", "tax_id": "UNITTEST01",
                "name": "安聯台灣科技基金", "company": "安聯投信", "currency": "TWD", "date": "2026-10-16", "nav": 788.11}], "sitca")
    calls = []

    def fake_yf(fid, isin):
        calls.append(isin)
        return [(f"2025-{m:02d}-15", 500.0 + m * 10) for m in range(1, 13)] + [("2026-10-16", 700.0)]
    monkeypatch.setattr(f, "_yf_history", fake_yf)
    h = f.history("D:UNITTEST01")
    assert calls == ["TW000T3604Y3"] and len(h["points"]) == 13 and "Yahoo" in h["source"]
    assert h["points"][-1] == ["2026-10-16", 788.11]  # the official NAV wins over Yahoo's
    r = f.search("安聯 科技")[0]
    assert r["r1y"] is not None and r["n_navs"] == 13
    f.history("D:UNITTEST01")
    assert len(calls) == 1  # cached, not refetched
    importlib.reload(funds)


def test_english_news_fallback_for_indices(monkeypatch):
    from ft_common import market
    monkeypatch.setattr(market, "FAKE", False)
    market._cache.clear() if hasattr(market, "_cache") else None
    calls = []

    def yf(sym, n):
        calls.append(sym)
        return [] if sym == "^GSPC" else [{"title": f"{sym} rallies", "publisher": "Yahoo", "link": "x", "time": "2026-10-19", "summary": ""}] * 3
    monkeypatch.setattr(market, "_yf_news", yf)
    monkeypatch.setattr(market, "news_en", lambda q, n: [{"title": "should not be needed", "publisher": "G", "link": "", "time": None, "summary": ""}])
    out = market.news("^GSPC", 10)
    assert calls == ["^GSPC", "SPY"] and out[0]["title"] == "SPY rallies"
    monkeypatch.setattr(market, "_yf_news", lambda sym, n: [])
    monkeypatch.setattr(market, "news_en", lambda q, n: [{"title": f"G: {q}", "publisher": "G", "link": "", "time": None, "summary": ""}])
    assert market.news("^SOX", 10)[0]["title"] == "G: semiconductor stocks"


def test_jsonable_makes_results_strict_json_safe():
    import datetime as dt
    import json as _json

    import numpy as np
    from ft_common.engine import jsonable
    o = {"a": float("nan"), "b": [np.float32(1.5), np.int64(3), float("inf")], "c": {1: np.bool_(True)},
         "d": dt.date(2026, 10, 20), "e": np.array([1.0, np.nan])}
    out = jsonable(o)
    assert out == {"a": None, "b": [1.5, 3, None], "c": {"1": True}, "d": "2026-10-20", "e": [1.0, None]}
    _json.dumps(out, allow_nan=False)


def test_half_filled_yahoo_bar_does_not_break_history_api(monkeypatch, tmp_path):
    import numpy as np
    import pandas as pd
    from ft_common import market
    raw = pd.DataFrame({"Open": [10.0, np.nan, np.nan], "High": [11.0, 12.0, np.nan], "Low": [9.0, np.nan, np.nan],
                        "Close": [10.5, 11.5, np.nan], "Volume": [100.0, np.nan, 5.0]},
                       index=pd.to_datetime(["2026-10-16", "2026-10-19", "2026-10-20"]))
    df = market.clean_ohlcv(raw)
    assert len(df) == 2 and df.iloc[1].tolist() == [11.5, 12.0, 11.5, 11.5, 0.0]
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "terminal"))
    import app as appmod
    from fastapi.testclient import TestClient
    monkeypatch.setattr(appmod.market, "history", lambda s, r, i: raw)  # even the raw frame must not 500
    r = TestClient(appmod.app).get("/api/history", params={"s": "2330.TW"})
    assert r.status_code == 200 and len(r.json()) == 2
