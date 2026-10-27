"""策略模擬追蹤 — paper-trade a 策略研究 strategy forward from the day you start it.

Every trading day, once new prices are in, each active track
  1. on its rebalance days takes the list the strategy selects with the data available that day and *freezes* it
     ("issued"): later data revisions never rewrite what it said it would buy;
  2. replays the paper portfolio from its start with the same simulator as the backtest (next-day-open execution,
     手續費／證交稅／滑價, stop-loss / take-profit);
  3. compares the result with what the backtest led you to expect (daily mean / volatility of the backtest at the
     time you started → a 90% band for the elapsed number of days) and with the benchmark, at 20 / 60 / 120 / 250
     trading days — the way 分析紀錄 checks AI calls once their horizon has passed;
  4. writes Obsidian notes: finance/策略追蹤/<策略>/總覽.md (rewritten daily) and one note per rebalance
     (<date> 換股.md, written once).

Tracks live in <data>/research/tracks/<id>.json. No LLM anywhere in this loop.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
import uuid

import numpy as np
import pandas as pd

from .. import notes
from . import backtest as B, fields as F, store

ROOT = store.ROOT / "tracks"
HORIZONS = (20, 60, 120, 250)
MAX_ACTIVE = 20
_lock = threading.RLock()


def _path(tid: str):
    return ROOT / f"{tid}.json"


def _load(tid: str) -> dict:
    return json.loads(_path(tid).read_text())


def _save(t: dict):
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _path(t["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(B.py(t), ensure_ascii=False))
    tmp.replace(_path(t["id"]))


def all_tracks() -> list[dict]:
    if not ROOT.exists():
        return []
    out = []
    for f in sorted(ROOT.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda t: t.get("created", ""), reverse=True)


def _ncdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ---------------------------------------------------------------- create
def create(spec: dict, name: str | None = None) -> dict:
    with _lock:
        active = [t for t in all_tracks() if t.get("status") == "active"]
        if len(active) >= MAX_ACTIVE:
            raise B.SpecError(f"最多同時追蹤 {MAX_ACTIVE} 個策略，先暫停或刪除一些")
        s = B.normalize(spec)
        P = B.prepare(s)                       # the backtest you are looking at = the expectation
        eq = B.simulate(s, P["cal"], P["rebal"], P["cond"], P["score"], P["codes"], P["end"])[0]
        lr = np.log(eq).diff().dropna()
        bench = B.benchmark(s["benchmark"], eq.index)
        bm = B.metrics(eq, bench, [], 0.0)
        t0 = P["cal"][-1]
        names = {t["name"] for t in all_tracks()}
        nm = (name or s["name"] or "未命名策略").strip()
        base, k = nm, 2
        while nm in names:
            nm, k = f"{base} ({k})", k + 1
        t = {"id": dt.datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6], "name": nm, "spec": s,
             "created": dt.datetime.now().isoformat(timespec="seconds"), "t0": t0.date().isoformat(), "status": "active",
             "expect": {"mu": float(lr.mean()), "sd": float(lr.std()), "from": eq.index[0].date().isoformat(),
                        "to": eq.index[-1].date().isoformat(), "cagr": bm.get("cagr"), "sharpe": bm.get("sharpe"),
                        "mdd": bm.get("mdd"), "bench_cagr": bm.get("bench_cagr")},
             "issued": {}, "notes_written": [], "last": None}
        update(t, force=True)
        return t


# ---------------------------------------------------------------- daily update
def _schedule(s, cal, t0) -> list[pd.Timestamp]:
    """Signal days from t0 on. The schedule is built on the calendar extended with future weekdays, so the last
    day of the data is not mistaken for the month's (week's) last trading day while the month is still running."""
    if cal[-1] < t0:
        return [t0]
    ext = cal.append(pd.bdate_range(cal[-1] + pd.Timedelta(days=1), cal[-1] + pd.Timedelta(days=45)))
    sched = B.rebalance_dates(ext, s["rebalance"], t0, ext[-1])
    return [t0] + [d for d in sched if t0 < d <= cal[-1]]


def next_rebalance(s, last: pd.Timestamp) -> str | None:
    fut = pd.bdate_range(last + pd.Timedelta(days=1), last + pd.Timedelta(days=200))
    cal = pd.DatetimeIndex([last]).append(fut)
    d = [x for x in B.rebalance_dates(cal, s["rebalance"], last + pd.Timedelta(days=1), cal[-1]) if x > last]
    return d[0].date().isoformat() if d else None


def update(t: dict, force: bool = False) -> dict:
    """Recompute one track from its start to the latest data; writes Obsidian notes when something is new."""
    s = t["spec"]
    cal = F.calendar()
    t0 = pd.Timestamp(t["t0"])
    if not len(cal) or cal[-1] < t0:
        return t
    last = cal[-1].date().isoformat()
    if not force and t.get("last") and t["last"].get("date") == last and t["last"].get("v") == store.version():
        return t
    codes = F._prices("close").columns
    _, cond, score = B.select(s, cal, codes)
    liquidity = F._prices("value").reindex(index=cal, columns=codes).rolling(20, min_periods=5).mean()
    rebal = _schedule(s, cal, t0)
    new_issue = []
    for d in rebal:  # freeze what the strategy said on each signal day
        k = d.date().isoformat()
        if k not in t["issued"]:
            t["issued"][k] = B.picks_on(d, cond, score, s, liquidity)
            new_issue.append(k)
    fixed = {pd.Timestamp(k): v for k, v in t["issued"].items()}
    if len(cal[cal >= t0]) >= 2:
        eq, trades, n_hold, _, _, shares = B.simulate(s, cal, rebal, cond, score, codes, None, fixed=fixed)
    else:
        eq, trades, shares = pd.Series([1.0], index=[t0]), [], {}
    bench = B.benchmark(s["benchmark"], eq.index)
    days = len(eq) - 1
    e = t["expect"]
    adj = F._prices("adj")
    close = F._prices("close")
    V = float(eq.iloc[-1])
    hold = []
    for c, sh in shares.items():
        px = adj.at[cal[-1], c] if c in adj.columns else np.nan
        if not np.isfinite(px):
            continue
        since = t["t0"]  # entry = first signal day of the current holding streak
        streak = [k for k in sorted(t["issued"]) if k <= last]
        for k in reversed(streak):
            if c in t["issued"][k]:
                since = k
            else:
                break
        ex_i = min(int(cal.searchsorted(pd.Timestamp(since))) + 1, len(cal) - 1)
        e_px = F._prices("adj_open").reindex(index=cal, columns=[c]).iloc[ex_i, 0] if c in adj.columns else np.nan
        hold.append({"code": c, "name": B._name(c), "weight": round(float(sh * px / V * 100), 1) if V else None,
                     "close": round(float(close.at[cal[-1], c]), 2) if c in close.columns and pd.notna(close.at[cal[-1], c]) else None,
                     "since": since, "ret": round(float(px / e_px - 1) * 100, 2) if np.isfinite(e_px) and e_px else None})
    hold.sort(key=lambda x: -(x["weight"] or 0))
    # what to do next
    today_sig = last in t["issued"] and cal[-1] in rebal
    prev_keys = [k for k in sorted(t["issued"]) if k < last]
    prev = set(t["issued"][prev_keys[-1]]) if prev_keys else set()
    action = None
    if today_sig:
        now = t["issued"][last]
        action = {"date": last, "buy": [[c, B._name(c)] for c in now if c not in prev],
                  "sell": [[c, B._name(c)] for c in prev if c not in now], "keep": [[c, B._name(c)] for c in now if c in prev]}
    checks = []
    lr = np.log(eq).diff().dropna()
    for h in HORIZONS:
        if days < h:
            checks.append({"h": h, "due_in": h - days})
            continue
        act = float(eq.iloc[h] / eq.iloc[0] - 1) * 100
        med = (math.exp(e["mu"] * h) - 1) * 100
        lo = (math.exp(e["mu"] * h - 1.645 * e["sd"] * math.sqrt(h)) - 1) * 100
        hi = (math.exp(e["mu"] * h + 1.645 * e["sd"] * math.sqrt(h)) - 1) * 100
        z = (math.log(1 + act / 100) - e["mu"] * h) / (e["sd"] * math.sqrt(h)) if e["sd"] > 0 else 0.0
        b = float(bench.iloc[h] / bench.iloc[0] - 1) * 100 if bench is not None and len(bench) > h else None
        checks.append({"h": h, "date": eq.index[h].date().isoformat(), "actual": round(act, 2), "expected": round(med, 2),
                       "lo": round(lo, 2), "hi": round(hi, 2), "pct": round(_ncdf(z) * 100), "bench": None if b is None else round(b, 2),
                       "verdict": "低於預期" if act < lo else "高於預期" if act > hi else "符合預期",
                       "vs_bench": None if b is None else ("贏過基準" if act > b else "輸給基準")})
    ret = (V - 1) * 100
    bret = float(bench.iloc[-1] / bench.iloc[0] - 1) * 100 if bench is not None and len(bench) else None
    mu_d, sd_d = e["mu"] * days, e["sd"] * math.sqrt(max(days, 1))
    curve = [{"t": d.date().isoformat(), "v": round(float(v), 5),
              "b": round(float(bench.get(d, np.nan) / bench.iloc[0]), 5) if bench is not None and d in bench.index else None,
              "lo": round(math.exp(e["mu"] * k - 1.645 * e["sd"] * math.sqrt(k)), 5) if k else 1.0,
              "mid": round(math.exp(e["mu"] * k), 5),
              "hi": round(math.exp(e["mu"] * k + 1.645 * e["sd"] * math.sqrt(k)), 5) if k else 1.0}
             for k, (d, v) in enumerate(eq.items())]
    t["last"] = {"date": last, "v": store.version(), "days": days, "ret": round(ret, 2), "bench_ret": None if bret is None else round(bret, 2),
                 "expected": round((math.exp(mu_d) - 1) * 100, 2), "lo": round((math.exp(mu_d - 1.645 * sd_d) - 1) * 100, 2) if days else 0.0,
                 "hi": round((math.exp(mu_d + 1.645 * sd_d) - 1) * 100, 2) if days else 0.0,
                 "mdd": round(float((eq / eq.cummax() - 1).min()) * 100, 2),
                 "vol": round(float(lr.std() * math.sqrt(252) * 100), 1) if len(lr) > 5 else None,
                 "holdings": hold, "action": action, "next_rebalance": next_rebalance(s, cal[-1]),
                 "checks": checks, "trades": trades[-100:], "curve": curve[-1500:]}
    lt = t["last"]
    lt["status"] = ("低於預期" if days and ret < lt["lo"] else "高於預期" if days and ret > lt["hi"] else "符合預期") if days else "剛開始"
    t["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    try:
        write_notes(t, new_issue)
    except Exception as ex:  # noqa: BLE001  notes never block tracking
        t["note_error"] = str(ex)[:200]
    _save(t)
    return t


def update_all(force: bool = False) -> int:
    n = 0
    with _lock:
        for t in all_tracks():
            if t.get("status") != "active":
                continue
            try:
                update(t, force)
                n += 1
            except Exception as e:  # noqa: BLE001
                t["error"] = f"{type(e).__name__}: {e}"[:300]
                _save(t)
    return n


def action(tid: str, what: str) -> dict | None:
    with _lock:
        t = _load(tid)
        if what == "delete":
            _path(tid).unlink(missing_ok=True)
            return None
        if what in ("pause", "resume"):
            t["status"] = "paused" if what == "pause" else "active"
            _save(t)
        if what == "refresh":
            update(t, force=True)
        return t


def loop_forever(stop: threading.Event):
    """Re-run the tracks whenever the research data publishes a new version (prices land ~15:00–16:00 Taipei)."""
    seen = None
    while not stop.is_set():
        try:
            v = store.version()
            if v != seen:
                update_all()
                seen = v
        except Exception:  # noqa: BLE001
            pass
        stop.wait(600)


# ---------------------------------------------------------------- Obsidian
def folder(t: dict) -> str:
    return f"{notes.FOLDER}/策略追蹤/{notes.safe_name(t['name'], 60)}"


def _fmt(v, d=2, pct=True):
    return "—" if v is None else (f"{v:+.{d}f}%" if pct else f"{v:.{d}f}")


def write_notes(t: dict, new_issue: list[str]):
    if not notes.enabled("research"):
        return
    lt, s, e = t["last"], t["spec"], t["expect"]
    base = folder(t)
    for k in new_issue:  # one note per signal day, written once
        rel = f"{base}/{k} 換股.md"
        if rel in t["notes_written"]:
            continue
        now = t["issued"][k]
        keys = sorted(t["issued"])
        i = keys.index(k)
        prev = set(t["issued"][keys[i - 1]]) if i else set()
        fm = {"type": "strategy-signal", "strategy": t["name"], "track_id": t["id"], "date": k, "n": len(now),
              "tags": ["finance", "strategy-track"]}
        body = [notes.frontmatter(fm), f"# {t['name']}｜{k} 換股訊號", "",
                f"> 用 {k} 收盤為止的資料選出、隔天開盤成交。這份名單之後不會被改寫。", "",
                "## 買進", ""] + [f"- {c} {B._name(c)}" for c in now if c not in prev] + ["", "## 賣出", ""] + \
               [f"- {c} {B._name(c)}" for c in prev if c not in now] + ["", "## 續抱", ""] + \
               [f"- {c} {B._name(c)}" for c in now if c in prev] + ["", f"[[{base}/總覽|回到總覽]]", ""]
        notes._write_outbox(rel, "\n".join(body))  # noqa: SLF001
        t["notes_written"].append(rel)
    fm = {"type": "strategy-track", "strategy": t["name"], "track_id": t["id"], "start": t["t0"], "date": lt["date"],
          "days": lt["days"], "return": lt["ret"], "bench_return": lt["bench_ret"], "expected": lt["expected"],
          "band_low": lt["lo"], "band_high": lt["hi"], "status": lt["status"], "state": t["status"],
          "tags": ["finance", "strategy-track"]}
    act = lt.get("action")
    body = [notes.frontmatter(fm), f"# 策略追蹤：{t['name']}", "",
            f"> 從 {t['t0']} 開始模擬（不花真錢），到 {lt['date']} 共 {lt['days']} 個交易日。狀態：**{lt['status']}**。", "",
            "## 今天", ""]
    if act:
        body += [f"今天（{act['date']}）是換股日，明天開盤：",
                 "- 買進：" + ("、".join(f"{c} {n}" for c, n in act["buy"]) or "無"),
                 "- 賣出：" + ("、".join(f"{c} {n}" for c, n in act["sell"]) or "無"),
                 "- 續抱：" + ("、".join(f"{c} {n}" for c, n in act["keep"]) or "無"), ""]
    else:
        body += [f"持股不變。下次換股約在 {lt.get('next_rebalance') or '—'}（{B.REBALANCE.get(s['rebalance'], s['rebalance'])}）。", ""]
    body += ["## 實際 vs 回測預期", "",
             "| | 模擬實際 | 回測預期（中位） | 預期 90% 範圍 | 基準 " + s["benchmark"] + " |", "|---|---:|---:|---:|---:|",
             f"| 累計報酬 | {_fmt(lt['ret'])} | {_fmt(lt['expected'])} | {_fmt(lt['lo'])} ～ {_fmt(lt['hi'])} | {_fmt(lt['bench_ret'])} |", "",
             f"回測（{e['from']} ～ {e['to']}）：年化 {_fmt(e.get('cagr'))}、夏普 {_fmt(e.get('sharpe'), pct=False)}、最大回撤 {_fmt(e.get('mdd'))}。"
             f"模擬至今最大回撤 {_fmt(lt['mdd'])}。", "",
             "### 到期核對", "", "| 交易日 | 日期 | 實際 | 預期 | 90% 範圍 | 落點 | 基準 | 結果 |", "|---:|---|---:|---:|---|---:|---:|---|"]
    for c in lt["checks"]:
        if "due_in" in c:
            body.append(f"| {c['h']} | 還有 {c['due_in']} 個交易日 | | | | | | |")
        else:
            body.append(f"| {c['h']} | {c['date']} | {_fmt(c['actual'])} | {_fmt(c['expected'])} | {_fmt(c['lo'])} ～ {_fmt(c['hi'])} | "
                        f"第 {c['pct']} 百分位 | {_fmt(c['bench'])} | {c['verdict']}、{c.get('vs_bench') or ''} |")
    body += ["", "## 目前持股", "", "| 代號 | 名稱 | 權重 | 收盤 | 持有起 | 報酬 |", "|---|---|---:|---:|---|---:|"]
    body += [f"| {h['code']} | {h['name']} | {h['weight']}% | {h['close'] or '—'} | {h['since']} | {_fmt(h['ret'])} |" for h in lt["holdings"]]
    body += ["", "## 換股紀錄", ""] + [f"- [[{r[:-3]}|{r.rsplit('/', 1)[1][:-3]}]]" for r in reversed(t["notes_written"])]
    body += ["", "## 策略", "", *[f"- `{c}`" for c in s["conditions"]]]
    if s.get("rank"):
        body.append(f"- 排名 `{s['rank']}`（{'由小到大' if s['ascending'] else '由大到小'}）取前 {s['top_n']} 檔")
    body += ["", "_模擬追蹤，不是實際交易；不構成投資建議。_", ""]
    notes._write_outbox(f"{base}/總覽.md", "\n".join(body))  # noqa: SLF001
