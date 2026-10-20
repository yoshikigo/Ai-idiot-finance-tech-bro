/* FT Terminal — vanilla JS front end. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k.nodeType ? k : document.createTextNode(String(k)));
  return el;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const o = { ...opts, headers: { "x-ft": "1", ...(opts.headers || {}) } };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["content-type"] = "application/json"; delete o.json; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || data || r.statusText);
  return data;
}

/* ------------------------------------------------------------ formatting */
const nf = (x, d = 2) => (x == null || isNaN(x) ? "—" : Number(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const pxd = (x) => (x == null ? 2 : Math.abs(x) >= 1000 ? 2 : Math.abs(x) >= 1 ? 2 : 4);
const big = (x) => {
  if (x == null || isNaN(x)) return "—";
  const a = Math.abs(x);
  for (const [d, s] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) if (a >= d) return (x / d).toFixed(2) + s;
  return nf(x);
};
const pct = (x, d = 2) => (x == null || isNaN(x) ? "—" : (x > 0 ? "+" : "") + Number(x).toFixed(d) + "%");
const ratio = (x) => (x == null ? "—" : (x * 100).toFixed(2) + "%");
const cls = (x) => (x > 0 ? "up" : x < 0 ? "down" : "flat");
const KIND = {};   // symbol -> "ETF" | "股票" (from quotes / resolve)
const NAMES = {};  // symbol -> Chinese / display name
const isTW = (s) => /\.TWO?$/.test(s || "");
const isFund = (s) => (s || "").startsWith("FUND:");
const isETF = (s) => isTW(s) && (KIND[s] === "ETF" || /^00\d{2,4}[A-Z]?\./.test(s));
const sector = (s) => (!s ? "" : isFund(s) ? "基金" : s.startsWith("^") ? "指數" : s.endsWith("=X") ? "外匯" : s.endsWith("=F") ? "商品" : /-USD$/.test(s) ? "加密" : isETF(s) ? "ETF" : "股票");
const showName = (s) => NAMES[s] ? `${s} ${NAMES[s]}` : s;
const timeAgo = (iso) => {
  if (!iso) return "";
  const d = new Date(iso); const m = (Date.now() - d) / 60000;
  if (m < 60) return Math.max(1, Math.round(m)) + "分前";
  if (m < 1440) return Math.round(m / 60) + "小時前";
  return d.toLocaleDateString("zh-TW", { month: "2-digit", day: "2-digit" });
};
const md = (s) => DOMPurify.sanitize(marked.parse(String(s || ""), { breaks: false, gfm: true }));

/* ------------------------------------------------------------ symbols / commands */
const EXCH = { TT: ".TW", TW: ".TW", TWO: ".TWO", US: "", JP: ".T", JT: ".T", HK: ".HK", LN: ".L", GR: ".DE", GY: ".DE", FP: ".PA", KS: ".KS", CN: ".TO", AU: ".AX" };
const PANE_FNS = ["GP", "GIP", "DES", "N", "FA", "ETF", "LAYA", "KIT"];
const STOCK_FNS = ["GP", "GIP", "DES", "N", "FA", "LAYA", "KIT"];
const ETF_FNS = ["GP", "GIP", "ETF", "N", "LAYA", "KIT"];
const FN_LABEL = { GP: "走勢", GIP: "盤中", DES: "概況", N: "新聞", FA: "財報", ETF: "ETF 資料", LAYA: "⚖ 判斷", KIT: "🧰 工具箱" };
const KIT_TABS = [["tech", "技術面"], ["options", "選擇權"], ["research", "評等籌碼"], ["earnings", "財報季"], ["value", "價值檢查"], ["intel", "市場情報"]];
const KIT_WORD = { "技術": "tech", "技術面": "tech", "技術分析": "tech", "選擇權": "options", "期權": "options", "評等": "research", "籌碼": "research", "目標價": "research", "法人": "research", "財報季": "earnings", "EPS": "earnings", "法說": "earnings", "價值": "value", "價值投資": "value", "情報": "intel", "類股輪動": "intel", "市場情報": "intel", "工具箱": "tech" };
const MON_FNS = ["WEI", "TW", "TWETF", "FX", "CMDTY", "CRYPTO", "RATES", "MAG7"];
const MON_TITLE = { TWFX: "台幣匯率", MACRO: "美國總經（FRED）", LEDGER: "分析紀錄與檢討", EVENTS: "重大事件", FOCUS: "今日焦點", SC: "供應鏈地圖", HOLD: "我的持股", WEI: "全球指數", TW: "台股焦點", TWETF: "熱門 ETF", FX: "外匯", CMDTY: "商品", CRYPTO: "加密貨幣", RATES: "利率／債券", MAG7: "美股科技", W: "自選清單", HELP: "說明", ETFM: "台股 ETF 總覽", FUNDS: "基金搜尋", WORLD: "世界事件（God's Eye View）", LAB: "策略研究", DATA: "資料庫目錄" };
const AI_CMDS = { TA: "tradingagents", HF: "hedgefund", QLIB: "qlib", QL: "qlib", RL: "finrl", FINRL: "finrl", FG: "fingpt", FINGPT: "fingpt", TFM: "timesfm", TIMESFM: "timesfm", LY: "laya", ASK: "ask", SET: "settings", DCA: "dca" };
const ZH_CMD = { "總經": "MACRO", "經濟": "MACRO", "美國總經": "MACRO", "FRED": "MACRO", "紀錄": "LEDGER", "分析紀錄": "LEDGER", "歷史紀錄": "LEDGER", "檢討": "LEDGER", "準確度": "LEDGER", "大事": "EVENTS", "重大事件": "EVENTS", "大事記": "EVENTS", "焦點": "FOCUS", "今日焦點": "FOCUS", "每日焦點": "FOCUS", "盤後": "FOCUS", "供應鏈": "SC", "產業鏈": "SC", "產業地圖": "SC", "供應鏈地圖": "SC",
  "持股": "HOLD", "我的持股": "HOLD", "庫存": "HOLD", "登入": "HOLD", "走勢": "GP", "K線": "GP", "線圖": "GP", "盤中": "GIP", "概況": "DES", "資料": "DES", "新聞": "N", "財報": "FA",
  "自選": "W", "台股": "TW", "全球": "WEI", "世界": "WORLD", "事件": "WORLD", "上帝視角": "WORLD", "地緣": "WORLD", "指數": "WEI", "美股": "MAG7", "外匯": "FX", "匯率": "TWFX", "台幣": "TWFX", "台幣匯率": "TWFX", "換匯": "TWFX", "商品": "CMDTY", "原物料": "CMDTY",
  "利率": "RATES", "加密": "CRYPTO", "說明": "HELP", "幫助": "HELP", "研究": "LAB", "策略": "LAB", "回測": "LAB", "選股": "LAB", "策略研究": "LAB", "資料庫": "DATA", "目錄": "DATA", "基金": "FUND", "熱門ETF": "TWETF", "ETF排行": "ETF",
  "分析": "TA", "多代理": "TA", "大師": "HF", "量化": "QLIB", "強化學習": "RL", "預測": "FG", "情緒": "FG", "趨勢": "TFM", "趨勢預測": "TFM", "判斷": "LAYA", "買賣": "LAYA", "多空": "LAYA", "LAYA": "LAYA", "問": "ASK", "設定": "SET", "定期定額": "DCA", "定額": "DCA" };
const alias = (w) => ZH_CMD[w] || ZH_CMD[(w || "").toUpperCase()] || (w || "").toUpperCase();

async function resolveSec(text) {
  const t = text.trim();
  if (!t) return null;
  if (isFund(t.toUpperCase())) return t.replace(/^fund:/i, "FUND:");
  try {
    const r = await api(`/api/resolve?q=${encodeURIComponent(t)}`);
    if (r.symbol) { if (r.kind) KIND[r.symbol] = r.kind; if (r.name) NAMES[r.symbol] = r.name; }
    return r.symbol;
  } catch (e) { return normSym(t.split(/\s+/)); }
}

function normSym(tokens) {
  let t = tokens.map((x) => x.toUpperCase());
  t = t.filter((x) => !["EQUITY", "<EQUITY>", "INDEX", "<INDEX>", "CURNCY", "CMDTY", "<GO>"].includes(x));
  if (!t.length) return "";
  let s = t[0];
  if (t[1] && EXCH[t[1]] !== undefined) s += EXCH[t[1]];
  if (/^\d{4,6}$/.test(s)) s += ".TW";
  return s;
}

/* ------------------------------------------------------------ state */
const S = {
  settings: null,
  panes: [],
  focus: 0,
  engines: [],
  sideTab: "tradingagents",
  jobs: {},       // engine -> [brief]
  selJob: {},     // engine -> id
  es: null,       // EventSource for selected job
  ask: [],
  charts: new Map(),
  lastPx: {},
};

/* ------------------------------------------------------------ boot */
async function boot() {
  try {  // public URL (Cloudflare): nothing loads until you log in
    const me = await api("/api/auth/me");
    S.public = !!me.public;
    if (me.public && !me.user) { gateLogin(); return; }
  } catch (e) {}
  try { S.settings = await api("/api/settings"); } catch (e) { S.settings = { watchlist: [], launchpad: [] }; }
  let lp = S.settings.launchpad || [];
  try { const saved = JSON.parse(localStorage.getItem("ft.panes") || "null"); if (saved && saved.length === 4) lp = saved; } catch (e) {}
  S.panes = [0, 1, 2, 3].map((i) => ({ sec: (lp[i] || {}).sec || "", fn: (lp[i] || {}).fn || "W", q: (lp[i] || {}).q || "", range: "1y",
    net: (lp[i] || {}).net || "", node: (lp[i] || {}).node || "", hl: (lp[i] || {}).hl || "", cmp: !!(lp[i] || {}).cmp }));
  applyColors();
  await Promise.all([loadUser(), loadSC()]);
  try { S.sideTab = localStorage.getItem("ft.sideTab") || S.sideTab; } catch (e) {}
  try { const w = localStorage.getItem("ft.sideW"); if (w) document.documentElement.style.setProperty("--side-w", w); } catch (e) {}
  try { if (localStorage.getItem("ft.sideClosed") === "1") $("#main").classList.add("side-closed"); } catch (e) {}
  if (window.matchMedia("(max-width: 820px)").matches) $("#main").classList.add("side-closed");
  buildFkeys();
  buildPanes();
  setupCmd();
  setupResizer();
  clocks(); setInterval(clocks, 1000);
  tape(); setInterval(tape, 30000);
  setInterval(refreshLive, 15000);
  await loadEngines();
  renderSide();
  setInterval(pollJobs, 5000);
  setFocus(0);
  document.addEventListener("keydown", hotkeys);
  const m = /#job=([a-z]+):([\w-]+)/.exec(location.hash);
  if (m) { openSide(m[1]); setTimeout(() => showJob(m[1], m[2]), 400); }
}

function savePanes() {
  try { localStorage.setItem("ft.panes", JSON.stringify(S.panes.map((p) => ({ sec: p.sec, fn: p.fn, q: p.q, net: p.net, node: p.node, hl: p.hl, cmp: p.cmp, fxSel: p.fxSel })))); } catch (e) {}
}

/* ------------------------------------------------------------ header */
function clocks() {
  const z = [["台北", "Asia/Taipei"], ["東京", "Asia/Tokyo"], ["倫敦", "Europe/London"], ["紐約", "America/New_York"]];
  $("#clocks").innerHTML = z.map(([n, tz]) => `${n} <b>${new Date().toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" })}</b>`).join("");
}

function buildFkeys() {
  const keys = [
    ["HELP", "說明"], ["FOCUS", "焦點"], ["EVENTS", "大事"], ["LEDGER", "紀錄"], ["MACRO", "總經"], ["SC", "供應鏈"], ["HOLD", "持股"], ["W", "自選"], ["TW", "台股"], ["ETF", "ETF"], ["FUND", "基金"], ["WORLD", "世界"], ["WEI", "全球"], ["MAG7", "美股"], ["TWFX", "台幣"], ["FX", "外匯"],
    ["CMDTY", "商品"], ["RATES", "利率"], ["CRYPTO", "加密"], "|", ["GP", "走勢"], ["GIP", "盤中"], ["DES", "概況"], ["N", "新聞"], ["FA", "財報"], "|",
    ["TA", "多代理人", 1], ["HF", "投資大師", 1], ["QLIB", "量化選股", 1], ["RL", "強化學習", 1], ["FG", "FinGPT", 1], ["TFM", "趨勢預測", 1], ["LAYA", "Laya 判斷", 1], ["LAB", "策略研究", 1], ["DATA", "資料庫", 1], ["ASK", "問 AI", 1], ["SET", "設定", 1],
  ];
  const nav = $("#fkeys");
  for (const k of keys) {
    if (k === "|") { nav.append(h("span", { class: "sep" })); continue; }
    nav.append(h("button", { class: k[2] ? "ai" : "", title: k[0], onclick: () => run(k[0]) }, h("b", {}, k[1]), h("small", {}, k[0])));
  }
}

/* ------------------------------------------------------------ command line */
function setupCmd() {
  const inp = $("#cmd"); const sug = $("#suggest");
  let timer, items = [], active = -1;
  $("#cmdform").addEventListener("submit", (e) => {
    e.preventDefault();
    if (active >= 0 && items[active]) { pick(items[active].symbol); return; }
    const v = inp.value.trim(); if (!v) return;
    run(v); inp.value = ""; hideSug();
  });
  const hideSug = () => { sug.classList.add("hidden"); items = []; active = -1; };
  const pick = (sym) => {
    const it = items.find((x) => x.symbol === sym) || {};
    if (it.type === "ETF") KIND[sym] = "ETF";
    if (it.name) NAMES[sym] = it.name;
    const fn = S.panes[S.focus].fn;
    if (isFund(sym)) setPane(S.focus, { sec: sym, fn: "FUNDD" });
    else run(`${sym} ${STOCK_FNS.includes(fn) ? fn : "GP"}`);
    inp.value = ""; hideSug();
  };
  inp.addEventListener("input", () => {
    clearTimeout(timer);
    const v = inp.value.trim();
    const first = v.split(/\s+/)[0].toUpperCase();
    $("#sectorKey").textContent = sector(normSym(v.split(/\s+/))) || "股票";
    const cjk = /[\u4e00-\u9fff]/.test(v);
    if ((!cjk && v.length < 2) || v.includes(" ") || AI_CMDS[alias(first)] || MON_FNS.includes(alias(first)) || ZH_CMD[first] || /^\d{1,3}$/.test(v)) { hideSug(); return; }
    timer = setTimeout(async () => {
      try {
        items = (await api(`/api/search?q=${encodeURIComponent(v)}`)).filter((x) => x.symbol).slice(0, 8);
        active = -1;
        sug.innerHTML = "";
        items.forEach((it, i) => sug.append(h("div", { onmousedown: (e) => { e.preventDefault(); pick(it.symbol); } },
          h("span", { class: "s-sym" }, it.symbol), h("span", {}, it.name || ""), h("span", { class: "s-ex" }, `${it.type || ""} ${it.exchange || ""}`))));
        sug.classList.toggle("hidden", !items.length);
      } catch (e) { hideSug(); }
    }, 250);
  });
  inp.addEventListener("keydown", (e) => {
    if (!items.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      $$("div", sug).forEach((d, i) => d.classList.toggle("active", i === active));
    } else if (e.key === "Escape") hideSug();
  });
  inp.addEventListener("blur", () => setTimeout(hideSug, 150));
}

function hotkeys(e) {
  if (e.ctrlKey && /^[1-4]$/.test(e.key)) { e.preventDefault(); setFocus(+e.key - 1); return; }
  if (e.ctrlKey && (e.key === "b" || e.key === "B")) { e.preventDefault(); toggleSide(); return; }
  const tag = (document.activeElement || {}).tagName;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) { if (e.key === "Escape") document.activeElement.blur(); return; }
  if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { $("#cmd").focus(); }
}

async function run(raw) {
  let t = raw.trim().split(/\s+/).filter(Boolean);
  if (!t.length) return;
  let target = S.focus;
  if (/^[1-4]$/.test(t[0]) && t.length > 1) { target = +t[0] - 1; t = t.slice(1); }
  // 工具箱：「技術 2330」「NVDA 選擇權」「情報」
  const kw = KIT_WORD[t[0]] || KIT_WORD[t[0].toUpperCase()] || (t.length > 1 && (KIT_WORD[t[t.length - 1]] || KIT_WORD[t[t.length - 1].toUpperCase()]));
  if (kw) {
    if (kw === "intel") return setPane(target, { fn: "KIT", kit: "intel", sec: S.panes[target].sec || "2330.TW" });
    const words = KIT_WORD[t[0]] || KIT_WORD[t[0].toUpperCase()] ? t.slice(1) : t.slice(0, -1);
    const sec = words.length ? await resolveSec(words.join(" ")) : (S.panes[target].sec || "2330.TW");
    if (!sec) { flash(`找不到「${words.join(" ")}」`); return; }
    return setPane(target, { fn: "KIT", kit: kw, sec });
  }
  const head = alias(t[0]);
  const tail = t.slice(1);

  if (head === "HELP" || head === "?") return setPane(target, { fn: "HELP" });
  if (head === "W" && !tail.length) return setPane(target, { fn: "W" });
  if (MON_FNS.includes(head) && !tail.length) return setPane(target, { fn: head });
  if (head === "ETF" && !tail.length) return setPane(target, { fn: "ETFM" });
  if (head === "WORLD") return setPane(target, { fn: "WORLD" });
  if (head === "FOCUS") return setPane(target, { fn: "FOCUS" });
  if (head === "EVENTS" || head === "EV") return setPane(target, { fn: "EVENTS" });
  if (head === "LEDGER" || head === "LOG") return setPane(target, { fn: "LEDGER", lgQ: tail.join(" ") });
  if (head === "TWFX") return setPane(target, { fn: "TWFX", fxSel: tail.join(" ") || S.panes[target].fxSel || "" });
  if (head === "MACRO") return setPane(target, { fn: "MACRO", fredId: (tail[0] || "").toUpperCase() || S.panes[target].fredId || "" });
  if (head === "HOLD") return setPane(target, { fn: "HOLD" });
  if (head === "SC") {
    if (!tail.length) return setPane(target, { fn: "SC" });
    const q = tail.join(" ");
    const hit = await api(`/api/sc/find?q=${encodeURIComponent(q)}`).catch(() => ({}));
    if (!hit.node) { flash(`供應鏈地圖裡找不到「${q}」`); return setPane(target, { fn: "SC" }); }
    const cur = S.panes[target];
    const net = cur.fn === "SC" && cur.net && (S.scNets || []).some((n) => n.id === cur.net) && netHas(cur.net, hit.node) ? cur.net : "ai-datacenter";
    return setPane(target, { fn: "SC", net, node: hit.node, hl: hit.symbol || "" });
  }
  if (head === "DATA") return setPane(target, { fn: "DATA" });
  if (head === "LAB") { if (LAB[target]) LAB[target].aiRan = false; return setPane(target, { fn: "LAB", q: tail.join(" ") }); }
  if (head === "FUND") {  // 「基金」alone reopens the last search instead of a blank box
    let q = tail.join(" ");
    if (!q) { try { q = localStorage.getItem("ft.fundQ") || ""; } catch (e) {} }
    return setPane(target, { fn: "FUNDS", q });
  }
  if (AI_CMDS[head] !== undefined && !(PANE_FNS.includes(alias(tail[tail.length - 1] || "")))) {
    const tab = AI_CMDS[head];
    openSide(tab);
    if (tab === "ask") { if (tail.length) askSend(tail.join(" ")); else setTimeout(() => $("#askIn") && $("#askIn").focus(), 50); return; }
    if (tab === "dca") { if (tail.length) dcaPrefill({ symbols: tail.join(" ").replace(/[,，、\s]+/g, ", ").replace(/^, |, $/g, "") }, true); return; }
    if (tail.length) prefillEngine(tab, tail.join(" "));
    return;
  }
  if (PANE_FNS.includes(head) && tail.length) {   // 判斷 2330 / 走勢 台積電
    const sec = await resolveSec(tail.join(" "));
    if (!sec || isFund(sec)) { flash(`找不到「${tail.join(" ")}」`); return; }
    return setPane(target, { sec, fn: fixFn(sec, head) });
  }
  if (PANE_FNS.includes(head) && !tail.length) {
    const sec = S.panes[target].sec || (S.settings.watchlist || [])[0] || "2330.TW";
    return setPane(target, { sec, fn: fixFn(sec, head) });
  }
  // <security> [exchange] [function]   — security can be a Chinese name, a code, a Yahoo symbol or FUND:<id>
  let fn = "GP";
  const last = alias(t[t.length - 1]);
  if (PANE_FNS.includes(last) && t.length > 1) { fn = last; t = t.slice(0, -1); }
  if (isFund(t[0].toUpperCase())) return setPane(target, { sec: t[0].replace(/^fund:/i, "FUND:"), fn: "FUNDD" });
  const sec = await resolveSec(t.join(" "));
  if (!sec) { flash(`找不到「${t.join(" ")}」`); return; }
  if (isFund(sec)) return setPane(target, { sec, fn: "FUNDD" });
  setPane(target, { sec, fn: fixFn(sec, fn) });
}

function fixFn(sec, fn) {
  if (isETF(sec) && (fn === "DES" || fn === "FA")) return "ETF";
  if (!isETF(sec) && fn === "ETF") return "DES";
  return fn;
}

function flash(msg) {
  const st = $("#status"); const old = st.innerHTML;
  st.innerHTML = `<span style="color:var(--amber)">${esc(msg)}</span>`;
  setTimeout(() => { st.innerHTML = old; }, 3500);
}

/* ------------------------------------------------------------ panes */
function buildPanes() {
  const root = $("#panes");
  root.innerHTML = "";
  S.panes.forEach((p, i) => {
    const el = h("div", { class: "pane", "data-i": i, onmousedown: () => setFocus(i) },
      h("div", { class: "pane-head" },
        h("span", { class: "pane-no" }, i + 1),
        h("span", { class: "pane-sec" }),
        h("span", { class: "pane-name" }),
        h("span", { class: "pane-fns" })),
      h("div", { class: "pane-body" }));
    root.append(el);
    renderPane(i);
  });
}

function setFocus(i) {
  S.focus = i;
  $$(".pane").forEach((el, j) => el.classList.toggle("focus", j === i));
  $("#paneTarget").textContent = i + 1;
  $("#sectorKey").textContent = sector(S.panes[i].sec) || "股票";
  const ctx = $("#askCtx"); if (ctx) ctx.textContent = S.panes[i].sec ? showName(S.panes[i].sec) : "（無）";
}

function setPane(i, patch) {
  Object.assign(S.panes[i], patch);
  savePanes();
  setFocus(i);
  renderPane(i);
}

function paneEl(i) { return $(`.pane[data-i="${i}"]`); }

function renderPane(i) {
  const p = S.panes[i]; const el = paneEl(i); if (!el) return;
  if (S.paneTimers && S.paneTimers[i]) { clearInterval(S.paneTimers[i]); S.paneTimers[i] = null; }
  const body = $(".pane-body", el);
  const old = S.charts.get(i); if (old) { old.ro && old.ro.disconnect(); old.chart.remove(); S.charts.delete(i); }
  body.className = "pane-body"; body.innerHTML = "";
  const usesSec = PANE_FNS.includes(p.fn);
  $(".pane-sec", el).textContent = usesSec ? showName(p.sec) : p.fn === "FUNDD" ? "基金" : (MON_TITLE[p.fn] || p.fn);
  $(".pane-name", el).textContent = usesSec ? fnLabel(p.fn) : "";
  const fns = $(".pane-fns", el); fns.innerHTML = "";
  if (usesSec) {
    for (const f of (isETF(p.sec) ? ETF_FNS : STOCK_FNS))
      fns.append(h("button", { class: f === p.fn ? "on" : "", title: f, onclick: (e) => { e.stopPropagation(); setPane(i, { fn: f }); } }, FN_LABEL[f]));
    const chains = (S.scIndex || {})[p.sec];
    if (chains && chains.length)
      fns.append(h("button", { title: "所屬供應鏈：" + chains.map((c) => c.name).join("、"), onclick: (e) => { e.stopPropagation(); setPane(i, { fn: "SC", net: "ai-datacenter", node: chains[0].id, hl: p.sec }); } }, "⛓ 供應鏈"));
    fns.append(h("button", { title: "TradingAgents 多代理人分析", onclick: (e) => { e.stopPropagation(); openSide("tradingagents"); prefillEngine("tradingagents", p.sec); } }, "AI▸"));
  }
  fns.append(h("button", { title: "最大化", onclick: (e) => { e.stopPropagation(); toggleMax(i); } }, "⤢"));
  const R = { KIT: renderKit, TWFX: renderTWFX, MACRO: renderMacro, LEDGER: renderLedger, EVENTS: renderEvents, FOCUS: renderFocus, SC: renderSC, HOLD: renderHold, GP: renderGP, GIP: renderGP, DES: renderDES, N: renderN, FA: renderFA, ETF: renderETF, W: renderW, HELP: renderHelp,
              ETFM: renderETFMon, FUNDS: renderFundSearch, FUNDD: renderFund, WORLD: renderWorld, LAYA: renderLaya, LAB: renderLab, DATA: renderData };
  if (R[p.fn]) R[p.fn](i, body, p);
  else if (MON_FNS.includes(p.fn)) renderMon(i, body, p);
  else body.innerHTML = `<div class="empty">未知功能 ${esc(p.fn)}</div>`;
  if (usesSec) loadName(i, p.sec);
}

function fnLabel(f) { return { GP: "價格走勢", GIP: "盤中走勢", DES: "公司概況", N: "新聞", FA: "財務報表", ETF: "ETF 資料", LAYA: "Laya 交易判斷", KIT: "分析工具箱" }[f] || f; }

async function loadName(i, sec) {
  try {
    const q = (await api(`/api/quotes?s=${encodeURIComponent(sec)}`))[0];
    const el = paneEl(i); if (!el || S.panes[i].sec !== sec || !PANE_FNS.includes(S.panes[i].fn)) return;
    if (q && q.name) { NAMES[sec] = q.name; $(".pane-sec", el).textContent = showName(sec); }
    if (q && q.kind) KIND[sec] = q.kind;
    if (q && q.last != null) $(".pane-name", el).innerHTML = `${fnLabel(S.panes[i].fn)} · <span class="${cls(q.pct)}">${nf(q.last, pxd(q.last))} ${pct(q.pct)}</span>`;
  } catch (e) {}
}

function toggleMax(i) {
  const root = $("#panes"); const el = paneEl(i);
  const on = !el.classList.contains("maximized");
  $$(".pane").forEach((p) => p.classList.remove("maximized"));
  root.classList.toggle("max-1", on);
  if (on) el.classList.add("maximized");
  setTimeout(() => S.charts.forEach((c) => c.fit && c.fit()), 30);
}

/* ---------- LAYA 判斷 */
const ACT_ORDER = ["buy", "hold", "wait", "sell", "short"];
const ACT_COLOR = { buy: "var(--up)", hold: "var(--cyan)", wait: "var(--muted)", sell: "var(--amber)", short: "var(--down)" };
const pp = (x, d = 0) => (x == null || isNaN(x) ? "—" : (x * 100).toFixed(d) + "%");

function decisionCard(d, compact) {
  const a = d.action;
  const top = a.probabilities[a.choice];
  const card = h("div", { class: "dec" + (compact ? " compact" : "") });
  card.append(h("div", { class: "dec-head" },
    h("span", { class: "dec-big", style: `color:${ACT_COLOR[a.choice]}` }, `${a.label} ${pp(top)}`),
    h("span", { class: "dec-sub" }, `${d.symbol} ${d.name || ""} · 基準 ${d.as_of} · 未來 ${d.horizon} 個交易日`)));
  const net = d.net || 0;
  card.append(h("div", { class: "dec-net", title: "買進 − 賣出 − 放空" },
    h("span", {}, "多空淨值"),
    h("span", { class: "dec-netbar" }, h("i", { style: `left:${net < 0 ? 50 + net * 50 : 50}%;width:${Math.abs(net) * 50}%;background:${net >= 0 ? "var(--up)" : "var(--down)"}` })),
    h("b", { style: `color:${net >= 0 ? "var(--up)" : "var(--down)"}` }, (net >= 0 ? "+" : "") + pp(net))));
  const rows = h("div", { class: "dec-rows" });
  for (const k of ACT_ORDER) {
    const v = a.probabilities[k] || 0;
    rows.append(h("div", { class: "dec-row" + (k === a.choice ? " top" : "") },
      h("span", { class: "dec-lab" }, a.labels[k]),
      h("span", { class: "dec-bar" }, h("i", { style: `width:${(v * 100).toFixed(1)}%;background:${ACT_COLOR[k]}` })),
      h("span", { class: "dec-pct" }, pp(v, 1))));
  }
  card.append(rows);
  card.append(h("div", { class: "dec-gauges" },
    h("div", {}, h("small", {}, `${d.horizon} 日後收高`), h("b", { style: "color:var(--up)" }, pp(d.p_up))),
    h("div", {}, h("small", {}, `跌逾 ${d.drop_threshold}% 風險`), h("b", { style: "color:var(--down)" }, pp(d.p_drop))),
    h("div", {}, h("small", {}, "模型信心"), h("b", {}, pp(a.answer_confidence)))));
  const o = d.outlook;
  const hist = h("div", { class: "dec-hist" });
  const mx = Math.max(...o.probabilities, 0.01);
  o.probabilities.forEach((v, j) => hist.append(h("div", { class: "dec-col", title: `${o.labels[j]} ${o.bins[j]}：${pp(v, 1)}` },
    h("span", { class: "dec-colv" }, pp(v)),
    h("i", { style: `height:${(v / mx) * 100}%;background:${j < 2 ? "var(--down)" : j > 2 ? "var(--up)" : "var(--muted)"}` }),
    h("small", {}, o.labels[j]), h("small", { class: "muted" }, o.bins[j]))));
  card.append(h("div", { class: "dec-sec" }, `${d.horizon} 個交易日後的位置`), hist);
  if (!compact) return card;
  const f = d.facts || {};
  const used = [].concat(f.timesfm ? [f.timesfm] : [], f.agents || [], (f.world || []).map((x) => "🌐 " + x));
  if (used.length) card.append(h("details", { class: "dec-in" }, h("summary", {}, `帶入的訊號（${used.length}）`), h("ul", {}, ...used.map((x) => h("li", {}, x)))));
  card.append(h("div", { class: "dec-warn" }, "Laya 是通用決策模型，未用金融資料訓練；百分比是它對這份狀態的信心，不是統計漲跌機率。用「📝 記錄」累積後，Obsidian「AI 預測回顧」會算它的實際命中率。" + (d.model.includes("假模型") ? "（目前是測試用假模型）" : "")));
  return card;
}

async function renderLaya(i, body, p) {
  const hz = p.lh || 20;
  const bar = h("div", { class: "ranges" });
  [[5, "一週"], [20, "一個月"], [60, "一季"]].forEach(([v, l]) => bar.append(h("button", { class: v === hz ? "on" : "", onclick: () => { S.panes[i].lh = v; renderPane(i); } }, l)));
  const out = h("div", { class: "dec-wrap" }, h("div", { class: "loading" }, "Laya 推論中（第一次要載入模型）"));
  body.append(bar, out);
  let d;
  try { d = await api(`/api/decide?s=${encodeURIComponent(p.sec)}&h=${hz}`); }
  catch (e) {
    out.innerHTML = `<div class="errbox">${esc(e.message)}</div>`;
    if (/下載中|載入中/.test(e.message)) setTimeout(() => { if (S.panes[i].fn === "LAYA" && S.panes[i].sec === p.sec) renderPane(i); }, 15000);
    return;
  }
  if (S.panes[i].fn !== "LAYA" || S.panes[i].sec !== p.sec) return;
  out.innerHTML = "";
  out.append(decisionCard(d, true));
  bar.append(h("span", { class: "grow" }),
    h("button", { title: "建立一筆 Laya 工作：留下紀錄、寫 Obsidian 筆記、之後計算命中率", onclick: async (e) => {
      e.target.disabled = true;
      try { await api("/api/engines/laya/jobs", { method: "POST", json: { params: { ticker: p.sec, horizon: String(hz), date: "today", use_agents: true, use_timesfm: true, use_world: true, use_news: true } } }); e.target.textContent = "📝 已記錄 ✓"; }
      catch (x) { e.target.textContent = "記錄失敗"; }
    } }, "📝 記錄"),
    h("button", { title: "側欄看完整報告與送進模型的狀態", onclick: () => { openSide("laya"); prefillEngine("laya", p.sec, { horizon: String(hz) }); } }, "詳細 ▸"));
}

/* ---------- 策略研究 LAB ＋ 資料庫 DATA（FinLab 式研究流程） */
const LAB = {};          // pane index -> { spec, res, tab, ai, busy, err }
let CATALOG = null;
async function catalog(force) {
  if (!CATALOG || force || Date.now() - CATALOG._at > 60000) { CATALOG = await api("/api/research/catalog"); CATALOG._at = Date.now(); }
  return CATALOG;
}
const fmtp = (v, d = 1) => (v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d) + "%");
const fmtn = (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d));
function labState(i) {
  if (!LAB[i]) {
    let spec = null;
    try { spec = JSON.parse(localStorage.getItem("ft.labSpec") || "null"); } catch (e) {}
    LAB[i] = { spec, res: null, tab: "spec", ai: "", busy: "", err: "", expl: "" };
  }
  return LAB[i];
}
function saveSpec(st) { try { localStorage.setItem("ft.labSpec", JSON.stringify(st.spec)); } catch (e) {} }

