"""分析紀錄與事後核對 — every finished AI analysis (when, which agent/model, which stock, what it said), checked
against what the price actually did 5 / 20 / 60 trading days later, and a periodic per-agent 檢討 whose lessons can be
fed back into that agent's next runs.

Scoring rules (kept deliberately simple and stated in the UI):
  direction  看多 / Buy / Overweight → +1, 看空 / Sell / Underweight / 放空 → −1, 中性 / Hold / 觀望 → 0
  hit        +1 and the price rose; −1 and it fell; 0 and |move| < 2 / 4 / 7 % (5 / 20 / 60 trading days)
  excess     the same move minus the benchmark's (0050 for Taiwan, S&P 500 otherwise), signed by the call
  TimesFM    judged at its own horizon end: did the close land inside the forecast's 80 % band, and how far from the median
Prices are the close on the analysis date and the close h trading days later (no look-ahead, no costs).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path

from . import market, notes

log = logging.getLogger("ft.ledger")
_lock = threading.Lock()
ENGINES = ("tradingagents", "hedgefund", "fingpt", "timesfm", "kronos", "laya", "qlib", "finrl", "research")
LLM_ENGINES = ("tradingagents", "hedgefund", "fingpt")      # these can take lessons back into their prompts
HORIZONS = (5, 20, 60)
NEUTRAL_BAND = {5: 2.0, 20: 4.0, 60: 7.0}
LABEL = notes.ENGINE_LABEL


def _data() -> Path:
    return Path(os.environ.get("FT_DATA", "/data"))


def _conn() -> sqlite3.Connection:
    p = _data() / "ledger.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=30)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, engine TEXT, job_id TEXT, created TEXT, finished TEXT,
        date TEXT, ticker TEXT, name TEXT, signal TEXT, dir INTEGER, price_at REAL, model TEXT, summary TEXT,
        excerpt TEXT, params TEXT, horizon INTEGER, fc TEXT, lessons_used INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS analyses_engine ON analyses(engine, date);
    CREATE TABLE IF NOT EXISTS checks(analysis_id TEXT, h INTEGER, due TEXT, price REAL, ret REAL, bench REAL,
        excess REAL, hit INTEGER, in_band INTEGER, err REAL, checked TEXT, PRIMARY KEY(analysis_id, h));
    CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, engine TEXT, created TEXT, period_from TEXT,
        period_to TEXT, n INTEGER, stats TEXT, summary TEXT, patterns TEXT, lessons TEXT, model TEXT, note TEXT);
    """)
    return c


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ collect
def _direction(engine: str, signal, fc: dict | None) -> int | None:
    if engine in notes.BAND_ENGINES:
        ch = (fc or {}).get("change_pct")
        return None if ch is None else (1 if ch > 1 else -1 if ch < -1 else 0)
    return notes.direction(signal)


def _model(job: dict) -> str:
    llm = job.get("llm") or {}
    models = llm.get("models") or {}
    key = {"tradingagents": "tradingagents_deep"}.get(job.get("engine"), job.get("engine"))
    return models.get(key) or llm.get("model") or ({"timesfm": "TimesFM", "kronos": "Kronos"}.get(job.get("engine"), ""))


