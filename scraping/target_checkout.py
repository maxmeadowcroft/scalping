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

import sys
import time
from dataclasses import dataclass
from enum import Enum

from botasaurus.browser import Driver

from scraping.config import (
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


def _click_text(driver: Driver, text: str, wait: float = 2.0) -> bool:
    try:
        el = driver.get_element_containing_text(text, wait=wait)
        if el is None:
            return False
        el.click()
        driver.sleep(0.8)
        return True
    except Exception:
        return False


def _click_selector(driver: Driver, selector: str, wait: float = 2.0) -> bool:
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
        driver.sleep(0.8)
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
    if _click_text(driver, "View cart & check out", wait=2):
        return
    if _click_text(driver, "View cart and check out", wait=1):
        return
    if _click_selector(driver, 'a[href="/cart"]', wait=1):
        return
    if _click_selector(driver, '[data-test="cartItem-checkoutButton"]', wait=1):
        return
    driver.get("https://www.target.com/cart")
    driver.sleep(2)


def clear_cart(driver: Driver, *, max_rounds: int = 12) -> None:
    """Remove every line item so each monitored product checks out alone.

    Only uses Target's cart delete control — never broad "Remove" text clicks,
    which can hit unrelated UI and race with a fresh add-to-cart.
    """
    driver.get("https://www.target.com/cart")
    driver.sleep(2.0)
    for _ in range(max_rounds):
        if cart_is_empty(driver):
            return
        # Click every visible delete control in one pass (mixed ship+pickup carts
        # often have multiple cartItem-deleteBtn nodes).
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
                or _click_selector(driver, '[data-test="cartItem-deleteBtn"]', wait=1.2)
            ):
                break
        driver.sleep(1.4)
    driver.sleep(1.0)


def cart_is_empty(driver: Driver) -> bool:
    text = _page_lower(driver)
    if "your cart is empty" in text or "cart is empty" in text:
        return True
    if driver.select('[data-test="cartItem"]', 0.5) is not None:
        return False
    if driver.select('[data-test="cartItem-deleteBtn"]', 0.5) is not None:
        return False
    if driver.select('[data-test="checkout-button"]', 0.5) is not None:
        return False
    return False


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
    driver.sleep(1.0)
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
                or _click_selector(driver, '[data-test="cartItem-deleteBtn"]', wait=1.0)
            ):
                break
        driver.sleep(1.3)
    count = cart_line_count(driver)
    if count > max_lines:
        print(f"[CART] warning: still {count} lines after trim (wanted ≤{max_lines})")
    elif rounds:
        print(f"[CART] trimmed to {count} line(s)")
    return count


def cart_has_items(driver: Driver) -> bool:
    go_to_cart(driver)
    driver.sleep(1.5)
    if not cart_is_empty(driver):
        return True
    # Target sometimes lags after ATC — one soft refresh before declaring empty.
    driver.sleep(2.0)
    driver.get("https://www.target.com/cart")
    driver.sleep(2.0)
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
        if driver.select(selector, 0.8) is not None:
            return True
    try:
        return driver.get_element_containing_text("Order Pickup", wait=1) is not None
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
        if _click_text(driver, label, wait=0.8):
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
        if _type_first(driver, [selector], preferred_store_name, wait=1):
            driver.sleep(1.2)
            break

    if _click_text(driver, preferred_store_name, wait=2):
        driver.sleep(1.0)
        for label in ("Set as shopping store", "Shop this store", "Select", "Confirm"):
            _click_text(driver, label, wait=0.5)
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
        if _click_text(driver, label, wait=1.2):
            driver.sleep(2.0)
            print(f"[FULFILLMENT] clicked {label!r}")
            break

    ok = select_pickup(driver, preferred_store_name)
    if ok:
        driver.sleep(1.5)
    # Confirm we are no longer stuck on the $35 shipping gate
    if shipping_blocked_by_minimum(driver) and "pickup" not in _page_lower(driver).split("order pickup", 1)[0][-80:]:
        # One more pass on the banner CTA
        _click_text(driver, "Change all to pickup", wait=1.0)
        driver.sleep(1.5)
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
        if _click_selector(driver, selector, wait=1) or _js_click(driver, selector):
            driver.sleep(1.5)
            clicked = True
            break
    if not clicked and _click_text(driver, "Order Pickup", wait=2):
        driver.sleep(1.5)
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
        if _click_selector(driver, selector, wait=1) or _js_click(driver, selector):
            driver.sleep(1.5)
            return True
    if _click_text(driver, "Shipping", wait=2):
        driver.sleep(1.5)
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
            driver.sleep(1.0)
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
    url = ""
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        pass
    if "/cart" in url:
        return True
    return driver.select('[data-test="checkout-button"]', 0.4) is not None


