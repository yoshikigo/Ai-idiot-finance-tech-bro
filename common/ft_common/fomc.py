"""FOMC 文件：聲明稿、會議紀要、經濟預測摘要（SEP），從 federalreserve.gov 抓取並在本機保存。

URLs (date = the meeting's decision day, same as events.FOMC):
  statement   /newsevents/pressreleases/monetary{YYYYMMDD}a.htm      (2:00 p.m. ET on decision day)
  minutes     /monetarypolicy/fomcminutes{YYYYMMDD}.htm              (three weeks later)
  projections /monetarypolicy/fomcprojtabl{YYYYMMDD}.htm             (Mar / Jun / Sep / Dec meetings)

What the terminal does with them:
  * statement: full text + sentence-level diff against the previous meeting's statement (措辭變化) + vote line
  * minutes:   full text kept locally, AI summary on request
  * SEP:       median projections (GDP, unemployment, PCE, core PCE, fed funds) vs the previous SEP
  * AI 解讀：the local Ollama reads the diff (or the minutes) and writes a short Chinese note — stored, never re-asked
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("ft.fomc")
BASE = "https://www.federalreserve.gov"
SEP_MONTHS = (3, 6, 9, 12)
HEADERS = {"User-Agent": "fin-terminal (python-httpx)", "Accept": "text/html,*/*", "Accept-Language": "en"}
_lock = threading.Lock()
VARS = [("Change in real GDP", "gdp", "實質 GDP 成長"), ("Unemployment rate", "unemp", "失業率"),
        ("PCE inflation", "pce", "PCE 通膨"), ("Core PCE inflation", "core", "核心 PCE 通膨"),
        ("Federal funds rate", "ffr", "聯邦基金利率（中位數）")]


def url(day: str, kind: str) -> str:
    d = day.replace("-", "")
    return {"statement": f"{BASE}/newsevents/pressreleases/monetary{d}a.htm",
            "minutes": f"{BASE}/monetarypolicy/fomcminutes{d}.htm",
            "sep": f"{BASE}/monetarypolicy/fomcprojtabl{d}.htm"}[kind]


def has_sep(day: str) -> bool:
    return int(day[5:7]) in SEP_MONTHS


def _db() -> Path:
    return Path(os.environ.get("FT_DATA", "/data")) / "fomc.sqlite"


def _conn() -> sqlite3.Connection:
    p = _db()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=30)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS docs(date TEXT, kind TEXT, url TEXT, text TEXT, data TEXT, fetched TEXT, status TEXT,
        PRIMARY KEY(date, kind));
    CREATE TABLE IF NOT EXISTS notes(date TEXT, kind TEXT, model TEXT, text TEXT, at TEXT, PRIMARY KEY(date, kind));
    """)
    return c


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ parsing
def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def parse_statement(html: str) -> str:
    """Statement paragraphs (incl. the vote line), without page chrome, media contacts or the implementation-note link."""
    soup = _soup(html)
    root = soup.find(id="article") or soup.find("div", class_=re.compile(r"col-sm-8")) or soup.body or soup
    paras = []
    for p in root.find_all("p"):
        t = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if not t or len(t) < 25:
            continue
        if t.startswith(("For media inquiries", "Implementation Note", "Last Update", "For release at", "Share")):
            continue
        if re.match(r"^(Release Date|Board of Governors)", t):
            continue
        paras.append(t)
    return "\n\n".join(paras)


def parse_minutes(html: str) -> str:
    soup = _soup(html)
    root = soup.find(id="article") or soup.find("div", class_=re.compile(r"col-sm-8")) or soup.body or soup
    out = []
    for el in root.find_all(["h4", "h5", "p", "strong"]):
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if t and (el.name != "strong" or len(t) < 120) and t not in out[-1:]:
            out.append(t)
    return "\n\n".join(out)


def _cell_num(t: str):
    t = t.replace("–", "-").replace("−", "-").strip()
    try:
        return float(t)
    except ValueError:
        return None


