"""ai-hedge-fund (virattt) worker — investor-persona LLM agents on local Ollama."""
from __future__ import annotations

import os
import statistics

from ft_common import ollama
from ft_common.engine import Ctx, Engine, serve, split_tickers, table, today

AGENTS = [
    {"value": "buffett", "label": "Warren Buffett 巴菲特"},
    {"value": "munger", "label": "Charlie Munger 蒙格"},
    {"value": "graham", "label": "Ben Graham 葛拉漢"},
    {"value": "lynch", "label": "Peter Lynch 林區"},
    {"value": "druckenmiller", "label": "Stan Druckenmiller 杜肯米勒"},
]

SCHEMA = [
    {"name": "tickers", "label": "標的（逗號分隔）", "type": "tickers", "required": True, "placeholder": "AAPL, MSFT, 2330.TW"},
    {"name": "date", "label": "評估日期", "type": "date", "default": "today"},
    {"name": "agents", "label": "投資大師", "type": "multi", "options": AGENTS,
     "default": [a["value"] for a in AGENTS]},
    {"name": "etf_mode", "label": "遇到 ETF（沒有自己的財報）", "type": "select", "default": "lookthrough",
     "options": [{"value": "lookthrough", "label": "改評估前幾大成分股，再依權重加權成 ETF 的訊號"},
                 {"value": "skip", "label": "略過（直接標示不適用）"}]},
    {"name": "etf_top", "label": "成分股檔數（ETF 穿透）", "type": "int", "default": 5, "min": 1, "max": 15},
    {"name": "language", "label": "推理語言", "type": "select", "default": "Traditional Chinese",
     "options": [{"value": "Traditional Chinese", "label": "繁體中文"}, {"value": "English", "label": "English"}]},
    {"name": "ft_context", "label": "加入 FT 市場背景（台股中文新聞、籌碼、月營收、產業強弱、台灣／美國總經、重大事件與日程）", "type": "bool", "default": True},
]

LABEL = {a["value"]: a["label"] for a in AGENTS}
ZH = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}


class OllamaLLM:
    """hedge_fund.llm.LLMClient protocol: anything with .model and complete(system, user)."""

    def __init__(self, llm: dict, language: str):
        self.cfg = llm
        self.model = f"ollama/{llm['model']}"
        self.language = language

    def complete(self, system: str, user: str) -> str:
        if self.cfg.get("context"):  # FT 市場背景: macro + events for everyone, plus this ticker's own block
            system += "\n\n" + self.cfg["context"]
            for sym, txt in (self.cfg.get("context_by") or {}).items():
                if sym in user or sym.split(".")[0] in user:
                    system += "\n\n" + txt
                    break
        if self.cfg.get("lessons"):
            system += ("\n\nLessons from reviewing this persona's past calls against actual prices "
                       "(avoid repeating these mistakes):\n" + self.cfg["lessons"])
        if self.language == "Traditional Chinese":
            system += ("\n\nRespond with JSON only: {\"signal\": \"bullish|neutral|bearish\", "
                       "\"confidence\": 0-100, \"reasoning\": \"...\"}. "
                       "Write the reasoning value in Traditional Chinese (繁體中文); keep keys and signal values in English.")
        return ollama.chat(self.cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                           json_mode=True)


def etf_holdings(sym: str, top: int) -> tuple[bool, list[tuple[str, str, float]], str]:
    """→ (is_etf, [(symbol, name, weight%)], note). The investor agents need a company's filed statements;
    an ETF has none, so we look through to its largest stock holdings."""
    from ft_common import market, twmarket
    if twmarket.is_tw(sym):
        sec = twmarket.lookup(sym) or {}
        if sec.get("kind") != "ETF":
            return False, [], ""
        h = twmarket.etf_holdings(sym)
        if h.get("error"):
            return True, [], h["error"]
        out = []
        for it in h.get("items", []):
            code = str(it.get("code") or "").strip()
            s2 = twmarket.lookup(code)
            if s2 and s2.get("kind") == "股票" and it.get("pct"):
                out.append((s2["symbol"], s2.get("name") or it.get("name") or code, float(it["pct"])))
        note = f"成分股資料 {h.get('period', '')}（{h.get('scope', '')}）"
        if not out:
            note = "這檔 ETF 的成分不是台股個股（債券、期貨或海外標的），投資大師無法評估"
        return True, out[:top], note
    info = market.info(sym)
    if (info.get("quoteType") or "").upper() != "ETF":
        return False, [], ""
    try:
        import yfinance as yf
        th = yf.Ticker(sym).funds_data.top_holdings
        out = [(str(i), str(r.get("Name") or i), float(r.get("Holding Percent") or 0) * 100) for i, r in th.iterrows()]
        return True, [o for o in out if o[2] > 0][:top], "Yahoo Finance top holdings"
    except Exception as e:  # noqa: BLE001
        return True, [], f"抓不到成分股：{e}"