def on_checkout_page(driver: Driver) -> bool:
    """True only when the real checkout review UI is present (not just /checkout URL)."""
    if step_up_auth_visible(driver) or _otp_entry_visible(driver):
        return False

    # Cart still showing Check out means we are NOT on the payment review page.
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""
    if "/cart" in url and driver.select('[data-test="checkout-button"]', 0.3) is not None:
        return False
    if driver.select('[data-test="checkout-button"]', 0.3) is not None and "/checkout" not in url:
        return False

    has_place = driver.select('[data-test="placeOrderButton"]', 0.5) is not None
    has_cvv = (
        driver.select('input[autocomplete="cc-csc"]', 0.3) is not None
        or driver.select('input[name*="cvv" i]', 0.3) is not None
        or driver.select('input[data-test*="cvv" i]', 0.3) is not None
        or driver.select('input[aria-label*="security code" i]', 0.3) is not None
    )
    if has_place or has_cvv:
        return True

    # Soft signal: /checkout URL with Place order copy (not the cart Check out button).
    if "/checkout" in url:
        text = _page_lower(driver)
        if "place order" in text and "check out" not in text[:500]:
            return True
        if "payment" in text and ("cvv" in text or "security code" in text or "ending in" in text):
            return True
    return False


def click_checkout_button(driver: Driver) -> bool:
    """Click the cart Check out button (JS click — overlays often intercept)."""
    disable_webauthn_prompts(driver)
    for selector in (
        '[data-test="checkout-button"]',
        '[data-test="checkout-button-bottom"]',
        'button[data-test*="checkout" i]',
    ):
        if _js_click(driver, selector) or _click_selector(driver, selector, wait=1.0):
            driver.sleep(2.0)
            disable_webauthn_prompts(driver)
            return True
    if _click_text(driver, "Check out", wait=2) or _click_text(driver, "Checkout", wait=1):
        driver.sleep(2.0)
        disable_webauthn_prompts(driver)
        return True
    return False


def ensure_reached_checkout(driver: Driver, *, attempts: int = 4) -> bool:
    """After auth, force navigation onto the real checkout page if still on cart."""
    for i in range(1, attempts + 1):
        if on_checkout_page(driver):
            return True

        if step_up_auth_visible(driver):
            return False

        print(f"[CHECKOUT] not on checkout yet (attempt {i}/{attempts}) — clicking Check out")
        if on_cart_page(driver) or "cart" in (driver.current_url or ""):
            click_checkout_button(driver)
        else:
            # Lost — go cart then checkout
            driver.get("https://www.target.com/cart")
            driver.sleep(2.0)
            click_checkout_button(driver)

        # Hard navigation fallback
        if not on_checkout_page(driver) and not step_up_auth_visible(driver):
            driver.get("https://www.target.com/checkout")
            driver.sleep(2.5)

        if on_checkout_page(driver):
            print("[CHECKOUT] reached checkout page")
            return True

        if step_up_auth_visible(driver):
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
        driver.sleep(1.0)
        return str(matched)
    for label in labels:
        if _click_text(driver, label, wait=0.6):
            return label
    return None


def _target_auth_error_visible(driver: Driver) -> bool:
    text = _page_lower(driver)
    return "something went wrong on our end" in text or "try again later or send a report" in text


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
    return _otp_copy_visible(driver) or _find_otp_input(driver) is not None


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
    """Click a Target NDS role=button cell (e.g. #otp) — click only, never #passkey."""
    if "passkey" in el_selector.lower():
        print(f"[AUTH] refused to activate passkey selector {el_selector!r}")
        return False
    try:
        result = driver.run_js(
            f"""
            const el = document.querySelector({el_selector!r});
            if (!el) return {{ok: false, reason: 'missing'}};
            if ((el.id || '').toLowerCase() === 'passkey') return {{ok: false, reason: 'passkey-blocked'}};
            if (el.getAttribute('aria-disabled') === 'true') return {{ok: false, reason: 'disabled'}};
            el.scrollIntoView({{block: 'center', inline: 'center'}});
            const r = el.getBoundingClientRect();
            const x = r.left + r.width / 2;
            const y = r.top + r.height / 2;
            const base = {{bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0}};
            for (const type of ['pointerover','mouseover','pointerdown','mousedown','pointerup','mouseup','click']) {{
              let ev;
              if (type.indexOf('pointer') === 0) {{
                ev = new PointerEvent(type, Object.assign({{}}, base, {{pointerId: 1, pointerType: 'mouse', isPrimary: true}}));
              }} else {{
                ev = new MouseEvent(type, base);
              }}
              el.dispatchEvent(ev);
            }}
            try {{ el.click(); }} catch (e) {{}}
            return {{
              ok: true,
              id: el.id || '',
              role: el.getAttribute('role') || '',
              text: ((el.innerText || '') + '').replace(/\\s+/g, ' ').trim().slice(0, 80),
            }};
            """
        )
    except Exception as exc:
        print(f"[AUTH] activate {el_selector!r} failed: {exc}")
        return False
    if isinstance(result, dict) and result.get("ok"):
        print(f"[AUTH] activated {el_selector!r} → {result.get('text')!r}")
        driver.sleep(1.2)
        return True
    return False


