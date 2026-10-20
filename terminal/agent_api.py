"""/api/agent/* — text-first API for Hermes (LINE / Signal) and Open WebUI tools.

Every endpoint returns Traditional-Chinese plain text by default (format=json for data);
`plain=1` strips Markdown for LINE. Inputs accept Chinese names ("台積電", "元大高股息"),
codes ("2330", "00679B"), Yahoo symbols ("AAPL", "^TWII") and fund ids ("FUND:D:12345678").
"""
from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ft_common import context, dca, edgar, fomc, toolkit, events, focus, fred, funds, fx, ledger, notes, supplychain, twmarket, world

router = APIRouter(prefix="/api/agent")
_hooks: dict = {}  # set by app.py: settings loader, llm block, engines map


def init(load_settings, llm_block, engines):
    _hooks.update(load_settings=load_settings, llm_block=llm_block, engines=engines)


def _out(text: str, plain: bool = False, data=None, fmt: str = "text"):
    if fmt == "json":
        return JSONResponse({"text": text, "data": data})
    return PlainTextResponse(context.strip_md(text) if plain else text)


def _terms(q: str) -> list[str]:
    return [t.strip() for t in q.replace("，", ",").replace("、", ",").split(",") if t.strip()]


def _resolve_all(q: str) -> tuple[list[str], list[str]]:
    ok, bad = [], []
    for t in _terms(q):
        s = context.resolve(t)
        (ok if s else bad).append(s or t)
    return ok, bad


@router.get("/help")
def help_():
    return PlainTextResponse(HELP)


@router.get("/quote")
def quote(q: str, plain: bool = False, format: str = "text"):  # noqa: A002
    syms, bad = _resolve_all(q)
    lines = context.quote_lines([s for s in syms if not s.startswith(context.FUND_PREFIX)])
    lines += [context.fund_text(s[len(context.FUND_PREFIX):]) for s in syms if s.startswith(context.FUND_PREFIX)]
    if bad:
        lines.append("找不到：" + "、".join(bad))
    return _out("\n".join(lines) or "沒有結果", plain, {"symbols": syms}, format)


@router.get("/stock")
def stock(q: str, news: bool = True, plain: bool = False, format: str = "text"):  # noqa: A002
    sym = context.resolve(q)
    if not sym:
        raise HTTPException(404, f"找不到「{q}」")
    return _out(context.security_context(sym, with_news=news), plain, {"symbol": sym}, format)


@router.get("/etf")
def etf(code: str, holdings: bool = True, plain: bool = False, format: str = "text"):  # noqa: A002
    sym = context.resolve(code)
    c = twmarket.code_of(sym) if sym else code
    return _out(context.etf_text(c, holdings), plain, {"code": c}, format)


@router.get("/etf/rank")
def etf_rank(sort: str = "scale", category: str = "", limit: int = 15, plain: bool = False, format: str = "text"):  # noqa: A002
    return _out(context.etf_rank_text(sort, category, max(1, min(limit, 50))), plain, None, format)


@router.get("/fund/search")
def fund_search(q: str, region: str = "", limit: int = 10, plain: bool = False, format: str = "text"):  # noqa: A002
    return _out(context.fund_search_text(q, region, max(1, min(limit, 30))), plain,
                funds.search(q, region, limit), format)


@router.get("/fund")
def fund(id: str = "", q: str = "", plain: bool = False, format: str = "text"):  # noqa: A002
    fid = id.removeprefix(context.FUND_PREFIX)
    if not fid and q:
        hits = funds.search(q, limit=1)
        if not hits:
            return _out(context.fund_search_text(q), plain, None, format)
        fid = hits[0]["id"]
    if not fid:
        raise HTTPException(400, "需要 id 或 q")
    return _out(context.fund_text(fid), plain, {"id": fid}, format)


@router.get("/market")
def market_(plain: bool = False, format: str = "text"):  # noqa: A002
    return _out(context.market_overview_text(), plain, None, format)


@router.get("/news")
def news(q: str, n: int = 10, plain: bool = False, format: str = "text"):  # noqa: A002
    sym = context.resolve(q)
    if sym and not sym.startswith(context.FUND_PREFIX):
        lines = context.news_lines(sym, n)
        head = f"【{sym} {twmarket.zh_name(sym) or ''} 新聞】"
    else:
        items = twmarket.news_zh(q, n)
        lines = [f"- {(x.get('time') or '')[:10]} {x['title']}（{x.get('publisher') or ''}）" for x in items]
        head = f"【{q} 新聞】"
    return _out(head + "\n" + ("\n".join(lines) or "沒有新聞"), plain, None, format)


