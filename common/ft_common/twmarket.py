"""Taiwan market data: securities master (Chinese names, ETFs), MIS real-time quotes,
ETF estimated NAV / premium, ETF info / holdings / dividends, company profiles, Chinese news.

Sources (all public, no key):
- 證券清單  isin.twse.com.tw C_public.jsp strMode=2 (上市) / 4 (上櫃)       daily, Big5 HTML
- 即時報價  mis.twse.com.tw/stock/api/getStockInfo.jsp                        ~5 s
- ETF 淨值  mis.twse.com.tw/stock/data/all_etf.txt (TWSE 介接格式 a–k)          ~15 s
- ETF 資料  SITCA ETF zone (see sitca.py), TWSE OpenAPI t187ap47_L
- 公司資料  TWSE OpenAPI t187ap03_L / TPEx OpenAPI mopsfin_t187ap03_O
- 中文新聞  Google News RSS (hl=zh-TW)
FT_FAKE_DATA=1 → synthetic data (tests / offline demo).
"""
from __future__ import annotations

import datetime as dt
import email.utils
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus

from . import market, sitca
from .twhttp import decode_tw, fetch, fetch_json

log = logging.getLogger("ft.tw")
FAKE = market.FAKE
DATA = Path(os.environ.get("FT_DATA", "/data"))

ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp"
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
ALL_ETF_URL = "https://mis.twse.com.tw/stock/data/all_etf.txt"
TWSE_OPENAPI = "https://openapi.twse.com.tw/v1"
TPEX_OPENAPI = "https://www.tpex.org.tw/openapi/v1"

