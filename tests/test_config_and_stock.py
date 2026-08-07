"""Unit tests for config + stock classification (no browser)."""

from __future__ import annotations

import json
from pathlib import Path

from scalping.bots.target.config import ItemConfig, extract_tcin, load_config
from scalping.bots.target.stock import StockStatus, classify_stock_from_text

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "target" / "default.json"

IN_STOCK_URL = (
    "https://www.target.com/p/drizzilicious-lemon-drizzled-mini-rice-cake-4oz/"
    "-/A-95049011#lnk=sametab"
)
OOS_URL = "https://www.target.com/p/-/A-95120838"


def test_default_config_has_enabled_items():
    config = load_config(CONFIG_PATH)
    assert config.enabled_items
    assert all(item.tcin for item in config.enabled_items)


def test_extract_tcin_from_both_url_shapes():
    assert extract_tcin(IN_STOCK_URL) == "95049011"
    assert extract_tcin(OOS_URL) == "95120838"


def test_normalized_url_strips_hash():
    item = ItemConfig(
        url=IN_STOCK_URL,
        max_quantity=1,
        label="rice",
        enabled=True,
    )
    assert "#" not in item.normalized_url
    assert item.normalized_url.startswith("https://")
    assert item.tcin == "95049011"


def test_shipping_defaults_to_garcia_st():
    config = load_config(CONFIG_PATH)
    assert config.shipping_address.zip == "87111"
    assert "Garcia" in config.shipping_address.street
    assert config.prefer_pickup is False
    assert "Albuquerque Wyoming" in config.preferred_store_name


def test_dry_run_defaults_safe():
    config = load_config(CONFIG_PATH)
    assert config.dry_run is True
    assert config.place_order is False
    assert config.refresh_jitter_seconds >= 0
    assert config.max_atc_retries >= 1


def test_with_place_order_helper():
    config = load_config(CONFIG_PATH)
    live = config.with_place_order(True)
    assert live.place_order is True
    assert live.dry_run is False
    assert config.place_order is False


def test_classify_in_stock_text():
    text = """
    Drizzilicious Lemon Drizzled Mini Rice Cake - 4oz
    $3.79
    Pickup Delivery Shipping
    Add to cart
    Eligible for registries
    """
    result = classify_stock_from_text(text)
    assert result.status == StockStatus.IN_STOCK


def test_classify_out_of_stock_text():
    text = """
    One Piece Card Game Red Monkey.D.Luffy Starter Deck 31
    $12.99
    Out of Stock
    Final sale item
    Find alternative
    Add to cart
    """
    result = classify_stock_from_text(text)
    assert result.status == StockStatus.OUT_OF_STOCK


def test_config_json_is_valid():
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "items" in raw
    assert len(raw["items"]) >= 1
    assert all("url" in item for item in raw["items"])


def test_payment_saved_card_needs_cvv_only():
    from scalping.bots.target.config import PaymentInfo

    saved = PaymentInfo(
        card_number="",
        card_holder_name="",
        card_expiration_date="",
        card_cvv="123",
        card_type="",
        use_saved_card=True,
    )
    assert saved.is_complete is True
    empty = PaymentInfo("", "", "", "", "", use_saved_card=True)
    assert empty.is_complete is False


def test_extract_otp_candidates_prefers_six_digits():
    from scalping.bots.target.gmail_otp import extract_otp_candidates

    text = "Your Target verification code is 482913. Do not share this code."
    assert extract_otp_candidates(text)[0] == "482913"


def test_extract_otp_ignores_years():
    from scalping.bots.target.gmail_otp import extract_otp_candidates

    codes = extract_otp_candidates("Code 391028 sent in 2026")
    assert "391028" in codes
    assert "2026" not in codes
