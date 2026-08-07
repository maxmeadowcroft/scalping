#!/usr/bin/env bash
# Target session: auto email-OTP login + cookie save.
# Examples:
#   ./scripts/session-target.sh
#   ./scripts/session-target.sh --check
#   ./scripts/session-target.sh --force
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec "$PROJECT_DIR/sessions/run_target_session.sh" "$@"