def parse_sep(html: str) -> dict:
    """Median projections from Table 1, plus the previous SEP's medians (the "xxx projection" rows)."""
    soup = _soup(html)
    table = next((t for t in soup.find_all("table") if "Change in real GDP" in t.get_text()), None)
    if table is None:
        raise ValueError("找不到 SEP 表格")
    years: list[str] = []
    for tr in table.find_all("tr"):
        labs = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        ys = [x for x in labs if re.fullmatch(r"\d{4}|Longer run", x)]
        if len(ys) >= 3:
            years = []
            for y in ys:  # first group = medians; stop when the year sequence restarts
                if y in years:
                    break
                years.append(y)
            break
    if not years:
        raise ValueError("SEP 表格沒有年份欄")
    n = len(years)
    med, prev, prev_label, last = {}, {}, None, None
    for tr in table.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if not cells:
            continue
        name = re.sub(r"\s*\d+$", "", cells[0]).strip()  # footnote digits
        key = next((k for lab, k, _ in VARS if name.startswith(lab)), None)
        vals = [_cell_num(x) for x in cells[1:1 + n]]
        if key:
            med[key] = vals
            last = key
        elif last and "projection" in name.lower():
            prev[last] = vals
            prev_label = name
            last = None
    if not med:
        raise ValueError("SEP 表格解析不到中位數")
    return {"years": years, "median": med, "previous": prev, "previous_label": prev_label}


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z])", text.replace("\n\n", " "))
    return [p.strip() for p in parts if len(p.strip()) > 3]


def diff(old: str, new: str) -> list[dict]:
    """Sentence-level change list: kept / added / removed, in the new statement's order."""
    a, b = sentences(old), sentences(new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out += [{"op": "same", "text": s} for s in b[j1:j2]]
        else:
            out += [{"op": "del", "text": s} for s in a[i1:i2]]
            out += [{"op": "add", "text": s} for s in b[j1:j2]]
    return out


# ------------------------------------------------------------------ fetching / storage
def _fake_html(u: str) -> str | None:
    m = re.search(r"(\d{8})", u)
    d = m.group(1) if m else "20260101"
    k = int(d[4:6]) + int(d[:4]) % 10  # varies a little per meeting so the diff has something to show
    if "fomcprojtabl" in u:
        yrs = [str(int(d[:4]) + i) for i in range(4)] + ["Longer run"]
        head = "<tr><th>Variable</th>" + "".join(f"<th>{y}</th>" for y in yrs * 3) + "</tr>"
        rows = "".join(f"<tr><td>{lab}</td>" + "".join(f"<td>{v:.1f}</td>" for v in vals * 3) + "</tr>"
                       "<tr><td>June projection</td>" + "".join(f"<td>{v - .1:.1f}</td>" for v in vals * 3) + "</tr>"
                       for lab, vals in (("Change in real GDP", [2.1, 2.0, 1.9, 1.8, 1.8]), ("Unemployment rate", [4.3, 4.4, 4.3, 4.2, 4.2]),
                                         ("PCE inflation", [2.8, 2.5, 2.2, 2.0, 2.0]), ("Core PCE inflation", [2.9, 2.6, 2.2, 2.0, 2.0]),
                                         ("Federal funds rate", [3.9, 3.6, 3.4, 3.1, 3.0])))
        return f"<html><body><div id='article'><table>{head}{rows}</table></div></body></html>"
    if "fomcminutes" in u:
        return None
    infl = "Inflation has moved up and remains somewhat elevated." if k % 2 else "Inflation remains somewhat elevated."
    return ("<html><body><div id='article'>"
            "<p>Recent indicators suggest that economic activity has continued to expand at a solid pace. "
            f"The unemployment rate has edged up but remains low. {infl}</p>"
            "<p>The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. "
            f"Uncertainty about the economic outlook {'has increased' if k % 3 else 'remains elevated'}.</p>"
            "<p>In support of its goals, the Committee decided to maintain the target range for the federal funds rate.</p>"
            "<p>Voting for the monetary policy action were Jerome H. Powell, Chair; and the other members (demo).</p>"
            "<p>For media inquiries, please email media@example.com</p></div></body></html>")


def _fetch(u: str) -> str | None:
    from . import market
    from .twhttp import fetch
    import httpx
    if market.FAKE:
        return _fake_html(u)
    try:
        return fetch(u, timeout=40, headers=HEADERS).decode("utf-8", "replace")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


def get(day: str, kind: str, refresh: bool = False) -> dict | None:
    """Stored document (fetching it once it should exist). None = not published (yet)."""
    with _conn() as c:
        r = c.execute("SELECT url, text, data, fetched, status FROM docs WHERE date=? AND kind=?", (day, kind)).fetchone()
    if r and r[4] == "ok" and not refresh:
        return {"date": day, "kind": kind, "url": r[0], "text": r[1], "data": json.loads(r[2]) if r[2] else None, "fetched": r[3]}
    today = dt.date.today().isoformat()
    if kind == "sep" and not has_sep(day):
        return None
    due = day if kind != "minutes" else (dt.date.fromisoformat(day) + dt.timedelta(days=20)).isoformat()
    if due > today:
        return None
    if r and r[4] == "missing" and r[3] and (dt.datetime.now() - dt.datetime.fromisoformat(r[3])).total_seconds() < 6 * 3600:
        return None  # checked recently
    u = url(day, kind)
    html = _fetch(u)
    if html is None:
        with _lock, _conn() as c:
            c.execute("INSERT OR REPLACE INTO docs(date,kind,url,fetched,status) VALUES(?,?,?,?,?)", (day, kind, u, _now(), "missing"))
        return None
    text, data = "", None
    if kind == "statement":
        text = parse_statement(html)
    elif kind == "minutes":
        text = parse_minutes(html)
    else:
        data = parse_sep(html)
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO docs(date,kind,url,text,data,fetched,status) VALUES(?,?,?,?,?,?,?)",
                  (day, kind, u, text, json.dumps(data) if data else None, _now(), "ok"))
    return {"date": day, "kind": kind, "url": u, "text": text, "data": data, "fetched": _now()}