def click_get_a_code_button(driver: Driver) -> bool:
    """Click Target's step-up 'Get a code' control.

    Live DOM (ModalDrawer sign-in sheet):
      <div id="otp" role="button" class="... styles_authMethodCell__tLZWs" tabindex="0">
        <span class="styles_ndsCellPrimaryText__...">Get a code</span>
      </div>
    Passkey sibling is #passkey — never click it (opens OS WebAuthn sheet).
    """
    disable_webauthn_prompts(driver)
    # Ensure #passkey is not focused / not clickable via text fallbacks.
    try:
        driver.run_js(
            """
            const pk = document.querySelector('#passkey');
            if (pk) {
              pk.setAttribute('aria-disabled', 'true');
              pk.style.pointerEvents = 'none';
            }
            return !!pk;
            """
        )
    except Exception:
        pass

    for selector in (
        '#otp[role="button"]',
        "#otp",
        '[id="otp"]',
        'div.styles_authMethodCell__tLZWs#otp',
        '[role="dialog"] #otp',
        '.ReactModal__Content #otp',
        '.ModalDrawer #otp',
    ):
        if _activate_role_button(driver, selector):
            return True

    # Fallback: find role=button whose primary text is exactly Get a code.
    try:
        found = driver.run_js(
            """
            const nodes = Array.from(document.querySelectorAll('[role="button"], button, a'));
            for (const el of nodes) {
              if ((el.id || '').toLowerCase() === 'passkey') continue;
              const primary = el.querySelector('.styles_ndsCellPrimaryText__7IkfY, [class*="PrimaryText"]');
              const text = ((primary && primary.innerText) || el.innerText || '')
                .replace(/\\s+/g, ' ').trim().toLowerCase();
              if (text === 'get a code' || (text.startsWith('get a code') && !text.includes('passkey'))) {
                if ((el.id || '').toLowerCase() === 'otp' || text === 'get a code') {
                  el.scrollIntoView({block: 'center'});
                  el.click();
                  return el.id || text;
                }
              }
            }
            return null;
            """
        )
    except Exception:
        found = None
    if found:
        print(f"[AUTH] clicked Get a code via role=button scan → {found!r}")
        driver.sleep(1.2)
        return True

    if _click_text(driver, "Get a code", wait=1.5):
        print("[AUTH] clicked Get a code via text fallback")
        return True
    return False


def request_email_code(driver: Driver, *, force_new: bool = False) -> bool:
    """Click Get a code / Resend on the Target step-up modal.

    Success means the code-entry UI appeared (or resend was clicked while
    already there). Target sometimes shows "Something went wrong on our end"
    on the chooser — we still click Get a code and retry until entry shows.
    """
    if _otp_entry_visible(driver) and not force_new:
        print("[AUTH] OTP entry already visible")
        return True

    if _target_auth_error_visible(driver):
        print("[AUTH] Target error banner visible — retrying Get a code")

    for attempt in range(1, 4):
        if _otp_entry_visible(driver) and force_new:
            # Already on entry screen — look for Resend / Didn't get a code?
            clicked = _click_visible_button_text(
                driver,
                ("resend", "send a new code", "didn't get a code?", "get a new code"),
            )
            if clicked:
                print(f"[AUTH] clicked resend: {clicked!r} (attempt {attempt})")
                driver.sleep(2.0)
                return True
            # Fall through to #otp in case Target bounced back to chooser.

        clicked = click_get_a_code_button(driver)
        if not clicked:
            print(f"[AUTH] Get a code control not found (attempt {attempt})")
        driver.sleep(2.0)

        if _otp_entry_visible(driver):
            print("[AUTH] code entry screen is up")
            return True
        if force_new and clicked:
            return True
        if _target_auth_error_visible(driver):
            print("[AUTH] still seeing Target 'something went wrong' — waiting then retry")
            driver.sleep(2.5)
            continue
        if _auth_method_chooser_visible(driver):
            print("[AUTH] still on passkey/Get a code chooser — retrying")
            driver.sleep(1.5)
            continue
        break

    return _otp_entry_visible(driver)

