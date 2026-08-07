"""Continuous Target drop data collector.

Priority: capture buy-box / Redsky / cart_items responses while inventory flips.
If ATC lands, optionally continue into checkout (place_order from config).

  uv run python -m scalping.bots.target.hunt_data --config configs/target/tonight.json
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from botasaurus.browser import Driver, browser

from scalping.bots.target.api import (
    VARIANT_ORDER,
    cart_api_add,
    poll_fulfillment_api,
    warm_cart_session,
)
from scalping.bots.target.checkout import (
    cart_is_empty,
    choose_fulfillment_and_checkout,
    clear_cart,
    go_to_cart,
    open_cart_after_atc,
)
from scalping.bots.target.config import load_config
from scalping.bots.target.runtime import (
    CHROME_ADD_ARGUMENTS,
    PROFILE_DIR,
    prepare_runtime,
)
from scalping.bots.target.stock import (
    StockStatus,
    _buybox_stock_probe,
    _install_cart_fetch_hook,
    _last_cart_call,
    add_to_cart,
    cart_looks_updated,
    check_stock,
    dismiss_post_atc_modals,
    open_product,
)

OUT_DIR = Path.home() / ".scalping" / "logs" / "target"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe(obj, limit: int = 6000):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)[:limit]


def _append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


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
def run_hunt(driver: Driver, data: dict):
    config = data["config"]
    item = config.enabled_items[0]
    poll_s = float(data.get("poll") or config.refresh_interval_seconds or 0.5)
    max_minutes = float(data.get("max_minutes") or 90)
    try_checkout = bool(data.get("try_checkout", True))
    clear_first = bool(data.get("clear_cart_first", True))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ndjson = OUT_DIR / f"hunt_{item.tcin}_{_ts()}.ndjson"
    summary_path = OUT_DIR / f"hunt_{item.tcin}_{_ts()}_summary.json"

    print(f"[HUNT] tcin={item.tcin} label={item.label}")
    print(f"[HUNT] ndjson={ndjson}")
    print(f"[HUNT] poll={poll_s}s max_minutes={max_minutes} try_checkout={try_checkout}")

    print("[HUNT] using existing profile session (no auto-login spam)")
    _append_event(ndjson, {"t": _ts(), "event": "login_skipped", "ok": None})
    # Navigate home gently first so cookies attach without identity hammering.
    try:
        driver.get("https://www.target.com/")
        time.sleep(1.0)
    except Exception:
        pass

    if clear_first:
        try:
            clear_cart(driver)
            _append_event(ndjson, {"t": _ts(), "event": "cart_cleared"})
        except Exception as exc:
            _append_event(ndjson, {"t": _ts(), "event": "cart_clear_err", "err": str(exc)})

    open_product(driver, item, force_navigate=True)
    time.sleep(0.5)
    _install_cart_fetch_hook(driver)

    started = time.time()
    poll_n = 0
    in_stock_hits = 0
    atc_ok_count = 0
    last_statuses: list[str] = []
    purchased = False

    while (time.time() - started) < max_minutes * 60:
        poll_n += 1
        # Soft reload every 12 polls to refresh sensors without hammering.
        if poll_n == 1 or poll_n % 12 == 0:
            open_product(driver, item, force_navigate=(poll_n == 1))
            time.sleep(0.35)
            _install_cart_fetch_hook(driver)

        probe = _buybox_stock_probe(driver)
        dom = check_stock(driver, item, navigate=False)
        api = poll_fulfillment_api(
            driver,
            tcin=item.tcin or "",
            zip_code=config.shipping_address.zip or "87111",
            state=config.shipping_address.state or "NM",
            prefer_pickup=config.prefer_pickup,
        )
        warm = warm_cart_session(driver)

        event = {
            "t": _ts(),
            "event": "poll",
            "n": poll_n,
            "dom": {"status": dom.status.value, "reason": dom.reason},
            "api": {
                "status": api.status.value,
                "reason": api.reason,
                "excerpt": api.page_text_excerpt,
            },
            "buybox": {
                "oos": probe.get("oosText"),
                "atc": probe.get("enabledAtc"),
                "text": str(probe.get("text") or "")[:220],
            },
            "cart_warm": _safe(warm),
        }
        _append_event(ndjson, event)
        status_line = f"dom={dom.status.value} api={api.status.value}"
        last_statuses.append(status_line)
        if len(last_statuses) > 8:
            last_statuses.pop(0)
        print(
            f"[HUNT {poll_n}] {status_line} atc={probe.get('enabledAtc')} "
            f"api=({api.page_text_excerpt}) warm={warm.get('status')}"
        )

        buyable = (
            api.status == StockStatus.IN_STOCK
            or dom.status == StockStatus.IN_STOCK
            or bool(probe.get("enabledAtc"))
        )
        if not buyable:
            time.sleep(poll_s)
            continue

        in_stock_hits += 1
        print(f"[HUNT] BUYABLE hit #{in_stock_hits} — collecting ATC data")
        _append_event(
            ndjson,
            {
                "t": _ts(),
                "event": "buyable",
                "n": poll_n,
                "hit": in_stock_hits,
                "buybox_text": str(probe.get("text") or "")[:400],
            },
        )

        # 1) Snapshot each API variant once (data), with spacing.
        variant_results = []
        for variant in VARIANT_ORDER:
            res = cart_api_add(
                driver, tcin=item.tcin or "", quantity=1, variant=variant
            )
            entry = {
                "variant": res.variant,
                "ok": res.ok,
                "status": res.status,
                "error": res.error,
                "retry_after": res.retry_after,
                "data": _safe(res.data, 2500),
            }
            variant_results.append(entry)
            print(
                f"[HUNT] ATC {variant}: status={res.status} ok={res.ok} err={res.error!r}"
            )
            if res.ok:
                atc_ok_count += 1
                break
            wait = 2.5 if res.status == 429 else (3.5 if res.status == 401 else 1.0)
            time.sleep(wait)
            if res.status in (429, 401):
                open_product(driver, item, force_navigate=True)
                time.sleep(0.6)
                _install_cart_fetch_hook(driver)

        _append_event(
            ndjson,
            {"t": _ts(), "event": "atc_variants", "results": variant_results},
        )

        # 2) UI path with fetch hook (even if API failed — capture page fetch).
        if not cart_looks_updated(driver):
            print("[HUNT] UI ATC path…")
            ui_ok = add_to_cart(driver, item, prefer_pickup=config.prefer_pickup)
            last = _last_cart_call(driver)
            _append_event(
                ndjson,
                {
                    "t": _ts(),
                    "event": "atc_ui",
                    "ok": ui_ok,
                    "last_cart_call": _safe(last),
                    "looks_updated": cart_looks_updated(driver),
                    "buybox_after": _safe(_buybox_stock_probe(driver)),
                    "cart_warm_after": _safe(warm_cart_session(driver)),
                },
            )
            print(f"[HUNT] UI ATC ok={ui_ok} last_call={last}")
            if ui_ok:
                atc_ok_count += 1

        landed = cart_looks_updated(driver)
        if landed or atc_ok_count:
            dismiss_post_atc_modals(driver)
            open_cart_after_atc(driver)
            go_to_cart(driver)
            time.sleep(0.4)
            empty = cart_is_empty(driver)
            _append_event(
                ndjson,
                {"t": _ts(), "event": "cart_check", "empty": empty, "landed": landed},
            )
            print(f"[HUNT] cart empty={empty}")
            if not empty and try_checkout and config.place_order:
                print("[HUNT] attempting checkout / place order…")
                checkout = choose_fulfillment_and_checkout(driver, config)
                _append_event(
                    ndjson,
                    {
                        "t": _ts(),
                        "event": "checkout",
                        "message": checkout.message,
                        "placed": checkout.placed_order,
                        "fulfillment": checkout.fulfillment.value,
                        "order_number": checkout.order_number,
                    },
                )
                print(f"[HUNT] CHECKOUT: {checkout.message}")
                if checkout.placed_order:
                    purchased = True
                    break
            elif not empty and try_checkout:
                # Dry capture of cart state only
                _append_event(ndjson, {"t": _ts(), "event": "in_cart_no_place_order"})
                print("[HUNT] item in cart — stopping for data (no place_order)")
                break

        # After a buyable wave, cool down before next stock poll (avoid 429 hole).
        cool = 8.0 if any(r.get("status") == 429 for r in variant_results) else 3.0
        print(f"[HUNT] post-buyable cool {cool}s")
        time.sleep(cool)
        open_product(driver, item, force_navigate=True)
        time.sleep(0.5)
        _install_cart_fetch_hook(driver)

    summary = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "tcin": item.tcin,
        "label": item.label,
        "polls": poll_n,
        "in_stock_hits": in_stock_hits,
        "atc_ok_count": atc_ok_count,
        "purchased": purchased,
        "ndjson": str(ndjson),
        "last_statuses": last_statuses,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[HUNT] DONE summary={summary_path}")
    _append_event(ndjson, {"t": _ts(), "event": "done", **summary})
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Continuous Target drop data hunt")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--poll", type=float, default=None)
    parser.add_argument("--max-minutes", type=float, default=90)
    parser.add_argument(
        "--no-checkout",
        action="store_true",
        help="Collect ATC data only; never place order even if config says so",
    )
    parser.add_argument("--no-clear-cart", action="store_true")
    args = parser.parse_args(argv)

    prepare_runtime()
    config = load_config(args.config)
    if not config.enabled_items:
        raise SystemExit("No enabled items")
    run_hunt(
        {
            "config": config,
            "poll": args.poll,
            "max_minutes": args.max_minutes,
            "try_checkout": (not args.no_checkout) and bool(config.place_order),
            "clear_cart_first": not args.no_clear_cart,
        }
    )


if __name__ == "__main__":
    main()