def _rows_from_job(job: dict) -> list[dict]:
    eng = job.get("engine")
    r = job.get("result") or {}
    meta = r.get("meta") or notes.job_meta(job)
    p = job.get("params") or {}
    base = {"engine": eng, "job_id": job["id"], "created": job.get("started") or job.get("created"),
            "finished": job.get("finished"), "date": meta.get("date"), "model": _model(job),
            "summary": (r.get("summary") or "")[:300], "excerpt": (r.get("markdown") or "")[:2500],
            "params": json.dumps(p, ensure_ascii=False)[:2000],
            "lessons_used": 1 if (job.get("llm") or {}).get("lessons") else 0}
    out = []
    if eng == "hedgefund" and meta.get("signals"):
        for t, sig in meta["signals"].items():
            if not re.fullmatch(r"[\w.^=\-]+", t or ""):
                continue
            out.append({**base, "id": f"{eng}:{job['id']}:{t}", "ticker": t, "name": notes._name(t), "signal": sig,  # noqa: SLF001
                        "dir": _direction(eng, sig, None), "price_at": (meta.get("prices_at") or {}).get(t),
                        "horizon": None, "fc": None})
        return out
    t = meta.get("ticker")
    fc = None
    horizon = None
    if eng in notes.BAND_ENGINES and meta.get("horizon_end"):
        fc = {k: meta.get(k) for k in ("horizon_end", "forecast_median", "forecast_p10", "forecast_p90", "forecast_change_pct", "p_up")}
        fc["change_pct"] = meta.get("forecast_change_pct")
    if eng == "fingpt" and (p.get("mode") or "forecast") == "forecast":
        horizon = 5
    if eng == "laya":
        horizon = int(p.get("horizon") or 20)
    sig = meta.get("signal") or r.get("signal")
    out.append({**base, "id": f"{eng}:{job['id']}", "ticker": t, "name": notes._name(t) if t else "",  # noqa: SLF001
                "signal": sig if sig else (r.get("summary") or "")[:60],
                "dir": _direction(eng, sig, fc) if t else None, "price_at": meta.get("price_at"),
                "horizon": horizon, "fc": json.dumps(fc, ensure_ascii=False) if fc else None})
    return out


def collect() -> int:
    """Pick up every finished job of every engine (reads /data/<engine>/jobs/*.json). Returns rows added."""
    added = 0
    rows = []
    for eng in ENGINES:
        d = _data() / eng / "jobs"
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            try:
                j = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if j.get("status") != "done":
                continue
            j.setdefault("engine", eng)
            rows.append(j)
    with _conn() as c:
        have = {r[0] for r in c.execute("SELECT job_id FROM analyses")}
    new = [j for j in rows if j["id"] not in have]
    for j in new:
        try:
            for r in _rows_from_job(j):
                with _lock, _conn() as c:
                    cols = ",".join(r)
                    c.execute(f"INSERT OR IGNORE INTO analyses({cols}) VALUES({','.join('?' * len(r))})", list(r.values()))
                added += 1
        except Exception as e:  # noqa: BLE001
            log.info("ledger: skip %s: %s", j.get("id"), e)
    return added


# ------------------------------------------------------------------ check against prices
def _bench(sym: str) -> str | None:
    if sym.endswith((".TW", ".TWO")):
        return "0050.TW"
    if sym.endswith("-USD"):
        return None
    return "^GSPC"


def _closes_after(sym: str, day: str):
    """(close on/before `day`, [(date, close) for the trading days after `day`])."""
    d0 = dt.date.fromisoformat(day)
    df = market.history(sym, start=(d0 - dt.timedelta(days=10)).isoformat(),
                        end=(dt.date.today() + dt.timedelta(days=1)).isoformat())
    if df is None or not len(df):
        return None, []
    pts = [(str(getattr(t, "date", lambda: t)())[:10], float(v)) for t, v in df["Close"].dropna().items()]
    before = [v for d, v in pts if d <= day]
    return (before[-1] if before else None), [(d, v) for d, v in pts if d > day]


