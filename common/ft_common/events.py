"""重大事件 — the year's market-moving days, marked on index charts and listed on their own page.

Sources
  move   : detected from the indices themselves — a day whose move is unusually large for that index
           (|change| ≥ max(floor, 2.5 × trailing 60-day σ)) or a week-long slide/surge; days within 3 calendar
           days of each other (US close → Taiwan open next morning, weekends) are merged into one event
  fomc   : the Fed's published meeting calendar (decision day)
  manual : added by you on the 重大事件 page
  world  : God's Eye View events (read from world.sqlite; shown on the page, the chart has its own 🌐 toggle)

Each detected event gets that window's Chinese headlines (Google News, date-bounded) and, when Ollama is
reachable, a short AI title + 2–3 sentence summary written *only* from those headlines and the index moves.
User edits (title, summary, hidden) always win over later re-detection / AI rewrites.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from . import market, twmarket

log = logging.getLogger("ft.events")
DATA = Path(os.environ.get("FT_DATA", "/data"))
_lock = threading.Lock()

INDEXES = {"^TWII": ("加權", 2.0, 6.0), "^GSPC": ("S&P 500", 1.8, 5.0), "^IXIC": ("Nasdaq", 2.2, 6.0),
           "^SOX": ("費半", 3.5, 10.0)}          # symbol → (short name, daily floor %, 5-day floor %)
SIGMA_K = 2.5
FOMC = ["2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
KINDS = {"move": "市場大波動", "history": "歷史大事", "fomc": "Fed 利率決策", "manual": "自訂", "world": "世界事件"}

# Well-documented market-moving events since 2020 (built-in list; dates are the day markets reacted).
# Later events come from detection + headlines. Edit freely — user edits in the UI are never overwritten.
HISTORY = [
    ("2020-02-24", "新冠疫情全球擴散", "義大利、南韓疫情急升，美股道瓊單日跌逾千點，揭開 2020 年 2–3 月全球股災。"),
    ("2020-03-09", "油價戰＋疫情 美股熔斷", "沙烏地與俄羅斯爆發油價戰，油價單日重挫約四分之一，美股開盤觸發 7% 熔斷。"),
    ("2020-03-16", "Fed 緊急降息至零 美股再熔斷", "Fed 週日緊急降息至 0–0.25% 並重啟購債，美股仍重挫，S&P 500 單日跌約 12%。"),
    ("2020-03-23", "Fed 宣布無上限購債", "Fed 宣布無上限 QE 並首度買進公司債，美股在這天前後落底。"),
    ("2020-04-20", "WTI 原油期貨跌成負價", "5 月合約到期前儲油空間不足，WTI 原油期貨史上首次收在負值。"),
    ("2020-11-09", "輝瑞疫苗有效性逾 90%", "輝瑞／BNT 公布疫苗期中結果，景氣循環股、航空與價值股大漲，資金自居家概念股輪出。"),
    ("2021-01-27", "GameStop 散戶軋空", "散戶集結推升 GameStop 等高放空股，避險基金被迫回補，美股當天明顯下跌。"),
    ("2021-05-12", "台股盤中重挫逾 1,400 點", "本土疫情升溫加上美國通膨疑慮，加權指數盤中一度重挫逾 1,400 點。"),
    ("2022-02-24", "俄羅斯入侵烏克蘭", "俄軍全面進攻烏克蘭，油價、天然氣與糧價急漲，全球股市劇烈震盪。"),
    ("2022-03-16", "Fed 疫情後首度升息", "Fed 升息 1 碼，開啟 2022–2023 年快速升息循環。"),
    ("2022-06-15", "Fed 升息 3 碼", "美國通膨創 40 年新高，Fed 一次升息 0.75 個百分點，為 1994 年以來最大單次升幅。"),
    ("2022-09-13", "美國 CPI 高於預期", "8 月 CPI 高於預期，市場預期 Fed 持續大幅升息，S&P 500 單日跌逾 4%。"),
    ("2022-11-30", "ChatGPT 發布", "OpenAI 推出 ChatGPT，之後帶動生成式 AI 與算力投資熱潮。"),
    ("2023-03-10", "矽谷銀行倒閉", "存款擠兌下 SVB 遭監管機關接管，引發美國區域銀行危機與降息預期。"),
    ("2023-05-25", "輝達財報引爆 AI 行情", "輝達前一晚預告營收遠超預期，股價單日大漲約 24%，AI 供應鏈全面走強。"),
    ("2024-01-13", "台灣總統大選", "總統與立委選舉，結果公布後首個交易日起台股反應。"),
    ("2024-08-05", "日圓套利平倉股災", "日銀升息、美國就業數據轉弱，日圓套利交易大舉平倉：日經單日跌 12.4%，台股跌 8.35%（1,807 點）。"),
    ("2024-09-18", "Fed 降息 2 碼", "Fed 四年多來首度降息，一次降 0.5 個百分點。"),
    ("2024-11-06", "美國大選川普勝選", "川普勝選結果明朗，美股、美元與比特幣大漲。"),
    ("2025-01-27", "DeepSeek 衝擊 AI 股", "中國 DeepSeek 低成本模型引發算力需求疑慮，輝達單日跌約 17%，市值蒸發近 6,000 億美元。"),
    ("2025-04-03", "川普「對等關稅」", "4/2 盤後宣布對各國課徵對等關稅（台灣 32%），美股連兩天重挫。"),
    ("2025-04-07", "台股連假後開盤暴跌", "清明連假後首個交易日，加權指數跌 2,065 點（-9.7%），創史上最大點數跌幅，逾千檔跌停。"),
    ("2025-04-09", "對等關稅暫緩 90 天", "川普宣布多數國家對等關稅暫緩 90 天，S&P 500 單日大漲約 9.5%。"),
    ("2025-05-12", "美中日內瓦關稅協議", "美中同意 90 天內大幅調降互徵關稅，美股與台股大漲。"),
]
NEWS_Q = "股市 OR 台股 OR 美股 OR 費半 OR 道瓊"


def _db() -> Path:
    return Path(os.environ.get("FT_DATA", str(DATA))) / "events.sqlite"


def _conn() -> sqlite3.Connection:
    p = _db()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=30)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, date TEXT NOT NULL, kind TEXT NOT NULL, dir INTEGER DEFAULT 0,
        score REAL DEFAULT 0, moves TEXT, auto_title TEXT, auto_summary TEXT, title TEXT, summary TEXT,
        headlines TEXT, ai_at TEXT, ai_model TEXT, news_at TEXT, hidden INTEGER DEFAULT 0, edited INTEGER DEFAULT 0,
        created TEXT, updated TEXT);
    CREATE INDEX IF NOT EXISTS events_date ON events(date);
    """)
    return c


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ detection
def _returns(sym: str, period: str = "2y"):
    df = market.history(sym, period, "1d")
    if df is None or len(df) < 30:
        return None
    close = df["Close"].astype(float)
    close.index = [str(getattr(t, "date", lambda: t)())[:10] for t in close.index]
    return close


