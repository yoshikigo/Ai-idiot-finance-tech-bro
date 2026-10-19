#!/usr/bin/env python3
"""FT Terminal client for Hermes (stdlib only). Talks to the fin-terminal agent API.

  ft.py quote 台積電 0050 AAPL ^TWII        報價（可中文名稱、代號、Yahoo 代號、基金 id）
  ft.py stock 2330                           個股 / ETF / 基金完整資訊＋新聞
  ft.py etf 00878 [--no-holdings]            ETF 淨值、折溢價、規模、配息、成分股
  ft.py etf-rank --sort premium --category 債券 --limit 10
  ft.py fund 統一黑馬 | ft.py fund D:12345678  基金淨值與報酬
  ft.py fund-search 安聯 收益 [--region 境外]
  ft.py market | news 台積電 | watchlist | brief
  ft.py focus                               今日焦點（供應鏈題材漲跌、強弱勢股、三大法人、新聞、AI 今日重點）
  ft.py macro                               美國總經（FRED：利率、通膨、就業、景氣）
  ft.py sec NVDA                            美股 SEC 最近申報與年度財報
  ft.py ledger                              AI 分析紀錄：各 agent 事後核對命中率、最近檢討
  ft.py events [--days 365]                 近一年市場重大事件（指數大波動、Fed 決策）＋AI 摘要
  ft.py chain [CoWoS | 散熱 | 奇鋐]          供應鏈：各環節今日漲跌／某環節的上下游與成分股
  ft.py forecast 0050 [--days 30]           TimesFM 趨勢預測（中位數、80% 區間、回測）
  ft.py decide 2330 [--days 20]             Laya 交易判斷：買進／持有／觀望／賣出／放空 各幾 %（5/20/60 日）
  ft.py strategy [內建策略名稱 | "中文選股想法"] [--no-backtest]   選股名單＋回測（空白＝列出內建策略）
  ft.py world [2603 | --region 台灣,紅海]    現實世界訊號（God's Eye View：地震、軍機、航運、氣旋）
  ft.py review                              AI 預測回顧：過去的預測準不準
  ft.py analyze tradingagents 2330 [--param debate_rounds=2] [--wait 540]
  ft.py job tradingagents <id> [--wait 540]  |  ft.py jobs
Output is plain text (no Markdown) and truncated to --max-chars (default 4500, LINE-safe).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("FT_URL", "http://127.0.0.1:8130").rstrip("/")


def call(path, params=None, body=None, timeout=60):
    q = dict(params or {})
    q.setdefault("plain", "1")
    url = f"{BASE}/api/agent/{path}?{urllib.parse.urlencode(q)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(msg).get("detail", msg)
        except ValueError:
            pass
        return f"錯誤 {e.code}：{msg}"
    except (urllib.error.URLError, TimeoutError) as e:
        return f"連不到 FT Terminal（{BASE}）：{e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--sort", default="scale")
    ap.add_argument("--category", default="")
    ap.add_argument("--region", default="", help="fund region (境內/境外/期信) or world regions (台灣,紅海,…)")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--no-holdings", action="store_true")
    ap.add_argument("--no-backtest", action="store_true", help="strategy: only today's picks")
    ap.add_argument("--param", action="append", default=[], help="engine parameter k=v (repeatable)")
    ap.add_argument("--wait", type=int, default=540, help="seconds to wait for an AI job (keep < Hermes timeout)")
    ap.add_argument("--days", type=int, default=30, help="forecast horizon (trading days)")
    ap.add_argument("--max-chars", type=int, default=4500)
    a = ap.parse_args()
    words = " ".join(a.args).strip()
    c = a.cmd

    if c == "quote":
        out = call("quote", {"q": ",".join(a.args)})
    elif c == "stock":
        out = call("stock", {"q": words})
    elif c == "etf":
        out = call("etf", {"code": words, "holdings": "0" if a.no_holdings else "1"})
    elif c in ("etf-rank", "rank"):
        out = call("etf/rank", {"sort": a.sort, "category": a.category, "limit": a.limit})
    elif c == "fund":
        out = call("fund", {"id": words} if ":" in words else {"q": words})
    elif c == "fund-search":
        out = call("fund/search", {"q": words, "region": a.region, "limit": a.limit})
    elif c in ("market", "watchlist", "brief", "help"):
        out = call(c, timeout=120)
    elif c == "focus":
        out = call("focus", timeout=180)
    elif c == "macro":
        out = call("macro", timeout=120)
    elif c == "sec":
        out = call("sec", {"q": words}, timeout=120)
    elif c in ("ledger", "track"):
        out = call("ledger", timeout=60)
    elif c == "events":
        out = call("events", {"days": a.days if a.days != 30 else 365}, timeout=60)
    elif c in ("chain", "supply"):
        out = call("chain", {"q": words}, timeout=120)
    elif c == "forecast":
        out = call("forecast", {"q": words, "h": a.days}, timeout=600)
    elif c in ("strategy", "screen"):
        out = call("strategy", {"q": words, "backtest": "0" if a.no_backtest else "1"}, timeout=900)
    elif c in ("decide", "laya"):
        out = call("decide", {"q": words, "h": a.days if a.days in (5, 20, 60) else 20}, timeout=600)
    elif c == "world":
        out = call("world", {"q": words, "region": a.region}, timeout=60)
    elif c == "review":
        out = call("review", timeout=180)
    elif c == "news":
        out = call("news", {"q": words})
    elif c == "jobs":
        out = call("jobs")
    elif c == "analyze":
        if len(a.args) < 2:
            sys.exit("用法：ft.py analyze <tradingagents|hedgefund|qlib|finrl|fingpt> <標的…>")
        params = {}
        for kv in a.param:
            k, _, v = kv.partition("=")
            params[k] = int(v) if v.isdigit() else v
        r = call("analyze", body={"engine": a.args[0], "q": ",".join(a.args[1:]), "params": params})
        try:
            j = json.loads(r)
        except ValueError:
            print(r)
            return
        print(f"已開始 {j['engine']} 工作 {j['id']}（{json.dumps(j['params'], ensure_ascii=False)}）")
        out = call("job", {"engine": j["engine"], "id": j["id"], "wait": a.wait}, timeout=a.wait + 60)
    elif c == "job":
        if len(a.args) < 2:
            sys.exit("用法：ft.py job <engine> <id>")
        out = call("job", {"engine": a.args[0], "id": a.args[1], "wait": a.wait}, timeout=a.wait + 60)
    else:
        sys.exit(f"未知指令 {c}；ft.py -h 看用法")

    if len(out) > a.max_chars:
        out = out[: a.max_chars - 60].rstrip() + "\n…（內容過長已截斷，完整報告請到 FT Terminal 網頁查看）"
    print(out)


if __name__ == "__main__":
    main()
