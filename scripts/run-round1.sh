#!/usr/bin/env bash
# Round1 / Shortstack launcher.
# Examples:
#   ./scripts/run-round1.sh --probe
#   ./scripts/run-round1.sh --config configs/round1/default.json
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$HOME"
exec uv run --directory "$PROJECT_DIR" python -m scalping.bots.round1.cli "$@"
