#!/usr/bin/env bash
# Target bot launcher (dry-run by default when config says so).
# Examples:
#   ./scripts/run-target.sh
#   ./scripts/run-target.sh --config configs/target/tonight.json --place-order
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$HOME"
# Resolve relative --config against the repo (we chdir to $HOME for macOS TCC).
args=()
prev=""
for arg in "$@"; do
  if [[ "$prev" == "--config" && "$arg" != /* ]]; then
    args+=("$PROJECT_DIR/$arg")
  else
    args+=("$arg")
  fi
  prev="$arg"
done
exec uv run --directory "$PROJECT_DIR" python -m scalping.bots.target.cli "${args[@]}"
