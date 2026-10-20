"""FT 市場背景：分析一檔標的前，把終端機裡「AI 引擎原本讀不到」的資料整理成一段文字。

送給 TradingAgents（每個代理人都看得到）、投資大師（每位大師）、FinGPT、Qlib／FinRL／TimesFM 的解讀，以及 Laya 的狀態。

時點正確（as_of）：
- 價格以外的每一段都只取分析基準日當天（含）以前的資料：事件依日期、總經序列取 ≤ 基準日的最後一筆、
  三大法人／融資券依日期、月營收依「可取得日」（次月 10 日）、新聞依發布時間。
- 只有「當下」才有的資料（今日產業強弱、SEC 最近申報）在回測（基準日早於昨天）時不放。
- 未來日程只放事先就公布的行事曆（Fed、央行、美國數據公布日）；中美談判、軍演等只放已經發生的。
"""
from __future__ import annotations

import datetime as dt
import logging
import math

import pandas as pd

from . import twmarket

log = logging.getLogger("ft.brief")
MAX_CHARS = 6000
PARTS = {"news": "中文新聞", "flows": "籌碼（三大法人、融資券）", "revenue": "月營收", "sector": "產業強弱",
         "tw_macro": "台灣總經", "us_macro": "美國總經", "events": "重大事件與日程", "sec": "SEC 最近申報"}


def _today() -> str:
    return dt.date.today().isoformat()


