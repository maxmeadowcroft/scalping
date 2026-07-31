#!/usr/bin/env bash
# Launch from $HOME so macOS Desktop TCC cannot break uv/Botasaurus getcwd().
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$HOME"
exec uv run --directory "$PROJECT_DIR" python sessions/target.py
