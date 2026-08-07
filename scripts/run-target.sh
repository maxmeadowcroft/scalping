#!/usr/bin/env bash
# Target bot launcher (dry-run by default).
# Examples:
#   ./scripts/run-target.sh
#   ./scripts/run-target.sh --config configs/target/tonight.json --place-order
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$HOME"
exec uv run --directory "$PROJECT_DIR" python -m scalping.bots.target.cli "$@"
