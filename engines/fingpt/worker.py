"""FinGPT worker — FinGPT-Forecaster and FinGPT v3 sentiment prompts on local Ollama.

Prompts follow AI4Finance-Foundation/FinGPT (fingpt/FinGPT_Forecaster/app.py and
FinGPT_Sentiment_Analysis_v3 instruction template). By default they run on your
general Ollama model; point the `fingpt` model setting at a FinGPT LoRA merged
into an Ollama model if you build one.
"""
from __future__ import annotations

import datetime as dt
import os
import re

import pandas as pd

from ft_common import market, ollama, twmarket
from ft_common.engine import Ctx, Engine, serve, split_tickers, table, today

SCHEMA = [
    {"name": "mode", "label": "任務", "type": "select", "default": "forecaster",
     "options": [{"value": "forecaster", "label": "FinGPT-Forecaster 下週走勢預測"},
                 {"value": "sentiment", "label": "FinGPT 新聞情緒（逐則）"}]},
    {"name": "ticker", "label": "標的", "type": "ticker", "required": True, "placeholder": "AAPL / 2330.TW"},
    {"name": "date", "label": "預測基準日", "type": "date", "default": "today"},
    {"name": "n_weeks", "label": "回看週數", "type": "int", "default": 3, "min": 1, "max": 8},
    {"name": "use_basics", "label": "加入財報基本數據", "type": "bool", "default": True},
    {"name": "text", "label": "自訂新聞（情緒模式，每行一則；留空則抓該股新聞）", "type": "textarea"},
    {"name": "language", "label": "輸出語言", "type": "select", "default": "Traditional Chinese",
     "options": [{"value": "Traditional Chinese", "label": "繁體中文"}, {"value": "English", "label": "English"}]},
]

# --- verbatim from FinGPT_Forecaster/app.py
SYSTEM_PROMPT = ("You are a seasoned stock market analyst. Your task is to list the positive developments and "
                 "potential concerns for companies based on relevant news and basic financials from the past weeks, "
                 "then provide an analysis and prediction for the companies' stock price movement for the upcoming "
                 "week. Your answer format should be as follows:\n\n[Positive Developments]:\n1. ...\n\n"
                 "[Potential Concerns]:\n1. ...\n\n[Prediction & Analysis]\nPrediction: ...\nAnalysis: ...")
# --- FinGPT v3 sentiment instruction template
SENT_TMPL = ("Instruction: What is the sentiment of this news? Please choose an answer from "
             "{{negative/neutral/positive}}.\nInput: {news}\nAnswer: ")


def _news(sym: str, n: int) -> list[dict]:
    """Taiwan listings: Chinese headlines from Google News (yfinance has few); others: yfinance."""
    if twmarket.is_tw(sym):
        return twmarket.news_zh(twmarket.news_query_for(sym), n, days=60)
    return market.news(sym, n)


def company_prompt(sym: str, info: dict) -> str:
    zh = twmarket.zh_name(sym)
    name = f"{zh} ({info.get('longName') or sym})" if zh else (info.get('longName') or sym)
    return (f"[Company Introduction]:\n\n{name} is a leading entity in the "
            f"{info.get('industry') or info.get('sector') or 'N/A'} sector. Incorporated and publicly traded on "
            f"{info.get('exchange') or 'N/A'}, the company has a market capitalization of "
            f"{market.fmt_big(info.get('marketCap'))} in {info.get('currency') or 'N/A'}, and it is based in "
            f"{info.get('country') or 'N/A'}. As a seasoned market analyst, your task is to analyse the company's "
            f"recent price movements and news.")


def basics_prompt(sym: str) -> str:
    st = market.statements(sym, "quarterly") or market.statements(sym, "yearly")
    inc = st.get("income") or {}
    if not inc.get("columns"):
        return "[Basic Financials]:\n\nNo basic financial reported."
    col = inc["columns"][0]
    lines = [f"{r[0]}: {market.fmt_big(r[1])}" for r in inc["rows"] if len(r) > 1]
    return f"[Basic Financials]:\n\nSome recent basic financials of {sym}, reported at {col}, are presented below:\n\n" + "\n".join(lines)