async function renderLab(i, body, p) {
  const st = labState(i);
  body.classList.add("lab");
  let cat;
  try { cat = await catalog(); } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (!st.spec) st.spec = { ...JSON.parse(JSON.stringify(cat.defaults)), ...cat.presets[0] };
  if (p.q && !st.aiRan) { st.ai = p.q; st.aiRan = true; S.panes[i].q = ""; savePanes(); setTimeout(() => labAI(i), 0); }
  const tabs = h("div", { class: "ranges lab-tabs" });
  const T = [["spec", "① 策略"], ["report", "② 績效報告"], ["picks", "③ 選股名單"], ["trades", "④ 交易明細"]];
  T.forEach(([k, l]) => tabs.append(h("button", { class: st.tab === k ? "on" : "", disabled: k !== "spec" && !st.res ? true : null,
    onclick: () => { st.tab = k; renderPane(i); } }, l)));
  tabs.append(h("span", { class: "grow" }));
  if (st.busy) tabs.append(h("span", { class: "legend lab-busy" }, st.busy));
  tabs.append(h("button", { title: "在下一格開資料庫目錄", onclick: () => setPane((i + 1) % 4, { fn: "DATA" }) }, "📚 資料庫"));
  const box = h("div", { class: "lab-body" });
  body.append(tabs, box);
  if (st.err) box.append(h("div", { class: "errbox" }, st.err));
  if (st.tab === "spec") labSpec(i, box, st, cat);
  else if (st.tab === "report") labReport(i, box, st);
  else if (st.tab === "picks") labPicks(i, box, st);
  else labTrades(i, box, st);
}

function labSpec(i, box, st, cat) {
  const s = st.spec;
  // AI
  const ta = h("textarea", { class: "lab-ai", rows: 2, placeholder: "用中文描述你的選股想法，例如：找營收連續三個月成長、本益比低於 20、投信最近在買的股票，每月換股" });
  ta.value = st.ai || "";
  ta.addEventListener("input", () => { st.ai = ta.value; });
  box.append(h("div", { class: "lab-sec" }, "🤖 用中文描述，AI 幫你建立策略（本機 Ollama）"),
    h("div", { class: "lab-airow" }, ta, h("button", { class: "btn", disabled: st.busy ? true : null, onclick: () => labAI(i) }, "建立策略")));
  if (st.expl) box.append(h("div", { class: "lab-expl" }, "AI：" + st.expl));
  // presets
  const pre = h("div", { class: "lab-presets" });
  for (const ps of cat.presets) pre.append(h("button", { class: "lab-preset" + (s.name === ps.name ? " on" : ""), title: ps.description,
    onclick: () => { st.spec = { ...JSON.parse(JSON.stringify(cat.defaults)), ...JSON.parse(JSON.stringify(ps)) }; st.expl = ""; saveSpec(st); renderPane(i); } },
    h("b", {}, ps.name), h("small", {}, ps.description)));
  box.append(h("div", { class: "lab-sec" }, "📦 內建策略（點一下載入，可再修改）"), pre);
  // editor
  const ed = h("div", { class: "lab-editor" });
  const nameIn = h("input", { value: s.name || "", class: "lab-name" });
  nameIn.addEventListener("change", () => { s.name = nameIn.value; saveSpec(st); });
  ed.append(h("div", { class: "lab-sec" }, "✏️ 條件（全部成立才入選；欄位與函數見下方）"), nameIn);
  const conds = h("div", { class: "lab-conds" });
  const condRow = (val, idx) => {
    const inp = h("input", { value: val, class: "lab-expr", spellcheck: "false" });
    const msg = h("span", { class: "lab-chk" });
    const check = async () => {
      s.conditions[idx] = inp.value.trim(); saveSpec(st);
      if (!inp.value.trim()) { msg.textContent = ""; return; }
      try { const r = await api("/api/research/check", { method: "POST", json: { expr: inp.value } });
        msg.textContent = r.ok ? "✓" : "✗ " + r.error; msg.className = "lab-chk " + (r.ok ? "ok" : "bad"); } catch (e) {}
    };
    inp.addEventListener("change", check);
    inp.addEventListener("focus", () => { LAB._focus = inp; });
    return h("div", { class: "lab-cond" }, inp, msg,
      h("button", { title: "刪除", onclick: () => { s.conditions.splice(idx, 1); saveSpec(st); renderPane(i); } }, "✕"));
  };
  (s.conditions || []).forEach((c, k) => conds.append(condRow(c, k)));
  ed.append(conds, h("button", { class: "btn ghost", onclick: () => { s.conditions.push(""); renderPane(i); } }, "＋ 新增條件"));
  const rank = h("input", { value: s.rank || "", class: "lab-expr", spellcheck: "false", placeholder: "排名依據（數值），例：營收年增率" });
  rank.addEventListener("change", () => { s.rank = rank.value.trim(); saveSpec(st); });
  rank.addEventListener("focus", () => { LAB._focus = rank; });
  const sel = (key, opts) => { const el = h("select", {}, ...opts.map(([v, l]) => h("option", { value: v, selected: String(s[key]) === String(v) ? true : null }, l)));
    el.addEventListener("change", () => { s[key] = el.value === "true" ? true : el.value === "false" ? false : el.value; saveSpec(st); }); return el; };
  const num = (key, scale = 1, step = "1") => { const el = h("input", { type: "number", step, value: s[key] == null ? "" : +(s[key] * scale).toFixed(4) });
    el.addEventListener("change", () => { s[key] = el.value === "" ? null : +el.value / scale; saveSpec(st); }); return el; };
  const u = s.universe || (s.universe = {});
  const uSel = h("select", {}, ...[["all", "上市＋上櫃"], ["上市", "只上市"], ["上櫃", "只上櫃"]].map(([v, l]) => h("option", { value: v, selected: (u.market || "all") === v ? true : null }, l)));
  uSel.addEventListener("change", () => { u.market = uSel.value; saveSpec(st); });
  const uMin = h("input", { type: "number", value: u.min_value ?? 20 }); uMin.addEventListener("change", () => { u.min_value = +uMin.value; saveSpec(st); });
  const uFin = h("input", { type: "checkbox", checked: u.exclude_finance ? true : null }); uFin.addEventListener("change", () => { u.exclude_finance = uFin.checked; saveSpec(st); });
  const start = h("input", { type: "date", value: s.start || "2016-01-01" }); start.addEventListener("change", () => { s.start = start.value; saveSpec(st); });
  ed.append(h("div", { class: "lab-grid" },
    h("label", { class: "wide" }, "排名", rank),
    h("label", {}, "排序", sel("ascending", [[false, "由大到小"], [true, "由小到大"]])),
    h("label", {}, "持股數", num("top_n")),
    h("label", {}, "換股", sel("rebalance", Object.entries(CATALOG.rebalance)))),
    h("div", { class: "lab-grid" },
    h("label", {}, "停損 %", num("stop_loss", 100)),
    h("label", {}, "停利 %", num("take_profit", 100)),
    h("label", {}, "單檔上限 %", num("max_weight", 100)),
    h("label", {}, "回測起點", start)),
    h("div", { class: "lab-grid" },
    h("label", {}, "市場", uSel),
    h("label", {}, "20 日均成交 ≥（百萬）", uMin),
    h("label", { class: "chk" }, uFin, "排除金融股"),
    h("label", {}, "基準", sel("benchmark", [["0050", "0050 元大台灣50"], ["006208", "006208 富邦台50"], ["0056", "0056 高股息"]]))));
  ed.append(h("div", { class: "run-row" },
    h("button", { class: "btn", disabled: st.busy ? true : null, onclick: () => labRun(i, "backtest") }, "▶ 回測"),
    h("button", { class: "btn ghost", disabled: st.busy ? true : null, onclick: () => labRun(i, "screen") }, "只看今天的選股"),
    h("button", { class: "btn ghost", title: "建一筆工作：留紀錄並寫進 Obsidian finance/", onclick: () => labJob(i) }, "📝 存成工作")));
  box.append(ed);
  // reference
  const ref = h("details", { class: "lab-ref" }, h("summary", {}, `欄位（${CATALOG.fields}）與函數 — 點一下插入到游標所在的輸入框`));
  for (const d of CATALOG.datasets) {
    ref.append(h("div", { class: "lab-refds" }, h("b", {}, d.name), h("small", {}, ` ${d.freq} · ${d.first || "尚無資料"} ～ ${d.last || ""}`)));
    ref.append(h("div", { class: "chips" }, ...d.fields.map((f) => h("button", { class: "chip", title: `${f.desc}（${f.unit}，${f.freq}）`, onclick: () => labInsert(f.name) }, f.name))));
  }
  ref.append(h("div", { class: "lab-refds" }, h("b", {}, "函數")));
  ref.append(h("div", { class: "chips" }, ...CATALOG.functions.map((f) => h("button", { class: "chip fn", title: `${f.desc}　例：${f.example}`, onclick: () => labInsert(f.example) }, f.name))));
  box.append(ref);
}

function labInsert(text) {
  const el = LAB._focus;
  if (!el || !document.body.contains(el)) { flash("先點一下條件或排名輸入框"); return; }
  const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? el.value.length;
  el.value = el.value.slice(0, a) + text + el.value.slice(b);
  el.focus(); el.dispatchEvent(new Event("change"));
}

async function labAI(i) {
  const st = labState(i);
  if (!(st.ai || "").trim()) { st.err = "請先輸入選股想法"; renderPane(i); return; }
  st.busy = "AI 建立策略中…（本機模型約 30 秒～2 分鐘）"; st.err = ""; renderPane(i);
  try {
    const r = await api("/api/research/ai", { method: "POST", json: { prompt: st.ai } });
    st.spec = r.spec; st.expl = r.explanation || ""; saveSpec(st);
    st.busy = ""; renderPane(i);
    await labRun(i, "backtest");
  } catch (e) { st.busy = ""; st.err = "AI：" + e.message; renderPane(i); }
}

async function labRun(i, what) {
  const st = labState(i);
  st.spec.conditions = (st.spec.conditions || []).filter((c) => c && c.trim());
  st.busy = what === "screen" ? "選股中…" : "回測中…（第一次要載入資料，約 10–60 秒）"; st.err = ""; renderPane(i);
  try {
    const r = await api(`/api/research/${what}`, { method: "POST", json: { spec: st.spec } });
    if (what === "screen") { st.res = { ...(st.res || {}), picks: r.picks, spec: r.spec, screenOnly: !st.res }; st.tab = "picks"; }
    else { st.res = r; st.tab = "report"; }
  } catch (e) { st.err = e.message; }
  st.busy = ""; renderPane(i);
}

async function labJob(i) {
  const st = labState(i);
  try {
    await api("/api/engines/research/jobs", { method: "POST", json: { params: { mode: "json", spec: JSON.stringify(st.spec), start: st.spec.start } } });
    flash("已建立回測工作，完成後會寫進 Obsidian"); openSide("research");
  } catch (e) { st.err = e.message; renderPane(i); }
}

function metricCard(label, v, sub, good) {
  return h("div", { class: "mcard" }, h("small", {}, label), h("b", { class: good == null ? "" : good ? "up" : "down" }, v), sub ? h("span", {}, sub) : null);
}

function labReport(i, box, st) {
  const r = st.res; if (!r || !r.metrics) { box.append(h("div", { class: "empty" }, "還沒有回測結果")); return; }
  const m = r.metrics, s = r.spec;
  if (st.expl) box.append(h("div", { class: "lab-expl", style: "margin-bottom:6px" }, "AI 轉換說明：" + st.expl));
  box.append(h("div", { class: "lab-title" }, h("b", {}, s.name), h("span", {}, ` ${m.start} ～ ${m.end} · ${CATALOG.rebalance[s.rebalance]} · 前 ${s.top_n} 檔 · 基準 ${s.benchmark}${r.ms != null ? ` · ${(r.ms / 1000).toFixed(1)} 秒` : ""}`)));
  box.append(h("div", { class: "mcards" },
    metricCard("年化報酬", fmtp(m.cagr), `基準 ${fmtp(m.bench_cagr)}`, m.cagr > (m.bench_cagr ?? 0)),
    metricCard("最大回撤", fmtp(m.mdd), `基準 ${fmtp(m.bench_mdd)}`, m.mdd > (m.bench_mdd ?? -100)),
    metricCard("夏普比率", fmtn(m.sharpe), `基準 ${fmtn(m.bench_sharpe)}`, m.sharpe > (m.bench_sharpe ?? 0)),
    metricCard("總報酬", fmtp(m.total_return, 0), `基準 ${fmtp(m.bench_total, 0)}`, null),
    metricCard("勝率", m.win_rate == null ? "—" : m.win_rate.toFixed(0) + "%", `${m.trades} 筆 · 平均 ${fmtp(m.avg_trade)}`, null),
    metricCard("Alpha／Beta", fmtp(m.alpha), `Beta ${fmtn(m.beta)}`, null),
    metricCard("年化波動", fmtp(m.vol).replace("+", ""), `Sortino ${fmtn(m.sortino)}`, null),
    metricCard("年週轉", m.turnover == null ? "—" : (m.turnover * 100).toFixed(0) + "%", `平均持有 ${fmtn(m.avg_days, 0)} 天`, null)));
  const cbox = h("div", { class: "lab-chart" }), dbox = h("div", { class: "lab-dd" });
  box.append(h("div", { class: "lab-sec" }, "淨值曲線（含成本）", h("span", { class: "lab-leg" }, h("i", { style: "background:var(--amber)" }), "策略 ", h("i", { style: "background:#4ba3ff" }), s.benchmark)), cbox,
    h("div", { class: "lab-sec" }, `回撤（最深 ${fmtp(m.mdd)}：${m.mdd_peak} → ${m.mdd_trough}${m.mdd_recover ? `，${m.mdd_recover} 回到高點` : "，尚未回到高點"}）`), dbox);
  requestAnimationFrame(() => {
    const ch = LightweightCharts.createChart(cbox, { ...chartTheme(), autoSize: true, rightPriceScale: { mode: 1, borderColor: "#1f252c" } });
    const a = ch.addLineSeries({ color: "#ffa028", lineWidth: 2, priceLineVisible: false, title: "策略" });
    a.setData(r.curve.map((x) => ({ time: x.t, value: x.v })));
    const bpts = r.curve.filter((x) => x.b != null).map((x) => ({ time: x.t, value: x.b }));
    if (bpts.length) { const b = ch.addLineSeries({ color: "#4ba3ff", lineWidth: 1, priceLineVisible: false, title: s.benchmark }); b.setData(bpts); }
    ch.timeScale().fitContent();
    const dc = LightweightCharts.createChart(dbox, { ...chartTheme(), autoSize: true });
    const d = dc.addAreaSeries({ lineColor: "#ff4d4d", topColor: "#ff4d4d22", bottomColor: "#ff4d4d55", lineWidth: 1, priceLineVisible: false });
    d.setData(r.curve.map((x) => ({ time: x.t, value: x.dd })));
    dc.timeScale().fitContent();
    ch.timeScale().subscribeVisibleLogicalRangeChange((rg) => rg && dc.timeScale().setVisibleLogicalRange(rg));
    S.charts.set(i, { chart: { remove: () => { ch.remove(); dc.remove(); } } });
  });
  // yearly
  const yt = h("table", { class: "grid lab-year" });
  const ymax = Math.max(1, ...r.yearly.map((y) => Math.max(Math.abs(y.ret), Math.abs(y.bench || 0))));
  yt.innerHTML = `<thead><tr><th>年度</th><th>策略</th><th></th><th>基準</th><th>超額</th></tr></thead>`;
  const tb = h("tbody");
  for (const y of r.yearly) {
    const w = Math.abs(y.ret) / ymax * 50;
    tb.append(h("tr", {}, h("td", {}, y.year), h("td", { class: cls(y.ret) }, fmtp(y.ret)),
      h("td", { class: "ybar" }, h("i", { style: `left:${y.ret >= 0 ? 50 : 50 - w}%;width:${w}%;background:${y.ret >= 0 ? "var(--up)" : "var(--down)"}` })),
      h("td", { class: cls(y.bench) }, fmtp(y.bench)), h("td", { class: cls(y.ret - (y.bench ?? 0)) }, y.bench == null ? "—" : fmtp(y.ret - y.bench))));
  }
  yt.append(tb);
  // monthly heatmap
  const byY = {};
  for (const x of r.monthly) { const [yy, mm] = x.m.split("-"); (byY[yy] = byY[yy] || {})[+mm] = x.r; }
  const hm = h("table", { class: "grid lab-heat" });
  hm.innerHTML = `<thead><tr><th>年</th>${[...Array(12)].map((_, k) => `<th>${k + 1}</th>`).join("")}</tr></thead>`;
  const hb = h("tbody");
  for (const yy of Object.keys(byY).sort()) {
    hb.append(h("tr", {}, h("td", {}, yy), ...[...Array(12)].map((_, k) => {
      const v = byY[yy][k + 1];
      if (v == null) return h("td", {}, "");
      const a = Math.min(Math.abs(v) / 10, 1);
      return h("td", { style: `background:${v >= 0 ? `rgba(var(--up-rgb),${0.12 + a * 0.6})` : `rgba(var(--down-rgb),${0.12 + a * 0.6})`}`, title: `${yy}-${k + 1}：${fmtp(v)}` }, v.toFixed(1));
    })));
  }
  hm.append(hb);
  box.append(h("div", { class: "lab-two" }, h("div", {}, h("div", { class: "lab-sec" }, "每年報酬"), yt), h("div", {}, h("div", { class: "lab-sec" }, "每月報酬（%）"), hm)));
  const notes = [...(r.warnings || []), ...(r.notes || [])];
  box.append(h("div", { class: "lab-sec" }, "策略內容"), h("pre", { class: "lab-spec" },
    (s.conditions || []).map((c) => "條件  " + c).join("\n") + (s.rank ? `\n排名  ${s.rank}（${s.ascending ? "由小到大" : "由大到小"}）` : "")));
  box.append(h("ul", { class: "lab-notes" }, ...notes.map((n) => h("li", {}, n))));
}

function labPicks(i, box, st) {
  const pk = st.res && st.res.picks;
  if (!pk) { box.append(h("div", { class: "empty" }, "還沒有選股結果")); return; }
  box.append(h("div", { class: "lab-title" }, h("b", {}, `${pk.date} 選股名單`), h("span", {}, ` 符合全部條件 ${pk.n_match} 檔，取前 ${pk.rows.length} 檔。點一下在下一格開走勢圖。`)));
  const keys = pk.rows.length ? Object.keys(pk.rows[0].values || {}) : [];
  const t = h("table", { class: "grid" });
  t.innerHTML = `<thead><tr><th>代號</th><th>名稱</th><th>產業</th><th>收盤</th>${pk.rows.some((r) => r.score != null) ? "<th>排名分數</th>" : ""}${keys.map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead>`;
  const tb = h("tbody");
  const hasScore = pk.rows.some((r) => r.score != null);
  for (const r of pk.rows) {
    const sym = `${r.code}.${r.market === "上櫃" ? "TWO" : "TW"}`;
    tb.append(h("tr", { class: "clk", onclick: () => setPane((i + 1) % 4, { sec: sym, fn: "GP" }) },
      h("td", { class: "sym" }, r.code), h("td", {}, r.name), h("td", { class: "muted" }, r.industry || ""), h("td", {}, nf(r.close)),
      hasScore ? h("td", {}, fmtn(r.score, 3)) : null, ...keys.map((k) => h("td", {}, r.values[k] == null ? "—" : nf(r.values[k], 2)))));
  }
  t.append(tb);
  box.append(t);
}

function labTrades(i, box, st) {
  const tr = (st.res && st.res.trades) || [];
  box.append(h("div", { class: "lab-title" }, h("b", {}, "交易明細"), h("span", {}, ` 共 ${st.res.n_trades_total} 筆（顯示最近 ${tr.length} 筆）`)));
  const t = h("table", { class: "grid" });
  t.innerHTML = `<thead><tr><th>代號</th><th>名稱</th><th>買進</th><th>賣出</th><th>天數</th><th>報酬</th><th>原因</th></tr></thead>`;
  const tb = h("tbody");
  for (const x of [...tr].reverse()) tb.append(h("tr", {}, h("td", { class: "sym" }, x.code), h("td", {}, x.name), h("td", {}, x.entry), h("td", {}, x.exit),
    h("td", {}, x.days), h("td", { class: cls(x.ret) }, fmtp(x.ret)), h("td", { class: "muted" }, x.reason)));
  t.append(tb);
  box.append(t);
}

