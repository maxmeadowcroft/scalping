"""Per-item Target monitor: refresh until in stock, then buy immediately.

Architecture
------------
Each enabled item in configuration.json is monitored independently:

1. Clear the cart so leftover lines do not mix into this purchase.
2. Open the product detail page (PDP).
3. If out of stock / unknown → sleep (interval + jitter) → reload → repeat.
4. If in stock → add to cart (prefer Order Pickup when configured) → checkout.
5. By default dry_run stops on the review page (no Place order click).

Sequential mode reuses one Chrome profile. Parallel mode clones the profile
per item so Chrome profile locks do not block other workers.
"""

from __future__ import annotations

import argparse
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from botasaurus.browser import Driver, browser

from scraping.config import AppConfig, ItemConfig, load_config
from scraping.runtime import (
    CHROME_ADD_ARGUMENTS,
    PROFILE_DIR,
    parallel_profile_dir,
    prepare_browser_profile,
    prepare_runtime,
)
from scraping.target_checkout import (
    CheckoutResult,
    cart_has_items,
    choose_fulfillment_and_checkout,
    clear_cart,
    trim_cart_to_max_lines,
)
from scraping.target_stock import (
    StockStatus,
    add_to_cart,
    check_stock,
    dismiss_post_atc_modals,
    open_product,
)


TERMINAL_STATUSES = frozenset({"ready", "purchased", "atc_failed", "error"})


@dataclass
class ItemRunResult:
    label: str
    url: str
    status: str
    detail: str
    checkout: CheckoutResult | None = None


def _sleep_poll(config: AppConfig) -> None:
    base = max(0.5, float(config.refresh_interval_seconds))
    jitter = max(0.0, float(config.refresh_jitter_seconds))
    delay = base + (random.uniform(0, jitter) if jitter else 0.0)
    time.sleep(delay)


def _attempt_purchase(driver: Driver, item: ItemConfig, config: AppConfig) -> ItemRunResult:
    """Assume the driver is already on the item PDP."""
    stock = check_stock(driver, item, navigate=False)
    if stock.status != StockStatus.IN_STOCK:
        return ItemRunResult(
            label=item.label,
            url=item.normalized_url,
            status=stock.status.value,
            detail=stock.reason,
        )

    print(f"[IN STOCK] {item.label} ({item.tcin}) — adding to cart")
    atc_ok = False
    for attempt in range(1, config.max_atc_retries + 1):
        # Never ATC on top of an existing cart — that creates ship+pickup twins.
        try:
            clear_cart(driver)
        except Exception as exc:
            print(f"[CART] pre-ATC clear skipped: {exc}")
        open_product(driver, item, force_navigate=True)
        driver.sleep(1.0)
        if add_to_cart(driver, item, prefer_pickup=config.prefer_pickup):
            dismiss_post_atc_modals(driver)
            driver.sleep(2.5)  # let Target cart sync before we verify
            if cart_has_items(driver):
                trim_cart_to_max_lines(driver, max_lines=1)
                atc_ok = True
                break
            print(f"[ATC] click ok but cart empty; retry {attempt}")
            continue
        print(f"[ATC] click failed; retry {attempt}/{config.max_atc_retries}")

    if not atc_ok:
        return ItemRunResult(
            label=item.label,
            url=item.normalized_url,
            status="atc_failed",
            detail=f"Could not add to cart after {config.max_atc_retries} tries",
        )

    checkout = choose_fulfillment_and_checkout(driver, config)
    print(f"[CHECKOUT] {item.label}: {checkout.message}")
    return ItemRunResult(
        label=item.label,
        url=item.normalized_url,
        status="purchased" if checkout.placed_order else "ready",
        detail=checkout.message,
        checkout=checkout,
    )


def monitor_item_until_bought(
    driver: Driver,
    item: ItemConfig,
    config: AppConfig,
    *,
    max_attempts: int | None = None,
    clear_cart_first: bool = True,
) -> ItemRunResult:
    """Refresh a single PDP until in stock, then checkout that item alone."""
    if clear_cart_first:
        try:
            print(f"[CART] clearing before {item.label}")
            clear_cart(driver)
        except Exception as exc:
            print(f"[CART] clear skipped: {exc}")

    attempt = 0
    while True:
        attempt += 1
        print(f"[POLL {attempt}] {item.label} → {item.normalized_url}")
        open_product(driver, item, force_navigate=(attempt == 1))
        result = _attempt_purchase(driver, item, config)

        if result.status in TERMINAL_STATUSES:
            return result

        if max_attempts is not None and attempt >= max_attempts:
            return ItemRunResult(
                label=item.label,
                url=item.normalized_url,
                status=result.status,
                detail=f"max_attempts={max_attempts}; last={result.detail}",
            )

        _sleep_poll(config)


