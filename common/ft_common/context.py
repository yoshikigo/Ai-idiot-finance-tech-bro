"""Plain-text (Traditional Chinese) market summaries shared by the ASK chat, the agent API
(Hermes / Open WebUI) and the AI engines. Every function returns compact text an LLM can cite.
"""
from __future__ import annotations

import datetime as dt
import re

from . import funds, market, twmarket

FUND_PREFIX = "FUND:"


def _n(x, d=2):
    if x is None:
        return "—"
    try:
        return f"{x:,.{d}f}"
    except (TypeError, ValueError):
        return str(x)


def _pct(x):
    return "—" if x is None else f"{x:+.2f}%"


def resolve(term: str) -> str | None:
    """'台積電' / '2330' / '0050' / 'AAPL' / '2330 TT' / 'FUND:…' → symbol."""
    t = term.strip()
    if not t:
        return None
    if t.upper().startswith(FUND_PREFIX):
        return FUND_PREFIX + t[len(FUND_PREFIX):]
    from .engine import normalize_symbol
    if re.fullmatch(r"[A-Za-z0-9.\-=^ ]+", t):
        sym = normalize_symbol(t)
        code = twmarket.code_of(sym)
        if sym.endswith(".TW") and (s := twmarket.lookup(code)):
            return s["symbol"]  # fix .TW → .TWO for OTC codes
        if re.fullmatch(r"\d{4,6}[A-Z]?", t.upper()) and (s := twmarket.lookup(t.upper())):
            return s["symbol"]
        return sym
    hits = twmarket.search(t, 1)
    if hits:
        return hits[0]["symbol"]
    return None


INDEX_NAMES = {"^TWII": "加權指數", "^TWOII": "櫃買指數", "^GSPC": "S&P 500", "^DJI": "道瓊", "^IXIC": "Nasdaq",
               "^SOX": "費城半導體", "^VIX": "VIX", "^N225": "日經 225", "^HSI": "恆生", "^KS11": "KOSPI",
               "TWD=X": "美元/台幣", "JPY=X": "美元/日圓", "GC=F": "黃金", "CL=F": "WTI 原油", "^TNX": "美 10 年殖利率",
               "BTC-USD": "比特幣", "ETH-USD": "以太幣"}


def quote_lines(symbols: list[str]) -> list[str]:
    out = []
    for q in market.quotes(symbols):
        sym = q["symbol"]
        name = twmarket.zh_name(sym) or q.get("name") or INDEX_NAMES.get(sym) or ""
        if twmarket.is_tw(sym) and q.get("lots") is None and q.get("volume") is not None:
            q["lots"] = q["volume"] / 1000
        if q.get("error") or q.get("last") is None:
            out.append(f"{sym} {name}：查無報價".replace(" ：", "："))
            continue
        vol = f"量 {q['lots']:,.0f} 張" if q.get("lots") is not None and not sym.startswith("^") else (
            f"量 {market.fmt_big(q.get('volume'))}" if q.get("volume") else "")
        hl = f"高 {_n(q.get('high'))} 低 {_n(q.get('low'))}" if q.get("high") else ""
        t = (q.get("time") or "")[:19].replace("T", " ")
        ind = "（試算價）" if q.get("indicative") else ""
        label = f"{sym} {name}" if name else sym
        out.append(f"{label}：{_n(q['last'])}{ind} {q.get('chg', 0):+,.2f}（{_pct(q.get('pct'))}）{hl} {vol} 時間 {t}".strip())
    return out


def news_lines(sym: str, n: int = 8) -> list[str]:
    if twmarket.is_tw(sym):
        items = twmarket.news_zh(twmarket.news_query_for(sym), n)
    else:
        items = market.news(sym, n)
    return [f"- {(x.get('time') or '')[:10]} {x['title']}（{x.get('publisher') or ''}）" for x in items[:n]]