async function renderData(i, body, p) {
  body.classList.add("lab");
  body.innerHTML = `<div class="loading">讀取資料庫目錄</div>`;
  let cat;
  try { cat = await catalog(true); } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  body.innerHTML = "";
  const c = cat.collector || {};
  const box = h("div", { class: "lab-body" });
  body.append(box);
  box.append(h("div", { class: "data-hero" },
    h("div", {}, h("b", {}, cat.fields), h("small", {}, "個欄位")),
    h("div", {}, h("b", {}, big(cat.rows)), h("small", {}, "筆資料")),
    h("div", {}, h("b", {}, (cat.datasets.find((d) => d.id === "prices") || {}).first?.slice(0, 4) || "—"), h("small", {}, "起始年")),
    cat.fake ? h("div", {}, h("b", {}, "示範"), h("small", {}, "FT_FAKE_DATA=1：合成資料，不是真實行情"))
      : h("div", {}, h("b", {}, "官方"), h("small", {}, "證交所・櫃買・公開資訊觀測站"))));
  box.append(h("div", { class: "data-coll" },
    h("span", { class: "dot " + (c.running ? (c.paused ? "off" : "busy") : "off") }),
    `背景收集：${c.paused ? "已暫停" : c.task || "—"}${c.prices_task ? `　｜${c.prices_task}` : ""}　已完成 ${c.done || 0} 次　股價更新 ${(c.prices_at || "—").replace("T", " ").slice(0, 16)}`,
    c.last_error ? h("span", { class: "down" }, `　最近錯誤：${c.last_error.slice(0, 120)}`) : null,
    h("span", { class: "grow" }),
    h("button", { onclick: async () => { await api("/api/research/collector", { method: "POST", json: { action: c.paused ? "resume" : "pause" } }); renderPane(i); } }, c.paused ? "▶ 繼續" : "⏸ 暫停"),
    h("button", { title: "重新下載全部股價（還原股價有變動時）", onclick: async () => { await api("/api/research/collector", { method: "POST", json: { action: "prices_full" } }); flash("已開始重新下載股價"); } }, "重抓股價"),
    h("button", { onclick: () => renderPane(i) }, "↻")));
  box.append(h("div", { class: "data-cfg muted" }, `回補設定：月營收／財報從 ${cat.config.start} 起，日資料（估值、法人、融資券）回補 ${cat.config.daily_years} 年；每個來源間隔 2.5–4.5 秒，避免被證交所封鎖。`));
  const grid = h("div", { class: "data-grid" });
  for (const d of cat.datasets) {
    const f = d.fetch || {};
    const ok = Object.entries(f).filter(([k]) => k.endsWith("ok")).reduce((a, [, v]) => a + v.n, 0);
    const err = Object.entries(f).filter(([k]) => k.endsWith("error")).reduce((a, [, v]) => a + v.n, 0);
    grid.append(h("div", { class: "data-card" },
      h("div", { class: "dc-head" }, h("b", {}, d.name), h("span", { class: "chip" }, d.freq)),
      h("div", { class: "dc-src" }, d.source),
      h("div", { class: "dc-cov" }, d.rows ? `${d.first} ～ ${d.last}　${nf(d.codes, 0)} 檔　${nf(d.rows, 0)} 筆` : "尚無資料（背景下載中）"),
      d.id !== "prices" ? h("div", { class: "dc-log" }, `已抓 ${ok} 批`, err ? h("span", { class: "down" }, `　失敗 ${err}（會自動重試）`) : null) : null,
      d.note ? h("div", { class: "dc-note" }, d.note) : null,
      h("div", { class: "chips" }, ...d.fields.map((x) => h("button", { class: "chip", title: `${x.desc}（${x.unit}）`, onclick: () => { S.panes[i].df = x.name; renderPane(i); } }, x.name)))));
  }
  box.append(grid);
  // explorer
  const fld = p.df || "月營收";
  const code = p.dc || "2330";
  const fi = h("select", {}, ...cat.datasets.flatMap((d) => d.fields).map((x) => h("option", { value: x.name, selected: x.name === fld ? true : null }, x.name)));
  const ci = h("input", { value: code, class: "tick", style: "width:90px" });
  const go = () => { S.panes[i].df = fi.value; S.panes[i].dc = ci.value.trim().replace(/\.TWO?$/i, ""); renderPane(i); };
  fi.addEventListener("change", go); ci.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const ex = h("div", { class: "data-ex" });
  const chartBox = h("div", { class: "lab-dd", style: "height:180px" });
  box.append(h("div", { class: "lab-sec" }, "🔎 欄位瀏覽"), h("div", { class: "ranges" }, fi, ci, h("button", { onclick: go }, "查詢"),
    h("span", { class: "legend" }, `在策略條件裡直接寫：${fld} > …、ma(${fld}, 3)、rank(${fld})`)), chartBox, ex);
  try {
    const sr = await api(`/api/research/series?field=${encodeURIComponent(fld)}&code=${encodeURIComponent(S.panes[i].dc || code)}&n=500`);
    if (!sr.points.length) chartBox.innerHTML = `<div class="empty">${esc(code)} 沒有「${esc(fld)}」資料</div>`;
    else requestAnimationFrame(() => {
      const ch = LightweightCharts.createChart(chartBox, { ...chartTheme(), autoSize: true });
      const ser = sr.freq === "D" ? ch.addLineSeries({ color: "#ffa028", lineWidth: 1, priceLineVisible: false }) : ch.addHistogramSeries({ color: "#ffa028" });
      ser.setData(sr.points.map((x) => ({ time: x.t, value: x.v })));
      ch.timeScale().fitContent();
      S.charts.set(i, { chart: ch });
      const last = sr.points[sr.points.length - 1];
      ex.textContent = `最新 ${last.t}：${nf(last.v, 2)} ${sr.unit}　（${sr.freq === "D" ? "交易日" : sr.freq === "M" ? "以公布期限日期標示的月資料" : "以公布期限日期標示的季資料"}）`;
    });
  } catch (e) { chartBox.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
  if (cat.errors && cat.errors.length) {
    box.append(h("details", { class: "lab-ref" }, h("summary", {}, `最近的抓取錯誤（${cat.errors.length}）`),
      h("ul", { class: "lab-notes" }, ...cat.errors.map((e) => h("li", {}, `${e.ts.slice(5, 16)} ${e.dataset} ${e.market} ${e.key}：${e.error}`)))));
  }
  box.append(h("div", { class: "lab-sec" }, "研究方法"), h("ul", { class: "lab-notes" },
    h("li", {}, "公布日對齊：月營收用次月 10 日、財報用法定期限（5/15、8/14、11/14、3/31）當作「可以用的日期」，回測不會偷看到還沒公布的數字。"),
    h("li", {}, "還原股價：報酬用除權息還原後的價格計算，避免除息缺口被當成下跌。"),
    h("li", {}, "執行時點：條件在換股日收盤後判斷，隔一個交易日開盤成交；含手續費（可設折扣）、證交稅、滑價。"),
    h("li", {}, "限制：股票池只含目前仍上市櫃的公司（已下市的抓不到）→ 存活者偏差，回測會偏樂觀。")));
}

/* ---------- GP / GIP */
const RANGES = { GP: [["1月", "1mo", "1d"], ["3月", "3mo", "1d"], ["6月", "6mo", "1d"], ["今年", "ytd", "1d"], ["1年", "1y", "1d"], ["2年", "2y", "1d"], ["5年", "5y", "1wk"], ["10年", "10y", "1mo"]],
                 GIP: [["1日", "1d", "5m"], ["5日", "5d", "15m"], ["1月", "1mo", "60m"]] };
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function applyColors() {
  const tw = ((S.settings || {}).color_mode || "tw") === "tw";
  document.documentElement.classList.toggle("tw-colors", tw);
}

async function renderGP(i, body, p) {
  body.classList.add("chart");
  const intraday = p.fn === "GIP";
  const rs = RANGES[p.fn];
  let cur = rs.find((r) => r[1] === p.range) || rs[intraday ? 0 : 4];
  const head = h("div", { class: "qhead" }, h("span", { class: "loading" }, "載入中"));
  const ranges = h("div", { class: "ranges" });
  const legend = h("span", { class: "legend" }, intraday ? "" : "月線 MA20 ─ 季線 MA60 ─");
  rs.forEach((r) => ranges.append(h("button", { class: r === cur ? "on" : "", onclick: () => { S.panes[i].range = r[1]; renderPane(i); } }, r[0])));
  ranges.append(legend);
  const fcBar = h("span", { class: "fc-bar" });
  if (!intraday) {
    fcBar.append(h("button", { class: p.fc ? "on fc-btn" : "fc-btn", title: "TimesFM 時間序列基礎模型預測（只看過去價格）",
      onclick: () => { S.panes[i].fc = p.fc ? 0 : (p.fcH || 30); renderPane(i); } }, "🔮 預測"));
    if (p.fc) [10, 30, 60, 120].forEach((d) => fcBar.append(h("button", { class: p.fc === d ? "on" : "", onclick: () => { S.panes[i].fc = S.panes[i].fcH = d; renderPane(i); } }, d + "日")));
    ranges.append(fcBar);
    ranges.append(h("button", { class: majorOn(p) ? "on mev-btn" : "mev-btn", title: "市場重大事件：指數大漲大跌、Fed 利率決策、你自己加的事件（滑到標記看說明，點標記開「大事」頁）",
      onclick: () => { S.panes[i].mev = !majorOn(p); renderPane(i); } }, "📌 大事"));
    ranges.append(h("button", { class: p.ev ? "on ev-btn" : "ev-btn", title: "God's Eye View 偵測到的現實世界事件（與這檔相關的區域）",
      onclick: () => { S.panes[i].ev = !p.ev; renderPane(i); } }, "🌐 事件"));
  }
  const box = h("div", { class: "chart-box" });
  body.append(head, ranges, box);
  try {
    const [rows, q] = await Promise.all([api(`/api/history?s=${encodeURIComponent(p.sec)}&range=${cur[1]}&interval=${cur[2]}`),
                                         api(`/api/quotes?s=${encodeURIComponent(p.sec)}`).then((x) => x[0]).catch(() => null)]);
    if (S.panes[i].sec !== p.sec || S.panes[i].fn !== p.fn) return;
    if (!rows.length) { box.innerHTML = `<div class="empty">${esc(p.sec)} 沒有資料（代號可能錯誤？台股可直接輸入 2330 或「台積電」）</div>`; head.innerHTML = ""; return; }
    const lastBar = rows[rows.length - 1]; const first = rows[0];
    const last = q && q.last != null ? q.last : lastBar.close;
    const rchg = (last / first.close - 1) * 100;
    head.innerHTML = `<span class="px">${nf(last, pxd(last))}</span>
      <span class="chg ${cls(q && q.pct)}">${q ? (q.chg > 0 ? "+" : "") + nf(q.chg, pxd(last)) + " (" + pct(q.pct) + ")" : ""}</span>
      <span class="meta">區間 <span class="${cls(rchg)}">${pct(rchg)}</span> · 高 ${nf(Math.max(...rows.map((r) => r.high)), pxd(last))} · 低 ${nf(Math.min(...rows.map((r) => r.low)), pxd(last))}${q && q.lots != null ? " · 量 " + nf(q.lots, 0) + " 張" : ""}${q && q.currency ? " · " + q.currency : ""}${q && q.indicative ? " · 試算價" : ""}</span>`;
    drawCandles(i, box, rows, !intraday);
    if (p.fc && !intraday) overlayForecast(i, p.sec, p.fc, legend);
    if (p.ev && !intraday) overlayEvents(i, p.sec, rows);
    if (majorOn(p) && !intraday) overlayMajor(i, p.sec, rows);
    if (p.focusDate && !intraday) { focusChartOn(i, p.focusDate); S.panes[i].focusDate = null; }
  } catch (e) { box.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; head.innerHTML = ""; }
}

function chartTheme() {
  return {
    layout: { background: { color: "#07090b" }, textColor: "#8a939c", fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11 },
    grid: { vertLines: { color: "#11161b" }, horzLines: { color: "#11161b" } },
    rightPriceScale: { borderColor: "#1f252c" }, timeScale: { borderColor: "#1f252c", timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0, vertLine: { color: "#a8661a", labelBackgroundColor: "#a8661a" }, horzLine: { color: "#a8661a", labelBackgroundColor: "#a8661a" } },
    localization: { locale: "zh-TW" },
  };
}

function drawCandles(i, box, rows, withMA) {
  const chart = LightweightCharts.createChart(box, { ...chartTheme(), autoSize: true });
  const UP = cssVar("--up"), DN = cssVar("--down");
  const c = chart.addCandlestickSeries({ upColor: UP, downColor: DN, borderVisible: false, wickUpColor: UP, wickDownColor: DN });
  c.setData(rows.map((r) => ({ time: r.time, open: r.open, high: r.high, low: r.low, close: r.close })));
  const v = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", color: "#2c343d" });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  v.setData(rows.map((r) => ({ time: r.time, value: r.volume, color: (r.close >= r.open ? UP : DN) + "59" })));
  if (withMA) {
    for (const [n, col] of [[20, "#ffa028"], [60, "#4ba3ff"]]) {
      if (rows.length < n) continue;
      const s = chart.addLineSeries({ color: col, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      const pts = []; let sum = 0;
      rows.forEach((r, k) => { sum += r.close; if (k >= n) sum -= rows[k - n].close; if (k >= n - 1) pts.push({ time: r.time, value: sum / n }); });
      s.setData(pts);
    }
  }
  chart.timeScale().fitContent();
  S.charts.set(i, { chart, candles: c, fit: () => chart.timeScale().fitContent() });
}

const EV_STYLE = { quake: ["#ff8a3d", "地震"], military: ["#ff4dd2", "軍機"], shipping: ["#3ad6d6", "航運"], cyclone: ["#4ba3ff", "氣旋"], fires: ["#ffa028", "野火"] };

async function overlayEvents(i, sec, rows) {
  let evs;
  try { evs = await api(`/api/world/events?symbol=${encodeURIComponent(sec)}&since=${rows[0].time}`); } catch (e) { return; }
  const c = S.charts.get(i);
  if (!c || !c.candles || S.panes[i].sec !== sec || !evs.length) return;
  const times = rows.map((r) => r.time);
  const byTime = new Map();
  for (const e of evs) {
    const t = times.find((x) => x >= e.date) || times[times.length - 1];
    if (!byTime.has(t)) byTime.set(t, []);
    byTime.get(t).push(e);
  }
  const markers = [...byTime.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([t, es]) => {
    const [color, label] = EV_STYLE[es[0].kind] || ["#e6e6e6", "事件"];
    return { time: t, position: "aboveBar", color, shape: "arrowDown", text: es.length > 1 ? `${label}+${es.length - 1}` : label };
  });
  c.mk = { ...(c.mk || {}), world: markers };
  applyMarkers(c);
  const box = paneEl(i) && $(".chart-box", paneEl(i));
  if (box) box.title = evs.slice(0, 12).map((e) => `${e.date} ${e.title}`).join("\n");
}

/* ---------- 重大事件 on the chart */
const majorOn = (p) => (p.mev === undefined || p.mev === null ? (p.sec || "").startsWith("^") : !!p.mev);
const tStr = (t) => (typeof t === "string" ? t : t && t.year ? `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}` : typeof t === "number" ? new Date(t * 1000).toISOString().slice(0, 10) : "");

function applyMarkers(c) {
  const all = [...((c.mk && c.mk.world) || []), ...((c.mk && c.mk.major) || [])];
  all.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  c.candles.setMarkers(all);
}

async function overlayMajor(i, sec, rows) {
  let evs;
  try { evs = await api(`/api/events/markers?since=${rows[0].time}&s=${encodeURIComponent(sec)}`); } catch (e) { return; }
  const c = S.charts.get(i);
  if (!c || !c.candles || S.panes[i].sec !== sec || !evs.length) return;
  const times = rows.map((r) => r.time);
  const byTime = new Map();
  for (const e of evs) {
    const t = times.find((x) => x >= e.date);
    if (!t) continue;
    if (!byTime.has(t)) byTime.set(t, []);
    byTime.get(t).push(e);
  }
  const UP = cssVar("--up"), DN = cssVar("--down");
  const mk = [];
  for (const [t, es] of byTime) {
    const e = es.find((x) => x.kind === "history") || es.find((x) => x.kind === "move") || es[0];
    const short = e.label ? clip(e.title.replace(/（.*?）/g, ""), 7) + (es.length > 1 ? ` +${es.length - 1}` : "") : "";
    if (e.kind === "move") mk.push({ time: t, position: e.dir > 0 ? "belowBar" : "aboveBar", color: e.dir > 0 ? UP : DN, shape: e.dir > 0 ? "arrowUp" : "arrowDown", text: short });
    else if (e.kind === "fomc") mk.push({ time: t, position: "belowBar", color: "#b48cff", shape: "square", text: "Fed" });
    else if (e.kind === "cbc") mk.push({ time: t, position: "belowBar", color: "#3ad6d6", shape: "square", text: "央行" });
    else if (e.kind === "macro") mk.push({ time: t, position: "belowBar", color: "#ff9f43", shape: "square", text: /CPI/.test(e.title) ? "CPI" : /非農/.test(e.title) ? "非農" : "PCE" });
    else if (e.kind === "history") mk.push({ time: t, position: e.dir > 0 ? "belowBar" : "aboveBar", color: "#e6e6e6", shape: "circle", text: short });
    else mk.push({ time: t, position: "aboveBar", color: "#ffd166", shape: "circle", text: short });
  }
  c.mk = { ...(c.mk || {}), major: mk };
  c.majorByTime = byTime;
  applyMarkers(c);
  const box = paneEl(i) && $(".chart-box", paneEl(i));
  if (!box) return;
  const tip = h("div", { class: "ev-tip hidden" });
  box.append(tip);
  c.chart.subscribeCrosshairMove((param) => {
    const es = param && param.time ? byTime.get(tStr(param.time)) : null;
    if (!es || !param.point) { tip.classList.add("hidden"); return; }
    tip.innerHTML = es.map((e) => `<div><b>${esc(e.date)} ${esc(e.title)}</b>` +
      (Object.values(e.moves || {}).length ? `<div class="ev-mv">${Object.values(e.moves).map((m) => `<span class="${cls(m.pct)}">${esc(m.name)} ${pct(m.pct, 1)}${m.kind === "week" ? "（5日）" : ""}</span>`).join(" ")}</div>` : "") +
      (e.summary ? `<div class="muted">${esc(e.summary)}</div>` : "") + `</div>`).join("") + `<div class="ev-hint">點一下看「大事」頁</div>`;
    tip.classList.remove("hidden");
    const w = box.clientWidth;
    tip.style.left = Math.min(Math.max(8, param.point.x + 14), w - 300) + "px";
    tip.style.top = Math.max(8, param.point.y - 20) + "px";
  });
  c.chart.subscribeClick((param) => {
    const es = param && param.time ? byTime.get(tStr(param.time)) : null;
    if (!es) return;
    const el = paneEl(i);
    if (el && el.classList.contains("maximized")) toggleMax(i);  // the 大事 page opens next to the chart, so make it visible
    setPane(otherPane(i, "EVENTS"), { fn: "EVENTS", evFocus: es[0].id });
  });
}

function focusChartOn(i, date) {
  const c = S.charts.get(i);
  if (!c) return;
  const d = new Date(date + "T00:00:00Z");
  const f = (k) => new Date(d.getTime() + k * 86400000).toISOString().slice(0, 10);
  try { c.chart.timeScale().setVisibleRange({ from: f(-60), to: f(45) }); } catch (e) {}
}

/* ---------- TimesFM overlay */
async function overlayForecast(i, sec, horizon, legend) {
  legend.textContent = "TimesFM 預測中…（第一次要載入模型）";
  let d;
  try { d = await api(`/api/forecast?s=${encodeURIComponent(sec)}&h=${horizon}`); }
  catch (e) { legend.innerHTML = `<span class="down">預測失敗：${esc(e.message).slice(0, 80)}</span>`; return; }
  const c = S.charts.get(i);
  if (!c || S.panes[i].sec !== sec) return;
  drawForecast(c.chart, d);
  const sm = d.summary;
  legend.innerHTML = `${esc(d.model)} ${d.horizon}日：中位數 <b class="${cls(sm.change_pct)}">${nf(sm.median_end, pxd(sm.median_end))}（${pct(sm.change_pct)}）</b>
    · 80% ${nf(sm.p10_end, pxd(sm.p10_end))}–${nf(sm.p90_end, pxd(sm.p90_end))} · 收高機率 ${Math.round(sm.p_up * 100)}%`;
  legend.title = "TimesFM 只根據過去價格做統計預測，不含新聞與基本面；色帶是 P10–P90（80%）區間";
}

function drawForecast(chart, d) {
  const bg = "#07090b", band = "#4ba3ff";
  const start = { time: d.last_date, value: d.last };
  const hi = chart.addAreaSeries({ lineColor: band + "99", topColor: band + "38", bottomColor: band + "38", lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  hi.setData([start, ...d.forecast.map((x) => ({ time: x.date, value: x.p90 }))]);
  const lo = chart.addAreaSeries({ lineColor: band + "99", topColor: bg, bottomColor: bg, lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  lo.setData([start, ...d.forecast.map((x) => ({ time: x.date, value: x.p10 }))]);
  const med = chart.addLineSeries({ color: "#ffa028", lineWidth: 2, lineStyle: 2, priceLineVisible: false, title: "TimesFM" });
  med.setData([start, ...d.forecast.map((x) => ({ time: x.date, value: x.median }))]);
  chart.timeScale().fitContent();
}

/* ---------- DES */
async function renderDES(i, body, p) {
  body.innerHTML = `<div class="loading">載入 ${esc(p.sec)} 概況</div>`;
  try {
    const [d, q] = await Promise.all([api(`/api/des?s=${encodeURIComponent(p.sec)}`), api(`/api/quotes?s=${encodeURIComponent(p.sec)}`).then((x) => x[0])]);
    if (S.panes[i].sec !== p.sec) return;
    const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
    body.innerHTML = `<div class="des">
      <h3>${d.zhName ? esc(d.zhName) + " " : ""}<span style="${d.zhName ? "color:var(--muted);font-weight:400" : ""}">${esc(d.longName || d.shortName || p.sec)}</span></h3>
      <div class="sub">${esc(p.sec)} · ${esc(d.exchange || "")} · ${esc(d.sector || "")} / ${esc(d.industry || "")} · ${esc(d.country || "")}</div>
      <div>
        <div class="sec-title">價格</div>
        ${kv("現價", `<span class="${cls(q.pct)}">${nf(q.last, pxd(q.last))} ${pct(q.pct)}</span>`)}
        ${kv("52 週高 / 低", `${nf(d.fiftyTwoWeekHigh)} / ${nf(d.fiftyTwoWeekLow)}`)}
        ${kv("50 / 200 日均", `${nf(d.fiftyDayAverage)} / ${nf(d.twoHundredDayAverage)}`)}
        ${kv("Beta", nf(d.beta))}
        ${kv("平均量", big(d.averageVolume))}
        ${kv("分析師目標價", `${nf(d.targetMeanPrice)} (${d.numberOfAnalystOpinions ?? "—"} 位 · ${esc(d.recommendationKey || "—")})`)}
      </div>
      <div>
        <div class="sec-title">估值</div>
        ${kv("市值", big(d.marketCap) + " " + esc(d.currency || ""))}
        ${kv("企業價值", big(d.enterpriseValue))}
        ${kv("本益比 TTM / 預估", `${nf(d.trailingPE)} / ${nf(d.forwardPE)}`)}
        ${kv("股價淨值比", nf(d.priceToBook))}
        ${kv("股價營收比", nf(d.priceToSalesTrailing12Months))}
        ${kv("殖利率", d.dividendYield != null ? nf(d.dividendYield) + "%" : "—")}
        ${kv("EPS TTM / 預估", `${nf(d.trailingEps)} / ${nf(d.forwardEps)}`)}
      </div>
      <div>
        <div class="sec-title">獲利與體質</div>
        ${kv("毛利率", ratio(d.grossMargins))}
        ${kv("營益率", ratio(d.operatingMargins))}
        ${kv("淨利率", ratio(d.profitMargins))}
        ${kv("ROE / ROA", `${ratio(d.returnOnEquity)} / ${ratio(d.returnOnAssets)}`)}
        ${kv("營收成長", ratio(d.revenueGrowth))}
        ${kv("負債權益比", nf(d.debtToEquity))}
        ${kv("流動比", nf(d.currentRatio))}
        ${kv("自由現金流", big(d.freeCashflow))}
      </div>
      ${twProfileHtml(d.twProfile)}
      ${d.longBusinessSummary ? `<div class="summary"><div class="sec-title" style="margin:0 0 6px">公司簡介 <button class="mini-btn" data-tr="1">AI 翻成中文</button></div><div class="sum-text">${esc(d.longBusinessSummary)}</div></div>` : ""}
      ${d.website ? `<div><a href="${esc(d.website)}" target="_blank" rel="noopener">${esc(d.website)}</a> · 員工 ${big(d.fullTimeEmployees)}</div>` : ""}
    </div>`;
    const tb = $("[data-tr]", body);
    if (tb) tb.addEventListener("click", async () => {
      tb.disabled = true; tb.textContent = "翻譯中…";
      try { const r = await api("/api/translate", { method: "POST", json: { text: d.longBusinessSummary } }); $(".sum-text", body).textContent = r.text; tb.remove(); }
      catch (e) { tb.textContent = "翻譯失敗"; }
    });
    if (isUSStock(p.sec)) secFilings(i, $(".des", body), p.sec);
  } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

const isUSStock = (s) => !!s && !isTW(s) && !isFund(s) && !/^\^|=X$|=F$|-USD$|\.[A-Z]{1,3}$/.test(s);

async function secFilings(i, box, sec) {
  const wrap = h("div", { class: "sec-box" }, h("div", { class: "sec-title" }, "SEC 最近申報（EDGAR 官方）"), h("div", { class: "loading" }, "讀取"));
  box.append(wrap);
  let d;
  try { d = await api(`/api/sec/filings?s=${encodeURIComponent(sec)}&limit=15`); } catch (e) { wrap.lastChild.replaceWith(h("div", { class: "muted" }, e.message)); return; }
  if (S.panes[i].sec !== sec) return;
  if (!d.enabled) { wrap.lastChild.replaceWith(h("div", { class: "muted" }, d.message)); return; }
  if (!d.filings.length) { wrap.lastChild.replaceWith(h("div", { class: "muted" }, "SEC 沒有這家公司的申報（可能不是在美國申報）")); return; }
  const t = h("table", { class: "grid" });
  t.innerHTML = `<thead><tr><th>申報日</th><th style="text-align:left">表單</th><th style="text-align:left">內容</th></tr></thead>`;
  const tb = h("tbody");
  for (const f of d.filings)
    tb.append(h("tr", {}, h("td", {}, f.date), h("td", { class: "nm" }, h("a", { href: f.url, target: "_blank", rel: "noopener noreferrer" }, f.form), " ", h("span", { class: "muted" }, f.form_zh)),
      h("td", { class: "nm" }, f.items.length ? f.items.map((x) => `${x.code} ${x.zh}`).join("；") : (f.report_date ? `期間 ${f.report_date}` : f.desc))));
  t.append(tb);
  wrap.lastChild.replaceWith(t);
}

function twProfileHtml(p) {
  if (!p) return "";
  const keys = [["公司名稱", "公司名稱"], ["產業別", "產業別"], ["市場", "市場"], ["董事長", "董事長"], ["總經理", "總經理"], ["發言人", "發言人"],
    ["成立日期", "成立日期"], ["上市日期", "上市日期"], ["上櫃日期", "上櫃日期"], ["實收資本額", "實收資本額"], ["營利事業統一編號", "統一編號"],
    ["住址", "地址"], ["網址", "網址"], ["ISIN", "ISIN"]];
  const rows = keys.filter(([k]) => p[k]).map(([k, l]) => {
    let v = String(p[k]);
    if (k === "實收資本額" && /^\d+$/.test(v)) v = big(+v) + " 元";
    if (k === "網址") v = `<a href="${esc(/^https?:/.test(v) ? v : "https://" + v)}" target="_blank" rel="noopener">${esc(v)}</a>`; else v = esc(v);
    return `<div class="kv"><span>${l}</span><span>${v}</span></div>`;
  });
  if (!rows.length) return "";
  return `<div class="tw-prof"><div class="sec-title">公司基本資料（證交所／櫃買中心）</div><div class="tw-grid">${rows.join("")}</div></div>`;
}

/* ---------- N */
async function renderN(i, body, p) {
  const lang = p.newsLang || (isTW(p.sec) ? "zh" : "en");
  body.innerHTML = `<div class="loading">載入 ${esc(showName(p.sec))} 新聞</div>`;
  try {
    const items = await api(`/api/news?s=${encodeURIComponent(p.sec)}&lang=${lang}`);
    if (S.panes[i].sec !== p.sec) return;
    body.innerHTML = "";
    const tools = h("div", { class: "ranges" },
      ...[["zh", "中文新聞"], ["en", "英文（Yahoo／Google）"]].map(([l, t]) => h("button", { class: l === lang ? "on" : "", onclick: () => { S.panes[i].newsLang = l; renderPane(i); } }, t)),
      h("span", { class: "legend" }),
      h("button", { onclick: () => { openSide("fingpt"); prefillEngine("fingpt", p.sec, { mode: "sentiment" }); } }, "FinGPT 情緒分析 ▸"));
    body.append(tools);
    if (!items.length) { body.append(h("div", { class: "empty" }, "沒有新聞")); return; }
    for (const n of items) {
      body.append(h("div", { class: "news-item" },
        h("span", { class: "t" }, timeAgo(n.time)),
        h("span", { class: "h" }, n.link ? h("a", { href: n.link, target: "_blank", rel: "noopener" }, n.title) : n.title),
        n.summary ? h("span", { class: "sum" }, n.summary) : null,
        h("span", { class: "src" }, n.publisher || "")));
    }
  } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

/* ---------- FA */
async function renderFA(i, body, p) {
  const freq = p.faFreq || "yearly";
  if (isUSStock(p.sec) && p.faSrc === "sec") return renderFASec(i, body, p, freq);
  body.innerHTML = `<div class="loading">載入財報</div>`;
  try {
    const d = await api(`/api/fa?s=${encodeURIComponent(p.sec)}&freq=${freq}`);
    if (S.panes[i].sec !== p.sec) return;
    body.innerHTML = "";
    body.append(faBar(i, p, freq));
    const names = { income: "損益表", balance: "資產負債表", cashflow: "現金流量表" };
    const ZH = { "Total Revenue": "營業收入", "Gross Profit": "營業毛利", "Operating Income": "營業利益", "Net Income": "稅後淨利",
      "Diluted EPS": "稀釋每股盈餘", "EBITDA": "EBITDA", "Total Assets": "資產總額", "Total Liabilities Net Minority Interest": "負債總額",
      "Stockholders Equity": "股東權益", "Total Debt": "有息負債", "Cash And Cash Equivalents": "現金及約當現金",
      "Ordinary Shares Number": "流通在外股數", "Operating Cash Flow": "營業現金流", "Capital Expenditure": "資本支出",
      "Free Cash Flow": "自由現金流", "Repurchase Of Capital Stock": "買回庫藏股", "Cash Dividends Paid": "發放現金股利" };
    let any = false;
    for (const k of ["income", "balance", "cashflow"]) {
      const t = d[k]; if (!t || !t.rows || !t.rows.length) continue;
      any = true;
      const tbl = h("table", { class: "grid" });
      tbl.innerHTML = `<thead><tr><th>${names[k]}</th>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
        <tbody>${t.rows.map((r) => `<tr><td class="nm" title="${esc(r[0])}">${esc(ZH[r[0]] || r[0])}</td>${r.slice(1).map((v) => `<td>${/EPS/.test(r[0]) ? nf(v) : big(v)}</td>`).join("")}</tr>`).join("")}</tbody>`;
      body.append(tbl);
    }
    if (!any) body.append(h("div", { class: "empty" }, "此標的沒有財報資料（指數／匯率／商品／ETF）"));
    body.append(h("div", { class: "hint", style: "padding:6px 10px" }, "資料來源：Yahoo Finance（依公司申報幣別）"));
  } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

function faBar(i, p, freq) {
  const bar = h("div", { class: "ranges" }, ...[["yearly", "年度"], ["quarterly", "季度"]].map(([f, l]) =>
    h("button", { class: f === freq ? "on" : "", onclick: () => { S.panes[i].faFreq = f; renderPane(i); } }, l)));
  if (isUSStock(p.sec)) {
    bar.append(h("span", { class: "sep-s" }));
    [["yahoo", "Yahoo"], ["sec", "SEC 官方（含申報日）"]].forEach(([k, l]) =>
      bar.append(h("button", { class: (p.faSrc || "yahoo") === k ? "on" : "", onclick: () => { S.panes[i].faSrc = k; renderPane(i); } }, l)));
  }
  return bar;
}

async function renderFASec(i, body, p, freq) {
  body.innerHTML = `<div class="loading">讀取 SEC EDGAR XBRL 財報</div>`;
  let d;
  try { d = await api(`/api/sec/financials?s=${encodeURIComponent(p.sec)}&freq=${freq === "quarterly" ? "quarterly" : "annual"}`); }
  catch (e) { body.innerHTML = ""; body.append(faBar(i, p, freq), h("div", { class: "errbox" }, e.message)); return; }
  if (S.panes[i].sec !== p.sec) return;
  body.innerHTML = "";
  body.append(faBar(i, p, freq));
  if (d.error) { body.append(h("div", { class: "empty" }, d.error)); return; }
  const t = h("table", { class: "grid" });
  t.innerHTML = `<thead><tr><th>${esc(d.name || p.sec)}（${freq === "quarterly" ? "單季" : "年度"}）</th>${d.periods.map((x) => `<th title="${esc(x.form)} ${esc(x.fp || "")}">${esc(x.end)}<br><small style="color:var(--muted);font-weight:400">${esc(x.form)} · ${esc(x.filed)} 申報</small></th>`).join("")}</tr></thead>
    <tbody>${d.rows.map((r) => `<tr><td class="nm">${esc(r.label)}</td>${r.values.map((v) => `<td>${/每股/.test(r.label) ? nf(v) : big(v)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  body.append(h("div", { class: "hold-scroll" }, t),
    h("div", { class: "hint", style: "padding:6px 10px" }, "資料來源：SEC EDGAR XBRL（公司原始申報，美元）。每一期取「第一次申報」的數字與日期，可避免回測用到當時還沒公布的資料；季度欄只列單季（Q4 包含在年報）。"));
}

/* ---------- quote tables (monitors + watchlist) */
function volCell(r) {
  if (r.lots != null && isTW(r.symbol)) return nf(r.lots, 0) + "<small> 張</small>";
  return big(r.volume);
}

function quoteTable(rows, onRemove) {
  const tbl = h("table", { class: "grid" });
  tbl.innerHTML = `<thead><tr><th>代號</th><th style="text-align:left">名稱</th><th>成交</th><th>漲跌</th><th>漲跌幅</th><th>最高</th><th>最低</th><th>成交量</th>${onRemove ? "<th></th>" : ""}</tr></thead>`;
  const tb = h("tbody");
  for (const r of rows) {
    if (r.name) NAMES[r.symbol] = r.name;
    if (r.kind) KIND[r.symbol] = r.kind;
    const prev = S.lastPx[r.symbol]; S.lastPx[r.symbol] = r.last;
    const flash = prev != null && r.last != null && prev !== r.last ? (r.last > prev ? "flash-up" : "flash-down") : "";
    const tr = h("tr", { class: "click", ondblclick: () => run(`${r.symbol} GP`), onclick: () => quickOpen(r.symbol) });
    tr.innerHTML = `<td class="sym">${esc(r.symbol.replace(/\.TWO?$/, ""))}</td><td class="nm">${esc(r.name || "")}${r.kind === "ETF" ? ' <span class="tag">ETF</span>' : ""}</td>` +
      (r.error ? `<td colspan="6" class="down" style="text-align:left">${esc(r.error)}</td>` :
      `<td class="${flash}">${nf(r.last, pxd(r.last))}${r.indicative ? "<small>*</small>" : ""}</td><td class="${cls(r.chg)}">${r.chg > 0 ? "+" : ""}${nf(r.chg, pxd(r.last))}</td>
       <td class="${cls(r.pct)}">${pct(r.pct)}</td><td>${nf(r.high, pxd(r.last))}</td><td>${nf(r.low, pxd(r.last))}</td><td>${volCell(r)}</td>`);
    if (onRemove) tr.append(h("td", {}, h("span", { class: "x", title: "移除", onclick: (e) => { e.stopPropagation(); onRemove(r.symbol); } }, "✕")));
    tb.append(tr);
  }
  tbl.append(tb);
  return tbl;
}

function quickOpen(sym, fn) {
  // single click in a list: open in the *next* pane so the list stays visible
  if (isFund(sym)) fn = "FUNDD";
  fn = fn || "GP";
  const target = S.panes.findIndex((p, j) => j !== S.focus && (p.fn === fn || (fn !== "FUNDD" && p.fn === "GP")));
  const j = target >= 0 ? target : (S.focus + 1) % 4;
  setPane(j, { sec: sym, fn });
}

async function renderMon(i, body, p) {
  body.innerHTML = `<div class="loading">載入 ${esc(MON_TITLE[p.fn] || p.fn)}</div>`;
  try {
    const d = await api(`/api/monitor/${p.fn}`);
    if (S.panes[i].fn !== p.fn) return;
    $(".pane-name", paneEl(i)).textContent = "單擊在下一格看圖、雙擊在本格";
    body.innerHTML = ""; body.append(quoteTable(d.rows));
    if (d.rows.some((r) => r.indicative)) body.append(h("div", { class: "hint", style: "padding:4px 10px" }, "* 試算價：該時點無成交，以最佳買賣價中間值顯示"));
  } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

async function renderW(i, body) {
  const wl = S.settings.watchlist || [];
  const inp = h("input", { placeholder: "加入：2330、台積電、0056、AAPL、BTC-USD…" });
  const add = async () => {
    const s = await resolveSec(inp.value); if (!s) { flash("找不到這個標的"); return; }
    if (isFund(s)) { flash("自選清單目前只放股票／ETF／指數，基金請用 基金 指令"); return; }
    if (!wl.includes(s)) wl.push(s);
    S.settings = await api("/api/settings", { method: "PUT", json: { watchlist: wl } });
    renderPane(i); tape();
  };
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  body.innerHTML = "";
  body.append(h("div", { class: "wl-add" }, inp, h("button", { class: "btn ghost", onclick: add }, "＋ 加入")));
  if (!wl.length) { body.append(h("div", { class: "empty" }, "自選清單是空的")); return; }
  const holder = h("div", {}, h("div", { class: "loading" }, "報價"));
  body.append(holder);
  try {
    const rows = await api(`/api/quotes?s=${encodeURIComponent(wl.join(","))}`);
    holder.innerHTML = "";
    holder.append(quoteTable(rows, async (sym) => {
      S.settings = await api("/api/settings", { method: "PUT", json: { watchlist: wl.filter((x) => x !== sym) } });
      renderPane(i); tape();
    }));
    $(".pane-name", paneEl(i)).textContent = "單擊看圖";
  } catch (e) { holder.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

/* ---------- ETF 總覽 */
const ETF_CATS = ["", "股票型", "高股息", "債券", "主動式", "槓桿/反向", "期貨/商品"];
const ETF_SORTS = [["scale", "規模"], ["premium", "溢價"], ["discount", "折價"], ["creation", "淨申購"], ["redemption", "淨買回"], ["code", "代號"]];

async function renderETFMon(i, body, p) {
  const st = p.etf || (p.etf = { sort: "scale", cat: "", q: "" });
  body.innerHTML = "";
  const bar = h("div", { class: "ranges" });
  ETF_CATS.forEach((c) => bar.append(h("button", { class: st.cat === c ? "on" : "", onclick: () => { st.cat = c; renderPane(i); } }, c || "全部")));
  bar.append(h("span", { class: "legend" }, "排序"));
  ETF_SORTS.forEach(([k, l]) => bar.append(h("button", { class: st.sort === k ? "on" : "", onclick: () => { st.sort = k; renderPane(i); } }, l)));
  const q = h("input", { class: "mini-input", placeholder: "代號／名稱篩選", value: st.q });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { st.q = q.value.trim(); renderPane(i); } });
  bar.append(q);
  body.append(bar);
  const holder = h("div", {}, h("div", { class: "loading" }, "載入證交所 ETF 淨值揭露"));
  body.append(holder);
  try {
    const d = await api(`/api/tw/etfs?sort=${st.sort}&category=${encodeURIComponent(st.cat)}&q=${encodeURIComponent(st.q)}`);
    if (S.panes[i].fn !== "ETFM") return;
    const dt = d.date ? `${d.date.slice(0, 4)}-${d.date.slice(4, 6)}-${d.date.slice(6)}` : "";
    $(".pane-name", paneEl(i)).textContent = `${d.count} 檔 · ${dt} · 單擊看 ETF 資料`;
    const tbl = h("table", { class: "grid" });
    tbl.innerHTML = `<thead><tr><th>代號</th><th style="text-align:left">名稱</th><th>市價</th><th>預估淨值</th><th>折溢價</th><th>前日淨值</th><th>規模(億)</th><th>單位數增減</th><th style="text-align:left">類別</th><th>時間</th></tr></thead>`;
    const tb = h("tbody");
    for (const r of d.rows) {
      NAMES[r.symbol] = r.name; KIND[r.symbol] = "ETF";
      const prem = r.premium == null ? "" : Math.abs(r.premium) >= 1 ? "warn" : "";
      const tr = h("tr", { class: "click", onclick: () => quickOpen(r.symbol, "ETF"), ondblclick: () => setPane(i, { sec: r.symbol, fn: "ETF" }) });
      tr.innerHTML = `<td class="sym">${esc(r.code)}</td><td class="nm">${esc(r.name)}</td><td>${nf(r.price)}</td><td>${nf(r.est_nav)}</td>
        <td class="${cls(r.premium)} ${prem}">${pct(r.premium)}</td><td>${r.prior_nav != null ? nf(r.prior_nav, 2) : esc(r.prior_nav_text || "—")}</td>
        <td>${r.scale ? nf(r.scale / 1e8, 0) : "—"}</td><td class="${cls(r.units_chg)}">${r.units_chg != null ? (r.units_chg > 0 ? "+" : "") + nf(r.units_chg / 1000, 0) + "<small>千</small>" : "—"}</td>
        <td class="nm">${esc(r.category)}</td><td>${esc(r.time || "")}</td>`;
      tb.append(tr);
    }
    tbl.append(tb);
    holder.innerHTML = "";
    holder.append(tbl);
    holder.append(h("div", { class: "hint", style: "padding:6px 10px" }, "預估淨值與折溢價：各投信揭露（證交所 ETF 淨值揭露專區，約 15 秒更新）。折溢價 ±1% 以上標示；單位數增減 ＝ 淨申購／淨買回。"));
  } catch (e) { holder.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

/* ---------- ETF 資料 */
async function renderETF(i, body, p) {
  const code = p.sec.replace(/\.TWO?$/, "");
  body.innerHTML = `<div class="loading">載入 ${esc(code)} ETF 資料</div>`;
  try {
    const d = await api(`/api/tw/etf?code=${encodeURIComponent(code)}`);
    if (S.panes[i].sec !== p.sec) return;
    const q = d.quote || {}, n = d.nav || {}, inf = d.info || {}, dv = d.dividends || {};
    NAMES[p.sec] = d.name || NAMES[p.sec]; KIND[p.sec] = "ETF";
    const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
    const premCls = n.premium == null ? "" : Math.abs(n.premium) >= 1 ? "warn" : cls(n.premium);
    body.innerHTML = `<div class="des">
      <h3>${esc(code)} ${esc(d.name || "")} <span class="tag">${esc(d.category || "ETF")}</span></h3>
      <div class="sub">${esc(inf["基金名稱"] || "")}${inf["經理公司"] ? " · " + esc(inf["經理公司"]) : ""}</div>
      <div class="etf-hero">
        <div><div class="lbl">市價</div><div class="big ${cls(q.pct)}">${nf(q.last)}</div><div class="${cls(q.pct)}">${q.chg > 0 ? "+" : ""}${nf(q.chg)}（${pct(q.pct)}）</div></div>
        <div><div class="lbl">預估淨值</div><div class="big">${nf(n.est_nav, 2)}</div><div class="muted">前日 ${n.prior_nav != null ? nf(n.prior_nav, 2) : esc(n.prior_nav_text || "—")}</div></div>
        <div><div class="lbl">折溢價</div><div class="big ${premCls}">${pct(n.premium)}</div><div class="muted">${n.premium == null ? "" : n.premium >= 1 ? "溢價偏高，留意追價" : n.premium <= -1 ? "折價" : "接近淨值"}</div></div>
        <div><div class="lbl">規模</div><div class="big">${n.scale ? nf(n.scale / 1e8, 0) + "<small> 億</small>" : "—"}</div><div class="${cls(n.units_chg)}">單位 ${n.units_chg != null ? (n.units_chg > 0 ? "+" : "") + big(n.units_chg) : "—"}</div></div>
        <div><div class="lbl">近一年配息</div><div class="big">${dv.ttm ? nf(dv.ttm, 3) : "—"}</div><div class="muted">${esc(dv.frequency || "")}${dv.yield_ttm != null ? " · 殖利率 " + nf(dv.yield_ttm) + "%" : ""}</div></div>
      </div>
      <div>
        <div class="sec-title">基本資料</div>
        ${kv("經理公司", esc(inf["經理公司"] || "—"))}
        ${kv("成立日期", esc(inf["基金成立日期"] || inf["成立日期"] || "—"))}
        ${kv("分類", esc(inf["分類"] || inf["基金類型"] || "—"))}
        ${kv("追蹤指數", esc(inf["追蹤指數"] || inf["標的指數/追蹤指數名稱"] || "—"))}
        ${kv("配息", esc(inf["配息"] || dv.frequency || "—"))}
        ${inf["經理人"] ? kv("經理人", esc(inf["經理人"])) : ""}
        ${inf["保管機構"] ? kv("保管機構", esc(inf["保管機構"])) : ""}
        ${kv("成交量", q.lots != null ? nf(q.lots, 0) + " 張" : "—")}
        ${kv("淨值資料時間", esc(`${n.date || ""} ${n.time || ""}`))}
      </div>
      <div>
        <div class="sec-title">配息紀錄（除息日・每單位）</div>
        <div class="div-list">${(dv.items || []).slice(0, 12).map((x) => `<div class="kv"><span>${esc(x.date)}</span><span>${nf(x.amount, 3)}</span></div>`).join("") || '<div class="muted">無配息紀錄</div>'}</div>
      </div>
      <div class="hold-wrap" style="grid-column:1/-1"><div class="sec-title">成分股 <span class="muted" id="holdMeta"></span>
        <button class="mini-btn" data-q="0">每月前十大</button><button class="mini-btn" data-q="1">每季全部（&gt;1%）</button></div>
        <div class="holdings"><div class="loading">查詢投信投顧公會基金投資明細（第一次較慢）</div></div></div>
      <div style="grid-column:1/-1;display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn ghost" data-act="gp">走勢圖</button><button class="btn ghost" data-act="n">新聞</button>
        <button class="btn ghost" data-act="ask">問 AI 這檔 ETF</button><button class="btn ghost" data-act="fg">FinGPT 新聞情緒</button>
        <button class="btn ghost" data-act="w">加入自選</button>
      </div>
    </div>`;
    const acts = { gp: () => quickOpen(p.sec, "GP"), n: () => quickOpen(p.sec, "N"),
      ask: () => { setFocus(i); askSend(`請分析 ${code} ${d.name || ""} 這檔 ETF：折溢價、規模與申贖趨勢、配息與殖利率、成分股集中度，適合什麼樣的投資人？`); },
      fg: () => { openSide("fingpt"); prefillEngine("fingpt", p.sec, { mode: "sentiment" }); },
      w: async () => { const wl = S.settings.watchlist || []; if (!wl.includes(p.sec)) { wl.push(p.sec); S.settings = await api("/api/settings", { method: "PUT", json: { watchlist: wl } }); tape(); flash("已加入自選"); } } };
    $$("[data-act]", body).forEach((b) => b.addEventListener("click", acts[b.dataset.act]));
    const loadHold = async (quarterly) => {
      const box = $(".holdings", body);
      box.innerHTML = `<div class="loading">查詢成分股</div>`;
      try {
        const hd = await api(`/api/tw/etf/holdings?code=${encodeURIComponent(code)}&quarterly=${quarterly}`);
        if (hd.error && !hd.items) { box.innerHTML = `<div class="muted" style="padding:6px 0">${esc(hd.error)}</div>`; return; }
        $("#holdMeta", body).textContent = `${hd.scope} · ${hd.period.slice(0, 4)}-${hd.period.slice(4)} · 合計 ${nf(hd.total_pct)}%`;
        const max = Math.max(...hd.items.map((x) => x.pct || 0), 1);
        box.innerHTML = `<table class="grid"><thead><tr><th>#</th><th style="text-align:left">標的</th><th style="text-align:left">種類</th><th>占淨值</th><th style="text-align:left;width:30%"></th><th>金額</th></tr></thead><tbody>${
          hd.items.map((x) => `<tr class="${/^\d{4,6}$/.test(x.code) ? "click" : ""}" data-code="${esc(x.code)}"><td>${x.rank}</td><td class="nm">${esc(x.name)} <small>${esc(x.code)}</small></td><td class="nm">${esc(x.kind)}</td><td>${nf(x.pct)}%</td>
            <td style="text-align:left"><div class="bar" style="width:${(x.pct || 0) / max * 100}%"></div></td><td>${x.amount ? nf(x.amount / 1e8, 1) + " 億" : "—"}</td></tr>`).join("")}</tbody></table>`;
        $$("tr[data-code]", box).forEach((tr) => /^\d{4,6}$/.test(tr.dataset.code) && tr.addEventListener("click", () => run(`${tr.dataset.code} GP`).catch(() => {})));
      } catch (e) { box.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
    };
    $$("[data-q]", body).forEach((b) => b.addEventListener("click", () => loadHold(b.dataset.q === "1")));
    loadHold(false);
  } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

/* ---------- 基金搜尋 */
async function renderFundSearch(i, body, p) {
  const st = p.fund || (p.fund = { region: "" });
  body.innerHTML = "";
  const inp = h("input", { placeholder: "基金名稱、公司或 ISIN，例如：安聯 台灣科技、貝萊德 世界科技、PIMCO", value: p.q || "" });
  const go = () => { S.panes[i].q = inp.value.trim(); try { localStorage.setItem("ft.fundQ", S.panes[i].q); } catch (e) {} savePanes(); renderPane(i); };
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  body.append(h("div", { class: "wl-add" }, inp, h("button", { class: "btn ghost", onclick: go }, "搜尋")));
  const bar = h("div", { class: "ranges" });
  [["", "全部"], ["境內", "境內基金"], ["境外", "境外基金"], ["期信", "期貨信託"]].forEach(([r, l]) =>
    bar.append(h("button", { class: st.region === r ? "on" : "", onclick: () => { st.region = r; renderPane(i); } }, l)));
  const stat = h("span", { class: "legend" });
  bar.append(stat);
  body.append(bar);
  api("/api/funds/status").then((s) => { stat.textContent = `${nf(s.funds, 0)} 檔 · 淨值 ${s.nav_span && s.nav_span[1] ? s.nav_span[1] : "—"} · 更新 ${(s.refreshed || "—").replace("T", " ")}`; }).catch(() => {});
  if (!p.q) {
    body.append(h("div", { class: "empty" }, "輸入關鍵字搜尋境內、境外、期貨信託基金（資料：投信投顧公會、集保結算所，每日更新）。"));
    setTimeout(() => inp.focus(), 30);
    return;
  }
  try { localStorage.setItem("ft.fundQ", p.q); } catch (e) {}
  const holder = h("div", {}, h("div", { class: "loading" }, "搜尋中"));
  body.append(holder);
  try {
    const rows = await api(`/api/funds/search?q=${encodeURIComponent(p.q)}&region=${encodeURIComponent(st.region)}`);
    $(".pane-name", paneEl(i)).textContent = `「${p.q}」${rows.length} 筆 · 單擊看淨值走勢`;
    holder.innerHTML = "";
    if (!rows.length) { holder.append(h("div", { class: "empty" }, "找不到符合的基金")); return; }
    const tbl = h("table", { class: "grid" });
    tbl.innerHTML = `<thead><tr><th style="text-align:left">基金名稱</th><th>淨值</th><th>日漲跌</th><th>1月</th><th>1年</th><th>日期</th></tr></thead>`;
    const tb = h("tbody");
    for (const f of rows) {
      const sym = "FUND:" + f.id; NAMES[sym] = f.name;
      const tr = h("tr", { class: "click", onclick: () => quickOpen(sym, "FUNDD"), ondblclick: () => setPane(i, { sec: sym, fn: "FUNDD" }) });
      tr.innerHTML = `<td class="nm wrapname">${esc(f.name)}<br><small class="muted">${esc(f.region)} · ${esc(f.company || "")} · ${esc(f.currency || "")}</small></td>
        <td>${nf(f.nav, 4)}</td><td class="${cls(f.pct)}">${pct(f.pct)}</td><td class="${cls(f.r1m)}">${f.r1m != null ? pct(f.r1m) : "…"}</td>
        <td class="${cls(f.r1y)}">${f.r1y != null ? pct(f.r1y) : "…"}</td><td>${esc(f.date || "")}</td>`;
      tb.append(tr);
    }
    tbl.append(tb);
    holder.append(tbl);
    if (rows.some((f) => (f.n_navs || 0) < 60)) {
      holder.append(h("div", { class: "legend", style: "padding:6px 8px" },
        "「…」＝本機還沒有這檔的歷史淨值：已在背景從 Yahoo／Morningstar 補（依 ISIN），約 1 分鐘後按「搜尋」重新整理；點進去看單檔也會立刻補。"));
      if (!p.fundRetry) { S.panes[i].fundRetry = true; setTimeout(() => { if (S.panes[i].fn === "FUNDS" && S.panes[i].q === p.q) renderPane(i); }, 45000); }
    }
  } catch (e) { holder.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

/* ---------- 基金資料 */
async function renderFund(i, body, p) {
  const id = p.sec.replace(/^FUND:/, "");
  body.classList.add("chart");
  body.innerHTML = `<div class="loading">載入基金</div>`;
  try {
    const f = await api(`/api/funds/detail?id=${encodeURIComponent(id)}`);
    if (S.panes[i].sec !== p.sec) return;
    NAMES[p.sec] = f.name;
    $(".pane-sec", paneEl(i)).textContent = "基金";
    $(".pane-name", paneEl(i)).textContent = f.name;
    const r = f.returns || {};
    body.innerHTML = "";
    body.append(h("div", { class: "qhead", html: `<span class="px">${nf(f.nav, 4)}</span><span class="chg ${cls(f.pct)}">${pct(f.pct)}</span>
      <span class="meta">${esc(f.currency || "")} · 淨值日 ${esc(f.date || "—")} · ${esc(f.region)} · ${esc(f.company || "")} · ${esc(f.code || "")} ${esc(f.isin || "")}</span>` }));
    body.append(h("div", { class: "ret-row", html: ["1週", "1月", "3月", "6月", "今年", "1年", "3年"].map((k) =>
      `<div><span>${k}</span><b class="${cls(r[k])}">${r[k] != null ? pct(r[k]) : "—"}</b></div>`).join("") }));
    const box = h("div", { class: "chart-box" });
    body.append(box);
    let lastQ = ""; try { lastQ = localStorage.getItem("ft.fundQ") || ""; } catch (e) {}
    body.append(h("div", { class: "ranges" },
      lastQ ? h("button", { title: "回到基金搜尋結果", onclick: () => setPane(i, { fn: "FUNDS", q: lastQ }) }, `← 「${lastQ}」搜尋結果`) : null,
      h("span", { class: "legend", style: "margin-left:0" }, `資料：${f.source || "—"}；共 ${f.history.length} 筆${f.history.length < 60 ? `（Yahoo／Morningstar 找不到這檔的歷史${f.isin ? `（ISIN ${f.isin}）` : ""}；官方開放資料只有近期淨值，本機每天累積）` : ""}`),
      h("span", { class: "legend fc-legend" }),
      h("button", { class: p.fc ? "on" : "", onclick: () => { S.panes[i].fc = p.fc ? 0 : 30; renderPane(i); } }, "🔮 預測 30 日"),
      h("button", { onclick: () => { setFocus(i); askSend(`請分析這檔基金：${f.name}。依淨值走勢與各期間報酬說明表現、波動與目前位置，並提醒該注意的地方。`); } }, "問 AI 這檔基金 ▸")));
    if (f.history.length === 1) {
      box.innerHTML = `<div class="empty">目前只有 ${esc(f.history[0][0])} 一筆淨值，還畫不出走勢。<br>${f.isin ? `已用 ISIN ${esc(f.isin)} 向 Yahoo／Morningstar 查詢，找不到歷史；` : ""}本機會每天累積官方淨值。</div>`;
    } else if (f.history.length) {
      requestAnimationFrame(() => {
        const chart = LightweightCharts.createChart(box, { ...chartTheme(), autoSize: true });
        const up = (f.history[f.history.length - 1][1] >= f.history[0][1]);
        const s = chart.addAreaSeries({ lineColor: cssVar(up ? "--up" : "--down"), topColor: cssVar(up ? "--up" : "--down") + "40", bottomColor: "#00000000", lineWidth: 2, priceFormat: { type: "price", precision: 4, minMove: 0.0001 } });
        s.setData(f.history.map(([t, v]) => ({ time: t, value: v })));
        chart.timeScale().fitContent();
        S.charts.set(i, { chart, fit: () => chart.timeScale().fitContent() });
        if (p.fc) {
          if (f.history.length < 64) $(".fc-legend", body).textContent = "淨值歷史少於 64 筆，還不能預測";
          else overlayForecast(i, p.sec, p.fc, $(".fc-legend", body));
        }
      });
    } else box.innerHTML = `<div class="empty">尚無淨值歷史</div>`;
  } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

/* ---------- 世界事件（God's Eye View） */
async function renderWorld(i, body, p) {
  body.innerHTML = `<div class="loading">讀取 God's Eye View 世界訊號</div>`;
  let st, lt, evs;
  try {
    [st, lt, evs] = await Promise.all([api("/api/world/status"), api("/api/world/latest"), api("/api/world/events?days=30")]);
  } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (S.panes[i].fn !== "WORLD") return;
  S.panes[i].worldAt = Date.now();
  const g = st.gev || {};
  const pub = lt.public_url || g.public_url;
  body.innerHTML = "";
  const bar = h("div", { class: "ranges" },
    h("span", { class: "legend", style: "margin-left:0" }, g.ok ? `● God's Eye View 已連線（${g.tools} 個工具）` : `● 連不到 God's Eye View：${g.error || ""}`),
    h("span", { class: "legend" }, `快照 ${nf(st.snapshots, 0)} 筆 · ${st.days} 天 · 最近 ${(st.last || "—").slice(0, 16).replace("T", " ")} UTC（成功 ${st.last_ok || "—"}）· 每 ${st.interval_min} 分鐘`),
    h("button", { onclick: async (e) => { e.target.disabled = true; e.target.textContent = "更新中…"; try { await api("/api/world/refresh", { method: "POST" }); } catch (x) {} renderPane(i); } }, "立即更新"),
    pub ? h("button", { onclick: () => window.open(pub, "_blank", "noopener") }, "開啟上帝視角 ↗") : null,
    pub ? h("button", { class: p.globe ? "on" : "", onclick: () => { S.panes[i].globe = !p.globe; renderPane(i); } }, "🌍 視窗內顯示") : null);
  bar.firstChild.style.color = g.ok ? "var(--ok)" : "var(--bad)";
  body.append(bar);
  if (p.globe && pub) {
    body.append(h("iframe", { class: "gev-frame", src: `${pub}/?embed=1`, allow: "fullscreen", loading: "lazy" }));
  }
  const tbl = h("table", { class: "grid world" });
  tbl.innerHTML = `<thead><tr><th style="text-align:left">區域</th><th style="text-align:left">為什麼重要</th><th style="text-align:left">最新訊號</th></tr></thead>`;
  const tb = h("tbody");
  const regions = await api("/api/world/regions").catch(() => []);
  for (const rg of regions) {
    const checks = (lt.latest || {})[rg.id] || {};
    const cells = rg.checks.map((c) => {
      const d = checks[c.id];
      if (!d) return `<div class="wl-line muted">${esc(c.label)}：尚無資料</div>`;
      if (!d.ok) return `<div class="wl-line muted">${esc(c.label)}：無法取得（${esc((d.summary || "").slice(0, 60))}）</div>`;
      const base = d.base != null && d.base_n >= 6 ? d.base : null;
      const hot = base != null && ((d.metric >= Math.max(4, 1.8 * base)) || (base >= 6 && d.metric <= 0.5 * base));
      const big = c.id.startsWith("quake") && d.metric >= 6;
      const link = d.view_url ? ` <a href="${esc(d.view_url)}" target="_blank" rel="noopener" title="在上帝視角看">🌐</a>` : "";
      return `<div class="wl-line ${hot || big ? "warn" : ""}"><b>${esc(c.label)}</b>　${esc(d.summary)}${base != null ? `<small>（平常約 ${nf(base, 0)}）</small>` : ""}${link}</div>`;
    }).join("");
    const tr = h("tr");
    tr.innerHTML = `<td class="nm" style="color:var(--amber)">${esc(rg.name)}</td><td class="nm wrap">${esc(rg.why)}</td><td class="wrap">${cells}</td>`;
    tb.append(tr);
  }
  tbl.append(tb);
  body.append(tbl);
  body.append(h("div", { class: "sec-title", style: "margin:10px 10px 4px" }, "近 30 天值得注意的事件"));
  if (!evs.length) body.append(h("div", { class: "empty" }, "沒有超過門檻的事件（M6+ 地震、氣旋、軍機或航運量明顯偏離平常）。剛裝好時需要先累積約一天的歷史才能判斷「異常」。"));
  for (const e of evs.slice(0, 40)) {
    const [color, label] = EV_STYLE[e.kind] || ["#e6e6e6", "事件"];
    body.append(h("div", { class: "news-item" }, h("span", { class: "t" }, e.date),
      h("span", { class: "h" }, h("b", { style: `color:${color}` }, `[${label}] `), e.title),
      e.detail ? h("span", { class: "sum" }, e.detail) : null, h("span", { class: "src" }, e.region_name)));
  }
  body.append(h("div", { class: "hint", style: "padding:8px 10px" },
    "資料：God's Eye View（USGS、adsb.lol、AISStream、NOAA NHC、WFIGS、Launch Library 2）。這些是感測資料，不是新聞；TradingAgents 與 TimesFM 分析時會自動帶入相關區域。西太平洋颱風不在 NHC 資料內；船舶需要 AISStream 金鑰。"));
}

function renderHelp(i, body) {
  body.innerHTML = `<div class="help">
  <h4>指令（直接打字進入指令列，Enter 或 GO 執行；中文名稱、代號都可以）</h4>
  <table>
    <tr><td>總經（MACRO）· 總經 DGS10</td><td>美國總經（FRED 官方）：Fed 利率、美債殖利率與利差、CPI／PCE、失業率、非農、GDP、信心、衰退機率、信用利差、VIX、美元、M2、油價；點一列看長期走勢（可加上 Fed 決策標記）</td></tr>
    <tr><td>紀錄（LEDGER）· 紀錄 2330</td><td>分析紀錄與檢討：每次 AI 分析的時間、Agent／模型、標的、結論；5／20／60 個交易日後自動用實際股價核對對錯與超額報酬；各 Agent 命中率；「🤖 AI 檢討」整理成給該 Agent 的提醒，下次分析自動帶入（可關）</td></tr>
    <tr><td>大事（EVENTS）</td><td>重大事件：近一年指數大漲大跌（自動偵測）、Fed 利率決策日、你自己加的事件，附當時新聞與 AI 摘要；指數走勢圖預設顯示「📌 大事」標記（滑過看說明、點一下跳到這頁）</td></tr>
    <tr><td>焦點（FOCUS）</td><td>今日焦點：主要指數、供應鏈題材漲跌排行、強弱勢股、三大法人、自動標上題材的新聞、我的持股今日損益、AI 今日重點</td></tr>
    <tr><td>供應鏈 · 供應鏈 CoWoS · 供應鏈 奇鋐（SC）</td><td>供應鏈地圖：AI 資料中心／半導體／記憶體／高速網路／電力散熱，上游 → 下游，色塊是成分股今日平均漲跌；點環節看公司與上下游。個股視窗的「⛓ 供應鏈」直接跳到它所在的環節</td></tr>
    <tr><td>持股（HOLD）或右上 👤</td><td>我的持股：要登入，每個帳號只看得到自己的；損益、今日損益、權重、供應鏈曝險、匯出 CSV</td></tr>
    <tr><td>台積電 · 2330 · 2330 走勢 · NVDA</td><td>價格走勢（K 線＋月線、季線）。台股可打中文名稱或代號</td></tr>
    <tr><td>&lt;標的&gt; 盤中 · 概況 · 新聞 · 財報</td><td>盤中走勢／公司概況（台股含證交所公司資料）／中文新聞／財報（中文科目）</td></tr>
    <tr><td>ETF</td><td>台股 ETF 總覽：預估淨值、折溢價、規模、淨申購／買回，可依類別與排序篩選</td></tr>
    <tr><td>0056 · 00878 · 00679B</td><td>ETF 走勢；「概況」會顯示 ETF 資料（淨值、配息、成分股）</td></tr>
    <tr><td>基金 安聯 台灣科技</td><td>搜尋境內／境外／期信基金，點選看淨值走勢與報酬</td></tr>
    <tr><td>台股 · 熱門ETF · 全球 · 美股 · 外匯 · 商品 · 利率 · 加密</td><td>市場監控表（英文代碼 TW、TWETF、WEI、MAG7、FX、CMDTY、RATES、CRYPTO 也可以）</td></tr>
    <tr><td>自選</td><td>自選清單（可新增／刪除，同步跑馬燈）</td></tr>
    <tr><td>判斷 2330（或視窗的 ⚖ 判斷）</td><td>Laya 決策模型：買進／持有／觀望／賣出／放空 各自的百分比、多空淨值、收高機率、大跌風險、未來區間分布（一週／一個月／一季）</td></tr>
    <tr><td>策略研究（LAB）、選股 營收連續成長…</td><td>FinLab 式研究：用中文描述想法讓 AI 建策略，或用內建策略／自己寫條件（例 營收年增率 &gt; 20、new_high(ma(月營收, 3), 12)），回測年化報酬、夏普、最大回撤、每年／每月報酬、今天的選股名單</td></tr>
    <tr><td>資料庫（DATA）</td><td>官方資料目錄：股價、本益比殖利率、三大法人、融資融券、月營收、季財報；涵蓋期間、背景下載進度、欄位瀏覽</td></tr>
    <tr><td>世界（WORLD）</td><td>God's Eye View 現實世界訊號：台海／南海軍機、荷莫茲／紅海／麻六甲／巴拿馬船舶、地震、氣旋；走勢圖的 🌐 事件 會把事件標在 K 線上</td></tr>
    <tr><td>2 鴻海 概況</td><td>開頭 1–4 指定視窗；Ctrl+1..4 切換視窗</td></tr>
  </table>
  <h4>AI（右側欄，全部走本機 Ollama）</h4>
  <table>
    <tr><td>分析 台積電（TA）</td><td>TradingAgents 多代理人交易公司</td></tr>
    <tr><td>大師 AAPL 2330（HF）</td><td>ai-hedge-fund 投資大師（巴菲特、蒙格…）</td></tr>
    <tr><td>量化（QLIB）</td><td>Qlib 選股與回測：台灣 50、熱門 ETF 輪動、美股 50</td></tr>
    <tr><td>強化學習 2330 2317（RL）</td><td>FinRL 強化學習交易</td></tr>
    <tr><td>預測 NVDA（FG）</td><td>FinGPT 下週預測／新聞情緒</td></tr>
    <tr><td>走勢圖的 🔮 預測（或 趨勢 2330）</td><td>TimesFM 時間序列預測：圖上畫中位數與 80% 區間；側欄分頁有回測與解讀</td></tr>
    <tr><td>問 台積電毛利率怎麼看？（ASK）</td><td>帶著目前視窗的標的（股票、ETF、基金）資料問 AI</td></tr>
    <tr><td>設定（SET）</td><td>Ollama、模型、漲跌顏色</td></tr>
    <tr><td>Ctrl+B</td><td>開關 AI 側欄</td></tr>
  </table>
  <h4>資料來源</h4>
  <p style="color:var(--muted)">台股即時報價：證交所 MIS；ETF 淨值／折溢價：證交所 ETF 淨值揭露；ETF 成分股：投信投顧公會；基金淨值：投信投顧公會、集保結算所；
  公司資料：證交所／櫃買中心 OpenAPI；中文新聞：Google 新聞；其他市場、K 線、財報：Yahoo Finance（延遲報價）。AI 輸出僅供研究，不構成投資建議。</p>
  </div>`;
}

/* ------------------------------------------------------------ live refresh / tape */
function refreshLive() {
  if (document.hidden) return;
  S.panes.forEach((p, i) => {
    if (p.fn === "W" || MON_FNS.includes(p.fn) || p.fn === "ETFM") renderPane(i);
    else if (p.fn === "HOLD" && S.user && !p.editing) refreshHold(i);
    else if (p.fn === "WORLD" && !p.globe && Date.now() - (p.worldAt || 0) > 300000) renderPane(i);
    else if (PANE_FNS.includes(p.fn)) loadName(i, p.sec);
  });
}

async function tape() {
  const wl = (S.settings && S.settings.watchlist) || [];
  if (!wl.length) return;
  try {
    const rows = await api(`/api/quotes?s=${encodeURIComponent(wl.join(","))}`);
    const track = h("div", { class: "tape-track" });
    for (const r of rows) {
      if (r.last == null) continue;
      if (r.name) NAMES[r.symbol] = r.name;
      const label = isTW(r.symbol) && r.name ? r.name : (r.name && r.symbol.startsWith("^") ? r.name : r.symbol);
      track.append(h("span", {}, h("b", { onclick: () => run(`${r.symbol} GP`) }, label),
        nf(r.last, pxd(r.last)), " ", h("span", { class: cls(r.pct) }, pct(r.pct))));
    }
    const inner = $("#tapeInner");
    const old = $(".tape-track", inner);
    if (old) track.style.animationDelay = getComputedStyle(old).animationDelay;
    inner.innerHTML = ""; inner.append(track);
  } catch (e) {}
}

/* ------------------------------------------------------------ side panel */
function toggleSide(force) {
  const m = $("#main");
  const closed = force === undefined ? !m.classList.contains("side-closed") : !force;
  m.classList.toggle("side-closed", closed);
  $("#toggleSide").textContent = closed ? "AI ◂" : "AI ▸";
  try { localStorage.setItem("ft.sideClosed", closed ? "1" : "0"); } catch (e) {}
  setTimeout(() => S.charts.forEach((c) => c.fit && c.fit()), 50);
}
$("#toggleSide").addEventListener("click", () => toggleSide());
$("#userBtn").addEventListener("click", () => setPane(S.focus, { fn: "HOLD" }));

function openSide(tab) {
  toggleSide(true);
  if (tab && tab !== S.sideTab) { S.sideTab = tab; try { localStorage.setItem("ft.sideTab", tab); } catch (e) {} renderSide(); }
}

function setupResizer() {
  const r = $("#resizer"); let x0, w0;
  r.addEventListener("mousedown", (e) => {
    x0 = e.clientX; w0 = $("#side").getBoundingClientRect().width; r.classList.add("drag");
    const move = (ev) => { const w = Math.max(320, Math.min(window.innerWidth * 0.7, w0 + (x0 - ev.clientX))); document.documentElement.style.setProperty("--side-w", w + "px"); };
    const up = () => { r.classList.remove("drag"); document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
      try { localStorage.setItem("ft.sideW", getComputedStyle(document.documentElement).getPropertyValue("--side-w")); } catch (e) {}
      S.charts.forEach((c) => c.fit && c.fit()); };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  });
}

const SHORT = { research: "策略回測", tradingagents: "TradingAgents", hedgefund: "大師基金", qlib: "Qlib", finrl: "FinRL", fingpt: "FinGPT", timesfm: "TimesFM", laya: "Laya 判斷" };

async function loadEngines() {
  try { S.engines = await api("/api/engines"); } catch (e) { S.engines = []; }
  updateStatus();
}

function updateStatus() {
  const s = S.settings || {};
  const on = S.engines.filter((e) => e.online).length;
  $("#status").innerHTML = `Ollama <b>${esc(s.model || "?")}</b> · 引擎 ${on}/${S.engines.length}`;
}

function renderTabs() {
  const tabs = $("#sideTabs"); tabs.innerHTML = "";
  const busy = (n) => (S.jobs[n] || []).some((j) => j.status === "running" || j.status === "queued");
  for (const e of S.engines) {
    tabs.append(h("button", { class: S.sideTab === e.name ? "on" : "", onclick: () => openSide(e.name) },
      h("span", { class: "dot " + (busy(e.name) ? "busy" : e.online ? "on" : "off") }), SHORT[e.name] || e.name));
  }
  tabs.append(h("button", { class: S.sideTab === "dca" ? "on" : "", onclick: () => openSide("dca") }, "📈 定期定額"));
  tabs.append(h("button", { class: S.sideTab === "ask" ? "on" : "", onclick: () => openSide("ask") }, "💬 ASK"));
  tabs.append(h("button", { class: S.sideTab === "settings" ? "on" : "", onclick: () => openSide("settings") }, "⚙ 設定"));
  tabs.append(h("button", { class: "side-close", title: "關閉側欄", onclick: () => toggleSide(false) }, "✕"));
}

function renderSide() {
  renderTabs();
  const body = $("#sideBody");
  if (S.es) { S.es.close(); S.es = null; }
  body.innerHTML = "";
  if (S.sideTab === "ask") return renderAsk(body);
  if (S.sideTab === "settings") return renderSettings(body);
  if (S.sideTab === "dca") return renderDCA(body);
  const eng = S.engines.find((e) => e.name === S.sideTab) || S.engines[0];
  if (!eng) { body.innerHTML = `<div class="empty">沒有引擎</div>`; return; }
  S.sideTab = eng.name;
  renderEngine(body, eng);
}

function engineModel(name) {
  const s = S.settings || {}; const m = s.models || {};
  if (name === "laya") return "ONNX Runtime · CPU（不用 Ollama）";
  if (name === "tradingagents") return `deep <b>${esc(m.tradingagents_deep || s.model)}</b> · quick <b>${esc(m.tradingagents_quick || s.model)}</b>`;
  return `model <b>${esc(m[name] || s.model)}</b>`;
}

function fieldDefault(f) {
  if (f.default === "today") return new Date().toLocaleDateString("sv-SE");
  if (f.type === "ticker" || f.type === "tickers") {
    if (f.default) return f.default;
    return f.required ? S.panes[S.focus].sec || "" : "";
  }
  if (f.name === "language") return (S.settings || {}).language || f.default;
  return f.default;
}

function renderEngine(body, eng) {
  body.append(h("div", { class: "eng-head" },
    h("h2", {}, h("span", { class: "dot " + (eng.online ? "on" : "off") }), eng.title || eng.name,
      eng.repo ? h("a", { href: eng.repo, target: "_blank", rel: "noopener" }, "GitHub ↗") : null),
    h("p", {}, eng.online ? eng.description : `引擎離線：${eng.error || ""}（systemctl --user status ft-${eng.name}）`),
    h("div", { class: "model", html: engineModel(eng.name) + (eng.version ? ` · ${esc(eng.version.slice(0, 7))}` : "") })));
  if (!eng.online) return;

  const form = h("form", { class: "form", id: "engForm" });
  const pre = (S.prefill && S.prefill.engine === eng.name) ? S.prefill : null;
  for (const f of eng.schema || []) {
    let v = pre && pre.values[f.name] !== undefined ? pre.values[f.name] : fieldDefault(f);
    const wrap = h("div", { class: "f" + (["multi", "textarea", "tickers"].includes(f.type) ? " wide" : "") });
    let input;
    if (f.type === "select") {
      input = h("select", { name: f.name }, ...(f.options || []).map((o) => h("option", { value: o.value, selected: o.value === v }, o.label)));
    } else if (f.type === "multi") {
      const vv = Array.isArray(v) ? v : [];
      input = h("div", { class: "chips", "data-name": f.name }, ...(f.options || []).map((o) =>
        h("label", {}, h("input", { type: "checkbox", value: o.value, checked: vv.includes(o.value) }), o.label)));
    } else if (f.type === "bool") {
      wrap.append(h("label", { class: "bool" }, h("input", { type: "checkbox", name: f.name, checked: !!v }), f.label));
      form.append(wrap); continue;
    } else if (f.type === "textarea") {
      input = h("textarea", { name: f.name, placeholder: f.placeholder || "" }); input.value = v || "";
    } else {
      input = h("input", { name: f.name, type: f.type === "int" ? "number" : f.type === "date" ? "date" : "text",
        class: f.type === "ticker" || f.type === "tickers" ? "tick" : "", placeholder: f.placeholder || "",
        min: f.min, max: f.max, value: v ?? "" });
    }
    wrap.append(h("label", {}, f.label + (f.required ? " *" : "")), input);
    form.append(wrap);
  }
  S.prefill = null;
  const runBtn = h("button", { class: "btn", type: "submit" }, "▶ 執行");
  form.append(h("div", { class: "run-row" }, runBtn, h("span", { class: "hint" }, "一次跑一個工作，其他會排隊；本機 27B 模型可能需數分鐘")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const params = {};
    for (const f of eng.schema || []) {
      if (f.type === "multi") params[f.name] = $$(`[data-name="${f.name}"] input:checked`, form).map((x) => x.value);
      else if (f.type === "bool") params[f.name] = form.elements[f.name].checked;
      else if (f.type === "int") params[f.name] = form.elements[f.name].value === "" ? null : +form.elements[f.name].value;
      else params[f.name] = form.elements[f.name].value;
    }
    runBtn.disabled = true;
    try {
      const j = await api(`/api/engines/${eng.name}/jobs`, { method: "POST", json: { params } });
      S.selJob[eng.name] = j.id;
      await refreshJobs(eng.name);
      showJob(eng.name, j.id);
    } catch (err) { alertBox(err.message); }
    runBtn.disabled = false;
  });
  body.append(form);
  body.append(h("div", { class: "jobs", id: "jobList" }));
  body.append(h("div", { class: "jobview", id: "jobView" }));
  refreshJobs(eng.name).then(() => {
    const id = S.selJob[eng.name] || ((S.jobs[eng.name] || [])[0] || {}).id;
    if (id) showJob(eng.name, id);
  });
}

function alertBox(msg) {
  const v = $("#jobView"); if (v) v.prepend(h("div", { class: "errbox" }, msg));
}

async function prefillEngine(name, text, extra = {}) {
  const eng = S.engines.find((e) => e.name === name);
  const values = { ...extra };
  if (eng && text) {
    const f = (eng.schema || []).find((f) => f.type === "ticker" || f.type === "tickers");
    if (f) {
      const parts = /\.TWO?$|^[A-Z0-9^=.\-]+$/i.test(text.trim()) && !/[\s,，、]/.test(text.trim()) ? [text.trim()] : text.split(/[\s,，、]+/).filter(Boolean);
      const syms = (await Promise.all(parts.map((t) => resolveSec(t)))).filter((x) => x && (name === "timesfm" || !isFund(x)));
      values[f.name] = f.type === "tickers" ? syms.join(", ") : (syms[0] || "");
    }
  }
  S.prefill = { engine: name, values };
  S.sideTab = name;
  renderSide();
}

async function refreshJobs(name) {
  try { S.jobs[name] = await api(`/api/engines/${name}/jobs`); } catch (e) { return; }
  const list = $("#jobList"); if (!list || S.sideTab !== name) return;
  list.innerHTML = "";
  const jobs = S.jobs[name];
  if (!jobs.length) { list.append(h("div", { class: "empty" }, "尚無紀錄")); return; }
  for (const j of jobs) {
    list.append(h("div", { class: "job" + (S.selJob[name] === j.id ? " sel" : ""), onclick: () => showJob(name, j.id) },
      h("span", { class: "st " + j.status }, { done: "完成", running: `${Math.round(j.progress)}%`, queued: "排隊", error: "錯誤", cancelled: "取消" }[j.status] || j.status),
      h("span", { class: "tt" }, j.title || j.id),
      h("span", { class: "tm" }, (j.created || "").slice(5, 16).replace("T", " ")),
      h("span", { class: "sm" }, j.summary || j.error || j.stage || "")));
  }
}

async function pollJobs() {
  if (document.hidden) return;
  const name = S.sideTab;
  if (S.engines.find((e) => e.name === name)) await refreshJobs(name);
  for (const e of S.engines)
    if (e.online && e.name !== name && (S.jobs[e.name] || []).some((j) => j.status === "running" || j.status === "queued")) await refreshJobs(e.name);
  renderTabs();
}

function sigClass(s) {
  s = String(s || "").toLowerCase();
  if (/buy|overweight|bull|看多|跑贏|up|買進/.test(s)) return "bull";
  if (/sell|underweight|bear|看空|落後|down|賣出|放空/.test(s)) return "bear";
  return "neu";
}

async function showJob(name, id) {
  S.selJob[name] = id;
  $$(".job").forEach((el) => el.classList.remove("sel"));
  refreshJobs(name);
  const view = $("#jobView"); if (!view) return;
  if (S.es) { S.es.close(); S.es = null; }
  view.innerHTML = `<div class="loading">載入</div>`;
  let j;
  try { j = await api(`/api/engines/${name}/jobs/${id}`); } catch (e) { view.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  view.innerHTML = "";
  const stage = h("div", { class: "stage" }, h("span", { class: "grow" }), null);
  const bar = h("div", { class: "progress" }, h("div", { style: `width:${j.progress}%` }));
  const logPre = h("pre");
  const log = h("details", { class: "log", open: j.status === "running" || j.status === "queued" || j.status === "error" }, h("summary", {}, `執行紀錄（${j.log.length} 行）`), logPre);
  const res = h("div", { class: "result" });
  const actions = h("div", { style: "display:flex;gap:6px;margin-top:4px" });
  const pipeBox = h("div", { class: "pipe-wrap" });
  view.append(stage, bar, pipeBox, actions, log, res);

  const setStatus = (b) => {
    if (b.pipeline) renderPipe(pipeBox, b.pipeline, b.status);
    stage.firstChild.innerHTML = `<span class="st ${b.status}">${esc(b.status.toUpperCase())}</span> ${esc(b.stage || "")} ${b.error ? `<span class="down">${esc(b.error)}</span>` : ""}`;
    bar.firstChild.style.width = (b.status === "done" ? 100 : b.progress || 0) + "%";
    actions.innerHTML = "";
    if (b.status === "running" || b.status === "queued")
      actions.append(h("button", { class: "btn danger", onclick: async () => { await api(`/api/engines/${name}/jobs/${id}`, { method: "DELETE" }); } }, "■ 取消"));
    else {
      actions.append(h("button", { class: "btn ghost", onclick: () => { prefillEngine(name, ""); S.prefill = { engine: name, values: j.params }; renderSide(); } }, "↻ 用相同參數"));
      if (b.status === "done") {
        const note = ((j.result || {}).meta || {}).note;
        actions.append(h("button", { class: "btn ghost", title: note ? `已排入 Obsidian：${note}` : "寫一篇筆記到 Obsidian vault 的 finance/",
          onclick: async (ev) => { ev.target.disabled = true; try { const r = await api(`/api/engines/${name}/jobs/${id}/note`, { method: "POST" }); ev.target.textContent = "📝 已存 ✓"; ev.target.title = r.note; } catch (e) { ev.target.textContent = "存檔失敗"; } } },
          note ? "📝 已存 Obsidian ✓" : "📝 存到 Obsidian"));
      }
      actions.append(h("button", { class: "btn ghost", onclick: async () => { if (!confirmDel(id)) return; await api(`/api/engines/${name}/jobs/${id}`, { method: "DELETE" }); S.selJob[name] = null; renderSide(); } }, "刪除"));
    }
  };
  const appendLog = (lines) => {
    const atBottom = logPre.scrollTop + logPre.clientHeight >= logPre.scrollHeight - 30;
    logPre.textContent += lines.join("\n") + "\n";
    if (logPre.textContent.length > 400000) logPre.textContent = logPre.textContent.slice(-300000);
    log.firstChild.textContent = `執行紀錄（${logPre.textContent.split("\n").length - 1} 行）`;
    if (atBottom) logPre.scrollTop = logPre.scrollHeight;
  };
  setStatus(j);
  appendLog(j.log);
  if (j.result) renderResult(res, j.result);

  if (j.status === "running" || j.status === "queued") {
    logPre.textContent = "";
    const es = new EventSource(`/api/engines/${name}/jobs/${id}/events`);
    S.es = es;
    es.onmessage = async (ev) => {
      const d = JSON.parse(ev.data);
      if (d.type === "log") appendLog(d.lines);
      else if (d.type === "status") setStatus(d);
      else if (d.type === "end") {
        es.close(); S.es = null;
        const full = await api(`/api/engines/${name}/jobs/${id}`);
        j = full; setStatus(full);
        if (full.result) { res.innerHTML = ""; renderResult(res, full.result); log.open = false; }
        refreshJobs(name);
      }
    };
    es.onerror = () => { /* browser retries automatically */ };
  }
}

/* ---------- 多代理人流程儀表板（誰在跑、跑多久、是模型還是工具） */
const fmtDur = (s) => (s < 60 ? `${Math.floor(s)} 秒` : s < 3600 ? `${Math.floor(s / 60)} 分 ${Math.floor(s % 60)} 秒` : `${Math.floor(s / 3600)} 時 ${Math.floor((s % 3600) / 60)} 分`);
const fmtTok = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n || 0));
function renderPipe(box, pipe, status) {
  if (S.pipeTimer) { clearInterval(S.pipeTimer); S.pipeTimer = null; }
  box.innerHTML = "";
  const live = status === "running" || status === "queued";
  const row = h("div", { class: "pipe" });
  pipe.forEach((st, k) => {
    const ns = st.nodes || [];
    const sst = ns.some((n) => n.status === "error") ? "error" : ns.some((n) => n.status === "running") ? "running"
      : ns.length && ns.every((n) => ["done", "cut"].includes(n.status)) ? "done" : "pending";
    const nodesBox = h("div", { class: "pipe-nodes" });
    const col = h("div", { class: `pipe-stage ${sst}` }, h("div", { class: "pipe-title" }, h("span", { class: "pipe-no" }, k + 1), st.stage), nodesBox);
    for (const n of ns) {
      const icon = { pending: "○", running: "◉", done: "✓", cut: "✂", error: "✗" }[n.status] || "○";
      const sub = h("div", { class: "pipe-sub" });
      if (n.status === "running") {
        const gen = n.inflight > 0;
        sub.append(h("span", { class: "pipe-what " + (gen ? "gen" : "tool") }, gen ? "LLM 生成中" : "工具／資料抓取中"),
          h("span", { class: "pipe-live", "data-since": gen ? n.call_started : (n.waiting_since || n.started) }, ""),
          h("span", { class: "pipe-tot", "data-since": n.started, "data-prefix": "共 " }, ""));
      } else if (n.started) {
        const dur = (n.ended || Date.now() / 1000) - n.started;
        sub.append(h("span", {}, fmtDur(dur)));
      }
      if (n.calls) sub.append(h("span", { class: "muted" }, `${n.calls} 次・${fmtTok(n.out)} tok`));
      if (n.cut) sub.append(h("span", { class: "pipe-cutnote", title: "達到輸出上限被截斷（內容保留）" }, "達上限"));
      if (n.note) sub.append(h("span", { class: "down" }, n.note));
      nodesBox.append(h("div", { class: `pipe-node ${n.status}`, title: n.label }, h("span", { class: "pipe-icon" }, icon), h("span", { class: "pipe-label" }, n.label), sub));
    }
    row.append(col);
    if (k < pipe.length - 1) row.append(h("div", { class: "pipe-arrow" }, "→"));
  });
  box.append(row);
  const tick = () => {
    const now = Date.now() / 1000;
    $$(".pipe-live, .pipe-tot", box).forEach((el) => {
      const since = +el.dataset.since; if (!since) return;
      const sec = now - since;
      el.textContent = (el.dataset.prefix || "") + fmtDur(sec);
      if (el.classList.contains("pipe-live")) el.classList.toggle("slow", sec > 300), el.classList.toggle("stuck", sec > 600);
    });
  };
  tick();
  if (live) S.pipeTimer = setInterval(() => { if (!document.body.contains(box)) { clearInterval(S.pipeTimer); S.pipeTimer = null; return; } tick(); }, 1000);
}

function confirmDel(id) { return window.confirm ? window.confirm(`刪除紀錄 ${id}？`) : true; }

function renderResult(el, r) {
  if (r.decision) el.append(decisionCard(r.decision, false));
  if (r.signal) el.append(h("div", { style: "margin:6px 0" }, "結論", h("span", { class: "signal " + sigClass(r.signal) }, r.signal)));
  if (r.markdown) el.append(h("div", { class: "md", html: md(r.markdown) }));
  for (const c of r.charts || []) {
    const box = h("div", { class: "res-chart" });
    el.append(h("div", { class: "res-block" }, h("h4", {}, c.title), box));
    requestAnimationFrame(() => {
      const chart = LightweightCharts.createChart(box, { ...chartTheme(), autoSize: true });
      const colors = ["#ffa028", "#4ba3ff", "#2ad46f", "#ff4d4d", "#3ad6d6"];
      (c.series || []).forEach((s, k) => {
        const style = { dashed: 2, dotted: 1 }[s.style] || 0;
        const ls = chart.addLineSeries({ color: s.color || colors[k % colors.length], lineWidth: s.style === "dotted" ? 1 : 2, lineStyle: style,
          title: s.name, priceLineVisible: false, lastValueVisible: s.style !== "dotted" });
        ls.setData(s.data.filter((p) => p[1] != null && isFinite(p[1])).map((p) => ({ time: String(p[0]).slice(0, 10), value: p[1] })));
      });
      chart.timeScale().fitContent();
    });
  }
  for (const t of r.tables || []) {
    const tbl = h("table", { class: "grid" });
    tbl.innerHTML = `<thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${
      t.rows.map((row) => `<tr>${row.map((v, k) => {
        const s = String(v ?? "");
        const long = s.length > 40;
        const color = /看多|正面|^\+/.test(s) ? "up" : /看空|負面|^-\d/.test(s) ? "down" : "";
        return `<td class="${long ? "wrap" : ""} ${k === 0 ? "sym" : ""} ${color}">${esc(s)}</td>`;
      }).join("")}</tr>`).join("")}</tbody>`;
    el.append(h("div", { class: "res-block" }, h("h4", {}, t.title), h("div", { class: "res-table" }, tbl)));
  }
}

/* ---------- ASK */
function renderAsk(body) {
  const sec = S.panes[S.focus].sec;
  const logEl = h("div", { class: "ask-log", id: "askLog" });
  const ta = h("textarea", { id: "askIn", placeholder: "問任何市場問題，Enter 送出、Shift+Enter 換行。會附上目前視窗標的（股票、ETF、基金）的資料與新聞。" });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); const v = ta.value.trim(); if (v) { ta.value = ""; askSend(v); } } });
  body.append(h("div", { class: "ask" },
    h("div", { class: "ask-ctx" }, "情境標的", h("b", { id: "askCtx" }, sec ? showName(sec) : "（無）"), h("span", { style: "margin-left:auto" }),
      h("button", { class: "btn ghost", onclick: () => { S.ask = []; renderSide(); } }, "清除")),
    logEl, h("div", { class: "ask-in" }, ta, h("button", { class: "btn", onclick: () => { const v = ta.value.trim(); if (v) { ta.value = ""; askSend(v); } } }, "送出"))));
  for (const m of S.ask) logEl.append(msgEl(m));
  logEl.scrollTop = logEl.scrollHeight;
}

function msgEl(m) {
  const b = h("div", { class: "body " + (m.role === "assistant" ? "md" : "") });
  if (m.role === "assistant") b.innerHTML = md(m.content); else b.textContent = m.content;
  return h("div", { class: "msg " + m.role }, h("div", { class: "who" }, m.role === "user" ? "YOU" : "OLLAMA"), b);
}

async function askSend(text) {
  if (S.sideTab !== "ask") openSide("ask");
  S.ask.push({ role: "user", content: text });
  const reply = { role: "assistant", content: "" };
  S.ask.push(reply);
  renderSide();
  const logEl = $("#askLog"); const node = logEl.lastChild; const bodyEl = $(".body", node);
  bodyEl.innerHTML = `<span class="loading"></span>`;
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ messages: S.ask.slice(0, -1), security: S.panes[S.focus].sec || null }) });
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, k); buf = buf.slice(k + 2);
        if (!line.startsWith("data:")) continue;
        const d = JSON.parse(line.slice(5));
        if (d.t) { reply.content += d.t; bodyEl.innerHTML = md(reply.content); logEl.scrollTop = logEl.scrollHeight; }
        if (d.error) { reply.content += `\n\n**錯誤**：${d.error}`; bodyEl.innerHTML = md(reply.content); }
      }
    }
  } catch (e) { reply.content += `\n\n**錯誤**：${e.message}`; bodyEl.innerHTML = md(reply.content); }
}

