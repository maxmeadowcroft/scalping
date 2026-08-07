"""Target cart + checkout: auth step-up, payment, place order.

Checkout flow
-------------
1. Open cart, choose Order Pickup (preferred) or Shipping.
2. Click Check out.
3. Target often shows a step-up sign-in modal (passkey / email code). We request
   an email code and wait for you to enter it in the browser, or paste it into
   TARGET_OTP in `.env` while the bot polls.
4. On the checkout page: ensure contact / address, fill CVV (saved card) or add
   a new card from `.env`, then click Place order when place_order is enabled.

Safety: dry_run stops before Place order.
"""

from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass
from enum import Enum

from botasaurus.browser import Driver

from scalping.bots.target.config import (
    AppConfig,
    PaymentInfo,
    ShippingAddress,
    clear_target_otp,
    read_target_otp,
)


class FulfillmentChoice(str, Enum):
    PICKUP = "pickup"
    SHIPPING = "shipping"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CheckoutResult:
    fulfillment: FulfillmentChoice
    dry_run: bool
    placed_order: bool
    message: str
    order_number: str | None = None


def _click_text(driver: Driver, text: str, wait: float = 1.0) -> bool:
    try:
        el = driver.get_element_containing_text(text, wait=wait)
        if el is None:
            return False
        el.click()
        time.sleep(random.uniform(0.03, 0.08))
        return True
    except Exception:
        return False


def _click_selector(driver: Driver, selector: str, wait: float = 1.0) -> bool:
    try:
        el = driver.select(selector, wait)
        if el is None:
            return False
        try:
            driver.click(selector)
        except Exception:
            driver.run_js(
                f"document.querySelector({selector!r})?.click()"
            )
        time.sleep(random.uniform(0.03, 0.08))
        return True
    except Exception:
        return False


def _js_click(driver: Driver, selector: str) -> bool:
    try:
        return bool(
            driver.run_js(
                f"""
                const el = document.querySelector({selector!r});
                if (!el) return false;
                el.click();
                return true;
                """
            )
        )
    except Exception:
        return False


def _human_pause(driver: Driver, lo: float = 0.05, hi: float = 0.14) -> None:
    """Tiny jittered pause — use time.sleep (Botasaurus driver.sleep is noisy/slow)."""
    time.sleep(random.uniform(lo, hi))


def _enable_human_mouse(driver: Driver) -> None:
    """Best-effort Botasaurus human mouse mode (smooth cursor moves)."""
    try:
        if hasattr(driver, "enable_human_mode"):
            driver.enable_human_mode()
    except Exception:
        pass


def _element_click_point(driver: Driver, selector: str) -> tuple[int, int] | None:
    """Visible center of selector with slight pixel jitter (never #passkey)."""
    if "passkey" in selector.lower():
        return None
    try:
        point = driver.run_js(
            f"""
            const el = document.querySelector({selector!r});
            if (!el) return null;
            if ((el.id || '').toLowerCase() === 'passkey') return null;
            el.scrollIntoView({{block: 'center', inline: 'nearest'}});
            const r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return null;
            const ox = (Math.random() * 0.36) - 0.18;
            const oy = (Math.random() * 0.36) - 0.18;
            return {{
              x: Math.round(r.left + r.width * (0.5 + ox)),
              y: Math.round(r.top + r.height * (0.5 + oy)),
            }};
            """
        )
    except Exception:
        return None
    if isinstance(point, dict) and "x" in point and "y" in point:
        return int(point["x"]), int(point["y"])
    return None


def _realistic_click_selector(driver: Driver, selector: str) -> bool:
    """Move cursor then click — prefers fast jump; falls back to synthetic events."""
    if "passkey" in selector.lower():
        return False
    _enable_human_mouse(driver)
    point = _element_click_point(driver, selector)
    if point is not None:
        x, y = point
        try:
            if hasattr(driver, "move_mouse_to_point"):
                # Jump is much faster than smooth glide for drop night.
                driver.move_mouse_to_point(x, y, is_jump=True)
                _human_pause(driver, 0.015, 0.04)
            if hasattr(driver, "click_at_point"):
                driver.click_at_point(x, y, skip_move=True)
                _human_pause(driver, 0.02, 0.05)
                return True
            if hasattr(driver, "mouse_press") and hasattr(driver, "mouse_release"):
                driver.mouse_press(x, y)
                _human_pause(driver, 0.015, 0.035)
                driver.mouse_release(x, y)
                _human_pause(driver, 0.02, 0.05)
                return True
        except Exception:
            pass
        try:
            if hasattr(driver, "click"):
                driver.click(selector, wait=1, skip_move=True)
                _human_pause(driver, 0.02, 0.05)
                return True
        except Exception:
            pass
    return _human_click_selector(driver, selector)


def _human_click_selector(driver: Driver, selector: str) -> bool:
    """Synthetic pointer click with pixel jitter (never for #passkey)."""
    if "passkey" in selector.lower():
        return False
    ox = round(random.uniform(-0.2, 0.2), 3)
    oy = round(random.uniform(-0.2, 0.2), 3)
    try:
        result = driver.run_js(
            f"""
            const el = document.querySelector({selector!r});
            if (!el) return {{ok: false}};
            if ((el.id || '').toLowerCase() === 'passkey') return {{ok: false}};
            el.scrollIntoView({{block: 'center', inline: 'nearest'}});
            try {{ el.focus({{preventScroll: true}}); }} catch (e) {{}}
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) return {{ok: false}};
            const x = r.left + r.width * (0.5 + {ox});
            const y = r.top + r.height * (0.5 + {oy});
            const opts = (buttons) => ({{
              bubbles: true, cancelable: true, view: window,
              clientX: x, clientY: y, button: 0, buttons,
              pointerId: 1, pointerType: 'mouse', isPrimary: true,
            }});
            el.dispatchEvent(new PointerEvent('pointerover', opts(0)));
            el.dispatchEvent(new MouseEvent('mouseover', opts(0)));
            el.dispatchEvent(new PointerEvent('pointerdown', opts(1)));
            el.dispatchEvent(new MouseEvent('mousedown', opts(1)));
            el.dispatchEvent(new PointerEvent('pointerup', opts(0)));
            el.dispatchEvent(new MouseEvent('mouseup', opts(0)));
            el.dispatchEvent(new MouseEvent('click', opts(0)));
            try {{ el.click(); }} catch (e) {{}}
            return {{ok: true, text: ((el.innerText || '') + '').replace(/\\s+/g, ' ').trim().slice(0, 60)}};
            """
        )
    except Exception:
        return False
    if isinstance(result, dict) and result.get("ok"):
        _human_pause(driver, 0.015, 0.04)
        return True
    return _js_click(driver, selector)


def _spam_until(
    driver: Driver,
    *,
    action,
    success,
    label: str,
    max_tries: int = 20,
    peek_lo: float = 0.05,
    peek_hi: float = 0.12,
) -> bool:
    """Fire action repeatedly with human jitter until success() is true."""
    for i in range(1, max_tries + 1):
        if success():
            return True
        ok = False
        try:
            ok = bool(action())
        except Exception:
            ok = False
        if success():
            if i > 1:
                print(f"[SPAM] {label} landed on try {i}")
            return True
        _human_pause(driver, peek_lo, peek_hi)
        if not ok and i % 5 == 0:
            print(f"[SPAM] {label} still waiting ({i}/{max_tries})")
    return bool(success())


def _page_lower(driver: Driver) -> str:
    try:
        text = driver.page_text or ""
    except Exception:
        text = ""
    if len(text) < 80:
        try:
            text = driver.run_js("return document.body ? document.body.innerText : ''") or text
        except Exception:
            pass
    return text.lower()


def _type_first(driver: Driver, selectors: list[str], value: str, wait: float = 1.5) -> bool:
    if not value:
        return False
    for selector in selectors:
        try:
            el = driver.select(selector, wait)
            if el is None:
                continue
            try:
                driver.clear(selector)
            except Exception:
                pass
            driver.type(selector, value)
            return True
        except Exception:
            continue
    return False


def ensure_logged_in_hint(driver: Driver) -> str | None:
    url = ""
    try:
        url = driver.current_url or ""
    except Exception:
        pass
    if "/login" in url or "/account/signin" in url:
        return "Browser appears to be on Target login — re-run sessions/run_target_session.sh"
    return None


def go_to_cart(driver: Driver) -> None:
    try:
        url = (driver.current_url or "").lower()
        if "/cart" in url and "checkout" not in url:
            return
    except Exception:
        pass
    # Prefer the post-ATC drawer CTA — JS click is fastest.
    for sel in (
        '[data-test="cartItem-checkoutButton"]',
        'a[href="/cart"]',
        '[data-test="@web/CartIcon"]',
        '[data-test="@web/CartLink"]',
    ):
        if _js_click(driver, sel):
            time.sleep(0.08)
            return
    try:
        hit = driver.run_js(
            """
            const labels = ['view cart & check out', 'view cart and check out', 'view cart'];
            const nodes = Array.from(document.querySelectorAll(
              'a, button, [role="button"], [data-test*="cart" i]'
            ));
            for (const el of nodes) {
              const t = ((el.innerText || el.getAttribute('aria-label') || '') + '')
                .replace(/\\s+/g, ' ').trim().toLowerCase();
              if (!t || t.length > 40) continue;
              if (!labels.some((l) => t === l || t.startsWith(l))) continue;
              const style = window.getComputedStyle(el);
              if (style.display === 'none' || style.visibility === 'hidden') continue;
              el.click();
              return t;
            }
            return null;
            """
        )
    except Exception:
        hit = None
    if hit:
        time.sleep(0.08)
        return
    if _click_text(driver, "View cart & check out", wait=0.05):
        time.sleep(0.08)
        return
    if _click_text(driver, "View cart and check out", wait=0.05):
        time.sleep(0.08)
        return
    driver.get("https://www.target.com/cart")
    time.sleep(0.15)


