#!/usr/bin/env bash
# Unified multi-bot launcher.
# Examples:
#   ./scalping/run.sh list
#   ./scalping/run.sh run target -- --config configs/target/tonight.json --place-order
#   ./scalping/run.sh run round1 -- --probe
#   ./scalping/run.sh session target
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$HOME"
exec uv run --directory "$PROJECT_DIR" python -m scalping "$@"
