"""Fin Terminal — terminal-style market workstation with local-Ollama AI engines.

Serves the SPA, market-data API (yfinance), AI settings, an ASK chat (streamed
from Ollama) and a reverse proxy to the five engine workers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from ft_common import brief, context, dca, edgar, toolkit, events, fomc, focus, fred, funds, fx, ledger, market, notes, ollama, sectors, supplychain, twmacro, twmarket, world

DATA = Path(os.environ.get("FT_DATA", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
SETTINGS = DATA / "settings.json"
STATIC = Path(__file__).parent / "static"

ENGINES = {}
for item in os.environ.get("FT_ENGINES", "tradingagents=http://127.0.0.1:8131,hedgefund=http://127.0.0.1:8132,"
                                          "qlib=http://127.0.0.1:8133,finrl=http://127.0.0.1:8134,"
                                          "fingpt=http://127.0.0.1:8135,timesfm=http://127.0.0.1:8136,"
                                          "kronos=http://127.0.0.1:8139,"
                                          "laya=http://127.0.0.1:8137,research=http://127.0.0.1:8138").split(","):
    if "=" in item:
        k, v = item.split("=", 1)
        ENGINES[k.strip()] = v.strip().rstrip("/")
ENGINES.setdefault("kronos", "http://127.0.0.1:8139")  # an older FT_ENGINES in the env file would not list it

DEFAULT_SETTINGS = {
    "ollama_url": ollama.DEFAULT_URL,
    "model": ollama.DEFAULT_MODEL,
    "models": {  # per-engine overrides; empty = use "model"
        "tradingagents_deep": "", "tradingagents_quick": "", "hedgefund": "", "qlib": "", "finrl": "",
        "fingpt": "", "timesfm": "", "kronos": "", "research": "", "ask": "",
    },
    "num_ctx": int(os.environ.get("FT_NUM_CTX", "0")) or None,
    "temperature": 0.2,
    "think": False,
    "language": "Traditional Chinese",
    "color_mode": "tw",
    "timesfm_model": "2.5",
    "kronos_model": "small",
    "obsidian_auto": True,
    "watchlist": ["^TWII", "2330.TW", "2317.TW", "2454.TW", "0050.TW", "0056.TW", "00878.TW", "00679B.TWO", "NVDA",
                  "AAPL", "TSM", "^GSPC", "^SOX", "TWD=X", "BTC-USD"],
    "launchpad": [{"sec": "2330.TW", "fn": "GP"}, {"sec": "", "fn": "TW"}, {"sec": "0056.TW", "fn": "ETF"},
                  {"sec": "", "fn": "W"}],
}

MONITORS = {
    "WEI": ("全球股市指數", ["^GSPC", "^DJI", "^IXIC", "^SOX", "^RUT", "^VIX", "^TWII", "^N225", "^HSI", "000001.SS",
                         "^KS11", "^STOXX50E", "^GDAXI", "^FTSE", "^FCHI", "^BSESN", "^AXJO"]),
    "TW": ("台股焦點", ["^TWII", "^TWOII", "0050.TW", "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "3711.TW",
                    "2881.TW", "2882.TW", "2891.TW", "2303.TW", "2412.TW", "3231.TW", "6669.TW", "2603.TW", "3661.TW"]),
    "TWETF": ("台股熱門 ETF", ["0050.TW", "006208.TW", "0056.TW", "00878.TW", "00919.TW", "00929.TW", "00713.TW",
                            "00940.TW", "00692.TW", "00850.TW", "00881.TW", "00631L.TW", "00632R.TW", "00679B.TWO",
                            "00687B.TWO", "00937B.TWO", "00720B.TWO", "00772B.TWO"]),
    "FX": ("外匯", ["TWD=X", "JPY=X", "EURUSD=X", "GBPUSD=X", "CNY=X", "HKD=X", "KRW=X", "AUDUSD=X", "DX-Y.NYB",
                  "EURTWD=X", "JPYTWD=X"]),
    "CMDTY": ("商品", ["CL=F", "BZ=F", "NG=F", "GC=F", "SI=F", "HG=F", "PL=F", "ZC=F", "ZW=F", "ZS=F"]),
    "CRYPTO": ("加密貨幣", ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "DOGE-USD", "ADA-USD"]),
    "RATES": ("利率 / 債券", ["^IRX", "^FVX", "^TNX", "^TYX", "TLT", "IEF", "SHY", "HYG", "LQD"]),
    "MAG7": ("美股七雄 + 半導體", ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "TSM", "AMD",
                            "ASML", "MU", "INTC", "QCOM"]),
}

NAMES = {"^TWOII": "櫃買指數", "^GSPC": "S&P 500", "^DJI": "道瓊工業", "^IXIC": "Nasdaq", "^SOX": "費城半導體", "^RUT": "羅素 2000",
         "^VIX": "VIX", "^TWII": "加權指數", "^N225": "日經 225", "^HSI": "恆生", "000001.SS": "上證",
         "^KS11": "KOSPI", "^STOXX50E": "Euro Stoxx 50", "^GDAXI": "DAX", "^FTSE": "FTSE 100", "^FCHI": "CAC 40",
         "^BSESN": "Sensex", "^AXJO": "ASX 200", "TWD=X": "USD/TWD", "JPY=X": "USD/JPY", "EURUSD=X": "EUR/USD",
         "GBPUSD=X": "GBP/USD", "CNY=X": "USD/CNY", "HKD=X": "USD/HKD", "KRW=X": "USD/KRW", "AUDUSD=X": "AUD/USD",
         "DX-Y.NYB": "美元指數", "EURTWD=X": "EUR/TWD", "JPYTWD=X": "JPY/TWD", "CL=F": "WTI 原油", "BZ=F": "布蘭特",
         "NG=F": "天然氣", "GC=F": "黃金", "SI=F": "白銀", "HG=F": "銅", "PL=F": "白金", "ZC=F": "玉米", "ZW=F": "小麥",
         "ZS=F": "黃豆", "^IRX": "美 13 週", "^FVX": "美 5 年", "^TNX": "美 10 年", "^TYX": "美 30 年",
         "0050.TW": "元大台灣50", "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", "2308.TW": "台達電",
         "2382.TW": "廣達", "3711.TW": "日月光投控", "2881.TW": "富邦金", "2882.TW": "國泰金", "2891.TW": "中信金",
         "2303.TW": "聯電", "2412.TW": "中華電", "3231.TW": "緯創", "6669.TW": "緯穎", "2603.TW": "長榮",
         "3661.TW": "世芯-KY", "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana"}
NAMES.update({k: v for k, v in sectors.US_ZH.items() if k not in NAMES})  # 美股中文名（監控表、自選）

from ft_common.engine import _safe_json_response  # noqa: E402

app = FastAPI(title="Fin Terminal", default_response_class=_safe_json_response())


# ------------------------------------------------------------------ settings
def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    if SETTINGS.exists():
        try:
            saved = json.loads(SETTINGS.read_text())
            for k, v in saved.items():
                if k == "models" and isinstance(v, dict):
                    s["models"].update(v)
                else:
                    s[k] = v
        except json.JSONDecodeError:
            pass
    return s


def llm_block(s: dict) -> dict:
    models = {k: v for k, v in (s.get("models") or {}).items() if v}
    b = {"url": s["ollama_url"], "model": s["model"], "models": models, "temperature": s.get("temperature", 0.2),
         "think": s.get("think", False)}
    if s.get("num_ctx"):
        b["num_ctx"] = int(s["num_ctx"])
    return b


@app.get("/api/settings")
def get_settings():
    return load_settings()


@app.put("/api/settings")
async def put_settings(req: Request):
    body = await req.json()
    s = load_settings()
    for k in ("ollama_url", "model", "num_ctx", "temperature", "think", "language", "color_mode", "timesfm_model", "kronos_model", "obsidian_auto", "obsidian_engines", "watchlist", "launchpad"):
        if k in body:
            s[k] = body[k]
    if isinstance(body.get("models"), dict):
        s["models"].update(body["models"])
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    return s


@app.get("/api/ollama/models")
def ollama_models():
    s = load_settings()
    try:
        return {"ok": True, "url": s["ollama_url"], "models": ollama.list_models(s["ollama_url"])}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "url": s["ollama_url"], "error": str(e), "models": []}


# ------------------------------------------------------------------ market data
def _err(e: Exception):
    raise HTTPException(502, f"資料來源錯誤：{type(e).__name__}: {str(e)[:200]}")


def _decorate(rows: list[dict]) -> list[dict]:
    for r in rows:
        sym = r["symbol"]
        if twmarket.is_tw(sym):
            r["name"] = twmarket.zh_name(sym) or r.get("name") or NAMES.get(sym)
            if r.get("lots") is None and r.get("volume") is not None:
                r["lots"] = r["volume"] / 1000
            s = twmarket.lookup(sym)
            r["kind"] = s["kind"] if s else None
        else:
            r["name"] = NAMES.get(sym) or r.get("name")
    return rows


@app.get("/api/quotes")
def api_quotes(s: str):
    syms = [x.strip() for x in s.split(",") if x.strip()][:120]
    return _decorate(market.quotes(syms))


@app.get("/api/monitor/{name}")
def api_monitor(name: str):
    name = name.upper()
    if name not in MONITORS:
        raise HTTPException(404, "unknown monitor")
    title, syms = MONITORS[name]
    return {"title": title, "rows": _decorate(market.quotes(syms))}


@app.get("/api/monitors")
def api_monitors():
    return {k: v[0] for k, v in MONITORS.items()}


@app.get("/api/history")
def api_history(s: str, range: str = "1y", interval: str = "1d"):  # noqa: A002
    try:
        df = market.history(s, range, interval)
    except Exception as e:  # noqa: BLE001
        _err(e)
    if df is None or len(df) == 0:
        return []
    out = []
    intraday = interval.endswith("m") or interval.endswith("h")
    for ts, r in df.iterrows():
        t = pd.Timestamp(ts)
        c = r.Close
        if pd.isna(c):  # Yahoo sometimes returns today's bar half-filled (NaN) — skip it rather than break the chart
            continue
        o, h, lo = (c if pd.isna(x) else x for x in (r.Open, r.High, r.Low))
        out.append({"time": int(t.timestamp()) if intraday else t.strftime("%Y-%m-%d"),
                    "open": round(float(o), 4), "high": round(float(max(h, o, c)), 4), "low": round(float(min(lo, o, c)), 4),
                    "close": round(float(c), 4), "volume": 0.0 if pd.isna(r.Volume) else float(r.Volume)})
    return out


@app.get("/api/des")
def api_des(s: str):
    try:
        i = market.info(s)
    except Exception as e:  # noqa: BLE001
        i = {"_error": str(e)[:200]}
    i = dict(i)
    i.setdefault("symbol", s)
    i["zhName"] = twmarket.zh_name(s) or NAMES.get(s)
    if twmarket.is_tw(s):
        i["twProfile"] = twmarket.company_profile(s)
        sec = twmarket.lookup(s)
        i["twKind"] = sec["kind"] if sec else None
    return i


@app.get("/api/news")
def api_news(s: str, n: int = 25, lang: str = "auto"):
    try:
        if lang == "zh" or (lang == "auto" and twmarket.is_tw(s)):
            return twmarket.news_zh(twmarket.news_query_for(s), n)
        return market.news(s, n)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/fa")
def api_fa(s: str, freq: str = "yearly"):
    try:
        return market.statements(s, freq)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/search")
def api_search(q: str, funds_too: bool = True):
    out = []
    try:
        out += twmarket.search(q, 10)
    except Exception:  # noqa: BLE001
        pass
    if not any("\u4e00" <= ch <= "\u9fff" for ch in q):
        try:
            seen = {x["symbol"] for x in out}
            out += [x for x in market.search(q) if x.get("symbol") not in seen]
        except Exception:  # noqa: BLE001
            pass
    if funds_too and len(q.strip()) >= 2:
        try:
            out += [{"symbol": context.FUND_PREFIX + f["id"], "name": f["name"], "exchange": f["region"],
                     "type": "基金"} for f in funds.search(q, limit=5)]
        except Exception:  # noqa: BLE001
            pass
    return out[:20]


@app.get("/api/resolve")
def api_resolve(q: str):
    sym = context.resolve(q)
    out = {"symbol": sym}
    if sym and twmarket.is_tw(sym):
        s = twmarket.lookup(sym)
        if s:
            out.update(kind=s["kind"], name=s["name"])
    elif sym and sym.startswith(context.FUND_PREFIX):
        f = funds.get(sym[len(context.FUND_PREFIX):])
        if f:
            out.update(kind="基金", name=f["name"])
    return out


# ------------------------------------------------------------------ Taiwan ETFs
@app.get("/api/tw/etfs")
def api_etfs(sort: str = "scale", category: str = "", q: str = "", limit: int = 400):
    return twmarket.etf_table(sort, category, q, limit)


@app.get("/api/tw/etf")
def api_etf(code: str):
    code = twmarket.code_of(code.upper())
    sym = twmarket.tw_symbol(code)
    quote = (_decorate(market.quotes([sym])) or [{}])[0]
    return {"code": code, "symbol": sym, "name": twmarket.zh_name(sym), "quote": quote,
            "nav": twmarket.etf_nav_one(code), "info": twmarket.etf_info(code),
            "category": twmarket.etf_category(code, twmarket.zh_name(sym) or ""),
            "dividends": twmarket.dividend_summary(sym, quote.get("last"))}


@app.get("/api/tw/etf/holdings")
def api_etf_holdings(code: str, quarterly: bool = False):
    return twmarket.etf_holdings(code, quarterly)


# ------------------------------------------------------------------ funds
@app.get("/api/funds/search")
def api_fund_search(q: str = "", region: str = "", limit: int = 60):
    if not q.strip():
        return []
    rows = funds.search(q, region, limit)
    funds.prefetch([r["id"] for r in rows if (r.get("n_navs") or 0) < 60][:20])  # history arrives in the background
    return rows


@app.get("/api/funds/status")
def api_fund_status():
    return funds.status()


@app.post("/api/funds/refresh")
def api_fund_refresh():
    return funds.refresh(force=True)


@app.get("/api/funds/detail")
def api_fund(id: str):  # noqa: A002
    f = funds.get(id)
    if not f:
        raise HTTPException(404, "查無此基金")
    h = funds.history(id)
    return {**f, "isin": f.get("isin") or h.get("isin"), "history": h["points"], "source": h["source"],
            "returns": funds.returns(h["points"])}


# ------------------------------------------------------------------ TimesFM forecast overlay
@app.get("/api/forecast")
async def api_forecast(s: str, h: int = 30, model: str = "", context: int = 1024, backtest: bool = False):
    url = ENGINES.get("timesfm")
    if not url:
        raise HTTPException(404, "沒有設定 timesfm 引擎")
    model = model or load_settings().get("timesfm_model", "2.5")
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.get(f"{url}/forecast", params={"symbol": s, "horizon": h, "model": model, "context": context,
                                                       "backtest": str(backtest).lower()})
    except httpx.HTTPError as e:
        raise HTTPException(503, f"TimesFM 引擎離線（systemctl --user status ft-timesfm）：{e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text)
    return r.json()


# ------------------------------------------------------------------ Kronos K-line forecast overlay
@app.get("/api/kronos")
async def api_kronos(s: str, h: int = 20, model: str = "", samples: int = 16, backtest: bool = False):
    url = ENGINES.get("kronos")
    if not url:
        raise HTTPException(404, "沒有設定 kronos 引擎")
    model = model or load_settings().get("kronos_model", "small")
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.get(f"{url}/forecast", params={"symbol": s, "horizon": h, "model": model, "samples": samples,
                                                       "backtest": str(backtest).lower()})
    except httpx.HTTPError as e:
        raise HTTPException(503, f"Kronos 引擎離線（systemctl --user status ft-kronos）：{e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text)
    return r.json()


# ------------------------------------------------------------------ Laya decision (買進／賣出／放空… %)
@app.get("/api/decide")
async def api_decide(s: str, h: int = 20, date: str = "", agents: bool = True, timesfm: bool = True, world: bool = True,
                     news: bool = True, ft: bool = True):
    url = ENGINES.get("laya")
    if not url:
        raise HTTPException(404, "沒有設定 laya 引擎")
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.get(f"{url}/decide", params={"symbol": s, "horizon": h, "date": date, "agents": str(agents).lower(),
                                                     "timesfm": str(timesfm).lower(), "world": str(world).lower(),
                                                     "news": str(news).lower(),
                                                     "ft": str(ft).lower()})
    except httpx.HTTPError as e:
        raise HTTPException(503, f"Laya 引擎離線（systemctl --user status ft-laya）：{e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text)
    return r.json()


# ------------------------------------------------------------------ 策略研究 (FinLab-style research engine)
@app.api_route("/api/research/{path:path}", methods=["GET", "POST"])
async def api_research(path: str, req: Request):
    url = ENGINES.get("research")
    if not url:
        raise HTTPException(404, "沒有設定 research 引擎")
    if path not in ("catalog", "series", "backtest", "screen", "ai", "check", "collector"):
        raise HTTPException(404, "unknown research endpoint")
    try:
        async with httpx.AsyncClient(timeout=900) as c:
            if req.method == "GET":
                r = await c.get(f"{url}/{path}", params=dict(req.query_params))
            else:
                body = await req.json()
                if path == "ai":
                    body["llm"] = llm_block(load_settings())
                r = await c.post(f"{url}/{path}", json=body)
    except httpx.HTTPError as e:
        raise HTTPException(503, f"策略研究引擎離線（systemctl --user status ft-research）：{e}") from e
    if "json" not in r.headers.get("content-type", ""):
        raise HTTPException(r.status_code, r.text[:300])
    return JSONResponse(r.json(), status_code=r.status_code)


# ------------------------------------------------------------------ translate (Ollama, cached)
TRANSLATIONS = DATA / "translations.json"


@app.post("/api/translate")
async def api_translate(req: Request):
    import hashlib
    body = await req.json()
    text = (body.get("text") or "").strip()[:6000]
    if not text:
        return {"text": ""}
    key = hashlib.sha1(text.encode()).hexdigest()
    cache = {}
    if TRANSLATIONS.exists():
        try:
            cache = json.loads(TRANSLATIONS.read_text())
        except json.JSONDecodeError:
            cache = {}
    if key in cache:
        return {"text": cache[key], "cached": True}
    llm = ollama.resolve(llm_block(load_settings()), "ask")
    from starlette.concurrency import run_in_threadpool
    out = await run_in_threadpool(ollama.ask, llm, "你是專業財經翻譯。把使用者給的內容翻成通順的繁體中文（台灣用語），"
                                  "公司名、產品名保留原文並在第一次出現時加中文。只輸出譯文。", text)
    cache[key] = out
    if len(cache) > 3000:
        cache = dict(list(cache.items())[-2000:])
    TRANSLATIONS.write_text(json.dumps(cache, ensure_ascii=False))
    return {"text": out}


# ------------------------------------------------------------------ ASK (streaming chat)
SECTOR_WORDS = ("產業分布", "產業別", "產業占比", "產業比重", "類股", "族群占比", "各產業", "哪個產業", "權重最大")
USCN_WORDS = ("中美", "美中", "川習", "習川", "貿易戰", "關稅休戰", "休戰", "和談", "貿易協議", "何立峰", "貝森特", "APEC")
DRILL_WORDS = ("軍演", "演習", "聯演", "利劍", "RIMPAC", "環太平洋", "與那國", "台海", "解放軍", "美軍")
TW_MACRO_WORDS = ("台灣總經", "景氣燈號", "景氣對策", "對策信號", "藍燈", "紅燈", "黃紅燈", "黃藍燈", "重貼現率", "央行",
                  "M1B", "資金動能", "國發會", "主計", "台灣經濟", "台灣通膨", "台灣 GDP", "台灣GDP", "拆款")
TW_MACRO_HINTS = ("總經", "經濟", "景氣", "通膨", "CPI", "物價", "失業", "GDP", "成長率", "利率", "升息", "降息", "M2", "資金")
WORLD_WORDS = ("世界", "地緣", "地震", "戰", "軍", "航運", "船", "油價", "颱風", "颶風", "海峽", "事件", "風險", "紅海",
               "荷莫茲", "南海", "供應鏈", "天災", "衝突")

@app.post("/api/ask")
async def api_ask(req: Request):
    body = await req.json()
    s = load_settings()
    llm = ollama.resolve(llm_block(s), "ask")
    sec = body.get("security")
    from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
    last_q = next((m.get("content", "") for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")
    mentioned = await run_in_threadpool(context.mentioned_securities, last_q)
    targets = mentioned or ([sec] if sec else [])
    parts = []
    for t in targets[:3]:
        try:
            parts.append(await run_in_threadpool(context.security_context, t, len(targets) == 1))
        except Exception as e:  # noqa: BLE001
            parts.append(f"(無法取得 {t} 市場資料：{e})")
    if not mentioned and any(k in last_q for k in ("大盤", "盤勢", "台股", "美股", "市場", "加權", "行情", "總經")):
        parts.insert(0, await run_in_threadpool(context.market_overview_text))
    try:  # real-world signals (God's Eye View)
        wregs = list(dict.fromkeys(r for t in targets[:3] for r in world.regions_for(t))) or list(world.REGIONS)
        if any(k in last_q for k in WORLD_WORDS):
            parts.append(await run_in_threadpool(world.digest, wregs))
        elif targets and world.events(wregs, days=7):
            parts.append(await run_in_threadpool(world.compact, wregs))
    except Exception:  # noqa: BLE001
        pass
    if any(k in last_q for k in ("總經", "經濟", "通膨", "CPI", "PCE", "利率", "殖利率", "降息", "升息", "Fed", "聯準會", "失業", "非農",
                                 "衰退", "GDP", "美債", "倒掛", "美元")):
        try:
            parts.append(await run_in_threadpool(fred.text))
        except Exception:  # noqa: BLE001
            pass
    for t in targets[:2]:  # US stocks: latest SEC filings (8-K items say what happened)
        if t and not twmarket.is_tw(t) and not t.startswith(("^", "FUND:")) and edgar.enabled():
            try:
                parts.append(await run_in_threadpool(edgar.filings_text, t, 8))
            except Exception:  # noqa: BLE001
                pass
    for tt in targets[:2]:  # 台股：籌碼與月營收（終端機自己的資料庫），問個股時一律帶上
        if tt and twmarket.is_tw(tt) and not tt.startswith("^"):
            code = twmarket.code_of(tt)
            for fn in (brief.flows_section, brief.revenue_section):
                try:
                    txt = await run_in_threadpool(fn, code, None)
                    if txt:
                        parts.append(f"{txt}（{tt}）")
                except Exception:  # noqa: BLE001
                    pass
    if any(k in last_q for k in ("技術", "均線", "KD", "RSI", "MACD", "布林", "支撐", "壓力", "型態", "一目")):
        for tt in targets[:2]:
            try:
                parts.append("【技術面（本機計算）】\n" + (await run_in_threadpool(toolkit.technical, tt))["text"])
            except Exception:  # noqa: BLE001
                pass
    if any(k in last_q for k in ("匯率", "台幣", "新台幣", "美元", "日圓", "日幣", "人民幣", "歐元", "韓元", "換匯", "升值", "貶值")):
        try:
            parts.append(await run_in_threadpool(fx.text, None))
        except Exception:  # noqa: BLE001
            pass
    if any(k in last_q for k in SECTOR_WORDS):
        try:
            mk = "tw" if any(k in last_q for k in ("台股", "台灣", "上市", "上櫃")) else "us"
            parts.append(await run_in_threadpool(sectors.text, mk, 3))
        except Exception:  # noqa: BLE001
            pass
    geo_k = tuple(k for k, words in (("uscn", USCN_WORDS), ("drill", DRILL_WORDS)) if any(w in last_q for w in words))
    if geo_k:
        try:
            parts.append(await run_in_threadpool(events.geo_text, geo_k))
        except Exception:  # noqa: BLE001
            pass
    if any(k in last_q for k in TW_MACRO_WORDS) or (any(k in last_q for k in ("台灣", "台股", "國內", "我國"))
                                                    and any(k in last_q for k in TW_MACRO_HINTS)):
        try:
            parts.append(await run_in_threadpool(twmacro.text, None))
        except Exception:  # noqa: BLE001
            pass
    if any(k in last_q for k in ("流動性", "資金面", "淨流動性", "逆回購", "TGA", "縮表", "QT", "QE", "資產負債表", "熱錢")):
        try:
            parts.append(await run_in_threadpool(fred.liquidity_text))
        except Exception:  # noqa: BLE001
            pass
    if any(k in last_q for k in ("Fed", "FOMC", "聯準會", "鮑爾", "點陣圖", "會議紀要", "利率決策")):
        try:
            parts.append(await run_in_threadpool(fomc.text, None))
        except Exception:  # noqa: BLE001
            pass
    if any(k in last_q for k in ("大跌", "大漲", "暴跌", "崩盤", "重挫", "大事", "重大事件", "為什麼", "Fed", "聯準會", "降息", "升息")):
        try:  # the year's market-moving days (detected + FOMC + your own), so "四月為什麼大跌" has something to stand on
            import datetime as _dt
            evs = events.list_events((_dt.date.today() - _dt.timedelta(days=400)).isoformat(), None,
                                     ["move", "fomc", "manual"], world_too=False)[:40]
            evs += events.list_events("2015-01-01", None, ["history"], world_too=False)
            if evs:
                parts.append("【近一年市場重大事件（偵測自指數大波動、Fed 行事曆與使用者標註；摘要由 AI 依當時新聞標題撰寫）】\n" + "\n".join(
                    f"- {e['date']} {e['title_final']}" + ("（即將）" if e["upcoming"] else "")
                    + ("｜" + "、".join(f"{m['name']} {m['pct']:+.1f}%" for m in e["moves"].values()) if e["moves"] else "")
                    + (f"：{e['summary_final'][:160]}" if e["summary_final"] else "") for e in evs))
        except Exception:  # noqa: BLE001
            pass
    if targets and targets[0] and not targets[0].startswith(context.FUND_PREFIX):
        try:  # 基本背景一律帶上（不必等問題裡出現「總經」「匯率」這些字）；已經因關鍵字帶入的段落不重複
            joined = "\n".join(parts)
            have = {"news", "news_us", "flows", "revenue", "sec"}
            for key, mark in (("us_macro", "【美國總經"), ("tw_macro", "【台灣總經"), ("fx", "【新台幣匯率"), ("fomc", "【FOMC"),
                              ("sector", "產業分布】"), ("events", "重大事件"), ("world", "世界"), ("etf", "ETF")):
                if mark in joined:
                    have.add(key)
            b = await run_in_threadpool(lambda: brief.build(targets[0], None, max_chars=3500, exclude=frozenset(have)))
            if b["text"]:
                parts.append(b["text"])
        except Exception:  # noqa: BLE001
            pass
    ctx_text = "\n\n".join(parts)
    system = ("你是專業金融終端機裡的市場分析師。回答精確、簡潔，引用下方提供的數字；資料缺漏要明講，絕不編造價格或淨值。"
              "台股漲跌以紅漲綠跌的習慣描述即可，金額單位用元、億元。不構成投資建議。"
              + ollama.LANG_HINT.get(s.get("language"), "") + "\n\n" + ctx_text)
    msgs = [{"role": "system", "content": system}] + [m for m in body.get("messages", []) if m.get("role") in ("user", "assistant")][-12:]

    def gen():
        try:
            for piece in ollama.chat_stream(llm, msgs):
                yield f"data: {json.dumps({'t': piece}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: {\"done\": true}\n\n"

    return StreamingResponse(iterate_in_threadpool(gen()), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ engines proxy
@app.get("/api/engines")
async def engines():
    out = []
    async with httpx.AsyncClient(timeout=3) as c:
        for name, url in ENGINES.items():
            try:
                r = await c.get(f"{url}/meta")
                meta = r.json()
                out.append({**meta, "name": name, "online": True})
            except Exception as e:  # noqa: BLE001
                out.append({"name": name, "title": name, "online": False, "error": str(e)[:120]})
    return out


def _eng(name: str) -> str:
    if name not in ENGINES:
        raise HTTPException(404, "unknown engine")
    return ENGINES[name]


CTX_ENGINES = ("tradingagents", "hedgefund", "fingpt", "qlib", "finrl", "timesfm", "kronos")
# what each engine already reads by itself → not repeated in its FT 市場背景
CTX_EXCLUDE = {"tradingagents": frozenset({"world", "news_us"}), "fingpt": frozenset({"news", "news_us"}),
               "timesfm": frozenset({"world"}), "qlib": frozenset({"news", "news_us", "world"}),
               "finrl": frozenset({"news", "news_us", "world"})}


def _job_symbols(name: str, p: dict) -> list[str]:
    raw = p.get("ticker") if name in ("tradingagents", "fingpt", "timesfm", "kronos") else (
        p.get("tickers") if name != "qlib" or p.get("universe") == "custom" else "")
    out = []
    for t in str(raw or "").replace("，", ",").replace(" ", ",").split(","):
        t = t.strip()
        if t:
            out.append(context.resolve(t) or t)
    return [s for s in out if not s.startswith(context.FUND_PREFIX)]


def enrich_job(name: str, body: dict) -> dict:
    """Everything an engine job gets besides its own params: Ollama settings, 檢討回饋 (lessons) and the
    FT 市場背景 (brief.py) — same for the web UI, Hermes and Open WebUI."""
    s = load_settings()
    body["llm"] = llm_block(s)
    if name in ledger.LLM_ENGINES and (s.get("ledger_feedback") or {}).get(name, True):
        try:  # 檢討回饋：the agent's latest lessons ride along in the llm block
            les = ledger.lessons_text(name)
            if les:
                body["llm"]["lessons"] = les
        except Exception:  # noqa: BLE001
            pass
    p = body.get("params") or {}
    if name in CTX_ENGINES and str(p.get("ft_context", True)).lower() not in ("false", "0", "no", "off"):
        date = p.get("date") if p.get("date") not in (None, "", "today") else None
        ex = CTX_EXCLUDE.get(name, frozenset())
        try:
            syms = _job_symbols(name, p)
            if name in ("tradingagents", "fingpt", "timesfm", "kronos") and syms:
                b = brief.build(syms[0], date, exclude=ex, engine=name)
                body["llm"]["context"] = b["text"]
            elif syms:
                b = brief.build_many(syms, date, per_chars=1500 if name in ("qlib", "finrl") else 2500, exclude=ex, engine=name)
                body["llm"].update(context=b["text"], context_by=b["by_symbol"])
            else:
                b = brief.build_market(date, True, ex)
                body["llm"]["context"] = b["text"]
            body["llm"]["context_parts"] = brief.describe(b["parts"])
        except Exception as e:  # noqa: BLE001  never block an analysis on the extras
            print("FT context failed:", name, e)
    return body


@app.api_route("/api/engines/{name}/jobs", methods=["GET", "POST"])
async def eng_jobs(name: str, req: Request):
    url = _eng(name)
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            if req.method == "GET":
                r = await c.get(f"{url}/jobs")
            else:
                body = await run_in_threadpool(enrich_job, name, await req.json())
                r = await c.post(f"{url}/jobs", json=body)
    except httpx.HTTPError as e:
        raise HTTPException(503, f"引擎 {name} 離線（systemctl --user status ft-{name}）") from e
    return JSONResponse(r.json(), status_code=r.status_code)


@app.api_route("/api/engines/{name}/jobs/{jid}", methods=["GET", "DELETE"])
async def eng_job(name: str, jid: str, req: Request):
    url = _eng(name)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await (c.get(f"{url}/jobs/{jid}") if req.method == "GET" else c.delete(f"{url}/jobs/{jid}"))
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/engines/{name}/jobs/{jid}/events")
async def eng_events(name: str, jid: str):
    url = _eng(name)
    client = httpx.AsyncClient(timeout=None)
    req = client.build_request("GET", f"{url}/jobs/{jid}/events")
    resp = await client.send(req, stream=True)

    async def gen():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ 供應鏈地圖
@app.get("/api/sc/networks")
def api_sc_networks():
    return supplychain.networks()


@app.get("/api/sc/network")
def api_sc_network(id: str = "ai-datacenter"):  # noqa: A002
    if id not in supplychain.NETWORKS:
        raise HTTPException(404, "沒有這張圖")
    return supplychain.layout(id)


@app.get("/api/sc/node")
def api_sc_node(id: str):  # noqa: A002
    if id not in supplychain.NODES:
        raise HTTPException(404, "沒有這個環節")
    return supplychain.node_detail(id)


@app.get("/api/sc/stats")
def api_sc_stats(net: str = "ai-datacenter"):
    if net not in supplychain.NETWORKS:
        raise HTTPException(404, "沒有這張圖")
    return supplychain.stats(supplychain.NETWORKS[net]["nodes"], ttl=60)


@app.get("/api/sc/find")
def api_sc_find(q: str):
    return supplychain.find(q) or {}


@app.get("/api/sc/index")
def api_sc_index():
    idx = supplychain.node_index()
    return {sym: [{"id": k, "name": supplychain.NODES[k]["name"]} for k in ks] for sym, ks in idx.items()}


@app.get("/api/sc/news")
def api_sc_news(id: str, n: int = 15):  # noqa: A002
    if id not in supplychain.NODES:
        raise HTTPException(404, "沒有這個環節")
    try:
        return supplychain.node_news(id, n)
    except Exception as e:  # noqa: BLE001
        _err(e)


# ------------------------------------------------------------------ 今日焦點
FOCUS_DIR = DATA / "focus"


@app.get("/api/focus")
def api_focus():
    return focus.build()


@app.get("/api/focus/ai")
def api_focus_ai_cached():
    import datetime as _dt
    f = FOCUS_DIR / f"ai-{_dt.date.today().isoformat()}.json"
    return json.loads(f.read_text()) if f.exists() else {}


@app.post("/api/focus/ai")
async def api_focus_ai(req: Request):
    """Ollama 寫今日重點（每天快取一份；refresh=true 重寫）。不含任何人的持股。"""
    import datetime as _dt
    from starlette.concurrency import run_in_threadpool
    body = await req.json() if (req.headers.get("content-length") or "0") != "0" else {}
    FOCUS_DIR.mkdir(parents=True, exist_ok=True)
    f = FOCUS_DIR / f"ai-{_dt.date.today().isoformat()}.json"
    if f.exists() and not body.get("refresh"):
        return json.loads(f.read_text())
    d = await run_in_threadpool(focus.build)
    s = load_settings()
    llm = ollama.resolve(llm_block(s), "ask")
    text = await run_in_threadpool(
        ollama.ask, llm,
        "你是台股盤後分析師。根據下方今天的指數、供應鏈題材漲跌、強弱勢股、三大法人與新聞標題，寫一份繁體中文「今日焦點」："
        "1) 一句話總結盤勢；2) 3–5 個今天最重要的題材／供應鏈環節，各說明發生什麼、哪些個股反應；3) 明天要注意的事。"
        "只能用下方提供的資料，數字照抄，不要編造；新聞只有標題，不要推測細節。用 Markdown，簡潔。不構成投資建議。",
        focus.ai_context(d), language=s.get("language"))
    out = {"date": _dt.date.today().isoformat(), "text": text, "at": _dt.datetime.now().isoformat(timespec="seconds"),
           "model": llm.get("model")}
    f.write_text(json.dumps(out, ensure_ascii=False))
    return out


# ------------------------------------------------------------------ 重大事件
@app.get("/api/events")
def api_events(since: str = "", until: str = "", kinds: str = "", hidden: bool = False, min_score: float = 0):
    return {"events": events.list_events(since or None, until or None, [k for k in kinds.split(",") if k] or None,
                                         include_hidden=hidden, min_score=min_score),
            "stats": events.stats(), "kinds": events.KINDS}


@app.get("/api/events/markers")
def api_event_markers(since: str, until: str = "", s: str = ""):
    return events.markers(since, until or None, s)


def _ev_body_check(req: Request):
    if req.headers.get("x-ft") != "1":
        raise HTTPException(403, "缺少 X-FT 標頭")


@app.post("/api/events/refresh")
async def api_events_refresh(req: Request, years: int = 0):
    """years=0 → FT_EVENTS_YEARS（預設 2）；「載入近年大事」用 years=10。"""
    _ev_body_check(req)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(events.refresh, f"{min(max(years, 1), 15)}y" if years else None)


@app.post("/api/events")
async def api_event_add(req: Request):
    _ev_body_check(req)
    b = await req.json()
    try:
        return {"id": events.add(b.get("date") or "", b.get("title") or "", b.get("summary") or "")}
    except ValueError as e:
        raise HTTPException(400, str(e) or "日期格式 YYYY-MM-DD") from e


@app.put("/api/events/{eid:path}")
async def api_event_edit(eid: str, req: Request):
    _ev_body_check(req)
    b = await req.json()
    try:
        ok = events.edit(eid, title=b.get("title"), summary=b.get("summary"), hidden=b.get("hidden"), date=b.get("date"))
    except ValueError as e:
        raise HTTPException(400, "日期格式 YYYY-MM-DD") from e
    if not ok:
        raise HTTPException(404, "找不到這個事件")
    return {"ok": True}


@app.delete("/api/events/{eid:path}")
def api_event_delete(eid: str, req: Request):
    _ev_body_check(req)
    if not events.delete(eid):
        raise HTTPException(404, "找不到這個事件")
    return {"ok": True}


@app.post("/api/events/ai/{eid:path}")
async def api_event_ai(eid: str, req: Request):
    """重抓新聞並讓 AI 重新命名（使用者自己改過的標題／摘要不會被覆蓋）。"""
    _ev_body_check(req)
    from starlette.concurrency import run_in_threadpool
    with events._conn() as c:  # noqa: SLF001
        c.execute("UPDATE events SET news_at=NULL WHERE id=?", (eid,))
    llm = ollama.resolve(llm_block(load_settings()), "ask")
    e = await run_in_threadpool(events.enrich, eid, llm)
    if not e:
        raise HTTPException(404, "找不到這個事件")
    return events._public(e)  # noqa: SLF001


# ------------------------------------------------------------------ FOMC 文件（聲明、紀要、SEP）
@app.get("/api/fomc/sep/latest")
async def api_fomc_sep():
    sep = await run_in_threadpool(fomc.latest_sep)
    if not sep:
        raise HTTPException(404, "還沒有經濟預測資料")
    sep["labels"] = {k: zh for _, k, zh in fomc.VARS}
    return sep


@app.get("/api/fomc/{day}")
async def api_fomc(day: str):
    try:
        b = await run_in_threadpool(fomc.bundle, day)
    except KeyError:
        raise HTTPException(404, "不是 FOMC 會議日")
    except Exception as e:  # noqa: BLE001
        _err(e)
    b["labels"] = {k: zh for _, k, zh in fomc.VARS}
    return b


@app.post("/api/fomc/{day}/ai")
async def api_fomc_ai(day: str, req: Request, kind: str = "statement", force: bool = False):
    _ev_body_check(req)
    if kind not in ("statement", "minutes"):
        raise HTTPException(400, "kind 只能是 statement 或 minutes")
    llm = ollama.resolve(llm_block(load_settings()), "ask")
    try:
        return await run_in_threadpool(fomc.ai, day, kind, llm, force)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e)) from e


# ------------------------------------------------------------------ SEC EDGAR + FRED
@app.get("/api/sec/financials")
def api_sec_financials(s: str, freq: str = "annual"):
    try:
        return edgar.statements(s, "quarterly" if freq.startswith("q") else "annual")
    except edgar.NotConfigured as e:
        raise HTTPException(412, str(e)) from e
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/sec/filings")
def api_sec_filings(s: str, forms: str = "", limit: int = 20):
    try:
        return {"enabled": True, "filings": edgar.recent_filings(s, tuple(f for f in forms.split(",") if f) or None, limit)}
    except edgar.NotConfigured as e:
        return {"enabled": False, "message": str(e), "filings": []}
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/twfx")
def api_twfx():
    """新台幣對主要貨幣：最新、日／月／季／年變動、52 週高低、sparkline，加台灣銀行牌告。"""
    return {"rows": fx.snapshot(), "regions": fx.REGIONS, "bot": fx.bot_rates()}


@app.get("/api/twfx/series")
def api_twfx_series(ccy: str, range: str = "1y"):  # noqa: A002
    period = range if range in ("1mo", "3mo", "6mo", "1y", "2y", "5y", "10y") else "1y"
    try:
        pts = fx.history(ccy, period)
    except KeyError:
        raise HTTPException(404, "不支援的幣別")
    except Exception as e:  # noqa: BLE001
        _err(e)
    name, region, flag = fx.CCY[ccy.upper()]
    return {"code": ccy.upper(), "name": name, "flag": flag, "unit": fx.UNIT.get(ccy.upper(), 1), "points": pts}


@app.get("/api/twfx/events")
def api_twfx_events(ccy: str, range: str = "1y"):  # noqa: A002
    """Chart markers for one currency: its own unusual days vs TWD + Fed / 台灣央行 / history / your events."""
    code = ccy.upper()
    if code not in fx.CCY:
        raise HTTPException(404, "不支援的幣別")
    period = range if range in ("1mo", "3mo", "6mo", "1y", "2y", "5y", "10y") else "1y"
    days = {"1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653}[period]
    import datetime as _dt
    since = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    try:
        mv = fx.moves(code, period if days >= 731 else "2y")
    except Exception:  # noqa: BLE001
        mv = []
    name = fx.CCY[code][0]
    out = [{"id": f"fxmove:{code}:{m['date']}", "date": m["date"], "kind": "fxmove", "dir": m["dir"], "label": True,
            "title": f"{name}{'升' if m['dir'] > 0 else '貶'} {abs(m['pct']):.2f}%（{m['sigma']}σ）",
            "summary": f"單日 {name}兌台幣 {m['pct']:+.2f}%，約 {m['sigma']} 倍近 60 日波動"} for m in mv if m["date"] >= since]
    out += events.markers(since, None, f"TWFX:{code}")
    return sorted(out, key=lambda e: e["date"])


# ------------------------------------------------------------------ 分析工具箱（技術／選擇權／研究／財報／價值／情報）
@app.get("/api/kit/{kind}")
def api_kit(kind: str, s: str = "", expiry: str = "", market_: str = Query("all", alias="market")):
    sym = "" if kind == "intel" else (s if s.upper().startswith("FUND:") else (context.resolve(s) or s.upper()))
    try:
        return toolkit.run(kind, sym, expiry=expiry, market=market_)
    except KeyError:
        raise HTTPException(404, "沒有這個工具")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        _err(e)


# ------------------------------------------------------------------ 定期定額回測
def _dca_run(b: dict) -> list[dict]:
    syms = [x.strip() for x in str(b.get("symbols") or "").replace("，", ",").replace("、", ",").split(",") if x.strip()][:6]
    if not syms:
        raise HTTPException(400, "請輸入至少一個標的（最多 6 個）")
    out = []
    for raw in syms:
        sym = raw if raw.upper().startswith("FUND:") else (context.resolve(raw) or raw.upper())
        try:
            r = dca.simulate(sym, b.get("amount") or 3000, b.get("freq") or "monthly", b.get("days") or "6",
                             b.get("start") or None, b.get("end") or None, b.get("fee_pct", 0.1425), b.get("min_fee", 1),
                             lots_out=0 if b.get("all_lots") else 24)
            r["name"] = _dca_name(sym)
            out.append(r)
        except (ValueError, KeyError) as e:
            out.append({"symbol": sym, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            out.append({"symbol": sym, "error": f"{type(e).__name__}: {str(e)[:160]}"})
    return out


def _dca_name(sym: str) -> str:
    try:
        if sym.startswith("FUND:"):
            return (funds.get(sym[5:]) or {}).get("name") or ""
        return twmarket.zh_name(sym) or NAMES.get(sym, "")
    except Exception:  # noqa: BLE001
        return ""


@app.post("/api/dca/backtest")
async def api_dca_backtest(req: Request):
    b = await req.json()
    res = await run_in_threadpool(_dca_run, b)
    return {"results": res, "text": dca.text(res)}


@app.post("/api/dca/ai")
async def api_dca_ai(req: Request):
    """Ollama 評估定期定額回測結果（只用回測數字，不碰你的持股）。"""
    b = await req.json()
    txt = str(b.get("text") or "")[:6000]
    if not txt:
        raise HTTPException(400, "先跑回測")
    s = load_settings()
    system = ("你是審慎的理財顧問。根據使用者提供的「定期定額回測數字」評估：哪個標的表現較好、報酬來自哪裡（長期趨勢或期末反彈）、"
              "定期定額相對單筆投入的優劣、最大回撤代表的持有難度、扣款頻率與手續費的影響，以及適合怎樣的投資人。"
              "只能引用提供的數字，不要編造；過去績效不代表未來；最後一句提醒這不是投資建議。用繁體中文，條列為主，600 字內。")
    try:
        out = await run_in_threadpool(ollama.ask, llm_block(s), system, txt, language=s.get("language"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Ollama：{e}") from e
    return {"text": out}


@app.get("/api/macro")
def api_macro(ids: str = ""):
    rows = fred.snapshot([x for x in ids.split(",") if x] or None)
    return {"rows": rows, "groups": fred.GROUPS}


@app.get("/api/macro/series")
def api_macro_series(id: str, years: float = 10):  # noqa: A002
    if id not in fred.SERIES and not id.isalnum():
        raise HTTPException(400, "bad id")
    try:
        meta = fred.SERIES.get(id, (id, "", "level", "", ""))
        return {"id": id, "name": meta[0], "unit": meta[1], "how": meta[2], "note": meta[4], "points": fred.history(id, years)}
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/sectors")
def api_sectors(market: str = "us", top: int = 30):  # noqa: A002
    try:
        return sectors.breakdown(market, max(5, min(top, 100)))
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/brief")
def api_brief(s: str, date: str = ""):
    """FT 市場背景預覽：分析這檔時會送給 AI 引擎的內容（date＝回測基準日，空白＝今天）。"""
    sym = context.resolve(s) or s
    return brief.build(sym, date or None)


@app.get("/api/twmacro")
def api_twmacro():
    return {"rows": twmacro.snapshot(), "groups": twmacro.GROUPS, "lights": [
        {"from": lo, "name": n, "color": c} for lo, n, c in twmacro.LIGHTS]}


@app.get("/api/twmacro/series")
def api_twmacro_series(id: str, years: float = 10):  # noqa: A002
    if id not in twmacro.SERIES:
        raise HTTPException(404, "unknown series")
    try:
        return twmacro.history(id, years)
    except Exception as e:  # noqa: BLE001
        _err(e)


@app.get("/api/twmacro/diag")
def api_twmacro_diag():
    return twmacro.diag()


# ------------------------------------------------------------------ 分析紀錄與檢討
@app.get("/api/ledger")
def api_ledger(engine: str = "", ticker: str = "", limit: int = 300):
    s = load_settings()
    fb = {e: (s.get("ledger_feedback") or {}).get(e, True) for e in ledger.LLM_ENGINES}
    return {"records": ledger.records(engine, ticker, limit), "stats": ledger.stats(), "feedback": fb,
            "latest": {e: (ledger.reviews(e, 1) or [None])[0] for e in ledger.ENGINES}, "horizons": ledger.HORIZONS,
            "neutral_band": ledger.NEUTRAL_BAND}


@app.post("/api/ledger/refresh")
async def api_ledger_refresh(req: Request):
    _ev_body_check(req)
    from starlette.concurrency import run_in_threadpool
    added = await run_in_threadpool(ledger.collect)
    checked = await run_in_threadpool(ledger.check)
    return {"added": added, "checked": checked}


@app.get("/api/ledger/reviews")
def api_ledger_reviews(engine: str = "", limit: int = 20):
    return ledger.reviews(engine, limit)


@app.post("/api/ledger/review/{engine}")
async def api_ledger_review(engine: str, req: Request):
    _ev_body_check(req)
    if engine not in ledger.ENGINES:
        raise HTTPException(404, "unknown engine")
    from starlette.concurrency import run_in_threadpool
    llm = ollama.resolve(llm_block(load_settings()), "ask")
    try:
        await run_in_threadpool(ledger.collect)
        await run_in_threadpool(ledger.check)
        return await run_in_threadpool(ledger.write_review, engine, llm)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/ledger/feedback")
async def api_ledger_feedback(req: Request):
    _ev_body_check(req)
    b = await req.json()
    s = load_settings()
    fb = dict(s.get("ledger_feedback") or {})
    for k, v in b.items():
        if k in ledger.LLM_ENGINES:
            fb[k] = bool(v)
    s["ledger_feedback"] = fb
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    return fb


# ------------------------------------------------------------------ 我的持股（登入）
import auth  # noqa: E402


def _chains(sym: str) -> list[str]:
    return [supplychain.NODES[k]["name"] for k in _SC_INDEX().get(sym, [])]


def _SC_INDEX():  # noqa: N802
    return market.cached("sc:index", 3600, supplychain.node_index)


def _name(sym: str) -> str | None:
    return twmarket.zh_name(sym) or NAMES.get(sym)


auth.quotes_fn = lambda syms: _decorate(market.quotes(syms))
auth.fund_fn = funds.get
auth.chains_fn = _chains
auth.name_fn = _name
app.include_router(auth.router)


# ------------------------------------------------------------------ agent API + warm-up
import agent_api  # noqa: E402

agent_api.init(load_settings, llm_block, ENGINES, enrich_job)
app.include_router(agent_api.router)


# ------------------------------------------------------------------ public exposure gate (Cloudflare Tunnel)
_PUBLIC_OPEN = ("/api/auth/me", "/api/auth/login", "/api/auth/logout", "/api/auth/register", "/healthz")
_PUBLIC_NEVER = ("/api/agent/", "/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def _public_gate(request: Request, call_next):
    """Over the public URL everything except the page shell and the login calls needs a session; the agent API
    (meant for Hermes / Open WebUI on this host) and settings changes (Ollama URL → SSRF) are local/tailnet only.
    Requests from 127.0.0.1 / the tailnet are unchanged."""
    if not auth.is_public(request):
        return await call_next(request)
    path = request.url.path
    if path.startswith(_PUBLIC_NEVER):
        return JSONResponse({"detail": "這個功能只開放本機／tailnet"}, status_code=404)
    if path == "/" or path.startswith("/static/") or path in ("/favicon.ico", "/manifest.webmanifest") or path in _PUBLIC_OPEN:
        resp = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp
    if not await run_in_threadpool(auth.current_user, request):
        return JSONResponse({"detail": "請先登入"}, status_code=401)
    if path == "/api/settings" and request.method != "GET":
        return JSONResponse({"detail": "設定只能從本機／tailnet 修改"}, status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


@app.on_event("startup")  # noqa: (lifespan would replace the whole app factory)
def _warm():
    import threading

    def go():
        import time as _t
        for fn in (twmarket.securities, funds.ensure_quick):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                print("warm-up failed:", fn.__name__, e)
        while True:  # keep fund NAV history accumulating even when nobody looks at it
            _t.sleep(3600)
            try:
                funds.refresh()
                twmarket.securities()
            except Exception as e:  # noqa: BLE001
                print("periodic refresh failed:", e)
    threading.Thread(target=go, daemon=True).start()
    if os.environ.get("FT_WORLD", "1") != "0":
        threading.Thread(target=world.loop_forever, daemon=True).start()
    threading.Thread(target=_daily_notes_loop, daemon=True).start()
    if os.environ.get("FT_LEDGER", "1") != "0":
        threading.Thread(target=ledger.loop_forever, args=(lambda: ollama.resolve(llm_block(load_settings()), "ask"),),
                         daemon=True).start()
    if os.environ.get("FT_FOMC", "1") != "0":
        def _fomc_loop():
            import time as _t
            _t.sleep(300)
            while True:
                try:
                    fomc.sync()
                except Exception as e:  # noqa: BLE001
                    print("fomc sync failed:", e)
                _t.sleep(3 * 3600)
        threading.Thread(target=_fomc_loop, daemon=True).start()
    if os.environ.get("FT_EVENTS", "1") != "0":
        threading.Thread(target=events.loop_forever, args=(lambda: ollama.resolve(llm_block(load_settings()), "ask"),),
                         daemon=True).start()


def _daily_notes_loop():
    """Once a day: yesterday's world-events note + the forecast scorecard."""
    import datetime as _dt
    import time as _t
    _t.sleep(90)
    while True:
        st = notes._state()
        today = _dt.date.today().isoformat()
        try:
            if st.get("review_day") != today and notes.enabled("review"):
                notes.write_review()
                st["review_day"] = today
            y = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
            if st.get("world_day") != y and notes.enabled("world"):
                evs = world.events(days=0, as_of=y, since=y)
                snaps = world.status()
                if snaps.get("first") and snaps["first"][:10] <= y:
                    notes.write_world_day(y, world.digest(list(world.REGIONS), as_of=y, days=1, max_chars=8000), evs)
                st["world_day"] = y
            notes._save_state(st)
        except Exception as e:  # noqa: BLE001
            print("daily notes failed:", e)
        _t.sleep(3600)


