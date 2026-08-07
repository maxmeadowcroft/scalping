"""Target product page stock detection and add-to-cart helpers.

Modern Target PDPs use fulfillment cells (Pickup / Delivery / Shipping). Selecting
a cell reveals an Add to cart button (e.g. data-test=shippingButton). Out-of-stock
buy boxes show "Out of stock" inside @web/AddToCart/FulfillmentSection without
fulfillment cells.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from enum import Enum

from botasaurus.browser import Driver

from scalping.bots.target.config import ItemConfig


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


def _first_present(driver: Driver, selectors: list[str], wait: float = 0.0):
    """Find first matching selector. Default wait=0 (instant) for hot paths."""
    if wait and wait > 0:
        for selector in selectors:
            try:
                el = driver.select(selector, wait)
            except Exception:
                el = None
            if el is not None:
                return el, selector
        return None, None
    # Instant multi-selector probe via JS — no Botasaurus waits.
    try:
        hit = driver.run_js(
            f"""
            const sels = {selectors!r};
            for (const s of sels) {{
              try {{
                const el = document.querySelector(s);
                if (el) return s;
              }} catch (e) {{}}
            }}
            return null;
            """
        )
    except Exception:
        hit = None
    if hit:
        return True, hit
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


def _wait_for_pdp(driver: Driver, wait: float = 0.9) -> None:
    try:
        driver.wait_for_element(
            '[data-test="@web/AddToCart/FulfillmentSection"], '
            '[data-test="fulfillment-cell-pickup"], '
            '[data-test="fulfillment-cell-shipping"], '
            '[data-test="product-title"], '
            '[data-test="shippingButton"], '
            '[data-test="outOfStockMessage"]',
            wait=max(1, int(wait)),
        )
    except Exception:
        time.sleep(min(0.2, wait))


def _wait_for_buybox_signal(driver: Driver, *, timeout: float = 0.7) -> str:
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
        # time.sleep — driver.sleep prints "Sleeping for…" and adds overhead
        time.sleep(0.05)
        elapsed += 0.05
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
        _wait_for_pdp(driver, wait=0.9)
    else:
        try:
            driver.reload()
        except Exception:
            driver.get(target)
        # Reloads should resolve faster — don't sit on a long wait_for.
        _wait_for_pdp(driver, wait=0.6)

    # Target sometimes restores cart from the profile; ensure we stayed on PDP.
    try:
        current = driver.current_url or ""
        if item.tcin and item.tcin not in current:
            driver.get(target)
            _wait_for_pdp(driver, wait=0.9)
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

    oos_el, oos_sel = _first_present(driver, OOS_SELECTORS, wait=0)
    if oos_el is not None:
        return StockCheckResult(StockStatus.OUT_OF_STOCK, f"selector:{oos_sel}")

    cell_el, cell_sel = _first_present(driver, FULFILLMENT_CELL_SELECTORS, wait=0)
    if cell_el is not None:
        return StockCheckResult(StockStatus.IN_STOCK, f"selector:{cell_sel}")

    atc_selectors = SHIP_ATC_SELECTORS + PICKUP_ATC_SELECTORS + GENERIC_ATC_SELECTORS
    atc_el, atc_sel = _first_present(driver, atc_selectors, wait=0)
    if atc_el is not None:
        return StockCheckResult(StockStatus.IN_STOCK, f"selector:{atc_sel}")

    text = _page_text(driver)
    try:
        oos_node = driver.get_element_containing_text("Out of stock", wait=0.05)
    except Exception:
        oos_node = None
    if oos_node is not None and not cell_el:
        # Related carousels can say Add to cart; buy-box OOS still wins.
        return StockCheckResult(StockStatus.OUT_OF_STOCK, "text_node:Out of stock")

    return classify_stock_from_text(text)


def set_quantity(driver: Driver, quantity: int) -> None:
    """Set PDP qty. If `quantity` isn't offered, pick the highest available option."""
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
        el = driver.select(selector, 0.35)
        if el is None:
            continue
        try:
            driver.select_option(selector, str(quantity))
            return
        except Exception:
            pass
        # <select>: choose highest option <= requested
        try:
            chosen = driver.run_js(
                f"""
                const el = document.querySelector({selector!r});
                if (!el || el.tagName !== 'SELECT') return null;
                const want = {int(quantity)};
                let best = null;
                for (const opt of el.options) {{
                  const n = parseInt(opt.value || opt.textContent, 10);
                  if (!Number.isFinite(n) || n < 1) continue;
                  if (n === want) {{ el.value = opt.value; el.dispatchEvent(new Event('change', {{bubbles:true}})); return n; }}
                  if (n <= want && (best === null || n > best)) best = n;
                }}
                if (best !== null) {{
                  for (const opt of el.options) {{
                    const n = parseInt(opt.value || opt.textContent, 10);
                    if (n === best) {{
                      el.value = opt.value;
                      el.dispatchEvent(new Event('change', {{bubbles:true}}));
                      return best;
                    }}
                  }}
                }}
                return null;
                """
            )
            if chosen:
                print(f"[QTY] select set to {chosen} (wanted {quantity})")
                return
        except Exception:
            pass
        try:
            # Custom picker: open then click the desired qty option (or closest)
            driver.click(selector)
            time.sleep(0.12)
            for q in range(quantity, 0, -1):
                if driver.get_element_with_exact_text(str(q), wait=0.15):
                    driver.click_element_containing_text(str(q))
                    if q != quantity:
                        print(f"[QTY] picker set to {q} (wanted {quantity})")
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
        if driver.select(selector, 0.2) is None:
            continue
        try:
            driver.click(selector)
            time.sleep(0.08)
            return name
        except Exception:
            continue
    return None