/* ---------- settings */
async function renderSettings(body) {
  const s = S.settings;
  const wrap = h("div", { class: "set" });
  body.append(wrap);
  wrap.innerHTML = `<div class="loading">讀取 Ollama 模型清單</div>`;
  const info = await api("/api/ollama/models");
  const names = info.models.map((m) => m.name);
  const canon = (v) => (v && !names.includes(v) && names.includes(v + ":latest") ? v + ":latest" : v);
  const sel = (name, val, allowEmpty) => {
    val = canon(val);
    const opts = (allowEmpty ? [["", `（沿用預設：${s.model}）`]] : []).concat(names.map((n) => [n, n]));
    if (val && !names.includes(val)) opts.push([val, `${val}（未安裝？）`]);
    return `<select name="${name}">${opts.map(([v, l]) => `<option value="${esc(v)}" ${v === val ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  };
  const m = s.models || {};
  wrap.innerHTML = `
    <h3>Ollama 連線</h3>
    <div class="row"><label>Ollama URL</label><input name="ollama_url" value="${esc(s.ollama_url)}"></div>
    <div class="row"><label>狀態</label><span class="${info.ok ? "ok" : "bad"}">${info.ok ? `● 已連線 · ${names.length} 個模型` : "● 無法連線：" + esc(info.error || "")}</span></div>
    <div class="row"><label>預設模型</label>${sel("model", s.model, false)}</div>
    <div class="row"><label>num_ctx（空=模型預設）</label><input name="num_ctx" type="number" placeholder="例如 65536" value="${s.num_ctx ?? ""}"></div>
    <div class="row"><label>temperature</label><input name="temperature" type="number" step="0.05" min="0" max="2" value="${s.temperature ?? 0.2}"></div>
    <div class="row"><label>thinking（Qwen3）</label><select name="think"><option value="false" ${!s.think ? "selected" : ""}>關閉（較快）</option><option value="true" ${s.think ? "selected" : ""}>開啟</option></select></div>
    <div class="row"><label>AI 結果存到 Obsidian</label><select name="obsidian_auto"><option value="true" ${s.obsidian_auto !== false ? "selected" : ""}>自動（每次分析完成）</option><option value="false" ${s.obsidian_auto === false ? "selected" : ""}>只在按「📝 存到 Obsidian」時</option></select></div>
    <div class="row"><label>漲跌顏色</label><select name="color_mode"><option value="tw" ${(s.color_mode || "tw") === "tw" ? "selected" : ""}>台灣：紅漲綠跌</option><option value="intl" ${s.color_mode === "intl" ? "selected" : ""}>國際：綠漲紅跌</option></select></div>
    <div class="row"><label>輸出語言</label><select name="language"><option value="Traditional Chinese" ${s.language === "Traditional Chinese" ? "selected" : ""}>繁體中文</option><option value="English" ${s.language === "English" ? "selected" : ""}>English</option></select></div>
    <h3>各引擎模型（覆寫預設）</h3>
    <div class="row"><label>TradingAgents deep</label>${sel("m_tradingagents_deep", m.tradingagents_deep, true)}</div>
    <div class="row"><label>TradingAgents quick</label>${sel("m_tradingagents_quick", m.tradingagents_quick, true)}</div>
    <div class="row"><label>AI Hedge Fund</label>${sel("m_hedgefund", m.hedgefund, true)}</div>
    <div class="row"><label>FinGPT</label>${sel("m_fingpt", m.fingpt, true)}</div>
    <div class="row"><label>Qlib 解讀</label>${sel("m_qlib", m.qlib, true)}</div>
    <div class="row"><label>FinRL 解讀</label>${sel("m_finrl", m.finrl, true)}</div>
    <div class="row"><label>TimesFM 解讀</label>${sel("m_timesfm", m.timesfm, true)}</div>
    <div class="row"><label>策略研究 AI</label>${sel("m_research", m.research, true)}</div>
    <div class="row"><label>TimesFM 模型</label><select name="timesfm_model"><option value="2.5" ${(s.timesfm_model || "2.5") === "2.5" ? "selected" : ""}>TimesFM 2.5（200M，Apache-2.0）</option><option value="3.0" ${s.timesfm_model === "3.0" ? "selected" : ""}>TimesFM 3.0（330M，權重限非商業用途）</option></select></div>
    <div class="row"><label>ASK 對話</label>${sel("m_ask", m.ask, true)}</div>
    <div class="run-row" style="margin-top:10px;display:flex;gap:8px;align-items:center"><button class="btn" id="saveSet">儲存</button><span class="hint" id="setMsg"></span></div>
    <p class="hint" style="margin-top:10px">TradingAgents 需要支援 tool calling 且 context ≥ 32K 的模型（例如 qwen3:32b，或其他 ≥ 32K context 的模型）。一張 24 GB 顯卡通常只放得下一個 27–32B 模型，引擎之間用不同模型會互相換載，第一次會變慢。</p>
    <h3>已安裝模型</h3>
    <div class="mlist">${info.models.map((x) => `<div><b>${esc(x.name)}</b><span>${esc(x.params || "")} ${esc(x.quant || "")} · ${big(x.size)}</span></div>`).join("") || "—"}</div>`;
  $("#saveSet", wrap).addEventListener("click", async () => {
    const g = (n) => $(`[name="${n}"]`, wrap).value;
    const models = {};
    for (const k of ["tradingagents_deep", "tradingagents_quick", "hedgefund", "fingpt", "qlib", "finrl", "timesfm", "research", "ask"]) models[k] = g("m_" + k);
    try {
      S.settings = await api("/api/settings", { method: "PUT", json: {
        ollama_url: g("ollama_url").trim(), model: g("model"), num_ctx: g("num_ctx") ? +g("num_ctx") : null,
        temperature: +g("temperature"), think: g("think") === "true", language: g("language"), color_mode: g("color_mode"), timesfm_model: g("timesfm_model"), obsidian_auto: g("obsidian_auto") === "true", models } });
      applyColors(); S.panes.forEach((_, i) => renderPane(i)); tape();
      $("#setMsg", wrap).textContent = "已儲存 ✓";
      updateStatus();
    } catch (e) { $("#setMsg", wrap).textContent = "失敗：" + e.message; }
  });
}

/* ============================================================ 供應鏈地圖 · 今日焦點 · 我的持股 */
const SC_LAYOUT = {};   // network id -> layout
const SC_NODE = {};     // node id -> detail
const MKT_ZH = { TW: "台", US: "美", JP: "日", KR: "韓" };

async function loadSC() {
  try {
    const [nets, idx] = await Promise.all([api("/api/sc/networks"), api("/api/sc/index")]);
    S.scNets = nets; S.scIndex = idx;
  } catch (e) { S.scNets = S.scNets || []; S.scIndex = S.scIndex || {}; }
}
function netHas(net, node) {
  const L = SC_LAYOUT[net];
  if (L) return L.nodes.some((n) => n.id === node);
  return net === "ai-datacenter";
}
function otherPane(i, fn) {
  const j = S.panes.findIndex((q, k) => k !== i && q.fn === fn);
  return j >= 0 ? j : (i + 1) % 4;
}
function openSC(fromPane, node, hl) { setPane(otherPane(fromPane, "SC"), { fn: "SC", net: "ai-datacenter", node, hl: hl || "" }); }
const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/* ---------- 供應鏈地圖 */
async function renderSC(i, body, p) {
  const net = p.net || "ai-datacenter";
  body.classList.add("sc-body");
  const chips = h("div", { class: "sc-nets" });
  for (const n of S.scNets || [])
    chips.append(h("button", { class: n.id === net ? "on" : "", title: n.desc,
      onclick: () => setPane(i, { net: n.id, node: netHas(n.id, p.node) ? p.node : "", hl: "" }) }, n.name));
  const inp = h("input", { placeholder: "找環節或個股：CoWoS、散熱、2330、奇鋐…", spellcheck: "false" });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && inp.value.trim()) run(`${i + 1} 供應鏈 ${inp.value.trim()}`); });
  const graph = h("div", { class: "sc-graph" }, h("div", { class: "loading" }, "載入供應鏈"));
  const detail = h("div", { class: "sc-detail" });
  const cmpBtn = h("button", { class: "sc-cmp" + (p.cmp ? " on" : ""), title: "每個環節同時顯示台股平均與美國同類公司平均（例：台灣散熱 vs Vertiv、Modine）",
    onclick: () => setPane(i, { cmp: !p.cmp }) }, "🇺🇸 美國對照");
  body.append(h("div", { class: "sc-top" }, chips, cmpBtn, h("div", { class: "sc-find" }, inp)), graph,
    h("div", { class: "sc-legend" }, h("span", {}, "箭頭：上游 → 下游"), h("span", {}, "色塊：成分股今日平均漲跌"),
      h("span", {}, "點環節：看公司、上下游；再點一次取消")), detail);
  let L;
  try { L = SC_LAYOUT[net] || (SC_LAYOUT[net] = await api(`/api/sc/network?id=${encodeURIComponent(net)}`)); }
  catch (e) { graph.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (S.panes[i].fn !== "SC") return;
  if (p.node && !L.nodes.some((n) => n.id === p.node)) p.node = "";
  const nodeName = p.node ? (L.nodes.find((n) => n.id === p.node) || {}).name : "";
  $(".pane-name", paneEl(i)).textContent = L.name + (nodeName ? ` › ${nodeName}` : "");
  let st = S.scStats && S.scStats[net];
  const draw = () => {
    const sl = graph.scrollLeft, stp = graph.scrollTop, first = !graph.querySelector("svg");
    graph.innerHTML = scSvg(L, p, st);
    if (first && p.node) {
      const g = graph.querySelector(`[data-node="${p.node}"]`);
      if (g) { graph.scrollLeft = Math.max(0, +g.dataset.x + 86 - graph.clientWidth / 2); graph.scrollTop = Math.max(0, +g.dataset.y - 40); }
    } else { graph.scrollLeft = sl; graph.scrollTop = stp; }
  };
  draw();
  graph.addEventListener("click", (e) => {
    const g = e.target.closest("[data-node]");
    if (g) setPane(i, { node: p.node === g.dataset.node ? "" : g.dataset.node, hl: "" });
  });
  scDetail(i, detail, p, L, st);
  const load = async () => {
    try { st = await api(`/api/sc/stats?net=${encodeURIComponent(net)}`); } catch (e) { return; }
    S.scStats = { ...(S.scStats || {}), [net]: st };
    if (S.panes[i].fn !== "SC" || (S.panes[i].net || "ai-datacenter") !== net || !body.isConnected) return;
    draw();
    scDetail(i, detail, p, L, st);
  };
  load();
  S.paneTimers = S.paneTimers || {};
  S.paneTimers[i] = setInterval(() => { if (!document.hidden) load(); }, 30000);
}