# ------------------------------------------------------------------ world (God's Eye View)
@app.get("/api/world/status")
def api_world_status():
    return world.status()


@app.get("/api/world/regions")
def api_world_regions():
    return [{"id": k, "name": v["name"], "why": v["why"], "bbox": v["bbox"],
             "checks": [{"id": c, "label": world.CHECKS[c][0]} for c in v["checks"]]} for k, v in world.REGIONS.items()]


@app.get("/api/world/latest")
def api_world_latest(regions: str = "", symbol: str = "", as_of: str = ""):
    rs = [r for r in regions.split(",") if r] or (world.regions_for(symbol) if symbol else list(world.REGIONS))
    snap = world.latest(rs, as_of or None)
    for checks in snap.values():
        for d in checks.values():
            d["view_url"] = world.public_view_url(d.get("view_url"))
    return {"regions": rs, "latest": snap, "public_url": world.GEV_PUBLIC_URL or None}


@app.get("/api/world/events")
def api_world_events(symbol: str = "", regions: str = "", days: int = 30, since: str = ""):
    rs = [r for r in regions.split(",") if r] or (world.regions_for(symbol) if symbol else None)
    return world.events(rs, days=days, since=since or None)


@app.get("/api/world/digest")
def api_world_digest(symbol: str = "", regions: str = "", as_of: str = ""):
    rs = [r for r in regions.split(",") if r] or (world.regions_for(symbol) if symbol else list(world.REGIONS))
    return {"regions": rs, "text": world.digest(rs, as_of or None, max_chars=8000)}