def _find_otp_input(driver: Driver):
    selectors = [
        'input[placeholder*="Enter your code" i]',
        'input[placeholder*="code" i]',
        'input[autocomplete="one-time-code"]',
        'input[name*="otp" i]',
        'input[name*="code" i]',
        'input[data-test*="otp" i]',
        'input[data-test*="code" i]',
        'input[inputmode="numeric"]',
        'input[type="tel"]',
        'input[type="text"]',
    ]
    for selector in selectors:
        el = driver.select(selector, 0.6)
        if el is None:
            continue
        try:
            placeholder = (el.get_attribute("placeholder") or "").lower()
            name = (el.get_attribute("name") or "").lower()
            autocomplete = (el.get_attribute("autocomplete") or "").lower()
            aria = (el.get_attribute("aria-label") or "").lower()
            blob = f"{placeholder} {name} {autocomplete} {aria}"
            if "search" in blob or "email" in name or "password" in blob:
                continue
            if selector in {'input[type="text"]', 'input[type="tel"]', 'input[inputmode="numeric"]'}:
                if not any(k in blob for k in ("code", "otp", "passcode", "one-time")):
                    # Only accept bare tel/text inputs when OTP copy is on-screen.
                    if not _otp_copy_visible(driver) and "one-time-code" not in autocomplete:
                        continue
        except Exception:
            pass
        return selector
    return None


def submit_otp(driver: Driver, code: str) -> bool:
    selector = _find_otp_input(driver)
    if not selector:
        request_email_code(driver)
        selector = _find_otp_input(driver)
        if not selector:
            return False
    try:
        driver.clear(selector)
    except Exception:
        pass
    driver.type(selector, code)
    driver.sleep(0.4)
    if _click_visible_button_text(driver, ("verify", "continue", "submit")):
        driver.sleep(2.5)
        return not step_up_auth_visible(driver) and _find_otp_input(driver) is None
    if _click_text(driver, "Verify", wait=1.5):
        driver.sleep(2.5)
        return not step_up_auth_visible(driver)
    return False


def wait_for_checkout_auth(driver: Driver, *, timeout_seconds: float) -> bool:
    """Wait until step-up auth clears and the real checkout page is open.

    After OTP succeeds Target often leaves you on /cart — only then re-click
    Check out. Do not click Check out while waiting for an emailed code.
    """
    from datetime import datetime, timedelta, timezone

    from scraping.gmail_otp import fetch_latest_target_otp, load_gmail_credentials

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
    next_resend = time.time() + 35.0
    otp_accepted = False
    auth_missing_streak = 0

    while time.time() < deadline:
        if on_checkout_page(driver):
            clear_target_otp()
            print("[AUTH] On checkout page")
            return True

        auth_up = step_up_auth_visible(driver) or _otp_entry_visible(driver)

        otp = read_target_otp()
        if gmail.is_configured and time.time() >= next_gmail_poll:
            next_gmail_poll = time.time() + 3.0
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
            used_codes.add(otp)
            if submit_otp(driver, otp):
                clear_target_otp()
                otp_accepted = True
                print("[AUTH] OTP accepted — opening checkout")
                driver.sleep(2.0)
                if ensure_reached_checkout(driver):
                    return True
                if step_up_auth_visible(driver) or _otp_entry_visible(driver):
                    print("[AUTH] Check out re-triggered sign-in — requesting new code")
                    code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
                    request_email_code(driver, force_new=True)
                    next_resend = time.time() + 35.0
                    otp_accepted = False
                continue
            print("[AUTH] OTP rejected")
            code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            request_email_code(driver, force_new=True)
            next_resend = time.time() + 35.0
            continue

        if auth_up and time.time() >= next_resend:
            if _auth_method_chooser_visible(driver) or _target_auth_error_visible(driver):
                print("[AUTH] chooser/error still up — clicking Get a code again")
                code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
                request_email_code(driver, force_new=False)
            else:
                print("[AUTH] no fresh OTP yet — resending code")
                code_requested_at = datetime.now(timezone.utc) - timedelta(seconds=5)
                if not request_email_code(driver, force_new=True):
                    request_email_code(driver, force_new=False)
            next_resend = time.time() + 25.0

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
                request_email_code(driver, force_new=True)
                next_resend = time.time() + 35.0
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
                next_resend = time.time() + 35.0

        time.sleep(1.5)

    if otp_accepted:
        return ensure_reached_checkout(driver)
    print("[AUTH] timed out waiting for step-up sign-in / OTP")
    return on_checkout_page(driver)



