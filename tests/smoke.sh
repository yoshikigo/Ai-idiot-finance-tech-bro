#!/usr/bin/env bash
# shellcheck disable=SC2015
# Post-install smoke test against the real stack (real yfinance + your Ollama).
#   ./tests/smoke.sh            health + market data + a quick FinGPT sentiment job
#   ./tests/smoke.sh --full     also queue a small Qlib run (takes a few minutes)
set -uo pipefail
B=http://127.0.0.1:8130
pass=0 fail=0
ok()  { printf '\e[32m✓\e[0m %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '\e[31m✗\e[0m %s\n' "$*"; fail=$((fail+1)); }
j()   { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }

curl -fsS -m 5 $B/healthz >/dev/null && ok "terminal up" || { bad "terminal down"; exit 1; }
curl -fsS $B/api/engines | j "'\n'.join(f\"{e['name']}: {'online' if e['online'] else 'OFFLINE'}\" for e in d)" | while read -r l; do echo "   $l"; done
[[ $(curl -fsS $B/api/engines | j "sum(e['online'] for e in d)") == 8 ]] && ok "8/8 engines online" || bad "some engines offline"
r=$(curl -fsS $B/api/ollama/models | j "d['ok'], len(d['models'])") && [[ $r == True* ]] && ok "Ollama reachable ($r)" || bad "Ollama unreachable"
px=$(curl -fsS "$B/api/quotes?s=AAPL,TWD=X" | j "', '.join(f\"{q['symbol']}={q.get('last')}\" for q in d)") && [[ $px != *None* ]] && ok "quotes: $px" || bad "quotes failed: $px"
raw=$(curl -sS "$B/api/history?s=2330.TW&range=1mo"); n=$(j "len(d)" <<<"$raw" 2>/dev/null) && [[ $n -gt 5 ]] && ok "history 2330.TW 1mo: $n bars" || bad "history failed：${raw:0:200}（journalctl --user -u fin-terminal -n 50）"
n=$(curl -fsS "$B/api/news?s=AAPL" | j "len(d)") && ok "news AAPL: $n items" || bad "news failed"

# ---- Taiwan data (real sources: TWSE MIS / ISIN list / ETF NAV / SITCA / funds)
g() { curl -fsS -G "$B/$1" --data-urlencode "$2"; }
r=$(g api/resolve q=台積電 | j "d.get('symbol')") && [[ $r == 2330.TW ]] && ok "中文名稱解析：台積電 → $r" || bad "中文名稱解析失敗（證券清單）：$r"
r=$(g api/resolve q=00679B | j "d.get('symbol')") && [[ $r == 00679B.TWO ]] && ok "上櫃 ETF 代號：$r" || bad "00679B 解析：$r"
r=$(curl -fsS "$B/api/quotes?s=2330.TW,^TWII" | j "', '.join(f\"{q['symbol']}={q.get('last')}({q.get('source','yf')})\" for q in d)") && ok "台股報價：$r" || bad "台股報價失敗"
n=$(curl -fsS "$B/api/tw/etfs?limit=5" | j "d['count']") && [[ $n -gt 100 ]] && ok "ETF 淨值揭露：$n 檔" || bad "ETF 淨值揭露（all_etf.txt）：$n 檔"
r=$(curl -fsS -m 120 "$B/api/tw/etf/holdings?code=0050" | j "d.get('error') or str(len(d['items'])) + ' 檔，' + d['items'][0]['name']") && [[ $r != *失敗* ]] && ok "0050 成分股：$r" || bad "成分股（SITCA）：$r"
sleep 2
n=$(curl -fsS "$B/api/funds/status" | j "d['funds']") && [[ $n -gt 1000 ]] && ok "基金資料庫：$n 檔" || bad "基金資料庫只有 $n 檔（GET /api/funds/status 看 last_result）"
r=$(g api/agent/fund/search q=統一 | head -n 2 | tail -n 1) && ok "基金搜尋：${r:0:60}" || bad "基金搜尋失敗"
n=$(curl -fsS "$B/api/news?s=2330.TW" | j "len(d)") && [[ $n -gt 0 ]] && ok "中文新聞 2330：$n 則" || bad "中文新聞（Google News）：$n 則"
r=$(curl -fsS "$B/api/agent/market" | head -n 2 | tail -n 1) && ok "agent API：${r:0:70}" || bad "agent API 失敗"
echo "   TimesFM（第一次會下載權重，最多等 10 分鐘）…"
r=$(curl -fsS -m 600 "$B/api/forecast?s=0050.TW&h=20&backtest=true" | j "f\"{d['model']} 20日 {d['summary']['change_pct']:+.2f}% 80%區間 {d['summary']['p10_end']:.2f}~{d['summary']['p90_end']:.2f} 回測MAPE {d['backtest']['mape']:.2f}% vs 基準 {d['backtest']['naive_mape']:.2f}%\"") && ok "TimesFM：$r" || bad "TimesFM 預測失敗（journalctl --user -u ft-timesfm -n 50）"

# ---- God's Eye View (現實世界訊號) + Obsidian notes
r=$(curl -fsS -m 5 -X POST -H 'content-type: application/json' -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://127.0.0.1:4173/mcp | j "len(d['result']['tools'])") \
  && ok "God's Eye View MCP：$r 個工具" || bad "God's Eye View MCP 無回應（journalctl --user -u gods-eye-view -n 50）"
r=$(curl -fsS -m 10 $B/api/world/status | j "d['gev']['ok'], d['snapshots'], d['last_ok']") && [[ $r == True* ]] \
  && ok "terminal → GEV：$r（ok, 快照數, 最近一輪成功/總數）" || bad "terminal 連不到 GEV：$r（GEV_URL）"
echo "   世界快照收集一輪（約 30–90 秒）…"
r=$(curl -fsS -m 240 -X POST $B/api/world/refresh | j "str(d)[:120]") && ok "world refresh：$r" || bad "world refresh 失敗"
r=$(curl -fsS -m 10 "$B/api/world/digest?symbol=2330.TW" | j "d.get('text','')[:200].replace(chr(10),' ')") && ok "台積電世界摘要：${r:0:110}…" || bad "world digest 失敗"
r=$(curl -fsS -m 10 $B/api/notes/status | j "f\"folder={d['folder']} pending={d['pending']} auto={d['auto']}\"") && ok "Obsidian：$r" || bad "notes status 失敗"
systemctl --user is-active --quiet ft-obsidian-apply.path && ok "ft-obsidian-apply.path active" || bad "ft-obsidian-apply.path 未啟用"

echo "   Laya 判斷（第一次會下載 1.7 GB 模型，最多等 20 分鐘）…"
for _ in $(seq 1 120); do
  r=$(curl -sS -m 600 "$B/api/decide?s=2330.TW&h=20")
  [[ $r == *'"action"'* || $r != *下載中* && $r != *載入中* ]] && break; sleep 10
done
raw=$r; r=$(j "f\"{d['action']['label']} {d['action']['probabilities'][d['action']['choice']]*100:.0f}% 淨值 {d['net']*100:+.0f}% 收高 {d['p_up']*100:.0f}% ({d['ms']} ms, {d['tokens']} tokens)\"" <<<"$r") && ok "Laya 2330：$r" || bad "Laya 判斷失敗：${raw:0:200}（journalctl --user -u ft-laya -n 50）"

# ---- 策略研究（官方資料庫）
r=$(curl -fsS -m 30 $B/api/research/catalog | j "' / '.join(f\"{x['name']} {x['rows']}筆 {x['first'] or '—'}~{x['last'] or '—'}\" for x in d['datasets'])") && ok "研究資料庫：$r" || bad "研究資料庫目錄失敗（journalctl --user -u ft-research -n 50）"
r=$(curl -fsS -m 30 $B/api/research/catalog | j "str(d['errors'][:3]) if d['errors'] else 'none'") && echo "   最近抓取錯誤：${r:0:300}"
r=$(curl -fsS -m 600 -XPOST $B/api/research/screen -H 'content-type: application/json' -d '{"spec":{"conditions":["收盤價 > ma(收盤價, 20)"],"rank":"pct(收盤價, 20)","top_n":5}}' | j "', '.join(x['code']+' '+x['name'] for x in d['picks']['rows'])") && ok "條件選股（站上月線、20 日漲幅前 5）：$r" || bad "條件選股失敗：$r"

id=$(curl -fsS -XPOST $B/api/engines/fingpt/jobs -H 'content-type: application/json' \
  -d '{"params":{"mode":"sentiment","ticker":"AAPL","text":"Apple beats revenue estimates and raises dividend\nRegulators open antitrust probe into App Store"}}' | j "d['id']")
echo "   FinGPT job $id …"
for _ in $(seq 1 90); do
  st=$(curl -fsS $B/api/engines/fingpt/jobs/$id | j "d['status'] + ' | ' + str(d.get('summary') or d.get('error') or '')")
  [[ $st == done* || $st == error* ]] && break; sleep 2
done
[[ $st == done* ]] && ok "FinGPT sentiment via Ollama: $st" || bad "FinGPT job: $st"

# 供應鏈地圖、今日焦點、登入
n=$(curl -fsS "$B/api/sc/stats?net=semi" | j "sum(1 for n in d['nodes'].values() if n['pct'] is not None)") && [[ $n -ge 8 ]] && ok "供應鏈報價：$n 個環節有漲跌" || bad "供應鏈報價：$n 個環節"
s=$(curl -fsS "$B/api/focus" | j "str(len(d['themes'])) + ' 題材, 法人 ' + (d['institutional']['date'] if d['institutional'] else 'NONE') + ', 新聞 ' + str(len(d['news']))") && [[ $s != *NONE* ]] && ok "今日焦點：$s" || bad "今日焦點（三大法人 BFI82U）：$s"
c=$(curl -s -o /dev/null -w '%{http_code}' "$B/api/me/holdings") && [[ $c == 401 ]] && ok "未登入讀不到持股（401）" || bad "未登入讀持股回 $c（應該 401）"
s=$(curl -fsS -XPOST -H 'x-ft: 1' "$B/api/events/refresh" | j "d['detected']") && [[ $s -ge 1 ]] && ok "重大事件：偵測到 $s 件指數大波動（近兩年）" || bad "重大事件偵測：$s"
n=$(curl -fsS "$B/api/events?kinds=move" | j "sum(1 for e in d['events'] if e['headlines'])") && ok "重大事件已附新聞：$n 件（背景慢慢補）" || bad "重大事件 API"
raw=$(curl -sS -m 150 "$B/api/macro?ids=DGS10,UNRATE")
n=$(j "sum(1 for r in d['rows'] if 'value' in r)" <<<"$raw" 2>/dev/null) && [[ $n -eq 2 ]] && ok "FRED 總經：DGS10、UNRATE 有資料" \
  || bad "FRED：${n:-0}/2 — $(j "'；'.join(r['id']+' '+r.get('error','') for r in d['rows'] if 'value' not in r)" <<<"$raw" 2>/dev/null || echo "${raw:0:200}")"
n=$(curl -fsS -m 150 "$B/api/twfx" | j "f\"{sum(1 for r in d['rows'] if 'twd' in r)} 幣別、台銀 {len(d['bot']['rates'])} 筆、USD {next(r['twd'] for r in d['rows'] if r['code']=='USD'):.3f}\"") && ok "台幣匯率：$n" || bad "台幣匯率 /api/twfx"
if grep -q '^FT_SEC_USER_AGENT=.*@' "$HOME/.config/fin-terminal/fin-terminal.env" 2>/dev/null; then
  s=$(curl -fsS "$B/api/sec/filings?s=NVDA&limit=3" | j "d['enabled'] and len(d['filings'])") && [[ $s != False && $s -ge 1 ]] && ok "SEC EDGAR：NVDA 最近申報 $s 筆" || bad "SEC EDGAR 申報：$s"
  s=$(curl -fsS "$B/api/sec/financials?s=NVDA" | j "d['periods'][0]['end'] + ' 申報 ' + d['periods'][0]['filed']") && ok "SEC XBRL 財報：$s" || bad "SEC XBRL 財報"
else
  echo "   （略過 SEC：fin-terminal.env 沒設 FT_SEC_USER_AGENT）"
fi
if [[ ${1:-} == --full ]]; then
  id=$(curl -fsS -XPOST $B/api/engines/qlib/jobs -H 'content-type: application/json' \
    -d '{"params":{"universe":"custom","tickers":"2330.TW,2317.TW,2454.TW,2308.TW,2382.TW,2881.TW,2882.TW,2303.TW","benchmark":"0050.TW","years":4,"model":"lgb","topk":3,"n_drop":1}}' | j "d['id']")
  echo "   Qlib job $id …（數分鐘）"
  for _ in $(seq 1 300); do
    st=$(curl -fsS $B/api/engines/qlib/jobs/$id | j "d['status'] + ' | ' + str(d.get('summary') or d.get('error') or d.get('stage'))")
    [[ $st == done* || $st == error* ]] && break; sleep 3
  done
  [[ $st == done* ]] && ok "Qlib: $st" || bad "Qlib: $st"
fi
echo; echo "passed $pass, failed $fail"
[[ $fail -eq 0 ]]