function scSvg(L, p, st) {
  const W = 172, H = 56, GX = 54, GY = 12, P = 10;
  const pos = {};
  for (const n of L.nodes) pos[n.id] = { x: P + n.col * (W + GX), y: P + n.row * (H + GY), n };
  const width = P * 2 + L.cols * (W + GX) - GX, height = P * 2 + L.rows * (H + GY) - GY;
  const sel = p.node, nb = new Set();
  if (sel) for (const [a, b] of L.edges) { if (a === sel) nb.add(b); if (b === sel) nb.add(a); }
  let s = `<svg class="sc-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
    <defs><marker id="scA" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L8,4L0,8z" class="sc-ah"/></marker>
    <marker id="scAh" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L8,4L0,8z" class="sc-ah hl"/></marker></defs>`;
  for (const [a, b] of L.edges) {
    const A = pos[a], B = pos[b];
    const x1 = A.x + W, y1 = A.y + H / 2, x2 = B.x - 2, y2 = B.y + H / 2, dx = Math.max(24, (x2 - x1) / 2);
    const on = sel && (a === sel || b === sel);
    s += `<path class="sc-edge${on ? " hl" : sel ? " dim" : ""}" d="M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}" marker-end="url(#${on ? "scAh" : "scA"})"/>`;
  }
  for (const n of L.nodes) {
    const { x, y } = pos[n.id];
    const ns = st && st.nodes[n.id];
    const v = ns ? ns.pct : null;
    const tone = v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "";
    const alpha = v == null ? 0 : Math.min(Math.abs(v) / 4, 1) * 0.38 + 0.06;
    const state = n.id === sel ? " sel" : sel ? (nb.has(n.id) ? " nb" : " dim") : "";
    const up = ns ? `${ns.up}↑ ${ns.down}↓` : `${n.n} 檔`;
    if (p.cmp && ns) {   // 台／美 side by side
      const tw = ns.tw_pct, us = ns.us_pct;
      const ttone = (x) => (x == null ? "" : x > 0 ? "up" : x < 0 ? "down" : "");
      s += `<g class="sc-node${state}" data-node="${esc(n.id)}" data-x="${x}" data-y="${y}" transform="translate(${x},${y})">
        <title>${esc(n.name)}：台股平均 ${tw == null ? "—" : pct(tw)}／美國同類 ${us == null ? "—" : pct(us)}</title>
        <rect class="sc-box" width="${W}" height="${H}" rx="3"/>
        ${tone ? `<rect class="sc-tint ${tone}" width="${W}" height="${H}" rx="3" style="fill-opacity:${alpha.toFixed(2)}"/>` : ""}
        <rect class="sc-mk" x="0" y="0" width="3" height="${H}"/>
        <text class="sc-name" x="10" y="20">${esc(clip(n.name, 12))}</text>
        <text class="sc-meta" x="10" y="41"><tspan class="sc-mkt">台</tspan> <tspan class="sc-cv ${ttone(tw)}">${tw == null ? "—" : pct(tw)}</tspan></text>
        <text class="sc-meta" x="${W - 8}" y="41" text-anchor="end"><tspan class="sc-usl">美</tspan> <tspan class="sc-cv ${ttone(us)}">${us == null ? "—" : pct(us)}</tspan></text>
      </g>`;
      continue;
    }
    s += `<g class="sc-node${state}" data-node="${esc(n.id)}" data-x="${x}" data-y="${y}" transform="translate(${x},${y})">
      <title>${esc(n.name)}</title>
      <rect class="sc-box" width="${W}" height="${H}" rx="3"/>
      ${tone ? `<rect class="sc-tint ${tone}" width="${W}" height="${H}" rx="3" style="fill-opacity:${alpha.toFixed(2)}"/>` : ""}
      <rect class="sc-mk" x="0" y="0" width="3" height="${H}"/>
      <text class="sc-name" x="10" y="20">${esc(clip(n.name, 12))}</text>
      <text class="sc-meta" x="10" y="41"><tspan class="sc-mkt">${MKT_ZH[n.mkt] || n.mkt}</tspan> ${esc(up)}</text>
      <text class="sc-pct ${tone}" x="${W - 8}" y="41" text-anchor="end">${v == null ? "" : pct(v)}</text>
    </g>`;
  }
  return s + "</svg>";
}