def meetings() -> list[str]:
    from . import events
    return sorted(events.FOMC)


def previous(day: str, kind: str = "statement") -> str | None:
    ms = [m for m in meetings() if m < day]
    if kind == "sep":
        ms = [m for m in ms if has_sep(m)]
    return ms[-1] if ms else None


def note(day: str, kind: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT model, text, at FROM notes WHERE date=? AND kind=?", (day, kind)).fetchone()
    return {"model": r[0], "text": r[1], "at": r[2]} if r else None


def bundle(day: str) -> dict:
    """Everything the 大事 page shows for one meeting."""
    if day not in meetings():
        raise KeyError(day)
    st = get(day, "statement")
    pd_ = previous(day)
    prev_st = get(pd_, "statement") if pd_ and st else None
    sep = get(day, "sep") if has_sep(day) else None
    psep = previous(day, "sep")
    mins = get(day, "minutes")
    out = {"date": day, "statement": st and {"url": st["url"], "text": st["text"]},
           "previous_date": pd_ if prev_st else None,
           "diff": diff(prev_st["text"], st["text"]) if st and prev_st else None,
           "sep": sep and {"url": sep["url"], **sep["data"]}, "previous_sep_date": psep if sep else None,
           "minutes": mins and {"url": mins["url"], "chars": len(mins["text"] or ""), "excerpt": (mins["text"] or "")[:1200]},
           "minutes_due": (dt.date.fromisoformat(day) + dt.timedelta(days=21)).isoformat(),
           "notes": {k: note(day, k) for k in ("statement", "minutes")},
           "upcoming": day > dt.date.today().isoformat()}
    out["changes"] = sum(1 for x in out["diff"] or [] if x["op"] != "same")
    return out


STATEMENT_SYSTEM = ("你是熟悉 Fed 的總經分析師。下面是這次 FOMC 聲明相對上次的逐句差異（- 刪除、+ 新增）與本次全文。"
                    "用繁體中文條列：1) 利率決策與投票（有無異議）；2) 措辭有哪些實質變化、代表偏鷹還是偏鴿；"
                    "3) 對美債殖利率、美元、美股與台股可能的影響。只根據提供的文字，不要編造數字；最後一句註明不構成投資建議。300 字內。")
MINUTES_SYSTEM = ("你是熟悉 Fed 的總經分析師。下面是 FOMC 會議紀要。用繁體中文條列：委員對經濟、就業、通膨的看法；"
                  "對未來利率路徑的分歧（多少人偏升息／降息／按兵不動）；資產負債表；風險。只根據紀要內容，400 字內。")


def ai(day: str, kind: str, llm: dict, force: bool = False) -> dict:
    from . import ollama
    if not force and (n := note(day, kind)):
        return n
    if kind == "statement":
        b = bundle(day)
        if not b["statement"]:
            raise ValueError("這次會議的聲明還沒發布")
        lines = [("- " if x["op"] == "del" else "+ ") + x["text"] for x in (b["diff"] or []) if x["op"] != "same"]
        user = (f"會議日期：{day}（上次 {b['previous_date'] or '—'}）\n【差異】\n" + ("\n".join(lines) or "（與上次相同）")
                + f"\n\n【本次全文】\n{b['statement']['text']}")
        system = STATEMENT_SYSTEM
    elif kind == "minutes":
        m = get(day, "minutes")
        if not m:
            raise ValueError("會議紀要還沒發布（會後約三週）")
        user, system = m["text"][:45000], MINUTES_SYSTEM
    else:
        raise KeyError(kind)
    text = ollama.ask(llm, system, user)
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO notes(date,kind,model,text,at) VALUES(?,?,?,?,?)", (day, kind, llm.get("model"), text, _now()))
    return {"model": llm.get("model"), "text": text, "at": _now()}


def latest_sep() -> dict | None:
    today = dt.date.today().isoformat()
    for d in reversed([m for m in meetings() if has_sep(m) and m <= today]):
        s = get(d, "sep")
        if s:
            return {"date": d, "url": s["url"], **s["data"]}
    return None


def sync(limit: int = 12) -> dict:
    """Fetch whatever is due and missing (statements, SEPs, minutes) — newest first, a few per run."""
    today = dt.date.today().isoformat()
    got = 0
    for d in reversed([m for m in meetings() if m <= today]):
        for k in ("statement", "sep", "minutes"):
            if got >= limit:
                return {"fetched": got}
            with _conn() as c:
                r = c.execute("SELECT status FROM docs WHERE date=? AND kind=?", (d, k)).fetchone()
            if r and r[0] == "ok":
                continue
            try:
                if get(d, k):
                    got += 1
            except Exception as e:  # noqa: BLE001
                log.info("fomc %s %s: %s", d, k, e)
    return {"fetched": got}


def text(day: str | None = None) -> str:
    today = dt.date.today().isoformat()
    day = day or max((m for m in meetings() if m <= today), default=None)
    if not day:
        return "沒有 FOMC 會議資料"
    b = bundle(day)
    lines = [f"【FOMC {day}】"]
    if b["statement"]:
        ch = [("刪除：" if x["op"] == "del" else "新增：") + x["text"] for x in b["diff"] or [] if x["op"] != "same"]
        lines.append(f"聲明相對 {b['previous_date'] or '上次'} 的措辭變化（{len(ch)} 處）：")
        lines += [f"- {x}" for x in ch[:12]] or ["- 無變化"]
        n = b["notes"].get("statement")
        if n:
            lines.append("AI 解讀：" + n["text"])
    else:
        lines.append("聲明尚未發布")
    sep = b["sep"] or latest_sep()
    if sep:
        lines.append(f"經濟預測中位數（SEP {sep.get('date', day)}，欄位 {'、'.join(sep['years'])}）：")
        for lab, k, zh in VARS:
            if k in sep["median"]:
                v = "、".join("—" if x is None else f"{x:g}" for x in sep["median"][k])
                pv = sep["previous"].get(k)
                lines.append(f"- {zh}：{v}" + (f"（前次 {'、'.join('—' if x is None else f'{x:g}' for x in pv)}）" if pv else ""))
    if b["notes"].get("minutes"):
        lines.append("會議紀要 AI 摘要：" + b["notes"]["minutes"]["text"])
    elif b["minutes"]:
        lines.append(f"會議紀要已發布（{b['minutes']['url']}）")
    else:
        lines.append(f"會議紀要預計 {b['minutes_due']} 前後發布")
    return "\n".join(lines)
