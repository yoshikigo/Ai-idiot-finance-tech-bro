"""策略研究 engine — FinLab-style research on official Taiwanese data.

  資料庫     official sources → point-in-time SQLite (/data/research), collected in the background
  條件選股   conditions + rank in a safe expression language (ft_common.research.expr)
  回測       next-day-open execution, fees/tax/slippage, report with CAGR / Sharpe / MDD / yearly …
  AI 建策略  中文想法 → strategy JSON with local Ollama, validated before it runs

Sync API (used by the terminal panes):
  GET  /catalog            datasets, fields, coverage, collector state, functions, presets
  GET  /series?field=&code=
  POST /backtest {spec}    POST /screen {spec}    POST /ai {prompt, llm}    POST /collector {action}
"""
from __future__ import annotations

import json
import os
import threading
import time

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ft_common.engine import Ctx, Engine, serve, table
from ft_common.research import ai, backtest, collect, expr, fields as F, presets, store

SCHEMA = [
    {"name": "mode", "label": "策略來源", "type": "select", "default": "preset",
     "options": [{"value": "preset", "label": "內建策略"}, {"value": "ai", "label": "用中文描述（AI 建立策略）"},
                 {"value": "json", "label": "策略 JSON"}]},
    {"name": "preset", "label": "內建策略", "type": "select", "default": presets.PRESETS[0]["id"],
     "options": [{"value": p["id"], "label": p["name"]} for p in presets.PRESETS]},
    {"name": "prompt", "label": "選股想法（AI 模式）", "type": "textarea",
     "placeholder": "例：找營收連續三個月成長、本益比低於 20、投信最近在買的股票，每月換股"},
    {"name": "spec", "label": "策略 JSON（JSON 模式）", "type": "textarea"},
    {"name": "start", "label": "回測起點", "type": "date", "default": "2016-01-01"},
]


def _spec_from(p: dict, llm: dict | None, ctx: Ctx | None = None) -> tuple[dict, str]:
    mode = p.get("mode") or "preset"
    expl = ""
    if mode == "ai":
        if not (p.get("prompt") or "").strip():
            raise ValueError("請輸入選股想法")
        if ctx:
            ctx.step(10, "AI 把想法轉成策略")
        r = ai.build(p["prompt"], llm or {})
        spec, expl = r["spec"], r["explanation"]
    elif mode == "json":
        spec = json.loads(p.get("spec") or "{}")
    else:
        spec = dict(presets.BY_ID.get(p.get("preset")) or presets.PRESETS[0])
    if p.get("start"):
        spec["start"] = p["start"]
    return backtest.normalize(spec), expl


def fmt(v, d=2, pct=False):
    if v is None:
        return "—"
    return f"{v:+.{d}f}%" if pct else f"{v:.{d}f}"


def report_md(r: dict, expl: str = "") -> str:
    s, m = r["spec"], r["metrics"]
    lines = [f"# 策略回測：{s['name']}", "", s.get("description") or "", ""]
    if expl:
        lines += [f"> **AI 的轉換說明**：{expl}", ""]
    lines += ["## 條件", ""] + [f"- `{c}`" for c in s["conditions"]]
    if s["rank"]:
        lines.append(f"- 排名：`{s['rank']}`（{'由小到大' if s['ascending'] else '由大到小'}）取前 {s['top_n']} 檔")
    lines += [f"- 換股：{backtest.REBALANCE[s['rebalance']]}；單檔上限 {s['max_weight'] * 100:.0f}%"
              + (f"；停損 {s['stop_loss'] * 100:.0f}%" if s["stop_loss"] else ""), "",
              f"## 績效（{m['start']} ～ {m['end']}，基準 {s['benchmark']}）", "",
              "| 指標 | 策略 | 基準 |", "|---|---:|---:|",
              f"| 年化報酬 CAGR | {fmt(m['cagr'], pct=True)} | {fmt(m.get('bench_cagr'), pct=True)} |",
              f"| 總報酬 | {fmt(m['total_return'], 1, True)} | {fmt(m.get('bench_total'), 1, True)} |",
              f"| 最大回撤 MDD | {fmt(m['mdd'], pct=True)} | {fmt(m.get('bench_mdd'), pct=True)} |",
              f"| 夏普比率 | {fmt(m['sharpe'])} | {fmt(m.get('bench_sharpe'))} |",
              f"| 年化波動 | {fmt(m['vol'], pct=True)} | |", f"| Sortino | {fmt(m['sortino'])} | |",
              f"| Alpha（年化）／Beta | {fmt(m.get('alpha'), pct=True)} ／ {fmt(m.get('beta'))} | |",
              f"| 交易次數／勝率 | {m['trades']} ／ {fmt(m['win_rate'], 1)}% | |",
              f"| 平均每筆報酬／持有天數 | {fmt(m['avg_trade'], pct=True)} ／ {fmt(m['avg_days'], 0)} 天 | |", ""]
    if r.get("warnings"):
        lines += ["> " + "；".join(r["warnings"]), ""]
    pk = r.get("picks") or {}
    if pk.get("rows"):
        lines += [f"## 最新選股名單（{pk['date']}，符合條件 {pk['n_match']} 檔）", ""]
        lines += [f"- {x['code']} {x['name']}" + (f"（分數 {x['score']}）" if x.get("score") is not None else "") for x in pk["rows"]]
        lines.append("")
    lines += ["## 注意", ""] + [f"- {n}" for n in r["notes"]] + ["", "_歷史回測僅供研究，不代表未來績效，不構成投資建議。_"]
    return "\n".join(lines)