def etf_text(code: str, holdings: bool = True) -> str:
    code = twmarket.code_of(code.upper())
    sym = twmarket.tw_symbol(code)
    lines = [f"【ETF {code} {twmarket.zh_name(sym) or ''}】"]
    lines += quote_lines([sym])
    nav = twmarket.etf_nav_one(code)
    if nav:
        prior = _n(nav["prior_nav"], 4) if nav["prior_nav"] is not None else (nav["prior_nav_text"] or "—")
        scale = f"{nav['scale'] / 1e8:,.1f} 億元" if nav.get("scale") else "—"
        lines.append(f"預估淨值 {_n(nav['est_nav'], 4)}｜折溢價 {_pct(nav['premium'])}｜前日淨值 {prior}｜"
                     f"規模約 {scale}｜受益單位 {_n(nav['units'], 0)}（較前日 {nav['units_chg'] or 0:+,.0f}）｜"
                     f"資料 {nav['date']} {nav['time']}")
        if nav["premium"] is not None and abs(nav["premium"]) >= 1:
            lines.append(f"⚠️ {'溢價' if nav['premium'] > 0 else '折價'}超過 1%，市價偏離淨值")
    info = twmarket.etf_info(code)
    keys = ["經理公司", "基金名稱", "基金成立日期", "分類", "追蹤指數", "配息", "經理人", "保管機構"]
    kv = [f"{k}：{info[k]}" for k in keys if info.get(k)]
    if kv:
        lines.append("｜".join(kv))
    q = (market.quotes([sym]) or [{}])[0]
    div = twmarket.dividend_summary(sym, q.get("last"))
    if div["items"]:
        recent = "、".join(f"{d['date']} {d['amount']:g}" for d in div["items"][:6])
        lines.append(f"配息：{div['frequency']}，近一年合計 {div['ttm']:g} 元，"
                     f"殖利率約 {_n(div['yield_ttm'])}%｜近期：{recent}")
    if holdings:
        h = twmarket.etf_holdings(code)
        if h.get("items"):
            top = "、".join(f"{x['name']} {_n(x['pct'])}%" for x in h["items"][:10])
            lines.append(f"成分股（{h['scope']}，{h['period'][:4]}-{h['period'][4:]}，合計 {h['total_pct']}%）：{top}")
        elif h.get("error"):
            lines.append(f"成分股：{h['error']}")
    return "\n".join(lines)


def etf_rank_text(sort: str = "scale", category: str = "", limit: int = 15) -> str:
    t = twmarket.etf_table(sort, category, limit=limit)
    label = {"scale": "規模", "premium": "溢價", "discount": "折價", "creation": "淨申購（單位數增加）",
             "redemption": "淨買回（單位數減少）"}.get(sort, sort)
    out = [f"【台股 ETF {label}排行{'・' + category if category else ''}】資料日期 {t['date']}（共 {t['count']} 檔）"]
    for i, r in enumerate(t["rows"], 1):
        scale = f"{r['scale'] / 1e8:,.0f}億" if r.get("scale") else "—"
        out.append(f"{i}. {r['code']} {r['name']}｜市價 {_n(r['price'])}｜預估淨值 {_n(r['est_nav'])}｜"
                   f"折溢價 {_pct(r['premium'])}｜規模 {scale}｜單位數 {r['units_chg'] or 0:+,.0f}｜{r['category']}")
    return "\n".join(out)


def fund_text(fid: str) -> str:
    f = funds.get(fid)
    if not f:
        return f"查無基金 {fid}"
    h = funds.history(fid)
    r = funds.returns(h["points"])
    lines = [f"【基金】{f['name']}（{f['region']}，{f['company'] or '—'}，{f['currency'] or '—'}）",
             f"代碼 {f['code'] or '—'}｜ISIN/受益憑證 {f['isin'] or '—'}｜id {f['id']}",
             f"最新淨值 {_n(f['nav'], 4)}（{f['date']}，日變動 {_pct(f['pct'])}）"]
    if r:
        lines.append("報酬：" + "｜".join(f"{k} {v:+.2f}%" for k, v in r.items()))
    if h["points"]:
        lines.append(f"淨值歷史：{len(h['points'])} 筆，{h['points'][0][0]} ~ {h['points'][-1][0]}（{h['source']}）")
        vals = [p[1] for p in h["points"]]
        hi, lo = max(vals), min(vals)
        lines.append(f"區間最高 {_n(hi, 4)}、最低 {_n(lo, 4)}，目前距高點 {(vals[-1] / hi - 1) * 100:+.2f}%")
    return "\n".join(lines)


def fund_search_text(q: str, region: str = "", limit: int = 10) -> str:
    rows = funds.search(q, region, limit)
    if not rows:
        st = funds.status()
        return f"找不到符合「{q}」的基金（資料庫共 {st['funds']} 檔，最後更新 {st['refreshed']}）"
    out = [f"【基金搜尋：{q}】{len(rows)} 筆"]
    for r in rows:
        out.append(f"- {r['name']}｜{r['region']}｜{r['company'] or '—'}｜淨值 {_n(r['nav'], 4)} {r['currency'] or ''}"
                   f"（{r['date']}，{_pct(r['pct'])}）｜id={r['id']}")
    return "\n".join(out)