def open_cart_after_atc(driver: Driver, *, tries: int = 12) -> bool:
    """Spam View cart / cart icon until we're on a non-empty cart."""

    def _on_cart() -> bool:
        try:
            url = (driver.current_url or "").lower()
            if "/cart" in url and "checkout" not in url:
                return not cart_is_empty(driver)
        except Exception:
            pass
        try:
            return bool(
                driver.run_js(
                    "return !!document.querySelector('[data-test=\"checkout-button\"]')"
                )
            )
        except Exception:
            return False

    return _spam_until(
        driver,
        action=lambda: (go_to_cart(driver) or True),
        success=_on_cart,
        label="View cart",
        max_tries=tries,
        peek_lo=0.04,
        peek_hi=0.1,
    )


def clear_cart(driver: Driver, *, max_rounds: int = 12) -> None:
    """Remove every line item so each monitored product checks out alone.

    Only uses Target's cart delete control — never broad "Remove" text clicks,
    which can hit unrelated UI and race with a fresh add-to-cart.
    """
    driver.get("https://www.target.com/cart")
    time.sleep(0.12)
    for _ in range(max_rounds):
        if cart_is_empty(driver):
            return
        removed = 0
        try:
            removed = int(
                driver.run_js(
                    """
                    const btns = Array.from(document.querySelectorAll(
                      '[data-test="cartItem-deleteBtn"]'
                    ));
                    let n = 0;
                    for (const b of btns) {
                      const style = window.getComputedStyle(b);
                      if (style.display === 'none' || style.visibility === 'hidden') continue;
                      b.click();
                      n += 1;
                    }
                    return n;
                    """
                )
                or 0
            )
        except Exception:
            removed = 0
        if removed == 0:
            if not (
                _js_click(driver, '[data-test="cartItem-deleteBtn"]')
                or _click_selector(driver, '[data-test="cartItem-deleteBtn"]', wait=0.12)
            ):
                break
        time.sleep(0.08)
    time.sleep(0.05)


def cart_is_empty(driver: Driver) -> bool:
    """Instant cart-empty check — no Botasaurus select waits."""
    try:
        return bool(
            driver.run_js(
                """
                const text = ((document.body && document.body.innerText) || '').toLowerCase();
                if (text.includes('your cart is empty') || text.includes('cart is empty')) return true;
                if (document.querySelector('[data-test="cartItem-deleteBtn"], [data-test="cartItem"]')) return false;
                if (document.querySelector('[data-test="checkout-button"]')) return false;
                return true;
                """
            )
        )
    except Exception:
        text = _page_lower(driver)
        return "your cart is empty" in text or "cart is empty" in text


def cart_line_count(driver: Driver) -> int:
    try:
        n = driver.run_js(
            """
            const dels = document.querySelectorAll('[data-test="cartItem-deleteBtn"]');
            if (dels && dels.length) return dels.length;
            const items = document.querySelectorAll('[data-test="cartItem"]');
            return items ? items.length : 0;
            """
        )
        return int(n or 0)
    except Exception:
        return 0


def trim_cart_to_max_lines(driver: Driver, max_lines: int = 1) -> int:
    """Delete excess cart lines (e.g. double-ATC left ship + pickup copies)."""
    go_to_cart(driver)
    driver.sleep(0.2)
    rounds = 0
    while cart_line_count(driver) > max_lines and rounds < 8:
        rounds += 1
        # Prefer deleting a shipping-group line when mixed fulfillment is present.
        deleted = False
        try:
            deleted = bool(
                driver.run_js(
                    """
                    // Prefer deleting a line that still looks like Shipping fulfillment.
                    const deletes = Array.from(document.querySelectorAll(
                      '[data-test="cartItem-deleteBtn"]'
                    ));
                    for (const btn of deletes) {
                      let root = btn;
                      for (let i = 0; i < 8 && root; i++) {
                        const text = (root.innerText || '').toLowerCase();
                        if (text.length > 80 && text.length < 4000) {
                          const shippy = /only ship with|arriving by|\\bshipping\\b/.test(text);
                          const pickupish = /ready within|order pickup\\b/.test(text)
                            && !/not available/.test(text);
                          if (shippy && !pickupish) {
                            btn.click();
                            return true;
                          }
                          break;
                        }
                        root = root.parentElement;
                      }
                    }
                    return false;
                    """
                )
            )
        except Exception:
            deleted = False
        if not deleted:
            if not (
                _js_click(driver, '[data-test="cartItem-deleteBtn"]')
                or _click_selector(driver, '[data-test="cartItem-deleteBtn"]', wait=0.35)
            ):
                break
        driver.sleep(0.28)
    count = cart_line_count(driver)
    if count > max_lines:
        print(f"[CART] warning: still {count} lines after trim (wanted ≤{max_lines})")
    elif rounds:
        print(f"[CART] trimmed to {count} line(s)")
    return count


def cart_has_items(driver: Driver) -> bool:
    # Fast path: header cart badge / mini-cart CTA without a full navigation.
    try:
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
        if driver.get_element_containing_text("View cart & check out", wait=0.15):
            return True
        if driver.get_element_containing_text("Added to cart", wait=0.1):
            return True
    except Exception:
        pass

    if open_cart_after_atc(driver, tries=3):
        return True
    go_to_cart(driver)
    driver.sleep(0.1)
    if not cart_is_empty(driver):
        return True
    driver.sleep(0.15)
    return not cart_is_empty(driver)


def pickup_available(driver: Driver) -> bool:
    text = _page_lower(driver)
    if "order pickup" in text:
        tail = text.split("order pickup", 1)[-1][:160]
        if "unavailable" not in tail and "not available" not in tail:
            return True
    for selector in (
        '[data-test="fulfillment-pickup"]',
        'input[value="STORE_PICKUP"]',
        'label[for*="instore" i]',
    ):
        if driver.select(selector, 0.4) is not None:
            return True
    try:
        return driver.get_element_containing_text("Order Pickup", wait=0.5) is not None
    except Exception:
        return False


def select_preferred_store(driver: Driver, preferred_store_name: str) -> bool:
    page = _page_lower(driver)
    if preferred_store_name.lower() in page:
        return True

    opened = False
    for label in (
        "Change store",
        "Choose a store",
        "Find a store",
        "Select a store",
        "Edit store",
    ):
        if _click_text(driver, label, wait=0.5):
            opened = True
            break
    if not opened:
        return preferred_store_name.lower() in page

    for selector in (
        'input[placeholder*="zip" i]',
        'input[placeholder*="city" i]',
        'input[placeholder*="store" i]',
        'input[data-test*="store" i]',
        'input[type="search"]',
    ):
        if _type_first(driver, [selector], preferred_store_name, wait=0.6):
            driver.sleep(0.5)
            break

    if _click_text(driver, preferred_store_name, wait=1):
        driver.sleep(0.4)
        for label in ("Set as shopping store", "Shop this store", "Select", "Confirm"):
            _click_text(driver, label, wait=0.35)
        return True
    return preferred_store_name.lower() in _page_lower(driver)


def shipping_blocked_by_minimum(driver: Driver) -> bool:
    text = _page_lower(driver)
    return any(
        token in text
        for token in (
            "only ship with $35",
            "ships with $35",
            "$35 orders",
            "this can't ship",
            "this cant ship",
            "want your items faster",
            "add $",
            "switch items to pickup",
            "change all to pickup",
        )
    )


def switch_cart_to_pickup(driver: Driver, preferred_store_name: str) -> bool:
    """Handle Target's shipping-minimum banner by switching everything to pickup.

    Cheap carts often show: "These items only ship with $35 orders" with a
    "Change all to pickup" CTA — checkout is blocked until we take it.
    """
    print("[FULFILLMENT] switching cart to Order Pickup")
    for label in (
        "Change all to pickup",
        "Switch items to pickup",
        "switch items to pickup instead",
        "Change to pickup",
    ):
        if _click_text(driver, label, wait=0.45):
            driver.sleep(0.35)
            print(f"[FULFILLMENT] clicked {label!r}")
            break

    ok = select_pickup(driver, preferred_store_name)
    if ok:
        driver.sleep(0.2)
    # Confirm we are no longer stuck on the $35 shipping gate
    if shipping_blocked_by_minimum(driver) and "pickup" not in _page_lower(driver).split("order pickup", 1)[0][-80:]:
        # One more pass on the banner CTA
        _click_text(driver, "Change all to pickup", wait=0.4)
        driver.sleep(0.3)
        ok = select_pickup(driver, preferred_store_name) or ok
    return ok


def select_pickup(driver: Driver, preferred_store_name: str) -> bool:
    """Select Order Pickup fulfillment if present.

    Avoid re-clicking an already-selected pickup radio — Target refreshes the
    cart on change and can flash 'item removed' / drop lines.
    """
    try:
        already = driver.run_js(
            """
            const checked = document.querySelector('input[value="STORE_PICKUP"]:checked');
            if (checked) return true;
            const label = [...document.querySelectorAll('label, button, div')]
              .find(el => /order pickup/i.test(el.innerText || '') && /selected|checked/i.test(el.getAttribute('aria-label') || ''));
            return !!label;
            """
        )
        if already:
            select_preferred_store(driver, preferred_store_name)
            return True
    except Exception:
        pass

    candidates = [
        'input[value="STORE_PICKUP"]',
        '[data-test="fulfillment-pickup"]',
        'input[value*="PICKUP" i]',
        'label[for*="instore" i]',
    ]
    clicked = False
    for selector in candidates:
        if _click_selector(driver, selector, wait=0.35) or _js_click(driver, selector):
            driver.sleep(0.25)
            clicked = True
            break
    if not clicked and _click_text(driver, "Order Pickup", wait=0.5):
        driver.sleep(0.25)
        clicked = True
    if not clicked:
        return False
    select_preferred_store(driver, preferred_store_name)
    return True


def select_shipping(driver: Driver) -> bool:
    candidates = [
        'input[value="STANDARD"]',
        '[data-test="fulfillment-shipping"]',
        'input[value*="SHIP" i]',
        'label[for*="shipping" i]',
    ]
    for selector in candidates:
        if _click_selector(driver, selector, wait=0.35) or _js_click(driver, selector):
            driver.sleep(0.25)
            return True
    if _click_text(driver, "Shipping", wait=0.5):
        driver.sleep(0.25)
        return True
    return False