def run(ctx: Ctx) -> dict:
    spec, expl = _spec_from(ctx.params, ctx.llm, ctx)
    ctx.log("spec: " + json.dumps(spec, ensure_ascii=False))
    ctx.step(40, "回測中")
    r = backtest.run(spec)
    m = r["metrics"]
    ctx.log(f"CAGR {m['cagr']} Sharpe {m['sharpe']} MDD {m['mdd']} trades {m['trades']}")
    yearly = [[y["year"], fmt(y["ret"], 1, True), fmt(y.get("bench"), 1, True)] for y in r["yearly"]]
    return {"summary": f"{spec['name']}：年化 {fmt(m['cagr'], 1, True)}、MDD {fmt(m['mdd'], 1, True)}、夏普 {fmt(m['sharpe'])}",
            "markdown": report_md(r, expl), "research": r | {"explanation": expl},
            "tables": [table("每年報酬", ["年度", "策略", "基準"], yearly),
                       table("最新選股名單", ["代號", "名稱", "產業", "收盤"],
                             [[x["code"], x["name"], x.get("industry") or "", x.get("close")] for x in (r.get("picks") or {}).get("rows", [])])]}


engine = Engine("research", "策略研究（FinLab 式）",
                "用官方公開資料（證交所、櫃買、公開資訊觀測站）建立時點正確的台股資料庫；條件＋排名選股、回測（含手續費、證交稅、滑價，隔日開盤成交），"
                "或用中文描述想法讓本機 AI 建立策略。",
                SCHEMA, run, repo="https://finlab.finance/data", version="ft-research 1")
app = engine.app
_bt_cache: dict = {}
_bt_lock = threading.Lock()


@app.get("/catalog")
def catalog():
    c = F.catalog()
    return c | {"collector": collect.state, "functions": expr.help_table(), "presets": presets.PRESETS,
                "rebalance": backtest.REBALANCE, "defaults": backtest.DEFAULTS,
                "config": {"start": collect.START.isoformat(), "daily_years": collect.DAILY_YEARS, "backfill": collect.BACKFILL},
                "fake": collect.FAKE}


@app.get("/series")
def series(field: str, code: str, n: int = 400):
    f = F.BY_NAME.get(field)
    if not f:
        raise HTTPException(404, f"沒有欄位 {field}")
    df = F.raw(f)
    if code not in df.columns:
        return {"field": f.name, "unit": f.unit, "freq": f.freq, "points": []}
    s = df[code].dropna().tail(n)
    return {"field": f.name, "unit": f.unit, "freq": f.freq,
            "points": [{"t": d.date().isoformat(), "v": round(float(v), 4)} for d, v in s.items()]}


def _bt(spec: dict) -> dict:
    s = backtest.normalize(spec)
    key = (backtest.spec_hash(s), store.version())
    with _bt_lock:
        hit = _bt_cache.get(key)
    if hit:
        return hit
    r = backtest.run(s)
    with _bt_lock:
        _bt_cache[key] = r
        while len(_bt_cache) > 30:
            _bt_cache.pop(next(iter(_bt_cache)))
    return r


@app.post("/backtest")
async def backtest_api(req: Request):
    b = await req.json()
    try:
        t0 = time.time()
        r = await run_in_threadpool(_bt, b.get("spec") or b)
        return r | {"ms": int((time.time() - t0) * 1000)}
    except (backtest.SpecError, expr.ExprError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/screen")
async def screen_api(req: Request):
    b = await req.json()
    try:
        return await run_in_threadpool(backtest.screen, b.get("spec") or b)
    except (backtest.SpecError, expr.ExprError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/ai")
async def ai_api(req: Request):
    b = await req.json()
    try:
        return await run_in_threadpool(ai.build, b.get("prompt") or "", b.get("llm") or {})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Ollama 無法使用：{e}") from e


@app.post("/check")
async def check_api(req: Request):
    b = await req.json()
    try:
        return {"ok": True, "fields": expr.names(b.get("expr") or "")}
    except expr.ExprError as e:
        return {"ok": False, "error": str(e)}


@app.post("/collector")
async def collector_api(req: Request):
    b = await req.json()
    a = b.get("action")
    if a == "pause":
        collect.state["paused"] = True
    elif a == "resume":
        collect.state["paused"] = False
    elif a == "prices_full":
        threading.Thread(target=collect.update_prices, kwargs={"full": True}, daemon=True).start()
    elif a == "publish":
        store.bump_version()
    return collect.state


@app.on_event("startup")
def _start():
    if os.environ.get("FT_RESEARCH_COLLECT", "1") not in ("0", "false", "no"):
        threading.Thread(target=collect.loop_forever, daemon=True, name="research-collector").start()


if __name__ == "__main__":
    serve(engine, 8138)