def _already_in_cart(driver: Driver) -> bool:
    """True only when the PDP fulfillment area shows 'N in cart' with N >= 1."""
    try:
        text = _fulfillment_section_text(driver).lower()
        m = re.search(r"(\d+)\s+in cart", text)
        return bool(m and int(m.group(1)) >= 1)
    except Exception:
        return False


def _click_visible_atc(driver: Driver) -> bool:
    ordered = SHIP_ATC_SELECTORS + PICKUP_ATC_SELECTORS + GENERIC_ATC_SELECTORS
    _, selector = _first_present(driver, ordered, wait=0)
    if selector:
        try:
            # Human-ish jittered JS click when possible.
            try:
                driver.run_js(
                    f"""
                    const el = document.querySelector({selector!r});
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const x = r.left + r.width * (0.4 + Math.random() * 0.2);
                    const y = r.top + r.height * (0.4 + Math.random() * 0.2);
                    const o = {{bubbles:true,cancelable:true,view:window,clientX:x,clientY:y,button:0}};
                    for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {{
                      el.dispatchEvent(t.startsWith('pointer')
                        ? new PointerEvent(t, Object.assign({{}}, o, {{pointerId:1,pointerType:'mouse',isPrimary:true}}))
                        : new MouseEvent(t, o));
                    }}
                    try {{ el.click(); }} catch (e) {{}}
                    return true;
                    """
                )
            except Exception:
                driver.click(selector)
            return True
        except Exception:
            pass

    for label in ("Add to cart", "Ship it", "Order Pickup", "Deliver it"):
        try:
            btn = driver.get_element_containing_text(label, wait=0.18)
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
            return True
        except Exception:
            continue
    return False


def add_to_cart(
    driver: Driver,
    item: ItemConfig,
    *,
    prefer_pickup: bool = False,
) -> bool:
    """Select fulfillment, set qty, spam Add to cart until it sticks."""
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

    chosen = None
    for name, selector in order:
        if driver.select(selector, 0.25) is None:
            continue
        try:
            driver.click(selector)
            driver.sleep(random.uniform(0.05, 0.12))
            chosen = name
            break
        except Exception:
            continue

    set_quantity(driver, item.max_quantity)

    # Spam ATC — competing bots win on first successful land.
    for attempt in range(1, 10):
        if _already_in_cart(driver) or cart_looks_updated(driver):
            print(f"[ATC] confirmed via {chosen or 'default'} (try {attempt})")
            return True
        if _click_visible_atc(driver):
            print(f"[ATC] click via {chosen or 'default'} ({attempt})")
        driver.sleep(random.uniform(0.05, 0.12))
        if _already_in_cart(driver) or cart_looks_updated(driver):
            print(f"[ATC] added via {chosen or 'default'}")
            return True
    return _already_in_cart(driver) or cart_looks_updated(driver)


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
            el = driver.get_element_containing_text(label, wait=0.12)
            if el is not None:
                el.click()
                driver.sleep(0.08)
        except Exception:
            continue


def cart_looks_updated(driver: Driver) -> bool:
    """Best-effort signal that ATC succeeded (mini-cart / view-cart CTA / in cart)."""
    if _already_in_cart(driver):
        return True
    try:
        if driver.get_element_containing_text("View cart & check out", wait=0.15):
            return True
        if driver.get_element_containing_text("Added to cart", wait=0.1):
            return True
        if driver.select('[data-test="cartItem-checkoutButton"]', 0.12):
            return True
        qty = driver.run_js(
            """
            const el = document.querySelector('[data-test="@web/CartLinkQuantity"]');
            if (!el) return 0;
            const n = parseInt((el.textContent || '').replace(/[^0-9]/g, ''), 10);
            return Number.isFinite(n) ? n : 0;
            """
        )
        if int(qty or 0) > 0:
            return True
    except Exception:
        pass
    return False