def check(today: dt.date | None = None) -> int:
    """Fill in every horizon that has come due. Returns checks written."""
    today = today or dt.date.today()
    n = 0
    with _conn() as c:
        rows = c.execute("SELECT id, engine, date, ticker, dir, price_at, fc FROM analyses WHERE ticker IS NOT NULL "
                         "AND ticker NOT LIKE 'FUND:%' AND date IS NOT NULL").fetchall()
        done = {(a, h) for a, h in c.execute("SELECT analysis_id, h FROM checks")}
    cache: dict = {}

    def series(sym, day):
        k = (sym, day)
        if k not in cache:
            try:
                cache[k] = _closes_after(sym, day)
            except Exception as e:  # noqa: BLE001
                log.info("ledger prices %s: %s", sym, e)
                cache[k] = (None, [])
        return cache[k]

    for aid, eng, day, sym, d, p0, fc in rows:
        want = [h for h in HORIZONS if (aid, h) not in done
                and (dt.date.fromisoformat(day) + dt.timedelta(days=int(h * 1.35))) <= today]
        fcd = json.loads(fc) if fc else None
        if fcd and fcd.get("horizon_end") and (aid, 0) not in done and fcd["horizon_end"] < today.isoformat():
            want.append(0)
        if not want:
            continue
        base, after = series(sym, day)
        if not p0 and base:
            p0 = base
            with _lock, _conn() as c:
                c.execute("UPDATE analyses SET price_at=? WHERE id=? AND price_at IS NULL", (p0, aid))
        if not p0:
            continue
        bsym = _bench(sym)
        bbase, bafter = series(bsym, day) if bsym else (None, [])
        for h in want:
            if h == 0:
                he = fcd["horizon_end"]
                upto = [(dd, v) for dd, v in after if dd <= he]
                if not upto or after[-1][0] < he:
                    continue
                due, p1 = upto[-1]
                lo, hi, med = fcd.get("forecast_p10"), fcd.get("forecast_p90"), fcd.get("forecast_median")
                rec = {"due": due, "price": p1, "ret": (p1 / p0 - 1) * 100, "bench": None, "excess": None,
                       "hit": None if d is None else int((p1 > p0) if d == 1 else (p1 < p0) if d == -1 else abs(p1 / p0 - 1) * 100 < 3),
                       "in_band": None if lo is None or hi is None else int(lo <= p1 <= hi),
                       "err": None if not med else (med / p1 - 1) * 100}
            else:
                if len(after) < h:
                    continue
                due, p1 = after[h - 1]
                ret = (p1 / p0 - 1) * 100
                bret = None
                if bbase and len(bafter) >= 1:
                    bpts = [v for dd, v in bafter if dd <= due]
                    bret = (bpts[-1] / bbase - 1) * 100 if bpts else None
                hit = None
                if d is not None:
                    hit = int(ret > 0) if d == 1 else int(ret < 0) if d == -1 else int(abs(ret) < NEUTRAL_BAND[h])
                ex = None if bret is None or d in (None, 0) else d * (ret - bret)
                rec = {"due": due, "price": p1, "ret": ret, "bench": bret, "excess": ex, "hit": hit, "in_band": None, "err": None}
            with _lock, _conn() as c:
                c.execute("INSERT OR REPLACE INTO checks(analysis_id,h,due,price,ret,bench,excess,hit,in_band,err,checked) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (aid, h, rec["due"], rec["price"], rec["ret"], rec["bench"],
                                                           rec["excess"], rec["hit"], rec["in_band"], rec["err"], _now()))
            n += 1
    return n


