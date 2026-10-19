"""策略研究：parsers (fixtures in the real source formats), expressions, point-in-time panels, backtest maths."""
import datetime as dt
import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))

from ft_common.research import sources  # noqa: E402

# ---------------------------------------------------------------- fixtures (shapes copied from the live feeds)
BWIBBU = {"stat": "OK", "date": "20261016", "title": "115年10月16日 個股日本益比、殖利率及股價淨值比",
          "fields": ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"],
          "data": [["1101", "台泥", "27.30", "2.93", 114, "-", "0.88", "115/2"],
                   ["1102", "亞泥", "38.20", "6.02", 114, "10.55", "0.73", "115/2"]]}
T86 = {"stat": "OK", "date": "20261016", "hints": "單位：股",
       "fields": ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
                  "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
                  "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
                  "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)",
                  "自營商買賣超股數(避險)", "三大法人買賣超股數"],
       "data": [["2002  ", "中鋼            ", "48,436,667", "4,707,721", "43,728,946", "0", "0", "0", "30,000", "3,000",
                 "27,000", "506,725", "52,000", "2,000", "50,000", "632,725", "176,000", "456,725", "44,262,671"]]}
MARGN = {"stat": "OK", "date": "20261016", "tables": [
    {"title": "115年10月16日 信用交易統計", "fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
     "data": [["融資(交易單位)", "343,600", "318,542", "7,624", "9,521,230", "9,538,664"]]},
    {"title": "115年10月16日 融資融券彙總 (全部)",
     "fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額", "買進", "賣出", "現券償還",
                "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
     "data": [["2330", "台積電", "169", "678", "70", "11,631", "11,052", "503,285", "0", "0", "0", "0", "33", "503,285", "0", " "]]}]}
TPEX_OLD_INSTI = {"reportDate": "115/10/16", "aaData": [
    ["6488", "環球晶", "100,000", "40,000", "60,000", "0", "0", "0", "100,000", "40,000", "60,000",
     "5,000", "1,000", "4,000", "2,000", "500", "1,500", "1,000", "0", "1,000", "3,000", "500", "2,500", "68,000"]]}
TPEX_OLD_MARGIN = {"aaData": [["6488", "環球晶", "1,000", "50", "40", "0", "1,010", "", "1.2", "", "100", "5", "3", "0", "102",
                               "", "0.1", "", "0", ""]]}
TPEX_OLD_PE = {"aaData": [["6488", "環球晶", "18.50", "8.00", "114", "2.10", "2.40"]]}
TWSE_HOLIDAY = {"stat": "很抱歉，沒有符合條件的資料!"}
REVENUE_HTML = """<html><body><table><tr><th>產業別：水泥工業</th></tr><tr><td>
<table class='hasBorder'>
<tr><th rowspan=2>公司代號</th><th rowspan=2>公司名稱</th><th colspan=5>營業收入</th><th colspan=3>累計營業收入</th><th rowspan=2>備註</th></tr>
<tr><th>當月營收</th><th>上月營收</th><th>去年當月營收</th><th>上月比較<br>增減(%)</th><th>去年同月<br>增減(%)</th>
<th>當月累計營收</th><th>去年累計營收</th><th>前期比較<br>增減(%)</th></tr>
<tr align=right><td align=center>1101</td><td align=left>台泥</td><td>&nbsp;12,345,678</td><td>11,000,000</td><td>10,000,000</td>
<td>12.23</td><td>23.45</td><td>98,765,432</td><td>90,000,000</td><td>9.74</td><td>-</td></tr>
<tr><th>合計</th><td>1</td></tr>
</table></td></tr></table></body></html>"""
INCOME_HTML = """<table class='hasBorder'><tr class='tblHead'><th>公司<br>代號</th><th>公司名稱</th><th>營業收入</th><th>營業成本</th>
<th>營業毛利（毛損）</th><th>營業利益（損失）</th><th>本期淨利（淨損）</th><th>淨利（淨損）歸屬於母公司業主</th><th>基本每股盈餘（元）</th></tr>
<tr class='even'><td>2330</td><td>台積電</td><td>839,253,664</td><td>350,000,000</td><td>489,253,664</td><td>400,000,000</td>
<td>360,000,000</td><td>359,000,000</td><td>13.95</td></tr></table>
<table class='hasBorder'><tr class='tblHead'><th>公司代號</th><th>公司名稱</th><th>利息淨收益</th><th>淨收益</th><th>本期稅後淨利（淨損）</th><th>基本每股盈餘（元）</th></tr>
<tr class='odd'><td>2881</td><td>富邦金</td><td>50,000</td><td>120,000</td><td>40,000</td><td>3.10</td></tr></table>"""
BALANCE_HTML = """<table><tr><th>公司代號</th><th>公司名稱</th><th>資產總計</th><th>負債總計</th><th>股本</th><th>歸屬於母公司業主之權益合計</th><th>權益總計</th></tr>
<tr><td>2330</td><td>台積電</td><td>6,000,000,000</td><td>2,000,000,000</td><td>259,320,000</td><td>3,950,000,000</td><td>4,000,000,000</td></tr></table>"""