def run(ctx: Ctx) -> dict:
    p = ctx.params
    tickers = split_tickers(p["tickers"])
    date = p.get("date") or today()
    if date == "today":
        date = today()
    agents = p.get("agents") or [a["value"] for a in AGENTS]
    lang = p.get("language") or "Traditional Chinese"
    llm = ollama.resolve(ctx.llm, "hedgefund")

    os.environ["HOME"] = str(ctx.dir / "home")
    ctx.step(3, "載入 ai-hedge-fund")
    from hedge_fund.llm import PromptCache
    from hedge_fund.signals import ALPHA_MODEL_REGISTRY

    if os.environ.get("FINANCIAL_DATASETS_API_KEY"):
        from hedge_fund.data.client import FDClient
        data = FDClient()
        ctx.log("資料來源：Financial Datasets API")
    else:
        from yf_client import YFinanceDataClient
        data = YFinanceDataClient()
        from ft_common import edgar
        ctx.log("資料來源：yfinance（年報 + TTM）；申報日：" + ("美股用 SEC EDGAR 實際申報日，其他以期末 +75/45 天近似"
                                                       if edgar.enabled() else "以期末 +75/45 天近似（設定 FT_SEC_USER_AGENT 可改用 SEC 實際申報日）"))

    # ETFs have no statements of their own (every agent would abstain with "0 filed periods")
    etf_map: dict[str, dict] = {}
    expanded: list[str] = []
    for t in tickers:
        is_etf, hold, note = etf_holdings(t, int(p.get("etf_top") or 5)) if p.get("etf_mode", "lookthrough") != "skip" else (False, [], "")
        if p.get("etf_mode") == "skip":
            from ft_common import twmarket
            is_etf = (twmarket.lookup(t) or {}).get("kind") == "ETF"
        if not is_etf:
            expanded.append(t)
            continue
        etf_map[t] = {"holdings": hold, "note": note}
        if hold:
            ctx.log(f"{t} 是 ETF：改評估前 {len(hold)} 大成分股（佔 {sum(w for *_, w in hold):.1f}%）："
                    + "、".join(f"{s} {n} {w:.1f}%" for s, n, w in hold))
            expanded += [s for s, *_ in hold if s not in expanded]
        else:
            ctx.log(f"{t} 是 ETF，{note or '沒有可評估的成分股'}，略過")
    ticker_order = tickers
    tickers = expanded

    if llm.get("lessons"):
        ctx.log("帶入過去檢討（分析紀錄頁可關閉）")
    if llm.get("context"):
        ctx.log(f"帶入 FT 市場背景：{llm.get('context_parts') or ''}（{len(llm.get('context_by') or {})} 檔個別資料）")
    client = OllamaLLM(llm, lang)
    cache = PromptCache(ctx.dir / "prompt-cache")
    rows, by_ticker = [], {}
    total = len(tickers) * len(agents)
    n = 0
    with ctx.capture():
        for t in tickers:
            for a in agents:
                n += 1
                ctx.step(5 + 85 * (n - 1) / total, f"{LABEL.get(a, a)} 評估 {t}")
                agent = ALPHA_MODEL_REGISTRY[a](llm=client, cache=cache)
                sig = agent.predict(t, date, data)
                md = sig.metadata or {}
                label = "棄權" if md.get("abstained") else ZH.get(md.get("signal"), md.get("signal"))
                conf = md.get("confidence")
                rows.append([t, LABEL.get(a, a), label, f"{conf:.0f}" if conf is not None else "—",
                             f"{sig.value:+.2f}", (sig.reasoning or md.get("reason") or "")[:900]])
                if not md.get("abstained"):
                    by_ticker.setdefault(t, []).append(sig.value)
                ctx.log(f"  → {t} {a}: {label} ({sig.value:+.2f}){' cached' if md.get('cached') else ''}")

    consensus = []
    view_of = lambda m: "看多" if m > 0.15 else "看空" if m < -0.15 else "中性"  # noqa: E731
    for t in ticker_order:
        if t in etf_map:
            e = etf_map[t]
            got = [(statistics.mean(by_ticker[s]), w) for s, _, w in e["holdings"] if by_ticker.get(s)]
            if got:
                wsum = sum(w for _, w in got)
                m = sum(v * w for v, w in got) / wsum
                consensus.append([f"{t}（成分股加權，涵蓋 {wsum:.0f}% 權重）", f"{m:+.2f}", view_of(m),
                                  f"{sum(v > 0 for v, _ in got)}/{sum(v < 0 for v, _ in got)}/{sum(v == 0 for v, _ in got)} 檔"])
            else:
                consensus.append([t, "—", "ETF 不適用" if not e["holdings"] else "資料不足", "0/0/0"])
            continue
    for t in tickers:
        vals = by_ticker.get(t, [])
        if vals:
            m = statistics.mean(vals)
            consensus.append([t, f"{m:+.2f}", view_of(m), f"{sum(v > 0 for v in vals)}/{sum(v < 0 for v in vals)}/{sum(v == 0 for v in vals)}"])
        else:
            consensus.append([t, "—", "資料不足", "0/0/0"])

    ctx.step(92, "投資組合經理彙整（Ollama）")
    pm_prompt = "以下是多位投資大師代理人對各標的的訊號（-1 看空 ~ +1 看多）與理由：\n\n" + "\n".join(
        f"- {r[0]} | {r[1]} | {r[2]} | 信心 {r[3]} | {r[5][:400]}" for r in rows)
    for t, e in etf_map.items():
        if e["holdings"]:
            pm_prompt += (f"\n\n注意：{t} 是 ETF，沒有自己的財報；上面評估的是它的前 {len(e['holdings'])} 大成分股（"
                          + "、".join(f"{s} {n} 權重 {w:.1f}%" for s, n, w in e["holdings"])
                          + "）。請依權重把成分股的看法彙整成對這檔 ETF 的建議，並提醒成分股只涵蓋部分權重。")
    pm = ollama.ask(llm, ollama.RULES_EN + "You are the portfolio manager of a long-only fund. Synthesise the agents' views into "
                         "a short allocation memo: for each ticker give the consensus, key agreements/disagreements, "
                         "and a suggested action (buy/add/hold/trim/avoid) with sizing guidance. Be concise, use markdown.",
                    pm_prompt, language=lang)

    md = f"# 投資大師評估 · {date}\n\n"
    for t, e in etf_map.items():
        if e["holdings"]:
            md += (f"> **{t} 是 ETF**，沒有自己的財報（投資大師的方法看的是公司財報），所以改評估它的前 {len(e['holdings'])} 大成分股，"
                   f"再依權重加權成 ETF 的訊號；涵蓋 {sum(w for *_, w in e['holdings']):.0f}% 權重。{e['note']}\n\n")
        else:
            md += f"> **{t} 是 ETF**，{e['note'] or '沒有可評估的成分股'}，投資大師不適用；ETF 請改用 TimesFM 趨勢或 Laya 判斷。\n\n"
    md += f"## 🧭 投資組合經理備忘\n\n{pm}\n"
    first = next((c for c in consensus if c[0].startswith(ticker_order[0])), None)
    sig = first[2] if len(ticker_order) == 1 and first else None
    return {"summary": " / ".join(f"{c[0]} {c[2]}" for c in consensus), "signal": sig, "markdown": md,
            "tables": [table("共識", ["標的", "平均訊號", "觀點", "多/空/中"], consensus),
                       table("各代理人訊號", ["標的", "大師", "訊號", "信心", "值", "理由"], rows)]}


engine = Engine("hedgefund", "AI Hedge Fund 投資大師",
                "virattt/ai-hedge-fund v2：巴菲特、蒙格、葛拉漢、林區、杜肯米勒等人格代理人，以時點基本面快照推理並給出多空訊號，最後由投資組合經理彙整。",
                SCHEMA, run, repo="https://github.com/virattt/ai-hedge-fund",
                version=os.environ.get("UPSTREAM_REF", ""))
app = engine.app

if __name__ == "__main__":
    serve(engine, 8132)
