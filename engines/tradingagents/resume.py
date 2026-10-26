"""Reuse finished analyst reports when a TradingAgents job is run again after a failure.

The four analysts run side by side; when one of them times out the whole job fails and the reports the
others already wrote are lost. Each analyst report is saved the moment it is written
(<engine dir>/resume/<ticker>_<date>_<language>.json). A later run of the same ticker, analysis date and
language within FT_TA_RESUME_HOURS (default 12) takes those reports instead of calling the model again,
so only the analysts that failed run. A job that finishes deletes its file: the next run is a fresh
analysis, not a replay.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Callable

# setup.py factory name → (analyst key, state key, label used by limits.Pipeline)
ANALYSTS = {
    "create_market_analyst": ("market", "market_report", "技術分析師"),
    "create_sentiment_analyst": ("social", "sentiment_report", "情緒分析師"),
    "create_news_analyst": ("news", "news_report", "新聞分析師"),
    "create_fundamentals_analyst": ("fundamentals", "fundamentals_report", "基本面分析師"),
}
_lock = threading.Lock()


class Store:
    def __init__(self, root: Path, ticker: str, date: str, language: str, hours: float = 12):
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{ticker}_{date}_{language}")
        self.path = Path(root) / "resume" / f"{slug}.json"
        self.hours = hours

    def load(self) -> dict[str, dict]:
        """{analyst key: {"report", "saved", "job"}} still fresh enough to reuse."""
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        cutoff = time.time() - self.hours * 3600
        return {k: v for k, v in (d.get("reports") or {}).items()
                if isinstance(v, dict) and (v.get("report") or "").strip() and v.get("saved", 0) >= cutoff}

    def save(self, key: str, report: str, job: str = ""):
        if not (report or "").strip():
            return
        with _lock:
            try:
                d = json.loads(self.path.read_text())
            except (OSError, ValueError):
                d = {}
            d.setdefault("reports", {})[key] = {"report": report, "saved": time.time(), "job": job}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False))
            tmp.replace(self.path)

    def clear(self):
        try:
            self.path.unlink()
        except OSError:
            pass


def install(store: Store, reuse: dict[str, dict], log: Callable[[str], None], pipe=None, job_id: str = ""):
    """Wrap the analyst factories in tradingagents.graph.setup (call after limits.install):
    analysts with a saved report answer with it at once; the others save their report when they write it."""
    import tradingagents.graph.setup as setup
    from langchain_core.messages import AIMessage

    for name, (key, state_key, label) in ANALYSTS.items():
        inner = getattr(setup, name)

        def make(inner=inner, key=key, state_key=state_key, label=label):
            def factory(llm, *a, **k):
                saved = reuse.get(key)
                if saved:
                    report = saved["report"]
                    when = time.strftime("%m-%d %H:%M", time.localtime(saved.get("saved", 0)))

                    def reused_node(state):
                        log(f"↺ {label}：沿用 {when} 的報告（{len(report)} 字），不再呼叫模型")
                        if pipe:
                            pipe.reused(label, f"沿用 {when}")
                        return {"messages": [AIMessage(content=report)], state_key: report}
                    return reused_node

                node = inner(llm, *a, **k)

                def saving_node(state):
                    out = node(state)
                    rep = (out or {}).get(state_key) if isinstance(out, dict) else None
                    if rep:
                        store.save(key, rep, job_id)
                    return out
                return saving_node
            factory.__name__ = getattr(inner, "__name__", name)
            return factory
        setattr(setup, name, make())