def ensure_shipping_address(driver: Driver, address: ShippingAddress) -> None:
    page = _page_lower(driver)
    if address.zip and address.zip in page and address.street.split()[0].lower() in page:
        return

    for label in ("Edit address", "Add address", "Change address", "Enter address"):
        if _click_text(driver, label, wait=0.8):
            break

    first = address.name.split()[0] if address.name else ""
    last = " ".join(address.name.split()[1:]) if address.name else ""
    field_map = [
        (
            [
                'input[name*="firstName" i]',
                'input[autocomplete="given-name"]',
                'input[data-test*="firstName" i]',
            ],
            first,
        ),
        (
            [
                'input[name*="lastName" i]',
                'input[autocomplete="family-name"]',
                'input[data-test*="lastName" i]',
            ],
            last,
        ),
        (
            [
                'input[name*="addressLine1" i]',
                'input[autocomplete="address-line1"]',
                'input[data-test*="addressLine1" i]',
            ],
            address.street,
        ),
        (
            [
                'input[name*="addressLine2" i]',
                'input[autocomplete="address-line2"]',
            ],
            address.street2,
        ),
        (
            [
                'input[name*="city" i]',
                'input[autocomplete="address-level2"]',
            ],
            address.city,
        ),
        (
            [
                'input[name*="state" i]',
                'input[autocomplete="address-level1"]',
            ],
            address.state,
        ),
        (
            [
                'input[name*="zip" i]',
                'input[name*="postal" i]',
                'input[autocomplete="postal-code"]',
            ],
            address.zip,
        ),
        (
            [
                'input[name*="phone" i]',
                'input[autocomplete="tel"]',
                'input[type="tel"]',
            ],
            address.phone,
        ),
    ]
    for selectors, value in field_map:
        _type_first(driver, selectors, value, wait=0.8)

    for label in ("Save & continue", "Save and continue", "Save", "Use this address"):
        if _click_text(driver, label, wait=0.8):
            driver.sleep(0.35)
            break


def ensure_contact_phone(driver: Driver, phone: str) -> None:
    if not phone:
        return
    _type_first(
        driver,
        [
            'input[name*="phone" i]',
            'input[autocomplete="tel"]',
            'input[data-test*="phone" i]',
            'input[type="tel"]',
        ],
        phone,
        wait=1.0,
    )


def step_up_auth_visible(driver: Driver) -> bool:
    text = _page_lower(driver)
    # Prefer modal-specific phrases so the global header "Sign in" is ignored.
    return any(
        token in text
        for token in (
            "use a passkey",
            "get a code",
            "enter your code",
            "we've sent your code",
            "sign in to your account",
            "try another way to sign in",
            "you've got mail",
            "keep this browser tab open to enter your code",
        )
    )


def on_cart_page(driver: Driver) -> bool:
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""
    if "/cart" in url:
        return True
    try:
        return bool(driver.run_js("return !!document.querySelector('[data-test=\"checkout-button\"]')"))
    except Exception:
        return False


def on_checkout_page(driver: Driver) -> bool:
    """True only when the real checkout review UI is present (not just /checkout URL)."""
    if step_up_auth_visible(driver) or _otp_entry_visible(driver):
        return False

    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""

    try:
        state = driver.run_js(
            """
            const checkoutBtn = !!document.querySelector('[data-test="checkout-button"]');
            const place = !!document.querySelector('[data-test="placeOrderButton"]');
            const cvv = !!(
              document.querySelector('input[autocomplete="cc-csc"]')
              || document.querySelector('input[name*="cvv" i]')
              || document.querySelector('input[aria-label*="security code" i]')
            );
            return {checkoutBtn, place, cvv};
            """
        ) or {}
    except Exception:
        state = {}

    if "/cart" in url and state.get("checkoutBtn"):
        return False
    if state.get("checkoutBtn") and "/checkout" not in url:
        return False
    if state.get("place") or state.get("cvv"):
        return True
    if "/checkout" in url:
        text = _page_lower(driver)
        if "place order" in text:
            return True
        if "payment" in text and ("cvv" in text or "security code" in text or "ending in" in text):
            return True
    return False


def click_checkout_button(driver: Driver) -> bool:
    """Spam Check out until auth modal or checkout page appears."""
    disable_webauthn_prompts(driver)

    def _action() -> bool:
        disable_webauthn_prompts(driver)
        for selector in (
            '[data-test="checkout-button"]',
            '[data-test="checkout-button-bottom"]',
            'button[data-test*="checkout" i]',
        ):
            if _human_click_selector(driver, selector) or _js_click(driver, selector):
                return True
        return _click_text(driver, "Check out", wait=0.2) or _click_text(
            driver, "Checkout", wait=0.15
        )

    def _ok() -> bool:
        return on_checkout_page(driver) or step_up_auth_visible(driver) or _otp_entry_visible(driver)

    return _spam_until(
        driver,
        action=_action,
        success=_ok,
        label="Check out",
        max_tries=16,
        peek_lo=0.05,
        peek_hi=0.12,
    )


def ensure_reached_checkout(driver: Driver, *, attempts: int = 8) -> bool:
    """After auth, force navigation onto the real checkout page if still on cart."""
    for i in range(1, attempts + 1):
        if on_checkout_page(driver):
            return True

        if step_up_auth_visible(driver) or _otp_entry_visible(driver):
            return False

        print(f"[CHECKOUT] not on checkout yet (attempt {i}/{attempts}) — spam Check out")
        if on_cart_page(driver) or "cart" in (driver.current_url or ""):
            click_checkout_button(driver)
        else:
            driver.get("https://www.target.com/cart")
            _human_pause(driver, 0.15, 0.3)
            click_checkout_button(driver)

        if not on_checkout_page(driver) and not step_up_auth_visible(driver):
            driver.get("https://www.target.com/checkout")
            _human_pause(driver, 0.2, 0.4)

        if on_checkout_page(driver):
            print("[CHECKOUT] reached checkout page")
            return True

        if step_up_auth_visible(driver) or _otp_entry_visible(driver):
            print("[CHECKOUT] sign-in modal returned after Check out click")
            return False

    return on_checkout_page(driver)


def _click_visible_button_text(driver: Driver, labels: tuple[str, ...]) -> str | None:
    """JS-click the first visible button/link whose text matches a label."""
    import json

    labels_json = json.dumps(list(labels))
    try:
        matched = driver.run_js(
            f"""
            const labels = {labels_json}.map((s) => s.toLowerCase());
            const roots = [
              document.querySelector('[role="dialog"]'),
              document.querySelector('[aria-modal="true"]'),
              document.querySelector('#modal-root'),
              document.querySelector('[data-test*="auth" i]'),
              document.body,
            ].filter(Boolean);
            const pickNodes = (root) => Array.from(root.querySelectorAll(
              'button, a, [role="button"], input[type="submit"], input[type="button"], div, span'
            ));
            const visible = (el) => {{
              const style = window.getComputedStyle(el);
              if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
              const r = el.getBoundingClientRect();
              return r.width > 8 && r.height > 8;
            }};
            const nodeText = (el) => ((el.innerText || el.value || el.getAttribute('aria-label') || '')
              ).replace(/\\s+/g, ' ').trim().toLowerCase();

            // Prefer exact matches inside dialogs first, then includes.
            for (const exact of [true, false]) {{
              for (const label of labels) {{
                for (const root of roots) {{
                  for (const el of pickNodes(root)) {{
                    if (!visible(el)) continue;
                    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
                    const text = nodeText(el);
                    if (!text || text.length > 80) continue;
                    const hit = exact ? (text === label) : (text.includes(label) && text.length < 40);
                    if (!hit) continue;
                    el.scrollIntoView({{block: 'center', inline: 'center'}});
                    el.click();
                    return el.innerText || el.value || label;
                  }}
                }}
              }}
            }}
            return null;
            """
        )
    except Exception:
        matched = None
    if matched:
        driver.sleep(0.35)
        return str(matched)
    for label in labels:
        if _click_text(driver, label, wait=0.6):
            return label
    return None


def _target_auth_error_visible(driver: Driver) -> bool:
    """True only for a visible Target error in the sign-in drawer.

    Avoid false positives from hidden/stale page text — those made us keep
    re-clicking Get a code after the OTP entry screen was already up.
    """
    try:
        return bool(
            driver.run_js(
                """
                const dialog = document.querySelector(
                  '[role="dialog"], .ReactModal__Content, .ModalDrawer .ReactModal__Content'
                );
                const root = dialog || document.body;
                const nodes = Array.from(root.querySelectorAll('div, p, span, h1, h2, h3, section'));
                for (const el of nodes) {
                  const text = ((el.innerText || '') + '').replace(/\\s+/g, ' ').trim();
                  if (!text || text.length > 220) continue;
                  if (!/something went wrong on our end/i.test(text)) continue;
                  const style = window.getComputedStyle(el);
                  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                  const r = el.getBoundingClientRect();
                  if (r.width < 8 || r.height < 8) continue;
                  return true;
                }
                return false;
                """
            )
        )
    except Exception:
        text = _page_lower(driver)
        return "something went wrong on our end" in text and "get a code" in text


def _otp_copy_visible(driver: Driver) -> bool:
    text = _page_lower(driver)
    return any(
        token in text
        for token in (
            "enter your code",
            "we've sent your code",
            "you've got mail",
            "keep this browser tab open to enter your code",
            "enter the code",
            "6-digit",
            "verification code",
        )
    )