@router.get("/world")
def world_(q: str = "", region: str = "", plain: bool = False, format: str = "text"):  # noqa: A002
    """God's Eye View real-world signals: for a security (q) or region ids (comma list), else everything."""
    if region:
        rs = [world_alias(r) for r in region.split(",") if r.strip()]
    elif q:
        sym = context.resolve(q)
        rs = world.regions_for(sym) if sym else [world_alias(q)]
    else:
        rs = list(world.REGIONS)
    rs = [r for r in rs if r in world.REGIONS] or ["global"]
    return _out(world.digest(rs, max_chars=6000), plain, {"regions": rs, "events": world.events(rs, days=14)}, format)


WORLD_ALIAS = {"台灣": "taiwan", "台海": "taiwan", "日本": "japan", "南海": "south_china_sea", "荷莫茲": "hormuz",
               "紅海": "red_sea", "蘇伊士": "red_sea", "麻六甲": "malacca", "巴拿馬": "panama", "墨西哥灣": "gulf_mexico",
               "美國西岸": "us_west", "加州": "us_west", "全球": "global"}


def world_alias(x: str) -> str:
    x = x.strip()
    return WORLD_ALIAS.get(x, x.lower())


@router.get("/review")
def review_(plain: bool = False, format: str = "text"):  # noqa: A002
    """Scorecard of past AI forecasts vs what prices did."""
    rv = notes.review()
    lines = ["【AI 預測回顧】"]
    for eng, s in sorted(rv["stats"].items()):
        hit = f"{s['hits']}/{s['scored']} 命中" if s["scored"] else "尚未計分"
        band = f"，TimesFM 落在 80% 區間 {s['band_in']}/{s['band_n']}" if s["band_n"] else ""
        lines.append(f"{notes.ENGINE_LABEL.get(eng, eng)}：{s['n']} 筆，{hit}{band}")
    for r in rv["rows"][:15]:
        ok = "" if "hit" not in r else ("✅" if r["hit"] else "❌")
        lines.append(f"{r['date']} {notes.ENGINE_LABEL.get(r['engine'], r['engine'])} {r['ticker']} {r.get('name') or ''} "
                     f"{str(r.get('signal') or '')[:12]}｜{r['price_at']:,.2f} → {r['price_now']:,.2f}（{r['ret_pct']:+.2f}%）{ok} {r['status']}")
    if not rv["rows"]:
        lines.append("還沒有可計分的預測。")
    return _out("\n".join(lines), plain, rv, format)


@router.get("/watchlist")
def watchlist(plain: bool = False, format: str = "text"):  # noqa: A002
    wl = _hooks["load_settings"]().get("watchlist") or []
    return _out("【自選清單】\n" + "\n".join(context.quote_lines(wl)), plain, {"watchlist": wl}, format)


@router.get("/brief")
def brief(plain: bool = False, format: str = "text"):  # noqa: A002
    wl = _hooks["load_settings"]().get("watchlist") or []
    parts = [context.market_overview_text(), "", "【自選清單】", *context.quote_lines(wl)]
    try:
        parts += ["", context.etf_rank_text("premium", limit=5), "", context.etf_rank_text("creation", limit=5)]
    except Exception:  # noqa: BLE001
        pass
    try:
        evs = world.events(days=2)
        if evs:
            parts += ["", "【現實世界事件（God's Eye View，近 2 天）】"] + [f"- {e['date']} {e['title']}" for e in evs[:8]]
    except Exception:  # noqa: BLE001
        pass
    return _out("\n".join(parts), plain, None, format)


@router.get("/focus")
def focus_text(plain: bool = False, format: str = "text"):  # noqa: A002
    """今日焦點：指數、供應鏈題材漲跌、強弱勢股、三大法人、新聞（+ 已產生的 AI 今日重點）。不含任何人的持股。"""
    import datetime as _dt
    import json as _json
    import os as _os
    from pathlib import Path as _P
    d = focus.build()
    parts = ["【今日焦點】", focus.ai_context(d)]
    f = _P(_os.environ.get("FT_DATA", "/data")) / "focus" / f"ai-{_dt.date.today().isoformat()}.json"
    if f.exists():
        try:
            parts.insert(1, "【AI 今日重點】\n" + _json.loads(f.read_text())["text"] + "\n")
        except Exception:  # noqa: BLE001
            pass
    return _out("\n".join(parts), plain, None, format)