def load_closes(period: str = "2y") -> dict:
    out = {}
    for sym in INDEXES:
        try:
            close = _returns(sym, period)
        except Exception as e:  # noqa: BLE001
            log.info("history %s failed: %s", sym, e)
            continue
        if close is not None:
            out[sym] = close
    return out


def moves_on(date: str, closes: dict) -> dict:
    """Each index's change on the first trading day on/after `date` (within 4 days) → {sym: {name, date, pct, kind}}."""
    out = {}
    end = (dt.date.fromisoformat(date) + dt.timedelta(days=4)).isoformat()
    for sym, close in closes.items():
        idx = list(close.index)
        k = next((n for n, d in enumerate(idx) if date <= d <= end), None)
        if k is None or k == 0:
            continue
        out[sym] = {"name": INDEXES[sym][0], "date": idx[k], "pct": round((float(close.iloc[k]) / float(close.iloc[k - 1]) - 1) * 100, 2),
                    "kind": "day"}
    return out


def detect(period: str = "2y", closes: dict | None = None) -> list[dict]:
    """Big index moves → merged events [{date, end, dir, score, moves:{sym:{name,date,pct,kind}}}]."""
    flags = []  # (date, sym, pct, kind, score)
    closes = closes if closes is not None else load_closes(period)
    for sym, (name, floor1, floor5) in INDEXES.items():
        close = closes.get(sym)
        if close is None:
            continue
        r1 = close.pct_change() * 100
        sd = r1.rolling(60, min_periods=20).std().shift(1)
        r5 = (close / close.shift(5) - 1) * 100
        for d in r1.index[1:]:
            x, s = r1[d], sd[d]
            thr = max(floor1, SIGMA_K * s) if s == s else floor1
            if x == x and abs(x) >= thr:
                flags.append((d, sym, float(x), "day", abs(x) / thr))
            y = r5[d]
            if y == y and abs(y) >= floor5:
                flags.append((d, sym, float(y), "week", abs(y) / floor5 * 0.9))
    flags.sort()
    clusters: list[list] = []
    for f in flags:
        d = dt.date.fromisoformat(f[0])
        if clusters and (d - dt.date.fromisoformat(clusters[-1][-1][0])).days <= 3:
            clusters[-1].append(f)
        else:
            clusters.append([f])
    out = []
    for cl in clusters:
        moves: dict[str, dict] = {}
        for d, sym, x, kind, sc in cl:  # per index keep its strongest daily move (a week move only if no daily one)
            cur = moves.get(sym)
            better = cur is None or (kind == "day" and cur["kind"] == "week") or (kind == cur["kind"] and abs(x) > abs(cur["pct"]))
            if better:
                moves[sym] = {"name": INDEXES[sym][0], "date": d, "pct": round(x, 2), "kind": kind}
        net = sum(m["pct"] for m in moves.values())
        day_dates = [f[0] for f in cl if f[3] == "day"]
        start = min(day_dates) if day_dates else min(f[0] for f in cl)
        out.append({"date": start, "end": max(f[0] for f in cl), "dir": 1 if net > 0 else -1,
                    "score": round(max(f[4] for f in cl) * (1 + 0.15 * (len(moves) - 1)), 2), "moves": moves})
    return out


