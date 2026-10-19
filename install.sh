#!/usr/bin/env bash
# Fin Terminal installer — rootless Podman + Quadlet (run as your normal user, not root).
#
#   ./install.sh                     build + install everything (incl. God's Eye View + Obsidian applier)
#   ./install.sh terminal qlib       only these components (terminal = UI + FinGPT; gev = God's Eye View)
#   ./install.sh --tailscale         also: tailscale serve https :8130 (terminal) and :4173 (God's Eye View)
#   ./install.sh --no-build          only (re)install Quadlet units and restart
#   FT_PUBLIC_HOST=fin.example.com ./install.sh --no-build --cloudflare
#                                    publish the terminal on that hostname via its own Cloudflare Tunnel
#                                    (login + TOTP required over that URL; God's Eye View stays private)
#   FT_DATA_ROOT=/mnt/x ./install.sh  put all container data somewhere else (default ~/fin-terminal-data;
#                                    an existing install keeps its location)
#   FT_VAULT=/path/to/vault ./install.sh   Obsidian vault (default ~/Obsidian/vault)
#   TA_REF=main ./install.sh tradingagents     build an engine from another upstream ref
#                                              (TA_REF / HF_REF / FINRL_REF / FINGPT_REF / GEV_REF)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

CFG="$HOME/.config/fin-terminal"
QDIR="$HOME/.config/containers/systemd"
SDIR="$HOME/.config/systemd/user"
LIBDIR="$HOME/.local/share/fin-terminal"
# keep an existing install where it is: read the data root from the installed unit
PREV_ROOT=$(sed -n 's|^Volume=\(.*\)/data:/data$|\1|p' "$QDIR/fin-terminal.container" 2>/dev/null | head -1)
DATA_ROOT="${FT_DATA_ROOT:-${PREV_ROOT:-$HOME/fin-terminal-data}}"
DATA_ROOT="${DATA_ROOT%/}"
ALL=(gev terminal tradingagents hedgefund qlib finrl timesfm laya)
declare -A IMG=([gev]=gods-eye-view [terminal]=fin-terminal [tradingagents]=ft-tradingagents [hedgefund]=ft-hedgefund
                [qlib]=ft-qlib [finrl]=ft-finrl [timesfm]=ft-timesfm [laya]=ft-laya)
declare -A FILE=([gev]=gods-eye-view/Containerfile [terminal]=terminal/Containerfile
                 [tradingagents]=engines/tradingagents/Containerfile
                 [hedgefund]=engines/hedgefund/Containerfile [qlib]=engines/qlib/Containerfile [finrl]=engines/finrl/Containerfile
                 [timesfm]=engines/timesfm/Containerfile [laya]=engines/laya/Containerfile)
declare -A UNITS=([gev]=gods-eye-view [terminal]="fin-terminal ft-fingpt ft-research" [tradingagents]=ft-tradingagents [hedgefund]=ft-hedgefund
                  [qlib]=ft-qlib [finrl]=ft-finrl [timesfm]=ft-timesfm [laya]=ft-laya)
declare -A PORT=([gods-eye-view]=4173 [fin-terminal]=8130 [ft-tradingagents]=8131 [ft-hedgefund]=8132 [ft-qlib]=8133
                 [ft-finrl]=8134 [ft-fingpt]=8135 [ft-timesfm]=8136 [ft-laya]=8137 [ft-research]=8138)
declare -A REFVAR=([gev]=GEV_REF [terminal]=FINGPT_REF [tradingagents]=TA_REF [hedgefund]=HF_REF [finrl]=FINRL_REF)

c_ok()   { printf '\e[32m✓\e[0m %s\n' "$*"; }
c_warn() { printf '\e[33m!\e[0m %s\n' "$*"; }
c_err()  { printf '\e[31m✗\e[0m %s\n' "$*" >&2; }
step()   { printf '\n\e[1;33m== %s\e[0m\n' "$*"; }
# set KEY=VAL in an env file, but only if KEY is missing or empty (never overwrite your edits)
env_fill() {
  local f=$1 k=$2 v=$3
  if grep -qE "^${k}=.+" "$f"; then return 0; fi
  if grep -qE "^#?${k}=" "$f"; then sed -i -E "s|^#?${k}=.*|${k}=${v}|" "$f"; else printf '%s=%s\n' "$k" "$v" >>"$f"; fi
  c_ok "$(basename "$f")：$k=$v"
}
# copy a unit file, substituting @DATA_ROOT@
put_unit() { sed "s|@DATA_ROOT@|$DATA_ROOT|g" "$1" >"$2.tmp" && mv "$2.tmp" "$2" && chmod 0644 "$2"; }