def is_live(as_of: str | None) -> bool:
    return not as_of or as_of >= (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _at(s: pd.Series, as_of: str | None):
    """(value, date) of the last observation on or before as_of."""
    if s is None or not len(s):
        return None, None
    if as_of:
        s = s[s.index <= pd.Timestamp(as_of)]
    s = s.dropna()
    if not len(s):
        return None, None
    return float(s.iloc[-1]), s.index[-1]


def _ago(s: pd.Series, t, days: int):
    p = s[s.index <= t - pd.Timedelta(days=days)].dropna()
    return float(p.iloc[-1]) if len(p) else None


# ------------------------------------------------------------------ sections
def news_section(sym: str, as_of: str | None, n: int = 10) -> str:
    q = twmarket.news_query_for(sym)
    if is_live(as_of):
        items = twmarket.news_zh(q, n, days=10)
    else:
        end = dt.date.fromisoformat(as_of)
        items = twmarket.news_between(q, (end - dt.timedelta(days=10)).isoformat(), end.isoformat(), n)
        items = [x for x in items if not x.get("time") or str(x["time"])[:10] <= as_of]
    if not items:
        return ""
    return "【中文新聞（近 10 天）】\n" + "\n".join(
        f"- {str(x.get('time') or '')[:10]} {x['title']}" + (f"（{x['publisher']}）" if x.get("publisher") else "") for x in items[:n])


def flows_section(code: str, as_of: str | None) -> str:
    from .research import store
    d = as_of or _today()
    with store.conn() as c:
        ins = c.execute("SELECT date, f_net, t_net, d_net FROM insti WHERE code=? AND date<=? ORDER BY date DESC LIMIT 20",
                        (code, d)).fetchall()
        mg = c.execute("SELECT date, margin, short FROM margin WHERE code=? AND date<=? ORDER BY date DESC LIMIT 6",
                       (code, d)).fetchall()
    lines = []
    if ins:
        lot = lambda v: f"{v / 1000:+,.0f}"  # noqa: E731  shares → 張
        f5, t5, d5 = (sum((r[i] or 0) for r in ins[:5]) for i in (1, 2, 3))
        f20, t20 = sum((r[1] or 0) for r in ins), sum((r[2] or 0) for r in ins)
        last = ins[0]
        streak = 0
        for r in ins:
            if (r[1] or 0) == 0 or (streak and (r[1] > 0) != (streak > 0)):
                break
            streak += 1 if r[1] > 0 else -1
        lines.append(f"- 三大法人（張）：{last[0]} 外資 {lot(last[1] or 0)}、投信 {lot(last[2] or 0)}、自營 {lot(last[3] or 0)}；"
                     f"近 5 日 外資 {lot(f5)}、投信 {lot(t5)}、自營 {lot(d5)}；近 20 日 外資 {lot(f20)}、投信 {lot(t20)}"
                     + (f"；外資連 {abs(streak)} 日{'買超' if streak > 0 else '賣超'}" if abs(streak) >= 2 else ""))
    if len(mg) >= 2:
        (d0, m0, s0), (_, m5, s5) = mg[0], mg[-1]
        ch = lambda a, b: f"{(a - b) / b * 100:+.1f}%" if a is not None and b else "—"  # noqa: E731
        lines.append(f"- 融資券（張）：{d0} 融資餘額 {m0:,.0f}（{len(mg) - 1} 日 {ch(m0, m5)}）、融券 {s0:,.0f}（{ch(s0, s5)}）")
    return ("【籌碼】\n" + "\n".join(lines)) if lines else ""


def revenue_section(code: str, as_of: str | None) -> str:
    from .research import store
    with store.conn() as c:
        rv = c.execute("SELECT period, revenue, mom, yoy, cum_yoy FROM revenue WHERE code=? AND avail<=? ORDER BY period DESC LIMIT 6",
                       (code, as_of or _today())).fetchall()
    if not rv:
        return ""
    f = lambda x: "—" if x is None else f"{x:+.1f}%"  # noqa: E731
    return "【月營收（已公布）】\n" + "\n".join(
        f"- {p}：{(r or 0) / 1e5:,.1f} 億，月增 {f(m)}、年增 {f(y)}、累計年增 {f(cy)}" for p, r, m, y, cy in rv)


def sector_section(sym: str) -> str:
    from . import sectors
    tw = twmarket.is_tw(sym)
    d = sectors.breakdown("tw" if tw else "us", top=100)
    for rank, s in enumerate(d["sectors"], 1):
        hit = next((x for x in s["stocks"] if x["symbol"] == sym), None)
        if hit or (tw and (twmarket.lookup(sym) or {}).get("industry") == s["name"]):
            ranked = sorted([x for x in d["sectors"] if x["pct"] is not None], key=lambda x: -x["pct"])
            pos = next((k for k, x in enumerate(ranked, 1) if x["name"] == s["name"]), None)
            day = "—" if s["pct"] is None else f"{s['pct']:+.2f}%"
            out = (f"【產業強弱（今日）】{'台股' if tw else '美股'}「{s['name']}」占市值 {s['weight']:.1f}%（第 {rank} 大），"
                   f"今日市值加權 {day}" + (f"，漲幅在 {len(ranked)} 個產業中排第 {pos}" if pos else ""))
            if hit:
                out += f"；本檔占該產業 {hit['weight_group']:.1f}%、占整體市場 {hit['weight_market']:.2f}%"
            return out
    return ""


TW_MACRO_IDS = ("SIGNAL", "LEADING", "GDP", "CPI", "CORE", "UNEMP", "DISCOUNT", "M1B", "M2")
US_MACRO_IDS = ("DFEDTARU", "DGS2", "DGS10", "T10Y2Y", "CPIAUCSL", "PCEPILFE", "UNRATE", "BAMLH0A0HYM2", "VIXCLS", "NETLIQ")


def tw_macro_section(as_of: str | None) -> str:
    from . import twmacro
    lines, vals = [], {}
    for sid in TW_MACRO_IDS:
        try:
            s = twmacro.raw(sid)
        except Exception:  # noqa: BLE001
            continue
        v, t = _at(s, as_of)
        if v is None:
            continue
        name, unit, *_ = twmacro.SERIES[sid]
        vals[sid] = v
        y1 = _ago(s[s.index <= t], t, 360)
        extra = f"，{twmacro.light(v)['name']}" if sid == "SIGNAL" and twmacro.light(v) else ""
        fmt = f"{v:.3f}" if sid == "DISCOUNT" else f"{v:.2f}"
        lines.append(f"- {name}：{fmt}{'%' if unit == '%' else ' ' + unit if unit else ''}{extra}"
                     f"（{twmacro._fmt_date(sid, t)}" + (f"；一年前 {y1:.2f}" if y1 is not None else "") + "）")  # noqa: SLF001
    if "M1B" in vals and "M2" in vals:
        gap = vals["M1B"] - vals["M2"]
        lines.append(f"- M1B − M2：{gap:+.2f} 百分點（{'黃金交叉，資金動能偏多' if gap > 0 else '死亡交叉，資金偏向定存'}）")
    return ("【台灣總經】\n" + "\n".join(lines)) if lines else ""


def us_macro_section(as_of: str | None) -> str:
    from . import fred
    lines = []
    for sid in US_MACRO_IDS:
        try:
            s = fred.transformed(sid)
        except Exception:  # noqa: BLE001
            continue
        v, t = _at(s, as_of)
        if v is None:
            continue
        name, unit, *_ = fred.SERIES[sid]
        if sid == "NETLIQ":
            prev = _ago(s[s.index <= t], t, 91)
            lines.append(f"- {name}：{v:,.0f} 十億美元（{t:%Y-%m-%d}" + (f"；13 週 {v - prev:+,.0f}" if prev is not None else "") + "）")
            continue
        y1 = _ago(s[s.index <= t], t, 365)
        lines.append(f"- {name}：{v:.2f}{'%' if unit == '%' else ' ' + unit if unit else ''}（{t:%Y-%m-%d}"
                     + (f"；一年前 {y1:.2f}" if y1 is not None else "") + "）")
    return ("【美國總經（FRED）】\n" + "\n".join(lines)) if lines else ""


SCHEDULED = ("fomc", "cbc", "macro")  # calendars published in advance → safe to show "upcoming" even in a backtest


def events_section(as_of: str | None, back: int = 30, ahead: int = 21) -> str:
    from . import events
    d = dt.date.fromisoformat(as_of) if as_of else dt.date.today()
    rows = events.list_events((d - dt.timedelta(days=back)).isoformat(), (d + dt.timedelta(days=ahead)).isoformat(),
                              ["move", "history", "fomc", "cbc", "macro", "uscn", "drill", "manual"], world_too=False)
    past = [e for e in rows if e["date"] <= d.isoformat()]
    fut = [e for e in rows if e["date"] > d.isoformat() and e["kind"] in SCHEDULED]
    if not is_live(as_of):  # summaries written later from news may describe the aftermath → titles only
        past_lines = [f"- {e['date']} [{e['kind_name']}] {e['title_final']}" for e in sorted(past, key=lambda e: e["date"])]
    else:
        past_lines = [f"- {e['date']} [{e['kind_name']}] {e['title_final']}" + (f"：{e['summary_final'][:120]}" if e["summary_final"] else "")
                      for e in sorted(past, key=lambda e: e["date"])]
    out = []
    if past_lines:
        out.append(f"【近 {back} 天重大事件】\n" + "\n".join(past_lines[-15:]))
    if fut:
        out.append(f"【接下來 {ahead} 天日程】\n" + "\n".join(
            f"- {e['date']}（{(dt.date.fromisoformat(e['date']) - d).days} 天後）{e['title_final']}" for e in sorted(fut, key=lambda e: e["date"])))
    return "\n".join(out)


def sec_section(sym: str) -> str:
    from . import edgar
    if not edgar.enabled():
        return ""
    return edgar.filings_text(sym, 6)


# ------------------------------------------------------------------ assemble
def build(sym: str, as_of: str | None = None, *, market_parts: bool = True, max_chars: int = MAX_CHARS) -> dict:
    """→ {"text": 中文段落, "parts": [已帶入的段落名], "skipped": {段落: 原因}, "as_of": 日期}."""
    if as_of in ("", "today"):
        as_of = None
    live = is_live(as_of)
    tw = twmarket.is_tw(sym)
    code = twmarket.code_of(sym) if tw else None
    is_stock = not sym.startswith("^") and "=" not in sym and not sym.endswith("-USD")
    plan = []
    if tw and is_stock:
        plan += [("news", lambda: news_section(sym, as_of)), ("flows", lambda: flows_section(code, as_of)),
                 ("revenue", lambda: revenue_section(code, as_of))]
    if live and is_stock:
        plan.append(("sector", lambda: sector_section(sym)))
    if market_parts:
        if tw:
            plan.append(("tw_macro", lambda: tw_macro_section(as_of)))
        plan += [("us_macro", lambda: us_macro_section(as_of)), ("events", lambda: events_section(as_of))]
    if not tw and is_stock and live:
        plan.append(("sec", lambda: sec_section(sym)))
    texts, parts, skipped = [], [], {}
    for key, fn in plan:
        try:
            t = fn()
        except Exception as e:  # noqa: BLE001
            skipped[key] = f"{type(e).__name__}: {str(e)[:80]}"
            continue
        if t:
            texts.append(t.strip())
            parts.append(key)
        else:
            skipped[key] = "沒有資料"
    if not live:
        skipped.setdefault("sector", "回測日不放當日產業強弱")
    head = (f"【FT 市場背景】標的 {sym}，資料截至 {as_of or _today()}（只含當時已公布的資訊）。"
            "以下是終端機另外整理的台灣／美國市場資料，請一併納入判斷，並在報告中引用用到的數字；資料沒有的不要編造。")
    body = "\n\n".join(texts)
    if len(body) > max_chars:
        body = body[:max_chars].rsplit("\n", 1)[0] + "\n…（已截斷）"
    return {"text": (head + "\n\n" + body) if texts else "", "parts": parts, "skipped": skipped, "as_of": as_of or _today()}


def build_many(syms: list[str], as_of: str | None = None, per_chars: int = 2500) -> dict:
    """For multi-ticker engines: market-level text once + a short per-ticker text."""
    mk = build_market(as_of, any(twmarket.is_tw(s) for s in syms))
    per, parts = {}, set(mk["parts"])
    for s in syms[:8]:
        b = build(s, as_of, market_parts=False, max_chars=per_chars)
        if b["text"]:
            per[s] = b["text"]
            parts |= set(b["parts"])
    return {"text": mk["text"], "by_symbol": per, "parts": sorted(parts), "as_of": mk["as_of"]}


def build_market(as_of: str | None = None, tw: bool = True) -> dict:
    if as_of in ("", "today"):
        as_of = None
    texts, parts = [], []
    for key, fn in ((("tw_macro", lambda: tw_macro_section(as_of)),) if tw else ()) + (
            ("us_macro", lambda: us_macro_section(as_of)), ("events", lambda: events_section(as_of))):
        try:
            t = fn()
        except Exception:  # noqa: BLE001
            continue
        if t:
            texts.append(t.strip())
            parts.append(key)
    head = f"【FT 市場背景】資料截至 {as_of or _today()}（只含當時已公布的資訊）。請一併納入判斷；資料沒有的不要編造。"
    return {"text": (head + "\n\n" + "\n\n".join(texts)) if texts else "", "parts": parts, "as_of": as_of or _today()}


def describe(parts: list[str]) -> str:
    return "、".join(PARTS.get(p, p) for p in parts) or "（沒有可用的資料）"


# ------------------------------------------------------------------ English facts for Laya
KIND_EN = {"fomc": "FOMC rate decision", "cbc": "Taiwan central bank policy meeting"}


def facts_en(sym: str, as_of: str | None = None) -> dict[str, str]:
    """Short English lines for Laya's state (it only reads English)."""
    if as_of in ("", "today"):
        as_of = None
    out: dict[str, str] = {}
    tw = twmarket.is_tw(sym)
    code = twmarket.code_of(sym) if tw else None
    if tw and code:
        try:
            from .research import store
            with store.conn() as c:
                ins = c.execute("SELECT f_net, t_net FROM insti WHERE code=? AND date<=? ORDER BY date DESC LIMIT 5",
                                (code, as_of or _today())).fetchall()
            if ins:
                f5, t5 = sum((r[0] or 0) for r in ins) / 1000, sum((r[1] or 0) for r in ins) / 1000
                out["institutional_flows"] = (f"last {len(ins)} sessions: foreign investors net {'bought' if f5 >= 0 else 'sold'} "
                                              f"{abs(f5):,.0f} lots, investment trusts net {'bought' if t5 >= 0 else 'sold'} {abs(t5):,.0f} lots")
            with store.conn() as c:
                rv = c.execute("SELECT period, yoy FROM revenue WHERE code=? AND avail<=? ORDER BY period DESC LIMIT 1",
                               (code, as_of or _today())).fetchone()
            if rv and rv[1] is not None:
                out["monthly_revenue"] = f"{rv[0]} revenue {rv[1]:+.1f}% year over year"
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import twmacro
            v, t = _at(twmacro.raw("SIGNAL"), as_of)
            cpi, _ = _at(twmacro.raw("CPI"), as_of)
            if v is not None:
                light = {"紅燈": "red (overheating)", "黃紅燈": "yellow-red", "綠燈": "green (stable)", "黃藍燈": "yellow-blue",
                         "藍燈": "blue (contraction)"}.get((twmacro.light(v) or {}).get("name"), "")
                out["taiwan_macro"] = f"business cycle signal {v:.0f}/45 {light}" + (f", CPI {cpi:+.1f}% y/y" if cpi is not None else "")
        except Exception:  # noqa: BLE001
            pass
    try:
        from . import fred
        up, _ = _at(fred.transformed("DFEDTARU"), as_of)
        t10, _ = _at(fred.transformed("DGS10"), as_of)
        vix, _ = _at(fred.transformed("VIXCLS"), as_of)
        bits = [f"Fed funds upper bound {up:.2f}%" if up is not None else "", f"US 10y yield {t10:.2f}%" if t10 is not None else "",
                f"VIX {vix:.1f}" if vix is not None else ""]
        if any(bits):
            out["us_macro"] = ", ".join(b for b in bits if b)
    except Exception:  # noqa: BLE001
        pass
    if is_live(as_of):
        try:
            from . import sectors
            d = sectors.breakdown("tw" if tw else "us", top=100)
            for s in d["sectors"]:
                if any(x["symbol"] == sym for x in s["stocks"]) or (tw and (twmarket.lookup(sym) or {}).get("industry") == s["name"]):
                    if s["pct"] is not None:
                        out["sector_today"] = f"its sector is {s['weight']:.0f}% of the market and moved {s['pct']:+.2f}% today"
                    break
        except Exception:  # noqa: BLE001
            pass
    try:
        from . import events
        d = dt.date.fromisoformat(as_of) if as_of else dt.date.today()
        rows = events.list_events(d.isoformat(), (d + dt.timedelta(days=21)).isoformat(), list(SCHEDULED), world_too=False)
        up = []
        for e in sorted(rows, key=lambda e: e["date"]):
            if e["date"] <= d.isoformat():
                continue
            name = KIND_EN.get(e["kind"]) or ("US CPI release" if "CPI" in e["title_final"] else "US jobs report" if "非農" in e["title_final"]
                                               else "US PCE inflation release" if "PCE" in e["title_final"] else "")
            if name:
                up.append(f"{name} in {(dt.date.fromisoformat(e['date']) - d).days} days")
        if up:
            out["upcoming_events"] = "; ".join(up[:4])
    except Exception:  # noqa: BLE001
        pass
    return {k: v for k, v in out.items() if v and not (isinstance(v, float) and math.isnan(v))}
