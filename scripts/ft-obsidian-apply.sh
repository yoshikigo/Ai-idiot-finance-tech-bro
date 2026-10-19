#!/usr/bin/env bash
# Move FT Terminal notes from the outbox (data drive) into the Obsidian vault.
# Runs as you (systemd user .path/.timer). Does nothing unless the vault is mounted and is a vault.
#   FT_OUTBOX  default ~/fin-terminal-data/data/obsidian-outbox
#   FT_VAULT   default ~/Obsidian/vault
set -euo pipefail
OUTBOX="${FT_OUTBOX:-$HOME/fin-terminal-data/data/obsidian-outbox}"
VAULT="${FT_VAULT:-$HOME/Obsidian/vault}"
[[ -d "$OUTBOX" ]] || exit 0
if [[ ! -d "$VAULT/.obsidian" ]]; then
  echo "vault not available ($VAULT/.obsidian missing) — keeping notes in the outbox"; exit 0
fi
n=0
while IFS= read -r -d '' f; do
  rel="${f#"$OUTBOX"/}"
  dest="$VAULT/$rel"
  mkdir -p "$(dirname "$dest")"
  # write next to the target, then rename: Obsidian/LiveSync never sees a half-written note
  cp -- "$f" "$dest.ft-tmp" && mv -f -- "$dest.ft-tmp" "$dest" && rm -f -- "$f"
  n=$((n + 1))
done < <(find "$OUTBOX" -type f -name '*.md' -print0)
find "$OUTBOX" -mindepth 1 -type d -empty -delete 2>/dev/null || true
[[ $n -gt 0 ]] && echo "moved $n note(s) into $VAULT"
exit 0