# ------------------------------------------------------------------ read
def records(engine: str = "", ticker: str = "", limit: int = 300) -> list[dict]:
    q, args = "SELECT * FROM analyses WHERE 1=1", []
    if engine:
        q += " AND engine=?"
        args.append(engine)
    if ticker:
        q += " AND (ticker LIKE ? OR name LIKE ?)"
        args += [f"%{ticker}%", f"%{ticker}%"]
    q += " ORDER BY created DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        cur = c.execute(q, args)
        cols = [x[0] for x in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        ids = [r["id"] for r in rows]
        chk: dict = {}
        if ids:
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                cur = c.execute(f"SELECT * FROM checks WHERE analysis_id IN ({','.join('?' * len(part))})", part)
                cc = [x[0] for x in cur.description]
                for r in cur.fetchall():
                    d = dict(zip(cc, r))
                    chk.setdefault(d["analysis_id"], {})[str(d["h"])] = d
    today = dt.date.today()
    for r in rows:
        r["checks"] = chk.get(r["id"], {})
        r["fc"] = json.loads(r["fc"]) if r.get("fc") else None
        r.pop("excerpt", None)
        r["engine_name"] = LABEL.get(r["engine"], r["engine"])
        if r.get("date"):
            r["due_est"] = {str(h): (dt.date.fromisoformat(r["date"]) + dt.timedelta(days=int(h * 1.4))).isoformat()
                            for h in HORIZONS if str(h) not in r["checks"]}
        r["scored"] = r.get("dir") is not None or bool(r.get("fc"))
        r["age_days"] = (today - dt.date.fromisoformat(r["date"])).days if r.get("date") else None
    return rows


def stats() -> dict:
    out: dict = {}
    with _conn() as c:
        for eng, n, last in c.execute("SELECT engine, COUNT(*), MAX(created) FROM analyses GROUP BY engine"):
            out[eng] = {"engine": eng, "name": LABEL.get(eng, eng), "n": n, "last": last, "h": {}}
        for eng, h, k, hits, ex, aligned in c.execute(
                "SELECT a.engine, k.h, COUNT(k.hit), SUM(k.hit), AVG(k.excess), AVG(CASE WHEN a.dir IS NOT NULL AND a.dir != 0 "
                "THEN a.dir * k.ret END) FROM checks k JOIN analyses a ON a.id=k.analysis_id WHERE k.h>0 GROUP BY a.engine, k.h"):
            out.setdefault(eng, {"engine": eng, "name": LABEL.get(eng, eng), "n": 0, "h": {}})["h"][str(h)] = {
                "n": k, "hits": hits or 0, "rate": (hits or 0) / k * 100 if k else None,
                "excess": ex, "aligned": aligned}
        for eng, n, inb, err in c.execute(
                "SELECT a.engine, COUNT(k.in_band), SUM(k.in_band), AVG(ABS(k.err)) FROM checks k JOIN analyses a ON "
                "a.id=k.analysis_id WHERE k.h=0 GROUP BY a.engine"):
            if n:
                out.setdefault(eng, {"engine": eng, "name": LABEL.get(eng, eng), "n": 0, "h": {}})["band"] = {
                    "n": n, "in": inb or 0, "rate": (inb or 0) / n * 100, "abs_err": err}
        for eng, created, model in c.execute("SELECT engine, MAX(created), model FROM reviews GROUP BY engine"):
            if eng in out:
                out[eng]["last_review"] = created
    return out


def reviews(engine: str = "", limit: int = 20) -> list[dict]:
    q, args = "SELECT * FROM reviews", []
    if engine:
        q += " WHERE engine=?"
        args.append(engine)
    with _conn() as c:
        cur = c.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit])
        cols = [x[0] for x in cur.description]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ("stats", "patterns", "lessons"):
                d[k] = json.loads(d[k]) if d.get(k) else ([] if k != "stats" else {})
            d["engine_name"] = LABEL.get(d["engine"], d["engine"])
            out.append(d)
    return out


def lessons_text(engine: str, max_items: int = 8) -> str:
    rv = reviews(engine, 1)
    if not rv or not rv[0]["lessons"]:
        return ""
    r = rv[0]
    head = f"（{r['created'][:10]} 依 {r['n']} 筆已核對的分析整理）"
    return head + "\n" + "\n".join(f"- {x}" for x in r["lessons"][:max_items])


# ------------------------------------------------------------------ 檢討 (LLM retrospective)
REVIEW_SYSTEM = (
    "你是投資研究團隊的績效檢討主管。下面是某個 AI 分析代理過去的判斷，以及事後實際股價的核對結果"
    "（h 個交易日後的漲跌、同期大盤、方向對錯）。請誠實檢討，只能根據提供的資料，不要編造。"
    "輸出 JSON：{\"summary\": \"3–5 句整體表現（引用命中率與報酬數字）\", "
    "\"patterns\": [\"最多 5 條：常見的錯誤或做得好的地方，要具體（例如：對高本益比成長股太樂觀、事件驅動的大跌都沒預警）\"], "
    "\"lessons\": [\"最多 8 條：給這個代理下次分析時的具體提醒，用祈使句，每條一句\"], "
    "\"caveats\": \"樣本數與市場環境造成的限制\"}。用繁體中文。")