@router.get("/events")
def major_events(days: int = 365, plain: bool = False, format: str = "text"):  # noqa: A002
    """近一年市場重大事件（指數大波動、Fed 決策、自訂），含 AI 摘要。"""
    import datetime as _dt
    since = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    rows = events.list_events(since, None, ["move", "history", "fomc", "manual"], world_too=False)
    lines = []
    for e in rows:
        mv = "、".join(f"{m['name']} {m['pct']:+.1f}%" for m in e["moves"].values())
        tag = "（即將）" if e["upcoming"] else ""
        lines.append(f"{e['date']} {e['title_final']}{tag}" + (f"｜{mv}" if mv else "")
                     + (f"\n  {e['summary_final']}" if e["summary_final"] else ""))
    return _out("【市場重大事件】\n" + "\n".join(lines or ["（還沒有偵測到事件）"]), plain, rows, format)


@router.get("/ledger")
def ledger_text(plain: bool = False, format: str = "text"):  # noqa: A002
    """AI 分析紀錄：各 agent 事後核對的命中率、最近的檢討與提醒、最近 15 筆分析。"""
    return _out(ledger.text_report(), plain, ledger.stats(), format)


@router.get("/macro")
def macro_text(plain: bool = False, format: str = "text"):  # noqa: A002
    """美國總經（FRED）：利率、殖利率曲線、通膨、就業、景氣、金融情勢。"""
    return _out(fred.text(), plain, None, format)


@router.get("/twfx")
def twfx_text(q: str = "", plain: bool = False, format: str = "text"):  # noqa: A002
    """新台幣匯率：1 外幣 = 多少台幣，日／月／年變動。q 可指定幣別（例：USD,JPY,EUR）。"""
    codes = [c.strip().upper() for c in q.replace("，", ",").split(",") if c.strip().upper() in fx.CCY] or None
    return _out(fx.text(codes), plain, None, format)


@router.get("/kit/{kind}")
def kit_text(kind: str, q: str = "", expiry: str = "", market: str = "all", plain: bool = False, format: str = "text"):  # noqa: A002
    """分析工具箱：kind = tech（技術面：均線、RSI、KD、MACD、布林、一目均衡表、K 線型態、支撐壓力）、options（美股選擇權鏈、IV、
    Greeks、Put/Call、max pain）、research（分析師評等／目標價／內部人；台股三大法人與融資券）、earnings（EPS 驚喜與財報後股價反應；
    台股月營收與財報）、value（Graham 數、NCAV、Piotroski F-score、護城河指標）、intel（美股類股輪動、台美權值股強弱與市場廣度，
    market=all|tw|us，不需 q）。"""
    sym = "" if kind == "intel" else (q if q.upper().startswith("FUND:") else (context.resolve(q) or q.upper()))
    try:
        r = toolkit.run(kind, sym, expiry=expiry, market=market)
        return _out(r["text"], plain, None, format)
    except KeyError:
        return _out(f"沒有這個工具：{kind}（可用 tech／options／research／earnings／value／intel）", plain, None, format)
    except Exception as e:  # noqa: BLE001
        return _out(f"{sym or kind}：{e}", plain, None, format)


@router.get("/fomc")
def fomc_text(date: str = "", plain: bool = False, format: str = "text"):  # noqa: A002
    """FOMC 會議：聲明稿相對上次的措辭變化（逐句新增／刪除）、AI 解讀、經濟預測中位數（SEP，含前次比較）、會議紀要摘要。
    date＝會議決策日 YYYY-MM-DD，空白＝最近一次。"""
    try:
        return _out(fomc.text(date or None), plain, None, format)
    except KeyError:
        return _out(f"{date} 不是 FOMC 會議日", plain, None, format)


