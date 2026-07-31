"""Target product page stock detection and add-to-cart helpers.

Modern Target PDPs use fulfillment cells (Pickup / Delivery / Shipping). Selecting
a cell reveals an Add to cart button (e.g. data-test=shippingButton). Out-of-stock
buy boxes show "Out of stock" inside @web/AddToCart/FulfillmentSection without
fulfillment cells.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from botasaurus.browser import Driver

from scraping.config import ItemConfig


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


OOS_PATTERNS = (
    r"\bout of stock\b",
    r"\bsold out\b",
    r"\bcurrently unavailable\b",
)

FULFILLMENT_CELL_SELECTORS = [
    '[data-test="fulfillment-cell-pickup"]',
    '[data-test="fulfillment-cell-shipping"]',
    '[data-test="fulfillment-cell-delivery"]',
]

SHIP_ATC_SELECTORS = [
    '[data-test="shippingButton"]',
    '[data-test="shipItButton"]',
]

PICKUP_ATC_SELECTORS = [
    '[data-test="orderPickupButton"]',
    '[data-test="fulfillment-add-to-cart"]',
]

GENERIC_ATC_SELECTORS = [
    'button[data-test="shippingButton"]',
    'button[data-test*="addToCart" i]',
    'button[data-test*="add-to-cart" i]',
]

OOS_SELECTORS = [
    '[data-test="outOfStockMessage"]',
    '[data-test="shipItUnavailable"]',
]


@dataclass(frozen=True)
class StockCheckResult:
    status: StockStatus
    reason: str
    page_text_excerpt: str = ""


def page_indicates_out_of_stock(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in OOS_PATTERNS)


def classify_stock_from_text(text: str) -> StockCheckResult:
    """Heuristic classifier used when selectors are missing.

    Looks at the first ~8k chars (buy box) so related-product ATC text further
    down the page does not mask a genuine Out of stock state.
    """
    excerpt = " ".join(text.split())[:500]
    head = text[:8000].lower()

    if "out of stock" in head or "sold out" in head:
        return StockCheckResult(StockStatus.OUT_OF_STOCK, "oos_text", excerpt)
    if any(
        needle in head
        for needle in (
            "add to cart",
            "ship it",
            "only ",
            "ready within",
            "arrives by",
            " in cart",
        )
    ):
        return StockCheckResult(StockStatus.IN_STOCK, "atc_text", excerpt)
    if page_indicates_out_of_stock(text):
        return StockCheckResult(StockStatus.OUT_OF_STOCK, "oos_text", excerpt)
    return StockCheckResult(StockStatus.UNKNOWN, "no_signal", excerpt)


def _first_present(driver: Driver, selectors: list[str], wait: float = 1.0):
    for selector in selectors:
        try:
            el = driver.select(selector, wait)
        except Exception:
            el = None
        if el is not None:
            return el, selector
    return None, None


def _page_text(driver: Driver) -> str:
    try:
        text = driver.page_text or ""
    except Exception:
        text = ""
    if text and len(text) > 100:
        return text
    try:
        return driver.run_js("return document.body ? document.body.innerText : ''") or ""
    except Exception:
        return text or ""


def _fulfillment_section_text(driver: Driver) -> str:
    try:
        return (
            driver.run_js(
                """
                const n = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]');
                return n ? (n.innerText || '') : '';
                """
            )
            or ""
        )
    except Exception:
        return ""


def _wait_for_pdp(driver: Driver, wait: float = 8.0) -> None:
    try:
        driver.wait_for_element(
            '[data-test="@web/AddToCart/FulfillmentSection"], '
            '[data-test="fulfillment-cell-pickup"], '
            '[data-test="fulfillment-cell-shipping"], '
            '[data-test="product-title"], '
            '[data-test="shippingButton"], '
            '[data-test="outOfStockMessage"]',
            wait=int(wait),
        )
    except Exception:
        driver.sleep(min(2.0, wait))


def _wait_for_buybox_signal(driver: Driver, *, timeout: float = 8.0) -> str:
    """Poll until the fulfillment section shows a decisive stock signal."""
    elapsed = 0.0
    last = ""
    while elapsed < timeout:
        last = _fulfillment_section_text(driver)
        lowered = last.lower()
        if any(
            token in lowered
            for token in (
                "out of stock",
                "sold out",
                "ready within",
                "arrives by",
                "as soon as",
                "only ",
                "in cart",
                "currently unavailable",
                "not available",
                "unavailable",
            )
        ):
            return last
        driver.sleep(0.5)
        elapsed += 0.5
    return last


def open_product(driver: Driver, item: ItemConfig, *, force_navigate: bool = True) -> None:
    """Navigate to the PDP (or reload if already there)."""
    target = item.normalized_url
    already_there = False
    try:
        current = driver.current_url or ""
        already_there = item.tcin is not None and item.tcin in current and "/p/" in current
    except Exception:
        already_there = False

    if force_navigate or not already_there:
        driver.get(target)
    else:
        try:
            driver.reload()
        except Exception:
            driver.get(target)

    _wait_for_pdp(driver)
    # Target sometimes restores cart from the profile; ensure we stayed on PDP.
    try:
        current = driver.current_url or ""
        if item.tcin and item.tcin not in current:
            driver.get(target)
            _wait_for_pdp(driver)
    except Exception:
        pass


def check_stock(
    driver: Driver,
    item: ItemConfig,
    *,
    navigate: bool = True,
) -> StockCheckResult:
    if navigate:
        open_product(driver, item, force_navigate=True)

    section_raw = _wait_for_buybox_signal(driver)
    section = section_raw.lower()
    if section:
        if (
            "out of stock" in section
            or "sold out" in section
            or "currently unavailable" in section
        ):
            return StockCheckResult(
                StockStatus.OUT_OF_STOCK,
                "fulfillment_section:oos",
                section_raw[:300],
            )
        # Require a real availability cue — bare "Add to cart" can be a skeleton
        # or a disabled OOS control next to related-product carousels.
        if any(
            s in section
            for s in (
                "in cart",
                "ready within",
                "arrives by",
                "as soon as",
                "only ",
                "pick up at",
                "ships to",
            )
        ):
            return StockCheckResult(
                StockStatus.IN_STOCK,
                "fulfillment_section:buyable",
                section_raw[:300],
            )

    oos_el, oos_sel = _first_present(driver, OOS_SELECTORS, wait=0.8)
    if oos_el is not None:
        return StockCheckResult(StockStatus.OUT_OF_STOCK, f"selector:{oos_sel}")

    cell_el, cell_sel = _first_present(driver, FULFILLMENT_CELL_SELECTORS, wait=0.8)
    if cell_el is not None:
        return StockCheckResult(StockStatus.IN_STOCK, f"selector:{cell_sel}")

    atc_selectors = SHIP_ATC_SELECTORS + PICKUP_ATC_SELECTORS + GENERIC_ATC_SELECTORS
    atc_el, atc_sel = _first_present(driver, atc_selectors, wait=0.8)
    if atc_el is not None:
        return StockCheckResult(StockStatus.IN_STOCK, f"selector:{atc_sel}")

    text = _page_text(driver)
    try:
        oos_node = driver.get_element_containing_text("Out of stock", wait=0.5)
    except Exception:
        oos_node = None
    if oos_node is not None and not cell_el:
        # Related carousels can say Add to cart; buy-box OOS still wins.
        return StockCheckResult(StockStatus.OUT_OF_STOCK, "text_node:Out of stock")

    return classify_stock_from_text(text)


def set_quantity(driver: Driver, quantity: int) -> None:
    if quantity <= 1:
        return
    selectors = [
        '[data-test="quantity-select"]',
        '[data-test="custom-quantity-picker"]',
        '[data-test="quantity-picker"]',
        'select[data-test*="quantity" i]',
        'input[name="quantity"]',
    ]
    for selector in selectors:
        el = driver.select(selector, 1)
        if el is None:
            continue
        try:
            driver.select_option(selector, str(quantity))
            return
        except Exception:
            pass
        try:
            # Custom picker: open then click the desired qty option
            driver.click(selector)
            driver.sleep(0.4)
            if driver.get_element_with_exact_text(str(quantity), wait=1):
                driver.click_element_containing_text(str(quantity))
                return
        except Exception:
            pass
        try:
            driver.clear(selector)
            driver.type(selector, str(quantity))
            return
        except Exception:
            continue


def _select_fulfillment_cell(driver: Driver, prefer_pickup: bool) -> str | None:
    """Click Pickup / Shipping / Delivery cell. Returns which one was chosen."""
    order = (
        [
            ("pickup", '[data-test="fulfillment-cell-pickup"]'),
            ("shipping", '[data-test="fulfillment-cell-shipping"]'),
            ("delivery", '[data-test="fulfillment-cell-delivery"]'),
        ]
        if prefer_pickup
        else [
            ("shipping", '[data-test="fulfillment-cell-shipping"]'),
            ("delivery", '[data-test="fulfillment-cell-delivery"]'),
            ("pickup", '[data-test="fulfillment-cell-pickup"]'),
        ]
    )
    for name, selector in order:
        if driver.select(selector, 1) is None:
            continue
        try:
            driver.click(selector)
            driver.sleep(1.2)
            return name
        except Exception:
            continue
    return None


def _already_in_cart(driver: Driver) -> bool:
    try:
        text = _fulfillment_section_text(driver).lower()
        if re.search(r"\d+\s+in cart", text):
            return True
        el = driver.get_element_containing_text("in cart", wait=0.5)
        return el is not None
    except Exception:
        return False


def _click_visible_atc(driver: Driver) -> bool:
    ordered = SHIP_ATC_SELECTORS + PICKUP_ATC_SELECTORS + GENERIC_ATC_SELECTORS
    _, selector = _first_present(driver, ordered, wait=1.5)
    if selector:
        try:
            driver.click(selector)
            driver.sleep(1.5)
            return True
        except Exception:
            pass

    for label in ("Add to cart", "Ship it", "Order Pickup", "Deliver it"):
        try:
            # Avoid related-product chooseOptionsButton by preferring data-test buttons first
            btn = driver.get_element_containing_text(label, wait=1.0)
            if btn is None:
                continue
            test = ""
            try:
                test = (btn.get_attribute("data-test") or "").lower()
            except Exception:
                test = ""
            if "chooseoptions" in test:
                continue
            btn.click()
            driver.sleep(1.5)
            return True
        except Exception:
            continue
    return False


def add_to_cart(
    driver: Driver,
    item: ItemConfig,
    *,
    prefer_pickup: bool = True,
) -> bool:
    """Select fulfillment, set qty, click Add to cart on an in-stock PDP.

    When prefer_pickup is set, ATC via the pickup cell so cheap carts do not
    create a shipping line that hits Target's $35 minimum (and so a later
    pickup ATC retry does not leave two lines of the same item).
    """
    set_quantity(driver, item.max_quantity)

    order = (
        [
            ("pickup", '[data-test="fulfillment-cell-pickup"]'),
            ("shipping", '[data-test="fulfillment-cell-shipping"]'),
            ("delivery", '[data-test="fulfillment-cell-delivery"]'),
        ]
        if prefer_pickup
        else [
            ("shipping", '[data-test="fulfillment-cell-shipping"]'),
            ("delivery", '[data-test="fulfillment-cell-delivery"]'),
            ("pickup", '[data-test="fulfillment-cell-pickup"]'),
        ]
    )
    for name, selector in order:
        if driver.select(selector, 0.8) is None:
            continue
        try:
            driver.click(selector)
            driver.sleep(1.2)
        except Exception:
            continue
        set_quantity(driver, item.max_quantity)
        if _click_visible_atc(driver):
            print(f"[ATC] added via {name}")
            driver.sleep(1.5)
            return True

    if _click_visible_atc(driver):
        driver.sleep(1.5)
        return True
    return False


def dismiss_post_atc_modals(driver: Driver) -> None:
    """Close warranty/coverage overlays that block checkout."""
    for label in (
        "No thanks",
        "No coverage",
        "Decline",
        "Not now",
        "Continue without",
    ):
        try:
            el = driver.get_element_containing_text(label, wait=0.4)
            if el is not None:
                el.click()
                driver.sleep(0.4)
        except Exception:
            continue


def cart_looks_updated(driver: Driver) -> bool:
    """Best-effort signal that ATC succeeded (mini-cart / view-cart CTA / in cart)."""
    if _already_in_cart(driver):
        return True
    try:
        if driver.get_element_containing_text("View cart & check out", wait=1):
            return True
        if driver.get_element_containing_text("Added to cart", wait=0.5):
            return True
        if driver.select('[data-test="cartItem-checkoutButton"]', 0.5):
            return True
        qty = driver.select('[data-test="@web/CartLinkQuantity"]', 0.5)
        if qty is not None:
            return True
    except Exception:
        return False
    return False