async function scDetail(i, box, p, L, st) {
  if (!p.node) {
    const rows = L.nodes.map((n) => ({ ...n, ...((st && st.nodes[n.id]) || {}) }))
      .sort((a, b) => (b.pct ?? -1e9) - (a.pct ?? -1e9));
    box.innerHTML = "";
    box.dataset.node = "";
    box.append(h("div", { class: "sc-title" }, h("b", {}, L.name), h("span", { class: "muted" }, L.desc)));
    const tbl = h("table", { class: "grid" });
    tbl.innerHTML = `<thead><tr><th>環節</th><th>市場</th><th>平均漲跌</th><th>台股平均</th><th>美國同類</th><th>台−美</th><th>漲／跌</th><th>檔數</th></tr></thead>`;
    const tb = h("tbody");
    for (const r of rows)
      tb.append(h("tr", { class: "click", onclick: () => setPane(i, { node: r.id, hl: "" }) },
        h("td", {}, r.name), h("td", {}, MKT_ZH[r.mkt] || r.mkt), h("td", { class: cls(r.pct) }, pct(r.pct)),
        h("td", { class: cls(r.tw_pct) }, pct(r.tw_pct)), h("td", { class: cls(r.us_pct) }, pct(r.us_pct)),
        h("td", { class: cls(r.tw_pct != null && r.us_pct != null ? r.tw_pct - r.us_pct : null) }, r.tw_pct != null && r.us_pct != null ? pct(r.tw_pct - r.us_pct) : "—"),
        h("td", {}, r.up != null ? `${r.up}／${r.down}` : "—"), h("td", {}, r.n)));
    tbl.append(tb);
    box.append(tbl);
    return;
  }
  let d = SC_NODE[p.node];
  if (!d) {
    try { d = SC_NODE[p.node] = await api(`/api/sc/node?id=${encodeURIComponent(p.node)}`); }
    catch (e) { box.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  }
  if (S.panes[i].node !== d.id) return;
  const live = st && st.nodes[d.id] ? st.nodes[d.id].companies : d.companies;
  if (box.dataset.node !== d.id) {   // build once; later refreshes only replace the price table
    box.dataset.node = d.id;
    box.innerHTML = "";
    const chipRow = (label, items, onPick) => h("div", { class: "sc-rel" }, h("span", { class: "sc-rel-l" }, label),
      items.length ? items.map((x) => h("button", { class: "chip", onclick: () => onPick(x) }, `${x.name}${x.mkt ? `（${MKT_ZH[x.mkt] || x.mkt}）` : ""}`)) : h("span", { class: "muted" }, "—"));
    const pick = (x) => setPane(i, { net: netHas(S.panes[i].net || "ai-datacenter", x.id) ? S.panes[i].net : "ai-datacenter", node: x.id, hl: "" });
    const nets = (S.scNets || []).filter((n) => d.networks.includes(n.id));
    box.append(
      h("div", { class: "sc-title" }, h("b", {}, d.name), h("span", { class: "tag" }, MKT_ZH[d.mkt] || d.mkt), h("span", { class: "muted" }, d.desc)),
      chipRow("上游", d.upstream, pick),
      chipRow("下游", d.downstream, pick),
      chipRow("所在地圖", nets.map((n) => ({ id: n.id, name: n.name })), (x) => setPane(i, { net: x.id })),
      h("div", { class: "sc-tbl" }),
      h("div", { class: "sc-us" }),
      h("div", { class: "ranges sc-actions" },
        h("button", { onclick: (e) => scNews(d, e.target, box) }, "📰 相關新聞"),
        h("button", { onclick: () => { setFocus(i); askSend(scPrompt(d, (S.scStats && S.scStats[S.panes[i].net || "ai-datacenter"]) || null)); } }, "問 AI 這個環節 ▸"),
        h("span", { class: "legend" }, "點公司開走勢（開在旁邊的視窗）")),
      h("div", { class: "sc-news" }));
  }
  const holder = $(".sc-tbl", box);
  const tbl = h("table", { class: "grid" });
  tbl.innerHTML = `<thead><tr><th>代號</th><th style="text-align:left">公司</th><th style="text-align:left">在這個環節做什麼</th><th>價格</th><th>漲跌</th><th>成交量</th></tr></thead>`;
  const tb = h("tbody");
  for (const c of live) {
    NAMES[c.symbol] = c.name;
    tb.append(h("tr", { class: "click" + (c.symbol === p.hl ? " hl" : ""), onclick: () => quickOpen(c.symbol, "GP") },
      h("td", {}, c.mkt === "TW" ? c.code : c.symbol),
      h("td", { class: "nm" }, c.name, c.mkt !== "TW" ? h("span", { class: "tag" }, MKT_ZH[c.mkt] || c.mkt) : null),
      h("td", { class: "nm muted" }, c.role),
      h("td", {}, c.last != null ? nf(c.last, pxd(c.last)) : "—"),
      h("td", { class: cls(c.pct) }, pct(c.pct)),
      h("td", {}, c.lots != null ? `${nf(c.lots, 0)} 張` : c.volume ? big(c.volume) : "—")));
  }
  tbl.append(tb);
  holder.innerHTML = "";
  holder.append(tbl);
  // 美國同類（對照）
  const usBox = $(".sc-us", box);
  const nst = st && st.nodes[d.id];
  const usRows = nst ? nst.us_companies : (d.us_peers || []);
  usBox.innerHTML = "";
  if (usRows && usRows.length) {
    const twp = nst ? nst.tw_pct : null, usp = nst ? nst.us_pct : null;
    usBox.append(h("div", { class: "sc-us-h" }, h("b", {}, "🇺🇸 美國同類（對照）"),
      twp != null || usp != null ? h("span", {}, "台股平均 ", h("b", { class: cls(twp) }, pct(twp)), "　美國平均 ", h("b", { class: cls(usp) }, pct(usp)),
        twp != null && usp != null ? h("span", { class: "muted" }, `　差 ${pct(twp - usp)}`) : null) : null));
    const t2 = h("table", { class: "grid" });
    t2.innerHTML = `<thead><tr><th>代號</th><th style="text-align:left">公司</th><th style="text-align:left">做什麼</th><th>價格</th><th>漲跌</th></tr></thead>`;
    const tb2 = h("tbody");
    for (const c of usRows) {
      NAMES[c.symbol] = c.name;
      tb2.append(h("tr", { class: "click", onclick: () => quickOpen(c.symbol, "GP") },
        h("td", {}, c.symbol), h("td", { class: "nm" }, c.name, c.peer ? h("span", { class: "tag", title: "不在這個環節的供應鏈裡，是美股同類公司，用來對照" }, "同類") : null),
        h("td", { class: "nm muted" }, c.role), h("td", {}, c.last != null ? nf(c.last, pxd(c.last)) : "—"), h("td", { class: cls(c.pct) }, pct(c.pct))));
    }
    t2.append(tb2);
    usBox.append(t2);
  }
}

async function scNews(d, btn, box) {
  const out = $(".sc-news", box);
  if (out.childElementCount) { out.innerHTML = ""; return; }
  out.innerHTML = `<div class="loading">搜尋「${esc(d.kw.slice(0, 3).join("、"))}」新聞</div>`;
  try {
    const rows = await api(`/api/sc/news?id=${encodeURIComponent(d.id)}`);
    out.innerHTML = "";
    if (!rows.length) { out.append(h("div", { class: "empty" }, "近 7 天沒有相關新聞")); return; }
    for (const n of rows) out.append(newsItem(n));
  } catch (e) { out.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

function newsItem(n, onTheme) {
  return h("div", { class: "fx-news" },
    h("span", { class: "muted fx-t" }, timeAgo(n.time)),
    h("div", {},
      n.link ? h("a", { href: n.link, target: "_blank", rel: "noopener noreferrer" }, n.title) : h("span", {}, n.title),
      h("span", { class: "muted" }, n.publisher ? `　${n.publisher}` : ""),
      onTheme && n.nodes && n.nodes.length ? h("div", { class: "fx-tags" }, n.nodes.map((k, j) =>
        h("button", { class: "chip", onclick: () => onTheme(k) }, n.node_names[j]))) : null));
}

function scPrompt(d, st) {
  const live = st && st.nodes[d.id] ? st.nodes[d.id].companies : d.companies;
  const lines = live.map((c) => `${c.mkt === "TW" ? c.code : c.symbol} ${c.name}（${c.role}）${c.pct != null ? ` 今日 ${pct(c.pct)}` : ""}`);
  return `請分析供應鏈環節「${d.name}」。\n說明：${d.desc}\n上游：${d.upstream.map((x) => x.name).join("、") || "—"}；下游：${d.downstream.map((x) => x.name).join("、") || "—"}\n` +
    `成分股：\n${lines.join("\n")}\n\n請說明這個環節在 AI／半導體供應鏈的位置、今天成分股的表現有沒有一致性、上下游怎麼連動，以及接下來要觀察的指標。數字只用上面提供的，不要編造。`;
}

/* ---------- 今日焦點 */
async function renderFocus(i, body, p) {
  body.classList.add("fx-body");
  body.innerHTML = `<div class="loading">整理今日焦點（指數、供應鏈題材、法人、新聞）</div>`;
  let d;
  try { d = await api("/api/focus"); } catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (S.panes[i].fn !== "FOCUS") return;
  body.innerHTML = "";
  body.append(h("div", { class: "ranges" },
    h("span", { class: "legend", style: "margin-left:0" }, `更新 ${d.at}　題材＝供應鏈環節（成分股等權平均漲跌）`),
    h("span", { class: "grow" }),
    h("button", { onclick: () => renderPane(i) }, "↻ 重新整理")));
  const idx = h("div", { class: "fx-idx" });
  for (const q of d.indices) {
    if (q.last == null) continue;
    idx.append(h("div", { class: "fx-card click", onclick: () => quickOpen(q.symbol, "GP") },
      h("span", { class: "muted" }, q.name || q.symbol), h("b", {}, nf(q.last, pxd(q.last))),
      h("span", { class: cls(q.pct) }, `${q.chg != null ? (q.chg > 0 ? "+" : "") + nf(q.chg, pxd(q.last)) : ""} ${pct(q.pct)}`)));
  }
  body.append(idx);
  const grid = h("div", { class: "fx-grid" });
  body.append(grid);
  const card = (title, ...kids) => h("section", { class: "fx-sec" }, h("h4", {}, title), ...kids);

  // 題材漲跌
  const mx = Math.max(1, ...d.themes.map((t) => Math.abs(t.pct)));
  const themes = h("div", { class: "fx-bars" });
  for (const t of d.themes) {
    const w = Math.abs(t.pct) / mx * 50;
    themes.append(h("div", { class: "fx-bar click", title: `${t.name}：漲 ${t.up}／跌 ${t.down}，點開供應鏈`, onclick: () => openSC(i, t.id) },
      h("span", { class: "fx-bar-n" }, t.name),
      h("span", { class: "fx-bar-t" }, h("i", { class: t.pct >= 0 ? "pos" : "neg", style: `${t.pct >= 0 ? "left:50%" : `left:${50 - w}%`};width:${w}%` })),
      h("b", { class: cls(t.pct) }, pct(t.pct))));
  }
  grid.append(card("題材漲跌（點開供應鏈地圖）", themes));

  // 強勢／弱勢
  const moverTbl = (rows) => {
    const t = h("table", { class: "grid" });
    t.innerHTML = `<thead><tr><th>股票</th><th style="text-align:left">環節</th><th>價格</th><th>漲跌</th></tr></thead>`;
    const tb = h("tbody");
    for (const m of rows) {
      NAMES[m.symbol] = m.name;
      tb.append(h("tr", { class: "click", onclick: () => quickOpen(m.symbol, "GP") },
        h("td", {}, `${m.code} ${m.name}`), h("td", { class: "nm muted" }, m.node_name),
        h("td", {}, nf(m.last, pxd(m.last))), h("td", { class: cls(m.pct) }, pct(m.pct))));
    }
    t.append(tb);
    return t;
  };
  grid.append(card("供應鏈強勢股", moverTbl(d.gainers)), card("供應鏈弱勢股", moverTbl(d.losers)));

  // 三大法人
  const inst = d.institutional;
  if (inst) {
    const t = h("table", { class: "grid" });
    t.innerHTML = `<thead><tr><th>法人</th><th>買進</th><th>賣出</th><th>買賣超</th></tr></thead>`;
    const tb = h("tbody");
    for (const r of inst.rows)
      tb.append(h("tr", { class: r.name === "合計" ? "fx-total" : "" }, h("td", {}, r.name), h("td", {}, nf(r.buy, 1)), h("td", {}, nf(r.sell, 1)),
        h("td", { class: cls(r.net) }, (r.net > 0 ? "+" : "") + nf(r.net, 1))));
    t.append(tb);
    grid.append(card(`三大法人 ${inst.date}（上市，億元）${inst.demo ? "　示範資料" : ""}`, t));
  } else grid.append(card("三大法人", h("div", { class: "empty" }, "證交所還沒公布（約 15:00 後）或暫時連不到")));

  // 我的持股今日（只有登入者自己看得到）
  const mine = card("我的持股今日", h("div", { class: "loading" }, "載入"));
  grid.append(mine);
  focusHoldings(i, mine);

  // 新聞
  const hot = h("div", { class: "fx-hot" }, h("span", { class: "muted" }, "新聞熱門題材："),
    d.news_themes.length ? d.news_themes.map((t) => h("button", { class: "chip", onclick: () => openSC(i, t.id) }, `${t.name} ${t.count}`)) : h("span", { class: "muted" }, "—"));
  const news = h("div", { class: "fx-newslist" });
  for (const n of d.news) news.append(newsItem(n, (k) => openSC(i, k)));
  if (!d.news.length) news.append(h("div", { class: "empty" }, "今天還沒有新聞（或連不到 Google 新聞）"));
  grid.append(h("section", { class: "fx-sec wide" }, h("h4", {}, "焦點新聞（自動標上相關供應鏈環節）"), hot, news));

  // AI 今日重點
  const ai = h("div", { class: "fx-ai" });
  grid.append(h("section", { class: "fx-sec wide" }, h("h4", {}, "🤖 AI 今日重點（本機 Ollama，依上面的資料撰寫）"), ai));
  focusAI(ai, false);
}

async function focusAI(box, generate) {
  if (generate) box.innerHTML = `<div class="loading">AI 撰寫中（約 1–2 分鐘）</div>`;
  let r;
  try { r = generate ? await api("/api/focus/ai", { method: "POST", json: { refresh: generate === "refresh" } }) : await api("/api/focus/ai"); }
  catch (e) { box.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  box.innerHTML = "";
  if (r && r.text) {
    box.append(h("div", { class: "md", html: md(r.text) }),
      h("div", { class: "ranges" }, h("span", { class: "legend", style: "margin-left:0" }, `${r.date} · ${r.model || ""} · ${String(r.at || "").replace("T", " ")}`),
        h("span", { class: "grow" }), h("button", { onclick: () => focusAI(box, "refresh") }, "用最新資料重寫")));
  } else {
    box.append(h("button", { class: "btn", onclick: () => focusAI(box, true) }, "請 AI 寫今日重點"),
      h("span", { class: "muted", style: "margin-left:8px" }, "每天存一份，之後打開直接看；不會用到任何人的持股"));
  }
}

async function focusHoldings(i, sec) {
  const body = sec.lastChild;
  if (!S.user) {
    body.replaceWith(h("div", { class: "fx-mine" }, h("span", { class: "muted" }, "登入後，這裡會顯示你的持股今日損益（只有你看得到）。"),
      h("button", { class: "btn ghost", onclick: () => setPane(otherPane(i, "HOLD"), { fn: "HOLD" }) }, "登入／註冊")));
    return;
  }
  let d;
  try { d = await api("/api/me/holdings"); } catch (e) { body.replaceWith(h("div", { class: "errbox" }, e.message)); return; }
  const t = d.total;
  const box = h("div", { class: "fx-mine" });
  if (!d.rows.length) box.append(h("span", { class: "muted" }, "還沒有持股。"));
  else {
    box.append(h("div", { class: "fx-kpis" },
      h("div", {}, h("span", { class: "muted" }, "今日損益"), h("b", { class: cls(t.day) }, `${t.day > 0 ? "+" : ""}${nf(t.day, 0)}`), h("span", { class: cls(t.day_pct) }, pct(t.day_pct))),
      h("div", {}, h("span", { class: "muted" }, "總市值"), h("b", {}, nf(t.value, 0)), h("span", { class: "muted" }, "台幣")),
      h("div", {}, h("span", { class: "muted" }, "未實現"), h("b", { class: cls(t.pnl) }, `${t.pnl > 0 ? "+" : ""}${nf(t.pnl, 0)}`), h("span", { class: cls(t.ret) }, pct(t.ret)))));
    const top = [...d.rows].filter((r) => r.pct != null).sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct)).slice(0, 4);
    for (const r of top)
      box.append(h("div", { class: "fx-mrow click", onclick: () => quickOpen(r.symbol, "GP") },
        h("span", {}, `${r.symbol.replace(/\.TWO?$/, "")} ${r.name || ""}`), h("span", { class: cls(r.pct) }, pct(r.pct)),
        h("span", { class: cls(r.day) }, r.day != null ? `${r.day > 0 ? "+" : ""}${nf(r.day, 0)} ${r.currency !== "TWD" ? r.currency : ""}` : "")));
  }
  box.append(h("button", { class: "btn ghost", onclick: () => setPane(otherPane(i, "HOLD"), { fn: "HOLD" }) }, "開啟我的持股"));
  body.replaceWith(box);
}

/* ---------- 我的持股（登入） */
async function loadUser() {
  try { const r = await api("/api/auth/me"); S.user = r.user; S.signup = r.signup; } catch (e) { S.user = null; }
  updateUserBtn();
}
function updateUserBtn() {
  const b = $("#userBtn");
  if (b) b.textContent = S.user ? `👤 ${S.user.username}` : "👤 登入";
}
function afterAuthChange() {
  if (S.public && !S.user) { location.reload(); return; }  // public URL: back to the login gate
  updateUserBtn();
  S.panes.forEach((q, j) => { if (q.fn === "HOLD" || q.fn === "FOCUS") { q.editing = null; renderPane(j); } });
}

function renderHold(i, body, p) {
  body.classList.add("hold-body");
  if (!S.user) return holdLogin(i, body);
  body.innerHTML = `<div class="loading">載入持股</div>`;
  refreshHold(i, true);
}

function gateLogin() {
  const user = h("input", { autocomplete: "username", placeholder: "帳號", spellcheck: "false" });
  const pw = h("input", { type: "password", autocomplete: "current-password", placeholder: "密碼" });
  const code = h("input", { class: "hidden", inputmode: "numeric", autocomplete: "one-time-code", placeholder: "6 位數驗證碼（或救援碼）" });
  const msg = h("div", { class: "hold-msg" });
  const go = h("button", { class: "btn", type: "submit" }, "登入");
  const form = h("form", { class: "hold-login gate-login" }, h("h3", {}, "FT 金融終端"), user, pw, code, go, msg,
    h("p", { class: "muted" }, "這是公開網址，需要登入才能使用。帳號請在本機或 tailnet 建立。"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    go.disabled = true;
    try { await api("/api/auth/login", { method: "POST", json: { username: user.value.trim(), password: pw.value, code: code.value.trim() } }); location.reload(); }
    catch (err) {
      msg.textContent = err.message; go.disabled = false;
      if (/驗證碼/.test(err.message)) { code.classList.remove("hidden"); code.value = ""; code.focus(); }
    }
  });
  document.body.innerHTML = "";
  document.body.append(h("div", { class: "gate" }, form));
  setTimeout(() => user.focus(), 30);
}

function holdLogin(i, body) {
  let mode = "login";
  const user = h("input", { autocomplete: "username", placeholder: "帳號", spellcheck: "false" });
  const pw = h("input", { type: "password", autocomplete: "current-password", placeholder: "密碼（至少 8 字）" });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", placeholder: "再輸入一次密碼", class: "hidden" });
  const code = h("input", { class: "hidden", inputmode: "numeric", autocomplete: "one-time-code", placeholder: "6 位數驗證碼（或救援碼）" });
  const msg = h("div", { class: "hold-msg" });
  const go = h("button", { class: "btn", type: "submit" }, "登入");
  const tabs = h("div", { class: "hold-tabs" });
  const setMode = (m) => {
    mode = m;
    $$("button", tabs).forEach((b) => b.classList.toggle("on", b.dataset.m === m));
    pw2.classList.toggle("hidden", m !== "register");
    pw.autocomplete = m === "register" ? "new-password" : "current-password";
    go.textContent = m === "register" ? "建立帳號" : "登入";
    msg.textContent = "";
  };
  tabs.append(h("button", { type: "button", "data-m": "login", class: "on", onclick: () => setMode("login") }, "登入"));
  if (S.signup) tabs.append(h("button", { type: "button", "data-m": "register", onclick: () => setMode("register") }, "註冊"));
  const form = h("form", { class: "hold-login" }, h("h3", {}, "我的持股"), tabs, user, pw, pw2, code, go, msg,
    h("p", { class: "muted" }, "持股存在這台主機的 fin-terminal 資料庫，每個帳號只看得到自己的；密碼以 scrypt 雜湊保存。持股不會寫進 Obsidian、Hermes、Open WebUI 或 AI 工作紀錄。"),
    S.signup ? null : h("p", { class: "muted" }, "這台已關閉註冊，請管理者建立帳號。"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (mode === "register" && pw.value !== pw2.value) { msg.textContent = "兩次密碼不一樣"; return; }
    go.disabled = true;
    try {
      await api(mode === "register" ? "/api/auth/register" : "/api/auth/login", { method: "POST", json: { username: user.value.trim(), password: pw.value, code: code.value.trim() } });
      await loadUser();
      afterAuthChange();
    } catch (err) {
      msg.textContent = err.message; go.disabled = false;
      if (/驗證碼/.test(err.message)) { code.classList.remove("hidden"); code.value = ""; code.focus(); }
    }
  });
  body.innerHTML = "";
  body.append(form);
  setTimeout(() => user.focus(), 30);
}

async function refreshHold(i, first) {
  const el = paneEl(i); if (!el) return;
  const body = $(".pane-body", el);
  const p = S.panes[i];
  if (p.fn !== "HOLD") return;
  const ae = document.activeElement;
  if (!first && (p.editing || (ae && body.contains(ae) && /INPUT|SELECT|TEXTAREA/.test(ae.tagName)))) return;  // don't wipe what you're typing
  let d, dd;
  try { [d, dd] = await Promise.all([api("/api/me/holdings"), api("/api/me/dca").catch((e) => ({ error: e.message, plans: [] }))]); }
  catch (e) {
    if (/登入/.test(e.message)) { S.user = null; afterAuthChange(); return; }
    body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return;
  }
  if (S.panes[i].fn !== "HOLD") return;
  const ae2 = document.activeElement;
  if (!first && (S.panes[i].editing || S.panes[i].dcaEditing || (ae2 && body.contains(ae2) && /INPUT|SELECT|TEXTAREA/.test(ae2.tagName)))) return;
  const scroll = body.scrollTop;
  body.innerHTML = "";
  holdView(i, body, d, dd);
  body.scrollTop = scroll;
}

function holdView(i, body, d, dd) {
  const p = S.panes[i];
  const t = d.total;
  $(".pane-name", paneEl(i)).textContent = `👤 ${d.user} · 只有你看得到`;
  body.append(h("div", { class: "ranges" },
    h("span", { class: "legend", style: "margin-left:0" }, `更新 ${String(d.at).replace("T", " ")} · 每 15 秒更新報價`),
    h("span", { class: "grow" }),
    d.rows.length ? h("button", { title: "把持股表交給本機 Ollama 分析（不會存檔）", onclick: () => { setFocus(i); askSend(holdPrompt(d)); } }, "問 AI 我的持股 ▸") : null,
    h("button", { onclick: () => { location.href = "/api/me/holdings.csv"; } }, "匯出 CSV"),
    h("button", { onclick: (e) => { const a = $(".hold-acct", body); a.classList.toggle("hidden"); } }, "帳號"),
    h("button", { onclick: async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {} S.user = null; afterAuthChange(); } }, "登出")));
  body.append(holdAccount(i));
  if (d.rows.length) {
    body.append(h("div", { class: "fx-kpis hold-kpis" },
      h("div", {}, h("span", { class: "muted" }, "總市值（台幣）"), h("b", {}, nf(t.value, 0)), h("span", { class: "muted" }, `成本 ${nf(t.basis, 0)}`)),
      h("div", {}, h("span", { class: "muted" }, "未實現損益"), h("b", { class: cls(t.pnl) }, `${t.pnl > 0 ? "+" : ""}${nf(t.pnl, 0)}`), h("span", { class: cls(t.ret) }, pct(t.ret))),
      h("div", {}, h("span", { class: "muted" }, "今日損益"), h("b", { class: cls(t.day) }, `${t.day > 0 ? "+" : ""}${nf(t.day, 0)}`), h("span", { class: cls(t.day_pct) }, pct(t.day_pct))),
      h("div", {}, h("span", { class: "muted" }, "檔數"), h("b", {}, d.rows.length), h("span", { class: "muted" }, d.missing_fx.length ? `${d.missing_fx.join("、")} 未換算` : ""))));
  }
  body.append(holdForm(i, d));
  if (!d.rows.length) {
    body.append(h("div", { class: "empty" }, "還沒有持股：在上面輸入代號或名稱、股數、每股成本後按「新增」。"));
    body.append(dcaSection(i, dd));
    return;
  }
  const tbl = h("table", { class: "grid hold-tbl" });
  tbl.innerHTML = `<thead><tr><th>標的</th><th>股數</th><th>成本</th><th>現價</th><th>漲跌</th><th>市值</th><th>損益</th><th>報酬</th><th>今日</th><th>權重</th><th style="text-align:left">供應鏈</th><th></th></tr></thead>`;
  const tb = h("tbody");
  for (const r of d.rows) {
    if (r.name) NAMES[r.symbol] = r.name;
    const cur = r.currency && r.currency !== "TWD" ? ` ${r.currency}` : "";
    const del = h("button", { class: "mini danger", onclick: async (e) => {
      e.stopPropagation();
      if (!del.dataset.armed) { del.dataset.armed = "1"; del.textContent = "確定？"; setTimeout(() => { delete del.dataset.armed; del.textContent = "刪除"; }, 3000); return; }
      try { await api(`/api/me/holdings/${r.id}`, { method: "DELETE" }); } catch (err) { flash(err.message); }
      refreshHold(i, true);
    } }, "刪除");
    tb.append(h("tr", { class: p.editing === r.id ? "hl" : "" },
      h("td", { class: "nm click", title: r.note || "", onclick: () => quickOpen(r.symbol, isFund(r.symbol) ? "FUNDD" : "GP") },
        h("b", {}, r.symbol.replace(/\.TWO?$/, "")), " ", r.name || "", r.note ? h("span", { class: "muted" }, ` · ${r.note}`) : null),
      h("td", {}, nf(r.shares, r.shares % 1 ? 3 : 0)), h("td", {}, nf(r.cost, pxd(r.cost)) + cur),
      h("td", {}, r.last != null ? nf(r.last, pxd(r.last)) : "—"), h("td", { class: cls(r.pct) }, pct(r.pct)),
      h("td", {}, r.value != null ? nf(r.value, 0) + cur : "—"),
      h("td", { class: cls(r.pnl) }, r.pnl != null ? `${r.pnl > 0 ? "+" : ""}${nf(r.pnl, 0)}` : "—"),
      h("td", { class: cls(r.ret) }, pct(r.ret)),
      h("td", { class: cls(r.day) }, r.day != null ? `${r.day > 0 ? "+" : ""}${nf(r.day, 0)}` : "—"),
      h("td", {}, r.weight != null ? nf(r.weight, 1) + "%" : "—"),
      h("td", { class: "nm" }, r.chains.length ? r.chains.slice(0, 2).map((c) => h("button", { class: "chip", onclick: () => openSC(i, scIdByName(c), r.symbol) }, c)) : h("span", { class: "muted" }, "—")),
      h("td", { class: "nm" }, h("button", { class: "mini", onclick: () => { S.panes[i].editing = r.id; S.panes[i].editRow = r; renderPane(i); } }, "編輯"), del)));
  }
  tbl.append(tb);
  body.append(h("div", { class: "hold-scroll" }, tbl));
  if (d.exposure.length) {
    const ex = h("div", { class: "fx-bars" });
    for (const x of d.exposure)
      ex.append(h("div", { class: "fx-bar click", onclick: () => openSC(i, scIdByName(x.chain)) },
        h("span", { class: "fx-bar-n" }, x.chain), h("span", { class: "fx-bar-t" }, h("i", { class: "pos amber", style: `left:0;width:${Math.min(100, x.weight)}%` })),
        h("b", {}, nf(x.weight, 1) + "%")));
    const covered = d.exposure.reduce((a, x) => a + x.weight, 0);
    body.append(h("section", { class: "fx-sec" }, h("h4", {}, `供應鏈曝險（佔總市值；地圖外 ${nf(Math.max(0, 100 - covered), 1)}%）`), ex));
  }
  body.append(dcaSection(i, dd));
}

/* ---------- 分析工具箱（技術／選擇權／評等籌碼／財報季／價值／市場情報） */
async function renderKit(i, body, p) {
  const tab = p.kit || "tech";
  body.classList.add("kit-body");
  const bar = h("div", { class: "ranges" }, ...KIT_TABS.map(([k, l]) => h("button", { class: tab === k ? "on" : "", onclick: () => setPane(i, { kit: k }) }, l)),
    h("span", { class: "grow" }),
    h("button", { title: "把這份資料交給本機 Ollama 解讀", onclick: () => { const txt = S.kitText && S.kitText[i]; if (txt) { setFocus(i); askSend(`根據下面的資料，用重點條列解讀現在的狀況與要注意的風險（不要給買賣建議）：\n\n${txt}`); } } }, "問 AI 解讀 ▸"));
  const holder = h("div", { class: "kit-out" }, h("div", { class: "loading" }, "計算中"));
  body.append(bar, holder);
  let d;
  try {
    const q = tab === "intel" ? "/api/kit/intel?market=all" : `/api/kit/${tab}?s=${encodeURIComponent(p.sec)}${p.kitExp ? `&expiry=${p.kitExp}` : ""}`;
    d = await api(q);
  } catch (e) { holder.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  const cur = S.panes[i];
  if (cur.fn !== "KIT" || (cur.kit || "tech") !== tab || (tab !== "intel" && cur.sec !== p.sec)) return;
  (S.kitText = S.kitText || {})[i] = d.text;
  holder.innerHTML = `<div class="md">${md(d.text)}</div>`;
  if (tab === "options" && d.data && d.data.expiries) {
    const sel = h("select", {}, ...d.data.expiries.slice(0, 16).map((e) => h("option", { value: e, ...(e === d.data.expiry ? { selected: "" } : {}) }, e)));
    sel.addEventListener("change", () => setPane(i, { kitExp: sel.value }));
    holder.prepend(h("div", { class: "kit-exp" }, h("span", { class: "muted" }, "到期日 "), sel));
  }
}

/* ---------- 側欄：定期定額回測＋AI 評估 */
const DCA_DEF = () => ({ symbols: S.panes[S.focus] && S.panes[S.focus].sec ? S.panes[S.focus].sec : "0050, VOO, BTC-USD", amount: 5000, freq: "monthly", days: "6",
  start: (() => { const d = new Date(); d.setFullYear(d.getFullYear() - 5); return d.toLocaleDateString("sv-SE"); })(), end: "", fee_pct: 0.1425 });

function dcaPrefill(v, run) {
  S.dcaForm = { ...(S.dcaForm || DCA_DEF()), ...v };
  S.dcaResult = null;
  if (S.sideTab === "dca") renderSide();
  if (run !== false) setTimeout(() => { const f = $("#dcaForm"); if (f) f.requestSubmit(); }, 50);
}

function renderDCA(body) {
  const v = S.dcaForm || (S.dcaForm = DCA_DEF());
  body.append(h("div", { class: "eng-head" },
    h("h2", {}, h("span", { class: "dot on" }), "定期定額回測＋AI 評估"),
    h("p", {}, "過去每期固定金額買進台股、美股、ETF、基金或比特幣，現在值多少？和同一筆錢第一天全部買進（單筆）比較，算年化報酬（XIRR）與最大回撤，再請本機 Ollama 評估。"),
    h("div", { class: "model", html: engineModel("ask") })));
  const inp = (name, attrs) => h("input", { name, value: v[name] ?? "", ...attrs });
  const freq = h("select", { name: "freq" }, ...Object.entries({ monthly: "每月", weekly: "每週", daily: "每個交易日" }).map(([k, l]) => h("option", { value: k, ...(v.freq === k ? { selected: "" } : {}) }, l)));
  const days = inp("days", { placeholder: "6,16,26" });
  const syncDays = () => { days.disabled = freq.value === "daily"; days.placeholder = freq.value === "weekly" ? "星期 1–5" : "6,16,26"; };
  freq.addEventListener("change", () => { days.value = freq.value === "weekly" ? "1" : freq.value === "monthly" ? "6" : ""; syncDays(); });
  syncDays();
  const row = (label, el, wide) => h("div", { class: "f" + (wide ? " wide" : "") }, h("label", {}, label), el);
  const go = h("button", { class: "btn run", type: "submit" }, "▶ 回測");
  const form = h("form", { class: "form", id: "dcaForm" },
    row("標的（逗號分隔，最多 6 個；可用中文名、FUND:代號）", inp("symbols", { placeholder: "0050, 台積電, VOO, BTC-USD, 安聯台灣科技", spellcheck: "false", class: "tick" }), true),
    row("每期金額（台幣）", inp("amount", { type: "number", min: "1", step: "1" })), row("頻率", freq),
    row("扣款日（每月幾號／每週星期幾）", days), row("手續費 %（最低 1 元）", inp("fee_pct", { type: "number", min: "0", step: "0.0001" })),
    row("開始", inp("start", { type: "date" })), row("結束（空白＝今天）", inp("end", { type: "date" })),
    h("div", { class: "f wide dca-quick" }, ...[[1, "近 1 年"], [3, "3 年"], [5, "5 年"], [10, "10 年"], [20, "20 年"]].map(([y, l]) =>
      h("button", { type: "button", class: "chip", onclick: () => { const d = new Date(); d.setFullYear(d.getFullYear() - y); form.start.value = d.toLocaleDateString("sv-SE"); form.end.value = ""; } }, l))),
    h("div", { class: "run-row f wide" }, go, h("span", { class: "hint" }, "用歷史收盤價計算，幾秒內完成；AI 評估另外按")));
  const out = h("div", { class: "dca-out" });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const b = Object.fromEntries(new FormData(form).entries());
    b.freq = freq.value;
    S.dcaForm = b;
    go.disabled = true; out.innerHTML = `<div class="loading">回測中</div>`;
    try { S.dcaResult = await api("/api/dca/backtest", { method: "POST", json: b }); dcaResults(out, S.dcaResult); }
    catch (err) { out.innerHTML = `<div class="errbox">${esc(err.message)}</div>`; }
    go.disabled = false;
  });
  body.append(form, out);
  if (S.dcaResult) dcaResults(out, S.dcaResult);
}

const DCA_COLORS = ["#ffa028", "#4ba3ff", "#3ad6d6", "#b48cff", "#ff6b9a", "#9be564"];