@router.get("/calendar")
def calendar_text(days: int = 21, plain: bool = False, format: str = "text"):  # noqa: A002
    """接下來的重要日程：Fed 利率決策、台灣央行理監事會、美國 CPI／非農／PCE 公布日（含台灣時間）。"""
    import datetime as _dt
    t = _dt.date.today()
    rows = events.list_events(t.isoformat(), (t + _dt.timedelta(days=max(1, min(days, 120)))).isoformat(),
                              ["fomc", "cbc", "macro"], world_too=False)
    lines = [f"【接下來 {days} 天重要日程】"] + [f"- {e['date']} {e['title_final']}" for e in sorted(rows, key=lambda e: e["date"])]
    return _out("\n".join(lines) if rows else "接下來沒有排定的重要日程", plain, None, format)


@router.get("/dca")
def dca_text(q: str, amount: float = 3000, freq: str = "monthly", days: str = "6", years: float = 5, start: str = "",
             fee: float = 0.1425, plain: bool = False, format: str = "text"):  # noqa: A002
    """定期定額回測：q＝標的（逗號分隔，最多 6 個，可用中文名或 FUND:代號），每期 amount 台幣、freq monthly/weekly/daily、
    days 扣款日（每月 6,16,26／每週 1–5）、回測 years 年（或 start=YYYY-MM-DD）、fee 手續費 %。對比同金額單筆投入。"""
    import datetime as _dt
    st = start or (_dt.date.today() - _dt.timedelta(days=int(365.25 * max(0.1, min(years, 30))))).isoformat()
    out = []
    for raw in [x.strip() for x in q.replace("，", ",").replace("、", ",").split(",") if x.strip()][:6]:
        sym = raw if raw.upper().startswith("FUND:") else (context.resolve(raw) or raw.upper())
        try:
            out.append(dca.simulate(sym, amount, freq, days, st, None, fee, 1, lots_out=0))
        except Exception as e:  # noqa: BLE001
            out.append({"symbol": sym, "error": str(e)[:160]})
    return _out(dca.text(out), plain, None, format)


@router.get("/sec")
def sec_text(q: str, plain: bool = False, format: str = "text"):  # noqa: A002
    """美股 SEC 官方資料：最近申報（10-K／10-Q／8-K／Form 4…）＋年度關鍵財報數字與申報日。"""
    sym = context.resolve(q) or q.upper()
    try:
        st = edgar.statements(sym, "annual", 4)
        lines = [edgar.filings_text(sym, 10)]
        if not st.get("error"):
            lines.append(f"\n【{st['name']} 年度財報（SEC XBRL）】")
            lines.append("期間：" + "、".join(f"{p['end']}（{p['form']}，{p['filed']} 申報）" for p in st["periods"]))
            for r in st["rows"]:
                vals = ["—" if v is None else (f"{v:,.2f}" if abs(v) < 1000 else f"{v / 1e9:,.2f}B") for v in r["values"]]
                lines.append(f"- {r['label']}：{' ｜ '.join(vals)}")
        return _out("\n".join(lines), plain, st, format)
    except edgar.NotConfigured as e:
        raise HTTPException(412, str(e)) from e


@router.get("/chain")
def chain(q: str = "", plain: bool = False, format: str = "text"):  # noqa: A002
    """供應鏈：q 空白＝各環節今日漲跌；q＝環節名稱或個股 → 該環節的上下游與成分股報價。"""
    if not q.strip():
        st = supplychain.stats()
        rows = sorted([n for n in st["nodes"].values() if n["pct"] is not None], key=lambda n: -n["pct"])
        lines = [f"{n['name']}（{supplychain.MKT_NAME.get(n['mkt'], n['mkt'])}）{n['pct']:+.2f}%  漲{n['up']}／跌{n['down']}"
                 + (f"｜台 {n['tw_pct']:+.2f}% vs 美 {n['us_pct']:+.2f}%" if n.get("tw_pct") is not None and n.get("us_pct") is not None else "")
                 for n in rows]
        return _out("【供應鏈題材今日漲跌】\n" + "\n".join(lines), plain, rows, format)
    hit = supplychain.find(q)
    if not hit:
        raise HTTPException(404, f"供應鏈地圖裡找不到「{q}」")
    d = supplychain.node_detail(hit["node"])
    st = supplychain.stats([hit["node"]])["nodes"][hit["node"]]
    lines = [f"【{d['name']}】{d['desc']}",
             "上游：" + ("、".join(x["name"] for x in d["upstream"]) or "—"),
             "下游：" + ("、".join(x["name"] for x in d["downstream"]) or "—"),
             f"成分股（平均 {st['pct']:+.2f}%）：" if st["pct"] is not None else "成分股："]
    for c in st["companies"]:
        px = f"{c['last']:,.2f} {c['pct']:+.2f}%" if c.get("last") is not None and c.get("pct") is not None else "—"
        lines.append(f"- {c['code'] if c['mkt'] == 'TW' else c['symbol']} {c['name']}（{c['role']}）{px}")
    if st.get("us_companies"):
        lines.append(f"美國同類（平均 {st['us_pct']:+.2f}%）：" if st.get("us_pct") is not None else "美國同類：")
        for c in st["us_companies"]:
            px = f"{c['last']:,.2f} {c['pct']:+.2f}%" if c.get("last") is not None and c.get("pct") is not None else "—"
            lines.append(f"- {c['symbol']} {c['name']}（{c['role']}）{px}")
    return _out("\n".join(lines), plain, {"node": d, "stats": st}, format)


