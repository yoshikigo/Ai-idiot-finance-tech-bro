"""產業分布：美股與台股依產業的市值占比、當日漲跌，以及各產業的成分股。

美股：Nasdaq 股票篩選器（api.nasdaq.com，NYSE／Nasdaq／AMEX 全部上市股票的市值、產業、細產業，一次一包）。
      抓不到時改用內建約 130 檔代表性大型股＋Yahoo 市值（畫面會註明是代表股估算）。
      依 Nasdaq 的 11 個 sector 分類，另把「運輸航運」從工業、「農業」從必需消費／原物料獨立出來。
台股：證交所／櫃買 ISIN 清單的產業別＋OpenAPI 的每日收盤價＋公司基本資料的發行股數（沒有時用實收資本額 ÷ 面額）
      → 每檔市值，加總成各產業占比（上市＋上櫃普通股，不含 ETF、存託憑證）。
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import re
import time

from . import market

log = logging.getLogger("ft.sectors")

NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"
NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/130.0 Safari/537.36", "Accept": "application/json, text/plain, */*",
                  "Accept-Language": "en-US,en;q=0.9", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
TWSE_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAY_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TWSE_PROFILE = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_PROFILE = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TTL = 30 * 60

# ------------------------------------------------------------------ US sector names
US_SECTOR = {"Technology": "科技", "Telecommunications": "通訊服務", "Consumer Discretionary": "非必需消費",
             "Consumer Staples": "必需消費", "Finance": "金融", "Health Care": "醫療保健", "Industrials": "工業／製造",
             "Energy": "能源", "Basic Materials": "原物料", "Utilities": "公用事業", "Real Estate": "房地產",
             "Miscellaneous": "其他"}
TRANSPORT_KW = ("Marine", "Shipping", "Trucking", "Air Freight", "Railroad", "Transportation Services", "Airline")
AGRI_KW = ("Farming", "Agricultural", "Seeds", "Milling", "Meat/Poultry/Fish")
AGRI_SYMS = {"DE", "AGCO", "ADM", "BG", "CTVA", "MOS", "CF", "NTR", "TSN", "FMC", "CNH", "LNN", "AGRO", "ANDE"}
TRANSPORT_SYMS = {"UPS", "FDX", "UNP", "CSX", "NSC", "ODFL", "DAL", "UAL", "LUV", "AAL", "MATX", "KEX", "ZIM", "JBHT",
                  "CHRW", "XPO", "SAIA", "EXPD", "KNX", "R", "CP", "CNI"}
INDUSTRY_ZH = {
    "Semiconductors": "半導體", "Computer Manufacturing": "電腦硬體", "EDP Services": "資訊服務",
    "Computer Software: Prepackaged Software": "套裝軟體", "Computer Software: Programming Data Processing": "軟體與數據處理",
    "Computer peripheral equipment": "電腦周邊", "Radio And Television Broadcasting And Communications Equipment": "通訊設備",
    "Industrial Machinery/Components": "工業機械", "Electrical Products": "電機產品", "Aerospace": "航太",
    "Military/Government/Technical": "國防", "Major Banks": "大型銀行", "Commercial Banks": "商業銀行",
    "Investment Bankers/Brokers/Service": "投資銀行／券商", "Finance: Consumer Services": "消費金融",
    "Business Services": "商業服務", "Property-Casualty Insurers": "產物保險", "Life Insurance": "壽險",
    "Accident &Health Insurance": "健康保險", "Investment Managers": "資產管理", "Finance/Investors Services": "金融服務",
    "Biotechnology: Pharmaceutical Preparations": "製藥", "Biotechnology: Biological Products (No Diagnostic Substances)": "生技",
    "Medical/Dental Instruments": "醫療器材", "Medical Specialities": "專科醫療", "Hospital/Nursing Management": "醫院管理",
    "Medical/Nursing Services": "醫療服務", "Integrated oil Companies": "綜合石油", "Oil & Gas Production": "油氣開採",
    "Oilfield Services/Equipment": "油田服務", "Oil Refining/Marketing": "煉油", "Natural Gas Distribution": "天然氣",
    "Electric Utilities: Central": "電力", "Power Generation": "發電", "Real Estate Investment Trusts": "不動產投信（REIT）",
    "Catalog/Specialty Distribution": "電商／目錄零售", "Department/Specialty Retail Stores": "零售", "Auto Manufacturing": "汽車",
    "Restaurants": "餐飲", "Hotels/Resorts": "旅館", "Packaged Foods": "包裝食品", "Beverages (Production/Distribution)": "飲料",
    "Package Goods/Cosmetics": "日用品／化妝品", "Food Chains": "連鎖量販", "Cable & Other Pay Television Services": "有線電視",
    "Telecommunications Equipment": "電信設備", "Movies/Entertainment": "影視娛樂", "Marine Transportation": "海運",
    "Trucking Freight/Courier Services": "貨運快遞", "Air Freight/Delivery Services": "航空貨運", "Railroads": "鐵路",
    "Transportation Services": "運輸服務", "Farming/Seeds/Milling": "農作物／穀物", "Agricultural Chemicals": "農用化學",
    "Meat/Poultry/Fish": "肉品", "Steel/Iron Ore": "鋼鐵", "Precious Metals": "貴金屬", "Major Chemicals": "化學",
    "Mining & Quarrying of Nonmetallic Minerals (No Fuels)": "礦業", "Engineering & Construction": "工程營造",
    "Homebuilding": "住宅建商", "Industrial Specialties": "工業特用品", "Diversified Commercial Services": "綜合商業服務",
}

# Representative large caps: fallback universe for the US breakdown and Chinese names for quotes.
# (symbol, 中文名, 分類)
US_STOCKS = [
    ("AAPL", "蘋果", "科技"), ("MSFT", "微軟", "科技"), ("NVDA", "輝達", "科技"), ("AVGO", "博通", "科技"),
    ("ORCL", "甲骨文", "科技"), ("CRM", "Salesforce", "科技"), ("AMD", "超微", "科技"), ("ADBE", "Adobe", "科技"),
    ("CSCO", "思科", "科技"), ("IBM", "IBM", "科技"), ("QCOM", "高通", "科技"), ("TXN", "德州儀器", "科技"),
    ("INTC", "英特爾", "科技"), ("MU", "美光", "科技"), ("AMAT", "應用材料", "科技"), ("LRCX", "科林研發", "科技"),
    ("KLAC", "科磊", "科技"), ("PLTR", "Palantir", "科技"), ("NOW", "ServiceNow", "科技"), ("ANET", "Arista", "科技"),
    ("GOOGL", "Alphabet", "通訊服務"), ("META", "Meta", "通訊服務"), ("NFLX", "網飛", "通訊服務"),
    ("TMUS", "T-Mobile", "通訊服務"), ("DIS", "迪士尼", "通訊服務"), ("VZ", "Verizon", "通訊服務"),
    ("T", "AT&T", "通訊服務"), ("CMCSA", "康卡斯特", "通訊服務"),
    ("AMZN", "亞馬遜", "非必需消費"), ("TSLA", "特斯拉", "非必需消費"), ("HD", "家得寶", "非必需消費"),
    ("MCD", "麥當勞", "非必需消費"), ("BKNG", "Booking", "非必需消費"), ("LOW", "勞氏", "非必需消費"),
    ("TJX", "TJX", "非必需消費"), ("NKE", "Nike", "非必需消費"), ("SBUX", "星巴克", "非必需消費"),
    ("WMT", "沃爾瑪", "必需消費"), ("COST", "好市多", "必需消費"), ("PG", "寶僑", "必需消費"),
    ("KO", "可口可樂", "必需消費"), ("PEP", "百事", "必需消費"), ("PM", "菲利普莫里斯", "必需消費"),
    ("MDLZ", "億滋", "必需消費"), ("CL", "高露潔", "必需消費"),
    ("BRK-B", "波克夏", "金融"), ("JPM", "摩根大通", "金融"), ("V", "Visa", "金融"), ("MA", "萬事達", "金融"),
    ("BAC", "美國銀行", "金融"), ("WFC", "富國銀行", "金融"), ("GS", "高盛", "金融"), ("MS", "摩根士丹利", "金融"),
    ("AXP", "美國運通", "金融"), ("BLK", "貝萊德", "金融"), ("SCHW", "嘉信理財", "金融"), ("C", "花旗", "金融"),
    ("LLY", "禮來", "醫療保健"), ("UNH", "聯合健康", "醫療保健"), ("JNJ", "嬌生", "醫療保健"), ("ABBV", "艾伯維", "醫療保健"),
    ("MRK", "默克", "醫療保健"), ("TMO", "賽默飛", "醫療保健"), ("ABT", "亞培", "醫療保健"), ("ISRG", "直覺手術", "醫療保健"),
    ("PFE", "輝瑞", "醫療保健"), ("AMGN", "安進", "醫療保健"),
    ("GE", "奇異航太", "工業／製造"), ("CAT", "開拓重工", "工業／製造"), ("RTX", "RTX", "工業／製造"),
    ("HON", "漢威聯合", "工業／製造"), ("BA", "波音", "工業／製造"), ("LMT", "洛克希德馬丁", "工業／製造"),
    ("ETN", "伊頓", "工業／製造"), ("GEV", "GE Vernova", "工業／製造"), ("MMM", "3M", "工業／製造"),
    ("UPS", "優比速", "運輸航運"), ("FDX", "聯邦快遞", "運輸航運"), ("UNP", "聯合太平洋鐵路", "運輸航運"),
    ("CSX", "CSX 鐵路", "運輸航運"), ("NSC", "諾福克南方鐵路", "運輸航運"), ("ODFL", "Old Dominion 貨運", "運輸航運"),
    ("DAL", "達美航空", "運輸航運"), ("UAL", "聯合航空", "運輸航運"), ("MATX", "Matson 海運", "運輸航運"),
    ("KEX", "Kirby 內河航運", "運輸航運"), ("ZIM", "以星航運", "運輸航運"),
    ("DE", "迪爾", "農業"), ("ADM", "艾地盟", "農業"), ("BG", "邦吉", "農業"), ("CTVA", "Corteva", "農業"),
    ("MOS", "美盛", "農業"), ("CF", "CF Industries", "農業"), ("TSN", "泰森食品", "農業"), ("AGCO", "愛科", "農業"),
    ("XOM", "埃克森美孚", "能源"), ("CVX", "雪佛龍", "能源"), ("COP", "康菲石油", "能源"), ("EOG", "EOG 資源", "能源"),
    ("SLB", "斯倫貝謝", "能源"), ("OXY", "西方石油", "能源"), ("PSX", "Phillips 66", "能源"), ("MPC", "馬拉松原油", "能源"),
    ("LIN", "林德", "原物料"), ("SHW", "宣偉", "原物料"), ("APD", "空氣化工", "原物料"), ("FCX", "自由港麥克莫蘭", "原物料"),
    ("NEM", "紐蒙特", "原物料"), ("ECL", "藝康", "原物料"), ("DOW", "陶氏", "原物料"), ("NUE", "紐柯鋼鐵", "原物料"),
    ("NEE", "新紀元能源", "公用事業"), ("SO", "南方電力", "公用事業"), ("DUK", "杜克能源", "公用事業"),
    ("CEG", "Constellation 能源", "公用事業"), ("AEP", "美國電力", "公用事業"), ("VST", "Vistra", "公用事業"),
    ("PLD", "安博", "房地產"), ("AMT", "美國電塔", "房地產"), ("EQIX", "Equinix", "房地產"), ("WELL", "Welltower", "房地產"),
    ("SPG", "西蒙地產", "房地產"), ("O", "Realty Income", "房地產"),
]
US_ZH = {s: n for s, n, _ in US_STOCKS} | {"TSM": "台積電 ADR", "ASML": "艾司摩爾", "GOOG": "Alphabet C", "BRK-A": "波克夏 A"}
US_SECTOR_OF = {s: g for s, _, g in US_STOCKS}

COLORS = ["#4ba3ff", "#ff9f40", "#3ad67a", "#ff6b6b", "#b48cff", "#3ad6d6", "#ffd166", "#ff8ad8", "#9fe870",
          "#c9a27e", "#8fa3ff", "#e6e6e6", "#ff4d4f", "#7bd88f", "#d6c43a"]

_neg: dict[str, tuple[float, str]] = {}


def _num(x) -> float | None:
    if x is None:
        return None
    t = re.sub(r"[^0-9.\-]", "", str(x).replace(",", ""))
    try:
        v = float(t)
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def _guard(key: str, ttl: float, fn):
    neg = _neg.get(key)
    if neg and time.time() < neg[0]:
        raise RuntimeError(neg[1])
    try:
        return market.cached(f"sectors:{key}", ttl, fn)
    except Exception as e:  # noqa: BLE001
        why = str(e)[:240] or type(e).__name__
        _neg[key] = (time.time() + 600, why)
        raise


# ------------------------------------------------------------------ US
def us_group(sector: str, industry: str, sym: str) -> str:
    """Nasdaq sector/industry → our group (運輸航運 and 農業 are carved out)."""
    if sym in AGRI_SYMS or any(k in (industry or "") for k in AGRI_KW):
        return "農業"
    if sym in TRANSPORT_SYMS or any(k in (industry or "") for k in TRANSPORT_KW):
        return "運輸航運"
    return US_SECTOR.get(sector or "", "其他")


def _norm_name(name: str) -> str:
    n = re.sub(r"\b(Class [A-C]|Series [A-C]|Common Stock|Capital Stock|Ordinary Shares?|Inc\.?|Corp(oration)?\.?|"
               r"American Depositary Shares?|ADS|Holdings?|plc|Ltd\.?|N\.V\.)\b", " ", name or "", flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()


def parse_nasdaq(d: dict) -> list[dict]:
    rows = ((d.get("data") or {}).get("rows") or (d.get("data") or {}).get("table", {}).get("rows") or [])
    out, seen = [], {}
    for r in rows:
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym or "^" in sym or len(sym) > 6:
            continue
        sym = sym.replace("/", "-")
        cap = _num(r.get("marketCap"))
        if not cap or cap <= 0:
            continue
        name = str(r.get("name") or "")
        if re.search(r"\b(Warrant|Right|Unit|Preferred|Depositary Shares? Representing|Notes? due)\b", name, re.I):
            continue
        pct = _num(r.get("pctchange"))
        row = {"symbol": sym, "name": US_ZH.get(sym) or re.sub(r"\s+(Common Stock|Class [A-C].*|Ordinary Shares.*)$", "", name),
               "price": _num(r.get("lastsale")), "pct": pct, "cap": cap, "industry_en": r.get("industry") or "",
               "industry": INDUSTRY_ZH.get(r.get("industry") or "", r.get("industry") or ""), "country": r.get("country") or "",
               "group": us_group(r.get("sector") or "", r.get("industry") or "", sym)}
        key = _norm_name(name)  # share classes (GOOG / GOOGL, BRK-A / BRK-B) → keep the bigger one, never both
        if key and key in seen:
            prev = out[seen[key]]
            if cap > prev["cap"]:
                out[seen[key]] = row
            continue
        seen[key] = len(out)
        out.append(row)
    return out


def _us_nasdaq() -> list[dict]:
    from .twhttp import fetch_json
    d = fetch_json(NASDAQ_URL, params={"tableonly": "true", "limit": "10000", "download": "true"},
                   headers=NASDAQ_HEADERS, timeout=40)
    rows = parse_nasdaq(d)
    if len(rows) < 500:
        raise RuntimeError(f"Nasdaq 篩選器只回 {len(rows)} 檔")
    return rows


def _us_curated() -> list[dict]:
    syms = [s for s, _, _ in US_STOCKS]
    qs = {q["symbol"]: q for q in market.quotes(syms)}
    caps = {}
    if market.FAKE:
        import numpy as np
        rng = np.random.default_rng(7)
        caps = {s: float(rng.lognormal(26, 0.9)) for s in syms}
        for s, v in (("NVDA", 4.6e12), ("MSFT", 3.9e12), ("AAPL", 3.8e12), ("GOOGL", 3.1e12), ("AMZN", 2.4e12), ("META", 1.8e12)):
            caps[s] = v
    else:
        from concurrent.futures import ThreadPoolExecutor
        yf = market._yf()  # noqa: SLF001

        def cap(s):
            try:
                return s, float(yf.Ticker(s).fast_info["market_cap"] or 0)
            except Exception:  # noqa: BLE001
                return s, 0.0
        with ThreadPoolExecutor(8) as ex:
            caps = dict(ex.map(cap, syms))
    out = []
    for s, zh, g in US_STOCKS:
        q = qs.get(s, {})
        if caps.get(s):
            out.append({"symbol": s, "name": zh, "price": q.get("last"), "pct": q.get("pct"), "cap": caps[s],
                        "industry": "", "industry_en": "", "country": "", "group": g})
    return out


def us_rows() -> tuple[list[dict], str]:
    if market.FAKE:
        return _guard("us_curated", TTL, _us_curated), "示範資料（代表性大型股）"
    try:
        return _guard("us_nasdaq", TTL, _us_nasdaq), "Nasdaq 股票篩選器（NYSE／Nasdaq／AMEX 全部上市股票）"
    except Exception as e:  # noqa: BLE001
        log.info("nasdaq screener failed: %s", e)
        return _guard("us_curated", TTL, _us_curated), f"代表性大型股 {len(US_STOCKS)} 檔估算（Nasdaq 篩選器抓不到：{str(e)[:80]}）"


# ------------------------------------------------------------------ Taiwan
def _pick(r: dict, *keys):
    for k in keys:
        if k in r and str(r[k]).strip() not in ("", "-", "--"):
            return r[k]
    return None


def tw_shares(profile_rows: list[dict], code_key: str) -> dict[str, float]:
    """code → issued common shares: 已發行普通股數 if given, else 實收資本額 ÷ 面額 (default NT$10)."""
    out = {}
    for r in profile_rows:
        code = str(r.get(code_key) or "").strip()
        if not code:
            continue
        n = _num(_pick(r, "已發行普通股數或TDR原股發行股數", "IssueShares", "NumberOfSharesIssued"))
        if not n:
            cap = _num(_pick(r, "實收資本額", "Paidin.Capital.NTDollars"))
            par = _num(re.sub(r"[^0-9.]", "", str(_pick(r, "普通股每股面額", "ParValueOfCommonStock") or ""))) or 10.0
            n = cap / par if cap and par else None
        if n:
            out[code] = n
    return out


def tw_prices(rows: list[dict]) -> dict[str, tuple[float, float | None]]:
    """code → (close, pct) from TWSE STOCK_DAY_ALL / TPEx daily close quotes."""
    out = {}
    for r in rows:
        code = str(_pick(r, "Code", "SecuritiesCompanyCode", "證券代號") or "").strip()
        close = _num(_pick(r, "ClosingPrice", "Close", "收盤價"))
        if not code or not close:
            continue
        chg = _num(_pick(r, "Change", "漲跌價差"))
        prev = close - chg if chg is not None else None
        out[code] = (close, (chg / prev * 100) if prev else None)
    return out


def _tw_live() -> list[dict]:
    from . import twmarket
    from .twhttp import fetch_json
    secs = twmarket.securities()
    prices, shares = {}, {}
    errs = []
    for url in (TWSE_DAY_ALL, TPEX_DAY_ALL):
        try:
            prices.update(tw_prices(fetch_json(url, timeout=40)))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{url.split('/')[-1]}: {type(e).__name__}")
    for url, key in ((TWSE_PROFILE, "公司代號"), (TPEX_PROFILE, "SecuritiesCompanyCode")):
        try:
            shares.update(tw_shares(fetch_json(url, timeout=40), key))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{url.split('/')[-1]}: {type(e).__name__}")
    out = []
    for code, s in secs.items():
        if s.get("kind") != "股票" or code not in prices or code not in shares:
            continue
        close, pct = prices[code]
        out.append({"symbol": s["symbol"], "code": code, "name": s["name"], "price": close, "pct": pct,
                    "cap": close * shares[code], "industry": s.get("market") or "", "group": s.get("industry") or "其他"})
    if len(out) < 200:
        raise RuntimeError(f"只算出 {len(out)} 檔市值（{'；'.join(errs) or '資料不足'}）")
    return out


def _tw_fake() -> list[dict]:
    import numpy as np
    from . import twmarket
    rng = np.random.default_rng(11)
    caps = {"2330": 2.6e13, "2317": 2.4e12, "2454": 1.9e12, "2308": 1.2e12, "2382": 1.0e12, "2881": 1.1e12,
            "2882": 9.0e11, "2891": 7.0e11, "2303": 6.0e11, "3711": 5.8e11, "2412": 1.0e12, "6488": 2.0e11, "5347": 1.5e11}
    q = {r["symbol"]: r for r in market.quotes([s["symbol"] for s in twmarket.securities().values() if s["kind"] == "股票"])}
    out = []
    for code, s in twmarket.securities().items():
        if s["kind"] != "股票":
            continue
        r = q.get(s["symbol"], {})
        out.append({"symbol": s["symbol"], "code": code, "name": s["name"], "price": r.get("last"),
                    "pct": r.get("pct", float(rng.normal(0, 1.2))), "cap": caps.get(code, float(rng.lognormal(25, 1))),
                    "industry": s["market"], "group": s["industry"] or "其他"})
    for g, n, c in (("航運業", "長榮", 4.5e11), ("航運業", "陽明", 2.2e11), ("塑膠工業", "台塑", 3.5e11),
                    ("鋼鐵工業", "中鋼", 3.6e11), ("食品工業", "統一", 4.3e11), ("水泥工業", "台泥", 2.4e11),
                    ("農業科技業", "大成", 5.5e10), ("生技醫療業", "藥華藥", 1.6e11)):
        out.append({"symbol": "", "code": "", "name": n, "price": None, "pct": float(rng.normal(0, 1.5)), "cap": c,
                    "industry": "上市", "group": g})
    return out


def tw_rows() -> tuple[list[dict], str]:
    if market.FAKE:
        return _guard("tw_fake", TTL, _tw_fake), "示範資料"
    return (_guard("tw_live", TTL, _tw_live),
            "證交所／櫃買每日收盤價 × 發行股數（上市＋上櫃普通股，不含 ETF、存託憑證）")


# ------------------------------------------------------------------ breakdown
def breakdown(mkt: str = "us", top: int = 30) -> dict:
    mkt = "tw" if mkt.lower() in ("tw", "台股", "taiwan") else "us"
    rows, source = tw_rows() if mkt == "tw" else us_rows()
    total = sum(r["cap"] for r in rows) or 1
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["group"] or "其他", []).append(r)
    out = []
    for g, rs in groups.items():
        cap = sum(r["cap"] for r in rs)
        wp = [(r["cap"], r["pct"]) for r in rs if r["pct"] is not None]
        day = sum(c * p for c, p in wp) / sum(c for c, _ in wp) if wp else None
        rs = sorted(rs, key=lambda r: -r["cap"])
        inds: dict[str, float] = {}
        for r in rs:
            if r.get("industry") and mkt == "us":
                inds[r["industry"]] = inds.get(r["industry"], 0) + r["cap"]
        out.append({"name": g, "cap": cap, "weight": cap / total * 100, "pct": day, "count": len(rs),
                    "industries": [{"name": k, "weight": v / cap * 100} for k, v in sorted(inds.items(), key=lambda x: -x[1])[:8]],
                    "stocks": [{"symbol": r["symbol"], "name": r["name"], "price": r["price"], "pct": r["pct"], "cap": r["cap"],
                                "weight_group": r["cap"] / cap * 100, "weight_market": r["cap"] / total * 100,
                                "industry": r.get("industry") or ""} for r in rs[:top]]})
    out.sort(key=lambda x: -x["weight"])
    for k, x in enumerate(out):
        x["color"] = COLORS[k % len(COLORS)]
    unit = ("兆元", 1e12) if mkt == "tw" else ("兆美元", 1e12)
    return {"market": mkt, "title": "台股產業分布" if mkt == "tw" else "美股產業分布", "source": source,
            "total": total, "unit": unit[0], "total_display": total / unit[1], "count": len(rows),
            "sectors": out, "at": dt.datetime.now().isoformat(timespec="minutes")}


def text(mkt: str = "us", n_stocks: int = 3) -> str:
    d = breakdown(mkt, top=n_stocks)
    lines = [f"【{d['title']}】總市值約 {d['total_display']:,.1f} {d['unit']}、{d['count']} 檔（{d['source']}）"]
    for s in d["sectors"][:15]:
        day = "" if s["pct"] is None else f"，今日 {s['pct']:+.2f}%"
        top = "、".join(f"{x['name']}（{x['weight_group']:.0f}%）" for x in s["stocks"][:n_stocks])
        lines.append(f"- {s['name']}：{s['weight']:.1f}%（{s['count']} 檔{day}）｜{top}")
    return "\n".join(lines)