def _review_input(engine: str, limit: int = 40) -> tuple[str, dict, list]:
    with _conn() as c:
        rows = c.execute(
            "SELECT a.id, a.date, a.ticker, a.name, a.signal, a.dir, a.price_at, a.excerpt, a.fc FROM analyses a "
            "WHERE a.engine=? AND EXISTS(SELECT 1 FROM checks k WHERE k.analysis_id=a.id) ORDER BY a.date DESC LIMIT ?",
            (engine, limit)).fetchall()
        chk: dict = {}
        for aid, h, ret, bench, hit, inb, err in c.execute("SELECT analysis_id, h, ret, bench, hit, in_band, err FROM checks"):
            chk.setdefault(aid, {})[h] = (ret, bench, hit, inb, err)
    lines = []
    for aid, day, t, name, sig, d, p0, ex, fc in rows:
        ck = chk.get(aid, {})
        res = []
        for h in (5, 20, 60):
            if h in ck:
                ret, bench, hit, _, _ = ck[h]
                res.append(f"{h}日 {ret:+.1f}%" + (f"（大盤 {bench:+.1f}%）" if bench is not None else "")
                           + ("" if hit is None else (" ✔" if hit else " ✘")))
        if 0 in ck:
            ret, _, _, inb, err = ck[0]
            res.append(f"到期 {ret:+.1f}%，{'落在' if inb else '超出'} 80% 區間，中位數誤差 {err:+.1f}%" if err is not None else "")
        why = re.sub(r"\s+", " ", re.sub(r"[#>*|`_-]{1,}", " ", ex or ""))[:350]
        lines.append(f"- {day} {t} {name or ''}｜判斷：{sig}｜當時 {p0 if p0 else '—'}｜結果：{'；'.join(res) or '尚未到期'}\n  理由摘錄：{why}")
    st = stats().get(engine, {})
    return "\n".join(lines), st, [r[0] for r in rows]


def write_review(engine: str, llm: dict | None) -> dict:
    body, st, ids = _review_input(engine)
    if not ids:
        raise ValueError("這個代理還沒有已到期、可核對的分析（最快要分析後 5 個交易日）")
    from . import ollama
    head = [f"代理：{LABEL.get(engine, engine)}，已核對 {len(ids)} 筆"]
    for h, s in sorted((st.get("h") or {}).items(), key=lambda x: int(x[0])):
        if s["n"]:
            head.append(f"{h} 日：命中 {s['hits']}/{s['n']}（{s['rate']:.0f}%）" + (f"，平均超額 {s['excess']:+.2f}%" if s.get("excess") is not None else ""))
    if st.get("band"):
        head.append(f"TimesFM 80% 區間覆蓋 {st['band']['in']}/{st['band']['n']}（理想 80%），平均中位數誤差 {st['band']['abs_err']:.1f}%")
    raw = ollama.ask(llm or {}, REVIEW_SYSTEM, "\n".join(head) + "\n\n明細（新到舊）：\n" + body, json_mode=True)
    try:
        j = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        j = {"summary": ollama.strip_think(raw)[:1500], "patterns": [], "lessons": [], "caveats": ""}
    summary = str(j.get("summary") or "").strip() or "（模型沒有回傳可用的檢討內容，請再按一次「AI 檢討」或換一個模型）"
    if j.get("caveats"):
        summary += f"\n\n限制：{str(j['caveats']).strip()}"
    pats = [str(x).strip() for x in (j.get("patterns") or []) if str(x).strip()][:5]
    les = [str(x).strip() for x in (j.get("lessons") or []) if str(x).strip()][:8]
    with _conn() as c:
        span = c.execute(f"SELECT MIN(date), MAX(date) FROM analyses WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()
    note = None
    try:
        if notes.enabled("review"):
            md = [notes.frontmatter({"type": "ai-agent-review", "engine": engine, "created": _now(), "n": len(ids),
                                     "tags": ["finance", "ai-forecast", "檢討"]}),
                  f"# {LABEL.get(engine, engine)} 檢討 {dt.date.today().isoformat()}", "", "\n".join(f"- {x}" for x in head), "",
                  "## 總結", summary, "", "## 觀察到的模式", *[f"- {x}" for x in pats], "", "## 給下次分析的提醒", *[f"- {x}" for x in les], ""]
            rel = f"{notes.FOLDER}/AI 檢討/{dt.date.today().isoformat()} {LABEL.get(engine, engine)}.md"
            notes._write_outbox(rel, "\n".join(md))  # noqa: SLF001
            note = rel
    except Exception as e:  # noqa: BLE001
        log.info("review note failed: %s", e)
    with _lock, _conn() as c:
        cur = c.execute("INSERT INTO reviews(engine,created,period_from,period_to,n,stats,summary,patterns,lessons,model,note) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (engine, _now(), span[0], span[1], len(ids), json.dumps(st, ensure_ascii=False), summary,
                         json.dumps(pats, ensure_ascii=False), json.dumps(les, ensure_ascii=False), (llm or {}).get("model"), note))
        rid = cur.lastrowid
    return next(r for r in reviews(engine, 5) if r["id"] == rid)