function dcaResults(out, d) {
  out.innerHTML = "";
  const ok = d.results.filter((r) => !r.error);
  for (const r of d.results.filter((x) => x.error)) out.append(h("div", { class: "errbox" }, `${r.symbol}：${r.error}`));
  if (!ok.length) return;
  const tbl = h("table", { class: "grid dca-res" });
  tbl.innerHTML = `<thead><tr><th>標的</th><th>期數</th><th>投入</th><th>市值</th><th>報酬</th><th title="XIRR：考慮每筆投入時間">年化</th><th title="持有期間「市值／累積投入」從高點的最大跌幅">最大回撤</th><th title="同一筆總金額在第一期全部買進">單筆報酬</th><th>單筆年化</th></tr></thead>`;
  const tb = h("tbody");
  ok.forEach((r, k) => tb.append(h("tr", { title: `${r.plan}，${r.start}～${r.end}；手續費合計 ${nf(r.fees, 0)} 元；${r.price_note}` },
    h("td", { class: "nm" }, h("i", { class: "dca-dot", style: `background:${DCA_COLORS[k % 6]}` }), h("b", {}, r.symbol.replace(/\.TWO?$/, "")), " ", r.name || ""),
    h("td", {}, r.n), h("td", {}, nf(r.invested, 0)), h("td", {}, nf(r.value, 0)),
    h("td", { class: cls(r.ret) }, pct(r.ret)), h("td", { class: cls(r.xirr) }, pct(r.xirr)), h("td", { class: "down" }, pct(r.mdd)),
    h("td", { class: cls(r.lump.ret) }, pct(r.lump.ret)), h("td", { class: cls(r.lump.xirr) }, pct(r.lump.xirr)))));
  tbl.append(tb);
  const chart = h("div", { class: "dca-chart" });
  const ai = h("div", { class: "dca-ai" });
  const aiBtn = h("button", { class: "btn ghost", onclick: async () => {
    aiBtn.disabled = true; ai.innerHTML = `<div class="loading">本機 Ollama 評估中（約 1–2 分鐘）</div>`;
    try { const r = await api("/api/dca/ai", { method: "POST", json: { text: d.text } }); ai.innerHTML = `<div class="md">${md(r.text)}</div>`; }
    catch (err) { ai.innerHTML = `<div class="errbox">${esc(err.message)}</div>`; }
    aiBtn.disabled = false;
  } }, "🤖 AI 評估這次回測");
  out.append(h("div", { class: "muted dca-sum" }, `${ok[0].plan}・${ok[0].start} 起`), h("div", { class: "hold-scroll" }, tbl), chart,
    h("div", { class: "run-row" }, aiBtn, h("button", { class: "btn ghost", onclick: () => { navigator.clipboard && navigator.clipboard.writeText(d.text); flash("已複製"); } }, "複製文字")), ai);
  requestAnimationFrame(() => {
    const c = LightweightCharts.createChart(chart, { ...chartTheme(), autoSize: true });
    ok.forEach((r, k) => {
      const s2 = c.addLineSeries({ color: DCA_COLORS[k % 6], lineWidth: 2, priceLineVisible: false, title: r.symbol.replace(/\.TWO?$/, "") });
      s2.setData(r.curve.map((x) => ({ time: x.time, value: x.value })));
    });
    const longest = ok.reduce((a, r) => (r.curve.length > a.curve.length ? r : a), ok[0]);
    const inv = c.addLineSeries({ color: "#8a929a", lineWidth: 1, lineStyle: 2, priceLineVisible: false, title: "累積投入" });
    inv.setData(longest.curve.map((x) => ({ time: x.time, value: x.invested })));
    c.timeScale().fitContent();
  });
}

/* ---------- 我的持股：定期定額 */
const signed0 = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${nf(v, 0)}`);

function dcaSection(i, dd) {
  dd = dd || { plans: [] };
  const p = S.panes[i];
  const sec = h("section", { class: "fx-sec dca-sec" });
  const t = dd.total || {};
  sec.append(h("h4", {}, "定期定額",
    h("span", { class: "muted", style: "font-weight:400" }, dd.plans.length ? `　${dd.plans.length} 個計畫・每月約 ${nf(t.monthly || 0, 0)} 元` : "　每期自動買進台股、美股、ETF、基金、比特幣，用歷史收盤價算出累積單位")));
  if (dd.error) sec.append(h("div", { class: "errbox" }, dd.error));
  if (dd.plans.length && t.invested) {
    sec.append(h("div", { class: "fx-kpis hold-kpis" },
      h("div", {}, h("span", { class: "muted" }, "累積投入"), h("b", {}, nf(t.invested, 0))),
      h("div", {}, h("span", { class: "muted" }, "目前市值"), h("b", {}, nf(t.value, 0))),
      h("div", {}, h("span", { class: "muted" }, "損益"), h("b", { class: cls(t.pnl) }, signed0(t.pnl)), h("span", { class: cls(t.ret) }, pct(t.ret)))));
  }
  sec.append(dcaForm(i));
  if (!dd.plans.length) return sec;
  const tbl = h("table", { class: "grid hold-tbl dca-tbl" });
  tbl.innerHTML = `<thead><tr><th>標的</th><th style="text-align:left">設定</th><th>期數</th><th>投入</th><th>累積單位</th><th>平均成本</th><th>市值</th><th>損益</th><th>報酬</th><th title="考慮每筆投入時間的年化報酬（XIRR）">年化</th><th>下次扣款</th><th></th></tr></thead>`;
  const tb = h("tbody");
  for (const r of dd.plans) {
    const cur = r.currency && r.currency !== "TWD" ? ` ${r.currency}` : "";
    const del = h("button", { class: "mini danger", onclick: async (e) => {
      e.stopPropagation();
      if (!del.dataset.armed) { del.dataset.armed = "1"; del.textContent = "確定？"; setTimeout(() => { delete del.dataset.armed; del.textContent = "刪除"; }, 3000); return; }
      try { await api(`/api/me/dca/${r.id}`, { method: "DELETE" }); } catch (err) { flash(err.message); }
      refreshHold(i, true);
    } }, "刪除");
    const lots = (r.lots || []).map((x) => `${x.date}  ${nf(x.amount, 0)} 元 → ${nf(x.units, 4)} 單位 @ ${nf(x.price, pxd(x.price))}`).join("\n");
    tb.append(h("tr", { class: p.dcaEditing === r.id ? "hl" : "", title: lots ? `最近扣款：\n${lots}` : "" },
      h("td", { class: "nm click", onclick: () => quickOpen(r.symbol, isFund(r.symbol) ? "FUNDD" : "GP") },
        h("b", {}, r.symbol.replace(/\.TWO?$/, "")), " ", r.name || "", r.note ? h("span", { class: "muted" }, ` · ${r.note}`) : null),
      h("td", { class: "nm" }, r.plan, h("span", { class: "muted" }, `　${r.start} 起${r.end ? `～${r.end}` : ""}${r.fee_pct ? `・手續費 ${r.fee_pct}%` : ""}`)),
      ...(r.error ? [h("td", { colspan: "8", class: "muted" }, r.error)] : [
        h("td", {}, r.n), h("td", {}, nf(r.invested, 0)), h("td", {}, nf(r.units, r.units >= 100 ? 2 : 4)),
        h("td", {}, r.avg_cost != null ? nf(r.avg_cost, pxd(r.avg_cost)) + cur : "—"),
        h("td", {}, nf(r.value, 0)), h("td", { class: cls(r.pnl) }, signed0(r.pnl)), h("td", { class: cls(r.ret) }, pct(r.ret)),
        h("td", { class: cls(r.xirr) }, pct(r.xirr))]),
      h("td", { class: "muted" }, r.next || (r.end ? "已結束" : "—")),
      h("td", { class: "nm" },
        h("button", { class: "mini", title: "用同樣設定在側欄回測、比較單筆投入", onclick: () => { openSide("dca"); dcaPrefill({ symbols: r.symbol, amount: r.amount, freq: r.freq, days: r.days, start: r.start, end: r.end || "", fee_pct: r.fee_pct }); } }, "回測"),
        h("button", { class: "mini", onclick: () => { S.panes[i].dcaEditing = r.id; S.panes[i].dcaRow = r; renderPane(i); } }, "編輯"), del)));
  }
  tbl.append(tb);
  sec.append(h("div", { class: "hold-scroll" }, tbl),
    h("div", { class: "muted dca-note" }, "依每期扣款日（遇假日順延到下一個交易日）的收盤價計算；台股與 ETF 用還原權息價（股利再投入），基金用淨值（配息型不含配息），美元資產以當天匯率換算台幣。不含稅，實際成交以券商為準。"));
  return sec;
}

function dcaForm(i) {
  const p = S.panes[i];
  const r = p.dcaEditing ? p.dcaRow || {} : {};
  const sym = h("input", { placeholder: "定期定額標的：0050、VOO、BTC-USD、基金名稱", value: r.symbol || "", spellcheck: "false" });
  const amount = h("input", { type: "number", step: "1", min: "1", placeholder: "每期金額（台幣）", value: r.amount ?? "" });
  const freq = h("select", {}, ...Object.entries({ monthly: "每月", weekly: "每週", daily: "每個交易日" }).map(([k, l]) => h("option", { value: k, ...((r.freq || "monthly") === k ? { selected: "" } : {}) }, l)));
  const days = h("input", { class: "dca-days", placeholder: "扣款日 6,16,26", value: r.days || "6" });
  const start = h("input", { type: "date", value: r.start || new Date().toLocaleDateString("sv-SE"), title: "開始扣款日（可填過去日期，系統會補算已經扣過的每一期）" });
  const end = h("input", { type: "date", value: r.end || "", title: "停扣日（選填）" });
  const fee = h("input", { type: "number", step: "0.0001", min: "0", class: "dca-fee", value: r.fee_pct ?? 0.1425, title: "手續費 %（台股券商定期定額常見 0.1425% 打折、最低 1 元；美股複委託請自填）" });
  const note = h("input", { placeholder: "備註", value: r.note || "", class: "dca-note-in" });
  const msg = h("span", { class: "hold-msg" });
  const syncDays = () => { days.placeholder = freq.value === "weekly" ? "星期 1–5，例 1" : freq.value === "monthly" ? "扣款日 6,16,26" : "—"; days.disabled = freq.value === "daily"; };
  freq.addEventListener("change", () => { if (freq.value === "weekly") days.value = "1"; else if (freq.value === "monthly") days.value = "6"; syncDays(); });
  syncDays();
  const save = h("button", { class: "btn", type: "submit" }, p.dcaEditing ? "儲存定期定額" : "新增定期定額");
  const form = h("form", { class: "hold-form dca-form" }, sym, amount, freq, days, start, end, h("span", { class: "muted" }, "手續費%"), fee, note, save,
    p.dcaEditing ? h("button", { class: "btn ghost", type: "button", onclick: () => { S.panes[i].dcaEditing = null; renderPane(i); } }, "取消") : null, msg);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.textContent = "";
    let s2 = sym.value.trim();
    if (!s2) { msg.textContent = "請輸入標的"; return; }
    if (!isFund(s2.toUpperCase()) && (/[一-鿿]/.test(s2) || /^\d{4,6}[A-Z]?$/i.test(s2) || !/[.=^-]/.test(s2))) {
      const r2 = await resolveSec(s2);
      if (!r2) { msg.textContent = `找不到「${s2}」`; return; }
      s2 = r2;
    }
    save.disabled = true;
    try {
      const b = { symbol: s2, amount: amount.value, freq: freq.value, days: days.value, start: start.value, end: end.value, fee_pct: fee.value, min_fee: 1, note: note.value };
      if (S.panes[i].dcaEditing) await api(`/api/me/dca/${S.panes[i].dcaEditing}`, { method: "PUT", json: b });
      else await api("/api/me/dca", { method: "POST", json: b });
      S.panes[i].dcaEditing = null;
      renderPane(i);
    } catch (err) { msg.textContent = err.message; save.disabled = false; }
  });
  return form;
}

function scIdByName(name) {
  for (const list of Object.values(S.scIndex || {})) for (const x of list) if (x.name === name) return x.id;
  return "";
}

function holdForm(i, d) {
  const p = S.panes[i];
  const r = p.editing ? p.editRow || {} : {};
  const sym = h("input", { placeholder: "代號或名稱：2330、台積電、NVDA、0056", value: r.symbol || "", spellcheck: "false" });
  const shares = h("input", { type: "number", step: "any", min: "0", placeholder: "股數", value: r.shares ?? "" });
  const cost = h("input", { type: "number", step: "any", min: "0", placeholder: "每股成本", value: r.cost ?? "" });
  const opened = h("input", { type: "date", value: r.opened || "" });
  const note = h("input", { placeholder: "備註（選填）", value: r.note || "" });
  const msg = h("span", { class: "hold-msg" });
  const save = h("button", { class: "btn", type: "submit" }, p.editing ? "儲存修改" : "新增");
  const form = h("form", { class: "hold-form" }, sym, shares, cost, opened, note, save,
    p.editing ? h("button", { class: "btn ghost", type: "button", onclick: () => { S.panes[i].editing = null; renderPane(i); } }, "取消") : null, msg,
    h("div", { class: "muted hold-hint" }, "台股以「股」計：1 張 = 1,000 股，零股照實際股數。成本填每股平均成本（要含手續費就自己加進去）。基金可輸入「基金名稱」或 FUND: 代號。"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.textContent = "";
    let s = sym.value.trim();
    if (!s) { msg.textContent = "請輸入標的"; return; }
    if (!isFund(s.toUpperCase()) && (/[一-鿿]/.test(s) || /^\d{4,6}[A-Z]?$/i.test(s) || !/[.=^-]/.test(s))) {
      const r2 = await resolveSec(s);
      if (!r2) { msg.textContent = `找不到「${s}」`; return; }
      s = r2;
    }
    save.disabled = true;
    try {
      const body = { symbol: s, shares: shares.value, cost: cost.value, opened: opened.value, note: note.value };
      if (S.panes[i].editing) await api(`/api/me/holdings/${S.panes[i].editing}`, { method: "PUT", json: body });
      else await api("/api/me/holdings", { method: "POST", json: body });
      S.panes[i].editing = null;
      renderPane(i);
    } catch (err) { msg.textContent = err.message; save.disabled = false; }
  });
  return form;
}

function holdAccount(i) {
  const old = h("input", { type: "password", autocomplete: "current-password", placeholder: "目前密碼" });
  const nw = h("input", { type: "password", autocomplete: "new-password", placeholder: "新密碼（至少 8 字）" });
  const msg = h("span", { class: "hold-msg" });
  const delPw = h("input", { type: "password", autocomplete: "current-password", placeholder: "輸入密碼確認刪除" });
  const f1 = h("form", { class: "hold-form" }, h("b", {}, "改密碼"), old, nw, h("button", { class: "btn ghost", type: "submit" }, "更新"), msg);
  f1.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/auth/password", { method: "POST", json: { old: old.value, new: nw.value } }); msg.textContent = "已更新，其他裝置已登出"; old.value = nw.value = ""; }
    catch (err) { msg.textContent = err.message; }
  });
  const f2 = h("form", { class: "hold-form" }, h("b", { class: "down" }, "刪除帳號"), delPw,
    h("button", { class: "btn danger", type: "submit" }, "永久刪除帳號與全部持股"));
  f2.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/auth/delete", { method: "POST", json: { password: delPw.value } }); S.user = null; afterAuthChange(); }
    catch (err) { msg.textContent = err.message; }
  });
  return h("div", { class: "hold-acct hidden" }, f1, totpBox(), f2);
}

/* 兩步驟驗證（TOTP）：設定 → 掃 QR → 輸入驗證碼啟用 → 顯示救援碼一次 */
function totpBox() {
  const box = h("div", { class: "hold-form totp-box" });
  const draw = () => {
    box.innerHTML = "";
    const on = S.user && S.user.totp;
    box.append(h("b", {}, "兩步驟驗證（TOTP）"), h("span", { class: on ? "up" : "muted" }, on ? `● 已啟用（剩 ${S.user.recovery_left ?? "?"} 組救援碼）` : "未啟用"));
    const pw = h("input", { type: "password", autocomplete: "current-password", placeholder: "目前密碼" });
    const msg = h("span", { class: "hold-msg" });
    if (!on) {
      const f = h("form", {}, pw, h("button", { class: "btn ghost", type: "submit" }, "開始設定"), msg);
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        try { setup(await api("/api/auth/totp/setup", { method: "POST", json: { password: pw.value } })); } catch (err) { msg.textContent = err.message; }
      });
      box.append(f, h("p", { class: "muted" }, "用 Google Authenticator、1Password、Aegis、Microsoft Authenticator 等 App。啟用後登入要再輸入 6 位數驗證碼。"));
    } else {
      const code = h("input", { inputmode: "numeric", autocomplete: "one-time-code", placeholder: "驗證碼或救援碼" });
      const re = h("button", { class: "btn ghost", type: "button", onclick: async () => {
        try { setup(await api("/api/auth/totp/setup", { method: "POST", json: { password: pw.value } })); } catch (err) { msg.textContent = err.message; } } }, "換手機／重產救援碼");
      const f = h("form", {}, pw, code, h("button", { class: "btn danger", type: "submit" }, "關閉"), re, msg);
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        try { await api("/api/auth/totp/disable", { method: "POST", json: { password: pw.value, code: code.value } }); await loadUser(); draw(); }
        catch (err) { msg.textContent = err.message; }
      });
      box.append(f);
    }
  };
  const setup = (t) => {
    box.innerHTML = "";
    const code = h("input", { inputmode: "numeric", autocomplete: "one-time-code", placeholder: "App 上的 6 位數", maxlength: "6" });
    const msg = h("span", { class: "hold-msg" });
    const f = h("form", {}, code, h("button", { class: "btn", type: "submit" }, "啟用"), h("button", { class: "btn ghost", type: "button", onclick: draw }, "取消"), msg);
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const r = await api("/api/auth/totp/enable", { method: "POST", json: { code: code.value } });
        await loadUser();
        box.innerHTML = "";
        box.append(h("b", { class: "up" }, "✓ 已啟用兩步驟驗證"),
          h("p", {}, "救援碼（手機遺失時代替驗證碼，每組只能用一次）。現在就存到密碼管理器，之後不會再顯示："),
          h("pre", { class: "totp-codes" }, r.recovery_codes.join("\n")),
          h("button", { class: "btn ghost", type: "button", onclick: draw }, "我已存好"));
      } catch (err) { msg.textContent = err.message; }
    });
    box.append(h("b", {}, "用驗證器 App 掃描"),
      t.qr_svg ? h("div", { class: "totp-qr", html: t.qr_svg }) : null,
      h("div", { class: "muted" }, "無法掃描時手動輸入：", h("code", { class: "totp-secret" }, t.secret.replace(/(.{4})/g, "$1 ").trim())),
      f);
    setTimeout(() => code.focus(), 30);
  };
  draw();
  return box;
}

function holdPrompt(d) {
  const lines = d.rows.map((r) => `${r.symbol} ${r.name || ""}：${r.shares} 股，成本 ${r.cost}，現價 ${r.last ?? "—"}（今日 ${pct(r.pct)}），` +
    `報酬 ${pct(r.ret)}，權重 ${r.weight != null ? r.weight.toFixed(1) + "%" : "—"}，幣別 ${r.currency}，供應鏈：${r.chains.join("、") || "—"}`);
  const t = d.total;
  return `這是我的持股（台幣總市值 ${nf(t.value, 0)}，未實現 ${pct(t.ret)}，今日 ${pct(t.day_pct)}）：\n${lines.join("\n")}\n\n` +
    `供應鏈曝險：${d.exposure.map((x) => `${x.chain} ${x.weight.toFixed(1)}%`).join("、") || "—"}\n\n` +
    "請檢查：集中度與單一供應鏈環節的曝險、今天表現最好／最差的部位與可能原因、有沒有上下游重複押注，以及接下來要觀察的風險。只用上面的數字，不要編造價格；不構成投資建議。";
}


/* ---------- 重大事件 */
const EV_KIND_STYLE = { move: "ev-move", history: "ev-hist", fomc: "ev-fomc", cbc: "ev-cbc", macro: "ev-macro", manual: "ev-manual", world: "ev-world" };
const WEEKDAY = "日一二三四五六";

async function renderEvents(i, body, p) {
  body.classList.add("ev-body");
  const kind = p.evKind || "";
  const days = p.evDays || 365;
  const bar = h("div", { class: "ranges" });
  [["", "全部"], ["history", "歷史大事"], ["move", "指數大波動"], ["fomc", "Fed 決策"], ["cbc", "台灣央行"], ["macro", "美國數據"], ["manual", "自訂"], ["world", "世界事件"]].forEach(([k, l]) =>
    bar.append(h("button", { class: kind === k ? "on" : "", onclick: () => setPane(i, { evKind: k }) }, l)));
  bar.append(h("span", { class: "sep-s" }));
  [[183, "半年"], [365, "1 年"], [730, "2 年"], [1826, "5 年"], [3653, "10 年"]].forEach(([d, l]) =>
    bar.append(h("button", { class: days === d ? "on" : "", onclick: () => setPane(i, { evDays: d }) }, l)));
  const stat = h("span", { class: "legend" });
  bar.append(stat, h("span", { class: "grow" }),
    h("button", { onclick: () => { const f = $(".ev-add", body); f.classList.toggle("hidden"); if (!f.classList.contains("hidden")) $("input", f).focus(); } }, "＋ 新增事件"),
    h("button", { class: "ev-load", title: "用近 10 年的指數歷史偵測大漲大跌，並載入 2020 年起的歷史大事（疫情股災、升降息、SVB、日圓套利平倉、DeepSeek、對等關稅…）；新聞與 AI 摘要會在背景慢慢補",
      onclick: async (e) => { e.target.disabled = true; e.target.textContent = "載入中（約 10–30 秒）…";
        try { const r = await api("/api/events/refresh?years=10", { method: "POST" }); flash(`已載入：偵測 ${r.detected} 件大波動、${r.history} 件歷史大事（${r.since || ""} 起）`); } catch (x) { flash(x.message); }
        setPane(i, { evDays: 3653 }); } }, "⤓ 載入近年大事"),
    h("button", { title: "用最新的指數資料重新偵測大漲大跌", onclick: async (e) => { e.target.disabled = true; e.target.textContent = "偵測中…"; try { await api("/api/events/refresh", { method: "POST" }); } catch (x) { flash(x.message); } renderPane(i); } }, "↻ 重新偵測"));
  body.append(bar, evAddForm(i));
  const list = h("div", { class: "ev-list" }, h("div", { class: "loading" }, "載入重大事件"));
  body.append(list);
  const since = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
  let d;
  try { d = await api(`/api/events?since=${since}${kind ? `&kinds=${kind}` : ""}`); }
  catch (e) { list.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (S.panes[i].fn !== "EVENTS") return;
  const st = d.stats;
  stat.textContent = `指數大波動 ${st.moves} 件 · AI 已命名 ${st.ai_named}${st.pending ? ` · 背景處理中 ${st.pending}` : ""}${st.ai_enabled ? "" : " · AI 命名已關閉"}`;
  list.innerHTML = "";
  const up = d.events.filter((e) => e.upcoming).reverse();
  const past = d.events.filter((e) => !e.upcoming);
  if (up.length) {
    const box = h("section", { class: "ev-up" }, h("h4", {}, "即將到來"));
    for (const e of up) {
      const days2 = Math.round((new Date(e.date) - new Date(new Date().toISOString().slice(0, 10))) / 86400000);
      box.append(h("div", { class: "ev-up-row" }, h("b", {}, e.date), h("span", { class: `ev-tag ${EV_KIND_STYLE[e.kind] || ""}` }, e.kind_name),
        h("span", {}, e.title_final), h("span", { class: "muted" }, `${days2} 天後`)));
    }
    list.append(box);
  }
  if (!past.length) {
    list.append(h("div", { class: "empty" }, st.moves ? "這個篩選下沒有事件（看更早的事件：上方選 5 年／10 年，或按「⤓ 載入近年大事」）" : "還沒有偵測結果：服務啟動約 2 分鐘後會自動偵測，或按「⤓ 載入近年大事」。"));
    return;
  }
  let month = "";
  for (const e of past) {
    const m = e.date.slice(0, 7);
    if (m !== month) { month = m; list.append(h("div", { class: "ev-month" }, `${m.slice(0, 4)} 年 ${+m.slice(5)} 月`)); }
    list.append(evCard(i, e, p));
  }
  if (p.evFocus) {
    const el = list.querySelector(`[data-ev="${CSS.escape(p.evFocus)}"]`);
    if (el) { el.classList.add("focus"); el.scrollIntoView({ block: "center" }); }
    S.panes[i].evFocus = null;
  }
}

function evCard(i, e, p) {
  const dd = new Date(e.date + "T00:00:00");
  const card = h("article", { class: `ev-card ${e.kind === "move" || e.kind === "history" ? (e.dir > 0 ? "rise" : "fall") : ""}`, "data-ev": e.id });
  const mv = Object.entries(e.moves || {}).sort((a, b) => Math.abs(b[1].pct) - Math.abs(a[1].pct));
  card.append(h("div", { class: "ev-date" }, h("b", {}, e.date.slice(5)), h("span", {}, `週${WEEKDAY[dd.getDay()]}`)));
  const main = h("div", { class: "ev-main" });
  main.append(h("div", { class: "ev-head" },
    h("span", { class: `ev-tag ${EV_KIND_STYLE[e.kind] || ""}` }, e.kind_name + (e.region ? `・${e.region}` : "")),
    h("span", { class: "ev-title" }, e.kind === "move" ? (e.dir > 0 ? "▲ " : "▼ ") : "", e.title_final),
    e.ai_at && !e.title ? h("span", { class: "ev-ai", title: `AI 依當時新聞標題命名（${e.ai_model || ""}）` }, "AI") : null));
  if (mv.length) main.append(h("div", { class: "ev-mv" }, mv.map(([sym, m]) =>
    h("button", { class: "chip", title: `${m.date} ${m.kind === "week" ? "五日累計" : "單日"}；點開走勢`, onclick: () => evOpenChart(i, sym, e.date) },
      `${m.name} `, h("b", { class: cls(m.pct) }, pct(m.pct, 1)), m.kind === "week" ? h("small", { class: "muted" }, " 5日") : null))));
  if (e.summary_final) main.append(h("p", { class: "ev-sum" }, e.summary_final));
  const hl = e.headlines || [];
  if (hl.length) {
    const ul = h("ul", { class: "ev-news" });
    hl.slice(0, 3).forEach((n) => ul.append(evNewsLi(n)));
    main.append(ul);
    if (hl.length > 3) {
      const more = h("button", { class: "mini", onclick: () => { hl.slice(3).forEach((n) => ul.append(evNewsLi(n))); more.remove(); } }, `更多新聞 ${hl.length - 3} 則`);
      main.append(more);
    }
  } else if (["move", "fomc", "cbc", "history"].includes(e.kind) && !e.news_at) main.append(h("div", { class: "muted ev-sum" }, "新聞與 AI 摘要在背景整理中…"));
  if (!e.readonly) {
    const acts = h("div", { class: "ev-acts" });
    const idx = mv.length ? mv[0][0] : e.kind === "cbc" ? "TWD=X" : e.kind === "macro" || e.kind === "fomc" ? "^GSPC" : "^TWII";
    acts.append(h("button", { class: "mini", onclick: () => evOpenChart(i, idx, e.date) }, "看走勢"));
    if (e.kind === "fomc") {
      const box = h("div", { class: "fomc-box hidden" });
      acts.append(h("button", { class: "mini", title: "聲明稿措辭變化、AI 解讀、經濟預測（SEP）、會議紀要", onclick: () => {
        box.classList.toggle("hidden");
        if (!box.dataset.loaded) { box.dataset.loaded = "1"; fomcDocs(box, e.date); }
      } }, "📄 聲明／紀要／預測"));
      main.append(box);
    }
    if (["move", "fomc", "cbc", "history"].includes(e.kind))
      acts.append(h("button", { class: "mini", title: "重抓當時新聞，讓本機 Ollama 重新命名（你改過的標題不會被覆蓋）", onclick: async (ev) => {
        ev.target.disabled = true; ev.target.textContent = "AI 撰寫中…";
        try { await api(`/api/events/ai/${encodeURIComponent(e.id)}`, { method: "POST" }); } catch (x) { flash(x.message); }
        S.panes[i].evFocus = e.id; renderPane(i);
      } }, e.kind === "history" ? "重抓新聞" : "AI 重寫"));
    acts.append(h("button", { class: "mini", onclick: () => evEdit(i, card, e) }, "編輯"));
    acts.append(h("button", { class: "mini danger", onclick: async () => {
      try { await api(`/api/events/${encodeURIComponent(e.id)}`, { method: "DELETE" }); } catch (x) { flash(x.message); }
      renderPane(i);
    } }, e.kind === "manual" ? "刪除" : "隱藏"));
    main.append(acts);
  }
  card.append(main);
  return card;
}

/* ---------- FOMC 文件 */
const sepFmt = (v) => (v == null ? "—" : Number(v).toFixed(1));

function sepTable(sep, labels) {
  const t = h("table", { class: "grid sep-tbl" });
  t.innerHTML = `<thead><tr><th>中位數（%）</th>${sep.years.map((y) => `<th>${y === "Longer run" ? "長期" : y}</th>`).join("")}</tr></thead>`;
  const tb = h("tbody");
  for (const [k, zh] of Object.entries(labels)) {
    const m = sep.median[k]; if (!m) continue;
    const pv = (sep.previous || {})[k] || [];
    tb.append(h("tr", {}, h("td", { class: "nm" }, zh), ...m.map((v, j) => {
      const d = v != null && pv[j] != null ? v - pv[j] : null;
      return h("td", { title: pv[j] != null ? `前次 ${sepFmt(pv[j])}` : "" }, sepFmt(v),
        d ? h("small", { class: d > 0 ? "up" : "down" }, ` ${d > 0 ? "▲" : "▼"}${Math.abs(d).toFixed(1)}`) : null);
    })));
  }
  t.append(tb);
  return t;
}

async function fomcDocs(box, day) {
  box.innerHTML = `<div class="loading">讀取 federalreserve.gov 文件</div>`;
  let d;
  try { d = await api(`/api/fomc/${day}`); } catch (e) { box.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  box.innerHTML = "";
  if (d.upcoming || !d.statement) { box.append(h("div", { class: "muted" }, d.upcoming ? "會議還沒開；聲明在決策日美東 14:00（台灣隔天凌晨 2:00，夏令 3:00）發布。" : "聲明稿暫時抓不到，稍後再試。")); return; }
  const aiBox = (kind, label) => {
    const out = h("div", { class: "fomc-ai" });
    const n = d.notes && d.notes[kind];
    if (n) out.innerHTML = `<div class="md">${md(n.text)}</div><div class="muted">${esc(n.model || "")}・${esc((n.at || "").replace("T", " "))}</div>`;
    const b = h("button", { class: "mini", onclick: async () => {
      b.disabled = true; b.textContent = "本機 Ollama 撰寫中…";
      try { const r = await api(`/api/fomc/${day}/ai?kind=${kind}&force=${n ? "true" : "false"}`, { method: "POST" }); out.innerHTML = `<div class="md">${md(r.text)}</div>`; }
      catch (x) { out.innerHTML = `<div class="errbox">${esc(x.message)}</div>`; }
      b.disabled = false; b.textContent = label;
    } }, n ? "重寫 " + label : label);
    return h("div", {}, b, out);
  };
  // statement diff
  const sec1 = h("section", { class: "fomc-sec" }, h("h5", {}, "聲明稿",
    h("a", { href: d.statement.url, target: "_blank", rel: "noopener noreferrer", class: "muted" }, "　原文 ↗"),
    h("span", { class: "muted" }, d.previous_date ? `　相對 ${d.previous_date}：${d.changes} 處變化（綠＝新增、紅刪除線＝刪除）` : "")));
  const body = h("div", { class: "fomc-diff" });
  for (const x of d.diff || d.statement.text.split("\n\n").map((t) => ({ op: "same", text: t })))
    body.append(h("span", { class: `fd-${x.op}` }, x.text + " "));
  sec1.append(body, aiBox("statement", "🤖 AI 解讀措辭變化"));
  box.append(sec1);
  if (d.sep) {
    box.append(h("section", { class: "fomc-sec" }, h("h5", {}, "經濟預測摘要（SEP）",
      h("a", { href: d.sep.url, target: "_blank", rel: "noopener noreferrer", class: "muted" }, "　原文 ↗"),
      h("span", { class: "muted" }, d.previous_sep_date ? `　▲▼ 相對 ${d.previous_sep_date}` : "")), sepTable(d.sep, d.labels)));
  }
  const sec3 = h("section", { class: "fomc-sec" }, h("h5", {}, "會議紀要"));
  if (d.minutes) {
    sec3.append(h("div", { class: "muted" }, h("a", { href: d.minutes.url, target: "_blank", rel: "noopener noreferrer" }, "原文 ↗"), `　約 ${nf(d.minutes.chars / 1000, 0)}k 字元`),
      aiBox("minutes", "🤖 AI 摘要紀要"));
  } else sec3.append(h("div", { class: "muted" }, `會後約三週發布（預計 ${d.minutes_due} 前後），背景會自動抓。`));
  box.append(sec3);
}

function evNewsLi(n) {
  return h("li", {}, n.link ? h("a", { href: n.link, target: "_blank", rel: "noopener noreferrer" }, n.title) : n.title,
    h("span", { class: "muted" }, n.publisher ? `　${n.publisher}` : ""));
}

function evOpenChart(i, sym, date) {
  const age = (Date.now() - new Date(date)) / 86400000;
  setPane(otherPane(i, "GP"), { sec: sym, fn: "GP", range: age > 330 ? "2y" : "1y", mev: true, focusDate: date });
}

function evEdit(i, card, e) {
  const t = h("input", { value: e.title_final, maxlength: "40", placeholder: "事件名稱" });
  const s = h("textarea", { rows: "3", placeholder: "說明（選填）" }, e.summary_final || "");
  const msg = h("span", { class: "hold-msg" });
  const f = h("form", { class: "ev-edit" }, t, s, h("div", {}, h("button", { class: "btn", type: "submit" }, "儲存"),
    h("button", { class: "btn ghost", type: "button", onclick: () => renderPane(i) }, "取消"), msg));
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await api(`/api/events/${encodeURIComponent(e.id)}`, { method: "PUT", json: { title: t.value, summary: s.value } }); S.panes[i].evFocus = e.id; renderPane(i); }
    catch (x) { msg.textContent = x.message; }
  });
  $(".ev-main", card).replaceChildren(f);
  t.focus();
}

function evAddForm(i) {
  const d = h("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
  const t = h("input", { placeholder: "事件名稱，例：川普宣布對等關稅", maxlength: "40" });
  const s = h("input", { placeholder: "說明（選填）" });
  const msg = h("span", { class: "hold-msg" });
  const f = h("form", { class: "hold-form ev-add hidden" }, d, t, s, h("button", { class: "btn", type: "submit" }, "新增"), msg,
    h("div", { class: "muted hold-hint" }, "自訂事件會出現在所有指數走勢圖上（黃色圓點），也可以標未來的日子（法說會、除息、選舉）。"));
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { const r = await api("/api/events", { method: "POST", json: { date: d.value, title: t.value, summary: s.value } }); S.panes[i].evFocus = r.id; S.panes[i].evKind = ""; renderPane(i); }
    catch (x) { msg.textContent = x.message; }
  });
  return f;
}


/* ---------- 分析紀錄與檢討 */
const LG_ENG = ["tradingagents", "hedgefund", "fingpt", "timesfm", "laya", "qlib", "finrl", "research"];
const LG_LLM = ["tradingagents", "hedgefund", "fingpt"];

async function renderLedger(i, body, p) {
  body.classList.add("lg-body");
  const eng = p.lgEng || "";
  const bar = h("div", { class: "ranges" });
  bar.append(h("button", { class: !eng ? "on" : "", onclick: () => setPane(i, { lgEng: "" }) }, "全部"));
  const tickIn = h("input", { class: "lg-q", placeholder: "標的：2330、台積電", value: p.lgQ || "" });
  tickIn.addEventListener("keydown", (e) => { if (e.key === "Enter") setPane(i, { lgQ: tickIn.value.trim() }); });
  const engBox = h("span", { class: "lg-engs" });
  bar.append(engBox, tickIn, h("span", { class: "grow" }),
    h("button", { title: "立刻收集新完成的分析，並核對已到期的", onclick: async (e) => {
      e.target.disabled = true; e.target.textContent = "核對中…";
      try { const r = await api("/api/ledger/refresh", { method: "POST" }); flash(`新增 ${r.added} 筆、核對 ${r.checked} 筆`); } catch (x) { flash(x.message); }
      renderPane(i); } }, "↻ 收集＋核對"));
  body.append(bar, h("div", { class: "lg-rule muted" },
    "核對方式：分析當天收盤 → 5／20／60 個交易日後收盤。看多漲了、看空跌了、中性漲跌在 2／4／7% 內算對；超額＝同期扣掉大盤（台股 0050、其他 S&P 500），依看多看空方向計。TimesFM 看到期日有沒有落在 80% 區間。不含交易成本。"));
  const holder = h("div", {}, h("div", { class: "loading" }, "讀取分析紀錄"));
  body.append(holder);
  let d;
  try { d = await api(`/api/ledger?engine=${eng}&ticker=${encodeURIComponent(p.lgQ || "")}`); }
  catch (e) { holder.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (S.panes[i].fn !== "LEDGER") return;
  for (const e of LG_ENG.filter((x) => d.stats[x])) engBox.append(h("button", { class: eng === e ? "on" : "", onclick: () => setPane(i, { lgEng: e }) }, d.stats[e].name));
  holder.innerHTML = "";

  // 各 agent 成績單
  const cards = h("div", { class: "lg-cards" });
  for (const e of LG_ENG.filter((x) => d.stats[x] && (!eng || x === eng))) {
    const s = d.stats[e], rv = d.latest[e];
    const row = (h0) => {
      const x = s.h[h0];
      return h("div", { class: "lg-h" }, h("span", { class: "muted" }, `${h0} 日`),
        x && x.n ? h("b", { class: x.rate >= 55 ? "up" : x.rate < 45 ? "down" : "" }, `${Math.round(x.rate)}%`) : h("b", { class: "muted" }, "—"),
        h("span", { class: "muted" }, x && x.n ? `${x.hits}/${x.n}` : "未到期"),
        x && x.excess != null ? h("span", { class: cls(x.excess), title: "依看多看空方向計的平均超額報酬" }, `超額 ${pct(x.excess)}`) : h("span", {}));
    };
    const card = h("section", { class: "lg-card" },
      h("div", { class: "lg-card-h" }, h("b", {}, s.name), h("span", { class: "muted" }, `${s.n} 次分析`)),
      ...(["qlib", "finrl", "research"].includes(e) ? [h("div", { class: "muted lg-note" }, "選股／回測類：只記錄，不做方向核對")] : ["5", "20", "60"].map(row)),
      s.band ? h("div", { class: "lg-h" }, h("span", { class: "muted" }, "80% 區間"), h("b", {}, `${Math.round(s.band.rate)}%`),
        h("span", { class: "muted" }, `${s.band.in}/${s.band.n}`), h("span", { class: "muted" }, `誤差 ${nf(s.band.abs_err, 1)}%`)) : null);
    const acts = h("div", { class: "lg-acts" });
    if (!["qlib", "finrl", "research"].includes(e))
      acts.append(h("button", { class: "mini", title: "讓本機 Ollama 根據已核對的結果寫檢討，並整理成給這個 agent 的提醒", onclick: async (ev) => {
        ev.target.disabled = true; ev.target.textContent = "AI 檢討中（1–3 分鐘）…";
        try { await api(`/api/ledger/review/${e}`, { method: "POST" }); S.panes[i].lgOpen = e; } catch (x) { flash(x.message); }
        renderPane(i); } }, "🤖 AI 檢討"));
    if (rv) acts.append(h("button", { class: "mini" + (p.lgOpen === e ? " on" : ""), onclick: () => setPane(i, { lgOpen: p.lgOpen === e ? "" : e }) }, `看檢討（${rv.created.slice(5, 10)}）`));
    if (LG_LLM.includes(e)) {
      const cb = h("input", { type: "checkbox" });
      cb.checked = d.feedback[e] !== false;
      cb.addEventListener("change", async () => { try { await api("/api/ledger/feedback", { method: "PUT", json: { [e]: cb.checked } }); flash(cb.checked ? `${s.name} 下次分析會帶入最新檢討` : `${s.name} 不再帶入檢討`); } catch (x) { flash(x.message); } });
      acts.append(h("label", { class: "lg-fb", title: "下次分析時，把最新檢討的「給下次的提醒」放進這個 agent 的提示詞" }, cb, " 下次帶入檢討"));
    }
    card.append(acts);
    cards.append(card);
  }
  if (!cards.childElementCount) cards.append(h("div", { class: "empty" }, "還沒有完成的 AI 分析。到右側跑一次 TradingAgents、投資大師、FinGPT、TimesFM 或 Laya，完成後約 30 分鐘內會出現在這裡（或按「↻ 收集＋核對」）。"));
  holder.append(cards);

  // 檢討內容
  if (p.lgOpen && d.latest[p.lgOpen]) {
    const rv = d.latest[p.lgOpen];
    const box = h("section", { class: "lg-review" },
      h("h4", {}, `${rv.engine_name} 檢討 · ${rv.created.replace("T", " ").slice(0, 16)} · ${rv.n} 筆（${rv.period_from} ～ ${rv.period_to}）· ${rv.model || ""}`),
      h("div", { class: "md", html: md(rv.summary) }),
      rv.patterns.length ? h("div", {}, h("b", {}, "觀察到的模式"), h("ul", {}, rv.patterns.map((x) => h("li", {}, x)))) : null,
      rv.lessons.length ? h("div", {}, h("b", { class: "up" }, "給下次分析的提醒" + (LG_LLM.includes(rv.engine) ? (d.feedback[rv.engine] !== false ? "（會帶入下次分析）" : "（目前不帶入）") : "")),
        h("ul", {}, rv.lessons.map((x) => h("li", {}, x)))) : null,
      rv.note ? h("div", { class: "muted" }, `已存到 Obsidian：${rv.note}`) : null,
      h("button", { class: "mini", onclick: async (ev) => {
        const hist = await api(`/api/ledger/reviews?engine=${rv.engine}`);
        ev.target.replaceWith(h("div", { class: "lg-hist" }, hist.slice(1).map((x) => h("details", {}, h("summary", {}, `${x.created.replace("T", " ").slice(0, 16)} · ${x.n} 筆`),
          h("div", { class: "md", html: md(x.summary) }), h("ul", {}, x.lessons.map((y) => h("li", {}, y))))), hist.length < 2 ? h("span", { class: "muted" }, "沒有更早的檢討") : null));
      } }, "更早的檢討"));
    holder.append(box);
  }

  // 明細
  const tbl = h("table", { class: "grid lg-tbl" });
  tbl.innerHTML = `<thead><tr><th>分析時間</th><th style="text-align:left">Agent／模型</th><th style="text-align:left">標的</th><th style="text-align:left">結論</th><th>當時</th><th>5 日</th><th>20 日</th><th>60 日</th><th></th></tr></thead>`;
  const tb = h("tbody");
  const cell = (r, hk) => {
    if (!r.scored || !r.ticker) return h("td", { class: "muted" }, "—");
    if (hk === "fc") {
      const c = r.checks["0"];
      if (!c) return h("td", { class: "muted" }, `⏳ ${r.fc.horizon_end.slice(5)}`);
      return h("td", { title: `到期 ${c.due} 收 ${nf(c.price, 2)}；中位數誤差 ${pct(c.err, 1)}` }, c.in_band ? "✅ 區間內" : "❌ 區間外", h("small", { class: cls(c.ret) }, ` ${pct(c.ret, 1)}`));
    }
    const c = r.checks[hk];
    if (!c) return h("td", { class: "muted", title: "預計到期日" }, r.due_est && r.due_est[hk] ? `⏳ ${r.due_est[hk].slice(5)}` : "—");
    const mark = c.hit == null ? "" : c.hit ? "✅" : "❌";
    return h("td", { title: `${c.due} 收 ${nf(c.price, 2)}${c.bench != null ? `；大盤 ${pct(c.bench, 1)}` : ""}${c.excess != null ? `；超額 ${pct(c.excess, 1)}` : ""}` },
      mark, h("span", { class: cls(c.ret) }, ` ${pct(c.ret, 1)}`));
  };
  for (const r of d.records) {
    const t = String(r.created || "").replace("T", " ").slice(0, 16);
    tb.append(h("tr", {},
      h("td", {}, t), h("td", { class: "nm" }, r.engine_name, h("small", { class: "muted" }, ` ${r.model || ""}`), r.lessons_used ? h("span", { class: "tag", title: "這次分析有帶入過去檢討" }, "檢討") : null),
      h("td", { class: "nm click", onclick: () => r.ticker && quickOpen(r.ticker, "GP") }, r.ticker ? `${r.ticker.replace(/\.TWO?$/, "")} ${r.name || ""}` : h("span", { class: "muted" }, "（選股／回測）")),
      h("td", { class: "nm", title: r.summary || "" }, clip(String(r.signal || "—"), 18)),
      h("td", {}, r.price_at != null ? nf(r.price_at, pxd(r.price_at)) : "—"),
      ...(r.fc ? [cell(r, "fc"), h("td", { class: "muted" }, ""), h("td", { class: "muted" }, "")] : ["5", "20", "60"].map((k) => cell(r, k))),
      h("td", {}, h("button", { class: "mini", onclick: () => { openSide(r.engine); setTimeout(() => showJob(r.engine, r.job_id), 300); } }, "報告"))));
  }
  tbl.append(tb);
  if (d.records.length) holder.append(h("div", { class: "hold-scroll" }, tbl));
}



/* ---------- 美國總經（FRED） */
function sparkSvg(vals, w = 110, hgt = 26) {
  if (!vals || vals.length < 2) return "";
  const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
  const pts = vals.map((v, k) => `${(k / (vals.length - 1) * w).toFixed(1)},${(hgt - 2 - (v - mn) / r * (hgt - 4)).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  return `<svg width="${w}" height="${hgt}" viewBox="0 0 ${w} ${hgt}"><polyline points="${pts}" fill="none" stroke="${up ? "var(--amber)" : "var(--cyan)"}" stroke-width="1.3"/></svg>`;
}
const fmtMacro = (v, r) => (v == null ? "—" : Math.abs(v) >= 1000 ? nf(v, 0) : nf(v, 2)) + (r.unit === "%" ? "%" : "");

