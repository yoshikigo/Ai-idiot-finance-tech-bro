"""Tiny local-Ollama client used by every engine (no cloud providers anywhere).

`llm` is a dict passed per job by the terminal:
    {"url": "http://127.0.0.1:11434", "model": "qwen3:32b",
     "num_ctx": 65536, "temperature": 0.2, "think": false}
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterator

import httpx

DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.environ.get("FT_MODEL", "qwen3:32b")

_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

LANG_HINT = {
    "Traditional Chinese": "請一律使用繁體中文（台灣用語）回答。",
    "English": "Answer in English.",
}


def resolve(llm: dict | None, key: str | None = None) -> dict:
    """Fill defaults; `key` picks a per-engine model override (llm['models'][key])."""
    llm = dict(llm or {})
    llm.setdefault("url", DEFAULT_URL)
    llm["url"] = str(llm["url"]).rstrip("/")
    models = llm.get("models") or {}
    if key and models.get(key):
        llm["model"] = models[key]
    llm.setdefault("model", DEFAULT_MODEL)
    llm.setdefault("temperature", 0.2)
    llm.setdefault("think", False)
    return llm


def strip_think(text: str) -> str:
    text = _THINK.sub("", text or "")
    # unterminated <think> (model ran out of tokens) – drop it
    if "<think>" in text and "</think>" not in text:
        text = text.split("<think>")[0]
    return text.strip()


def _payload(llm: dict, messages: list[dict], stream: bool, json_mode: bool) -> dict:
    opts = {"temperature": llm.get("temperature", 0.2)}
    if llm.get("num_ctx"):
        opts["num_ctx"] = int(llm["num_ctx"])
    body = {"model": llm["model"], "messages": messages, "stream": stream, "options": opts}
    if llm.get("think") is not None:
        body["think"] = bool(llm.get("think"))
    if json_mode:
        body["format"] = "json"
    return body


def chat(llm: dict, messages: list[dict], *, json_mode: bool = False, timeout: float = 1800) -> str:
    llm = resolve(llm)
    body = _payload(llm, messages, False, json_mode)
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{llm['url']}/api/chat", json=body)
        if r.status_code == 400 and "think" in r.text.lower():
            body.pop("think", None)
            r = c.post(f"{llm['url']}/api/chat", json=body)
        r.raise_for_status()
        return strip_think(r.json().get("message", {}).get("content", ""))


def chat_stream(llm: dict, messages: list[dict], timeout: float = 1800) -> Iterator[str]:
    llm = resolve(llm)
    body = _payload(llm, messages, True, False)
    filt = _ThinkFilter()
    with httpx.Client(timeout=timeout) as c:
        with c.stream("POST", f"{llm['url']}/api/chat", json=body) as r:
            if r.status_code >= 400:
                r.read()
                raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                out = filt.feed(d.get("message", {}).get("content", ""))
                if out:
                    yield out
                if d.get("done"):
                    break
    tail = filt.flush()
    if tail:
        yield tail


class _ThinkFilter:
    """Drops <think>…</think> spans from a token stream even when tags are split across chunks."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf, self.inside = "", False

    def feed(self, s: str) -> str:
        self.buf += s
        out = []
        while True:
            tag = self.CLOSE if self.inside else self.OPEN
            i = self.buf.find(tag)
            if i >= 0:
                if not self.inside:
                    out.append(self.buf[:i])
                self.buf = self.buf[i + len(tag):]
                self.inside = not self.inside
                continue
            # keep a possible partial tag at the end
            keep = 0
            for k in range(1, len(tag)):
                if self.buf.endswith(tag[:k]):
                    keep = k
            if not self.inside:
                out.append(self.buf[: len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:]
            return "".join(out)

    def flush(self) -> str:
        s = "" if self.inside else self.buf
        self.buf = ""
        return s


def ask(llm: dict, system: str, user: str, *, language: str | None = None, json_mode: bool = False) -> str:
    if language and language in LANG_HINT:
        system = f"{system}\n\n{LANG_HINT[language]}"
    return chat(llm, [{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=json_mode)


def list_models(url: str | None = None) -> list[dict]:
    url = (url or DEFAULT_URL).rstrip("/")
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{url}/api/tags")
        r.raise_for_status()
        out = []
        for m in r.json().get("models", []):
            out.append({"name": m.get("name"), "size": m.get("size"),
                        "params": (m.get("details") or {}).get("parameter_size"),
                        "quant": (m.get("details") or {}).get("quantization_level")})
        return sorted(out, key=lambda m: m["name"] or "")
