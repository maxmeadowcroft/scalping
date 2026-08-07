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
        if not ok:
            print(
                "Logged out — run without --check to auto email-OTP login:\n"
                "  ./scripts/session-target.sh\n"
                "Or just start the bot; it re-logins in-browser if needed:\n"
                "  ./scripts/run-target.sh --config configs/target/tonight.json --place-order",
                file=sys.stderr,
            )
        return 0 if ok else 2

    try:
        meta = ensure_target_session(force=args.force, timeout=args.timeout)
    except Exception as exc:
        print(f"[LOGIN] FAILED: {exc}", file=sys.stderr)
        return 1

    if not meta:
        print(
            "[LOGIN] FAILED: no session result (browser task aborted).\n"
            "Target may be soft-blocking this profile. Wait, then sign in manually\n"
            "in ~/.scalping/chrome-profiles/target or retry:\n"
            "  ./scripts/session-target.sh --force",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(meta, indent=2, default=str))
    if not meta.get("signed_in"):
        err = meta.get("error") or "not_signed_in"
        print(
            f"[LOGIN] not signed in ({err}).\n"
            "If you saw Target's 'Something went wrong' banner, wait before retrying.\n"
            "Spam-clicking Continue makes the block worse.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
