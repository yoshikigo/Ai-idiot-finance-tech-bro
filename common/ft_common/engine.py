"""Minimal job server shared by every AI engine container.

Each engine declares a form schema + a run(ctx) function. Jobs run one at a time
(single GPU / single Ollama) in a background thread; logs stream over SSE.

Routes:  GET /health  GET /meta  GET /jobs  POST /jobs
         GET /jobs/{id}  DELETE /jobs/{id}  GET /jobs/{id}/events (SSE)
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import io
import json
import logging
import os
import queue
import sys
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

DATA = Path(os.environ.get("FT_DATA", "/data"))
MAX_LOG = 4000


class Cancelled(Exception):
    pass


def _safe_json_response():
    from fastapi.responses import JSONResponse

    class SafeJSONResponse(JSONResponse):
        """Default response for every FT app: a stray NaN/inf (e.g. a half-filled bar from Yahoo) becomes null
        instead of turning the whole endpoint into a 500."""
        def render(self, content) -> bytes:
            return json.dumps(jsonable(content), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    return SafeJSONResponse


def jsonable(o):
    """Make a result safe for strict JSON (FastAPI rejects NaN/Infinity and numpy scalars): NaN/inf → None."""
    import math
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    if isinstance(o, bool) or o is None or isinstance(o, str):
        return o
    if isinstance(o, int):
        return o
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    try:
        import numpy as np
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            f = float(o)
            return f if math.isfinite(f) else None
        if isinstance(o, np.ndarray):
            return jsonable(o.tolist())
    except ImportError:
        pass
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Job:
    def __init__(self, engine: str, params: dict, llm: dict | None):
        self.id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.engine = engine
        self.params = params
        self.llm = llm or {}
        self.status = "queued"
        self.created = now()
        self.started: str | None = None
        self.finished: str | None = None
        self.log: list[str] = []
        self.progress = 0.0
        self.stage = ""
        self.result: dict | None = None
        self.error: str | None = None
        self.cancel = threading.Event()
        self.pipeline: list | None = None   # [{"stage", "nodes": [{"label", "status", ...}]}] (live per-node status)
        self.pipe_rev = 0

    def brief(self) -> dict:
        r = self.result or {}
        return {"id": self.id, "engine": self.engine, "status": self.status, "created": self.created,
                "started": self.started, "finished": self.finished, "progress": self.progress,
                "stage": self.stage, "title": _title(self.params), "summary": r.get("summary"),
                "signal": r.get("signal"), "error": self.error, "pipeline": self.pipeline}

    def full(self) -> dict:
        return {**self.brief(), "params": self.params, "llm": {k: v for k, v in self.llm.items() if k != "models"},
                "log": self.log[-MAX_LOG:], "result": self.result}

    @classmethod
    def load(cls, d: dict) -> "Job":
        j = cls.__new__(cls)
        j.id, j.engine, j.params, j.llm = d["id"], d["engine"], d.get("params", {}), d.get("llm", {})
        j.status, j.created, j.started, j.finished = d["status"], d["created"], d.get("started"), d.get("finished")
        j.log, j.progress, j.stage = d.get("log", []), d.get("progress", 0), d.get("stage", "")
        j.result, j.error, j.cancel = d.get("result"), d.get("error"), threading.Event()
        j.pipeline, j.pipe_rev = d.get("pipeline"), 0
        if j.status in ("queued", "running"):
            j.status, j.error = "error", "服務重啟，工作中斷"
            for st in j.pipeline or []:
                for n in st.get("nodes", []):
                    if n.get("status") == "running":
                        n["status"], n["note"] = "error", "服務重啟時正在執行"
        return j


def _title(p: dict) -> str:
    for k in ("ticker", "tickers", "universe", "text"):
        if p.get(k):
            v = p[k]
            return (", ".join(v) if isinstance(v, list) else str(v))[:60]
    return ""


class Ctx:
    """Handed to run(): params, llm settings, logging, progress, cancellation."""

    def __init__(self, job: Job, engine: "Engine"):
        self.job, self.engine = job, engine
        self.params = job.params
        self.llm = job.llm
        self.dir = engine.dir

    def log(self, msg: str):
        for line in str(msg).rstrip("\n").split("\n"):
            self.job.log.append(f"{dt.datetime.now():%H:%M:%S} {line}")
        if len(self.job.log) > MAX_LOG * 2:
            del self.job.log[: len(self.job.log) - MAX_LOG]

    def step(self, pct: float, stage: str):
        self.check()
        self.job.progress = max(0.0, min(100.0, pct))
        self.job.stage = stage
        self.log(f"▶ {stage}")

    def check(self):
        if self.job.cancel.is_set():
            raise Cancelled()

    @contextlib.contextmanager
    def capture(self):
        """Redirect stdout/stderr + python logging into the job log."""
        ctx = self

        class W(io.TextIOBase):
            def __init__(self):
                self.buf = ""

            def write(self, s):
                ctx.check()
                self.buf += s.replace("\r", "\n")
                while "\n" in self.buf:
                    line, self.buf = self.buf.split("\n", 1)
                    if line.strip():
                        ctx.log(line)
                return len(s)

            def flush(self):
                if self.buf.strip():
                    ctx.log(self.buf)
                self.buf = ""

        w = W()
        h = logging.StreamHandler(w)
        h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(h)
        try:
            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                yield
        finally:
            w.flush()
            root.removeHandler(h)


class Engine:
    def __init__(self, name: str, title: str, description: str, schema: list[dict],
                 run: Callable[[Ctx], dict], repo: str = "", version: str = ""):
        self.name, self.title, self.description, self.schema = name, title, description, schema
        self.run_fn, self.repo, self.version = run, repo, version
        self.dir = DATA / name
        (self.dir / "jobs").mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.q: queue.Queue[Job] = queue.Queue()
        self._load()
        threading.Thread(target=self._worker, daemon=True).start()
        self.app = self._make_app()

    # ---------------------------------------------------------- persistence
    def _path(self, j: Job) -> Path:
        return self.dir / "jobs" / f"{j.id}.json"

    def _save(self, j: Job):
        tmp = self._path(j).with_suffix(".tmp")
        tmp.write_text(json.dumps(j.full(), ensure_ascii=False, default=str))
        tmp.replace(self._path(j))

    def _load(self):
        files = sorted((self.dir / "jobs").glob("*.json"))[-200:]
        for f in files:
            try:
                j = Job.load(json.loads(f.read_text()))
                self.jobs[j.id] = j
            except Exception:  # noqa: BLE001
                continue

    # ---------------------------------------------------------- worker
    def _worker(self):
        while True:
            j = self.q.get()
            if j.cancel.is_set():
                j.status, j.finished = "cancelled", now()
                self._save(j)
                continue
            j.status, j.started = "running", now()
            self._save(j)
            ctx = Ctx(j, self)
            try:
                ctx.log(f"開始 {self.title} · model={j.llm.get('model', '(default)')}")
                j.result = jsonable(self.run_fn(ctx) or {})
                j.status, j.progress, j.stage = "done", 100.0, "完成"
            except Cancelled:
                j.status, j.error = "cancelled", "已取消"
            except Exception as e:  # noqa: BLE001
                j.status, j.error = "error", f"{type(e).__name__}: {e}"
                ctx.log(traceback.format_exc())
            j.finished = now()
            if j.status == "done":
                self._after_done(j, ctx)
            ctx.log(f"■ {j.status}")
            self._save(j)

    def _after_done(self, j: "Job", ctx: "Ctx"):
        """Record review facts (price at forecast time…) and queue the Obsidian note."""
        try:
            from . import notes
            j.result.setdefault("meta", notes.job_meta(j.full()))
            rel = notes.write_job_note(j.full())
            if rel:
                j.result["meta"]["note"] = rel
                ctx.log(f"📝 Obsidian：{rel}")
        except Exception as e:  # noqa: BLE001  never fail a finished job over a note
            ctx.log(f"Obsidian 筆記失敗：{type(e).__name__}: {e}")

    # ---------------------------------------------------------- api
    def _make_app(self) -> FastAPI:
        app = FastAPI(title=f"ft-{self.name}", default_response_class=_safe_json_response())
        eng = self

        @app.get("/health")
        def health():
            return {"ok": True, "engine": eng.name, "queue": eng.q.qsize()}

        @app.get("/meta")
        def meta():
            return {"name": eng.name, "title": eng.title, "description": eng.description,
                    "schema": eng.schema, "repo": eng.repo, "version": eng.version}

        @app.get("/jobs")
        def list_jobs(limit: int = 50):
            js = sorted(eng.jobs.values(), key=lambda j: j.id, reverse=True)[:limit]
            return [j.brief() for j in js]

        @app.post("/jobs")
        async def create(req: Request):
            body = await req.json()
            params = body.get("params") or {}
            for f in eng.schema:
                if f.get("required") and not params.get(f["name"]):
                    raise HTTPException(400, f"缺少欄位：{f.get('label', f['name'])}")
            j = Job(eng.name, params, body.get("llm"))
            eng.jobs[j.id] = j
            eng._save(j)
            eng.q.put(j)
            return j.brief()

        @app.get("/jobs/{jid}")
        def get(jid: str):
            j = eng.jobs.get(jid)
            if not j:
                raise HTTPException(404, "no such job")
            return j.full()

        @app.post("/jobs/{jid}/note")
        def note(jid: str):
            j = eng.jobs.get(jid)
            if not j or j.status != "done":
                raise HTTPException(404, "沒有完成的工作")
            from . import notes
            j.result = j.result or {}
            j.result.setdefault("meta", notes.job_meta(j.full()))
            rel = notes.write_job_note(j.full(), force=True)
            j.result["meta"]["note"] = rel
            eng._save(j)
            return {"note": rel}

        @app.delete("/jobs/{jid}")
        def delete(jid: str):
            j = eng.jobs.get(jid)
            if not j:
                raise HTTPException(404, "no such job")
            if j.status in ("queued", "running"):
                j.cancel.set()
                return {"cancelling": True}
            eng.jobs.pop(jid, None)
            eng._path(j).unlink(missing_ok=True)
            return {"deleted": True}

        @app.get("/jobs/{jid}/events")
        async def events(jid: str, request: Request):
            j = eng.jobs.get(jid)
            if not j:
                raise HTTPException(404, "no such job")

            async def gen():
                sent = 0
                last = None
                while True:
                    if await request.is_disconnected():
                        return
                    lines = j.log[sent:]
                    if lines:
                        sent += len(lines)
                        yield f"data: {json.dumps({'type': 'log', 'lines': lines}, ensure_ascii=False)}\n\n"
                    b = j.brief()
                    sig = (b["status"], b["progress"], b["stage"], j.pipe_rev)
                    if sig != last:
                        last = sig
                        yield f"data: {json.dumps({'type': 'status', **b}, ensure_ascii=False)}\n\n"
                    if j.status in ("done", "error", "cancelled") and sent >= len(j.log):
                        yield f"data: {json.dumps({'type': 'end'})}\n\n"
                        return
                    await asyncio.sleep(0.7)

            return StreamingResponse(gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        return app


# ---------------------------------------------------------- isolated (child process) jobs
class ChildCtx:
    """Same surface as Ctx, used inside the child process: everything goes to stdout."""

    def __init__(self, payload: dict):
        self.params, self.llm, self.dir = payload["params"], payload["llm"], Path(payload["dir"])

    def log(self, msg):
        print(msg, flush=True)

    def step(self, pct, stage):
        print(f"@@STEP {pct} {stage}", flush=True)

    def check(self):
        pass

    @contextlib.contextmanager
    def capture(self):
        yield


def run_isolated(ctx: Ctx, script: str) -> dict:
    """Run `script --child payload.json` in a fresh interpreter (libraries like Qlib
    can't be re-initialised in-process; also frees RAM and makes cancel a hard kill)."""
    import subprocess
    tmp = ctx.dir / "tmp"
    tmp.mkdir(exist_ok=True)
    pin, pout = tmp / f"{ctx.job.id}.in.json", tmp / f"{ctx.job.id}.out.json"
    pin.write_text(json.dumps({"params": ctx.params, "llm": ctx.llm, "dir": str(ctx.dir), "out": str(pout)},
                              ensure_ascii=False))
    proc = subprocess.Popen([sys.executable, "-u", script, "--child", str(pin)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)

    def watch():
        while proc.poll() is None:
            if ctx.job.cancel.wait(0.5):
                proc.kill()
                return
    threading.Thread(target=watch, daemon=True).start()
    tail: list[str] = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("@@STEP "):
                _, pct, stage = line.split(" ", 2)
                ctx.job.progress, ctx.job.stage = float(pct), stage
                ctx.log(f"▶ {stage}")
            elif line.strip():
                ctx.log(line)
                tail = (tail + [line])[-15:]
        rc = proc.wait()
        if ctx.job.cancel.is_set():
            raise Cancelled()
        if rc != 0 or not pout.exists():
            raise RuntimeError("子程序失敗：\n" + "\n".join(tail[-8:]))
        return json.loads(pout.read_text())
    finally:
        pin.unlink(missing_ok=True)
        pout.unlink(missing_ok=True)


def child_main(compute: Callable[[Any], dict]) -> bool:
    """Call at the top of `if __name__ == '__main__'`; returns True if it handled a child run."""
    if len(sys.argv) < 3 or sys.argv[1] != "--child":
        return False
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    payload = json.loads(Path(sys.argv[2]).read_text())
    res = compute(ChildCtx(payload))
    Path(payload["out"]).write_text(json.dumps(res, ensure_ascii=False, default=str))
    sys.exit(0)


def serve(engine: Engine, port: int):
    import uvicorn
    host = os.environ.get("FT_BIND", "127.0.0.1")
    uvicorn.run(engine.app, host=host, port=int(os.environ.get("FT_PORT", port)), log_level="warning")


# ---------------------------------------------------------- result helpers
def table(title: str, columns: list[str], rows: list[list[Any]]) -> dict:
    return {"title": title, "columns": columns, "rows": rows}


def line_chart(title: str, series: dict[str, list[tuple[str, float]]]) -> dict:
    return {"title": title, "type": "line",
            "series": [{"name": n, "data": [[str(t), float(v)] for t, v in pts]} for n, pts in series.items()]}


def today() -> str:
    return dt.date.today().isoformat()


def split_tickers(v) -> list[str]:
    if isinstance(v, list):
        items = v
    else:
        items = str(v or "").replace("，", ",").replace(" ", ",").split(",")
    return [normalize_symbol(s) for s in items if s.strip()]


def normalize_symbol(s: str) -> str:
    """'2330' → '2330.TW'; '2330 TT' → '2330.TW'; 'aapl' → 'AAPL'."""
    s = s.strip().upper()
    parts = s.split()
    exch = {"TT": ".TW", "TW": ".TW", "TWO": ".TWO", "US": "", "JP": ".T", "JT": ".T", "HK": ".HK",
            "LN": ".L", "GR": ".DE", "GY": ".DE", "FP": ".PA", "KS": ".KS", "CN": ".TO", "AU": ".AX"}
    if len(parts) >= 2 and parts[1] in exch:
        s = parts[0] + exch[parts[1]]
    elif len(parts) >= 1:
        s = parts[0]
    if s.isdigit() and 4 <= len(s) <= 6:
        s += ".TW"
    return s