def default_title(e: dict) -> str:
    mv = sorted(e["moves"].values(), key=lambda m: (m["kind"] != "day", -abs(m["pct"])))  # a single-day move names it
    top = mv[0]
    word = ("大漲" if top["pct"] > 0 else "大跌") if top["kind"] == "day" else ("五日漲" if top["pct"] > 0 else "五日跌")
    return f"{top['name']}{word} {top['pct']:+.1f}%"


def refresh(period: str | None = None) -> dict:
    """Re-detect and upsert. Keeps user edits / AI text; events that vanish are left alone (never auto-deleted).

    Built-in historical events are inserted once and get their index moves from the same price history; a detected
    move within 3 days of one of them is folded into it (no duplicate marker)."""
    period = period or f"{int(os.environ.get('FT_EVENTS_YEARS', '2'))}y"
    closes = load_closes(period)
    found = detect(period, closes)
    now = _now()
    hist_days = [dt.date.fromisoformat(d) for d, _, _ in HISTORY]

    def near_hist(e):
        lo, hi = dt.date.fromisoformat(e["date"]), dt.date.fromisoformat(e["end"])
        return next((h for h in hist_days if lo - dt.timedelta(days=3) <= h <= hi + dt.timedelta(days=3)), None)
    folded = {}
    fed = {}
    for d in FOMC:  # what the Fed actually did (FRED target range), fetched before taking the DB lock
        if d < dt.date.today().isoformat():
            try:
                from . import fred
                fd = fred.fed_decision(d)
            except Exception:  # noqa: BLE001
                fd = None
            if fd:
                fed[d] = fd
    with _lock, _conn() as c:
        for e in found:
            h = near_hist(e)
            if h:
                folded.setdefault(h.isoformat(), {}).update(e["moves"])
                c.execute("DELETE FROM events WHERE id=? AND kind='move' AND edited=0", (f"move:{e['date']}",))
                continue
            eid = f"move:{e['date']}"
            row = c.execute("SELECT id FROM events WHERE id=?", (eid,)).fetchone()
            if row:
                c.execute("UPDATE events SET dir=?, score=?, moves=? WHERE id=?",
                          (e["dir"], e["score"], json.dumps(e["moves"], ensure_ascii=False), eid))
                c.execute("UPDATE events SET auto_title=? WHERE id=? AND ai_at IS NULL", (default_title(e), eid))
            else:
                c.execute("INSERT INTO events(id,date,kind,dir,score,moves,auto_title,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                          (eid, e["date"], "move", e["dir"], e["score"], json.dumps(e["moves"], ensure_ascii=False),
                           default_title(e), now, now))
        for d, title, summary in HISTORY:
            eid = f"hist:{d}"
            mv = moves_on(d, closes)
            for sym, m in folded.get(d, {}).items():  # a stronger single-day move found nearby (e.g. the next session)
                if m["kind"] == "day" and abs(m["pct"]) > abs(mv.get(sym, {}).get("pct", 0)):
                    mv[sym] = m
            net = sum(m["pct"] for m in mv.values())
            score = max([abs(m["pct"]) / INDEXES[s][1] for s, m in mv.items()], default=0)
            if c.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone():
                if mv:
                    c.execute("UPDATE events SET moves=?, dir=?, score=? WHERE id=?",
                              (json.dumps(mv, ensure_ascii=False), 1 if net > 0 else -1, round(score, 2), eid))
            else:
                c.execute("INSERT INTO events(id,date,kind,dir,score,moves,auto_title,auto_summary,created,updated) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, d, "history", 1 if net > 0 else -1, round(score, 2),
                                                          json.dumps(mv, ensure_ascii=False), title, summary, now, now))
        for d in FOMC:
            fd = fed.get(d)
            if fd:  # fact from FRED's target range, never overrides a user's edit
                c.execute("INSERT OR IGNORE INTO events(id,date,kind,created,updated) VALUES(?,?,?,?,?)",
                          (f"fomc:{d}", d, "fomc", now, now))
                c.execute("UPDATE events SET auto_title=?, dir=?, auto_summary=COALESCE(auto_summary, ?) WHERE id=?",
                          (fd["title"], (1 if fd["bp"] < 0 else -1 if fd["bp"] > 0 else 0),
                           f"聯邦基金利率目標區間 {fd['before_lower']:.2f}%–{fd['before_upper']:.2f}% → "
                           f"{fd['lower']:.2f}%–{fd['upper']:.2f}%（FRED DFEDTARL／DFEDTARU）", f"fomc:{d}"))
        for d in FOMC:
            eid = f"fomc:{d}"
            if not c.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone():
                c.execute("INSERT INTO events(id,date,kind,auto_title,created,updated) VALUES(?,?,?,?,?,?)",
                          (eid, d, "fomc", "Fed 利率決策（FOMC）", now, now))
    return {"detected": len(found), "history": len(HISTORY), "period": period,
            "since": min((str(v.index[0]) for v in closes.values()), default=None), "at": now}