def _result_to_dict(r: ItemRunResult) -> dict:
    return {
        "label": r.label,
        "url": r.url,
        "status": r.status,
        "detail": r.detail,
        "fulfillment": r.checkout.fulfillment.value if r.checkout else None,
        "placed_order": r.checkout.placed_order if r.checkout else False,
    }


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    reuse_driver=True,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def run_items_sequentially(driver: Driver, data: dict):
    """Process each enabled item one-by-one (buy ASAP per item)."""
    config: AppConfig = data["config"]
    max_attempts = data.get("max_attempts")
    clear_cart_first = bool(data.get("clear_cart_first", True))
    results: list[ItemRunResult] = []

    for item in config.enabled_items:
        print("\n" + "=" * 60)
        print(f"ITEM: {item.label}  qty={item.max_quantity}")
        print("=" * 60)
        result = monitor_item_until_bought(
            driver,
            item,
            config,
            max_attempts=max_attempts,
            clear_cart_first=clear_cart_first,
        )
        results.append(result)
        print(f"[DONE] {item.label}: {result.status} — {result.detail}")

    return [_result_to_dict(r) for r in results]


def _make_single_item_runner(profile: Path):
    prepare_browser_profile(profile)

    @browser(
        profile=str(profile),
        tiny_profile=False,
        headless=False,
        block_images=False,
        output=None,
        add_arguments=CHROME_ADD_ARGUMENTS,
        close_on_crash=True,
    )
    def _run(driver: Driver, data: dict):
        config: AppConfig = data["config"]
        item: ItemConfig = data["item"]
        max_attempts = data.get("max_attempts")
        clear_cart_first = bool(data.get("clear_cart_first", True))
        result = monitor_item_until_bought(
            driver,
            item,
            config,
            max_attempts=max_attempts,
            clear_cart_first=clear_cart_first,
        )
        return _result_to_dict(result)

    return _run


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def run_single_item(driver: Driver, data: dict):
    config: AppConfig = data["config"]
    item: ItemConfig = data["item"]
    max_attempts = data.get("max_attempts")
    clear_cart_first = bool(data.get("clear_cart_first", True))
    result = monitor_item_until_bought(
        driver,
        item,
        config,
        max_attempts=max_attempts,
        clear_cart_first=clear_cart_first,
    )
    return _result_to_dict(result)


def run_items_parallel(config: AppConfig, *, max_attempts: int | None = None, clear_cart_first: bool = True) -> list[dict]:
    """One browser per item so stock on item A never blocks buying item B."""

    runners = []
    for index, item in enumerate(config.enabled_items):
        profile = parallel_profile_dir(item.label, index)
        runners.append((item, _make_single_item_runner(profile)))

    def _worker(item: ItemConfig, runner) -> dict:
        return runner(
            {
                "config": config,
                "item": item,
                "max_attempts": max_attempts,
                "clear_cart_first": clear_cart_first,
            }
        )

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(runners) or 1) as pool:
        futures = {
            pool.submit(_worker, item, runner): item for item, runner in runners
        }
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Target stock monitor + checkout bot. "
            "Out-of-stock PDPs refresh until buyable; each item is purchased ASAP."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to configuration.json (default: scraping/configuration.json)",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Open one browser per item (cloned profiles; buys ASAP independently)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Stop polling after N attempts per item (omit to run until bought)",
    )
    parser.add_argument(
        "--place-order",
        action="store_true",
        help="Override config to actually place orders (requires .env CARD_*)",
    )
    parser.add_argument(
        "--no-clear-cart",
        action="store_true",
        help="Skip clearing the cart before each item (not recommended)",
    )
    args = parser.parse_args(argv)

    prepare_runtime()
    config = load_config(args.config)
    if args.place_order:
        config = config.with_place_order(True)

    clear_cart_first = not args.no_clear_cart

    print(f"Items: {len(config.enabled_items)}")
    print(f"dry_run={config.dry_run} place_order={config.place_order}")
    print(f"prefer_pickup={config.prefer_pickup} store={config.preferred_store_name}")
    print(f"ship_to={config.shipping_address.as_single_line()}")
    print(
        f"poll every {config.refresh_interval_seconds}s "
        f"(+0..{config.refresh_jitter_seconds}s jitter)"
    )
    pay = config.payment
    if config.place_order:
        print(
            f"payment ready={pay.is_complete} "
            f"use_saved_card={pay.use_saved_card} "
            f"has_cvv={pay.has_cvv} "
            f"auth_timeout={config.checkout_auth_timeout_seconds}s"
        )
        if not pay.is_complete:
            print(
                "WARNING: .env payment incomplete — "
                "need CARD_CVV (and full CARD_* if USE_SAVED_CARD=false)"
            )

    if not config.enabled_items:
        raise SystemExit("No enabled items in configuration.json")

    if args.parallel and len(config.enabled_items) > 1:
        results = run_items_parallel(
            config,
            max_attempts=args.max_attempts,
            clear_cart_first=clear_cart_first,
        )
    else:
        results = run_items_sequentially(
            {
                "config": config,
                "max_attempts": args.max_attempts,
                "clear_cart_first": clear_cart_first,
            }
        )

    print("\n=== SUMMARY ===")
    for row in results:
        placed = "ORDER PLACED" if row.get("placed_order") else row.get("status")
        print(f"- {row['label']}: {placed} — {row['detail']}")


if __name__ == "__main__":
    main()
