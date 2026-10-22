#!/usr/bin/env bash
# Stop and remove Fin Terminal units.   ./uninstall.sh [--images] [--purge]
#   --images  also remove the built images
#   --purge   also delete the old fin-terminal-data volume (data on the external drive is NEVER deleted automatically)
#   --hermes  also remove the fin-terminal Hermes skill and restart Hermes
set -euo pipefail
QDIR="$HOME/.config/containers/systemd"
UNITS=(fin-terminal ft-fingpt ft-research ft-tradingagents ft-hedgefund ft-qlib ft-finrl ft-timesfm ft-laya ft-kronos gods-eye-view)
for u in "${UNITS[@]}"; do
  systemctl --user stop "$u.service" 2>/dev/null || true
  rm -f "$QDIR/$u.container" "$QDIR/$u.container.d/ft-dns.conf"; rmdir "$QDIR/$u.container.d" 2>/dev/null || true
done
systemctl --user disable --now ft-obsidian-apply.path ft-obsidian-apply.timer 2>/dev/null || true
rm -f "$HOME"/.config/systemd/user/ft-obsidian-apply.{service,path,timer} "$HOME/.local/share/fin-terminal/ft-obsidian-apply.sh"
systemctl --user daemon-reload
echo "units removed (incl. gods-eye-view, ft-obsidian-apply)"
for a in "$@"; do
  case "$a" in
    --images) podman rmi -f localhost/fin-terminal localhost/ft-tradingagents localhost/ft-hedgefund localhost/ft-qlib localhost/ft-finrl localhost/ft-timesfm localhost/ft-laya localhost/ft-kronos localhost/gods-eye-view 2>/dev/null || true; echo "images removed" ;;
    --purge) podman volume rm -f fin-terminal-data 2>/dev/null || true; echo "volume fin-terminal-data removed" ;;
    --hermes) podman unshare rm -rf "$HOME/.hermes/skills/fin-terminal" && systemctl --user restart hermes.service && echo "Hermes skill removed" ;;
  esac
done
command -v tailscale >/dev/null && echo "如有設定：tailscale serve --https=8130 off ; tailscale serve --https=4173 off"
echo "資料保留在 ${FT_DATA_ROOT:-~/fin-terminal-data}/（data、gods-eye-view）— 不要了請自己刪；Obsidian 筆記留在 vault"
echo "設定檔保留在 ~/.config/fin-terminal/；Open WebUI 的 Tool 請在介面裡刪"
