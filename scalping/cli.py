"""Unified CLI: python -m scalping <command> <bot>.

Examples
--------
  uv run python -m scalping list
  uv run python -m scalping health target
  uv run python -m scalping run target -- --config configs/target/tonight.json --place-order
  uv run python -m scalping run round1 -- --probe
  uv run python -m scalping session target
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scalping.core.bot import BotContext, run_bot
from scalping.core.logging import setup_logging
from scalping.core.paths import ensure_data_dirs
from scalping.core.registry import get_bot, list_bots


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scalping",
        description="Multi-bot drop scalping platform",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="DEBUG|INFO|WARNING|ERROR",
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        help="Emit JSON logs (better for aggregators)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List registered bots")

    p_health = sub.add_parser("health", help="Run bot healthcheck")
    p_health.add_argument("bot", help="Bot id (target, round1, …)")
    p_health.add_argument("--config", type=Path, default=None)

    p_run = sub.add_parser("run", help="Run a bot")
    p_run.add_argument("bot", help="Bot id")
    p_run.add_argument("--config", type=Path, default=None)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--place-order", action="store_true")
    p_run.add_argument(
        "--force-login",
        action="store_true",
        help="Force session refresh before run (bots that support it)",
    )
    p_run.add_argument(
        "bot_args",
        nargs=argparse.REMAINDER,
        help="Args after -- are forwarded to the underlying bot CLI",
    )

    p_session = sub.add_parser("session", help="Ensure / refresh bot session")
    p_session.add_argument("bot", help="Bot id (currently: target)")
    p_session.add_argument("--force", action="store_true")
    p_session.add_argument("--timeout", type=float, default=120.0)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    setup_logging(level=args.log_level, json_logs=args.json_logs)
    ensure_data_dirs()

    if args.command == "list":
        for bot_id in list_bots():
            bot = get_bot(bot_id)
            print(f"{bot.id:12}  {bot.name} — {bot.description}")
        return 0

    if args.command == "health":
        bot = get_bot(args.bot)
        ctx = BotContext(config_path=args.config)
        result = bot.healthcheck(ctx)
        print(json.dumps(result.__dict__, indent=2, default=str))
        return result.exit_code

    if args.command == "session":
        if args.bot.lower() != "target":
            print(f"session refresh not implemented for bot={args.bot!r}", file=sys.stderr)
            return 2
        from scalping.bots.target.session import ensure_target_session

        meta = ensure_target_session(force=args.force, timeout=args.timeout)
        print(json.dumps(meta, indent=2, default=str))
        return 0 if meta.get("signed_in", True) else 1

    if args.command == "run":
        bot = get_bot(args.bot)
        forwarded = list(args.bot_args or [])
        if forwarded and forwarded[0] == "--":
            forwarded = forwarded[1:]
        ctx = BotContext(
            config_path=args.config,
            dry_run=bool(args.dry_run),
            place_order=bool(args.place_order),
            argv=forwarded,
            extra={
                "force_login": bool(args.force_login),
            },
        )
        result = run_bot(bot, ctx)
        return result.exit_code

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
