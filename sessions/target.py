"""CLI: capture / refresh Target.com Chrome session via email OTP.

  ./sessions/run_target_session.sh              # ensure logged in (auto OTP)
  ./sessions/run_target_session.sh --force      # force re-login
  ./sessions/run_target_session.sh --check      # print signed_in only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scalping.bots.target.session import (
    ensure_target_session,
    is_target_session_logged_in,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Target session login + capture")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force a fresh email-OTP login even if already signed in",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check whether the bot profile is signed in",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for Gmail OTP",
    )
    args = parser.parse_args(argv)

    if args.check:
        ok = is_target_session_logged_in()
        print(json.dumps({"signed_in": ok}, indent=2))
        return 0 if ok else 2

    try:
        meta = ensure_target_session(force=args.force, timeout=args.timeout)
    except Exception as exc:
        print(f"[LOGIN] FAILED: {exc}")
        return 1
    print(json.dumps(meta, indent=2, default=str))
    if not meta.get("signed_in", True):
        # save_session may set signed_in from homepage text; re-check loosely
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
