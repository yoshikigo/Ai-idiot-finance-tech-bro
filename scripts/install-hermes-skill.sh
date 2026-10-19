#!/usr/bin/env bash
# Install / update the `fin-terminal` Hermes skill (LINE + Signal).
#   ./scripts/install-hermes-skill.sh            copy skill, test from inside the Hermes container, restart Hermes if changed
#   NO_RESTART=1 ./scripts/install-hermes-skill.sh
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONTAINER="${HERMES_CONTAINER:-hermes}"
UNIT="${HERMES_UNIT:-hermes.service}"
DEST="$HERMES_HOME/skills/fin-terminal"
OWNER="${HERMES_UID:-9999}"   # container uid that owns ~/.hermes (host uid = your subuid base + 9999)

ok()   { printf '\e[32m✓\e[0m %s\n' "$*"; }
warn() { printf '\e[33m!\e[0m %s\n' "$*"; }
die()  { printf '\e[31m✗\e[0m %s\n' "$*" >&2; exit 1; }

[[ -d "$HERMES_HOME" ]] || die "找不到 $HERMES_HOME"
curl -fsS -m 5 http://127.0.0.1:8130/healthz >/dev/null || warn "FT Terminal（127.0.0.1:8130）目前沒回應，skill 仍會安裝"

new_sum=$(cd hermes/fin-terminal && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16)
old_sum=$(podman unshare sh -c "cd '$DEST' 2>/dev/null && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16" 2>/dev/null || true)

podman unshare sh -c "mkdir -p '$DEST' && rm -rf '$DEST'/* && cp -r hermes/fin-terminal/. '$DEST'/ && chown -R $OWNER:$OWNER '$DEST' && chmod 0755 '$DEST/scripts/ft.py'"
ok "skill 已複製到 $DEST（擁有者 $OWNER）"

if podman container exists "$CONTAINER"; then
  if out=$(podman exec "$CONTAINER" python3 /opt/data/skills/fin-terminal/scripts/ft.py quote 2330 2>&1); then
    ok "Hermes 容器內測試：$(head -n1 <<<"$out")"
    grep -q "連不到" <<<"$out" && warn "Hermes 容器連不到 127.0.0.1:8130（Hermes 需要 Network=host）"
  else
    warn "容器內測試失敗：$out"
  fi
else
  warn "找不到容器 $CONTAINER，略過測試"
fi

if [[ "$new_sum" == "$old_sum" ]]; then
  ok "skill 內容沒變，不重啟 Hermes"
elif [[ "${NO_RESTART:-0}" == 1 ]]; then
  warn "skill 有變動，記得重啟：systemctl --user restart $UNIT"
else
  systemctl --user restart "$UNIT" && ok "已重啟 $UNIT（LINE / Signal 會中斷幾秒）"
fi

cat <<'EOF'

在 LINE / Signal 試試：
  「台積電現在多少？」「00878 折溢價和配息」「哪些 ETF 溢價最高」
  「統一黑馬基金今年報酬」「幫我用 TradingAgents 分析 2330」
每日盤後簡報（跟 Hermes 說）：
  「每個交易日下午 2 點用 fin-terminal 的 brief 傳給我」
EOF