INDUSTRY = {"01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
            "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造業",
            "15": "航運業", "16": "觀光餐旅", "17": "金融保險業", "18": "貿易百貨業", "19": "綜合", "20": "其他業",
            "21": "化學工業", "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業",
            "26": "光電業", "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
            "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務", "35": "綠能環保",
            "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "80": "管理股票"}

# Section headers on the ISIN page we keep (everything else — warrants, bonds… — is skipped)
KEEP_TYPES = ("股票", "ETF", "ETN", "特別股", "臺灣存託憑證", "受益證券", "創新板")


def is_tw(sym: str) -> bool:
    return sym.endswith((".TW", ".TWO"))


def code_of(sym: str) -> str:
    return sym.split(".")[0]


# ================================================================ securities master
class _IsinParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_isin(html: str, market_label: str) -> dict[str, dict]:
    p = _IsinParser()
    p.feed(html)
    out, section = {}, ""
    for r in p.rows:
        if len(r) == 1 or (len(r) >= 2 and not any(r[1:])):
            section = r[0].strip()
            continue
        if len(r) < 5 or not r[0] or r[0].startswith("有價證券代號"):
            continue
        if section and not any(k in section for k in KEEP_TYPES):
            continue
        parts = re.split(r"[　\s]+", r[0].strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        code, name = parts
        kind = section or "股票"
        if code.startswith("00") and "ETF" not in kind and "ETN" not in kind and len(code) >= 4:
            kind = "ETF"
        if kind.startswith("受益證券-指數股票型") or "ETF" in kind:
            kind = "ETF"
        out[code] = {"code": code, "name": name.strip(), "isin": r[1], "listed": r[2].replace("/", "-"),
                     "market": r[3] or market_label, "industry": r[4], "kind": kind,
                     "symbol": f"{code}.{'TW' if (r[3] or market_label) == '上市' else 'TWO'}"}
    return out


_sec_lock = threading.Lock()
_sec: dict | None = None
_sec_at = 0.0


def securities() -> dict[str, dict]:
    """code → {code,name,isin,listed,market,industry,kind,symbol}. Cached 24 h on disk."""
    global _sec, _sec_at
    with _sec_lock:
        if _sec is not None and time.time() - _sec_at < 6 * 3600:
            return _sec
        f = DATA / "cache" / "tw_securities.json"
        if FAKE:
            _sec, _sec_at = _fake_securities(), time.time()
            return _sec
        if f.exists() and time.time() - f.stat().st_mtime < 24 * 3600:
            try:
                _sec, _sec_at = json.loads(f.read_text()), time.time()
                return _sec
            except json.JSONDecodeError:
                pass
        out = {}
        try:
            for mode, label in (("2", "上市"), ("4", "上櫃")):
                html = decode_tw(fetch(ISIN_URL, params={"strMode": mode}, timeout=90))
                out.update(parse_isin(html, label))
        except Exception as e:  # noqa: BLE001
            log.warning("securities list fetch failed: %s", e)
            if f.exists():
                _sec, _sec_at = json.loads(f.read_text()), time.time() - 5 * 3600  # retry in 1 h
                return _sec
            return {}
        if out:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(out, ensure_ascii=False))
            _sec, _sec_at = out, time.time()
        return out


def lookup(code_or_sym: str) -> dict | None:
    return securities().get(code_of(code_or_sym.upper()))


def tw_symbol(code: str) -> str:
    s = lookup(code)
    return s["symbol"] if s else f"{code}.TW"


def zh_name(sym: str) -> str | None:
    if not is_tw(sym):
        return None
    s = lookup(sym)
    return s["name"] if s else None


def search(q: str, limit: int = 15) -> list[dict]:
    """Chinese/ code search over listed + OTC securities (stocks, ETFs)."""
    q = q.strip().upper()
    if not q:
        return []
    res = []
    for s in securities().values():
        score = None
        if s["code"] == q:
            score = 0
        elif s["name"].upper() == q:
            score = 1
        elif s["code"].startswith(q):
            score = 2
        elif q in s["name"].upper():
            score = 3 + len(s["name"]) / 100
        if score is not None:
            res.append((score, s))
    res.sort(key=lambda x: (x[0], x[1]["code"]))
    return [{"symbol": s["symbol"], "name": s["name"], "exchange": s["market"], "type": s["kind"],
             "industry": s["industry"]} for _, s in res[:limit]]


# ================================================================ MIS real-time quotes
def _f(x):
    try:
        v = float(str(x).replace(",", ""))
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def realtime(symbols: list[str]) -> dict[str, dict]:
    """symbol → quote dict (same shape as market.quotes rows) using MIS. Indexes: ^TWII, ^TWOII."""
    if not symbols:
        return {}
    if FAKE:
        return {}
    chans, back = [], {}
    for s in symbols:
        if s == "^TWII":
            ch = "tse_t00.tw"
        elif s == "^TWOII":
            ch = "otc_o00.tw"
        else:
            ch = f"{'otc' if s.endswith('.TWO') else 'tse'}_{code_of(s).lower()}.tw"
        chans.append(ch)
        back[ch.split("_", 1)[1].split(".")[0]] = s
    out = {}
    for i in range(0, len(chans), 60):
        try:
            d = fetch_json(MIS_URL, params={"ex_ch": "|".join(chans[i:i + 60]), "json": "1", "delay": "0",
                                            "_": str(int(time.time() * 1000))}, timeout=8, ttl=5)
        except Exception as e:  # noqa: BLE001
            log.info("MIS failed: %s", e)
            continue
        for it in d.get("msgArray") or []:
            c = (it.get("c") or "").lower()
            if not c or c not in back:
                continue
            sym = back[c]
            prev = _f(it.get("y"))
            last = _f(it.get("z"))
            indicative = False
            if last is None:  # no trade in this snapshot → best bid/ask mid
                b = _f((it.get("b") or "").split("_")[0])
                a = _f((it.get("a") or "").split("_")[0])
                last = (b + a) / 2 if b and a else (b or a or _f(it.get("o")) or prev)
                indicative = True
            if last is None:
                continue
            row = {"symbol": sym, "name": it.get("n"), "last": last, "prev": prev,
                   "chg": last - prev if prev else 0.0, "pct": (last / prev - 1) * 100 if prev else 0.0,
                   "open": _f(it.get("o")), "high": _f(it.get("h")), "low": _f(it.get("l")),
                   "volume": (_f(it.get("v")) or 0) * (1 if sym.startswith("^") else 1000),
                   "lots": _f(it.get("v")), "limit_up": _f(it.get("u")), "limit_down": _f(it.get("w")),
                   "time": f"{it.get('d', '')} {it.get('t', '')}".strip(), "currency": "TWD",
                   "source": "MIS", "indicative": indicative}
            out[sym] = row
    return out


# ================================================================ ETF NAV / premium
def parse_all_etf(payload: dict) -> list[dict]:
    rows = []
    groups = payload.get("a1")
    if groups is None and "msgArray" in payload:
        groups = [payload]
    for g in groups or []:
        for it in g.get("msgArray") or []:
            code = str(it.get("a", "")).strip()
            if not code:
                continue
            units, prior, est = _f(it.get("c")), _f(it.get("h")), _f(it.get("f"))
            nav = prior if prior is not None else est
            rows.append({"code": code, "name": str(it.get("b", "")).strip(), "units": units,
                         "units_chg": _f(it.get("d")), "price": _f(it.get("e")), "est_nav": est,
                         "premium": _f(it.get("g")), "prior_nav": prior,
                         "prior_nav_text": str(it.get("h", "")).strip(), "date": str(it.get("i", "")).strip(),
                         "time": str(it.get("j", "")).strip(), "kind": str(it.get("k", "")).strip(),
                         "scale": units * nav if units is not None and nav is not None else None})
    return rows


def etf_nav() -> list[dict]:
    if FAKE:
        return _fake_etf_nav()
    try:
        return parse_all_etf(fetch_json(ALL_ETF_URL, timeout=15, ttl=30))
    except Exception as e:  # noqa: BLE001
        log.warning("all_etf.txt failed: %s", e)
        return []


KIND_LABEL = {"1": "國內成分", "2": "亞洲時區", "3": "歐美時區", "4": "全球"}


def etf_category(code: str, name: str = "") -> str:
    c = code.upper()
    if c.endswith("B"):
        return "債券"
    if c.endswith(("L", "R")) or "正2" in name or "反1" in name or "槓桿" in name or "反向" in name:
        return "槓桿/反向"
    if c.endswith("U"):
        return "期貨/商品"
    if c.endswith("A") or "主動" in name:
        return "主動式"
    if any(k in name for k in ("高股息", "股利", "收益", "高息", "優息", "息")):
        return "高股息"
    return "股票型"


def etf_table(sort: str = "scale", category: str = "", q: str = "", limit: int = 400) -> dict:
    rows = etf_nav()
    sec = securities()
    for r in rows:
        s = sec.get(r["code"])
        r["symbol"] = s["symbol"] if s else f"{r['code']}.TW"
        r["category"] = etf_category(r["code"], r["name"])
        r["market"] = s["market"] if s else ""
    if category:
        rows = [r for r in rows if r["category"] == category]
    if q:
        qq = q.upper()
        rows = [r for r in rows if qq in r["code"].upper() or q in r["name"]]
    keys = {
        "scale": (lambda r: r["scale"] or 0, True),
        "premium": (lambda r: r["premium"] if r["premium"] is not None else -1e9, True),
        "discount": (lambda r: r["premium"] if r["premium"] is not None else 1e9, False),
        "creation": (lambda r: r["units_chg"] or 0, True),
        "redemption": (lambda r: r["units_chg"] or 0, False),
        "code": (lambda r: r["code"], False),
    }
    k, rev = keys.get(sort, keys["scale"])
    rows.sort(key=k, reverse=rev)
    date = max((r["date"] for r in rows), default="")
    return {"date": date, "count": len(rows), "rows": rows[:limit]}


def etf_nav_one(code: str) -> dict | None:
    code = code_of(code.upper())
    return next((r for r in etf_nav() if r["code"] == code), None)


def etf_info(code: str) -> dict:
    """Merged ETF profile: SITCA ETF zone + TWSE t187ap47_L (TWSE-listed funds)."""
    code = code_of(code.upper())
    out: dict = {"code": code}
    if FAKE:
        return {**out, "經理公司": "示範投信", "基金名稱": f"示範 {code} ETF 基金", "基金成立日期": "2010-01-01",
                "分類": "國內成分證券指數股票型", "追蹤指數": "臺灣 50 指數", "配息": "季配"}

    def cached(key, ttl, fn):
        return market.cached(key, ttl, fn)

    try:
        info = cached("sitca_info", 6 * 3600, sitca.etf_info_all)
        r = next((x for x in info if x.get("證券代號") == code), None)
        if r:
            for k in ("經理公司", "證券簡稱", "基金名稱", "基金成立日期", "分類", "追蹤指數"):
                if r.get(k) and r[k] != "-":
                    out[k] = _ymd(r[k]) if k == "基金成立日期" else r[k]
            div = next((v for k, v in r.items() if k.startswith("是否有發行")), None)
            if div and div != "-":
                out["配息"] = div
    except Exception as e:  # noqa: BLE001
        out["_sitca_error"] = str(e)[:200]
    try:
        rows = cached("t187ap47_L", 6 * 3600, lambda: fetch_json(f"{TWSE_OPENAPI}/opendata/t187ap47_L", timeout=30))
        r = next((x for x in rows if str(x.get("基金代號", "")).strip() == code), None)
        if r:
            for k, v in r.items():
                v = str(v).strip()
                if v and k not in ("出表日期", "基金代號") and k not in out:
                    out[k] = _ymd(v) if "日期" in k else v
    except Exception as e:  # noqa: BLE001
        out["_twse_error"] = str(e)[:200]
    return out


def _ymd(t: str) -> str:
    t = str(t).strip()
    if re.fullmatch(r"\d{8}", t):
        return f"{t[:4]}-{t[4:6]}-{t[6:]}"
    if re.fullmatch(r"\d{7}", t):
        return f"{int(t[:3]) + 1911}-{t[3:5]}-{t[5:]}"
    return t


def etf_holdings(code: str, quarterly: bool = False) -> dict:
    code = code_of(code.upper())
    if FAKE:
        names = [("2330", "台積電", 45.1), ("2317", "鴻海", 5.2), ("2454", "聯發科", 4.6), ("2308", "台達電", 2.4),
                 ("2382", "廣達", 1.9), ("2881", "富邦金", 1.8), ("2891", "中信金", 1.6), ("3711", "日月光投控", 1.4),
                 ("2882", "國泰金", 1.3), ("2412", "中華電", 1.2)]
        return {"period": dt.date.today().strftime("%Y%m"), "fund_name": f"示範 {code} ETF 基金", "scope": "每月前十大",
                "items": [{"rank": i + 1, "code": c, "name": n, "kind": "國內上市", "pct": p, "amount": p * 1e9}
                          for i, (c, n, p) in enumerate(names)], "total_pct": round(sum(p for *_, p in names), 2)}
    try:
        return market.cached(f"hold:{code}:{quarterly}", 6 * 3600, lambda: sitca.etf_holdings(code, quarterly))
    except Exception as e:  # noqa: BLE001
        return {"error": f"成分股查詢失敗：{e}"}


def dividends(sym: str, years: int = 5) -> list[dict]:
    """Cash dividends per share from yfinance (ex-date, amount)."""
    def load():
        if FAKE:
            today = dt.date.today()
            return [{"date": (today - dt.timedelta(days=91 * i)).isoformat(), "amount": round(0.8 + 0.1 * (i % 3), 3)}
                    for i in range(1, 9)]
        import yfinance as yf
        s = yf.Ticker(sym).dividends
        if s is None or len(s) == 0:
            return []
        cutoff = dt.datetime.now(s.index.tz) - dt.timedelta(days=365 * years)
        s = s[s.index >= cutoff]
        return [{"date": str(i.date()), "amount": round(float(v), 4)} for i, v in s.items()][::-1]
    try:
        return market.cached(f"div:{sym}:{years}", 6 * 3600, load)
    except Exception:  # noqa: BLE001
        return []


def dividend_summary(sym: str, price: float | None) -> dict:
    divs = dividends(sym)
    one_year = [d for d in divs if dt.date.fromisoformat(d["date"]) >= dt.date.today() - dt.timedelta(days=365)]
    ttm = sum(d["amount"] for d in one_year)
    freq = len(one_year)
    label = {12: "月配", 4: "季配", 2: "半年配", 1: "年配"}.get(freq, f"一年 {freq} 次" if freq else "近一年未配息")
    return {"items": divs, "ttm": round(ttm, 4), "count_1y": freq, "frequency": label,
            "yield_ttm": round(ttm / price * 100, 2) if price and ttm else None}


# ================================================================ company profile
def company_profile(sym: str) -> dict:
    """Chinese company basics from TWSE (上市) / TPEx (上櫃) open data."""
    code = code_of(sym)
    s = lookup(code) or {}
    out = {"代號": code, "名稱": s.get("name"), "市場": s.get("market"), "產業別": s.get("industry"),
           "上市日": s.get("listed"), "ISIN": s.get("isin"), "類別": s.get("kind")}
    if FAKE or s.get("kind") == "ETF":
        return {k: v for k, v in out.items() if v}
    try:
        if sym.endswith(".TWO"):
            rows = market.cached("tpex_profile", 12 * 3600,
                                 lambda: fetch_json(f"{TPEX_OPENAPI}/mopsfin_t187ap03_O", timeout=30))
            r = next((x for x in rows if str(x.get("SecuritiesCompanyCode", "")).strip() == code), None)
            if r:
                for k, v in r.items():
                    v = str(v).strip()
                    if v and v not in ("-",):
                        out[OTC_KEYS.get(k, k)] = v
        else:
            rows = market.cached("twse_profile", 12 * 3600,
                                 lambda: fetch_json(f"{TWSE_OPENAPI}/opendata/t187ap03_L", timeout=30))
            r = next((x for x in rows if str(x.get("公司代號", "")).strip() == code), None)
            if r:
                for k, v in r.items():
                    v = str(v).strip()
                    if v and k not in ("出表日期",):
                        out[k] = v
        if out.get("產業別") in INDUSTRY:
            out["產業別"] = INDUSTRY[out["產業別"]]
        for k in list(out):
            if "日期" in k and out[k]:
                out[k] = _ymd(out[k])
    except Exception as e:  # noqa: BLE001
        out["_error"] = str(e)[:200]
    return {k: v for k, v in out.items() if v}


OTC_KEYS = {"SecuritiesCompanyCode": "公司代號", "CompanyName": "公司名稱", "CompanyAbbreviation": "公司簡稱",
            "SecuritiesIndustryCode": "產業別", "Address": "住址", "Chairman": "董事長", "GeneralManager": "總經理",
            "Spokesman": "發言人", "DateOfIncorporation": "成立日期", "DateOfListing": "上櫃日期",
            "Paidin.Capital.NTDollars": "實收資本額", "WebAddress": "網址", "Telephone": "總機電話",
            "UnifiedBusinessNo.": "營利事業統一編號", "EnglishAbbreviationOfCompanyName": "英文簡稱"}


# ================================================================ Chinese news (Google News RSS)
def news_zh(query: str, limit: int = 25, days: int = 14) -> list[dict]:
    def load():
        if FAKE:
            return [{"title": f"{query} 法說會釋出樂觀展望", "publisher": "示範新聞", "link": "", "summary": "",
                     "time": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3 + 7 * i)).isoformat()}
                    for i in range(8)]
        url = (f"https://news.google.com/rss/search?q={quote_plus(query)}+when:{days}d"
               f"&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
        out = _parse_gn(fetch(url, timeout=15, ttl=600))
        return out[:limit]
    try:
        return market.cached(f"gn:{query}:{limit}:{days}", 600, load)
    except Exception as e:  # noqa: BLE001
        log.info("google news failed: %s", e)
        return []


def news_between(query: str, start: str, end: str, limit: int = 15) -> list[dict]:
    """Google News for a past window: start ≤ published < end (YYYY-MM-DD)."""
    if FAKE:
        return [{"title": f"{query.split(' OR ')[0]}：{start} 市場劇烈波動（示範標題 {i + 1}）", "publisher": "示範新聞",
                 "link": "", "summary": "", "time": f"{start}T0{i}:00:00+00:00"} for i in range(5)]
    url = (f"https://news.google.com/rss/search?q={quote_plus(f'{query} after:{start} before:{end}')}"
           f"&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    return _parse_gn(fetch(url, timeout=20, ttl=86400, disk=True))[:limit]


def _parse_gn(body: bytes) -> list[dict]:
    root = ET.fromstring(body)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.find("source")
        pub = src.text.strip() if src is not None and src.text else ""
        if pub and title.endswith(f" - {pub}"):
            title = title[: -len(pub) - 3]
        t = it.findtext("pubDate")
        try:
            t = email.utils.parsedate_to_datetime(t).isoformat() if t else None
        except (TypeError, ValueError):
            t = None
        out.append({"title": title, "publisher": pub, "link": it.findtext("link"), "time": t, "summary": ""})
    out.sort(key=lambda x: x["time"] or "", reverse=True)
    return out


def news_query_for(sym: str) -> str:
    s = lookup(sym) if is_tw(sym) else None
    if s:
        return f"{s['name']} {s['code']}"
    i = market.info(sym) if not sym.startswith("^") else {}
    return i.get("shortName") or i.get("longName") or sym


# ================================================================ fake data
def _fake_securities() -> dict:
    base = [("2330", "台積電", "上市", "半導體業", "股票"), ("2317", "鴻海", "上市", "其他電子業", "股票"),
            ("2454", "聯發科", "上市", "半導體業", "股票"), ("2308", "台達電", "上市", "電子零組件業", "股票"),
            ("2382", "廣達", "上市", "電腦及週邊設備業", "股票"), ("2881", "富邦金", "上市", "金融保險業", "股票"),
            ("2882", "國泰金", "上市", "金融保險業", "股票"), ("2891", "中信金", "上市", "金融保險業", "股票"),
            ("2303", "聯電", "上市", "半導體業", "股票"), ("3711", "日月光投控", "上市", "半導體業", "股票"),
            ("2412", "中華電", "上市", "通信網路業", "股票"), ("6488", "環球晶", "上櫃", "半導體業", "股票"),
            ("5347", "世界", "上櫃", "半導體業", "股票"), ("0050", "元大台灣50", "上市", "", "ETF"),
            ("006208", "富邦台50", "上市", "", "ETF"), ("0056", "元大高股息", "上市", "", "ETF"),
            ("00878", "國泰永續高股息", "上市", "", "ETF"), ("00919", "群益台灣精選高息", "上市", "", "ETF"),
            ("00929", "復華台灣科技優息", "上市", "", "ETF"), ("00713", "元大台灣高息低波", "上市", "", "ETF"),
            ("00940", "元大台灣價值高息", "上市", "", "ETF"), ("00692", "富邦公司治理", "上市", "", "ETF"),
            ("00850", "元大臺灣ESG永續", "上市", "", "ETF"), ("00631L", "元大台灣50正2", "上市", "", "ETF"),
            ("00632R", "元大台灣50反1", "上市", "", "ETF"), ("00679B", "元大美債20年", "上櫃", "", "ETF"),
            ("00687B", "國泰20年美債", "上櫃", "", "ETF"), ("00937B", "群益ESG投等債20+", "上櫃", "", "ETF"),
            ("00981A", "主動統一台股增長", "上市", "", "ETF"), ("00635U", "期元大S&P黃金", "上市", "", "ETF")]
    return {c: {"code": c, "name": n, "isin": f"TW000{c}", "listed": "2003-06-30", "market": m, "industry": ind,
                "kind": k, "symbol": f"{c}.{'TW' if m == '上市' else 'TWO'}"} for c, n, m, ind, k in base}


def _fake_etf_nav() -> list[dict]:
    import numpy as np
    out = []
    today = dt.date.today().strftime("%Y%m%d")
    for s in _fake_securities().values():
        if s["kind"] != "ETF":
            continue
        rng = np.random.default_rng(market._seed(s["code"]) + dt.date.today().toordinal())
        prem = float(rng.normal(0.1, 0.6))
        try:
            nav = float(market.history(s["symbol"], "5d")["Close"].iloc[-1]) / (1 + prem / 100)
        except Exception:  # noqa: BLE001
            nav = float(rng.uniform(15, 200))
        units = float(rng.uniform(2e8, 2e10))
        out.append({"code": s["code"], "name": s["name"], "units": units, "units_chg": float(rng.normal(0, units * 0.002)),
                    "price": round(nav * (1 + prem / 100), 2), "est_nav": round(nav, 2), "premium": round(prem, 2),
                    "prior_nav": round(nav * 0.998, 2), "prior_nav_text": "", "date": today, "time": "13:30:00",
                    "kind": "1", "scale": units * nav})
    return out
