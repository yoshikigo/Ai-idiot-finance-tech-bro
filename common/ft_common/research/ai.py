"""中文選股想法 → strategy spec (JSON) with the local Ollama model, validated against the expression grammar.

The model never writes code: it fills a JSON spec whose conditions are parsed by expr.py's whitelist.
"""
from __future__ import annotations

import json
import re

from .. import ollama
from . import backtest, expr, fields as F, presets

SYSTEM = """你是台股量化研究助理。把使用者的中文選股想法轉成「策略 JSON」，只輸出 JSON，不要其他文字。

可用欄位（直接用中文名稱當變數；D=日、M=月、Q=季，月/季資料以法定公布日對齊）：
{fields}

可用函數：
{funcs}
運算：+ - * /、比較 > >= < <= == !=、且用 &、或用 |、非用 ~。條件要用括號包好，例如 (收盤價 > 10) & (成交量 > 500)。
月/季欄位的 shift、rise、ma 是以「期」計（月或季），日欄位以交易日計。

JSON 格式：
{{"name": "策略名稱", "description": "一句話說明", "conditions": ["布林條件", ...], "rank": "排名用的數值運算式或空字串",
 "ascending": false, "top_n": 10, "rebalance": "D|W|M|Q|revenue|financial", "stop_loss": null 或 0.1,
 "take_profit": null, "max_weight": 0.2, "start": "2016-01-01",
 "universe": {{"market": "all|上市|上櫃", "min_value": 20, "exclude_finance": false, "include_etf": false}},
 "explanation": "用繁體中文 2-4 句說明你怎麼把想法轉成條件、有哪些假設"}}

規則：
- 只能用上面列出的欄位與函數；不確定的概念用最接近的欄位並在 explanation 說明。
- conditions 每一條都是布林（比較或 new_high/rise/always 這類）。rank 是數值（越大越好時 ascending=false）。
- 跟月營收有關的策略 rebalance 用 "revenue"；跟財報有關用 "financial"；籌碼、技術面用 "W" 或 "D"；價值股用 "M"。
- min_value 是 20 日平均成交金額門檻（百萬元），預設 20，避免買不到的冷門股。
- 範例：
{examples}
"""


def _examples() -> str:
    out = []
    for p in presets.PRESETS[:4]:
        out.append(f"想法：{p['description']}\nJSON：" + json.dumps(
            {k: p[k] for k in ("name", "conditions", "rank", "ascending", "top_n", "rebalance") if k in p}
            | {"stop_loss": p.get("stop_loss")}, ensure_ascii=False))
    return "\n".join(out)


def system_prompt() -> str:
    fl = "\n".join(f"- {f.name}（{f.freq}，{f.unit}）：{f.desc}" for f in F.FIELDS)
    fn = "\n".join(f"- {h['name']}：{h['desc']}，例 {h['example']}" for h in expr.help_table())
    return SYSTEM.format(fields=fl, funcs=fn, examples=_examples())


def _extract(text: str) -> dict:
    text = ollama.strip_think(text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("模型沒有回傳 JSON")
    return json.loads(m.group(0))


def build(prompt: str, llm: dict, tries: int = 3) -> dict:
    llm = ollama.resolve(llm, "research")
    msgs = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": f"想法：{prompt.strip()}"}]
    last_err = ""
    for _ in range(tries):
        raw = ollama.chat(llm, msgs, json_mode=True, timeout=600)
        try:
            d = _extract(raw)
            expl = d.pop("explanation", "")
            spec = backtest.normalize(d)
            for c in spec["conditions"] + ([spec["rank"]] if spec["rank"] else []):
                expr.names(c)
            spec["description"] = spec.get("description") or prompt.strip()[:120]
            return {"spec": spec, "explanation": expl, "model": llm["model"], "prompt": prompt}
        except (ValueError, backtest.SpecError, expr.ExprError, json.JSONDecodeError) as e:
            last_err = str(e)
            msgs += [{"role": "assistant", "content": raw[:4000]},
                     {"role": "user", "content": f"這個 JSON 有錯：{last_err}。請修正後只輸出完整 JSON。"}]
    raise ValueError(f"AI 產生的策略無法使用：{last_err}")