@router.get("/forecast")
async def forecast(q: str, h: int = 30, model: str = "", plain: bool = False, format: str = "text"):  # noqa: A002
    """TimesFM trend forecast as text (fast path, no LLM)."""
    sym = await asyncio.to_thread(context.resolve, q)
    if not sym:
        raise HTTPException(404, f"找不到「{q}」")
    url = _hooks["engines"].get("timesfm")
    model = model or _hooks["load_settings"]().get("timesfm_model", "2.5")
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.get(f"{url}/forecast", params={"symbol": sym, "horizon": h, "model": model, "backtest": "true"})
    except httpx.HTTPError as e:
        raise HTTPException(503, f"TimesFM 引擎離線：{e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    d = r.json()
    sm, bt = d["summary"], d.get("backtest")
    f = lambda v: f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}"  # noqa: E731
    pts = d["forecast"]
    marks = sorted({min(len(pts), k) for k in (5, 10, 20, len(pts))})
    lines = [f"【TimesFM {model} 趨勢預測】{sym} {d['name']}",
             f"最新 {f(d['last'])}（{d['last_date']}），預測 {d['horizon']} 個交易日，用 {d['context']} 筆歷史",
             f"{d['horizon']} 日後中位數 {f(sm['median_end'])}（{sm['change_pct']:+.2f}%），80% 區間 {f(sm['p10_end'])} ~ {f(sm['p90_end'])}，收高於現價機率約 {sm['p_up'] * 100:.0f}%",
             "路徑：" + "｜".join(f"{pts[k - 1]['date']} {f(pts[k - 1]['median'])}（{f(pts[k - 1]['p10'])}~{f(pts[k - 1]['p90'])}）" for k in marks)]
    if bt:
        lines.append(f"回測（{bt['from']} 之後）：MAPE {bt['mape']:.2f}%（價格不變基準 {bt['naive_mape']:.2f}%），落在 80% 區間 {bt['coverage80']:.0f}%，方向{'正確' if bt['direction_hit'] else '錯誤'}")
    lines.append("註：TimesFM 只根據過去價格做統計預測，不含新聞與基本面；區間是不確定性，不是保證。")
    return _out("\n".join(lines), plain, d, format)