def _otp_entry_visible(driver: Driver) -> bool:
    """Instant — JS only. Never use driver.select waits here (spam loops call this)."""
    try:
        return bool(
            driver.run_js(
                """
                const text = ((document.body && document.body.innerText) || '').toLowerCase();
                const copy = (
                  text.includes('enter your code')
                  || text.includes("we've sent your code")
                  || text.includes("you've got mail")
                  || text.includes('keep this browser tab open to enter your code')
                  || text.includes('verification code')
                  || text.includes('6-digit')
                );
                if (copy) return true;
                const inputs = Array.from(document.querySelectorAll(
                  'input[autocomplete="one-time-code"], input[placeholder*="code" i], input[name*="otp" i], input[name*="code" i], input[inputmode="numeric"]'
                ));
                for (const el of inputs) {
                  const ph = (el.getAttribute('placeholder') || '').toLowerCase();
                  const name = (el.name || '').toLowerCase();
                  const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
                  const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                  const blob = ph + ' ' + name + ' ' + ac + ' ' + aria;
                  if (blob.includes('search') || name.includes('email') || blob.includes('password')) continue;
                  if (ac === 'one-time-code' || /otp|code|passcode/.test(blob)) return true;
                }
                return false;
                """
            )
        )
    except Exception:
        return _otp_copy_visible(driver)


def _find_otp_input(driver: Driver):
    """Return a CSS selector for the OTP field via one JS pass (no waits)."""
    try:
        return driver.run_js(
            """
            const candidates = [
              'input[autocomplete="one-time-code"]',
              'input[placeholder*="Enter your code" i]',
              'input[placeholder*="code" i]',
              'input[name*="otp" i]',
              'input[name*="code" i]',
              'input[data-test*="otp" i]',
              'input[data-test*="code" i]',
              'input[inputmode="numeric"]',
              'input[type="tel"]',
            ];
            for (const sel of candidates) {
              let el = null;
              try { el = document.querySelector(sel); } catch (e) { continue; }
              if (!el) continue;
              const ph = (el.getAttribute('placeholder') || '').toLowerCase();
              const name = (el.name || '').toLowerCase();
              const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
              const aria = (el.getAttribute('aria-label') || '').toLowerCase();
              const blob = ph + ' ' + name + ' ' + ac + ' ' + aria;
              if (blob.includes('search') || name.includes('email') || blob.includes('password')) continue;
              if (ac === 'one-time-code' || /otp|code|passcode|one-time/.test(blob)) return sel;
              if (sel === 'input[inputmode="numeric"]' || sel === 'input[type="tel"]') {
                const text = ((document.body && document.body.innerText) || '').toLowerCase();
                if (text.includes('enter your code') || text.includes('verification code')) return sel;
              }
            }
            return null;
            """
        )
    except Exception:
        return None


def _auth_method_chooser_visible(driver: Driver) -> bool:
    """True on the passkey / Get a code chooser (before code entry)."""
    text = _page_lower(driver)
    return ("get a code" in text or "use a passkey" in text) and not _otp_entry_visible(driver)


def disable_webauthn_prompts(driver: Driver) -> None:
    """Stub WebAuthn so Target cannot open the OS passkey sheet.

    The macOS/Chrome dialog ("Use a saved passkey for target.com") blocks
    automation. We never want passkey — email OTP (#otp) is the path.
    Call this before Check out / whenever the sign-in drawer may appear.
    """
    try:
        ok = driver.run_js(
            """
            const reject = () => Promise.reject(new DOMException(
              'WebAuthn disabled for automation', 'NotAllowedError'
            ));
            if (navigator.credentials) {
              try {
                Object.defineProperty(navigator.credentials, 'get', {configurable:true, value: reject});
                Object.defineProperty(navigator.credentials, 'create', {configurable:true, value: reject});
              } catch (e) {
                navigator.credentials.get = reject;
                navigator.credentials.create = reject;
              }
            }
            try {
              if (window.PublicKeyCredential) {
                PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable =
                  () => Promise.resolve(false);
                if (PublicKeyCredential.isConditionalMediationAvailable) {
                  PublicKeyCredential.isConditionalMediationAvailable =
                    () => Promise.resolve(false);
                }
              }
            } catch (e) {}
            return true;
            """
        )
        if ok:
            print("[AUTH] WebAuthn/passkey prompts disabled in page")
    except Exception as exc:
        print(f"[AUTH] could not stub WebAuthn: {exc}")


def _activate_role_button(driver: Driver, el_selector: str) -> bool:
    """Human-ish click on a Target NDS role=button cell — never #passkey."""
    if "passkey" in el_selector.lower():
        print(f"[AUTH] refused to activate passkey selector {el_selector!r}")
        return False
    ok = _realistic_click_selector(driver, el_selector)
    if ok:
        print(f"[AUTH] activated {el_selector!r}")
    return ok


def click_get_a_code_button(driver: Driver) -> bool:
    """Realistic mouse click on #otp / Get a code — never #passkey."""
    try:
        driver.run_js(
            """
            const pk = document.querySelector('#passkey');
            if (pk) {
              pk.setAttribute('aria-disabled', 'true');
              pk.style.pointerEvents = 'none';
            }
            """
        )
    except Exception:
        pass

    for sel in ('#otp[role="button"]', "#otp", '[role="dialog"] #otp'):
        if _realistic_click_selector(driver, sel):
            return True

    # Text fallback when Target changes the id
    try:
        result = driver.run_js(
            """
            const nodes = Array.from(document.querySelectorAll('[role="button"], button, a'));
            for (const n of nodes) {
              if ((n.id || '').toLowerCase() === 'passkey') continue;
              const t = ((n.innerText || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
              if (t === 'get a code' || t.startsWith('get a code')
                  || t.includes('email me a code') || t.includes('send a code')) {
                n.scrollIntoView({block: 'center', inline: 'nearest'});
                const r = n.getBoundingClientRect();
                return {
                  ok: true,
                  x: Math.round(r.left + r.width * (0.4 + Math.random() * 0.2)),
                  y: Math.round(r.top + r.height * (0.4 + Math.random() * 0.2)),
                };
              }
            }
            return {ok: false};
            """
        )
    except Exception as exc:
        print(f"[AUTH] Get a code locate failed: {exc}")
        return False
    if isinstance(result, dict) and result.get("ok"):
        x, y = int(result["x"]), int(result["y"])
        _enable_human_mouse(driver)
        try:
            if hasattr(driver, "move_mouse_to_point"):
                driver.move_mouse_to_point(x, y, is_jump=True)
                _human_pause(driver, 0.015, 0.04)
            if hasattr(driver, "click_at_point"):
                driver.click_at_point(x, y, skip_move=True)
                _human_pause(driver, 0.02, 0.05)
                return True
        except Exception:
            pass
        # Last resort: synthetic events at those coords via JS on element under point
        try:
            driver.run_js(
                f"""
                const el = document.elementFromPoint({x}, {y});
                if (!el) return false;
                const opts = (buttons) => ({{
                  bubbles: true, cancelable: true, view: window,
                  clientX: {x}, clientY: {y}, button: 0, buttons,
                  pointerId: 1, pointerType: 'mouse', isPrimary: true,
                }});
                el.dispatchEvent(new PointerEvent('pointerdown', opts(1)));
                el.dispatchEvent(new MouseEvent('mousedown', opts(1)));
                el.dispatchEvent(new PointerEvent('pointerup', opts(0)));
                el.dispatchEvent(new MouseEvent('mouseup', opts(0)));
                el.dispatchEvent(new MouseEvent('click', opts(0)));
                try {{ el.click(); }} catch (e) {{}}
                return true;
                """
            )
            return True
        except Exception:
            return False
    return False


def request_email_code(driver: Driver, *, force_new: bool = False) -> bool:
    """Request email OTP gently — a few human clicks, then wait.

    Do NOT spam through Target's "Something went wrong on our end" banner; that
    is how we get rate-limited / T83072242. Stop early and let the user retry.
    """
    disable_webauthn_prompts(driver)
    if _otp_entry_visible(driver) and not force_new:
        print("[AUTH] OTP entry already visible")
        return True

    if _target_auth_error_visible(driver) and not force_new:
        print(
            "[AUTH] Target error banner visible — NOT spam-clicking. "
            "Wait / refresh / use session-target.sh manually."
        )
        return False

    def _ok() -> bool:
        return _otp_entry_visible(driver)

    if force_new and _ok():
        clicked = _click_visible_button_text(
            driver,
            ("resend", "send a new code", "didn't get a code?", "get a new code"),
        )
        if clicked:
            print(f"[AUTH] clicked resend once: {clicked!r}")
            _human_pause(driver, 0.8, 1.4)
            return True
        # One Get a code only — never a loop of 8.
        click_get_a_code_button(driver)
        _human_pause(driver, 1.0, 1.8)
        return _ok()

    # Soft path: at most 3 spaced clicks.
    max_tries = 3
    for i in range(1, max_tries + 1):
        if _ok():
            if i > 1:
                print(f"[AUTH] Get a code landed on try {i}")
            return True
        if _target_auth_error_visible(driver):
            print(
                f"[AUTH] Target 'something went wrong' after click {i} — stopping "
                "(spam makes it worse)"
            )
            return False
        click_get_a_code_button(driver)
        if _ok():
            print(f"[AUTH] Get a code landed on try {i}")
            return True
        # Human-scale pause between attempts.
        _human_pause(driver, 1.2, 2.2)
    return bool(_ok())


def _paste_into_selector(driver: Driver, selector: str, value: str) -> bool:
    """Paste a value in one shot (insertFromPaste) instead of key-by-key typing."""
    if not value or not selector:
        return False
    try:
        ok = driver.run_js(
            f"""
            const el = document.querySelector({selector!r});
            if (!el) return false;
            el.focus({{preventScroll: true}});
            try {{ el.select(); }} catch (e) {{}}
            el.value = '';
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            const text = {value!r};
            // Prefer native value setter so React controlled inputs update.
            const proto = window.HTMLInputElement && window.HTMLInputElement.prototype;
            const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) {{
              desc.set.call(el, text);
            }} else {{
              el.value = text;
            }}
            try {{
              el.dispatchEvent(new InputEvent('input', {{
                bubbles: true,
                cancelable: true,
                inputType: 'insertFromPaste',
                data: text,
              }}));
            }} catch (e) {{
              el.dispatchEvent(new Event('input', {{bubbles: true}}));
            }}
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            try {{
              el.dispatchEvent(new ClipboardEvent('paste', {{bubbles: true, cancelable: true}}));
            }} catch (e) {{}}
            return (el.value || '') === text || (el.value || '').includes(text);
            """
        )
        return bool(ok)
    except Exception:
        return False