def due_for_review(engine: str, min_new: int = 5, min_days: int = 7) -> bool:
    with _conn() as c:
        last = c.execute("SELECT MAX(created) FROM reviews WHERE engine=?", (engine,)).fetchone()[0]
        q = ("SELECT COUNT(DISTINCT k.analysis_id) FROM checks k JOIN analyses a ON a.id=k.analysis_id "
             "WHERE a.engine=? AND k.h>0" + (" AND k.checked > ?" if last else ""))
        n = c.execute(q, (engine, last) if last else (engine,)).fetchone()[0]
    if last and (dt.datetime.now() - dt.datetime.fromisoformat(last)).days < min_days:
        return False
    return n >= min_new


def text_report(days: int = 365) -> str:
    st = stats()
    L = ["【AI 分析紀錄與事後核對】"]
    for eng, s in sorted(st.items(), key=lambda x: -x[1]["n"]):
        parts = [f"{s['name']}：{s['n']} 次"]
        for h in ("5", "20", "60"):
            x = s["h"].get(h)
            if x and x["n"]:
                parts.append(f"{h}日命中 {x['hits']}/{x['n']}（{x['rate']:.0f}%）")
        if s.get("band"):
            parts.append(f"80% 區間覆蓋 {s['band']['rate']:.0f}%")
        L.append("、".join(parts))
    for eng in st:
        rv = reviews(eng, 1)
        if rv:
            L.append(f"\n【{rv[0]['engine_name']} 最近檢討 {rv[0]['created'][:10]}】\n{rv[0]['summary']}")
            L += [f"- {x}" for x in rv[0]["lessons"]]
    recent = records(limit=15)
    if recent:
        L.append("\n【最近 15 筆分析】")
        for r in recent:
            c20 = r["checks"].get("20") or r["checks"].get("5")
            res = "" if not c20 else f"｜{c20['h']}日 {c20['ret']:+.1f}%{'' if c20['hit'] is None else (' ✔' if c20['hit'] else ' ✘')}"
            L.append(f"- {str(r['created'])[:16].replace('T', ' ')} {r['engine_name']} {r.get('ticker') or ''} {r.get('name') or ''}：{r.get('signal') or '—'}{res}")
    return "\n".join(L)


def loop_forever(get_llm, interval_min: float = 30):
    """Collect + check every 30 minutes; auto-檢討 an agent once it has ≥5 newly checked calls and a week has passed."""
    import time
    time.sleep(float(os.environ.get("FT_LEDGER_DELAY", "180")))
    while True:
        try:
            collect()
            check()
            if os.environ.get("FT_LEDGER_AUTO_REVIEW", "1") != "0":
                for eng in [e for e in ENGINES if e in stats()]:
                    if eng in ("qlib", "finrl", "research"):
                        continue
                    if due_for_review(eng):
                        try:
                            write_review(eng, get_llm())
                        except Exception as e:  # noqa: BLE001
                            log.info("auto review %s: %s", eng, e)
        except Exception as e:  # noqa: BLE001
            log.warning("ledger loop: %s", e)
        time.sleep(interval_min * 60)

