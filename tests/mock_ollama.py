"""A fake Ollama (native /api + OpenAI /v1) for offline smoke tests. Not shipped to production."""
import json
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
CALLS = []


def answer(messages, json_mode=False):
    full = " ".join(str(m.get("content", "")) for m in messages)
    text = full[-4000:]
    if "台股量化研究助理" in full:
        bad = "這個 JSON 有錯" not in full and "測試重試" in full
        return json.dumps({"name": "營收成長＋投信買超（模擬）", "description": "（模擬）",
                           "conditions": ["營收年增率 > 15", "rise(月營收, 2)" if not bad else "不存在的欄位 > 1",
                                          "sum(投信買賣超, 10) > 0", "本益比 < 25"],
                           "rank": "營收年增率", "ascending": False, "top_n": 10, "rebalance": "revenue", "stop_loss": 0.1,
                           "explanation": "（模擬）把「營收連續成長」寫成營收年增率>15 且連 2 個月成長，投信最近在買用 10 日買超加總>0，本益比低於 25。"},
                          ensure_ascii=False)
    if json_mode or '"signal"' in text:
        return json.dumps({"signal": "bullish", "confidence": 72, "reasoning": "（模擬）護城河穩固、ROE 高，估值合理。"})
    if "What is the sentiment of this news" in text:
        return ["positive", "neutral", "negative"][len(text) % 3]
    if "[Positive Developments]" in text or "Prediction & Analysis" in text:
        return ("[Positive Developments]:\n1. 營收成長\n\n[Potential Concerns]:\n1. 估值偏高\n\n"
                "[Prediction & Analysis]\nPrediction: Up by 1-2%\nAnalysis: （模擬）短期動能偏多。")
    if "FINAL TRANSACTION PROPOSAL" in text or "Rating" in text or "rating" in text:
        return "<think>hidden</think>**Rating: Buy**\n\n（模擬）最終決策：買進。FINAL TRANSACTION PROPOSAL: **BUY**"
    return "<think>hidden</think>（模擬回答）這是 mock Ollama 產生的內容。Rating: Hold"


@app.get("/api/tags")
def tags():
    return {"models": [{"name": "qwen3:32b", "size": 17_000_000_000,
                        "details": {"parameter_size": "27B", "quantization_level": "Q4_K_M"}},
                       {"name": "qwen3-embedding:4b", "size": 2_500_000_000, "details": {"parameter_size": "4B"}}]}


@app.post("/api/chat")
async def chat(req: Request):
    b = await req.json()
    CALLS.append(b)
    out = answer(b["messages"], b.get("format") == "json")
    if b.get("stream"):
        def gen():
            for i in range(0, len(out), 6):
                yield json.dumps({"message": {"content": out[i:i + 6]}, "done": False}) + "\n"
                time.sleep(0.01)
            yield json.dumps({"message": {"content": ""}, "done": True}) + "\n"
        return StreamingResponse(gen(), media_type="application/x-ndjson")
    return {"message": {"role": "assistant", "content": out}, "done": True}


@app.post("/v1/chat/completions")
async def oai(req: Request):
    b = await req.json()
    CALLS.append(b)
    out = answer(b["messages"], (b.get("response_format") or {}).get("type") == "json_object")
    out = out.replace("<think>hidden</think>", "")
    if b.get("response_format", {}).get("type") == "json_schema" or b.get("tools") and any(
            t.get("function", {}).get("name", "").lower().startswith(("research", "trader", "portfolio", "rating"))
            for t in b.get("tools", [])):
        pass
    head = json.dumps(b.get("messages", [])[:1], ensure_ascii=False)
    finish = "stop"
    if os.environ.get("MOCK_LENGTH_FOR") and os.environ["MOCK_LENGTH_FOR"] in head:
        finish = "length"  # pretend the node hit max_tokens
    if os.environ.get("MOCK_SLOW_FOR") and os.environ["MOCK_SLOW_FOR"] in head:
        time.sleep(float(os.environ.get("MOCK_SLOW_SEC", "8")))
    return JSONResponse({"id": "x", "object": "chat.completion", "created": int(time.time()), "model": b["model"],
                         "choices": [{"index": 0, "finish_reason": finish,
                                      "message": {"role": "assistant", "content": out}}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


@app.get("/stats")
def stats():
    return {"calls": len(CALLS)}


@app.get("/last")
def last():
    """Last request's system prompt (to check what context the terminal sent)."""
    if not CALLS:
        return {}
    msgs = CALLS[-1].get("messages", [])
    return {"system": next((m["content"] for m in msgs if m.get("role") == "system"), "")[:3000]}


@app.get("/calls")
def calls():
    """Per-call summary: tool names offered and whether the world block reached the prompt."""
    out = []
    for b in CALLS:
        text = json.dumps(b.get("messages", []), ensure_ascii=False)
        first = next((m.get("content") for m in b.get("messages", []) if isinstance(m.get("content"), str)), "") or ""
        out.append({"tools": [t.get("function", {}).get("name") for t in b.get("tools", []) or []],
                    "world": "God's Eye View" in text, "max_tokens": b.get("max_tokens"),
                    "max_completion_tokens": b.get("max_completion_tokens"), "head": first[:80]})
    return out
