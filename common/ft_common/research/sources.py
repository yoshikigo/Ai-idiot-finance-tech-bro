"""Official Taiwanese data sources for the research database (FinLab-style point-in-time panels).

Every source has a pure `parse_*` function (tested offline with fixtures) and a `get_*` function that
does the HTTP call. Parsers map columns **by header name** wherever the source gives headers, and only
fall back to positions for the old header-less TPEx `aaData` feeds.

Units we store:  shares-based flows → 張 (1,000 shares);  revenue / financials → 千元 (as published);
valuation ratios as published.
"""
from __future__ import annotations

import datetime as dt
import html as htmlmod
import re
from typing import Iterable

from ..twhttp import decode_tw, fetch

TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX_NEW = "https://www.tpex.org.tw/www/zh-tw"
TPEX_OLD = "https://www.tpex.org.tw/web/stock"
MOPS_HOSTS = ("https://mopsov.twse.com.tw", "https://mops.twse.com.tw")
CODE_RE = re.compile(r"^[0-9]{4}[0-9A-Z]{0,2}$")


class SourceError(RuntimeError):
    pass


class NoData(SourceError):
    """The source answered but has nothing for that date (holiday, not yet published)."""


# ---------------------------------------------------------------- helpers
def num(x) -> float | None:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = htmlmod.unescape(str(x)).strip().replace(",", "").replace("＋", "+").replace("－", "-")
    s = re.sub(r"<[^>]+>", "", s).strip()
    if s in ("", "-", "--", "---", "N/A", "NA", "X", "x", "不適用", "無"):
        return None
    m = re.match(r"^[+-]?\d+(\.\d+)?", s)
    return float(m.group(0)) if m else None


def norm_header(h: str) -> str:
    h = htmlmod.unescape(re.sub(r"<[^>]+>", "", str(h)))
    return re.sub(r"\s+", "", h).replace("（", "(").replace("）", ")")


def roc(d: dt.date, sep="/") -> str:
    return f"{d.year - 1911}{sep}{d.month:02d}{sep}{d.day:02d}"


def _tables(payload: dict) -> list[tuple[list[str], list[list]]]:
    """Every (fields, rows) pair in a TWSE/TPEx JSON payload, whatever the vintage."""
    out = []
    if isinstance(payload.get("tables"), list):
        for t in payload["tables"]:
            if isinstance(t, dict) and t.get("data"):
                out.append(([norm_header(f) for f in t.get("fields") or []], t["data"]))
    if payload.get("data") and isinstance(payload["data"], list):
        out.append(([norm_header(f) for f in payload.get("fields") or []], payload["data"]))
    for k in ("creditList", "aaData"):
        if payload.get(k):
            fk = "creditFields" if k == "creditList" else None
            out.append(([norm_header(f) for f in payload.get(fk) or []] if fk else [], payload[k]))
    return out


def _code_table(payload: dict, must: Iterable[str] = ()) -> tuple[list[str], list[list]]:
    """The table whose rows are securities (first cell looks like a stock code)."""
    best = None
    for fields, rows in _tables(payload):
        n = sum(1 for r in rows[:50] if r and CODE_RE.match(str(r[0]).strip()))
        if n == 0:
            continue
        if must and fields and not all(any(m in f for f in fields) for m in must):
            continue
        if best is None or len(rows) > len(best[1]):
            best = (fields, rows)
    if best is None:
        # an answer with tables but no stock rows is a format change or a block page, not a holiday → retry & report
        if _tables(payload):
            raise SourceError("回應裡找不到個股表格（格式可能改了）")
        raise NoData("沒有資料")
    return best


def _idx(fields: list[str], *pats: str, exclude: tuple[str, ...] = (), nth: int = 0) -> int | None:
    hits = [i for i, f in enumerate(fields) if any(re.search(p, f) for p in pats) and not any(e in f for e in exclude)]
    return hits[nth] if len(hits) > nth else None


def _stat_ok(payload: dict):
    st = str(payload.get("stat") or payload.get("status") or "OK")
    if any(w in st for w in ("很抱歉", "沒有符合", "查無", "無資料")):
        raise NoData(st)


def _json(url: str, params: dict | None = None, data: dict | None = None, method="GET") -> dict:
    import json
    body = fetch(url, params=params, data=data, method=method, timeout=60)
    txt = decode_tw(body).strip()
    if not txt.startswith(("{", "[")):
        raise SourceError(f"非 JSON 回應（可能被限流）：{txt[:80]}")
    return json.loads(txt)