# ------------------------------------------------------------------ enrichment (headlines + AI)
def _row(c, eid: str) -> dict | None:
    cur = c.execute("SELECT * FROM events WHERE id=?", (eid,))
    r = cur.fetchone()
    if not r:
        return None
    d = dict(zip([x[0] for x in cur.description], r))
    for k in ("moves", "headlines"):
        d[k] = json.loads(d[k]) if d.get(k) else ({} if k == "moves" else [])
    return d


def fetch_headlines(e: dict) -> list[dict]:
    start = dt.date.fromisoformat(e["date"]) - dt.timedelta(days=1)
    end_d = dt.date.fromisoformat(max([e["date"]] + [m["date"] for m in e.get("moves", {}).values()])) + dt.timedelta(days=2)
    q = "聯準會 OR Fed OR 利率 OR 美股" if e["kind"] == "fomc" else NEWS_Q
    try:
        rows = twmarket.news_between(q, start.isoformat(), end_d.isoformat(), 15)
    except Exception as e2:  # noqa: BLE001
        log.info("headlines %s failed: %s", e["id"], e2)
        return []
    return [{"title": r["title"], "publisher": r.get("publisher"), "link": r.get("link"), "time": r.get("time")} for r in rows]


def ai_prompt(e: dict) -> str:
    mv = "\n".join(f"- {m['name']}：{m['date']} {'單日' if m['kind'] == 'day' else '五日'} {m['pct']:+.2f}%"
                   for m in e.get("moves", {}).values()) or "（無指數大波動資料）"
    hl = "\n".join(f"- {h['title']}（{h.get('publisher') or ''}）" for h in e.get("headlines", [])[:15]) or "（沒有找到新聞）"
    return f"日期：{e['date']}\n類型：{KINDS.get(e['kind'], e['kind'])}\n指數變動：\n{mv}\n當時新聞標題：\n{hl}"