def _click_verify_button(driver: Driver) -> bool:
    if _click_visible_button_text(driver, ("verify", "continue", "submit")):
        return True
    return _click_text(driver, "Verify", wait=0.35)


def submit_otp(driver: Driver, code: str, *, max_verify_tries: int = 20) -> bool:
    """Paste the emailed code and spam Verify until Target accepts it."""
    code = (code or "").strip()
    if len(code) < 4:
        return False

    selector = _find_otp_input(driver)
    if not selector:
        request_email_code(driver, force_new=False)
        selector = _find_otp_input(driver)
        if not selector:
            return False

    def _accepted() -> bool:
        if on_checkout_page(driver):
            return True
        if not step_up_auth_visible(driver) and _find_otp_input(driver) is None:
            return True
        if not step_up_auth_visible(driver) and not _otp_entry_visible(driver):
            return True
        return False

    for attempt in range(1, max_verify_tries + 1):
        if _accepted():
            return True

        selector = _find_otp_input(driver) or selector
        pasted = _paste_into_selector(driver, selector, code)
        if not pasted:
            try:
                driver.clear(selector)
            except Exception:
                pass
            try:
                if not _paste_into_selector(driver, selector, code):
                    driver.type(selector, code)
                    pasted = True
            except Exception:
                print(f"[AUTH] could not paste OTP (attempt {attempt})")
                _human_pause(driver, 0.06, 0.12)
                continue
        if pasted:
            print(f"[AUTH] pasted OTP + Verify ({attempt}/{max_verify_tries})")

        _human_pause(driver, 0.05, 0.12)
        if not _click_verify_button(driver):
            _click_text(driver, "Verify", wait=0.12)
            _human_pause(driver, 0.05, 0.1)

        peek_until = time.time() + random.uniform(0.25, 0.45)
        while time.time() < peek_until:
            if _accepted():
                return True
            driver.sleep(0.05)

        if _target_auth_error_visible(driver):
            print("[AUTH] error on code entry — spam Verify again")
        _human_pause(driver, 0.06, 0.14)

    return _accepted()


def wait_for_checkout_auth(driver: Driver, *, timeout_seconds: float) -> bool:
    """Wait until step-up auth clears and the real checkout page is open.

    After OTP succeeds Target often leaves you on /cart — only then re-click
    Check out. Do not click Check out while waiting for an emailed code.
    """
    from datetime import datetime, timedelta, timezone

    from scalping.bots.target.gmail_otp import fetch_latest_target_otp, load_gmail_credentials

    if on_checkout_page(driver):
        return True

    disable_webauthn_prompts(driver)

    gmail = load_gmail_credentials()
    # Small lookback so a code emailed a second before our clock stamp still matches.
    code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=15)
    print("[AUTH] requesting email code (not passkey)…")
    requested = request_email_code(driver, force_new=False)
    if not _otp_entry_visible(driver):
        requested = request_email_code(driver, force_new=False) or requested
    if not requested and not _otp_entry_visible(driver):
        print("[AUTH] warning: could not find Get a code control yet")

    print(
        "\n=== TARGET CHECKOUT SIGN-IN REQUIRED ===\n"
        "Target is asking for a step-up sign-in before checkout.\n"
        "Options:\n"
        "  1) Auto-read emailed code via Gmail IMAP "
        f"({'configured: ' + gmail.login if gmail.is_configured else 'NOT configured'})\n"
        "  2) If a macOS passkey sheet appears, click Cancel — we use email codes only\n"
        "  3) Paste TARGET_OTP=123456 into .env while waiting\n"
        f"Waiting up to {int(timeout_seconds)}s...\n"
    )

    deadline = time.time() + max(5.0, timeout_seconds)
    used_codes: set[str] = set()
    next_gmail_poll = 0.0
    # Give the first email time to arrive before hammering Resend.
    next_resend = time.time() + 45.0
    otp_accepted = False
    auth_missing_streak = 0

    while time.time() < deadline:
        if on_checkout_page(driver):
            clear_target_otp()
            print("[AUTH] On checkout page")
            return True

        entry_up = _otp_entry_visible(driver)
        auth_up = step_up_auth_visible(driver) or entry_up

        otp = read_target_otp()
        if gmail.is_configured and time.time() >= next_gmail_poll:
            next_gmail_poll = time.time() + 0.25
            try:
                gmail_otp = fetch_latest_target_otp(gmail, newer_than=code_requested_at)
            except Exception as exc:
                print(f"[GMAIL] poll error: {exc}")
                gmail_otp = None
            if gmail_otp and gmail_otp not in used_codes:
                print(f"[GMAIL] using code ending …{gmail_otp[-2:]}")
                otp = gmail_otp

        if auth_up and otp and len(otp) >= 4 and otp not in used_codes:
            print(f"[AUTH] Submitting OTP ({len(otp)} digits)")
            if submit_otp(driver, otp):
                used_codes.add(otp)
                clear_target_otp()
                otp_accepted = True
                print("[AUTH] OTP accepted — opening checkout")
                driver.sleep(0.2)
                if ensure_reached_checkout(driver):
                    return True
                if step_up_auth_visible(driver) or _otp_entry_visible(driver):
                    print("[AUTH] Check out re-triggered sign-in — requesting new code")
                    code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
                    request_email_code(driver, force_new=False)
                    next_resend = time.time() + 45.0
                    otp_accepted = False
                continue
            # Same code may still be valid — Target just glitched. Retry Verify
            # path again shortly without burning the code or forcing Resend.
            print("[AUTH] OTP not accepted yet — will retry same code")
            next_resend = min(next_resend, time.time() + 8.0)
            time.sleep(0.3)
            continue

        # OTP entry already up → just wait for Gmail. Do not treat the leftover
        # "something went wrong" banner as a reason to re-click Get a code.
        if entry_up and time.time() >= next_resend:
            print("[AUTH] still waiting for email — resending code once")
            code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            request_email_code(driver, force_new=True)
            next_resend = time.time() + 1.0
        elif (not entry_up) and auth_up and time.time() >= next_resend:
            print("[AUTH] chooser/error still up — spam-clicking Get a code")
            code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            request_email_code(driver, force_new=False)
            next_resend = time.time() + 0.8

        if auth_up:
            auth_missing_streak = 0
        else:
            auth_missing_streak += 1

        # Only re-click Check out AFTER OTP succeeded. Do not treat a brief
        # modal flicker as "unlocked" — that was aborting the email OTP flow.
        if otp_accepted:
            if ensure_reached_checkout(driver):
                clear_target_otp()
                print("[AUTH] Checkout unlocked")
                return True
            if step_up_auth_visible(driver) or _otp_entry_visible(driver):
                otp_accepted = False
                code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
                request_email_code(driver, force_new=False)
                next_resend = time.time() + 45.0
            continue

        if auth_missing_streak >= 8 and not otp_accepted:
            # Modal gone without OTP — maybe Face ID / already cleared. Try checkout once.
            print("[AUTH] sign-in UI gone without OTP — trying Check out")
            if ensure_reached_checkout(driver):
                clear_target_otp()
                print("[AUTH] Checkout unlocked")
                return True
            auth_missing_streak = 0
            if step_up_auth_visible(driver) or _otp_entry_visible(driver):
                code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
                request_email_code(driver, force_new=False)
                next_resend = time.time() + 45.0

        time.sleep(0.2)

    if otp_accepted:
        return ensure_reached_checkout(driver)
    print("[AUTH] timed out waiting for step-up sign-in / OTP")
    return on_checkout_page(driver)


def sign_in_to_buy_visible(driver: Driver) -> bool:
    """PDP buy-box gate: signed-in header can still show 'Sign in to buy now'."""
    try:
        return bool(
            driver.run_js(
                """
                const btn = document.querySelector('[data-test="sign-in-to-buy-now-button"]');
                if (btn) {
                  const r = btn.getBoundingClientRect();
                  if (r.width > 4 && r.height > 4) return true;
                }
                const root = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]');
                const t = ((root && root.innerText) || '').toLowerCase();
                return t.includes('sign in to buy');
                """
            )
        )
    except Exception:
        return False


def click_sign_in_to_buy(driver: Driver) -> bool:
    """Open the purchase step-up from the PDP buy box."""
    disable_webauthn_prompts(driver)
    try:
        hit = driver.run_js(
            """
            const prefer = document.querySelector('[data-test="sign-in-to-buy-now-button"]');
            const candidates = prefer
              ? [prefer]
              : Array.from(document.querySelectorAll('button, a, [role="button"]'));
            for (const el of candidates) {
              const t = ((el.innerText || el.getAttribute('aria-label') || '') + '')
                .replace(/\\s+/g, ' ').trim().toLowerCase();
              if (!t.includes('sign in to buy')) continue;
              if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
              const r = el.getBoundingClientRect();
              if (r.width < 4 || r.height < 4) continue;
              el.scrollIntoView({block: 'center', inline: 'nearest'});
              el.click();
              return true;
            }
            return false;
            """
        )
        if hit:
            print("[AUTH] clicked Sign in to buy now")
            time.sleep(0.6)
            return True
    except Exception as exc:
        print(f"[AUTH] Sign in to buy click failed: {exc}")
    return False