def test_valuation_twse_and_old_tpex():
    v = sources.parse_valuation(BWIBBU)
    assert v["1101"] == {"pe": None, "dy": 2.93, "pb": 0.88}
    assert v["1102"]["pe"] == 10.55
    assert sources.parse_valuation(TPEX_OLD_PE)["6488"] == {"pe": 18.5, "dy": 2.1, "pb": 2.4}


def test_insti_by_header_in_lots():
    v = sources.parse_insti(T86)["2002"]
    assert v["foreign"] == pytest.approx(43728.946) and v["trust"] == 27 and v["dealer"] == pytest.approx(506.725)
    assert v["total"] == pytest.approx(44262.671)
    o = sources.parse_insti(TPEX_OLD_INSTI)["6488"]
    assert o["foreign"] == 60 and o["trust"] == 4 and o["dealer"] == 2.5 and o["total"] == 68


def test_margin_picks_the_per_stock_table():
    m = sources.parse_margin(MARGN)
    assert m == {"2330": {"margin": 11052.0, "short": 33.0}}
    assert sources.parse_margin(TPEX_OLD_MARGIN)["6488"] == {"margin": 1010.0, "short": 102.0}


def test_holiday_is_nodata():
    with pytest.raises(sources.NoData):
        sources.parse_valuation(TWSE_HOLIDAY)


def test_revenue_html():
    r = sources.parse_revenue(REVENUE_HTML)
    assert list(r) == ["1101"]
    assert r["1101"]["revenue"] == 12345678 and r["1101"]["yoy"] == 23.45 and r["1101"]["cum_yoy"] == 9.74


def test_statements_html_multiple_tables():
    inc = sources.parse_statement(INCOME_HTML, sources.INCOME_MAP)
    assert inc["2330"]["revenue"] == 839253664 and inc["2330"]["gross"] == 489253664 and inc["2330"]["eps"] == 13.95
    assert inc["2330"]["net_parent"] == 359000000
    assert inc["2881"]["revenue"] == 120000 and inc["2881"]["net_income"] == 40000  # financial-industry table
    bal = sources.parse_statement(BALANCE_HTML, sources.BALANCE_MAP)
    assert bal["2330"]["equity_parent"] == 3.95e9 and bal["2330"]["capital"] == 259320000


def test_publication_deadlines():
    assert sources.revenue_available(2026, 12) == dt.date(2027, 1, 10)
    assert sources.statement_available(2026, 4) == dt.date(2027, 3, 31)
    assert sources.statement_available(2026, 2) == dt.date(2026, 8, 14)


