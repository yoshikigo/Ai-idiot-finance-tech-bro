#!/usr/bin/env bash
# Run every unit test, each engine in its own venv (their pinned deps clash with the terminal's).
#   ./tests/run_all.sh            all groups; a group whose venv/checkout is missing is skipped
#   ./tests/run_all.sh -x -k foo  extra args go to every pytest run
# Paths (override with env): FT_VENVS=~/.venvs, FT_SRC=~/src (Kronos checkout, Laya fake bundle), FT_NODE (dir with node).
set -uo pipefail
cd "$(dirname "$0")/.."
VENVS=${FT_VENVS:-$HOME/.venvs} SRC=${FT_SRC:-$HOME/src}
NODE=${FT_NODE:-/opt/homebrew/opt/node@24/bin}
[[ -d $NODE ]] && export PATH=$NODE:$PATH
pass=() fail=() skip=()

run() {  # name venv [env...] -- tests...
  local name=$1 py=$VENVS/$2/bin/python; shift 2
  local env=(); while [[ $1 != -- ]]; do env+=("$1"); shift; done; shift
  [[ -x $py ]] || { skip+=("${name}（沒有 ${py}）"); return; }
  printf '\n\e[1m== %s\e[0m\n' "$name"
  if env ${env[@]+"${env[@]}"} "$py" -m pytest -q "$@" ${ARGS[@]+"${ARGS[@]}"}; then pass+=("$name"); else fail+=("$name"); fi
}
ARGS=("$@")

LAYA=()
if [[ -d $SRC/laya-fake-bundle ]] && command -v node >/dev/null && [[ -d engines/laya/sidecar/node_modules ]]; then
  LAYA=(LAYA_TEST_MODEL_DIR="$SRC/laya-fake-bundle" LAYA_SIDECAR="$PWD/engines/laya/sidecar/server.mjs")
else
  skip+=("Laya 端對端（要 node、sidecar 的 node_modules、${SRC}/laya-fake-bundle）")
fi

run terminal fin_terminal ${LAYA[@]+"${LAYA[@]}"} -- tests --ignore-glob='tests/test_ta_*.py' --ignore=tests/test_kronos.py
run tradingagents ft_tradingagents PYTHONPATH=common:engines/tradingagents -- tests/test_ta_*.py
if [[ -d $SRC/Kronos ]]; then
  run kronos ft_kronos KRONOS_SRC="$SRC/Kronos" -- tests/test_kronos.py
else
  skip+=("kronos（沒有 ${SRC}/Kronos）")
fi

echo
for n in ${pass[@]+"${pass[@]}"}; do printf '\e[32m✓\e[0m %s\n' "$n"; done
for n in ${skip[@]+"${skip[@]}"}; do printf '\e[33m–\e[0m 略過 %s\n' "$n"; done
for n in ${fail[@]+"${fail[@]}"}; do printf '\e[31m✗\e[0m %s\n' "$n"; done
[[ ${#fail[@]} -eq 0 ]]