def wait_for_pdp_purchase_auth(driver: Driver, *, timeout_seconds: float = 120.0) -> bool:
    """Clear PDP 'Sign in to buy now' via email OTP so ATC can write the cart.

    Header 'Hi, Name' is not enough — Target still gates cart writes until this
    step-up completes. Gentle: one Sign-in click, few Get a code clicks, Gmail OTP.
    """
    from datetime import datetime, timedelta, timezone

    from scalping.bots.target.gmail_otp import fetch_latest_target_otp, load_gmail_credentials

    if not sign_in_to_buy_visible(driver) and not step_up_auth_visible(driver) and not _otp_entry_visible(driver):
        return True

    disable_webauthn_prompts(driver)
    gmail = load_gmail_credentials()
    code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=15)

    if sign_in_to_buy_visible(driver):
        if not click_sign_in_to_buy(driver):
            print("[AUTH] could not click Sign in to buy now")
            return False

    print("[AUTH] PDP purchase step-up — requesting email code…")
    requested = request_email_code(driver, force_new=False)
    if not _otp_entry_visible(driver):
        requested = request_email_code(driver, force_new=False) or requested
    if not requested and not _otp_entry_visible(driver) and _target_auth_error_visible(driver):
        print("[AUTH] Target soft-blocked on PDP step-up — stop (do not spam)")
        return False

    print(
        "\n=== TARGET PDP SIGN-IN TO BUY ===\n"
        "Buy box requires purchase step-up (not just homepage login).\n"
        f"Gmail auto-OTP: {'yes (' + gmail.login + ')' if gmail.is_configured else 'NO — set GMAIL_* or TARGET_OTP'}\n"
        f"Waiting up to {int(timeout_seconds)}s...\n"
    )

    deadline = time.time() + max(15.0, timeout_seconds)
    used_codes: set[str] = set()
    next_gmail_poll = 0.0
    next_resend = time.time() + 50.0

    while time.time() < deadline:
        if not sign_in_to_buy_visible(driver) and not step_up_auth_visible(driver) and not _otp_entry_visible(driver):
            clear_target_otp()
            print("[AUTH] PDP purchase gate cleared")
            return True

        if _target_auth_error_visible(driver) and not _otp_entry_visible(driver):
            print("[AUTH] error banner on PDP step-up — stopping (wait / manual)")
            return False

        entry_up = _otp_entry_visible(driver)
        auth_up = step_up_auth_visible(driver) or entry_up or sign_in_to_buy_visible(driver)

        otp = read_target_otp()
        if gmail.is_configured and time.time() >= next_gmail_poll:
            next_gmail_poll = time.time() + 0.35
            try:
                gmail_otp = fetch_latest_target_otp(gmail, newer_than=code_requested_at)
            except Exception as exc:
                print(f"[GMAIL] poll error: {exc}")
                gmail_otp = None
            if gmail_otp and gmail_otp not in used_codes:
                print(f"[GMAIL] using code ending …{gmail_otp[-2:]}")
                otp = gmail_otp

        if auth_up and otp and len(otp) >= 4 and otp not in used_codes:
            print(f"[AUTH] Submitting PDP OTP ({len(otp)} digits)")
            if submit_otp(driver, otp):
                used_codes.add(otp)
                clear_target_otp()
                time.sleep(0.8)
                if not sign_in_to_buy_visible(driver) and not _otp_entry_visible(driver):
                    print("[AUTH] PDP purchase gate cleared after OTP")
                    return True
                # Modal may need one more Sign in to buy / Get a code cycle.
                if sign_in_to_buy_visible(driver):
                    click_sign_in_to_buy(driver)
                continue
            print("[AUTH] OTP not accepted yet — will retry")
            time.sleep(0.4)
            continue

        if entry_up and time.time() >= next_resend:
            print("[AUTH] still waiting for email — one resend")
            code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            if not request_email_code(driver, force_new=True):
                next_resend = time.time() + 90.0
            else:
                next_resend = time.time() + 55.0
        elif sign_in_to_buy_visible(driver) and not entry_up and time.time() >= next_resend:
            click_sign_in_to_buy(driver)
            request_email_code(driver, force_new=False)
            next_resend = time.time() + 55.0
        elif (not entry_up) and auth_up and time.time() >= next_resend:
            code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            if not request_email_code(driver, force_new=False):
                next_resend = time.time() + 90.0
            else:
                next_resend = time.time() + 55.0

        time.sleep(0.25)

    print("[AUTH] timed out on PDP Sign in to buy now")
    return not sign_in_to_buy_visible(driver)


def start_checkout(driver: Driver, *, wait_auth: bool = True, auth_timeout: float = 300.0) -> bool:
    clicked = click_checkout_button(driver)

    # Direct navigation fallback
    if not clicked and not on_checkout_page(driver) and not step_up_auth_visible(driver):
        driver.get("https://www.target.com/checkout")
        time.sleep(0.2)
        clicked = True

    if not wait_auth:
        # Caller will handle auth / re-click; just report whether checkout is open
        if on_checkout_page(driver):
            return True
        if step_up_auth_visible(driver):
            return False
        return clicked

    if step_up_auth_visible(driver) or not on_checkout_page(driver):
        ok = wait_for_checkout_auth(driver, timeout_seconds=auth_timeout)
        if ok:
            return True
        # Last attempt: force checkout click after auth wait
        return ensure_reached_checkout(driver)

    return True


def _open_add_card(driver: Driver) -> None:
    for label in (
        "Add card",
        "Add a card",
        "Add new card",
        "Add credit or debit card",
        "Edit",
        "Payment",
        "Change",
    ):
        if _click_text(driver, label, wait=0.4):
            driver.sleep(0.3)
            break


def fill_billing_address(driver: Driver, payment: PaymentInfo) -> None:
    billing = payment.billing
    if not billing.is_complete:
        return
    field_map = [
        (
            [
                'input[name*="billing" i][name*="address" i]',
                'input[autocomplete="billing address-line1"]',
                'input[name*="addressLine1" i]',
            ],
            billing.street,
        ),
        (
            ['input[autocomplete="billing address-line2"]', 'input[name*="addressLine2" i]'],
            billing.street2,
        ),
        (
            ['input[autocomplete="billing address-level2"]', 'input[name*="city" i]'],
            billing.city,
        ),
        (
            ['input[autocomplete="billing address-level1"]', 'input[name*="state" i]'],
            billing.state,
        ),
        (
            [
                'input[autocomplete="billing postal-code"]',
                'input[name*="zip" i]',
                'input[name*="postal" i]',
            ],
            billing.zip,
        ),
    ]
    for selectors, value in field_map:
        _type_first(driver, selectors, value, wait=0.6)


def fill_cvv(driver: Driver, cvv: str) -> bool:
    if not cvv:
        return False

    # Expand / reveal CVV if Target collapses the saved-card row.
    for label in (
        "Enter CVV",
        "Edit",
        "Edit payment",
        "Security code",
        "CVV",
    ):
        try:
            el = driver.get_element_containing_text(label, wait=0.4)
            if el is not None:
                text = (el.text or "").strip().lower()
                if text in {"edit", "enter cvv", "security code", "cvv"} or "cvv" in text:
                    el.click()
                    driver.sleep(0.25)
        except Exception:
            pass

    selectors = [
        'input[autocomplete="cc-csc"]',
        'input[name*="cvv" i]',
        'input[name*="security" i]',
        'input[data-test*="cvv" i]',
        'input[data-test*="CVV" i]',
        'input[aria-label*="CVV" i]',
        'input[aria-label*="security code" i]',
        'input[placeholder*="CVV" i]',
        'input[placeholder*="security code" i]',
        'input[id*="cvv" i]',
        'input[id*="csc" i]',
    ]
    if _type_first(driver, selectors, cvv, wait=0.6):
        return True

    # Card fields are often inside payment iframes.
    try:
        frames = driver.run_js(
            """
            return Array.from(document.querySelectorAll('iframe')).map((f, i) => ({
              i,
              name: f.name || '',
              id: f.id || '',
              title: f.title || '',
              src: (f.src || '').slice(0, 120),
            }));
            """
        ) or []
    except Exception:
        frames = []
    for frame in frames:
        blob = f"{frame.get('name','')} {frame.get('id','')} {frame.get('title','')} {frame.get('src','')}".lower()
        if not any(k in blob for k in ("card", "pay", "credit", "cvv", "secure", "token", "braintree", "spreedly", "stripe")):
            # Still try unnamed small payment frames later
            if frame.get("name") or frame.get("id") or frame.get("title"):
                continue
        try:
            iframe = None
            if frame.get("id"):
                iframe = driver.select_iframe(f"iframe#{frame['id']}", 1.0)
            if iframe is None and frame.get("name"):
                name = str(frame["name"]).replace("\\", "\\\\").replace('"', '\\"')
                iframe = driver.select_iframe(f'iframe[name="{name}"]', 1.0)
            if iframe is None:
                continue
            for sel in (
                'input[autocomplete="cc-csc"]',
                'input[name*="cvv" i]',
                'input[aria-label*="CVV" i]',
                'input[placeholder*="CVV" i]',
                "input",
            ):
                try:
                    el = iframe.select(sel, 0.8) if hasattr(iframe, "select") else None
                    if el is None:
                        continue
                    try:
                        iframe.clear(sel)
                    except Exception:
                        pass
                    iframe.type(sel, cvv)
                    print(f"[PAYMENT] typed CVV in iframe {frame.get('id') or frame.get('name') or frame.get('i')}")
                    return True
                except Exception:
                    continue
        except Exception:
            continue

    # Last resort: JS set on any matching input (non-iframe).
    try:
        ok = bool(
            driver.run_js(
                f"""
                const cvv = {cvv!r};
                const nodes = Array.from(document.querySelectorAll('input')).filter((el) => {{
                  const blob = [
                    el.autocomplete||'', el.name||'', el.id||'',
                    el.placeholder||'', el.getAttribute('aria-label')||'',
                    el.getAttribute('data-test')||''
                  ].join(' ').toLowerCase();
                  return /cvv|csc|security code|card verification/.test(blob);
                }});
                if (!nodes.length) return false;
                const el = nodes[0];
                el.focus();
                el.value = cvv;
                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                return true;
                """
            )
        )
        if ok:
            print("[PAYMENT] set CVV via JS")
            return True
    except Exception:
        pass
    return False