@app.get("/api/world/series")
def api_world_series(region: str, check: str):
    if region not in world.REGIONS or check not in world.CHECKS:
        raise HTTPException(404, "unknown region/check")
    return world.series(region, check)


@app.post("/api/world/refresh")
async def api_world_refresh():
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(world.collect_once)


# ------------------------------------------------------------------ Obsidian notes
@app.get("/api/notes/status")
def api_notes_status():
    pending = [str(p.relative_to(notes.OUTBOX)) for p in notes.OUTBOX.rglob("*.md")] if notes.OUTBOX.exists() else []
    st = notes._state()
    return {"folder": notes.FOLDER, "pending": len(pending), "pending_files": pending[:20],
            "applied": st.get("applied"), "review_day": st.get("review_day"), "auto": notes.enabled("tradingagents")}


@app.post("/api/notes/review")
async def api_notes_review():
    from starlette.concurrency import run_in_threadpool
    return {"note": await run_in_threadpool(notes.write_review)}


@app.post("/api/engines/{name}/jobs/{jid}/note")
async def api_job_note(name: str, jid: str):
    url = _eng(name)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{url}/jobs/{jid}/note")
    return JSONResponse(r.json(), status_code=r.status_code)


# ------------------------------------------------------------------ static
@app.get("/healthz")
def healthz():
    return {"ok": True, "fake_data": market.FAKE}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("FT_BIND", "127.0.0.1"), port=int(os.environ.get("FT_PORT", 8130)),
                log_level="warning")