AI_SYSTEM = ("你是財經編輯。根據提供的指數變動與「當時的新聞標題」，替這個市場事件取名並摘要。"
             "只輸出 JSON：{\"title\": \"不超過 12 個字的事件名稱\", \"summary\": \"2–3 句：發生什麼、市場怎麼反應\"}。"
             "只能根據標題推論；標題看不出原因就寫「原因不明，標題未提及」，不要編造數字或事件。用繁體中文。")


def enrich(eid: str, llm: dict | None, *, news: bool = True, ai: bool = True) -> dict | None:
    with _conn() as c:
        e = _row(c, eid)
    if not e or e["kind"] not in ("move", "fomc", "history"):
        return e
    if news and not e.get("news_at"):
        e["headlines"] = fetch_headlines(e)
        with _lock, _conn() as c:
            c.execute("UPDATE events SET headlines=?, news_at=? WHERE id=?",
                      (json.dumps(e["headlines"], ensure_ascii=False), _now(), eid))
    fed_fact = e["kind"] == "fomc" and "DFEDTARU" in (e.get("auto_summary") or "")
    if ai and llm and e["kind"] != "history" and not fed_fact and (e["kind"] == "move" or e["headlines"]):
        from . import ollama
        try:
            raw = ollama.ask(llm, AI_SYSTEM, ai_prompt(e), json_mode=True)
            j = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            title = str(j.get("title") or "").strip()[:24]
            summary = str(j.get("summary") or "").strip()[:600]
        except Exception as e2:  # noqa: BLE001
            log.info("AI naming %s failed: %s", eid, e2)
            return e
        if title:
            with _lock, _conn() as c:
                c.execute("UPDATE events SET auto_title=?, auto_summary=?, ai_at=?, ai_model=? WHERE id=?",
                          (title, summary, _now(), llm.get("model"), eid))
    with _conn() as c:
        return _row(c, eid)


def pending(limit: int = 50) -> list[str]:
    # wait until the news window (event day −1 … +2) has passed, so headlines and the AI title see the whole story
    today = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    with _conn() as c:
        return [r[0] for r in c.execute(
            "SELECT id FROM events WHERE kind IN ('move','fomc','history') AND date<=? AND date>=? AND (news_at IS NULL OR "
            "(kind='move' AND ai_at IS NULL)) AND hidden=0 ORDER BY score DESC, date DESC LIMIT ?",
            (today, (dt.date.today() - dt.timedelta(days=int(os.environ.get("FT_EVENTS_ENRICH_DAYS", "4000")))).isoformat(), limit))]