def fill_payment(driver: Driver, payment: PaymentInfo) -> bool:
    if not payment.is_complete:
        return False

    # Wait briefly for checkout payment widgets to hydrate.
    for _ in range(8):
        if (
            driver.select('[data-test="placeOrderButton"]', 0.25) is not None
            or "cvv" in _page_lower(driver)
            or "security code" in _page_lower(driver)
            or "ending in" in _page_lower(driver)
        ):
            break
        driver.sleep(0.35)

    # Saved-card path: Target usually only asks for CVV.
    if payment.use_saved_card:
        for attempt in range(1, 4):
            if fill_cvv(driver, payment.card_cvv):
                print(f"[PAYMENT] CVV filled (attempt {attempt})")
                return True
            driver.sleep(0.4)
        page = _page_lower(driver)
        if "ending in" in page or "card ending" in page or "visa" in page or "mastercard" in page:
            if "cvv" not in page and "security code" not in page:
                print("[PAYMENT] saved card visible and no CVV prompt — continuing")
                return True
        if not (
            payment.card_number
            and payment.card_holder_name
            and payment.card_expiration_date
        ):
            print("[PAYMENT] could not find CVV field for saved card")
            try:
                snippet = driver.run_js(
                    "return (document.body && document.body.innerText || '').slice(0, 800)"
                )
                print(f"[PAYMENT] checkout snippet: {snippet!r}")
            except Exception:
                pass
            return False

    return fill_new_card(driver, payment)


def fill_new_card(driver: Driver, payment: PaymentInfo) -> bool:
    _open_add_card(driver)
    month, year = payment.expiration_month_year
    filled = 0
    if _type_first(
        driver,
        [
            'input[autocomplete="cc-number"]',
            'input[name*="cardNumber" i]',
            'input[data-test*="cardNumber" i]',
            'input[aria-label*="card number" i]',
        ],
        payment.card_number,
    ):
        filled += 1
    if _type_first(
        driver,
        [
            'input[autocomplete="cc-name"]',
            'input[name*="nameOnCard" i]',
            'input[name*="cardName" i]',
            'input[aria-label*="name on card" i]',
        ],
        payment.card_holder_name,
    ):
        filled += 1

    # Combined expiry — Target #credit-card-expiration-input is maxlength=5 (MM/YY).
    exp_value = payment.expiration_mm_yy
    if exp_value and _type_first(
        driver,
        [
            "#credit-card-expiration-input",
            'input[name="credit-card-expiration-input"]',
            'input[autocomplete="cc-exp"]',
            'input[name*="expir" i]',
            'input[aria-label*="expir" i]',
            'input[placeholder*="MM" i]',
        ],
        exp_value,
        wait=1.0,
    ):
        filled += 1
        print(f"[PAYMENT] typed expiry {exp_value!r} (from env {payment.card_expiration_date!r})")
    else:
        # Separate month / year selects or inputs
        if month and _type_first(
            driver,
            [
                'select[name*="expir" i][name*="month" i]',
                'select[autocomplete="cc-exp-month"]',
                'input[name*="expMonth" i]',
            ],
            month,
            wait=0.8,
        ):
            filled += 1
        yy = year[-2:] if year else ""
        if yy and _type_first(
            driver,
            [
                'select[name*="expir" i][name*="year" i]',
                'select[autocomplete="cc-exp-year"]',
                'input[name*="expYear" i]',
            ],
            yy,
            wait=0.8,
        ):
            filled += 1

    if fill_cvv(driver, payment.card_cvv):
        filled += 1

    fill_billing_address(driver, payment)
    for label in ("Save & continue", "Save and continue", "Save", "Use this card"):
        _click_text(driver, label, wait=0.5)
    return filled >= 3


def checkout_appears_stuck(driver: Driver) -> bool:
    """Spinner / empty checkout — Discord tip: cart → checkout to refresh."""
    try:
        return bool(
            driver.run_js(
                """
                const text = ((document.body && document.body.innerText) || '').toLowerCase();
                if (text.includes('still loading') || text.includes('please wait')) return true;
                if (text.includes('something went wrong') && text.includes('try again')) return true;
                const spin = document.querySelector(
                  '[class*="Spinner"], [class*="spinner"], [data-test*="spinner" i], '
                  + '[aria-busy="true"], .circular-loading-indicator'
                );
                if (spin) {
                  const r = spin.getBoundingClientRect();
                  if (r.width > 8 && r.height > 8) return true;
                }
                // Checkout URL but no Place order and no CVV for a while → hung shell
                const url = (location.href || '').toLowerCase();
                if (url.includes('/checkout')) {
                  const place = document.querySelector('[data-test="placeOrderButton"]');
                  const cvv = document.querySelector('input[autocomplete="cc-csc"]');
                  if (!place && !cvv && text.length < 400) return true;
                }
                return false;
                """
            )
        )
    except Exception:
        return False


def checkout_ready_for_place_order(driver: Driver) -> bool:
    """True when checkout shows cart contents / Place order (post–F5 hydrate)."""
    try:
        return bool(
            driver.run_js(
                """
                const place = document.querySelector(
                  '[data-test="placeOrderButton"], button[data-test*="placeOrder" i]'
                );
                if (place && !place.disabled && place.getAttribute('aria-disabled') !== 'true') {
                  const r = place.getBoundingClientRect();
                  if (r.width > 8 && r.height > 8) return true;
                }
                const text = ((document.body && document.body.innerText) || '').toLowerCase();
                if (text.includes('place order') && !text.includes('your cart is empty')) {
                  if (document.querySelector('[data-test*="cartItem" i], [data-test*="cart-item" i]')) {
                    return true;
                  }
                }
                return false;
                """
            )
        )
    except Exception:
        return bool(driver.select('[data-test="placeOrderButton"]', 0.2))


def reload_checkout_until_ready(
    driver: Driver,
    *,
    timeout_seconds: float = 180.0,
) -> bool:
    """Live success path: open /checkout and F5 until cart / Place order appears.

    Mobile ATC → shared account cart → PC www.target.com/checkout → refresh until
    hydrated → click Place order. Stops early if cart is empty.
    """
    deadline = time.time() + max(15.0, timeout_seconds)
    reloads = 0
    try:
        driver.get("https://www.target.com/checkout")
    except Exception:
        pass
    time.sleep(0.6)
    print(
        f"[CHECKOUT] F5 until cart/Place order ready "
        f"(up to {int(timeout_seconds)}s)"
    )
    while time.time() < deadline:
        if checkout_order_blocked(driver):
            print("[CHECKOUT] sold-out/unavailable while waiting for hydrate")
            return False
        if cart_is_empty(driver) and "checkout" in (driver.current_url or "").lower():
            # Empty checkout page vs not-yet-loaded — only bail after a few reloads.
            if reloads >= 4:
                text = _page_lower(driver)
                if "cart is empty" in text or "your cart is empty" in text:
                    print("[CHECKOUT] cart empty on checkout — abort hydrate")
                    return False
        if checkout_ready_for_place_order(driver):
            print(f"[CHECKOUT] ready after {reloads} reloads")
            return True
        reloads += 1
        if reloads == 1 or reloads % 8 == 0:
            print(f"[CHECKOUT] reload #{reloads} (waiting for cart hydrate)")
        try:
            driver.refresh()
        except Exception:
            try:
                driver.get("https://www.target.com/checkout")
            except Exception:
                pass
        time.sleep(0.45 + random.uniform(0.1, 0.35))
    print(f"[CHECKOUT] hydrate timed out after {reloads} reloads")
    return checkout_ready_for_place_order(driver)


def refresh_cart_then_checkout(driver: Driver) -> bool:
    """When checkout spins: cart → checkout, else plain /checkout reload."""
    print("[CHECKOUT] refreshing via cart → checkout")
    try:
        go_to_cart(driver)
        time.sleep(0.35 + random.uniform(0.1, 0.3))
    except Exception:
        try:
            driver.get("https://www.target.com/cart")
            time.sleep(0.5)
        except Exception:
            pass
    if cart_is_empty(driver):
        print("[CHECKOUT] cart empty after refresh — abort")
        return False
    click_checkout_button(driver)
    time.sleep(0.4)
    if not on_checkout_page(driver):
        try:
            driver.get("https://www.target.com/checkout")
            time.sleep(0.5)
        except Exception:
            pass
    return on_checkout_page(driver) or checkout_ready_for_place_order(driver)


def place_order(driver: Driver) -> bool:
    """Single Place order click (see spam_place_order for clicker-style retries)."""
    for selector in (
        '[data-test="placeOrderButton"]',
        'button[data-test*="placeOrder" i]',
        'button[data-test*="place-order" i]',
    ):
        if _js_click(driver, selector) or _click_selector(driver, selector, wait=0.15):
            return True
    return _click_text(driver, "Place order", wait=0.25) or _click_text(
        driver, "Place my order", wait=0.2
    )


def checkout_order_blocked(driver: Driver) -> bool:
    """True when inventory/traffic killed the order (stop spamming)."""
    text = _page_lower(driver)
    return any(
        token in text
        for token in (
            "no longer available",
            "out of stock",
            "sold out",
            "item is unavailable",
            "removed from your cart",
            "can't place your order",
            "cannot place your order",
            "payment could not be authorized",
            "sorry, this item is no longer",
        )
    )


