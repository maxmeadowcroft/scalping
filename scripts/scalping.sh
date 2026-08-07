#!/usr/bin/env bash
# Unified platform CLI.
#   ./scripts/scalping.sh list
#   ./scripts/scalping.sh run target -- --config configs/target/tonight.json
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$PROJECT_DIR/scalping/run.sh" "$@"
