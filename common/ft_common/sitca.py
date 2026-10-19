"""投信投顧公會 (SITCA) ETF zone + 基金投資明細 parsers.

Ported from TWSEMCPServer (github.com/twjackysu/TWSEMCPServer, commit a78e985, MIT License,
Copyright (c) 2024-2025 TWStockMCPServer Contributors): utils/sitca.py, utils/mops.py
(_TableParser) and tools/etf/holdings.py — adapted to return data instead of text.

ETF zone result pages are read directly:
    <page>2.aspx?txtYM=YYYYMM&txtcomid=&txtfund=&txtemcode=<code>,&txtkeyword=&txtR1=1&txtR2=1
Holdings pages (IN2629 monthly top-10, IN2630 quarterly >1%) are ASP.NET postback forms:
GET → period postback → query by fund class, each POST carrying the previous VIEWSTATE.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Callable

from .twhttp import fetch

SITCA_ETF_BASE = "https://www.sitca.org.tw/ROC/SITCA_ETF"
SITCA_INDUSTRY_BASE = "https://www.sitca.org.tw/ROC/Industry"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TTL = 6 * 3600
GRID_ID = "ctl00_ContentPlaceHolder1_GridView1"
HOLDINGS_MONTHLY_PAGE = "IN2629.aspx"
HOLDINGS_QUARTERLY_PAGE = "IN2630.aspx"
STATEMENT_PAGE, STATEMENT_RESULT = "etf_statement.aspx", "etf_statement2.aspx"
INFO_PAGE, INFO_RESULT = "etf_info.aspx", "etf_info2.aspx"

_YM_SELECT_RE = re.compile(r'name="ctl00\$ContentPlaceHolder1\$ddlYM"[^>]*>(.*?)</select>', re.S)
_HOLDINGS_YM_RE = re.compile(r'name="ctl00\$ContentPlaceHolder1\$ddlQ_YM"[^>]*>(.*?)</select>', re.S)
_FIELD_PREFIX = "ctl00$ContentPlaceHolder1$"
_HIDDEN_RE = re.compile(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"')
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<td([^>]*)>(.*?)</t[dh]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_DISCLAIMER_RE = re.compile(r"\s+[（(].*$", re.S)
_SHARE_CLASS_RE = re.compile(r"-(?:新台幣|新臺幣|台幣|美元|人民幣|日圓|日幣|澳幣)(?:[（(][^)）]*[)）])?$")

# Fetcher signature: (url, params=None, data=None, method="GET") -> bytes. Swappable in tests.
Fetcher = Callable[..., bytes]


def _default_fetch(url, params=None, data=None, method="GET"):
    return fetch(url, params=params, data=data, method=method, headers=HEADERS, timeout=120, ttl=TTL, disk=True)


_fetch: Fetcher = _default_fetch


def set_fetcher(f: Fetcher | None):
    global _fetch
    _fetch = f or _default_fetch


def _get(url, params=None):
    return _fetch(url, params=params).decode("utf-8", errors="replace")


def _post(url, form):
    return _fetch(url, data=form, method="POST").decode("utf-8", errors="replace")


# ---------------------------------------------------------------- HTML table parser
class _TableParser(HTMLParser):
    def __init__(self, table_id):
        super().__init__(convert_charrefs=True)
        self.table_id, self.depth = table_id, 0
        self.rows, self._row, self._cell, self._done = [], None, None, False

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table":
            if self.depth:
                self.depth += 1
            elif self.table_id is None or dict(attrs).get("id") == self.table_id:
                self.depth, self.rows = 1, []
            return
        if tag == "br" and self._cell is not None:
            self._cell.append(" ")
        if self.depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if not self.depth or self._done:
            return
        if tag == "table":
            self.depth -= 1
            if not self.depth:
                if self._row:
                    self.rows.append(self._row)
                self._done = True
        elif self.depth != 1:
            return
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self.depth and self._cell is not None:
            self._cell.append(data)


def parse_html_table(html: str, table_id: str) -> list[list[str]]:
    p = _TableParser(table_id)
    p.feed(html)
    return p.rows


# ---------------------------------------------------------------- ETF zone
def available_months(page: str) -> list[str]:
    html = _get(f"{SITCA_ETF_BASE}/{page}")
    sel = _YM_SELECT_RE.search(html)
    months = re.findall(r'<option[^>]*value="(\d{6})"', sel.group(1)) if sel else []
    if not months:
        raise ValueError(f"SITCA {page} 找不到資料月份選單，頁面可能改版")
    return months


def query_rows(result_page: str, ym: str, code: str = "", fund_id: str = "") -> list[list[str]]:
    params = {"txtYM": ym, "txtcomid": "", "txtfund": f"{fund_id}," if fund_id else "",
              "txtemcode": f"{code}," if code else "", "txtkeyword": "", "txtR1": "1", "txtR2": "1"}
    table = parse_html_table(_get(f"{SITCA_ETF_BASE}/{result_page}", params), GRID_ID)
    if not table or not table[0] or table[0][0] != "序號":
        return []
    return [table[0]] + [r for r in table[1:] if r and r[0].isdigit()]


def to_number(text: str):
    text = (text or "").replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def etf_info_all() -> list[dict]:
    """Every SITCA-member ETF: 經理公司, 證券代號, 證券簡稱, 基金名稱, 基金成立日期, 分類, 追蹤指數, 配息…"""
    ym = available_months(INFO_PAGE)[0]
    rows = query_rows(INFO_RESULT, ym)
    if not rows:
        return []
    header = rows[0]
    out = [dict(zip(header, r)) for r in rows[1:]]
    return [r for r in out if r.get("證券代號", "-") not in ("", "-")]


def etf_statement(code: str) -> dict | None:
    """Latest monthly statement row for one ETF (規模, 受益人數, 申購/買回, 類型代號…)."""
    months = available_months(STATEMENT_PAGE)
    rows = query_rows(STATEMENT_RESULT, months[0], code=code)
    if len(rows) < 2:
        return None
    d = dict(zip(rows[0], rows[1]))
    d["_ym"] = months[0]
    return d


# ---------------------------------------------------------------- holdings
def fund_name_key(name: str) -> str:
    name = _DISCLAIMER_RE.sub("", name.strip())
    return re.sub(r"\s", "", _SHARE_CLASS_RE.sub("", name))


def parse_holdings(html: str):
    start = html.find("DTHeader")
    if start < 0:
        return [], {}
    table = html[html.rfind("<table", 0, start):]
    table = table[: table.find("</table>")]

    def clean(c):
        return " ".join(_TAG_RE.sub(" ", c.replace("<br>", " ")).replace("&nbsp;", " ").split())

    header, funds, current = [], {}, None
    for row in _ROW_RE.findall(table):
        cells = [clean(c) for _a, c in _CELL_RE.findall(row)]
        if not cells:
            continue
        if not header:
            header = cells
        elif len(cells) == len(header):
            current = cells[0]
            funds.setdefault(current, []).append(cells[1:])
        elif len(cells) == len(header) - 1 and current is not None:
            funds[current].append(cells)
    return header, funds


def holdings_months(page: str) -> list[str]:
    sel = _HOLDINGS_YM_RE.search(_get(f"{SITCA_INDUSTRY_BASE}/{page}"))
    months = re.findall(r'<option[^>]*value="(\d{6})"', sel.group(1)) if sel else []
    if not months:
        raise ValueError(f"SITCA {page} 找不到資料月份選單，頁面可能改版")
    return sorted(set(months), reverse=True)


def holdings_by_class(page: str, ym: str, fund_class: str):
    by_class = {_FIELD_PREFIX + "ddlQ_YM": ym, _FIELD_PREFIX + "rdo1": "rbClass"}
    first = _get(f"{SITCA_INDUSTRY_BASE}/{page}")
    period_page = _post(f"{SITCA_INDUSTRY_BASE}/{page}", {
        **dict(_HIDDEN_RE.findall(first)), "__EVENTTARGET": _FIELD_PREFIX + "ddlQ_YM", "__EVENTARGUMENT": "", **by_class})
    html = _post(f"{SITCA_INDUSTRY_BASE}/{page}", {
        **dict(_HIDDEN_RE.findall(period_page)), **by_class,
        _FIELD_PREFIX + "ddlQ_Class": fund_class, _FIELD_PREFIX + "BtnQuery": "查詢"})
    return parse_holdings(html)


COL_RANK, COL_KIND, COL_CODE, COL_NAME, COL_AMOUNT, COL_PCT = (
    "名次", "標的種類", "標的代號 或 Bloomberg Isin Code", "標的名稱", "金額", "占基金淨資產價值 之比例(%)")


def etf_holdings(code: str, quarterly: bool = False) -> dict:
    """{'period','fund_name','scope','items':[{rank,code,name,kind,pct,amount}], 'total_pct'} or {'error'}."""
    page = HOLDINGS_QUARTERLY_PAGE if quarterly else HOLDINGS_MONTHLY_PAGE
    ym = holdings_months(page)[0]
    st = etf_statement(code)
    if not st:
        return {"error": f"投信投顧公會沒有 {code} 的資料（期貨 ETF、外幣級別或新上市 ETF 不在其中）"}
    fund_name, fund_class = st.get("基金名稱", ""), st.get("類型代號", "")
    if ym != st["_ym"]:
        past = query_rows(STATEMENT_RESULT, ym, fund_id=st.get("基金統編", ""))
        if len(past) >= 2:
            d = dict(zip(past[0], past[1]))
            fund_name, fund_class = d.get("基金名稱", fund_name), d.get("類型代號", fund_class)
    header, funds = holdings_by_class(page, ym, fund_class)
    if not header:
        return {"error": f"查無 {ym} 類別 {fund_class} 的基金投資明細"}
    target = fund_name_key(fund_name)
    rows = next((r for n, r in funds.items() if fund_name_key(n) == target), None)
    if rows is None:
        return {"error": f"{ym[:4]}-{ym[4:]} 基金投資明細沒有 {code}（槓桿/反向 ETF 多半只持有期貨與現金）",
                "period": ym, "fund_name": fund_name}
    cols = header[1:]
    items = []
    for i, r in enumerate(rows, 1):
        d = dict(zip(cols, r))
        items.append({"rank": int(to_number(d.get(COL_RANK, "")) or i), "code": d.get(COL_CODE, ""),
                      "name": d.get(COL_NAME, ""), "kind": d.get(COL_KIND, ""),
                      "pct": to_number(d.get(COL_PCT, "")), "amount": to_number(d.get(COL_AMOUNT, ""))})
    if COL_RANK not in cols:
        items.sort(key=lambda x: x["pct"] or 0, reverse=True)
        for i, it in enumerate(items, 1):
            it["rank"] = i
    return {"period": ym, "fund_name": fund_name, "scope": "每季占淨值 1% 以上" if quarterly else "每月前十大",
            "items": items, "total_pct": round(sum(x["pct"] or 0 for x in items), 2),
            "statement": {k: v for k, v in st.items() if not k.startswith("_")}}
