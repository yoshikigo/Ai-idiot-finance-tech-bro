"""SEC EDGAR — official US filings (free, no key).

  ticker → CIK        https://www.sec.gov/files/company_tickers.json
  financial facts     https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json   (XBRL, every 10-K/10-Q/20-F)
  recent filings      https://data.sec.gov/submissions/CIK##########.json             (10-K, 10-Q, 8-K, Form 4, 13D/G…)

SEC's fair-access rules: a User-Agent that names you and gives a contact e-mail, ≤ 10 requests / second.
Set FT_SEC_USER_AGENT="FT-Terminal your@email" in fin-terminal.env; without it the EDGAR features say so and
everything falls back to Yahoo. Responses are cached on disk (facts 12 h, filings 1 h, ticker list 24 h).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from pathlib import Path

from . import market

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_lock = threading.Lock()
_last = [0.0]


class NotConfigured(RuntimeError):
    pass


def user_agent() -> str:
    ua = os.environ.get("FT_SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise NotConfigured("SEC EDGAR 需要聯絡資訊：在 ~/.config/fin-terminal/fin-terminal.env 設 "
                            "FT_SEC_USER_AGENT=\"FT-Terminal 你的email\" 後重啟 fin-terminal")
    return ua


def enabled() -> bool:
    try:
        user_agent()
        return not market.FAKE
    except NotConfigured:
        return False


def _cache_dir() -> Path:
    p = Path(os.environ.get("FT_DATA", "/data")) / "cache" / "edgar"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_json(url: str, ttl: float, name: str):
    f = _cache_dir() / name
    if f.exists() and time.time() - f.stat().st_mtime < ttl:
        try:
            return json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    import httpx
    with _lock:  # ≤ ~8 requests/second, well under SEC's 10
        wait = 0.125 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    r = httpx.get(url, headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"}, timeout=30,
                  follow_redirects=True)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(f)
    return data


def cik_of(ticker: str) -> int | None:
    t = ticker.upper().replace(".", "-")  # BRK.B → BRK-B as SEC lists it
    if t.startswith("^") or t.endswith(("=X", "=F", "-USD")) or "." in ticker and not ticker.upper().endswith((".A", ".B")):
        return None
    d = _get_json(TICKERS_URL, 24 * 3600, "company_tickers.json") or {}
    for row in d.values():
        if str(row.get("ticker", "")).upper() == t:
            return int(row["cik_str"])
    return None


def facts(ticker: str) -> dict | None:
    cik = cik_of(ticker)
    return _get_json(FACTS_URL.format(cik=cik), 12 * 3600, f"facts-{cik}.json") if cik else None


def submissions(ticker: str) -> dict | None:
    cik = cik_of(ticker)
    return _get_json(SUBS_URL.format(cik=cik), 3600, f"subs-{cik}.json") if cik else None


# ------------------------------------------------------------------ financial statements from XBRL facts
# label → candidate us-gaap / ifrs-full concepts (first one with data wins), unit, kind (flow | instant)
ITEMS = [
    ("營業收入", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenue"], "USD", "flow"),
    ("營業毛利", ["GrossProfit"], "USD", "flow"),
    ("營業利益", ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"], "USD", "flow"),
    ("稅後淨利", ["NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"], "USD", "flow"),
    ("每股盈餘（稀釋）", ["EarningsPerShareDiluted", "DilutedEarningsLossPerShare"], "USD/shares", "flow"),
    ("營業現金流", ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"], "USD", "flow"),
    ("資本支出", ["PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], "USD", "flow"),
    ("研發費用", ["ResearchAndDevelopmentExpense"], "USD", "flow"),
    ("現金及約當現金", ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"], "USD", "instant"),
    ("資產總額", ["Assets"], "USD", "instant"),
    ("負債總額", ["Liabilities"], "USD", "instant"),
    ("股東權益", ["StockholdersEquity", "Equity", "EquityAttributableToOwnersOfParent"], "USD", "instant"),
    ("長期負債", ["LongTermDebtNoncurrent", "LongTermDebt", "NoncurrentFinancialLiabilities"], "USD", "instant"),
]
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F")
QUARTER_FORMS = ("10-Q", "10-Q/A", "6-K")


def _days(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def _series(fx: dict, concepts: list[str], unit: str, kind: str, freq: str) -> list[dict]:
    pools = [fx.get("facts", {}).get(ns, {}) for ns in ("us-gaap", "ifrs-full")]
    for concept in concepts:
        node = next((p[concept] for p in pools if concept in p), None)
        if not node:
            continue
        units = node.get("units", {})
        vals = units.get(unit) or next((v for k, v in units.items() if k.startswith(unit.split("/")[0])), None)
        if not vals:
            continue
        rows = {}
        for v in vals:
            form = v.get("form", "")
            if freq == "annual" and form not in ANNUAL_FORMS:
                continue
            if freq == "quarterly" and form not in QUARTER_FORMS + ANNUAL_FORMS:
                continue
            if kind == "flow":
                if not v.get("start"):
                    continue
                d = _days(v["start"], v["end"])
                if freq == "annual" and not 330 <= d <= 380:
                    continue
                if freq == "quarterly" and not 80 <= d <= 100:
                    continue
            elif freq == "quarterly" and form in ANNUAL_FORMS:
                pass  # year-end balance sheet doubles as Q4
            key = v["end"]
            cur = rows.get(key)
            # point in time: keep the FIRST filing that reported this period (later 10-Ks repeat it as comparatives)
            if cur is None or v.get("filed", "9999") < cur["filed"]:
                rows[key] = {"end": v["end"], "val": v.get("val"), "filed": v.get("filed"), "form": form, "fy": v.get("fy"),
                             "fp": v.get("fp"), "concept": concept}
        if rows:
            return sorted(rows.values(), key=lambda r: r["end"], reverse=True)
    return []


def statements(ticker: str, freq: str = "annual", periods: int = 8) -> dict:
    """{'name', 'cik', 'periods': [{end, filed, form, fp}], 'rows': [{label, values: [..]}], 'source'}."""
    fx = facts(ticker)
    if not fx:
        return {"error": f"SEC EDGAR 沒有 {ticker} 的財報（可能不是在美國申報的公司）"}
    series = {label: _series(fx, concepts, unit, kind, freq) for label, concepts, unit, kind in ITEMS}
    anchor = series["營業收入"] or series["稅後淨利"] or series["資產總額"]
    ends = [r["end"] for r in anchor][:periods]
    meta = {r["end"]: r for r in anchor}
    rows = []
    for label, *_ in ITEMS:
        by_end = {r["end"]: r for r in series[label]}
        vals = [by_end.get(e, {}).get("val") for e in ends]
        if any(v is not None for v in vals):
            rows.append({"label": label, "values": vals})
    ocf = next((r for r in rows if r["label"] == "營業現金流"), None)
    capex = next((r for r in rows if r["label"] == "資本支出"), None)
    if ocf and capex:
        rows.insert(rows.index(capex) + 1, {"label": "自由現金流（計算）", "values": [
            (a - b) if a is not None and b is not None else None for a, b in zip(ocf["values"], capex["values"])]})
    return {"name": fx.get("entityName"), "cik": fx.get("cik"), "freq": freq, "source": "SEC EDGAR XBRL",
            "periods": [{"end": e, "filed": meta[e]["filed"], "form": meta[e]["form"], "fp": meta[e].get("fp")} for e in ends],
            "rows": rows}


def filed_date(ticker: str, period_end: str, tolerance_days: int = 5) -> str | None:
    """When the report for the period ending `period_end` was first filed (for point-in-time backtests)."""
    try:
        fx = facts(ticker)
    except Exception:  # noqa: BLE001
        return None
    if not fx:
        return None
    pe = dt.date.fromisoformat(period_end[:10])
    best = None
    for concepts in (ITEMS[0][1], ITEMS[3][1], ITEMS[9][1]):
        for ns in ("us-gaap", "ifrs-full"):
            for c in concepts:
                node = fx.get("facts", {}).get(ns, {}).get(c)
                if not node:
                    continue
                for vals in node.get("units", {}).values():
                    for v in vals:
                        if v.get("form") not in ANNUAL_FORMS + QUARTER_FORMS or not v.get("filed"):
                            continue
                        if abs((dt.date.fromisoformat(v["end"]) - pe).days) <= tolerance_days:
                            best = min(best, v["filed"]) if best else v["filed"]
        if best:
            return best
    return best


# ------------------------------------------------------------------ recent filings
FORM_ZH = {"10-K": "年報", "10-K/A": "年報（修正）", "10-Q": "季報", "10-Q/A": "季報（修正）", "8-K": "重大事件",
           "8-K/A": "重大事件（修正）", "4": "內部人持股變動", "3": "內部人初次申報", "5": "內部人年度申報",
           "DEF 14A": "股東會委託書", "S-1": "上市註冊", "S-3": "增資註冊", "424B2": "發行說明", "SC 13D": "大股東持股（主動）",
           "SC 13G": "大股東持股（被動）", "SC 13D/A": "大股東持股（主動）修正", "SC 13G/A": "大股東持股（被動）修正",
           "20-F": "外國公司年報", "6-K": "外國公司臨時報告", "11-K": "員工持股計畫年報", "144": "內部人擬出售",
           "13F-HR": "機構持股季報"}
ITEM_ZH = {"1.01": "簽訂重大合約", "1.02": "終止重大合約", "1.05": "重大資安事件", "2.01": "完成收購或處分資產",
           "2.02": "營運成果與財務狀況（財報）", "2.03": "新增重大債務", "2.05": "重組／裁員", "2.06": "重大減損",
           "3.01": "下市或不符上市標準", "3.02": "未註冊股票發行", "4.01": "更換會計師", "4.02": "財報不可再依賴",
           "5.01": "控制權變動", "5.02": "董事或高階主管異動", "5.03": "章程修訂", "5.07": "股東會表決結果",
           "7.01": "Regulation FD 揭露", "8.01": "其他重大事件", "9.01": "財報與附件"}


def recent_filings(ticker: str, forms: tuple[str, ...] | None = None, limit: int = 20) -> list[dict]:
    s = submissions(ticker)
    if not s:
        return []
    rc = (s.get("filings") or {}).get("recent") or {}
    n = len(rc.get("form", []))
    out = []
    cik = int(s.get("cik") or 0)
    for i in range(n):
        form = rc["form"][i]
        if forms and form not in forms:
            continue
        acc = rc["accessionNumber"][i]
        doc = (rc.get("primaryDocument") or [""] * n)[i]
        items = [x.strip() for x in str((rc.get("items") or [""] * n)[i] or "").split(",") if x.strip()]
        out.append({"form": form, "form_zh": FORM_ZH.get(form, ""), "date": rc["filingDate"][i],
                    "report_date": (rc.get("reportDate") or [""] * n)[i] or None,
                    "items": [{"code": x, "zh": ITEM_ZH.get(x, "")} for x in items],
                    "desc": (rc.get("primaryDocDescription") or [""] * n)[i] or "",
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}" if doc else
                           f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"})
        if len(out) >= limit:
            break
    return out


def filings_text(ticker: str, limit: int = 12) -> str:
    rows = recent_filings(ticker, limit=limit)
    if not rows:
        return f"（SEC EDGAR 沒有 {ticker} 的申報）"
    lines = [f"【{ticker} SEC 最近申報】"]
    for r in rows:
        it = "；".join(f"{x['code']} {x['zh']}" for x in r["items"]) if r["items"] else ""
        lines.append(f"- {r['date']} {r['form']} {r['form_zh']}" + (f"（{it}）" if it else "") + f" {r['url']}")
    return "\n".join(lines)