def start_checkout(driver: Driver, *, wait_auth: bool = True, auth_timeout: float = 300.0) -> bool:
    clicked = click_checkout_button(driver)

    # Direct navigation fallback
    if not clicked and not on_checkout_page(driver) and not step_up_auth_visible(driver):
        driver.get("https://www.target.com/checkout")
        driver.sleep(2.5)
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
        if _click_text(driver, label, wait=0.6):
            driver.sleep(0.8)
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
                    driver.sleep(0.6)
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
    if _type_first(driver, selectors, cvv, wait=1.5):
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
    for _ in range(6):
        if (
            driver.select('[data-test="placeOrderButton"]', 0.4) is not None
            or "cvv" in _page_lower(driver)
            or "security code" in _page_lower(driver)
            or "ending in" in _page_lower(driver)
        ):
            break
        driver.sleep(1.0)

    # Saved-card path: Target usually only asks for CVV.
    if payment.use_saved_card:
        for attempt in range(1, 4):
            if fill_cvv(driver, payment.card_cvv):
                print(f"[PAYMENT] CVV filled (attempt {attempt})")
                return True
            driver.sleep(1.0)
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

    # Combined expiry MM/YY field
    if payment.card_expiration_date and _type_first(
        driver,
        [
            'input[autocomplete="cc-exp"]',
            'input[name*="expir" i]',
            'input[aria-label*="expir" i]',
            'input[placeholder*="MM" i]',
        ],
        payment.card_expiration_date,
        wait=1.0,
    ):
        filled += 1
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
        if year and _type_first(
            driver,
            [
                'select[name*="expir" i][name*="year" i]',
                'select[autocomplete="cc-exp-year"]',
                'input[name*="expYear" i]',
            ],
            year,
            wait=0.8,
        ):
            filled += 1

    if fill_cvv(driver, payment.card_cvv):
        filled += 1

    fill_billing_address(driver, payment)
    for label in ("Save & continue", "Save and continue", "Save", "Use this card"):
        _click_text(driver, label, wait=0.5)
    return filled >= 3


def place_order(driver: Driver) -> bool:
    for selector in (
        '[data-test="placeOrderButton"]',
        'button[data-test*="placeOrder" i]',
        'button[data-test*="place-order" i]',
    ):
        if _js_click(driver, selector) or _click_selector(driver, selector, wait=2):
            driver.sleep(3.0)
            return True
    return _click_text(driver, "Place order", wait=2) or _click_text(
        driver, "Place my order", wait=1
    )


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
    driver.sleep(1.5)

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

    if config.prefer_pickup or shipping_blocked_by_minimum(driver):
        if switch_cart_to_pickup(driver, config.preferred_store_name):
            fulfillment = FulfillmentChoice.PICKUP
            driver.sleep(1.0)
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
            driver.sleep(1.0)

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
            driver.sleep(1.0)
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

    # Dry-runs should not block for minutes on Face ID / email OTP.
    auth_timeout = config.checkout_auth_timeout_seconds
    if config.dry_run or not config.place_order:
        auth_timeout = min(auth_timeout, 8.0)

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
                    "(passkey / emailed code). For real buys use --place-order "
                    "and complete Face ID or set TARGET_OTP while waiting."
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
            return CheckoutResult(
                fulfillment=fulfillment,
                dry_run=config.dry_run,
                placed_order=False,
                message="Authenticated but could not open checkout page",
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

    if not place_order(driver):
        return CheckoutResult(
            fulfillment=fulfillment,
            dry_run=False,
            placed_order=False,
            message="Failed to click Place order",
        )

    confirmed, order_number = order_confirmation(driver)
    if confirmed:
        return CheckoutResult(
            fulfillment=fulfillment,
            dry_run=False,
            placed_order=True,
            message=f"Order confirmed{f' #{order_number}' if order_number else ''}",
            order_number=order_number,
        )

    return CheckoutResult(
        fulfillment=fulfillment,
        dry_run=False,
        placed_order=True,
        message="Place order clicked (confirmation page not detected yet — check email/Target orders)",
        order_number=order_number,
    )
