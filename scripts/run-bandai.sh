#!/usr/bin/env bash
# Premium Bandai US bot launcher.
# Examples:
#   ./scripts/run-bandai.sh --probe
#   ./scripts/run-bandai.sh --dry-run
#   ./scripts/run-bandai.sh --place-order
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$HOME"
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
exec uv run --directory "$PROJECT_DIR" python -m scalping.bots.bandai.cli "${args[@]}"