# ------------------------------------------------------------------ read / edit
def _public(e: dict) -> dict:
    e["title_final"] = e.get("title") or e.get("auto_title") or ""
    e["summary_final"] = e.get("summary") or e.get("auto_summary") or ""
    e["kind_name"] = KINDS.get(e["kind"], e["kind"])
    e["upcoming"] = e["date"] > dt.date.today().isoformat()
    return e


def list_events(since: str | None = None, until: str | None = None, kinds: list[str] | None = None,
                include_hidden: bool = False, world_too: bool = True, min_score: float = 0) -> list[dict]:
    since = since or (dt.date.today() - dt.timedelta(days=365)).isoformat()
    until = until or (dt.date.today() + dt.timedelta(days=120)).isoformat()
    q, args = "SELECT * FROM events WHERE date>=? AND date<=?", [since, until]
    if not include_hidden:
        q += " AND hidden=0"
    if kinds:
        q += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    with _conn() as c:
        cur = c.execute(q + " ORDER BY date DESC", args)
        cols = [x[0] for x in cur.description]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            d["moves"] = json.loads(d["moves"]) if d.get("moves") else {}
            d["headlines"] = json.loads(d["headlines"]) if d.get("headlines") else []
            if d["kind"] == "move" and d["score"] < min_score:
                continue
            out.append(_public(d))
    if world_too and (not kinds or "world" in kinds):
        try:
            from . import world
            for w in world.events(days=0, since=since):
                if (w.get("severity") or 0) < 2:
                    continue
                out.append({"id": f"world:{w['date']}:{w['region']}:{w['kind']}", "date": w["date"], "kind": "world",
                            "dir": 0, "score": w.get("severity") or 0, "moves": {}, "headlines": [],
                            "title_final": w["title"], "summary_final": w.get("detail") or "", "kind_name": "世界事件",
                            "region": w.get("region_name"), "upcoming": False, "hidden": 0, "readonly": True})
        except Exception:  # noqa: BLE001
            pass
    out.sort(key=lambda e: e["date"], reverse=True)
    return out


def markers(since: str, until: str | None = None, symbol: str = "", top: int = 12) -> list[dict]:
    """Compact list for the chart overlay (no world events — the chart has its own 🌐 toggle).

    On one of the tracked indices only that index's own big moves are shown; on anything else the `top`
    strongest market-wide events in the window. The strongest `top` also get `label=True` (text on the chart);
    the rest are drawn as bare arrows so a volatile year stays readable."""
    rows = list_events(since, until or dt.date.today().isoformat(), ["move", "history", "fomc", "manual"], world_too=False)
    years = max(1.0, (dt.date.fromisoformat((until or dt.date.today().isoformat())[:10]) - dt.date.fromisoformat(since[:10])).days / 365)
    moves = [e for e in rows if e["kind"] == "move"]
    if symbol in INDEXES:
        moves = [e for e in moves if symbol in e["moves"] or e.get("edited")]
        moves = sorted(moves, key=lambda e: -abs(e["moves"].get(symbol, {}).get("pct", 0)))[:int(min(40, max(15, 6 * years)))]
    else:
        moves = sorted(moves, key=lambda e: -e["score"])[:int(top * min(years, 3))]
    if years > 3:  # long charts: Fed days would bury everything else
        rows = [e for e in rows if e["kind"] != "fomc"]
    n_lab = top if years <= 3 else 5  # long charts: built-in history events carry the labels
    strong = {e["id"] for e in sorted(moves, key=lambda e: -(e["moves"].get(symbol, {}).get("pct", 0) ** 2 or e["score"]))[:n_lab]}
    keep = moves + [e for e in rows if e["kind"] != "move"]
    out = []
    for e in sorted(keep, key=lambda e: e["date"]):
        mine = e["moves"].get(symbol) if symbol else None
        out.append({"id": e["id"], "date": (mine or {}).get("date") if mine and mine["kind"] == "day" else e["date"],
                    "kind": e["kind"], "dir": (1 if mine["pct"] > 0 else -1) if mine else e["dir"], "title": e["title_final"],
                    "summary": e["summary_final"][:200], "moves": e["moves"],
                    "label": e["id"] in strong or e["kind"] in ("manual", "history")})
    return out


