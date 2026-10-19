// Laya sidecar: one JSON object per line on stdin → one per line on stdout.
//   {"id":1,"op":"load"}                              → {"id":1,"ok":true,"model_dir":…}
//   {"id":2,"op":"decide","state":{…},"questions":{…}} → {"id":2,"ok":true,"result":{model,answers,usage}}
// Unsolicited lines: {"event":"progress","file":…,"received":…,"total":…} while the weights download.
// Uses @receptron/laya (MIT) → ONNX Runtime CPU; weights (Apache-2.0, ~1.7 GB) from receptron/laya-onnx.
import { createInterface } from "node:readline";
import { Laya } from "@receptron/laya";

const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");
let laya = null;
let loading = null;
let lastPct = -1;

function load() {
  if (laya) return Promise.resolve(laya);
  if (!loading) {
    const threads = Number(process.env.LAYA_THREADS || 0);
    loading = Laya.load({
      modelDir: process.env.LAYA_MODEL_DIR || undefined,
      revision: process.env.LAYA_REVISION || "main",
      cacheDir: process.env.LAYA_CACHE || undefined,
      sessionOptions: threads > 0 ? { intraOpNumThreads: threads } : undefined,
      onProgress: ({ file, received, total }) => {
        const pct = total ? Math.floor((received / total) * 100) : -1;
        if (pct !== lastPct) { lastPct = pct; out({ event: "progress", file, received, total }); }
      },
    }).then((l) => { laya = l; return l; }, (e) => { loading = null; throw e; });
  }
  return loading;
}

const rl = createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  const { id, op } = msg;
  try {
    if (op === "load") {
      const l = await load();
      out({ id, ok: true, model_dir: l.modelDir, config: l.config });
    } else if (op === "decide") {
      const l = await load();
      const t0 = Date.now();
      const result = await l.systemOne(msg.state, msg.questions);
      out({ id, ok: true, result, ms: Date.now() - t0 });
    } else {
      out({ id, ok: false, error: `unknown op ${op}` });
    }
  } catch (e) {
    out({ id, ok: false, error: String((e && e.message) || e) });
  }
});
rl.on("close", () => process.exit(0));