def stock_text(sym: str) -> str:
    lines = quote_lines([sym])
    if twmarket.is_tw(sym):
        p = twmarket.company_profile(sym)
        keep = ["公司名稱", "產業別", "董事長", "總經理", "成立日期", "上市日期", "上櫃日期", "實收資本額"]
        kv = [f"{k}：{p[k]}" for k in keep if p.get(k)]
        if kv:
            lines.append("｜".join(kv))
    try:
        i = market.info(sym)
        kv = []
        for k, label, f in (("marketCap", "市值", market.fmt_big), ("trailingPE", "本益比", _n), ("priceToBook", "股價淨值比", _n),
                            ("dividendYield", "殖利率%", _n), ("grossMargins", "毛利率", lambda v: f"{v * 100:.1f}%"),
                            ("profitMargins", "淨利率", lambda v: f"{v * 100:.1f}%"),
                            ("returnOnEquity", "ROE", lambda v: f"{v * 100:.1f}%"),
                            ("revenueGrowth", "營收成長", lambda v: f"{v * 100:.1f}%"),
                            ("fiftyTwoWeekHigh", "52週高", _n), ("fiftyTwoWeekLow", "52週低", _n),
                            ("targetMeanPrice", "分析師目標價", _n)):
            if i.get(k) is not None:
                kv.append(f"{label} {f(i[k])}")
        if kv:
            lines.append("｜".join(kv))
        if not twmarket.is_tw(sym) and i.get("longName"):
            lines.insert(0, f"【{i['longName']}】{i.get('sector') or ''} / {i.get('industry') or ''}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def security_context(sec: str, with_news: bool = True) -> str:
    """Everything useful about one security (stock / ETF / fund) for an LLM."""
    if sec.startswith(FUND_PREFIX):
        return fund_text(sec[len(FUND_PREFIX):])
    s = twmarket.lookup(sec) if twmarket.is_tw(sec) else None
    if s and s["kind"] == "ETF":
        txt = etf_text(s["code"])
    else:
        txt = stock_text(sec)
    if with_news:
        nl = news_lines(sec)
        if nl:
            txt += "\n近期新聞：\n" + "\n".join(nl)
    return txt


def market_overview_text() -> str:
    groups = [("台股", ["^TWII", "^TWOII", "0050.TW", "2330.TW"]), ("美股", ["^GSPC", "^IXIC", "^SOX", "^VIX"]),
              ("亞洲", ["^N225", "^HSI", "^KS11"]), ("匯率／商品", ["TWD=X", "JPY=X", "GC=F", "CL=F"]),
              ("利率", ["^TNX"]), ("加密", ["BTC-USD"])]
    names = {"^TWII": "加權指數", "^TWOII": "櫃買指數", "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^SOX": "費半",
             "^VIX": "VIX", "^N225": "日經", "^HSI": "恆生", "^KS11": "KOSPI", "TWD=X": "美元/台幣", "JPY=X": "美元/日圓",
             "GC=F": "黃金", "CL=F": "WTI 原油", "^TNX": "美 10 年殖利率", "BTC-USD": "比特幣", "0050.TW": "元大台灣50",
             "2330.TW": "台積電"}
    allq = {q["symbol"]: q for q in market.quotes([s for _, ss in groups for s in ss])}
    out = [f"【市場總覽】{dt.datetime.now():%Y-%m-%d %H:%M}"]
    for g, ss in groups:
        parts = []
        for s in ss:
            q = allq.get(s, {})
            if q.get("last") is not None:
                parts.append(f"{names.get(s, s)} {_n(q['last'])}（{_pct(q.get('pct'))}）")
        if parts:
            out.append(f"{g}：" + "，".join(parts))
    try:
        t = twmarket.etf_table("premium", limit=3)
        hot = [r for r in t["rows"] if r["premium"] is not None and r["premium"] >= 1]
        if hot:
            out.append("ETF 溢價警示：" + "、".join(f"{r['code']} {r['name']} {_pct(r['premium'])}" for r in hot))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(out)


def strip_md(text: str) -> str:
    """Markdown → plain text for LINE."""
    t = re.sub(r"```.*?```", "", text, flags=re.S)
    t = re.sub(r"^#{1,6}\s*(.+)$", r"【\1】", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.M)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 \2", t)
    t = re.sub(r"<details>.*?</details>", "", t, flags=re.S)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def mentioned_securities(text: str, limit: int = 3) -> list[str]:
    """Securities named in free text: Taiwan codes (2330, 00679B), Chinese names (台積電), $TICKERS."""
    found: list[str] = []
    for m in re.finditer(r"(?<![\w.])(\d{4,6}[A-Z]?)(?![\w])", text.upper()):
        s = twmarket.lookup(m.group(1))
        if s and s["symbol"] not in found:
            found.append(s["symbol"])
    names = sorted(((s["name"], s["symbol"]) for s in twmarket.securities().values() if len(s["name"]) >= 2),
                   key=lambda x: -len(x[0]))
    rest = text
    for name, sym in names:
        if name in rest:
            rest = rest.replace(name, " ")
            if sym not in found:
                found.append(sym)
        if len(found) >= limit:
            break
    for m in re.finditer(r"\$([A-Z]{1,5})\b", text):
        if m.group(1) not in found:
            found.append(m.group(1))
    return found[:limit]