def spam_place_order(
    driver: Driver,
    *,
    timeout_seconds: float = 1800.0,
    payment: PaymentInfo | None = None,
) -> tuple[bool, str | None]:
    """Clicker-style Place order until confirm, sold-out, or timeout.

    Mirrors live success: Free Mouse Clicker on Place order after /checkout
    hydrates. Fast clicks (~100–200ms). If the button disappears, F5 /checkout.
    """
    timeout_seconds = max(15.0, float(timeout_seconds))
    deadline = time.time() + timeout_seconds
    clicks = 0
    refreshes = 0
    last_refresh = 0.0
    print(
        f"[ORDER] Place order clicker-style for up to {int(timeout_seconds)}s "
        f"(~8–10 clicks/s when button visible; stop on sold-out)"
    )
    while time.time() < deadline:
        confirmed, order_number = order_confirmation(driver)
        if confirmed:
            print(
                f"[ORDER] confirmed after {clicks} Place order clicks "
                f"({refreshes} refreshes)"
            )
            return True, order_number
        if checkout_order_blocked(driver):
            print(f"[ORDER] blocked/sold-out after {clicks} clicks — stopping")
            return False, None

        now = time.time()
        ready = checkout_ready_for_place_order(driver)
        if (not ready or checkout_appears_stuck(driver)) and (now - last_refresh) > 3.0:
            # Proven path: F5 on /checkout until Place order is back.
            try:
                if "checkout" in (driver.current_url or "").lower():
                    driver.refresh()
                else:
                    driver.get("https://www.target.com/checkout")
            except Exception:
                if not refresh_cart_then_checkout(driver):
                    if cart_is_empty(driver):
                        return False, None
            refreshes += 1
            last_refresh = now
            time.sleep(0.35)
            if payment and payment.is_complete:
                try:
                    fill_payment(driver, payment)
                except Exception:
                    if payment.card_cvv:
                        fill_cvv(driver, payment.card_cvv)
            continue

        if place_order(driver):
            clicks += 1
            if clicks == 1 or clicks % 50 == 0:
                elapsed = int(time.time() - (deadline - timeout_seconds))
                print(
                    f"[ORDER] Place order #{clicks} "
                    f"(~{elapsed}s elapsed, refreshes={refreshes})"
                )
        # Mouse-clicker pace (not ATC Shape mash).
        time.sleep(0.08 + random.uniform(0.02, 0.08))

        if payment and payment.card_cvv and clicks and clicks % 40 == 0:
            try:
                page = _page_lower(driver)
                if "cvv" in page or "security code" in page:
                    fill_cvv(driver, payment.card_cvv)
            except Exception:
                pass

    confirmed, order_number = order_confirmation(driver)
    if confirmed:
        print(f"[ORDER] confirmed after {clicks} Place order clicks")
        return True, order_number
    print(
        f"[ORDER] Place order timed out after {clicks} clicks / {refreshes} refreshes "
        f"({int(timeout_seconds)}s)"
    )
    return False, order_number


def order_confirmation(driver: Driver) -> tuple[bool, str | None]:
    text = _page_lower(driver)
    url = ""
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        pass
    confirmed = any(
        token in text
        for token in (
            "thanks for your order",
            "thank you for your order",
            "order confirmed",
            "your order number",
            "we've received your order",
        )
    ) or ("order-confirmation" in url) or ("/order/" in url and "confirm" in url)
    order_number = None
    try:
        order_number = driver.run_js(
            """
            const m = (document.body.innerText || '').match(/order\\s*(?:number|#)\\s*[:#]?\\s*([A-Z0-9-]{6,})/i);
            return m ? m[1] : null;
            """
        )
    except Exception:
        order_number = None
    return confirmed, order_number


def choose_fulfillment_and_checkout(driver: Driver, config: AppConfig) -> CheckoutResult:
    login_hint = ensure_logged_in_hint(driver)
    go_to_cart(driver)
    driver.sleep(0.2)

    if cart_is_empty(driver):
        return CheckoutResult(
            fulfillment=FulfillmentChoice.UNKNOWN,
            dry_run=config.dry_run,
            placed_order=False,
            message="Cart is empty after add-to-cart (item missing — retry)",
        )

    fulfillment = FulfillmentChoice.UNKNOWN

    # Cheap items often ATC as shipping then hit Target's $35 ship minimum.
    # Prefer the banner CTA / Order Pickup before attempting checkout.
    # Also collapse double-ATC carts (one ship line + one pickup line).
    if cart_line_count(driver) > 1:
        trim_cart_to_max_lines(driver, max_lines=1)

    already_pickup = False
    try:
        already_pickup = bool(
            driver.run_js(
                """
                const checked = document.querySelector('input[value="STORE_PICKUP"]:checked');
                if (checked) return true;
                const text = (document.body && document.body.innerText || '').toLowerCase();
                return /order pickup/.test(text) && /ready within|pick up at/.test(text)
                  && !/only ship with \\$35/.test(text);
                """
            )
        )
    except Exception:
        already_pickup = False

    if already_pickup and not shipping_blocked_by_minimum(driver):
        fulfillment = FulfillmentChoice.PICKUP
        select_preferred_store(driver, config.preferred_store_name)
    elif config.prefer_pickup or shipping_blocked_by_minimum(driver):
        if switch_cart_to_pickup(driver, config.preferred_store_name):
            fulfillment = FulfillmentChoice.PICKUP
            driver.sleep(0.18)
            if cart_line_count(driver) > 1:
                trim_cart_to_max_lines(driver, max_lines=1)
            if cart_is_empty(driver):
                return CheckoutResult(
                    fulfillment=fulfillment,
                    dry_run=config.dry_run,
                    placed_order=False,
                    message="Cart emptied after selecting pickup — item may be unavailable for pickup",
                )
            if shipping_blocked_by_minimum(driver):
                # Still seeing $35 banner usually means a shipping twin remains.
                trim_cart_to_max_lines(driver, max_lines=1)
                switch_cart_to_pickup(driver, config.preferred_store_name)

    if fulfillment != FulfillmentChoice.PICKUP and config.prefer_pickup and pickup_available(driver):
        if select_pickup(driver, config.preferred_store_name):
            fulfillment = FulfillmentChoice.PICKUP
            driver.sleep(0.18)

    if fulfillment != FulfillmentChoice.PICKUP:
        # Do not force shipping when Target says the cart can't ship yet.
        if shipping_blocked_by_minimum(driver):
            if switch_cart_to_pickup(driver, config.preferred_store_name):
                fulfillment = FulfillmentChoice.PICKUP
            else:
                return CheckoutResult(
                    fulfillment=FulfillmentChoice.SHIPPING,
                    dry_run=config.dry_run,
                    placed_order=False,
                    message="Shipping blocked ($35 minimum) and could not switch to pickup",
                )
        elif select_shipping(driver):
            fulfillment = FulfillmentChoice.SHIPPING
            ensure_shipping_address(driver, config.shipping_address)
            driver.sleep(0.18)
            if shipping_blocked_by_minimum(driver):
                if switch_cart_to_pickup(driver, config.preferred_store_name):
                    fulfillment = FulfillmentChoice.PICKUP
                else:
                    return CheckoutResult(
                        fulfillment=FulfillmentChoice.SHIPPING,
                        dry_run=config.dry_run,
                        placed_order=False,
                        message="Shipping blocked by $35 minimum after selecting ship",
                    )
            if cart_is_empty(driver):
                return CheckoutResult(
                    fulfillment=fulfillment,
                    dry_run=config.dry_run,
                    placed_order=False,
                    message="Cart emptied after selecting shipping",
                )
        elif pickup_available(driver) and select_pickup(driver, config.preferred_store_name):
            fulfillment = FulfillmentChoice.PICKUP

    # Always allow the configured auth window — Gmail OTP needs real time.
    # Dry-run / no-place-order still stops before Place order below.
    auth_timeout = config.checkout_auth_timeout_seconds

    if not start_checkout(
        driver,
        wait_auth=True,
        auth_timeout=auth_timeout,
    ):
        if config.dry_run or not config.place_order:
            return CheckoutResult(
                fulfillment=fulfillment,
                dry_run=True,
                placed_order=False,
                message=(
                    "Dry run stop at Target checkout sign-in "
                    "(passkey / emailed code). Sign-in did not complete in time."
                ),
            )
        msg = "Could not start checkout (sign-in step-up timed out or checkout blocked)"
        if login_hint:
            msg = f"{msg}; {login_hint}"
        return CheckoutResult(
            fulfillment=fulfillment,
            dry_run=config.dry_run,
            placed_order=False,
            message=msg,
        )

    # Auth can leave us on /cart — force the real checkout UI before payment.
    if not on_checkout_page(driver):
        if not ensure_reached_checkout(driver):
            # Live success path: go straight to /checkout and F5 until ready.
            if not reload_checkout_until_ready(driver, timeout_seconds=min(120.0, auth_timeout)):
                return CheckoutResult(
                    fulfillment=fulfillment,
                    dry_run=config.dry_run,
                    placed_order=False,
                    message="Authenticated but could not open checkout page",
                )

    # Cart from mobile ATC may take F5s to hydrate on PC checkout.
    if not checkout_ready_for_place_order(driver):
        reload_checkout_until_ready(
            driver,
            timeout_seconds=min(180.0, max(60.0, auth_timeout)),
        )

    if fulfillment == FulfillmentChoice.SHIPPING:
        ensure_shipping_address(driver, config.shipping_address)
    ensure_contact_phone(driver, config.shipping_address.phone)

    if config.dry_run or not config.place_order:
        return CheckoutResult(
            fulfillment=fulfillment,
            dry_run=True,
            placed_order=False,
            message=(
                f"Dry run stop before place order "
                f"(fulfillment={fulfillment.value}, "
                f"ship_to={config.shipping_address.as_single_line()})"
            ),
        )

    if not config.payment.is_complete:
        return CheckoutResult(
            fulfillment=fulfillment,
            dry_run=False,
            placed_order=False,
            message=(
                "place_order enabled but payment incomplete in .env "
                "(need CARD_CVV; and full CARD_* if USE_SAVED_CARD=false)"
            ),
        )

    if not fill_payment(driver, config.payment):
        return CheckoutResult(
            fulfillment=fulfillment,
            dry_run=False,
            placed_order=False,
            message="Could not fill payment / CVV on checkout page",
        )

    # Clicker-style Place order (mobile ATC → PC /checkout F5 → mash Place order).
    spam_timeout = float(getattr(config, "place_order_spam_seconds", 0) or 0) or 1800.0
    confirmed, order_number = spam_place_order(
        driver, timeout_seconds=spam_timeout, payment=config.payment
    )
    if confirmed:
        return CheckoutResult(
            fulfillment=fulfillment,
            dry_run=False,
            placed_order=True,
            message=f"Order confirmed{f' #{order_number}' if order_number else ''}",
            order_number=order_number,
        )
    if order_number:
        return CheckoutResult(
            fulfillment=fulfillment,
            dry_run=False,
            placed_order=True,
            message="Place order finished — possible order (check email/Target)",
            order_number=order_number,
        )
    return CheckoutResult(
        fulfillment=fulfillment,
        dry_run=False,
        placed_order=False,
        message="Place order timed out / blocked — no confirmation",
        order_number=None,
    )