# ---------------------------------------------------------------- store / panels / expressions / backtest
@pytest.fixture
def R(tmp_path, monkeypatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    monkeypatch.setenv("FT_FAKE_DATA", "1")
    import ft_common.market as market
    import ft_common.twmarket as twmarket
    importlib.reload(market)
    importlib.reload(twmarket)
    from ft_common.research import backtest, collect, expr, fields, store
    store = importlib.reload(store)
    fields = importlib.reload(fields)
    expr = importlib.reload(expr)
    collect = importlib.reload(collect)
    backtest = importlib.reload(backtest)
    return store, fields, expr, collect, backtest


def _prices(store, codes_growth: dict, start="2024-01-01", n=300):
    days = pd.bdate_range(start, periods=n)
    rows = []
    for code, g in codes_growth.items():
        c = 100.0
        for d in days:
            o = c
            c = c * (1 + g)
            rows.append((code, d.date().isoformat(), o, max(o, c), min(o, c), c, c, 2_000_000))
    store.upsert("prices", rows)
    store.bump_version()
    return days


def test_point_in_time_revenue(R):
    store, F, expr, collect, bt = R
    days = _prices(store, {"9001": 0.001})
    store.upsert("revenue", [("9001", "2024-01", "2024-02-10", 100.0, None, 30.0, None, None),
                             ("9001", "2024-02", "2024-03-10", 120.0, 20.0, 50.0, None, None)])
    store.bump_version()
    p = expr.evaluate("營收年增率", days).daily(days)["9001"]
    assert pd.isna(p[pd.Timestamp("2024-02-09")])
    assert p[pd.Timestamp("2024-02-12")] == 30.0 and p[pd.Timestamp("2024-03-11")] == 50.0
    up = expr.evaluate("rise(月營收, 1)", days)
    assert up.freq == "M"  # period-level until mixed with daily data
    assert bool(up.daily(days)["9001"][pd.Timestamp("2024-03-11")])


def test_expression_whitelist(R):
    _, F, expr, *_ = R
    for bad in ["__import__('os')", "收盤價.__class__", "open('x')", "[1,2]", "lambda: 1", "不存在 > 1", "'a' > 1"]:
        with pytest.raises(expr.ExprError):
            expr.parse(bad)
    assert expr.names("收盤價 > ma(收盤價, 20) & 投信買賣超 > 0") == ["收盤價", "投信買賣超"]
    assert expr.names("close > 均線(close, 5)") == ["收盤價"]


def test_ttm_and_single_quarter_from_ytd(R):
    store, F, *_ = R
    rows = []
    for y in (2023, 2024):
        ytd_eps = 0
        for q in (1, 2, 3, 4):
            ytd_eps += q  # single-quarter EPS = q
            rows.append(("9001", f"{y}Q{q}", sources.statement_available(y, q).isoformat(), 100.0 * q, 40.0 * q, 20.0 * q, 18.0 * q,
                         15.0 * q, 15.0 * q, float(ytd_eps)))
    store.upsert("income_ytd", rows)
    store.bump_version()
    fin = F._fin_all()
    eps = fin["eps"]["9001"].dropna()
    assert list(eps.values[:4]) == [1, 2, 3, 4]
    assert fin["eps_ttm"]["9001"].dropna().iloc[0] == 10
    assert fin["gross_margin"]["9001"].dropna().iloc[0] == pytest.approx(40)


def test_backtest_matches_buy_and_hold_without_costs(R):
    store, F, expr, collect, bt = R
    days = _prices(store, {"9001": 0.004, "9002": 0.0, "9003": -0.002})
    spec = {"conditions": ["收盤價 > 0"], "rank": "收盤價", "top_n": 1, "rebalance": "M", "start": "2024-01-01",
            "fee_discount": 0, "slippage": 0, "max_weight": 1, "universe": {"min_value": 0, "market": "all"}, "benchmark": "9002"}
    r = bt.run(spec)
    # first rebalance = last trading day of Jan 2024 → buy 9001 next open, hold to the end
    first = [d for d in days if d.month == 1 and d.year == 2024][-1]
    entry = days[days.get_loc(first) + 1]
    o_entry = 100 * 1.004 ** days.get_loc(entry)  # open = previous close
    expect = 100 * 1.004 ** (len(days)) / o_entry
    assert r["curve"][-1]["v"] == pytest.approx(expect, rel=1e-4)
    assert r["metrics"]["trades"] == 0 and r["holdings"][0]["code"] == "9001"
    assert r["picks"]["rows"][0]["code"] == "9001"
    with_cost = bt.run({**spec, "fee_discount": 1, "slippage": 0.001})
    assert with_cost["curve"][-1]["v"] == pytest.approx(expect / (1 + 0.001425 + 0.001), rel=1e-4)


def test_stop_loss_exits(R):
    store, F, expr, collect, bt = R
    _prices(store, {"9001": -0.01})
    r = bt.run({"conditions": ["收盤價 > 0"], "rank": "收盤價", "top_n": 1, "rebalance": "Q", "start": "2024-01-01", "stop_loss": 0.05,
                "fee_discount": 0, "slippage": 0, "max_weight": 1, "universe": {"min_value": 0}})
    assert any(t["reason"] == "停損" and t["ret"] <= -5 for t in r["trades"])


def test_spec_validation(R):
    *_, bt = R
    with pytest.raises(bt.SpecError):
        bt.normalize({"conditions": ["收盤價 >"]})
    with pytest.raises(bt.SpecError):
        bt.normalize({"conditions": [], "rank": ""})
    s = bt.normalize({"conditions": ["收盤價 > 10"], "stop_loss": 10})
    assert s["stop_loss"] == 0.1 and s["top_n"] == 10


def test_presets_parse(R):
    *_, bt = R
    from ft_common.research import presets
    for p in presets.PRESETS:
        bt.normalize(p)


def test_collector_plan_and_parsers_offline(R, monkeypatch):
    store, F, expr, collect, bt = R
    monkeypatch.setattr(collect, "BACKFILL", True)
    tasks = collect.plan(20)
    kinds = {t[0] for t in tasks}
    assert "daily" in kinds or "revenue" in kinds
    # a holiday is never retried; an error is retried later
    store.log_fetch("valuation", "上市", "2020-01-01", "nodata")
    assert not collect._due(store.fetch_state("valuation")[("上市", "2020-01-01")], False)
    store.log_fetch("valuation", "上市", "2020-01-02", "error", 0, "boom")
    st = store.fetch_state("valuation")[("上市", "2020-01-02")]
    assert not collect._due(st, False)  # just failed → wait
    assert collect._due(("error", 1, "2000-01-01T00:00:00"), False)


def test_ai_extract_and_validate(R, monkeypatch):
    *_, bt = R
    from ft_common.research import ai
    ai = importlib.reload(ai)
    replies = iter(['<think>x</think>{"name":"x","conditions":["亂寫 > 1"]}',
                    json.dumps({"name": "營收", "conditions": ["營收年增率 > 20"], "rank": "營收年增率", "rebalance": "revenue",
                                "explanation": "說明"}, ensure_ascii=False)])
    seen = []
    monkeypatch.setattr(ai.ollama, "chat", lambda llm, msgs, **k: seen.append(msgs) or next(replies))
    r = ai.build("營收成長", {})
    assert r["spec"]["conditions"] == ["營收年增率 > 20"] and r["explanation"] == "說明"
    assert "這個 JSON 有錯" in seen[-1][-1]["content"]
    assert "營收年增率" in ai.system_prompt() and "new_high" in ai.system_prompt()


def test_revenue_openapi_fallback_and_block_page(monkeypatch):
    rows = [{"出表日期": "1151011", "資料年月": "11509", "公司代號": "2330", "公司名稱": "台積電", "產業別": "半導體業",
             "營業收入-當月營收": "330,000,000", "營業收入-上月營收": "300,000,000", "營業收入-去年當月營收": "250,000,000",
             "營業收入-上月比較增減(%)": "10.0", "營業收入-去年同月增減(%)": "32.0", "累計營業收入-當月累計營收": "2,700,000,000",
             "累計營業收入-去年累計營收": "2,000,000,000", "累計營業收入-前期比較增減(%)": "35.0", "備註": "-"}]
    period, data = sources.parse_revenue_openapi(rows)
    assert period == "2026-09" and data["2330"]["revenue"] == 330000000 and data["2330"]["yoy"] == 32.0
    assert data["2330"]["cum_yoy"] == 35.0

    def fake_fetch(url, **k):
        if "openapi" in url:
            return json.dumps(rows).encode()
        return "<html><body>請稍候，安全檢查中</body></html>".encode()
    monkeypatch.setattr(sources, "fetch", fake_fetch)
    assert sources.get_revenue("上市", 2026, 9)["2330"]["revenue"] == 330000000  # latest month via OpenAPI
    with pytest.raises(sources.SourceError):  # past month, MOPS shows no table → error (retried), not "no data"
        sources.get_revenue("上市", 2025, 3)


def test_periodic_nodata_is_retried(R):
    store, F, expr, collect, bt = R
    old = (dt.datetime.now() - dt.timedelta(days=2)).isoformat(timespec="seconds")
    assert collect._due(("nodata", 1, old), False, periodic=True)
    assert not collect._due(("nodata", 1, old), False)  # a daily holiday is never retried
    assert not collect._due(("nodata", 20, old), False, periodic=True)


def test_period_before_deadline_is_refreshed_and_not_an_error(R, monkeypatch):
    store, F, expr, collect, bt = R
    old = (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec="seconds")
    assert collect._due(("ok", 1, old), True, True, early=True)       # partial (only early filers) → refresh
    assert not collect._due(("ok", 1, old), True, True, early=False)  # past the deadline → final
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: "<html><body>系統維護中</body></html>".encode())
    y = dt.date.today().year + 1  # a quarter whose deadline is in the future
    with pytest.raises(sources.NoData, match="尚未到申報期限"):
        sources.get_statement("balance", "上市", y, 3)
    with pytest.raises(sources.SourceError) as e:  # long past → still a real error
        sources.get_statement("balance", "上市", 2020, 3)
    assert not isinstance(e.value, sources.NoData)


def test_no_data_message_explains_progress(R):
    store, F, expr, collect, bt = R
    _prices(store, {"9001": 0.001})
    store.log_fetch("revenue", "上市", "2026-08", "error", 0, "SourceError: 頁面沒有營收表格")
    with pytest.raises(bt.SpecError) as e:
        bt.run({"conditions": ["營收年增率 > 20"], "universe": {"min_value": 0}})
    assert "失敗 1 批" in str(e.value) and "頁面沒有營收表格" in str(e.value)
