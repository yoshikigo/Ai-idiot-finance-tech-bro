"""AI analysis / forecast notes for the Obsidian vault (folder `finance/` by default).

Engines never touch the vault directly. Every finished job is rendered to Markdown and written
to an *outbox* on the data drive (/data/obsidian-outbox/<vault-relative path>); a host-side
applier (scripts/ft-obsidian-apply.sh, systemd .path + .timer, runs as your user) moves the files
into the vault only when the vault drive is mounted and looks like a vault (.obsidian/ exists).
That keeps the containers off the vault and makes an unmounted drive harmless.

Also generates:
  finance/AI 預測回顧.md        daily scorecard: did past forecasts come true? (overwritten)
  finance/世界事件/YYYY-MM-DD.md  daily world-events note from God's Eye View
  finance/AI 預測索引.md         Dataview index (written once; edit freely)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("ft.notes")
DATA = Path(os.environ.get("FT_DATA", "/data"))
OUTBOX = DATA / "obsidian-outbox"
FOLDER = os.environ.get("FT_OBSIDIAN_FOLDER", "finance").strip("/") or "finance"
PUBLIC_URL = os.environ.get("FT_PUBLIC_URL", "").rstrip("/")  # https://myhost.<tailnet>.ts.net:8130
SETTINGS = DATA / "settings.json"
STATE = DATA / "obsidian-state.json"

ENGINE_LABEL = {"tradingagents": "TradingAgents", "hedgefund": "投資大師", "qlib": "Qlib", "finrl": "FinRL",
                "fingpt": "FinGPT", "timesfm": "TimesFM", "kronos": "Kronos", "laya": "Laya", "research": "策略回測"}
FORECAST_ENGINES = ("tradingagents", "fingpt", "timesfm", "kronos", "hedgefund", "laya")
BAND_ENGINES = ("timesfm", "kronos")  # price forecasts with a P10–P90 band, scored at horizon_end
_BAD = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


def enabled(engine: str) -> bool:
    if os.environ.get("FT_OBSIDIAN", "1") in ("0", "false", "no"):
        return False
    try:
        s = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    except (OSError, json.JSONDecodeError):
        s = {}
    if not s.get("obsidian_auto", True):
        return False
    engines = s.get("obsidian_engines")
    return engines is None or engine in ("review", "world") or engine in engines


def safe_name(s: str, limit: int = 90) -> str:
    s = _BAD.sub(" ", str(s)).strip().strip(".")
    s = re.sub(r"\s+", " ", s)
    return s[:limit].rstrip() or "untitled"


def _yaml(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:.6g}" if isinstance(v, float) else str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_yaml(x) for x in v) + "]"
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?", v):
        return v  # bare ISO date → Obsidian Properties / Dataview treat it as a date
    return json.dumps(str(v), ensure_ascii=False)


def frontmatter(d: dict) -> str:
    return "---\n" + "\n".join(f"{k}: {_yaml(v)}" for k, v in d.items() if v not in (None, "", [])) + "\n---\n"


def md_table(t: dict, max_rows: int = 40) -> str:
    cols = [str(c) for c in t.get("columns", [])]
    if not cols:
        return ""
    esc = lambda x: str("" if x is None else x).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    out = [f"**{t.get('title', '')}**", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in t.get("rows", [])[:max_rows]:
        out.append("| " + " | ".join(esc(x)[:300] for x in r) + " |")
    if len(t.get("rows", [])) > max_rows:
        out.append(f"\n…共 {len(t['rows'])} 列，只列前 {max_rows} 列")
    return "\n".join(out)


def _ticker_of(job: dict) -> tuple[str | None, list[str]]:
    p = job.get("params") or {}
    t = p.get("ticker")
    ts = p.get("tickers")
    many = [x.strip() for x in str(ts).replace("，", ",").split(",") if x.strip()] if ts else []
    if t:
        return str(t).strip(), [str(t).strip()]
    return (many[0] if len(many) == 1 else None), many


def _name(sym: str | None) -> str:
    if not sym:
        return ""
    try:
        from . import twmarket
        return twmarket.zh_name(sym) or ""
    except Exception:  # noqa: BLE001
        return ""


def price_on(sym: str, day: str) -> float | None:
    """Close on or before `day` (YYYY-MM-DD)."""
    if not sym or sym.startswith("FUND:"):
        return None
    try:
        from . import market
        d = dt.date.fromisoformat(day[:10])
        df = market.history(sym, start=(d - dt.timedelta(days=10)).isoformat(), end=(d + dt.timedelta(days=1)).isoformat())
        return float(df["Close"].iloc[-1]) if len(df) else None
    except Exception:  # noqa: BLE001
        return None


def job_meta(job: dict) -> dict:
    """Facts for review & frontmatter, computed when the job finishes (stored in result.meta)."""
    eng = job.get("engine")
    p = job.get("params") or {}
    r = job.get("result") or {}
    ticker, tickers = _ticker_of(job)
    day = str(p.get("date") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        day = (job.get("finished") or job.get("created") or dt.date.today().isoformat())[:10]
    meta = {"date": day, "ticker": ticker, "tickers": tickers, "name": _name(ticker), "signal": r.get("signal")}
    if ticker:
        meta["price_at"] = r.get("last") or price_on(ticker, day)
    fc = r.get("forecast_summary")
    if eng in BAND_ENGINES and fc:
        meta.update({"horizon_end": fc.get("horizon_end"), "forecast_median": fc.get("median_end"),
                     "forecast_p10": fc.get("p10_end"), "forecast_p90": fc.get("p90_end"),
                     "forecast_change_pct": fc.get("change_pct"), "p_up": fc.get("p_up"), "price_at": fc.get("last")})
    if eng == "hedgefund":
        for t in r.get("tables") or []:
            if t.get("title") == "共識":
                meta["signals"] = {str(row[0]).split("（")[0]: row[2] for row in t.get("rows", []) if len(row) > 2}
        meta["prices_at"] = {t: price_on(t, day) for t in tickers[:10]}
    return meta


def render_job(job: dict) -> tuple[str, str]:
    """→ (vault-relative path, markdown)."""
    eng = job.get("engine", "engine")
    r = job.get("result") or {}
    meta = r.get("meta") or job_meta(job)
    label = ENGINE_LABEL.get(eng, eng)
    who = meta.get("ticker") or (", ".join(meta.get("tickers") or [])[:40]) or (job.get("params") or {}).get("universe") \
        or ((r.get("research") or {}).get("spec") or {}).get("name") or ""
    title = f"{meta['date']} {who} {meta.get('name') or ''} · {label}".replace("  ", " ")
    month = meta["date"][:7]
    path = f"{FOLDER}/AI 預測/{month}/{safe_name(title)} ({job['id'][-6:]}).md"
    tags = ["finance", "ai-forecast", f"engine/{eng}"] + [f"ticker/{safe_name(t.split('.')[0], 20).replace(' ', '_')}" for t in (meta.get("tickers") or [])[:8]]
    fm = {
        "type": "ai-forecast", "engine": eng, "engine_name": label, "ticker": meta.get("ticker"),
        "tickers": meta.get("tickers") if len(meta.get("tickers") or []) > 1 else None, "name": meta.get("name"),
        "date": meta["date"], "signal": meta.get("signal"), "summary": r.get("summary"), "price_at": meta.get("price_at"),
        "horizon_end": meta.get("horizon_end"), "forecast_median": meta.get("forecast_median"),
        "forecast_p10": meta.get("forecast_p10"), "forecast_p90": meta.get("forecast_p90"),
        "forecast_change_pct": meta.get("forecast_change_pct"), "p_up": meta.get("p_up"),
        "model": (job.get("llm") or {}).get("model"), "job_id": job.get("id"),
        "world_events": [e.get("title") for e in (r.get("world") or {}).get("events", [])][:8] or None,
        "created": (job.get("finished") or dt.datetime.now().isoformat(timespec="seconds"))[:19],
        "source": "FT 金融終端", "tags": tags,
    }
    body = [frontmatter(fm), f"# {title}", ""]
    if r.get("summary"):
        sig = r.get("signal")
        body += [f"> **結論**：{r['summary']}" + (f"（{sig}）" if sig and str(sig) not in r["summary"] else ""), ""]
    if PUBLIC_URL:
        body += [f"[在 FT 金融終端開啟]({PUBLIC_URL}/#job={eng}:{job['id']})", ""]
    md = r.get("markdown") or ""
    md = re.sub(r"<details>.*?</details>", "", md, flags=re.S)  # prompts are noise in the vault
    md = re.sub(r"^# .*\n", "", md, count=1)  # we already have a title
    body += [md.strip(), ""]
    for t in r.get("tables") or []:
        body += [md_table(t), ""]
    w = r.get("world") or {}
    if w.get("digest"):
        body += ["> [!info]- 分析時的現實世界訊號（God's Eye View）", "> " + w["digest"].replace("\n", "\n> "), ""]
    p = {k: v for k, v in (job.get("params") or {}).items() if k != "language"}
    body += ["---", f"參數：`{json.dumps(p, ensure_ascii=False)}`　模型：`{fm['model'] or '—'}`　工作：`{eng} {job['id']}`",
             "", "_AI 產生的研究筆記，不構成投資建議。_", ""]
    return path, "\n".join(body)


def _write_outbox(rel: str, text: str) -> Path:
    dest = OUTBOX / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    # the host applier's .path unit watches this file (inotify is not recursive)
    (OUTBOX / ".ft-stamp").write_text(dt.datetime.now().isoformat(timespec="seconds"))
    return dest


def write_job_note(job: dict, force: bool = False) -> str | None:
    """Render + queue a note. Returns the vault-relative path (or None if disabled)."""
    if not force and not enabled(job.get("engine", "")):
        return None
    rel, text = render_job(job)
    _write_outbox(rel, text)
    ensure_index()
    return rel


def ensure_index():
    st = _state()
    if st.get("index_written"):
        return
    rel = f"{FOLDER}/AI 預測索引.md"
    _write_outbox(rel, INDEX_MD.replace("{FOLDER}", FOLDER))
    st["index_written"] = dt.datetime.now().isoformat(timespec="seconds")
    _save_state(st)


def _state() -> dict:
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(st: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))


INDEX_MD = """---
type: index
tags: [finance]
---
# AI 預測索引