def forecaster(ctx: Ctx, sym: str, end: dt.date, n_weeks: int, use_basics: bool, llm: dict, lang: str):
    ctx.step(10, "抓取股價與新聞")
    info = market.info(sym)
    hist = market.history(sym, start=(end - dt.timedelta(weeks=n_weeks + 1)).isoformat(),
                          end=(end + dt.timedelta(days=1)).isoformat())
    if len(hist) < 3:
        raise RuntimeError(f"{sym} 無足夠股價資料")
    hist.index = pd.DatetimeIndex(hist.index).tz_localize(None)
    news = _news(sym, 40)
    blocks = []
    for w in range(n_weeks, 0, -1):
        ws, we = end - dt.timedelta(weeks=w), end - dt.timedelta(weeks=w - 1)
        h = hist[(hist.index.date > ws) & (hist.index.date <= we)]
        if len(h) < 1:
            continue
        p0, p1 = float(h.Close.iloc[0]), float(h.Close.iloc[-1])
        head = (f"From {ws} to {we}, {sym}'s stock price {'increased' if p1 >= p0 else 'decreased'} from "
                f"{p0:.2f} to {p1:.2f}. Company news during this period are listed below:\n\n")
        items = [n for n in news if n.get("time") and ws < dt.date.fromisoformat(n["time"][:10]) <= we][:5]
        body = "\n\n".join(f"[Headline]: {n['title']}\n[Summary]: {n.get('summary') or n['title']}" for n in items)
        blocks.append(head + (body or "No relative news reported."))
    prompt = company_prompt(sym, info) + "\n\n" + "\n\n".join(blocks)
    if use_basics:
        prompt += "\n\n" + basics_prompt(sym)
    prompt += (f"\n\nBased on all the information before {end}, let's first analyze the positive developments and "
               f"potential concerns for {sym}. Come up with 2-4 most important factors respectively and keep them "
               f"concise. Most factors should be inferred from company related news. Then make your prediction of "
               f"the {sym} stock price movement for next week ({end + dt.timedelta(days=1)} - "
               f"{end + dt.timedelta(days=7)}). Provide a summary analysis to support your prediction.")
    sysmsg = SYSTEM_PROMPT
    if lang == "Traditional Chinese":
        sysmsg += ("\n\nWrite the content in Traditional Chinese (繁體中文) but keep the section headers "
                   "[Positive Developments], [Potential Concerns], [Prediction & Analysis] and the line "
                   "'Prediction: Up/Down by X-Y%' in English.")
    if llm.get("lessons"):
        sysmsg += ("\n\nLessons from reviewing your past weekly predictions against actual prices "
                   "(avoid repeating these mistakes):\n" + llm["lessons"])
        ctx.log("帶入過去檢討（分析紀錄頁可關閉）")
    ctx.step(40, f"FinGPT-Forecaster 推論（{llm['model']}）")
    ctx.log(f"prompt 長度 {len(prompt)} 字元，{len(blocks)} 週資料")
    out = ollama.chat(llm, [{"role": "system", "content": sysmsg}, {"role": "user", "content": prompt}])
    m = re.search(r"Prediction:\s*(Up|Down|Flat|上漲|下跌|持平)[^\n]*", out, re.I)
    pred = m.group(0).strip() if m else "—"
    sig = "看多" if m and m.group(1).lower() in ("up", "上漲") else "看空" if m and m.group(1).lower() in ("down", "下跌") else "中性"
    md = (f"# FinGPT-Forecaster · {sym} · 基準日 {end}\n\n**{pred}**\n\n"
          + out.replace("[Positive Developments]", "### ✅ [Positive Developments]")
                .replace("[Potential Concerns]", "### ⚠️ [Potential Concerns]")
                .replace("[Prediction & Analysis]", "### 🔮 [Prediction & Analysis]")
          + f"\n\n<details><summary>FinGPT prompt</summary>\n\n```\n{prompt}\n```\n</details>\n")
    return {"summary": f"{sym} 下週：{pred}", "signal": sig, "markdown": md}


def sentiment(ctx: Ctx, sym: str, text: str, llm: dict):
    if text and text.strip():
        heads = [{"title": l.strip(), "publisher": "自訂", "time": ""} for l in text.splitlines() if l.strip()]
    else:
        ctx.step(10, f"抓取 {sym} 新聞")
        heads = _news(sym, 25)
    if not heads:
        raise RuntimeError("沒有可分析的新聞")
    rows, score = [], 0
    for i, n in enumerate(heads):
        ctx.step(15 + 80 * i / len(heads), f"情緒 {i + 1}/{len(heads)}")
        ans = ollama.chat(llm, [{"role": "user", "content": SENT_TMPL.format(news=n["title"])}]).lower()
        lab = "positive" if "positive" in ans else "negative" if "negative" in ans else "neutral"
        score += {"positive": 1, "negative": -1}.get(lab, 0)
        rows.append([(n.get("time") or "")[:10], {"positive": "🟢 正面", "negative": "🔴 負面", "neutral": "⚪ 中性"}[lab],
                     n["title"], n.get("publisher") or ""])
    pos = sum("正面" in r[1] for r in rows)
    neg = sum("負面" in r[1] for r in rows)
    s = score / len(rows)
    sig = "看多" if s > 0.2 else "看空" if s < -0.2 else "中性"
    md = (f"# FinGPT 新聞情緒 · {sym}\n\n情緒分數 **{s:+.2f}**（{pos} 正面 / {neg} 負面 / {len(rows) - pos - neg} 中性，"
          f"共 {len(rows)} 則）→ **{sig}**\n\n使用 FinGPT v3 指令模板：`{SENT_TMPL.splitlines()[0].replace('{{', '{').replace('}}', '}')}`\n")
    return {"summary": f"{sym} 情緒 {s:+.2f} {sig}", "signal": sig, "markdown": md,
            "tables": [table("逐則情緒", ["日期", "情緒", "標題", "來源"], rows)]}


def run(ctx: Ctx) -> dict:
    p = ctx.params
    sym = split_tickers(p["ticker"])[0]
    end = p.get("date") or today()
    end = dt.date.fromisoformat(today() if end == "today" else end)
    llm = ollama.resolve(ctx.llm, "fingpt")
    lang = p.get("language") or "Traditional Chinese"
    if p.get("mode") == "sentiment":
        return sentiment(ctx, sym, p.get("text") or "", llm)
    return forecaster(ctx, sym, end, int(p.get("n_weeks") or 3), bool(p.get("use_basics", True)), llm, lang)


engine = Engine("fingpt", "FinGPT 金融大模型",
                "AI4Finance-Foundation/FinGPT：Forecaster（正面發展/潛在隱憂/下週預測）與 v3 新聞情緒分類的原版提示模板，跑在本機 Ollama。",
                SCHEMA, run, repo="https://github.com/AI4Finance-Foundation/FinGPT", version=os.environ.get("UPSTREAM_REF", ""))
app = engine.app

if __name__ == "__main__":
    serve(engine, 8135)