def add(date: str, title: str, summary: str = "") -> str:
    dt.date.fromisoformat(date)
    title = title.strip()[:40]
    if not title:
        raise ValueError("請輸入事件名稱")
    eid = f"manual:{date}:{abs(hash((title, _now()))) % 10 ** 8}"
    with _lock, _conn() as c:
        c.execute("INSERT INTO events(id,date,kind,title,summary,edited,created,updated) VALUES(?,?,?,?,?,1,?,?)",
                  (eid, date, "manual", title, summary.strip()[:1000], _now(), _now()))
    return eid


def edit(eid: str, **kw) -> bool:
    sets, args = [], []
    for k in ("title", "summary"):
        if k in kw and kw[k] is not None:
            v = str(kw[k]).strip()
            sets.append(f"{k}=?")
            args.append(v[:40 if k == "title" else 1000] or None)
    if "hidden" in kw and kw["hidden"] is not None:
        sets.append("hidden=?")
        args.append(1 if kw["hidden"] else 0)
    if "date" in kw and kw["date"] and eid.startswith("manual:"):
        dt.date.fromisoformat(kw["date"])
        sets.append("date=?")
        args.append(kw["date"])
    if not sets:
        return False
    sets += ["edited=1", "updated=?"]
    args += [_now(), eid]
    with _lock, _conn() as c:
        return c.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", args).rowcount > 0


def delete(eid: str) -> bool:
    """Manual events are deleted; detected ones are hidden (re-detection would bring them back otherwise)."""
    with _lock, _conn() as c:
        if eid.startswith("manual:"):
            return c.execute("DELETE FROM events WHERE id=?", (eid,)).rowcount > 0
        return c.execute("UPDATE events SET hidden=1 WHERE id=?", (eid,)).rowcount > 0


def stats() -> dict:
    with _conn() as c:
        n, = c.execute("SELECT COUNT(*) FROM events WHERE kind='move'").fetchone()
        ai, = c.execute("SELECT COUNT(*) FROM events WHERE kind='move' AND ai_at IS NOT NULL").fetchone()
        news, = c.execute("SELECT COUNT(*) FROM events WHERE kind IN ('move','fomc') AND news_at IS NOT NULL").fetchone()
        last, = c.execute("SELECT MAX(updated) FROM events").fetchone()
    return {"moves": n, "ai_named": ai, "with_news": news, "pending": len(pending(500)), "updated": last,
            "ai_enabled": os.environ.get("FT_EVENTS_AI", "1") != "0"}


# ------------------------------------------------------------------ background loop
def loop_forever(get_llm, interval_h: float = 6):
    """Detect a few minutes after start, then every few hours; enrich one event at a time (headlines, then AI)."""
    import time
    time.sleep(float(os.environ.get("FT_EVENTS_DELAY", "120")))
    last = 0.0
    while True:
        try:
            if time.time() - last > interval_h * 3600:
                refresh()
                last = time.time()
            ids = pending(20)
            use_ai = os.environ.get("FT_EVENTS_AI", "1") != "0"
            for eid in ids:
                llm = get_llm() if use_ai else None
                enrich(eid, llm, ai=use_ai)
                time.sleep(15)
        except Exception as e:  # noqa: BLE001
            log.warning("events loop: %s", e)
        time.sleep(600)