BUILD=1 TS=0 CF=0 SEL=()
for a in "$@"; do
  case "$a" in
    --no-build) BUILD=0 ;;
    --tailscale) TS=1 ;;
    --cloudflare) CF=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    gev|terminal|tradingagents|hedgefund|qlib|finrl|timesfm|laya) SEL+=("$a") ;;
    gods-eye-view|world) SEL+=(gev) ;;
    fingpt) SEL+=(terminal) ;;
    *) c_err "unknown argument: $a"; exit 1 ;;
  esac
done
[[ ${#SEL[@]} -eq 0 ]] && SEL=("${ALL[@]}")

step "檢查環境"
[[ $EUID -eq 0 ]] && { c_err "請用一般使用者（rootless Podman）執行，不要用 root"; exit 1; }
command -v podman >/dev/null || { c_err "找不到 podman"; exit 1; }
podman --version | grep -qE ' (4\.[4-9]|[5-9])\.' || c_warn "Quadlet 需要 podman ≥ 4.4（目前 $(podman --version)）"
systemctl --user show-environment >/dev/null || { c_err "systemctl --user 無法使用"; exit 1; }
loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes || c_warn "linger 未啟用：開機後不會自動啟動（sudo loginctl enable-linger $USER）"
c_ok "podman $(podman --version | awk '{print $3}')"

step "資料位置 → $DATA_ROOT"
parent="$(dirname "$DATA_ROOT")"
if [[ ! -d "$DATA_ROOT" && ! -d "$parent" ]]; then
  c_err "$parent 不存在（外接硬碟沒掛上？）。掛好後再跑，或用 FT_DATA_ROOT=/其他/路徑 ./install.sh"; exit 1
fi
if command -v findmnt >/dev/null; then
  mp=$(findmnt -n -o TARGET --target "${DATA_ROOT}" 2>/dev/null || findmnt -n -o TARGET --target "$parent" 2>/dev/null || true)
  fstype=$(findmnt -n -o FSTYPE --target "$parent" 2>/dev/null || true)
  if [[ "$mp" == "/" ]]; then
    c_warn "$DATA_ROOT 在根目錄分割區上（不是外接硬碟）。如果硬碟還沒掛上，請先 Ctrl-C 掛好再跑"
    sleep 5
  fi
  [[ -n "$fstype" ]] && c_ok "檔案系統：$fstype（掛載點 $mp）"
  case "$fstype" in exfat|vfat|ntfs|ntfs3|fuseblk) c_warn "$fstype 不支援 Unix 權限；資料庫已使用相容模式，但請確認掛載選項 uid=$(id -u)" ;; esac
fi
if [[ ! -d "$DATA_ROOT" ]]; then
  mkdir -p "$DATA_ROOT" 2>/dev/null || sudo install -d -o "$(id -u)" -g "$(id -g)" "$DATA_ROOT"
fi
mkdir -p "$DATA_ROOT/data/obsidian-outbox" "$DATA_ROOT/gods-eye-view/gev-cache" "$DATA_ROOT/gods-eye-view/gev-logs"
t="$DATA_ROOT/.ft-write-test.$$"
if ! ( : >"$t" ) 2>/dev/null; then c_err "$DATA_ROOT 無法寫入（sudo chown -R $USER: $DATA_ROOT）"; exit 1; fi
rm -f "$t"
# sentinel: every unit refuses to start unless the drive is mounted (so nothing writes into an empty mountpoint)
[[ -f "$DATA_ROOT/.ft-root" ]] || printf 'fin-terminal data root – do not delete (units check for this file)\n' >"$DATA_ROOT/.ft-root"
c_ok "$DATA_ROOT/{data,gods-eye-view}  （哨兵檔 .ft-root）"

# migrate the old named volume (≤ v2026.10.19-4) once
if podman volume exists fin-terminal-data 2>/dev/null; then
  if [[ -z "$(find "$DATA_ROOT/data" -mindepth 1 -maxdepth 1 ! -name obsidian-outbox -print -quit 2>/dev/null)" ]]; then
    step "搬移舊的 podman volume fin-terminal-data → $DATA_ROOT/data"
    for u in fin-terminal ft-fingpt ft-tradingagents ft-hedgefund ft-qlib ft-finrl ft-timesfm; do
      systemctl --user stop "$u.service" 2>/dev/null || true
    done
    podman volume export fin-terminal-data | tar -x --no-same-owner --no-same-permissions -C "$DATA_ROOT/data"
    c_ok "已搬移 $(du -sh "$DATA_ROOT/data" | cut -f1)。確認沒問題後可刪除舊 volume：podman volume rm fin-terminal-data"
  else
    c_warn "舊 volume fin-terminal-data 還在，但 $DATA_ROOT/data 已有資料 → 不搬移（舊 volume 不再使用）"
  fi
fi

mkdir -p "$CFG" "$QDIR"
if [[ ! -f "$CFG/fin-terminal.env" ]]; then
  cp config/fin-terminal.env.example "$CFG/fin-terminal.env"
  c_ok "已建立 $CFG/fin-terminal.env（可編輯）"
else
  c_ok "沿用 $CFG/fin-terminal.env"
  # new keys from this version
  grep -q '^#\?GEV_URL=' "$CFG/fin-terminal.env" || sed -n '/^# ---- God.s Eye View/,$p' config/fin-terminal.env.example >>"$CFG/fin-terminal.env"
fi
if [[ ! -f "$CFG/gods-eye-view.env" ]]; then
  cp config/gods-eye-view.env.example "$CFG/gods-eye-view.env"; chmod 600 "$CFG/gods-eye-view.env"
  c_ok "已建立 $CFG/gods-eye-view.env（API 金鑰放這裡）"
fi

TSHOST=""
if command -v tailscale >/dev/null; then
  TSHOST=$(tailscale status --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
fi
if [[ -n "$TSHOST" ]]; then
  env_fill "$CFG/gods-eye-view.env" GEV_ALLOWED_HOSTS "$TSHOST"
  env_fill "$CFG/gods-eye-view.env" GEV_EMBED_FRAME_ANCESTORS "https://$TSHOST:8130"
  env_fill "$CFG/fin-terminal.env" GEV_PUBLIC_URL "https://$TSHOST:4173"
  env_fill "$CFG/fin-terminal.env" FT_PUBLIC_URL "https://$TSHOST:8130"
fi

set -a; # shellcheck disable=SC1091
. "$CFG/fin-terminal.env"; set +a
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
FT_MODEL="${FT_MODEL:-qwen3:32b}"

if tags=$(curl -fsS -m 5 "$OLLAMA_URL/api/tags" 2>/dev/null); then
  c_ok "Ollama 可連線：$OLLAMA_URL"
  if grep -q "\"name\":\"${FT_MODEL}\(:latest\)\?\"" <<<"$tags"; then c_ok "模型 $FT_MODEL 已安裝"
  else c_warn "Ollama 上找不到模型 $FT_MODEL（編輯 $CFG/fin-terminal.env 的 FT_MODEL，或之後在 ⚙ 設定 裡選）"; fi
else
  c_warn "連不到 Ollama（$OLLAMA_URL）。服務仍會安裝，AI 功能要等 Ollama 起來才能用"
fi

if [[ $BUILD -eq 1 ]]; then
  for comp in "${SEL[@]}"; do
    step "建置 ${IMG[$comp]}"
    args=()
    rv="${REFVAR[$comp]:-}"
    if [[ -n "$rv" && -n "${!rv:-}" ]]; then
      args+=(--build-arg "UPSTREAM_REF=${!rv}" --build-arg "FINGPT_REF=${!rv}" --build-arg "GEV_REF=${!rv}"); c_warn "使用 $rv=${!rv}"
    fi
    ctx=.; [[ $comp == gev ]] && ctx=gods-eye-view
    podman build "${args[@]}" -t "localhost/${IMG[$comp]}:latest" -f "${FILE[$comp]}" "$ctx"
    c_ok "localhost/${IMG[$comp]}:latest"
  done
fi

step "安裝 Quadlet 單元 → $QDIR"
units=()
for comp in "${SEL[@]}"; do
  for u in ${UNITS[$comp]}; do
    put_unit "quadlet/$u.container" "$QDIR/$u.container"
    units+=("$u")
    c_ok "$u.container"
  done
done
# terminal units installed earlier with the old named volume must move too
for u in fin-terminal ft-fingpt ft-research ft-tradingagents ft-hedgefund ft-qlib ft-finrl ft-timesfm ft-laya; do
  f="$QDIR/$u.container"
  if [[ -f "$f" ]] && grep -q 'fin-terminal-data:/data' "$f"; then
    put_unit "quadlet/$u.container" "$f"; units+=("$u"); c_ok "$u.container（改用 $DATA_ROOT/data）"
  fi
done
mapfile -t units < <(printf '%s\n' "${units[@]}" | awk '!s[$0]++')

step "Obsidian 筆記（ft-obsidian-apply）"
VAULT="${FT_VAULT:-$HOME/Obsidian/vault}"
mkdir -p "$LIBDIR" "$SDIR"
install -m 0755 scripts/ft-obsidian-apply.sh "$LIBDIR/ft-obsidian-apply.sh"
if [[ ! -f "$CFG/obsidian.env" ]]; then
  printf '# Obsidian vault that receives FT Terminal notes (folder FT_OBSIDIAN_FOLDER in fin-terminal.env)\nFT_VAULT=%s\nFT_OUTBOX=%s\n' \
    "$VAULT" "$DATA_ROOT/data/obsidian-outbox" >"$CFG/obsidian.env"
  c_ok "已建立 $CFG/obsidian.env（FT_VAULT=$VAULT）"
fi
for f in systemd/ft-obsidian-apply.{service,path,timer}; do put_unit "$f" "$SDIR/$(basename "$f")"; done
# shellcheck disable=SC1091
VAULT=$(. "$CFG/obsidian.env"; echo "${FT_VAULT:-$VAULT}")
if [[ -d "$VAULT/.obsidian" ]]; then c_ok "vault：$VAULT → $VAULT/${FT_OBSIDIAN_FOLDER:-finance}/"
else c_warn "找不到 $VAULT/.obsidian（vault 未掛載或路徑不對）。筆記會先留在 outbox，vault 出現後自動搬過去；路徑改 $CFG/obsidian.env"; fi

systemctl --user daemon-reload
systemctl --user enable --now ft-obsidian-apply.path ft-obsidian-apply.timer >/dev/null
c_ok "ft-obsidian-apply.path / .timer 已啟用"
for u in "${units[@]}"; do
  systemctl --user restart "$u.service"
done

step "等待服務就緒"
for u in "${units[@]}"; do
  p=${PORT[$u]}; ok=0
  for _ in $(seq 1 90); do
    if [[ $u == gods-eye-view ]]; then
      curl -fsS -m 3 -X POST -H 'content-type: application/json' -H 'MCP-Protocol-Version: 2025-06-18' \
        -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' "http://127.0.0.1:$p/mcp" >/dev/null 2>&1 && { ok=1; break; }
    else
      path=health; [[ $u == fin-terminal ]] && path=healthz
      curl -fsS -m 2 "http://127.0.0.1:$p/$path" >/dev/null 2>&1 && { ok=1; break; }
    fi
    sleep 2
  done
  if [[ $ok -eq 1 ]]; then c_ok "$u  127.0.0.1:$p"
  else c_err "$u 沒有回應 → journalctl --user -u $u -n 80"; fi
done

if [[ $TS -eq 1 ]]; then
  step "Tailscale serve"
  if [[ -n "$TSHOST" ]]; then
    tailscale serve --bg --https=8130 http://127.0.0.1:8130 >/dev/null && c_ok "FT Terminal     https://$TSHOST:8130"
    tailscale serve --bg --https=4173 http://127.0.0.1:4173 >/dev/null && c_ok "God's Eye View  https://$TSHOST:4173"
    c_warn "只用 tailscale serve（tailnet 內），不要用 funnel／Cloudflare 公開：God's Eye View 會代你的 API 金鑰發請求"
  else c_warn "找不到 tailscale 或未登入"; fi
fi

if [[ $CF -eq 1 ]]; then
  step "Cloudflare Tunnel（公開網址）"
  PUBHOST="${FT_PUBLIC_HOST:-$(sed -n 's/^FT_PUBLIC_HOSTS=\([^,]*\).*/\1/p' "$CFG/fin-terminal.env" 2>/dev/null)}"
  [[ -n $PUBHOST ]] || { c_err "請指定公開網址：FT_PUBLIC_HOST=fin.example.com ./install.sh --no-build --cloudflare"; exit 1; }
  if ! podman secret exists fin-terminal-tunnel-token 2>/dev/null; then
    echo "   Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel（Cloudflared，名稱例如 fin-terminal）"
    echo "   → 複製安裝指令裡 eyJ 開頭的 token；Public Hostname：$PUBHOST → HTTP → 127.0.0.1:8130"
    read -rsp "   貼上 tunnel token（不會顯示）： " TOK; echo
    [[ $TOK == eyJ* ]] || { c_err "token 應該是 eyJ 開頭"; exit 1; }
    printf '%s' "$TOK" | podman secret create fin-terminal-tunnel-token - >/dev/null && c_ok "podman secret fin-terminal-tunnel-token"
    unset TOK
  else c_ok "podman secret fin-terminal-tunnel-token 已存在（換 token：podman secret rm fin-terminal-tunnel-token 後重跑）"; fi
  env_fill "$CFG/fin-terminal.env" FT_PUBLIC_HOSTS "$PUBHOST"
  env_fill "$CFG/fin-terminal.env" FT_PUBLIC_SIGNUP off
  env_fill "$CFG/fin-terminal.env" FT_PUBLIC_TOTP required
  # make sure someone can log in before the site goes public
  if ! podman exec fin-terminal python /app/terminal/auth.py list 2>/dev/null | grep -q .; then
    c_warn "還沒有任何帳號：先建立一個（公開網址不能註冊）"
    read -rp "   帳號名稱： " FTU
    [[ -n $FTU ]] && podman exec -it fin-terminal python /app/terminal/auth.py add "$FTU" || true
  fi
  if grep -q '^FT_PUBLIC_TOTP=required' "$CFG/fin-terminal.env"; then
    for u in $(podman exec fin-terminal python /app/terminal/auth.py list 2>/dev/null | awk -F'\t' '$5 ~ /關/ {print $1}'); do
      c_warn "帳號 $u 還沒有兩步驟驗證（公開網址要求 TOTP，沒設就只能從本機／tailnet 登入）"
      read -rp "   現在幫 $u 設定 TOTP？[Y/n] " yn
      [[ ${yn:-Y} =~ ^[Yy]$ ]] && podman exec -it fin-terminal python /app/terminal/auth.py totp "$u" || true
    done
  fi
  put_unit quadlet/fin-terminal-tunnel.container "$QDIR/fin-terminal-tunnel.container"
  systemctl --user daemon-reload
  systemctl --user restart fin-terminal.service   # pick up FT_PUBLIC_HOSTS
  systemctl --user restart fin-terminal-tunnel.service
  sleep 6
  if systemctl --user is-active --quiet fin-terminal-tunnel.service; then c_ok "fin-terminal-tunnel 執行中 → https://$PUBHOST"
  else c_err "fin-terminal-tunnel 沒起來 → journalctl --user -u fin-terminal-tunnel -n 50"; fi
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 20 "https://$PUBHOST/api/settings" || true)
  [[ $code == 401 ]] && c_ok "公開網址未登入 → 401（有擋住）" || c_warn "公開網址未登入回 $code（預期 401；DNS 剛建可能要等一下）"
  c_warn "建議再加一層 Cloudflare Access（Zero Trust → Access → Applications → $PUBHOST，只允許你的 email）"
fi

step "完成"
echo "資料：   $DATA_ROOT/data（工作紀錄、快取、設定、world.sqlite）  $DATA_ROOT/gods-eye-view（快取、日誌）"
echo "本機：   http://127.0.0.1:8130     God's Eye View：http://127.0.0.1:4173"
echo "Tailnet：./install.sh --no-build --tailscale   （https :8130 與 :4173）"
echo "金鑰：   $CFG/gods-eye-view.env（AISSTREAM_API_KEY 才有船舶資料）→ systemctl --user restart gods-eye-view"
echo "Obsidian：$CFG/obsidian.env → 筆記在 vault 的 ${FT_OBSIDIAN_FOLDER:-finance}/AI 預測/"
echo "研究：   策略研究的資料庫在背景從官方來源回補（月營收／財報約 1 小時、日資料 5 年約 6–8 小時），進度看「資料庫」功能鍵"
echo "Laya：   第一次啟動會下載約 1.7 GB 模型到 $DATA_ROOT/data/laya（journalctl --user -u ft-laya -f 看進度）"
echo "日誌：   journalctl --user -u fin-terminal -u gods-eye-view -u ft-tradingagents -u ft-timesfm -u ft-laya -u ft-obsidian-apply -f"
echo "Hermes： ./scripts/install-hermes-skill.sh        （LINE / Signal 查股價、世界事件、跑 AI 分析）"
echo "Open WebUI：Workspace → Tools → ＋，貼上 openwebui/ft_terminal_tool.py"
