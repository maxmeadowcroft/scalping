"""Round1 Shortstack drop bot (API + optional paid Turnstile solver).

Examples
--------
# Plumbing test against the current URL (no solver needed):
./scraping/run_round1.sh --probe

# Also buy a Turnstile token and see if /campaigns/start returns a session:
./scraping/run_round1.sh --probe --solve

# Live: poll until page ready, solve Turnstile, submit all entries:
./scraping/run_round1.sh
./scraping/run_round1.sh --dry-run   # query_only=true (no real entry if API allows)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from scraping.round1_api import (
    load_round1_config,
    pick_location,
    poll_until_ready,
    probe,
    submit_all_people,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Round1 / Shortstack API bot")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to configuration.round1.json",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Test fetch + bootData + /campaigns/start (safe on a closed drop)",
    )
    parser.add_argument(
        "--solve",
        action="store_true",
        help="With --probe, also call CapSolver/2Captcha for a Turnstile token",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Submit with query_only=true when possible (no lasting entry)",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Override campaign URL",
    )
    parser.add_argument(
        "--phase",
        type=str,
        default=None,
        help="Override phase number (e.g. 4)",
    )
    args = parser.parse_args(argv)

    config = load_round1_config(args.config)
    if args.url:
        config = replace(config, url=args.url)
    if args.phase:
        config = replace(config, phase=args.phase)

    people = config.people()
    print(f"url={config.url}")
    print(f"solver={config.solver} key={'set' if config.solver_api_key else 'MISSING'}")
    print(f"phase={config.phase} people={len(people)} parallel={config.parallel}")
    for i, p in enumerate(people, 1):
        print(f"  {i}. {p.first_name} {p.last_name} <{p.email}>")

    if args.probe:
        result = probe(config, try_solver=args.solve)
        print("\n=== PROBE DONE ===")
        print(json.dumps({"ok": True, "http": result.get("summary", {}).get("http_status")}, indent=2))
        return 0

    print("[RUN] polling until campaign bootData is ready…")
    boot = poll_until_ready(config)
    location = pick_location(boot.location_choices, config.location_match)
    if not location:
        print(
            "[FATAL] no location matched "
            f"{config.location_match!r}. Choices sample: {boot.location_choices[:5]}"
        )
        return 2
    print(f"[RUN] location={location!r}")

    if not boot.turnstile_site_key:
        print("[FATAL] no turnstile_site_key — cannot submit")
        return 2

    query_only = bool(args.dry_run) or not config.submit
    results = submit_all_people(
        config, boot, location=location, query_only=query_only
    )
    ok_n = sum(1 for r in results if r.get("ok"))
    print("\n=== SUMMARY ===")
    print(json.dumps(results, indent=2, default=str)[:4000])
    print(f"accepted {ok_n}/{len(results)}")
    if ok_n == 0:
        return 4
    if ok_n < len(results):
        return 5
    print("[DONE] all submits accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
