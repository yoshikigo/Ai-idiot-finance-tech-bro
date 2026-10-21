"""Laya worker — 買進／持有／賣出／放空／觀望 with a probability per option.

receptron/laya (MIT) runs Convai Innovations' Laya "System 1" decision model (ModernBERT-large +
calibrated decision head, Apache-2.0 weights) on ONNX Runtime from Node.js. Laya does not generate
text: it reads a state and typed questions and returns calibrated probabilities in one forward pass.

We build an English state for a security as of a date (price action, technicals, valuation, what the
other FT engines concluded, God's Eye View events, a few English headlines) and ask:
  action   choice  buy / hold / sell / short / wait
  outlook  score   5 levels of the move over the horizon
  up       noul    P(close higher after the horizon)
  drop     noul    P(drop larger than X% within the horizon)

Laya is a general decision model (support tickets, routing, moderation…). It was not trained on
markets; its zero-shot accuracy on its own benchmark is near chance. The percentages are how
confident it is in each reading of *this state*, not a statistical probability of price moves —
the 回顧 scorecard in Obsidian shows whether it is any good for you.

  GET /decide?symbol=2330.TW&horizon=20   synchronous (terminal pane), cached 10 min
  GET /status                              model download / load state
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import os
import subprocess
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from fastapi import HTTPException

from ft_common import market, twmarket
from ft_common.engine import DATA, Ctx, Engine, jsonable, serve, split_tickers, table, today

SIDECAR = Path(os.environ.get("LAYA_SIDECAR", Path(__file__).with_name("sidecar") / "server.mjs"))
TIMESFM_URL = os.environ.get("FT_TIMESFM_URL", "http://127.0.0.1:8136").rstrip("/")
KRONOS_URL = os.environ.get("FT_KRONOS_URL", "http://127.0.0.1:8139").rstrip("/")
MAX_LEN = 512

ACTIONS = {  # option → (description shown to the model, 中文標籤)
    "buy": ("open or add to a long position now", "買進"),
    "hold": ("keep an existing position, no new trade", "持有"),
    "sell": ("sell, take profit or cut an existing long position", "賣出"),
    "short": ("open a short position, expecting the price to fall", "放空"),
    "wait": ("stay out, the evidence is mixed or unclear", "觀望"),
}
# move thresholds per horizon (small, large) in %
THRESH = {5: (1.5, 4.0), 20: (3.0, 8.0), 60: (5.0, 15.0)}

SCHEMA = [
    {"name": "ticker", "label": "標的", "type": "ticker", "required": True, "placeholder": "2330 / 0050 / NVDA"},
    {"name": "date", "label": "判斷基準日", "type": "date", "default": "today"},
    {"name": "horizon", "label": "期間", "type": "select", "default": "20",
     "options": [{"value": "5", "label": "一週（5 個交易日）"}, {"value": "20", "label": "一個月（20 個交易日）"},
                 {"value": "60", "label": "一季（60 個交易日）"}]},
    {"name": "use_agents", "label": "加入其他 AI 引擎最近的結論（TradingAgents、大師基金、FinGPT）", "type": "bool", "default": True},
    {"name": "use_timesfm", "label": "加入 TimesFM 與 Kronos 預測（只在基準日＝今天時）", "type": "bool", "default": True},
    {"name": "use_world", "label": "加入現實世界事件（God's Eye View）", "type": "bool", "default": True},
    {"name": "use_news", "label": "加入英文新聞標題", "type": "bool", "default": True},
    {"name": "use_ft", "label": "加入 FT 市場背景（籌碼、月營收、總經、產業強弱、即將公布的事件）", "type": "bool", "default": True},
]


# ---------------------------------------------------------------- Node sidecar
class Sidecar:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.ids = itertools.count(1)
        self.pending: dict[int, Future] = {}
        self.lock = threading.Lock()
        self.state = {"status": "stopped", "detail": "", "progress": None}

    def _start(self):
        env = {**os.environ}
        env.setdefault("LAYA_CACHE", str(DATA / "laya"))
        self.proc = subprocess.Popen(["node", str(SIDECAR)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1, env=env, cwd=str(SIDECAR.parent))
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._err, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if m.get("event") == "progress":
                tot = m.get("total")
                self.state.update(status="downloading", detail=m.get("file", ""),
                                  progress=round(m["received"] / tot * 100, 1) if tot else None,
                                  received_mb=round(m["received"] / 1e6))
                continue
            f = self.pending.pop(m.get("id"), None)
            if f and not f.done():
                f.set_result(m)
        for f in list(self.pending.values()):
            if not f.done():
                f.set_result({"ok": False, "error": "Laya sidecar exited"})
        self.pending.clear()

    def _err(self, proc):
        for line in proc.stderr:
            print("[laya-node]", line.rstrip(), flush=True)

    def call(self, op: str, timeout: float = 120, **kw) -> dict:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start()
            i = next(self.ids)
            f: Future = Future()
            self.pending[i] = f
            self.proc.stdin.write(json.dumps({"id": i, "op": op, **kw}, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        m = f.result(timeout=timeout)
        if not m.get("ok"):
            raise RuntimeError(m.get("error") or "Laya error")
        return m

    def load(self):
        self.state.update(status="loading", detail="")
        try:
            m = self.call("load", timeout=6 * 3600)  # first run downloads ~1.7 GB
            self.state = {"status": "ready", "detail": m.get("model_dir", ""), "progress": 100,
                          "fake": Path(m.get("model_dir", ""), "FAKE").exists()}
        except Exception as e:  # noqa: BLE001
            self.state = {"status": "error", "detail": str(e)[:300], "progress": None}
            raise


SC = Sidecar()


# ---------------------------------------------------------------- state
def _pct(a, b):
    return (a / b - 1) * 100 if b else float("nan")


def _f(x, nd=1, sign=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def rsi(close: pd.Series, n: int = 14) -> float:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up.iloc[-1] / dn.iloc[-1] if dn.iloc[-1] else float("inf")
    return float(100 - 100 / (1 + rs))


def technicals(df: pd.DataFrame) -> dict:
    c = df["Close"].astype(float)
    v = df["Volume"].astype(float)
    last = float(c.iloc[-1])
    ret = {k: _pct(last, float(c.iloc[-1 - n])) if len(c) > n else float("nan")
           for k, n in (("1d", 1), ("5d", 5), ("20d", 20), ("60d", 60), ("120d", 120), ("250d", 250))}
    ma = {n: float(c.rolling(n).mean().iloc[-1]) if len(c) >= n else float("nan") for n in (20, 60, 200)}
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    lr = np.log(c).diff().dropna()
    w = c.iloc[-250:]
    dd60 = float((c.iloc[-60:] / c.iloc[-60:].cummax() - 1).min() * 100)
    vr = float(v.iloc[-5:].mean() / v.iloc[-60:].mean()) if v.iloc[-60:].mean() else float("nan")
    return {"last": last, "ret": ret, "ma": ma, "rsi": rsi(c), "macd_hist": float(hist.iloc[-1]),
            "macd_rising": bool(hist.iloc[-1] > hist.iloc[-2]) if len(hist) > 1 else None,
            "vol20": float(lr.iloc[-20:].std() * math.sqrt(252) * 100) if len(lr) >= 20 else float("nan"),
            "dd60": dd60, "hi52": _pct(last, float(w.max())), "lo52": _pct(last, float(w.min())), "vol_ratio": vr,
            "date": str(pd.Timestamp(c.index[-1]).date())}


def _rsi_word(r):
    return "overbought" if r >= 70 else "oversold" if r <= 30 else "strong" if r >= 60 else "weak" if r <= 40 else "neutral"


REGION_EN = {"taiwan": "Taiwan Strait", "japan": "Japan", "south_china_sea": "South China Sea", "hormuz": "Strait of Hormuz",
             "red_sea": "Red Sea", "malacca": "Strait of Malacca", "panama": "Panama Canal", "gulf_mexico": "Gulf of Mexico",
             "us_west": "US West Coast", "global": "worldwide"}
SIG_EN = {"看多": "bullish", "看空": "bearish", "中性": "neutral", "跑贏": "outperform", "落後": "underperform",
          "買進": "buy", "賣出": "sell", "放空": "short", "持有": "hold", "觀望": "wait"}


def world_lines(sym: str, as_of: str, days: int = 7) -> list[str]:
    from ft_common import world
    out = []
    for e in world.events(world.regions_for(sym), days=days, as_of=as_of)[:6]:
        r, k, s = REGION_EN.get(e["region"], e["region"]), e["kind"], e["severity"] or 0
        what = {"quake": f"M{s:.1f} earthquake", "military": f"military aircraft {s:.1f}x the usual count",
                "shipping": f"ship traffic fell to 1/{s:.1f} of normal", "cyclone": f"{int(s)} tropical cyclone(s)",
                "fires": f"{int(s)} active wildfires"}.get(k, k)
        out.append(f"{e['date']} {r}: {what}")
    return out


def agent_lines(sym: str, as_of: str, days: int = 14) -> list[str]:
    start = (dt.date.fromisoformat(as_of) - dt.timedelta(days=days)).isoformat()
    best: dict[str, tuple[str, str]] = {}
    for eng, label in (("tradingagents", "TradingAgents"), ("hedgefund", "AI hedge fund (investor personas)"),
                       ("fingpt", "FinGPT"), ("timesfm", "TimesFM job"), ("kronos", "Kronos job")):
        d = DATA / eng / "jobs"
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json"))[-200:]:
            try:
                j = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            r = j.get("result") or {}
            meta = r.get("meta") or {}
            day = (meta.get("date") or (j.get("created") or ""))[:10]
            if j.get("status") != "done" or not (start <= day <= as_of):
                continue
            sig = None
            if eng == "hedgefund":
                sig = (meta.get("signals") or {}).get(sym)
            elif meta.get("ticker") == sym or sym in (meta.get("tickers") or []):
                sig = meta.get("signal") or r.get("signal")
            if sig and (label not in best or day >= best[label][0]):
                best[label] = (day, str(sig))
    return [f"{lab} ({d}): {SIG_EN.get(s, s)}" for lab, (d, s) in best.items()]


def timesfm_line(sym: str, h: int) -> str | None:
    try:
        r = httpx.get(f"{TIMESFM_URL}/forecast", params={"symbol": sym, "horizon": h}, timeout=90, trust_env=False)
        r.raise_for_status()
        sm, last = r.json()["summary"], r.json()["last"]
        return (f"TimesFM {h}-day forecast: median {sm['change_pct']:+.1f}%, P(higher) {sm['p_up'] * 100:.0f}%, "
                f"80% band {_pct(sm['p10_end'], last):+.1f}% to {_pct(sm['p90_end'], last):+.1f}%")
    except Exception:  # noqa: BLE001
        return None


def kronos_line(sym: str, h: int) -> str | None:
    try:
        r = httpx.get(f"{KRONOS_URL}/forecast", params={"symbol": sym, "horizon": h, "samples": 16}, timeout=180, trust_env=False)
        r.raise_for_status()
        sm, last = r.json()["summary"], r.json()["last"]
        return (f"Kronos candlestick-model {h}-day forecast: median close {sm['change_pct']:+.1f}%, {sm['p_up'] * 100:.0f}% of sampled "
                f"paths end higher, 80% band {_pct(sm['p10_end'], last):+.1f}% to {_pct(sm['p90_end'], last):+.1f}%")
    except Exception:  # noqa: BLE001
        return None


def headlines(sym: str, n: int = 5) -> list[str]:
    try:
        items = market.news(sym, 20)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in items:
        t = (it.get("title") or "").strip()
        if t and sum(ord(ch) < 128 for ch in t) / len(t) > 0.9:  # the English checkpoint reads English only
            if t[:140] not in out:
                out.append(t[:140])
        if len(out) >= n:
            break
    return out


def build_state(sym: str, as_of: str, h: int, use_agents=True, use_timesfm=True, use_world=True, use_news=True,
                use_ft=True) -> tuple[dict, dict]:
    """→ (English state for Laya, facts for the Chinese report)."""
    end = dt.date.fromisoformat(as_of)
    df = market.history(sym, start=(end - dt.timedelta(days=420)).isoformat(), end=(end + dt.timedelta(days=1)).isoformat())
    if len(df) < 70:
        raise ValueError(f"{sym} 價格資料不足（{len(df)} 筆，至少要 70 個交易日）")
    t = technicals(df)
    info = market.info(sym) if not sym.startswith("^") else {}
    zh = twmarket.zh_name(sym)
    name = info.get("shortName") or info.get("longName") or sym
    kind = "Taiwan-listed" if twmarket.is_tw(sym) else (info.get("exchange") or "")
    cat = info.get("industry") or info.get("sector") or info.get("quoteType") or ""
    small, large = THRESH.get(h, THRESH[20])
    ma = t["ma"]
    trend = ", ".join(f"{'above' if t['last'] > ma[n] else 'below'} MA{n} ({_f(_pct(t['last'], ma[n]))}%)"
                      for n in (20, 60, 200) if not math.isnan(ma[n]))
    if not math.isnan(ma[60]):
        trend += f"; MA20 {'above' if ma[20] > ma[60] else 'below'} MA60"
    st = {
        "instrument": f"{sym} {name} ({kind} {cat})".replace("  ", " ").replace("( ", "("),
        "as_of": t["date"],
        "decision_horizon": f"next {h} trading days",
        "price": f"{t['last']:,.2f} {info.get('currency') or ''}".strip(),
        "returns": ", ".join(f"{k} {_f(v)}%" for k, v in t["ret"].items() if not math.isnan(v)),
        "trend": trend,
        "momentum": f"RSI14 {t['rsi']:.0f} ({_rsi_word(t['rsi'])}), MACD histogram "
                    f"{'positive' if t['macd_hist'] > 0 else 'negative'} and {'rising' if t['macd_rising'] else 'falling'}",
        "risk": f"20d volatility {t['vol20']:.0f}% annualized; worst 60d drawdown {t['dd60']:.1f}%; "
                f"{abs(t['hi52']):.1f}% below 52-week high, {t['lo52']:.1f}% above 52-week low",
        "volume": f"5-day average volume {t['vol_ratio']:.2f}x the 60-day average",
    }
    val = [f"{lab} {info[k]:.1f}" for k, lab in (("trailingPE", "P/E"), ("priceToBook", "P/B"), ("beta", "beta"))
           if isinstance(info.get(k), (int, float))]
    if isinstance(info.get("dividendYield"), (int, float)):
        val.append(f"dividend yield {info['dividendYield']:.1f}%")
    if val:
        st["valuation"] = ", ".join(val)
    facts = {"name": zh or name, "tech": t, "agents": [], "timesfm": None, "world": [], "news": []}
    sigs = []
    if use_timesfm and as_of == today():
        tl = timesfm_line(sym, h)
        if tl:
            sigs.append(tl)
            facts["timesfm"] = tl
        kl = kronos_line(sym, h)
        if kl:
            sigs.append(kl)
            facts["kronos"] = kl
    if use_agents:
        al = agent_lines(sym, as_of)
        sigs += al
        facts["agents"] = al
    if sigs:
        st["other_model_signals"] = sigs
    if use_world:
        try:
            wl = world_lines(sym, as_of)
        except Exception:  # noqa: BLE001  world DB missing → just leave it out
            wl = []
        st["world_events_last_7d"] = wl or ["none above alert thresholds in the relevant regions"]
        facts["world"] = wl
    if use_news and as_of == today():
        hl = headlines(sym)
        if use_ft and twmarket.is_tw(sym):  # Taiwan news is Chinese: translated by local Ollama so Laya can read it
            try:
                from ft_common import brief
                hl = (hl or []) + brief.tw_headlines_en(sym)
            except Exception:  # noqa: BLE001
                pass
        if hl:
            st["recent_headlines"] = hl
            facts["news"] = hl
    if use_ft:  # FT 市場背景 in English (Laya only reads English); all as of the base date
        try:
            from ft_common import brief
            ft = brief.facts_en(sym, None if as_of == today() else as_of)
        except Exception:  # noqa: BLE001
            ft = {}
        st.update(ft)
        facts["ft"] = [f"{k}: {v}" for k, v in ft.items()]
    facts["thresholds"] = (small, large)
    return st, facts


def questions(h: int) -> dict:
    small, large = THRESH.get(h, THRESH[20])
    return {
        "action": {"type": "choice",
                   "instructions": f"As a disciplined trader, what is the best action on this security for the {st_h(h)}?",
                   "criteria": {k: v[0] for k, v in ACTIONS.items()}},
        "outlook": {"type": "score", "instructions": f"Where will the price most likely be after {h} trading days?",
                    "criteria": [f"down more than {large:g}%", f"down {small:g} to {large:g}%", f"within {small:g}% of today",
                                 f"up {small:g} to {large:g}%", f"up more than {large:g}%"]},
        "up": {"type": "noul", "instructions": f"Will the closing price be higher {h} trading days from now than today?"},
        "drop": {"type": "noul", "instructions": f"Is there a serious risk of a fall of more than {large:g}% within {h} trading days?"},
    }


def st_h(h: int) -> str:
    return {5: "next week", 20: "next month", 60: "next quarter"}.get(h, f"next {h} trading days")


OUTLOOK_ZH = ["大跌", "下跌", "持平", "上漲", "大漲"]


def decide(sym: str, as_of: str | None = None, h: int = 20, **flags) -> dict:
    as_of = as_of or today()
    st, facts = build_state(sym, as_of, h, **flags)
    t0 = time.time()
    m = SC.call("decide", timeout=300, state=st, questions=questions(h))
    a = m["result"]["answers"]
    act = a["action"]["probabilities"]
    probs = {k: act.get(k, 0.0) for k in ACTIONS}
    top = max(probs, key=probs.get)
    out_p = [a["outlook"]["probabilities"].get(str(i), 0.0) for i in range(5)]
    small, large = facts["thresholds"]
    n_tok = m["result"]["usage"]["input_tokens"]
    return {
        "symbol": sym, "name": facts["name"], "as_of": st["as_of"], "horizon": h, "last": facts["tech"]["last"],
        "action": {"choice": top, "label": ACTIONS[top][1], "probabilities": probs,
                   "labels": {k: v[1] for k, v in ACTIONS.items()},
                   "answer_confidence": a["action"]["answer_confidence"], "confidence": a["action"]["confidence"]},
        "net": round(probs["buy"] - probs["sell"] - probs["short"], 4),
        "outlook": {"score": a["outlook"]["score"], "probabilities": out_p, "labels": OUTLOOK_ZH,
                    "bins": [f"< -{large:g}%", f"-{large:g}~-{small:g}%", f"±{small:g}%", f"+{small:g}~+{large:g}%", f"> +{large:g}%"]},
        "p_up": a["up"]["noul"], "p_drop": a["drop"]["noul"], "drop_threshold": large,
        "tokens": n_tok, "truncated": n_tok / 4 >= MAX_LEN - 4, "ms": m.get("ms") or int((time.time() - t0) * 1000),
        "state": st, "facts": {k: v for k, v in facts.items() if k != "tech"},
        "model": "Laya (convaiinnovations/laya via receptron/laya-onnx)" + ("（測試用假模型）" if SC.state.get("fake") else ""),
    }


# ---------------------------------------------------------------- report
def bar(p: float, w: int = 20) -> str:
    n = int(round(p * w))
    return "█" * n + "░" * (w - n)


def report(d: dict) -> dict:
    a = d["action"]
    rows = sorted(a["probabilities"].items(), key=lambda kv: -kv[1])
    md = [f"# Laya 交易判斷 · {d['symbol']} {d['name']} · 基準日 {d['as_of']} · 未來 {d['horizon']} 個交易日", "",
          f"**{a['label']} {a['probabilities'][a['choice']] * 100:.0f}%**　多空淨值 {d['net'] * 100:+.0f}%"
          f"（買進 − 賣出 − 放空）　收高機率 {d['p_up'] * 100:.0f}%　跌逾 {d['drop_threshold']:g}% 風險 {d['p_drop'] * 100:.0f}%", "",
          "| 動作 | 機率 | |", "|---|---:|---|"]
    md += [f"| {a['labels'][k]} | {p * 100:.1f}% | `{bar(p)}` |" for k, p in rows]
    md += ["", f"### {d['horizon']} 個交易日後的位置", "", "| 區間 | 機率 |", "|---|---:|"]
    md += [f"| {lab}（{b}） | {p * 100:.1f}% |" for lab, b, p in zip(d["outlook"]["labels"], d["outlook"]["bins"], d["outlook"]["probabilities"])]
    md += ["", f"期望等級 {d['outlook']['score']:.2f}（0＝大跌 … 4＝大漲，2＝持平）", ""]
    f = d["facts"]
    if f.get("agents") or f.get("timesfm") or f.get("kronos"):
        md += ["### 帶入的其他 AI 結論", ""] + [f"- {x}" for x in [f[k] for k in ("timesfm", "kronos") if f.get(k)] + f.get("agents", [])] + [""]
    if f.get("world"):
        md += ["### 帶入的世界事件（7 天）", ""] + [f"- {x}" for x in f["world"]] + [""]
    if f.get("ft"):
        md += ["### 帶入的 FT 市場背景", ""] + [f"- {x}" for x in f["ft"]] + [""]
    md += ["> [!warning] 怎麼看這些百分比",
           "> Laya 是通用的「System 1」決策模型（客服分流、審核、評分這類任務訓練的），**沒有用金融資料訓練**，官方說零樣本表現接近亂猜、"
           "而且偏過度自信。這裡的百分比是它讀完下面這份英文狀態後，對各選項的信心，**不是**統計上的漲跌機率。"
           "請和 TimesFM 的回測、其他引擎一起看，並用 Obsidian「AI 預測回顧」追蹤它實際的命中率。", ""]
    if d.get("truncated"):
        md += ["> 狀態超過模型 512 token 上限，後段（新聞標題）可能被截掉。", ""]
    md += ["<details><summary>送給 Laya 的狀態與問題（英文）</summary>", "", "```json",
           json.dumps(d["state"], ensure_ascii=False, indent=1), "```", "", "```json",
           json.dumps(questions(d["horizon"]), ensure_ascii=False, indent=1), "```", "</details>", "",
           f"模型：{d['model']}　輸入 {d['tokens']} tokens（4 個問題合計）　推論 {d['ms']} ms"]
    sig = a["label"]
    return {"summary": f"{d['symbol']} {d['name']} {d['horizon']}日：{sig} {a['probabilities'][a['choice']] * 100:.0f}%"
                       f"（收高 {d['p_up'] * 100:.0f}%）",
            "signal": sig, "last": d["last"], "decision": {k: v for k, v in d.items() if k not in ("state",)},
            "markdown": "\n".join(md),
            "tables": [table("動作機率", ["動作", "機率"], [[a["labels"][k], f"{p * 100:.1f}%"] for k, p in rows])]}


# ---------------------------------------------------------------- job + sync API
def run(ctx: Ctx) -> dict:
    p = ctx.params
    sym = split_tickers(p["ticker"])[0]
    as_of = p.get("date") or today()
    as_of = today() if as_of == "today" else as_of
    h = int(p.get("horizon") or 20)
    if SC.state["status"] != "ready":
        ctx.step(5, "載入 Laya 模型（第一次會下載約 1.7 GB）")
        SC.load()
    ctx.step(30, "整理狀態（價格、技術指標、其他引擎、世界事件、新聞）")
    d = decide(sym, as_of, h, use_agents=bool(p.get("use_agents", True)), use_timesfm=bool(p.get("use_timesfm", True)),
               use_world=bool(p.get("use_world", True)), use_news=bool(p.get("use_news", True)),
               use_ft=bool(p.get("use_ft", True)))
    ctx.log("state: " + json.dumps(d["state"], ensure_ascii=False))
    ctx.log(f"answers: action={d['action']['probabilities']} p_up={d['p_up']} p_drop={d['p_drop']} ({d['ms']} ms, {d['tokens']} tokens)")
    return report(d)


engine = Engine("laya", "Laya 交易判斷",
                "receptron/laya：Convai Innovations 的 Laya「System 1」決策模型（ONNX，CPU），一次前向推論就給出 買進／持有／賣出／放空／觀望 "
                "各自的機率、未來區間分布、收高與大跌風險。輸入是價格技術面、其他 AI 引擎的結論、世界事件與英文新聞。通用模型，未針對金融訓練。",
                SCHEMA, run, repo="https://github.com/receptron/laya", version=os.environ.get("UPSTREAM_REF", ""))
app = engine.app
_cache: dict = {}
_clock = threading.Lock()


@app.get("/status")
def status():
    return SC.state


@app.get("/decide")
def decide_api(symbol: str, horizon: int = 20, date: str = "", agents: bool = True, timesfm: bool = True,
               world: bool = True, news: bool = True, ft: bool = True):
    if SC.state["status"] in ("loading", "downloading"):
        pr = SC.state.get("progress")
        raise HTTPException(503, f"Laya 模型{'下載中 ' + str(pr) + '%' if pr is not None else '載入中'}，請稍候")
    if SC.state["status"] != "ready":
        try:
            SC.load()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"Laya 模型無法載入：{e}") from e
    h = horizon if horizon in THRESH else 20
    key = (symbol, h, date, agents, timesfm, world, news, ft)
    with _clock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
    try:
        d = jsonable(decide(symbol, date or None, h, use_agents=agents, use_timesfm=timesfm, use_world=world, use_news=news, use_ft=ft))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    with _clock:
        _cache[key] = (time.time(), d)
    return d


@app.on_event("startup")
def _preload():
    if os.environ.get("LAYA_PRELOAD", "1") == "1":
        threading.Thread(target=_safe_load, daemon=True).start()


def _safe_load():
    try:
        SC.load()
    except Exception as e:  # noqa: BLE001
        print("Laya preload failed:", e, flush=True)


if __name__ == "__main__":
    serve(engine, 8137)
