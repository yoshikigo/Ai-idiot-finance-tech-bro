"""今日焦點 — one page for "what moved today and why": indices, supply-chain themes ranked by today's move,
the biggest movers inside the map, 三大法人 net buying, tagged headlines and the world-signal digest."""
from __future__ import annotations

import datetime as dt
import random

from . import market, supplychain, twmarket
from .twhttp import fetch_json

INDEX_SYMS = ["^TWII", "^TWOII", "TSM", "^SOX", "^GSPC", "^IXIC", "^N225", "^KS11", "TWD=X"]
INDEX_NAMES = {"^TWII": "加權指數", "^TWOII": "櫃買指數", "TSM": "台積電 ADR", "^SOX": "費城半導體", "^GSPC": "S&P 500",
               "^IXIC": "Nasdaq", "^N225": "日經 225", "^KS11": "韓國綜合", "TWD=X": "美元／台幣"}
BFI_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
BFI_NAME = {"自營商(自行買賣)": "自營商（自行）", "自營商(避險)": "自營商（避險）", "投信": "投信",
            "外資及陸資(不含外資自營商)": "外資", "外資自營商": "外資自營商", "合計": "合計"}


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_bfi(d: dict) -> dict | None:
    """TWSE BFI82U (三大法人買賣金額統計表) → {'date', 'rows': [{name, buy, sell, net}] in 億元}."""
    if not d or d.get("stat") != "OK" or not d.get("data"):
        return None
    rows = []
    for r in d["data"]:
        if len(r) < 4:
            continue
        name = str(r[0]).strip()
        buy, sell, net = _num(r[1]), _num(r[2]), _num(r[3])
        if net is None:
            continue
        rows.append({"name": BFI_NAME.get(name, name), "buy": (buy or 0) / 1e8, "sell": (sell or 0) / 1e8, "net": net / 1e8})
    ds = str(d.get("date") or "")
    date = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}" if len(ds) == 8 else ds
    return {"date": date, "rows": rows} if rows else None


def institutional() -> dict | None:
    """Latest trading day's 三大法人 (tries today and up to 6 days back; published ~15:00)."""
    if market.FAKE:
        rng = random.Random(dt.date.today().toordinal())
        rows = [{"name": n, "buy": b, "sell": b - x, "net": x} for n, b, x in
                [("外資", 2600, rng.uniform(-400, 400)), ("投信", 420, rng.uniform(-80, 120)),
                 ("自營商（自行）", 80, rng.uniform(-30, 30)), ("自營商（避險）", 300, rng.uniform(-150, 150))]]
        rows.append({"name": "合計", "buy": sum(r["buy"] for r in rows), "sell": sum(r["sell"] for r in rows),
                     "net": sum(r["net"] for r in rows)})
        return {"date": dt.date.today().isoformat(), "rows": rows, "demo": True}

    def load():
        d0 = dt.date.today()
        for back in range(7):
            day = d0 - dt.timedelta(days=back)
            if day.weekday() >= 5:
                continue
            try:
                res = parse_bfi(fetch_json(BFI_URL, params={"type": "day", "dayDate": day.strftime("%Y%m%d"),
                                                           "response": "json"}, timeout=15, ttl=1800))
            except Exception:  # noqa: BLE001
                res = None
            if res:
                return res
        return None
    try:
        return market.cached("focus:bfi", 1800, load)
    except Exception:  # noqa: BLE001
        return None


def headlines(limit: int = 30) -> list[dict]:
    """Today's Taiwan market headlines (Google News zh-TW), each tagged with the supply-chain nodes it mentions."""
    def load():
        seen, out = set(), []
        for q in ("台股", "半導體 OR AI伺服器 OR 供應鏈"):
            try:
                items = twmarket.news_zh(q, 25, days=1)
            except Exception:  # noqa: BLE001
                items = []
            for it in items:
                t = (it.get("title") or "").strip()
                k = t.split(" - ")[0][:40]
                if not t or k in seen:
                    continue
                seen.add(k)
                out.append({**it, "nodes": supplychain.tag_text(t + " " + (it.get("summary") or ""))})
        out.sort(key=lambda x: x.get("time") or "", reverse=True)
        return out
    return market.cached("focus:news", 600, load)[:limit]


def build() -> dict:
    idx = market.quotes(INDEX_SYMS)
    for q in idx:
        q["name"] = INDEX_NAMES.get(q["symbol"], q.get("name"))
    st = supplychain.stats()
    nodes = [dict({k: v for k, v in n.items() if k != "companies"}) for n in st["nodes"].values()]
    ranked = sorted([n for n in nodes if n["pct"] is not None], key=lambda n: -n["pct"])
    movers = []
    seen = set()
    for n in st["nodes"].values():
        for c in n["companies"]:
            if c["symbol"] in seen or c.get("pct") is None:
                continue
            seen.add(c["symbol"])
            movers.append({**c, "node": n["id"], "node_name": n["name"]})
    tw_movers = [m for m in movers if m["mkt"] == "TW"]
    tw_movers.sort(key=lambda m: -m["pct"])
    news = headlines()
    for n in news:
        n["node_names"] = [supplychain.NODES[k]["name"] for k in n["nodes"]]
    hot: dict[str, int] = {}
    for n in news:
        for k in n["nodes"]:
            hot[k] = hot.get(k, 0) + 1
    return {"indices": idx, "themes": ranked, "gainers": tw_movers[:10], "losers": tw_movers[::-1][:10],
            "institutional": institutional(), "news": news,
            "news_themes": sorted(({"id": k, "name": supplychain.NODES[k]["name"], "count": v} for k, v in hot.items()),
                                  key=lambda x: -x["count"])[:8],
            "at": st["at"]}


def ai_context(d: dict) -> str:
    """Plain-text digest of build() for the LLM."""
    L = ["## 主要指數"]
    L += [f"- {q.get('name') or q['symbol']}: {q['last']:,.2f} ({q['pct']:+.2f}%)" for q in d["indices"]
          if q.get("pct") is not None and q.get("last") is not None]
    L.append("\n## 供應鏈題材今日漲跌（成分股等權平均）")
    L += [f"- {t['name']}: {t['pct']:+.2f}%（漲 {t['up']}／跌 {t['down']}）" for t in d["themes"]]
    L.append("\n## 台股強勢股")
    L += [f"- {m['code']} {m['name']}（{m['node_name']}）{m['pct']:+.2f}%" for m in d["gainers"][:8]]
    L.append("\n## 台股弱勢股")
    L += [f"- {m['code']} {m['name']}（{m['node_name']}）{m['pct']:+.2f}%" for m in d["losers"][:8]]
    inst = d.get("institutional")
    if inst:
        L.append(f"\n## 三大法人（{inst['date']}，億元）")
        L += [f"- {r['name']}: 買超 {r['net']:+.1f}" for r in inst["rows"]]
    L.append("\n## 今日新聞標題")
    L += [f"- {n['title']}" + (f"〔{'、'.join(n['node_names'])}〕" if n.get("node_names") else "") for n in d["news"][:25]]
    return "\n".join(L)