FT 金融終端每做完一次 AI 分析就會在 `{FOLDER}/AI 預測/` 新增一篇筆記；每天更新 [[AI 預測回顧]]。
這篇只會建立一次，可以自由修改（需要 Dataview 外掛，Compass 已內建）。

## 最近 30 筆
```dataview
TABLE engine_name AS 引擎, ticker AS 標的, name AS 名稱, signal AS 結論, price_at AS 當時價格, forecast_change_pct AS "預測漲跌%"
FROM "{FOLDER}/AI 預測"
WHERE type = "ai-forecast"
SORT date DESC, created DESC
LIMIT 30
```

## 依標的
```dataview
TABLE rows.date AS 日期, rows.engine_name AS 引擎, rows.signal AS 結論
FROM "{FOLDER}/AI 預測"
WHERE ticker
GROUP BY ticker
```

## TimesFM／Kronos 預測到期
```dataview
TABLE ticker AS 標的, price_at AS 起點, forecast_median AS 中位數, forecast_p10 AS P10, forecast_p90 AS P90, horizon_end AS 到期
FROM "{FOLDER}/AI 預測"
WHERE (engine = "timesfm" OR engine = "kronos") AND horizon_end
SORT horizon_end ASC
```
"""


# ---------------------------------------------------------------- review scorecard
def _load_jobs(engines=FORECAST_ENGINES, days: int = 365) -> list[dict]:
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    for e in engines:
        d = DATA / e / "jobs"
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                j = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if j.get("status") == "done" and (j.get("created") or "")[:10] >= cutoff:
                out.append(j)
    return out


_UP = re.compile(r"buy|overweight|看多|bull|up|上漲|買進|加碼", re.I)
_DOWN = re.compile(r"sell|underweight|short|看空|bear|down|下跌|賣出|放空|減碼", re.I)


def direction(signal: str | None) -> int | None:
    if not signal:
        return None
    s = str(signal)
    if _DOWN.search(s):
        return -1
    if _UP.search(s):
        return 1
    if re.search(r"hold|wait|中性|neutral|flat|持平|持有|觀望", s, re.I):
        return 0
    return None


def review(now_prices: dict | None = None, today: dt.date | None = None) -> dict:
    """Score past forecasts against what prices actually did. Returns rows + per-engine stats."""
    today = today or dt.date.today()
    jobs = _load_jobs()
    now_prices = dict(now_prices or {})
    rows, stats = [], {}

    def now_price(sym):
        if sym not in now_prices:
            try:
                from . import market
                q = market.quotes([sym])[0]
                now_prices[sym] = q.get("last")
            except Exception:  # noqa: BLE001
                now_prices[sym] = None
        return now_prices[sym]

    for j in jobs:
        eng = j["engine"]
        r = j.get("result") or {}
        meta = r.get("meta") or {}
        items = []
        if eng == "hedgefund" and meta.get("signals"):
            for t, sig in meta["signals"].items():
                items.append((t, sig, (meta.get("prices_at") or {}).get(t)))
        elif meta.get("ticker"):
            items.append((meta["ticker"], meta.get("signal") or r.get("signal"), meta.get("price_at")))
        for t, sig, p0 in items:
            if not p0 or t.startswith("FUND:"):
                continue
            row = {"engine": eng, "date": meta.get("date"), "ticker": t, "name": _name(t), "signal": sig, "price_at": p0,
                   "job_id": j["id"]}
            if eng in BAND_ENGINES and meta.get("horizon_end"):
                he = meta["horizon_end"]
                row["horizon_end"] = he
                if he > today.isoformat():
                    row["status"] = "未到期"
                    p1 = now_price(t)
                else:
                    p1 = price_on(t, he)
                    row["status"] = "已到期"
                    if p1 is not None and meta.get("forecast_p10") is not None:
                        row["in_band"] = meta["forecast_p10"] <= p1 <= meta["forecast_p90"]
                        row["err_pct"] = (meta["forecast_median"] / p1 - 1) * 100
            else:
                p1 = now_price(t)
                age = (today - dt.date.fromisoformat(meta["date"])).days if meta.get("date") else 0
                row["status"] = f"{age} 天"
            if p1 is None:
                continue
            ret = (p1 / p0 - 1) * 100
            row["price_now"], row["ret_pct"] = p1, ret
            d = direction(sig) if eng not in BAND_ENGINES else (1 if (meta.get("forecast_change_pct") or 0) > 1 else -1 if (meta.get("forecast_change_pct") or 0) < -1 else 0)
            if d is not None:
                row["hit"] = (ret > 0) if d == 1 else (ret < 0) if d == -1 else (abs(ret) < 3)
            rows.append(row)
            s = stats.setdefault(eng, {"n": 0, "hits": 0, "scored": 0, "band_n": 0, "band_in": 0})
            s["n"] += 1
            if "hit" in row and (eng not in BAND_ENGINES or row.get("status") == "已到期") and (eng in BAND_ENGINES or (today - dt.date.fromisoformat(row["date"])).days >= 5):
                s["scored"] += 1
                s["hits"] += int(row["hit"])
            if "in_band" in row:
                s["band_n"] += 1
                s["band_in"] += int(row["in_band"])
    rows.sort(key=lambda x: (x.get("date") or "", x["engine"]), reverse=True)
    return {"rows": rows, "stats": stats, "generated": dt.datetime.now().isoformat(timespec="minutes")}


def render_review(rv: dict) -> str:
    fm = frontmatter({"type": "ai-forecast-review", "generated": rv["generated"], "tags": ["finance", "ai-forecast"]})
    out = [fm, "# AI 預測回顧", "", f"> 自動產生於 {rv['generated'].replace('T', ' ')}，每天覆寫——**不要在這篇寫筆記**。",
           "> 方向判斷：看多／Buy／Overweight 之後上漲、看空／Sell／Underweight 之後下跌、中性／Hold 之後漲跌 < 3% 算對；"
           "訊號滿 5 天才計分，TimesFM 以到期日收盤計分。", "", "## 各引擎命中率", "",
           "| 引擎 | 預測數 | 已計分 | 方向命中 | TimesFM 落在 80% 區間 |", "|---|---|---|---|---|"]
    for eng, s in sorted(rv["stats"].items()):
        hit = f"{s['hits']}/{s['scored']}（{s['hits'] / s['scored'] * 100:.0f}%）" if s["scored"] else "—"
        band = f"{s['band_in']}/{s['band_n']}（{s['band_in'] / s['band_n'] * 100:.0f}%，理想約 80%）" if s["band_n"] else "—"
        out.append(f"| {ENGINE_LABEL.get(eng, eng)} | {s['n']} | {s['scored']} | {hit} | {band} |")
    out += ["", "## 明細（最近 100 筆）", "", "| 日期 | 引擎 | 標的 | 結論 | 當時 | 現在／到期 | 報酬 | 對錯 | 狀態 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rv["rows"][:100]:
        ok = "—" if "hit" not in r else ("✅" if r["hit"] else "❌")
        if "in_band" in r:
            ok += " 區間內" if r["in_band"] else " 區間外"
        out.append(f"| {r['date']} | {ENGINE_LABEL.get(r['engine'], r['engine'])} | {r['ticker']} {r.get('name') or ''} | "
                   f"{str(r.get('signal') or '—')[:20]} | {r['price_at']:,.2f} | {r['price_now']:,.2f} | {r['ret_pct']:+.2f}% | {ok} | {r['status']} |")
    if not rv["rows"]:
        out.append("| — | — | 還沒有可計分的預測 | | | | | | |")
    out += ["", "_AI 預測僅供研究，過去命中率不代表未來。_", ""]
    return "\n".join(out)


def write_review(now_prices: dict | None = None) -> str:
    rv = review(now_prices)
    rel = f"{FOLDER}/AI 預測回顧.md"
    _write_outbox(rel, render_review(rv))
    return rel


def write_world_day(day: str, digest_text: str, events: list[dict]) -> str:
    rel = f"{FOLDER}/世界事件/{day}.md"
    fm = frontmatter({"type": "world-events", "date": day, "events": [e["title"] for e in events][:20],
                      "source": "God's Eye View", "tags": ["finance", "world-events"]})
    body = [fm, f"# 世界事件 {day}", ""]
    if events:
        body += ["## 值得注意", ""] + [f"- **{e['title']}**　{e.get('detail') or ''}" for e in events] + [""]
    else:
        body += ["今天沒有超過門檻的事件。", ""]
    body += ["## 當日最後一次快照", "", digest_text, ""]
    _write_outbox(rel, "\n".join(body))
    return rel
