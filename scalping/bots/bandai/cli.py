"""Premium Bandai US bot CLI.

Examples
--------
./scripts/run-bandai.sh --probe
./scripts/run-bandai.sh --dry-run
./scripts/run-bandai.sh --place-order
./scripts/run-bandai.sh --ensure-session
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scalping.bots.bandai.api import load_bandai_config, probe_apis, probe_browser
from scalping.bots.bandai.runtime import prepare_runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Premium Bandai US bot")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to Bandai config JSON (default: configs/bandai/default.json)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Exercise member/product/cart APIs with saved cookies",
    )
    parser.add_argument(
        "--ensure-session",
        action="store_true",
        help="Login / refresh Bandai cookies and exit",
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="Force password login even if session looks valid",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ATC + open checkout, stop before placing order",
    )
    parser.add_argument(
        "--place-order",
        action="store_true",
        help="Place a real order (uses saved card; optional BANDAI_CARD_CVV)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Stock poll attempts (0 = forever)",
    )
    parser.add_argument(
        "--item-id",
        type=str,
        default=None,
        help="Override product code (e.g. N2904549002)",
    )
    parser.add_argument(
        "--item-url",
        type=str,
        default=None,
        help="Override item URL",
    )
    args = parser.parse_args(argv)

    prepare_runtime()
    config = load_bandai_config(args.config)
    if args.item_id:
        config.item_id = args.item_id
        config.item_url = f"https://p-bandai.com/us/item/{args.item_id}"
    if args.item_url:
        config.item_url = args.item_url
    if args.max_attempts is not None:
        config.max_attempts = args.max_attempts
    if args.dry_run:
        config.dry_run = True
        config.place_order = False
    if args.place_order:
        config.place_order = True
        config.dry_run = False

    print(f"label={config.label} item={config.product_code()} qty={config.qty}")
    print(f"dry_run={config.dry_run} place_order={config.place_order}")

    if args.ensure_session or args.force_login:
        from scalping.bots.bandai.session import ensure_bandai_session

        meta = ensure_bandai_session(force=bool(args.force_login))
        print(json.dumps(meta, indent=2, default=str))
        return 0 if meta.get("signed_in") else 2

    if args.probe:
        print("[BANDAI] HTTP cookie probe…")
        http_out = probe_apis(config)
        print("[BANDAI] browser session probe…")
        out = probe_browser(config)
        logged = bool(out.get("member_logged_in")) or bool(
            (http_out.get("member") or {}).get("logged_in")
        )
        return 0 if logged else 3

    from scalping.bots.bandai.checkout import run_buy_pipeline

    result = run_buy_pipeline(
        config,
        dry_run=config.dry_run,
        place_order=config.place_order,
        ensure_session=True,
    )
    print(
        json.dumps(
            {
                "ok": result.placed_order or result.dry_run,
                "message": result.message,
                "dry_run": result.dry_run,
                "placed_order": result.placed_order,
                "order_number": result.order_number,
                "checkout_sn": result.checkout_sn,
                "capture_path": result.capture_path,
            },
            indent=2,
            default=str,
        )
    )
    if result.placed_order:
        return 0
    if result.dry_run:
        return 0
    return 4


if __name__ == "__main__":
    sys.exit(main())