async function renderMacro(i, body, p) {
  body.classList.add("mc-body");
  const chartBox = h("div", { class: "mc-chart hidden" });
  const holder = h("div", {}, h("div", { class: "loading" }, "讀取 FRED 總經數據"));
  body.append(h("div", { class: "ranges" }, h("span", { class: "legend", style: "margin-left:0" }, "資料：FRED（St. Louis Fed）。年增率由原始指數計算；點一列看走勢"),
    h("span", { class: "grow" }), h("button", { onclick: () => { setFocus(i); askSend("根據目前的美國總經數據（Fed 利率、殖利率曲線、通膨、就業、景氣、金融情勢），整理現在的景氣位置與對股市的意涵，並指出最值得注意的變化。"); } }, "問 AI 總經 ▸")), chartBox, holder);
  let d;
  try { d = await api("/api/macro"); } catch (e) { holder.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (S.panes[i].fn !== "MACRO") return;
  holder.innerHTML = "";
  const sepBox = h("section", { class: "fomc-sec mc-sep" });
  holder.append(sepBox);
  api("/api/fomc/sep/latest").then((sep) => {
    sepBox.append(h("h5", {}, `Fed 經濟預測中位數（SEP ${sep.date}）`,
      h("a", { href: sep.url, target: "_blank", rel: "noopener noreferrer", class: "muted" }, "　原文 ↗"),
      h("span", { class: "muted" }, "　▲▼ 相對前一次預測；聲明措辭變化看「大事」頁的 Fed 事件")), sepTable(sep, sep.labels));
  }).catch(() => sepBox.remove());
  const bad = d.rows.filter((r) => r.error);
  if (bad.length) {  // one explanation at the top instead of the same long message on every row
    const why = bad[0].error.replace(/（10 分鐘內不重試）/, "");
    holder.append(h("div", { class: "errbox" }, `${bad.length === d.rows.length ? "FRED 目前抓不到資料" : `${bad.length} 個序列抓不到`}：${why}`));
  }
  for (const g of d.groups) {
    const rows = d.rows.filter((r) => r.group === g);
    if (!rows.length) continue;
    const t = h("table", { class: "grid mc-tbl" });
    t.innerHTML = `<thead><tr><th>${esc(g)}</th><th>最新</th><th>日期</th><th>前期</th><th>變動</th><th>一年前</th><th>近 2 年</th></tr></thead>`;
    const tb = h("tbody");
    for (const r of rows) {
      if (r.error) { tb.append(h("tr", {}, h("td", { class: "nm" }, r.name), h("td", { class: "muted", colspan: "6", title: r.error }, "— 抓取失敗"))); continue; }
      tb.append(h("tr", { class: "click" + (p.fredId === r.id ? " hl" : ""), title: `${r.id}${r.note ? "：" + r.note : ""}`, onclick: () => setPane(i, { fredId: p.fredId === r.id ? "" : r.id }) },
        h("td", { class: "nm" }, r.name, h("small", { class: "muted" }, ` ${r.id}`)), h("td", {}, h("b", {}, fmtMacro(r.value, r))), h("td", { class: "muted" }, r.date),
        h("td", {}, fmtMacro(r.prev, r)), h("td", { class: cls(r.chg) }, r.chg == null ? "—" : (r.chg > 0 ? "+" : "") + (Math.abs(r.chg) >= 1000 ? nf(r.chg, 0) : nf(r.chg, 2))),
        h("td", {}, fmtMacro(r.y1, r)), h("td", { html: sparkSvg(r.spark) })));
    }
    t.append(tb);
    holder.append(t);
  }
  if (p.fredId) macroChart(i, chartBox, p.fredId, p);
}

async function macroChart(i, box, id, p) {
  box.classList.remove("hidden");
  const yrs = p.fredYears || (id === "NETLIQ" ? 5 : 10);
  const spxOn = p.fredSpx === undefined || p.fredSpx === null ? id === "NETLIQ" : !!p.fredSpx;
  const bar = h("div", { class: "ranges" });
  const area = h("div", { class: "mc-area" });
  box.append(bar, area);
  let d;
  try { d = await api(`/api/macro/series?id=${encodeURIComponent(id)}&years=${yrs}`); } catch (e) { area.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  bar.append(h("b", { style: "color:var(--amber)" }, `${d.name}（${id}）`), h("span", { class: "muted" }, d.note ? `　${d.note}` : ""),
    h("span", { class: "grow" }), ...[[2, "2 年"], [5, "5 年"], [10, "10 年"], [30, "30 年"], [300, "全部"]].map(([y, l]) =>
      h("button", { class: yrs === y ? "on" : "", onclick: () => setPane(i, { fredYears: y }) }, l)),
    h("button", { class: p.fredFed ? "on mev-btn" : "mev-btn", title: "標出 Fed 利率決策（升息／降息）", onclick: () => setPane(i, { fredFed: !p.fredFed }) }, "📌 Fed"),
    h("button", { class: spxOn ? "on" : "", title: "在左軸疊上 S&P 500 對照", onclick: () => setPane(i, { fredSpx: !spxOn }) }, "疊 S&P 500"),
    h("button", { onclick: () => setPane(i, { fredId: "" }) }, "✕"));
  if (!d.points.length) { area.innerHTML = `<div class="empty">沒有資料</div>`; return; }
  requestAnimationFrame(async () => {
    const chart = LightweightCharts.createChart(area, { ...chartTheme(), autoSize: true });
    const s = chart.addLineSeries({ color: cssVar("--amber"), lineWidth: 2, priceFormat: { type: "price", precision: 2, minMove: 0.01 } });
    s.setData(d.points);
    if (["T10Y2Y", "T10Y3M"].includes(id) || d.how !== "level") s.createPriceLine({ price: 0, color: "#5b636b", lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    if (spxOn) {
      try {
        const rng = yrs <= 2 ? "2y" : yrs <= 5 ? "5y" : yrs <= 10 ? "10y" : "max";
        const spx = await api(`/api/history?s=%5EGSPC&range=${rng}`);
        chart.applyOptions({ leftPriceScale: { visible: true, borderColor: "#2a2f35" } });
        const l = chart.addLineSeries({ color: "#4ba3ff", lineWidth: 1, priceScaleId: "left", priceLineVisible: false, title: "S&P 500" });
        const t0 = d.points[0].time;
        l.setData(spx.filter((x) => x.time >= t0).map((x) => ({ time: x.time, value: x.close })));
      } catch (e) {}
    }
    chart.timeScale().fitContent();
    S.charts.set(i, { chart, fit: () => chart.timeScale().fitContent() });
    if (p.fredFed) {
      try {
        const evs = await api(`/api/events?since=${d.points[0].time}&kinds=fomc`);
        const times = d.points.map((x) => x.time);
        const mk = evs.events.filter((e) => !e.upcoming && /升息|降息/.test(e.title_final)).map((e) => {
          const t = times.find((x) => x >= e.date); const cut = /降息/.test(e.title_final);
          return t && { time: t, position: cut ? "belowBar" : "aboveBar", color: cut ? "#3ad6d6" : "#ff8a3d", shape: cut ? "arrowDown" : "arrowUp", text: cut ? "降息" : "升息" };
        }).filter(Boolean).sort((a, b) => (a.time < b.time ? -1 : 1));
        s.setMarkers(mk);
      } catch (e) {}
    }
  });
}

/* ---------- 台幣匯率：1 外幣 = ? 台幣（漲＝外幣升值／台幣貶值） */
const fxFmt = (v) => (v == null ? "—" : v >= 100 ? nf(v, 2) : v >= 10 ? nf(v, 3) : v >= 1 ? nf(v, 4) : nf(v, 5));
const pctCell = (v) => h("td", { class: cls(v) }, v == null ? "—" : (v > 0 ? "+" : "") + nf(v, 2) + "%");

async function renderTWFX(i, body, p) {
  body.classList.add("mc-body");
  const chartBox = h("div", { class: "mc-chart hidden" });
  const holder = h("div", {}, h("div", { class: "loading" }, "讀取匯率"));
  const conv = h("div", { class: "fx-conv" });
  body.append(h("div", { class: "ranges" },
    h("span", { class: "legend", style: "margin-left:0" }, "1 外幣 = 台幣（日圓／韓元等以 100、1000 為單位）。紅＝外幣升值、台幣貶值；點一列看走勢"),
    h("span", { class: "grow" }),
    h("button", { onclick: () => { setFocus(i); askSend("根據目前新台幣對美元、日圓、人民幣、歐元等主要貨幣的匯率與近一年走勢，整理台幣強弱、可能原因，以及對台股與出口產業的意涵。"); } }, "問 AI 匯率 ▸")),
    conv, chartBox, holder);
  let d;
  try { d = await api("/api/twfx"); } catch (e) { holder.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (S.panes[i].fn !== "TWFX") return;
  holder.innerHTML = "";
  const rows = d.rows;
  const bot = (d.bot && d.bot.rates) || {};
  // selection by code or Chinese name ("匯率 日圓")
  const q = (p.fxSel || "").trim();
  const sel = q && (rows.find((r) => r.code === q.toUpperCase()) || rows.find((r) => r.name === q || r.name.includes(q) || q.includes(r.name)));
  // quick converter
  const ok = rows.filter((r) => r.twd);
  const amt = h("input", { class: "fx-amt", value: "1000", inputmode: "decimal" });
  const pick = h("select", {}, ...ok.map((r) => h("option", { value: r.code, ...(r.code === (sel ? sel.code : "USD") ? { selected: "" } : {}) }, `${r.flag} ${r.code} ${r.name}`)));
  const dir = h("select", {}, h("option", { value: "to" }, "外幣 → 台幣"), h("option", { value: "from" }, "台幣 → 外幣"));
  const out = h("b", { class: "fx-out" });
  const calc = () => {
    const r = ok.find((x) => x.code === pick.value); const a = parseFloat(amt.value.replace(/,/g, ""));
    if (!r || isNaN(a)) { out.textContent = "—"; return; }
    const b = bot[r.code];
    const mid = dir.value === "to" ? `${nf(a * r.twd, 2)} 台幣` : `${fxFmt(a / r.twd)} ${r.code}`;
    const bank = b && (dir.value === "to" ? (b.spot_buy || b.cash_buy) && `台銀買入 ${nf(a * (b.spot_buy || b.cash_buy), 2)} 台幣`
                                             : (b.cash_sell || b.spot_sell) && `台銀現金賣出 ${fxFmt(a / (b.cash_sell || b.spot_sell))} ${r.code}`);
    out.textContent = `＝ ${mid}（中價）${bank ? "　" + bank : ""}`;
  };
  [amt, pick, dir].forEach((x) => x.addEventListener("input", calc));
  conv.append(h("span", { class: "muted" }, "換算"), amt, pick, dir, out);
  calc();
  const bad = rows.filter((r) => r.error);
  if (bad.length) holder.append(h("div", { class: "errbox" }, `${bad.length} 個幣別抓不到：${bad.map((r) => r.code).join("、")}（${bad[0].error}）`));
  for (const g of d.regions) {
    const rs = rows.filter((r) => r.region === g && r.twd);
    if (!rs.length) continue;
    const t = h("table", { class: "grid mc-tbl fx-tbl" });
    t.innerHTML = `<thead><tr><th>${esc(g)}</th><th>台幣</th><th>1 台幣＝</th><th>日</th><th>1 月</th><th>3 月</th><th>1 年</th><th title="台灣銀行牌告：現金賣出／即期賣出（你買外幣的價格）">台銀賣出</th><th>近 1 年</th></tr></thead>`;
    const tb = h("tbody");
    for (const r of rs) {
      const u = r.unit || 1, b = bot[r.code];
      tb.append(h("tr", { class: "click" + (sel && sel.code === r.code ? " hl" : ""), title: `52 週 ${fxFmt(r.lo52 * u)}–${fxFmt(r.hi52 * u)}（${r.date}）`,
        onclick: () => setPane(i, { fxSel: sel && sel.code === r.code ? "" : r.code }) },
        h("td", { class: "nm" }, `${r.flag} ${r.name}`, h("small", { class: "muted" }, ` ${r.code}${u > 1 ? "×" + u : ""}`)),
        h("td", {}, h("b", {}, fxFmt(r.twd * u))), h("td", { class: "muted" }, fxFmt(r.per_twd)),
        pctCell(r.d1), pctCell(r.m1), pctCell(r.m3), pctCell(r.y1),
        h("td", { class: "muted" }, b ? `${b.cash_sell ? fxFmt(b.cash_sell * u) : "—"}／${b.spot_sell ? fxFmt(b.spot_sell * u) : "—"}` : ""),
        h("td", { html: sparkSvg(r.spark) })));
    }
    t.append(tb);
    holder.append(t);
  }
  holder.append(h("div", { class: "muted fx-src" }, `資料：Yahoo Finance 交叉匯率（中價，台幣＝USD/TWD ÷ USD/外幣）；${esc((d.bot && d.bot.source) || "台灣銀行牌告")}${d.bot && d.bot.error ? "（目前抓不到：" + esc(d.bot.error) + "）" : ""}。實際換匯以銀行為準。`));
  if (sel) fxChart(i, chartBox, sel.code, p);
}

async function fxChart(i, box, code, p) {
  box.classList.remove("hidden");
  const rng = p.fxRange || "1y";
  const evOn = p.fxEv !== false, fcOn = !!p.fxFc, hz = p.fxH || 60;
  const bar = h("div", { class: "ranges" });
  const legend = h("div", { class: "legend fx-legend" });
  const area = h("div", { class: "mc-area chart-box" });
  box.append(bar, legend, area);
  let d;
  try { d = await api(`/api/twfx/series?ccy=${code}&range=${rng}`); } catch (e) { area.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  const u = d.unit || 1;
  const first = d.points.length ? d.points[0].value : null, last = d.points.length ? d.points[d.points.length - 1].value : null;
  const chg = first && last ? (last / first - 1) * 100 : null;
  bar.append(h("b", { style: "color:var(--amber)" }, `${d.flag} ${u > 1 ? u + " " : ""}${d.name}（${code}）＝ ${fxFmt(last * u)} 台幣`),
    h("span", { class: cls(chg) }, chg == null ? "" : `　區間 ${chg > 0 ? "+" : ""}${nf(chg, 2)}%`),
    h("span", { class: "grow" }), ...[["1mo", "1 月"], ["3mo", "3 月"], ["6mo", "6 月"], ["1y", "1 年"], ["2y", "2 年"], ["5y", "5 年"], ["10y", "10 年"]].map(([k, l]) =>
      h("button", { class: rng === k ? "on" : "", onclick: () => setPane(i, { fxRange: k }) }, l)),
    h("button", { class: evOn ? "on mev-btn" : "mev-btn", title: "標出這個貨幣的異常波動日、Fed 與台灣央行利率決策、歷史大事", onclick: () => setPane(i, { fxEv: !evOn }) }, "📌 事件"),
    h("button", { class: fcOn ? "on" : "", title: "TimesFM 統計預測（只看過去匯率）", onclick: () => setPane(i, { fxFc: !fcOn }) }, "🔮 預測"),
    ...(fcOn ? [[20, "1 月"], [60, "3 月"], [120, "6 月"]].map(([n, l]) => h("button", { class: hz === n ? "on" : "", onclick: () => setPane(i, { fxH: n }) }, l)) : []));
  if (!d.points.length) { area.innerHTML = `<div class="empty">沒有資料</div>`; return; }
  requestAnimationFrame(async () => {
    const chart = LightweightCharts.createChart(area, { ...chartTheme(), autoSize: true });
    const prec = last * u >= 100 ? 2 : last * u >= 10 ? 3 : last * u >= 1 ? 4 : 5;
    const pf = { type: "price", precision: prec, minMove: 1 / 10 ** prec };
    const s = chart.addAreaSeries({ lineColor: cssVar("--amber"), topColor: "rgba(255,160,40,.25)", bottomColor: "rgba(255,160,40,0)", lineWidth: 2, priceFormat: pf });
    s.setData(d.points.map((x) => ({ time: x.time, value: x.value * u })));
    chart.timeScale().fitContent();
    S.charts.set(i, { chart, fit: () => chart.timeScale().fitContent() });
    const tasks = [];
    if (evOn) tasks.push(fxMarkers(i, chart, s, area, code, rng, d.points, legend));
    if (fcOn) tasks.push(fxForecast(chart, code, hz, u, pf, legend));
    await Promise.all(tasks);
  });
}

async function fxMarkers(i, chart, series, box, code, rng, pts, legend) {
  let evs;
  try { evs = await api(`/api/twfx/events?ccy=${code}&range=${rng}`); } catch (e) { return; }
  const times = pts.map((x) => x.time);
  const byTime = new Map();
  for (const e of evs) {
    const t = times.find((x) => x >= e.date);
    if (!t) continue;
    if (!byTime.has(t)) byTime.set(t, []);
    byTime.get(t).push(e);
  }
  const UP = cssVar("--up"), DN = cssVar("--down");
  const mk = [];
  for (const [t, es] of byTime) {
    const e = es.find((x) => x.kind === "fxmove") || es.find((x) => x.kind === "cbc") || es[0];
    const more = es.length > 1 ? ` +${es.length - 1}` : "";
    if (e.kind === "fxmove") mk.push({ time: t, position: e.dir > 0 ? "aboveBar" : "belowBar", color: e.dir > 0 ? UP : DN, shape: e.dir > 0 ? "arrowUp" : "arrowDown", text: clip(e.title, 9) + more });
    else if (e.kind === "cbc") mk.push({ time: t, position: "belowBar", color: "#3ad6d6", shape: "square", text: "央行" + more });
    else if (e.kind === "macro") mk.push({ time: t, position: "belowBar", color: "#ff9f43", shape: "square", text: (/CPI/.test(e.title) ? "CPI" : /非農/.test(e.title) ? "非農" : "PCE") + more });
    else if (e.kind === "fomc") mk.push({ time: t, position: "belowBar", color: "#b48cff", shape: "square", text: "Fed" + more });
    else mk.push({ time: t, position: "aboveBar", color: e.kind === "history" ? "#e6e6e6" : "#ffd166", shape: "circle", text: e.label ? clip(e.title.replace(/（.*?）/g, ""), 7) + more : "" });
  }
  mk.sort((a, b) => (a.time < b.time ? -1 : 1));
  // crowded stretches (e.g. 2025-04): keep one text label per ~15 trading days on each side of the line
  const lastAt = { aboveBar: -99, belowBar: -99 };
  for (const m of mk) {
    const k = times.indexOf(m.time);
    if (m.shape === "square") continue;  // Fed／央行 tags are short — always shown
    if (m.text && k - lastAt[m.position] < Math.max(8, Math.round(times.length / 40))) m.text = "";
    else if (m.text) lastAt[m.position] = k;
  }
  series.setMarkers(mk);
  const n = evs.filter((e) => e.kind === "fxmove").length;
  legend.prepend(h("span", { class: "muted" }, `📌 異常波動 ${n} 次・Fed／央行 ${evs.filter((e) => e.kind === "fomc" || e.kind === "cbc").length} 次　`));
  const tip = h("div", { class: "ev-tip hidden" });
  box.append(tip);
  chart.subscribeCrosshairMove((param) => {
    const es = param && param.time ? byTime.get(tStr(param.time)) : null;
    if (!es || !param.point) { tip.classList.add("hidden"); return; }
    tip.innerHTML = es.map((e) => `<div><b>${esc(e.date)} ${esc(e.title)}</b>${e.summary ? `<div class="muted">${esc(e.summary)}</div>` : ""}</div>`).join("")
      + (es.some((e) => e.kind !== "fxmove") ? `<div class="ev-hint">點一下看「大事」頁</div>` : "");
    tip.classList.remove("hidden");
    tip.style.left = Math.min(Math.max(8, param.point.x + 14), box.clientWidth - 300) + "px";
    tip.style.top = Math.max(8, param.point.y - 20) + "px";
  });
  chart.subscribeClick((param) => {
    const es = param && param.time ? (byTime.get(tStr(param.time)) || []).filter((e) => e.kind !== "fxmove") : [];
    if (!es.length) return;
    const el = paneEl(i);
    if (el && el.classList.contains("maximized")) toggleMax(i);
    setPane(otherPane(i, "EVENTS"), { fn: "EVENTS", evFocus: es[0].id });
  });
}

async function fxForecast(chart, code, hz, u, pf, legend) {
  const span = h("span", {}, "TimesFM 預測中…");
  legend.append(span);
  let d;
  try { d = await api(`/api/forecast?s=${encodeURIComponent("TWFX:" + code)}&h=${hz}`); }
  catch (e) { span.innerHTML = `<span class="down">預測失敗：${esc(e.message).slice(0, 80)}</span>`; return; }
  const k = (v) => v * u;
  const scaled = { ...d, last: k(d.last), forecast: d.forecast.map((x) => ({ ...x, median: k(x.median), p10: k(x.p10), p90: k(x.p90) })) };
  drawForecast(chart, scaled);
  const sm = d.summary;
  span.innerHTML = `🔮 ${esc(d.model)} ${d.horizon} 個交易日：中位數 <b class="${cls(sm.change_pct)}">${fxFmt(k(sm.median_end))}（${pct(sm.change_pct)}）</b>
    · 80% 區間 ${fxFmt(k(sm.p10_end))}–${fxFmt(k(sm.p90_end))} · 外幣升值機率 ${Math.round(sm.p_up * 100)}%`;
  span.title = "TimesFM 只根據過去匯率做統計預測，不含央行政策、資金流與新聞；色帶是 P10–P90（80%）區間。匯率接近隨機漫步，區間比中位數更有參考價值。";
}

boot();
