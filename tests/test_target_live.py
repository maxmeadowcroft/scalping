"""Live dry-run Target flow tests (requires captured browser session).

Run:
  uv run pytest tests/test_target_live.py -m live -s

These open a real browser using ~/.scalping/chrome-profiles/target.
dry_run is forced ON — never places an order.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from botasaurus.browser import Driver, browser

from scraping.config import AppConfig, ItemConfig, load_config
from scraping.runtime import CHROME_ADD_ARGUMENTS, PROFILE_DIR, prepare_runtime
from scraping.target_checkout import FulfillmentChoice, choose_fulfillment_and_checkout
from scraping.target_stock import (
    StockStatus,
    add_to_cart,
    check_stock,
    dismiss_post_atc_modals,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "scraping" / "configuration.json"

IN_STOCK = ItemConfig(
    url=(
        "https://www.target.com/p/drizzilicious-lemon-drizzled-mini-rice-cake-4oz/"
        "-/A-95049011#lnk=sametab"
    ),
    max_quantity=1,
    label="drizzilicious-lemon-rice-cake",
)
OOS = ItemConfig(
    url="https://www.target.com/p/-/A-95120838",
    max_quantity=1,
    label="one-piece-starter-deck-31",
)


pytestmark = pytest.mark.live


@pytest.fixture(scope="module", autouse=True)
def _runtime():
    prepare_runtime()
    if not PROFILE_DIR.exists():
        pytest.skip(
            f"No Target profile at {PROFILE_DIR}. "
            "Run: ./sessions/run_target_session.sh"
        )


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def _live_stock_check(driver: Driver, data: dict):
    item: ItemConfig = data["item"]
    return check_stock(driver, item)


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def _live_in_stock_checkout_dry_run(driver: Driver, data: dict):
    config: AppConfig = data["config"]
    item: ItemConfig = data["item"]

    stock = check_stock(driver, item)
    assert stock.status == StockStatus.IN_STOCK, stock

    # Force dry-run regardless of config file
    safe = config.as_dry_run()
    assert add_to_cart(driver, item, prefer_pickup=safe.prefer_pickup), "Add to cart failed"
    dismiss_post_atc_modals(driver)

    result = choose_fulfillment_and_checkout(driver, safe)
    return {
        "stock": stock.status.value,
        "fulfillment": result.fulfillment.value,
        "placed_order": result.placed_order,
        "message": result.message,
    }


def test_live_in_stock_product_detected():
    result = _live_stock_check({"item": IN_STOCK})
    assert result.status == StockStatus.IN_STOCK, result


def test_live_out_of_stock_product_detected():
    result = _live_stock_check({"item": OOS})
    assert result.status == StockStatus.OUT_OF_STOCK, result


def test_live_in_stock_add_to_cart_and_fulfillment_dry_run():
    config = load_config(CONFIG_PATH)
    outcome = _live_in_stock_checkout_dry_run({"config": config, "item": IN_STOCK})
    assert outcome["placed_order"] is False
    assert outcome["fulfillment"] in {
        FulfillmentChoice.PICKUP.value,
        FulfillmentChoice.SHIPPING.value,
        FulfillmentChoice.UNKNOWN.value,
    }
    assert (
        "Dry run" in outcome["message"]
        or outcome["fulfillment"] != "unknown"
        or "sign-in" in outcome["message"].lower()
    )
