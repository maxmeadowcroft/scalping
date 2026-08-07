"""Per-item Target monitor: refresh until in stock, then buy immediately.

Architecture
------------
Each enabled item in the Target config JSON is monitored independently:

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
from dataclasses import dataclass, replace
from pathlib import Path

from botasaurus.browser import Driver, browser

from scalping.bots.target.config import AppConfig, ItemConfig, load_config
from scalping.bots.target.runtime import (
    CHROME_ADD_ARGUMENTS,
    PROFILE_DIR,
    parallel_profile_dir,
    prepare_browser_profile,
    prepare_runtime,
)
from scalping.bots.target.session import (
    ensure_signed_in_on_driver,
    looks_logged_out_wall,
)
from scalping.bots.target.checkout import (
    CheckoutResult,
    cart_has_items,
    cart_line_count,
    choose_fulfillment_and_checkout,
    clear_cart,
    open_cart_after_atc,
    trim_cart_to_max_lines,
)
from scalping.bots.target.stock import (
    StockStatus,
    add_to_cart,
    check_stock,
    dismiss_post_atc_modals,
    open_product,
)
from scalping.bots.target.api import poll_fulfillment_api, warm_cart_session


TERMINAL_STATUSES = frozenset({"ready", "purchased", "error"})


@dataclass
class ItemRunResult:
    label: str
    url: str
    status: str
    detail: str
    checkout: CheckoutResult | None = None


def _sleep_poll(config: AppConfig) -> None:
    base = max(0.08, float(config.refresh_interval_seconds))
    jitter = max(0.0, float(config.refresh_jitter_seconds))
    delay = base + (random.uniform(0, jitter) if jitter else 0.0)
    time.sleep(delay)


def _poll_stock(
    driver: Driver,
    item: ItemConfig,
    config: AppConfig,
    *,
    attempt: int,
):
    """Fast stock poll: Redsky in-browser every tick; DOM reload every few polls."""
    # Keep PDP cookies / CORS context warm — navigate only when needed.
    need_nav = attempt == 1 or attempt % 8 == 0
    if need_nav:
        open_product(driver, item, force_navigate=(attempt == 1))

    api = poll_fulfillment_api(
        driver,
        tcin=item.tcin or "",
        zip_code=config.shipping_address.zip or "87111",
        state=config.shipping_address.state or "NM",
        prefer_pickup=config.prefer_pickup,
    )
    if api.status == StockStatus.IN_STOCK:
        # Confirm buy-box when API says buyable (avoid related-product false positives
        # on DOM; API is authoritative for allocation).
        print(f"[STOCK] api IN_STOCK — {api.reason} ({api.page_text_excerpt})")
        if need_nav or attempt % 3 == 0:
            dom = check_stock(driver, item, navigate=False)
            if dom.status == StockStatus.OUT_OF_STOCK:
                # API ahead of UI or race — trust API for ATC attempt.
                print(f"[STOCK] DOM says OOS ({dom.reason}) but API buyable — ATC anyway")
        return api

    if api.status == StockStatus.OUT_OF_STOCK:
        return api

    # API unknown / captcha → fall back to DOM
    if need_nav:
        return check_stock(driver, item, navigate=False)
    open_product(driver, item, force_navigate=False)
    return check_stock(driver, item, navigate=False)


def _attempt_purchase(driver: Driver, item: ItemConfig, config: AppConfig) -> ItemRunResult:
    """Assume stock was just confirmed; run ATC + checkout."""
    print(f"[IN STOCK] {item.label} ({item.tcin}) — adding to cart")
    atc_ok = False
    for attempt in range(1, config.max_atc_retries + 1):
        # Under drop traffic, do NOT clear cart between ATC tries — that wastes
        # the window and races other shoppers. Only re-open PDP if needed.
        if attempt > 1:
            open_product(driver, item, force_navigate=True)
        if add_to_cart(driver, item, prefer_pickup=config.prefer_pickup):
            dismiss_post_atc_modals(driver)
            # Jump straight to cart via the drawer CTA — don't sit on the PDP.
            if open_cart_after_atc(driver) or cart_has_items(driver):
                if cart_line_count(driver) > 1:
                    trim_cart_to_max_lines(driver, max_lines=1)
                atc_ok = True
                break
            print(f"[ATC] landed but cart empty; retry {attempt}/{config.max_atc_retries}")
            continue
        print(f"[ATC] failed; retry {attempt}/{config.max_atc_retries}")

    if not atc_ok:
        # Keep polling — drop traffic often means soft fails, not true OOS.
        return ItemRunResult(
            label=item.label,
            url=item.normalized_url,
            status="atc_retry",
            detail=f"ATC soft-fail after {config.max_atc_retries} tries — keep polling",
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
    ensure_login: bool = True,
    login_timeout: float = 90.0,
    force_login: bool = False,
) -> ItemRunResult:
    """Refresh a single PDP until in stock, then checkout that item alone."""
    if ensure_login:
        ok = ensure_signed_in_on_driver(
            driver, timeout=login_timeout, force=force_login
        )
        if not ok:
            return ItemRunResult(
                label=item.label,
                url=item.normalized_url,
                status="error",
                detail="Target login required but auto re-login failed",
            )

    if clear_cart_first:
        try:
            print(f"[CART] clearing before {item.label}")
            clear_cart(driver)
        except Exception as exc:
            print(f"[CART] clear skipped: {exc}")

    # Warm cart session + land on PDP before the hot loop.
    open_product(driver, item, force_navigate=True)
    warm = warm_cart_session(driver)
    print(f"[ATC] pre-warm cart_views status={warm.get('status')}")

    attempt = 0
    relogin_tries = 0
    while True:
        attempt += 1
        print(f"[POLL {attempt}] {item.label} → {item.normalized_url}")

        if looks_logged_out_wall(driver) and ensure_login and relogin_tries < 2:
            relogin_tries += 1
            print(f"[LOGIN] auth wall mid-poll — re-login ({relogin_tries}/2)")
            if not ensure_signed_in_on_driver(driver, timeout=login_timeout, force=True):
                return ItemRunResult(
                    label=item.label,
                    url=item.normalized_url,
                    status="error",
                    detail="Logged out mid-run; re-login failed",
                )
            open_product(driver, item, force_navigate=True)

        stock = _poll_stock(driver, item, config, attempt=attempt)
        print(f"[POLL {attempt}] {stock.status.value} — {stock.reason}")

        if stock.status == StockStatus.IN_STOCK:
            result = _attempt_purchase(driver, item, config)
        else:
            result = ItemRunResult(
                label=item.label,
                url=item.normalized_url,
                status=stock.status.value,
                detail=stock.reason,
            )

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
    ensure_login = not bool(data.get("skip_login_check"))
    login_timeout = float(data.get("login_timeout") or 90)
    force_login = bool(data.get("force_login"))
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
            ensure_login=ensure_login,
            login_timeout=login_timeout,
            force_login=force_login,
        )
        # Only force login on the first item.
        force_login = False
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
        ensure_login = not bool(data.get("skip_login_check"))
        result = monitor_item_until_bought(
            driver,
            item,
            config,
            max_attempts=max_attempts,
            clear_cart_first=clear_cart_first,
            ensure_login=ensure_login,
            login_timeout=float(data.get("login_timeout") or 90),
            force_login=bool(data.get("force_login")),
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
    ensure_login = not bool(data.get("skip_login_check"))
    result = monitor_item_until_bought(
        driver,
        item,
        config,
        max_attempts=max_attempts,
        clear_cart_first=clear_cart_first,
        ensure_login=ensure_login,
        login_timeout=float(data.get("login_timeout") or 90),
        force_login=bool(data.get("force_login")),
    )
    return _result_to_dict(result)


def run_items_parallel(
    config: AppConfig,
    *,
    max_attempts: int | None = None,
    clear_cart_first: bool = True,
    skip_login_check: bool = False,
    force_login: bool = False,
    login_timeout: float = 90.0,
) -> list[dict]:
    """One browser per item so stock on item A never blocks buying item B."""

    items = config.enabled_items
    print(
        f"[PARALLEL] launching {len(items)} browsers — each item polls until buyable"
    )
    for item in items:
        print(f"[PARALLEL] · {item.label}")

    runners = []
    for index, item in enumerate(items):
        profile = parallel_profile_dir(item.label, index)
        runners.append((item, _make_single_item_runner(profile)))

    def _worker(item: ItemConfig, runner) -> dict:
        print(f"[MONITOR] start {item.label} (own browser)")
        result = runner(
            {
                "config": config,
                "item": item,
                "max_attempts": max_attempts,
                "clear_cart_first": clear_cart_first,
                "skip_login_check": skip_login_check,
                "force_login": force_login,
                "login_timeout": login_timeout,
            }
        )
        print(f"[MONITOR] done {item.label}: {result.get('status')} — {result.get('detail')}")
        return result

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(runners) or 1) as pool:
        futures = {
            pool.submit(_worker, item, runner): item for item, runner in runners
        }
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append(
                    {
                        "label": item.label,
                        "url": item.normalized_url,
                        "status": "error",
                        "detail": str(exc),
                        "fulfillment": None,
                        "placed_order": False,
                    }
                )
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Target stock monitor + checkout bot. "
            "With 2+ items, each gets its own browser and polls until buyable."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to Target config JSON (default: configs/target/default.json)",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Force one browser per item (default already when 2+ items enabled)",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Force one browser for all items (item 2 waits until item 1 finishes)",
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
        "--dry-run",
        action="store_true",
        help="Full flow through checkout review, but never click Place order",
    )
    parser.add_argument(
        "--no-clear-cart",
        action="store_true",
        help="Skip clearing the cart before each item (not recommended)",
    )
    parser.add_argument(
        "--skip-login-check",
        action="store_true",
        help="Do not auto email-OTP login when the Target session is stale",
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="Force a fresh Target email-OTP login before monitoring",
    )
    args = parser.parse_args(argv)

    prepare_runtime()
    config = load_config(args.config)
    if args.place_order and args.dry_run:
        raise SystemExit("Use either --place-order or --dry-run, not both")
    if args.parallel and args.sequential:
        raise SystemExit("Use either --parallel or --sequential, not both")
    if args.place_order:
        config = config.with_place_order(True)
    elif args.dry_run:
        config = config.as_dry_run()

    if args.parallel:
        config = replace(config, parallel=True)
    elif args.sequential:
        config = replace(config, parallel=False)

    clear_cart_first = not args.no_clear_cart
    login_payload = {
        "skip_login_check": bool(args.skip_login_check),
        "force_login": bool(args.force_login),
        "login_timeout": float(config.checkout_auth_timeout_seconds or 90),
    }
    if not args.skip_login_check:
        print(
            "[LOGIN] will verify in the bot browser "
            "(auto email-OTP only if logged out)"
        )

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
        raise SystemExit("No enabled items in Target config")

    use_parallel = config.use_parallel()
    mode = "parallel (1 browser per item)" if use_parallel else "sequential (one browser)"
    print(f"mode={mode}")
    if use_parallel:
        print(
            f"[PARALLEL] {len(config.enabled_items)} items → one browser each "
            "(OOS items keep refreshing until buyable)"
        )
    if use_parallel and args.max_attempts is not None:
        print(
            f"WARNING: --max-attempts={args.max_attempts} stops EACH item after that many "
            "polls — OOS products will NOT keep refreshing. Omit --max-attempts for forever."
        )

    if use_parallel:
        results = run_items_parallel(
            config,
            max_attempts=args.max_attempts,
            clear_cart_first=clear_cart_first,
            **login_payload,
        )
    else:
        results = run_items_sequentially(
            {
                "config": config,
                "max_attempts": args.max_attempts,
                "clear_cart_first": clear_cart_first,
                **login_payload,
            }
        )

    print("\n=== SUMMARY ===")
    for row in results:
        placed = "ORDER PLACED" if row.get("placed_order") else row.get("status")
        print(f"- {row['label']}: {placed} — {row['detail']}")


if __name__ == "__main__":
    main()