# ---------------------------------------------------------------- valuation (本益比 / 殖利率 / 股價淨值比)
def parse_valuation(payload: dict) -> dict[str, dict]:
    _stat_ok(payload)
    fields, rows = _code_table(payload)
    if fields:
        i_pe = _idx(fields, r"本益比")
        i_dy = _idx(fields, r"殖利率")
        i_pb = _idx(fields, r"淨值比")
    else:  # old TPEx pera_result: 代號,名稱,本益比,每股股利,股利年度,殖利率(%),股價淨值比
        i_pe, i_dy, i_pb = 2, 5, 6
    out = {}
    for r in rows:
        code = str(r[0]).strip()
        if not CODE_RE.match(code):
            continue
        g = lambda i: num(r[i]) if i is not None and i < len(r) else None  # noqa: E731
        out[code] = {"pe": g(i_pe), "dy": g(i_dy), "pb": g(i_pb)}
    return out


def get_valuation(market: str, d: dt.date) -> dict[str, dict]:
    if market == "上市":
        return parse_valuation(_json(f"{TWSE}/afterTrading/BWIBBU_d",
                                     {"date": d.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"}))
    return _tpex("valuation", d)


# ---------------------------------------------------------------- 三大法人
def parse_insti(payload: dict) -> dict[str, dict]:
    _stat_ok(payload)
    fields, rows = _code_table(payload)
    if fields and _idx(fields, r"投信.*買賣超") is not None:
        i_f = _idx(fields, r"外.*資.*買賣超", exclude=("自營商", "合計"))
        if i_f is None:
            i_f = _idx(fields, r"外.*資.*買賣超")
        i_t = _idx(fields, r"投信.*買賣超")
        i_d = _idx(fields, r"^自營商買賣超", r"^自營商\(?合計\)?.*買賣超", r"自營商合計.*買賣超")
        i_all = _idx(fields, r"三大法人.*買賣超")
        shares = any("股數" in f for f in fields)
    else:  # old TPEx 3itrade_hedge_result (24 columns): see module doc in collect.py
        i_f, i_t, i_d, i_all = 4, 13, 22, 23
        shares = True
    k = 1000.0 if shares else 1.0
    out = {}
    for r in rows:
        code = str(r[0]).strip()
        if not CODE_RE.match(code):
            continue
        g = lambda i: (num(r[i]) / k if num(r[i]) is not None else None) if i is not None and i < len(r) else None  # noqa: E731
        f, t, dd = g(i_f), g(i_t), g(i_d)
        tot = g(i_all)
        if tot is None and None not in (f, t, dd):
            tot = f + t + dd
        out[code] = {"foreign": f, "trust": t, "dealer": dd, "total": tot}
    return out


def get_insti(market: str, d: dt.date) -> dict[str, dict]:
    if market == "上市":
        return parse_insti(_json(f"{TWSE}/fund/T86", {"date": d.strftime("%Y%m%d"), "selectType": "ALLBUT0999",
                                                      "response": "json"}))
    return _tpex("insti", d)


# ---------------------------------------------------------------- 融資融券
def parse_margin(payload: dict) -> dict[str, dict]:
    _stat_ok(payload)
    fields, rows = _code_table(payload)
    if fields and (_idx(fields, r"今日餘額") is not None or _idx(fields, r"資餘額") is not None):
        i_m = _idx(fields, r"今日餘額") if _idx(fields, r"今日餘額") is not None else _idx(fields, r"資餘額")
        i_s = _idx(fields, r"今日餘額", nth=1) if _idx(fields, r"今日餘額") is not None else _idx(fields, r"券餘額")
    else:  # old TPEx margin_bal_result: 代號,名稱,前資餘額,資買,資賣,現償,資餘額,…,前券餘額,券賣,券買,券償,券餘額
        i_m, i_s = 6, 14
    out = {}
    for r in rows:
        code = str(r[0]).strip()
        if not CODE_RE.match(code):
            continue
        m = num(r[i_m]) if i_m is not None and i_m < len(r) else None
        s = num(r[i_s]) if i_s is not None and i_s < len(r) else None
        out[code] = {"margin": m, "short": s}
    return out


def get_margin(market: str, d: dt.date) -> dict[str, dict]:
    if market == "上市":
        return parse_margin(_json(f"{TWSE}/marginTrading/MI_MARGN", {"date": d.strftime("%Y%m%d"), "selectType": "ALL",
                                                                     "response": "json"}))
    return _tpex("margin", d)


# ---------------------------------------------------------------- TPEx (new site first, old php feeds as fallback)
_TPEX = {
    "valuation": (("afterTrading/peQryDate", {}), ("aftertrading/peratio_analysis/pera_result.php", {}), parse_valuation),
    "insti": (("insti/dailyTrade", {"type": "Daily", "sect": "EW"}), ("3insti/daily_trade/3itrade_hedge_result.php", {"se": "EW", "t": "D"}), parse_insti),
    "margin": (("margin/balance", {}), ("margin_trading/margin_balance/margin_bal_result.php", {}), parse_margin),
}


def _tpex(kind: str, d: dt.date) -> dict[str, dict]:
    (new_path, new_p), (old_path, old_p), parser = _TPEX[kind]
    errs = []
    try:
        res = parser(_json(f"{TPEX_NEW}/{new_path}", {**new_p, "date": d.strftime("%Y/%m/%d"), "response": "json"}))
        if res:
            return res
    except (SourceError, ValueError, KeyError) as e:
        errs.append(f"new: {e}")
    except Exception as e:  # noqa: BLE001  HTTP errors
        errs.append(f"new: {type(e).__name__} {str(e)[:80]}")
    try:
        res = parser(_json(f"{TPEX_OLD}/{old_path}", {**old_p, "l": "zh-tw", "o": "json", "d": roc(d)}))
        if res:
            return res
        raise NoData("空資料")
    except NoData:
        raise
    except Exception as e:  # noqa: BLE001
        errs.append(f"old: {type(e).__name__} {str(e)[:80]}")
    raise SourceError("櫃買 " + "；".join(errs))


# ---------------------------------------------------------------- HTML tables (MOPS)
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t([hd])[^>]*>(.*?)</t[hd]>", re.S | re.I)
_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)


def html_rows(fragment: str) -> list[tuple[list[str], bool]]:
    """[(cells, is_header_row)] for every <tr>; nested tables are flattened by the regexes."""
    out = []
    for tr in _TR.findall(fragment):
        cells = _CELL.findall(tr)
        if not cells:
            continue
        txt = [norm_header(c[1]) if c[0].lower() == "h" else htmlmod.unescape(re.sub(r"<[^>]+>", "", c[1])).strip()
               for c in cells]
        out.append((txt, all(c[0].lower() == "h" for c in cells)))
    return out


# ---------------------------------------------------------------- 月營收 (MOPS t21sc03)
def parse_revenue(html: str) -> dict[str, dict]:
    """公司代號,公司名稱,當月營收,上月營收,去年當月營收,上月比較增減(%),去年同月增減(%),當月累計營收,去年累計營收,前期比較增減(%),備註 (千元)."""
    out = {}
    for cells, is_head in html_rows(html):
        if is_head or len(cells) < 10:
            continue
        code = cells[0].strip()
        if not CODE_RE.match(code):
            continue
        v = [num(c) for c in cells[2:10]]
        if v[0] is None:
            continue
        out[code] = {"revenue": v[0], "rev_prev": v[1], "rev_ly": v[2], "mom": v[3], "yoy": v[4],
                     "cum": v[5], "cum_ly": v[6], "cum_yoy": v[7]}
    return out


OPENAPI_REVENUE = {"上市": ("https://openapi.twse.com.tw/v1/opendata/t187ap05_L",),
                   "上櫃": ("https://www.tpex.org.tw/openapi/v1/mopsfe_t187ap05_O",)}


def parse_revenue_openapi(rows: list[dict]) -> tuple[str | None, dict[str, dict]]:
    """TWSE/TPEx OpenAPI 每月營業收入彙總表 (latest month only) → (YYYY-MM, data)."""
    out, period = {}, None

    def g(r, *keys):
        for k in r:
            if all(x in k for x in keys):
                return num(r[k])
        return None
    for r in rows or []:
        code = str(r.get("公司代號") or "").strip()
        if not CODE_RE.match(code):
            continue
        ym = re.sub(r"\D", "", str(r.get("資料年月") or ""))
        if len(ym) >= 4 and period is None:
            period = f"{int(ym[:-2]) + 1911}-{int(ym[-2:]):02d}"
        out[code] = {"revenue": g(r, "當月營收"), "rev_prev": g(r, "上月營收"), "rev_ly": g(r, "去年當月營收"),
                     "mom": g(r, "上月比較"), "yoy": g(r, "去年同月"), "cum": g(r, "當月累計"), "cum_ly": g(r, "去年累計"),
                     "cum_yoy": g(r, "前期比較")}
    return period, {k: v for k, v in out.items() if v["revenue"] is not None}


def get_revenue(market: str, year: int, month: int) -> dict[str, dict]:
    typ = "sii" if market == "上市" else "otc"
    out, errs = {}, []
    for host in MOPS_HOSTS:
        ok = False
        for suffix in ("_0", "_1", ""):  # _0 = 國內公司, _1 = 外國企業（KY）, no suffix = older single page
            if suffix == "" and ok:
                break
            url = f"{host}/nas/t21/{typ}/t21sc03_{year - 1911}_{month}{suffix}.html"
            try:
                html = decode_tw(fetch(url, timeout=60))
            except Exception as e:  # noqa: BLE001
                errs.append(f"{url.split('/nas')[0]} {suffix}: {type(e).__name__}")
                continue
            got = parse_revenue(html)
            if got:
                out.update(got)
                ok = True
        if ok:
            return out
    # MOPS unreachable / changed: the OpenAPI has the latest month at least
    for url in OPENAPI_REVENUE[market]:
        try:
            import json
            period, data = parse_revenue_openapi(json.loads(decode_tw(fetch(url, timeout=60))))
            if period == f"{year}-{month:02d}" and data:
                return data
        except Exception as e:  # noqa: BLE001
            errs.append(f"OpenAPI: {type(e).__name__}")
    if errs and len(errs) >= 3 * len(MOPS_HOSTS):
        raise SourceError("月營收 " + "；".join(errs[:3]))
    if revenue_available(year, month) >= dt.date.today():
        raise NoData(f"{year}-{month:02d} 尚未公布")
    # past the legal deadline but no table: blocked, redirected or the page layout changed — retry, and show it
    raise SourceError(f"{year}-{month:02d} 頁面沒有營收表格（可能被公開資訊觀測站擋下或改版）" + (f"；{'；'.join(errs[:2])}" if errs else ""))


# ---------------------------------------------------------------- 財報 (MOPS t163sb04 綜合損益 / t163sb05 資產負債, 彙總表)
INCOME_MAP = {
    "revenue": ("營業收入", "營業收入合計", "收入合計", "收益合計", "淨收益", "營業收入淨額"),
    "gross": ("營業毛利(毛損)淨額", "營業毛利(毛損)"),
    "op_income": ("營業利益(損失)",),
    "pretax": ("稅前淨利(淨損)", "繼續營業單位稅前淨利(淨損)", "繼續營業單位稅前損益", "稅前損益"),
    "net_income": ("本期淨利(淨損)", "本期稅後淨利(淨損)", "本期綜合損益總額"),
    "net_parent": ("淨利(淨損)歸屬於母公司業主", "淨利(損)歸屬於母公司業主", "母公司業主(淨利/損)"),
    "eps": ("基本每股盈餘(元)", "基本每股盈餘"),
}
BALANCE_MAP = {
    "assets": ("資產總計", "資產總額", "資產合計"),
    "liabilities": ("負債總計", "負債總額", "負債合計"),
    "equity": ("權益總計", "權益總額", "權益合計"),
    "equity_parent": ("歸屬於母公司業主之權益合計", "歸屬於母公司業主之權益"),
    "capital": ("股本",),
}


def parse_statement(html: str, mapping: dict[str, tuple[str, ...]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    header: list[str] | None = None
    for table in _TABLE.findall(html) or [html]:
        header = None
        for cells, is_head in html_rows(table):
            if is_head or ("公司代號" in "".join(cells[:2]) and not CODE_RE.match(cells[0])):
                header = [norm_header(c) for c in cells]
                continue
            if not header or not cells or not CODE_RE.match(cells[0].strip()):
                continue
            rec = out.setdefault(cells[0].strip(), {})
            for key, names in mapping.items():
                if key in rec:
                    continue
                for nm in names:
                    if nm in header:
                        i = header.index(nm)
                        if i < len(cells):
                            v = num(cells[i])
                            if v is not None:
                                rec[key] = v
                                break
    return {k: v for k, v in out.items() if v}


def get_statement(kind: str, market: str, year: int, quarter: int) -> dict[str, dict]:
    page, mapping = ("ajax_t163sb04", INCOME_MAP) if kind == "income" else ("ajax_t163sb05", BALANCE_MAP)
    form = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "isQuery": "Y",
            "TYPEK": "sii" if market == "上市" else "otc", "year": str(year - 1911), "season": f"{quarter:02d}"}
    errs = []
    for host in MOPS_HOSTS:
        try:
            html = decode_tw(fetch(f"{host}/mops/web/{page}", data=form, method="POST", timeout=90,
                                   headers={"Referer": f"{host}/mops/web/t163sb04"}))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{host}: {type(e).__name__}")
            continue
        if "查詢過於頻繁" in html or "Overrun" in html:
            raise SourceError("公開資訊觀測站限流（查詢過於頻繁），稍後重試")
        got = parse_statement(html, mapping)
        if got:
            return got
        if "查無資料" in html or "查無所需資料" in html:
            raise NoData(f"{year}Q{quarter} 查無資料")
    if statement_available(year, quarter) >= dt.date.today() and len(errs) < len(MOPS_HOSTS):
        # before the legal deadline an empty / not-yet-built page is normal, not an error
        raise NoData(f"{year}Q{quarter} 尚未到申報期限（{statement_available(year, quarter):%m/%d}）")
    raise SourceError("財報 " + "；".join(errs) if errs else f"{year}Q{quarter} 無法解析")


# ---------------------------------------------------------------- availability (公布日對齊)
# ---------------------------------------------------------------- 處置股 (disposition: matching by 分盤 / 預收款券)
_DATE = re.compile(r"(\d{2,4})\s*[/.\-年]\s*(\d{1,2})\s*[/.\-月]\s*(\d{1,2})")


def _tw_date(y: str, m: str, d: str) -> dt.date | None:
    yy = int(y)
    yy = yy + 1911 if yy < 1000 else yy
    try:
        return dt.date(yy, int(m), int(d))
    except ValueError:
        return None


def parse_dates(text) -> list[dt.date]:
    """Every date in a cell: '115/07/22～115/08/04', '2026/07/22 ~ 2026/08/04', '1150722' …"""
    t = re.sub(r"<[^>]+>", " ", htmlmod.unescape(str(text or "")))
    out = [d for d in (_tw_date(*m) for m in _DATE.findall(t)) if d]
    if not out:
        for m in re.findall(r"(?<!\d)(\d{3})(\d{2})(\d{2})(?!\d)", t):
            d = _tw_date(*m)
            if d:
                out.append(d)
    return out


def parse_disposal(payload: dict) -> list[tuple[str, dt.date, dt.date, str]]:
    """→ [(code, start, end, reason)] from a TWSE「公布處置有價證券」/ TPEx「處置有價證券」JSON answer.
    The column layout differs by market and vintage, so columns are found by their header."""
    _stat_ok(payload)
    out = []
    for fields, rows in _tables(payload):
        if not fields:
            continue
        i_code = _idx(fields, r"代號")
        i_per = _idx(fields, r"處置起迄|處置期間|起迄|期間")
        i_from = _idx(fields, r"處置起|開始")
        i_to = _idx(fields, r"處置迄|結束|迄日")
        i_why = _idx(fields, r"處置條件|處置原因|原因|條件")
        if i_code is None or (i_per is None and i_from is None):
            continue
        for r in rows:
            if not isinstance(r, (list, tuple)) or i_code >= len(r):
                continue
            code = re.sub(r"<[^>]+>", "", str(r[i_code])).strip()
            if not CODE_RE.match(code):
                continue
            ds = parse_dates(r[i_per]) if i_per is not None and i_per < len(r) else []
            if len(ds) < 2 and i_from is not None and i_to is not None and max(i_from, i_to) < len(r):
                ds = parse_dates(r[i_from])[:1] + parse_dates(r[i_to])[:1]
            if len(ds) < 2:
                continue
            why = str(r[i_why]).strip()[:60] if i_why is not None and i_why < len(r) else ""
            out.append((code, min(ds[0], ds[1]), max(ds[0], ds[1]), why))
    if not out and any(rows for _, rows in _tables(payload)):
        raise SourceError("回應裡有表格但找不到處置股的代號／期間欄位（格式可能改了）")
    return out


def get_disposal(market: str, start: dt.date, end: dt.date) -> list[tuple[str, dt.date, dt.date, str]]:
    if market == "上市":
        return parse_disposal(_json(f"{TWSE}/announcement/punish",
                                    {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "response": "json"}))
    return parse_disposal(_json(f"{TPEX_NEW}/bulletin/disposal",
                                {"startDate": start.strftime("%Y/%m/%d"), "endDate": end.strftime("%Y/%m/%d"), "response": "json"}))


def revenue_available(year: int, month: int) -> dt.date:
    """月營收法定公布期限：次月 10 日。"""
    y, m = (year + 1, 1) if month == 12 else (year, month + 1)
    return dt.date(y, m, 10)


def statement_available(year: int, quarter: int) -> dt.date:
    """一般公司財報法定期限：Q1 5/15、Q2 8/14、Q3 11/14、年報 次年 3/31。"""
    return {1: dt.date(year, 5, 15), 2: dt.date(year, 8, 14), 3: dt.date(year, 11, 14), 4: dt.date(year + 1, 3, 31)}[quarter]