@router.get("/decide")
async def decide(q: str, h: int = 20, plain: bool = False, format: str = "text"):  # noqa: A002
    """Laya 交易判斷：買進／持有／觀望／賣出／放空 的百分比（ONNX，約 1 秒，不用 LLM）."""
    sym = await asyncio.to_thread(context.resolve, q)
    if not sym:
        raise HTTPException(404, f"找不到「{q}」")
    url = _hooks["engines"].get("laya")
    if not url:
        raise HTTPException(404, "沒有設定 laya 引擎")
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.get(f"{url}/decide", params={"symbol": sym, "horizon": h})
    except httpx.HTTPError as e:
        raise HTTPException(503, f"Laya 引擎離線：{e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text)
    d = r.json()
    a = d["action"]
    order = sorted(a["probabilities"].items(), key=lambda kv: -kv[1])
    o = d["outlook"]
    lines = [f"【Laya 交易判斷】{sym} {d.get('name') or ''}　基準 {d['as_of']}　未來 {d['horizon']} 個交易日",
             f"結論：{a['label']} {a['probabilities'][a['choice']] * 100:.0f}%　多空淨值 {d['net'] * 100:+.0f}%",
             "　".join(f"{a['labels'][k]} {v * 100:.0f}%" for k, v in order),
             f"收高機率 {d['p_up'] * 100:.0f}%　跌逾 {d['drop_threshold']:g}% 風險 {d['p_drop'] * 100:.0f}%",
             "區間：" + "　".join(f"{lab}{b} {v * 100:.0f}%" for lab, b, v in zip(o["labels"], o["bins"], o["probabilities"])),
             "註：Laya 是通用決策模型，未用金融資料訓練；百分比是模型對這份資料的信心，不是統計漲跌機率，請和其他引擎一起看。"]
    return _out("\n".join(lines), plain, d, format)


@router.get("/strategy")
async def strategy(q: str = "", backtest: bool = True, top: int = 15, plain: bool = False, format: str = "text"):  # noqa: A002
    """FinLab 式選股：q = 內建策略名稱／id，或一句中文選股想法（交給本機 AI 轉成條件）。回傳今天的選股名單＋回測摘要。"""
    url = _hooks["engines"].get("research")
    if not url:
        raise HTTPException(404, "沒有設定 research 引擎")
    try:
        async with httpx.AsyncClient(timeout=900) as c:
            cat = (await c.get(f"{url}/catalog")).json()
            if not q.strip():
                lines = ["【內建策略】（ft.py strategy <名稱>，或直接用中文描述想法）"]
                lines += [f"- {p['name']}（{p['id']}）：{p['description']}" for p in cat["presets"]]
                return _out("\n".join(lines), plain, cat["presets"], format)
            pre = next((p for p in cat["presets"] if q.strip() in (p["id"], p["name"]) or p["name"] in q), None)
            expl = ""
            if pre:
                spec = pre
            else:
                r = await c.post(f"{url}/ai", json={"prompt": q, "llm": _hooks["llm_block"](_hooks["load_settings"]())})
                if r.status_code >= 400:
                    raise HTTPException(r.status_code, r.json().get("detail", r.text))
                spec, expl = r.json()["spec"], r.json().get("explanation", "")
            r = await c.post(f"{url}/{'backtest' if backtest else 'screen'}", json={"spec": spec})
            if r.status_code >= 400:
                raise HTTPException(r.status_code, r.json().get("detail", r.text))
            d = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(503, f"策略研究引擎離線：{e}") from e
    s = d["spec"]
    lines = [f"【策略】{s['name']}"]
    if expl:
        lines.append(f"AI 轉換：{expl}")
    lines += ["條件：" + "；".join(s["conditions"]) + (f"；排名 {s['rank']}" if s["rank"] else "")]
    m = d.get("metrics")
    if m:
        lines.append(f"回測 {m['start']}～{m['end']}：年化 {m['cagr']:+.1f}%（0050 {m.get('bench_cagr') or 0:+.1f}%）、"
                     f"最大回撤 {m['mdd']:.1f}%、夏普 {m['sharpe']:.2f}、勝率 {m['win_rate'] or 0:.0f}%（{m['trades']} 筆）")
    pk = d.get("picks") or {}
    lines.append(f"{pk.get('date', '')} 選股（符合 {pk.get('n_match', 0)} 檔）：" +
                 ("、".join(f"{x['code']} {x['name']}" for x in pk.get("rows", [])[:top]) or "今天沒有符合的股票"))
    for w in (d.get("warnings") or [])[:2]:
        lines.append("註：" + w)
    lines.append("註：回測只含目前上市櫃公司（存活者偏差），歷史績效不代表未來。")
    return _out("\n".join(lines), plain, {k: d.get(k) for k in ("spec", "metrics", "picks", "yearly")}, format)


# ---------------------------------------------------------------- AI engines
ENGINE_ALIASES = {"tradingagents": "tradingagents", "ta": "tradingagents", "交易": "tradingagents",
                  "hedgefund": "hedgefund", "hf": "hedgefund", "大師": "hedgefund",
                  "qlib": "qlib", "量化": "qlib", "finrl": "finrl", "rl": "finrl", "強化學習": "finrl",
                  "fingpt": "fingpt", "fg": "fingpt", "預測": "fingpt", "情緒": "fingpt",
                  "timesfm": "timesfm", "tfm": "timesfm", "趨勢": "timesfm", "走勢預測": "timesfm",
                  "laya": "laya", "判斷": "laya", "買賣": "laya"}


@router.post("/analyze")
async def analyze(req: Request):
    """{engine, q, params?} → starts a job. q = ticker (TradingAgents/FinGPT) or tickers (HF/FinRL/Qlib custom)."""
    b = await req.json()
    eng = ENGINE_ALIASES.get(str(b.get("engine", "tradingagents")).lower(), str(b.get("engine")))
    url = _hooks["engines"].get(eng)
    if not url:
        raise HTTPException(400, f"未知引擎 {b.get('engine')}；可用：tradingagents, hedgefund, qlib, finrl, fingpt")
    params = dict(b.get("params") or {})
    q = b.get("q") or b.get("ticker") or b.get("tickers") or ""
    if q:
        syms, bad = await asyncio.to_thread(_resolve_all, str(q))
        if bad:
            raise HTTPException(404, "找不到：" + "、".join(bad))
        syms = [s for s in syms if not s.startswith(context.FUND_PREFIX)]
        if eng in ("tradingagents", "fingpt", "timesfm", "laya"):
            params.setdefault("ticker", syms[0])
        elif eng == "qlib":
            params.setdefault("universe", "custom")
            params.setdefault("tickers", ", ".join(syms))
        else:
            params.setdefault("tickers", ", ".join(syms))
    params.setdefault("language", _hooks["load_settings"]().get("language", "Traditional Chinese"))
    llm = _hooks["llm_block"](_hooks["load_settings"]())
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            schema = (await c.get(f"{url}/meta")).json().get("schema", [])
            params = coerce(params, schema)
            r = await c.post(f"{url}/jobs", json={"params": params, "llm": llm})
    except httpx.HTTPError as e:
        raise HTTPException(503, f"{eng} 引擎離線（systemctl --user status ft-{eng}）：{e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    j = r.json()
    return {"engine": eng, "id": j["id"], "status": j["status"], "params": params,
            "hint": f"GET /api/agent/job?engine={eng}&id={j['id']}&wait=600"}


def coerce(params: dict, schema: list[dict]) -> dict:
    """Agent callers send strings; match the engine's form schema (multi → list, int, bool)."""
    types = {f["name"]: f for f in schema}
    out = dict(params)
    for k, v in params.items():
        f = types.get(k)
        if not f:
            continue
        t = f.get("type")
        if t == "multi" and isinstance(v, str):
            out[k] = [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
        elif t == "int" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
            out[k] = int(v)
        elif t == "bool" and isinstance(v, str):
            out[k] = v.strip().lower() in ("1", "true", "yes", "on", "是")
    for f in schema:  # fill defaults the UI would have sent
        if f["name"] not in out and f.get("default") is not None:
            d = f["default"]
            out[f["name"]] = time.strftime("%Y-%m-%d") if d == "today" else d
    return out


@router.get("/job")
async def job(engine: str, id: str, wait: int = 0, plain: bool = False, full: bool = True, format: str = "text"):  # noqa: A002
    eng = ENGINE_ALIASES.get(engine.lower(), engine)
    url = _hooks["engines"].get(eng)
    if not url:
        raise HTTPException(400, "未知引擎")
    deadline = time.time() + max(0, min(wait, 1800))
    try:
        j = await _poll(url, id, deadline)
    except httpx.HTTPError as e:
        raise HTTPException(503, f"{eng} 引擎離線：{e}") from e
    if j is None:
        raise HTTPException(404, "查無此工作")
    return _render_job(eng, id, j, full, plain, format)


async def _poll(url: str, jid: str, deadline: float):
    async with httpx.AsyncClient(timeout=30) as c:
        while True:
            r = await c.get(f"{url}/jobs/{jid}")
            if r.status_code == 404:
                return None
            j = r.json()
            if j["status"] in ("done", "error", "cancelled") or time.time() >= deadline:
                return j
            await asyncio.sleep(5)


def _render_job(eng, id, j, full, plain, format):  # noqa: A002
    if j["status"] == "done":
        res = j.get("result") or {}
        text = res.get("markdown") or res.get("summary") or ""
        if not full:
            text = f"{res.get('summary') or ''}\n\n{(res.get('headline') or '')}"
        for t in (res.get("tables") or [])[:2]:
            text += f"\n\n{t['title']}\n" + "\n".join(" | ".join(str(c) for c in row)[:300] for row in t["rows"][:15])
        body = f"DONE {eng} {id}\n結論：{res.get('signal') or '—'}\n{text}"
    elif j["status"] in ("error", "cancelled"):
        body = f"{j['status'].upper()} {eng} {id}：{j.get('error')}"
    else:
        body = (f"PENDING {eng} {id} 進度 {j.get('progress', 0):.0f}%｜{j.get('stage') or '排隊中'}\n"
                f"稍後再查：/api/agent/job?engine={eng}&id={id}&wait=600")
    return _out(body, plain, {"status": j["status"], "id": id, "engine": eng}, format)


@router.get("/jobs")
async def jobs(engine: str = "", limit: int = 5, plain: bool = False, format: str = "text"):  # noqa: A002
    names = [ENGINE_ALIASES.get(engine.lower(), engine)] if engine else list(_hooks["engines"])
    lines = []
    async with httpx.AsyncClient(timeout=10) as c:
        for n in names:
            url = _hooks["engines"].get(n)
            if not url:
                continue
            try:
                js = (await c.get(f"{url}/jobs"))
                for j in js.json()[:limit]:
                    lines.append(f"{n} {j['id']} {j['status']} {j.get('title') or ''} {j.get('summary') or j.get('error') or j.get('stage') or ''}")
            except Exception as e:  # noqa: BLE001
                lines.append(f"{n} 離線：{e}")
    return _out("\n".join(lines) or "沒有工作紀錄", plain, None, format)


HELP = """FT Terminal agent API（全部 GET，除了 analyze）
/api/agent/quote?q=台積電,0050,AAPL,^TWII        報價（台股即時、可用中文名稱）
/api/agent/stock?q=2330                          個股 / ETF / 基金完整資訊＋新聞
/api/agent/etf?code=00878                         ETF 淨值、折溢價、規模、配息、成分股
/api/agent/etf/rank?sort=premium&category=債券    ETF 排行 sort=scale|premium|discount|creation|redemption
                                                  category=股票型|高股息|債券|主動式|槓桿/反向|期貨/商品
/api/agent/fund/search?q=安聯 台灣科技&region=境內 基金搜尋（region=境內|境外|期信）
/api/agent/fund?id=D:12345678  或 ?q=名稱          基金淨值、報酬、歷史區間
/api/agent/market                                 市場總覽
/api/agent/news?q=台積電                          中文新聞
/api/agent/forecast?q=0050&h=30                   TimesFM 趨勢預測（中位數、80% 區間、回測）
/api/agent/decide?q=2330&h=20                     Laya 交易判斷（買進／持有／觀望／賣出／放空 %）
/api/agent/strategy?q=營收成長動能  或 ?q=中文選股想法   FinLab 式選股＋回測（q 空白＝列出內建策略）
/api/agent/watchlist   /api/agent/brief           自選清單 / 盤後簡報
/api/agent/focus                                  今日焦點（供應鏈題材漲跌、強弱勢股、三大法人、新聞、AI 今日重點）
/api/agent/macro                                  美國總經（FRED）
/api/agent/sec?q=NVDA                             美股 SEC 最近申報＋年度財報（需 FT_SEC_USER_AGENT）
/api/agent/ledger                                 AI 分析紀錄：各 agent 5／20／60 日命中率、最近檢討與提醒
/api/agent/events?days=365                        近一年市場重大事件（指數大波動、Fed 決策、自訂）＋AI 摘要
/api/agent/chain   /api/agent/chain?q=CoWoS        供應鏈：各環節今日漲跌／某環節（或某檔股票所在環節）的上下游與成分股
/api/agent/world?q=2603  或 ?region=台灣,紅海      現實世界訊號（God's Eye View：地震、軍機、航運、氣旋）
/api/agent/review                                 AI 預測回顧（過去的預測準不準）
POST /api/agent/analyze {"engine":"tradingagents","q":"2330"}   啟動 AI 分析（tradingagents|hedgefund|qlib|finrl|fingpt|timesfm|laya）
/api/agent/job?engine=tradingagents&id=…&wait=600 等結果（PENDING / DONE）
/api/agent/jobs                                   最近的工作
共通參數：plain=1 去掉 Markdown（LINE 用）、format=json
"""
