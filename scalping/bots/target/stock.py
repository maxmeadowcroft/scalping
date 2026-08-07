"""Target product page stock detection and add-to-cart helpers.

Modern Target PDPs use fulfillment cells (Pickup / Delivery / Shipping). Selecting
a cell reveals an Add to cart button (e.g. data-test=shippingButton). Out-of-stock
buy boxes show "Out of stock" inside @web/AddToCart/FulfillmentSection without
fulfillment cells.

IMPORTANT: Related-product carousels also render "Add to cart". Stock checks MUST
stay scoped to the buy-box / FulfillmentSection or they false-positive on OOS drops.
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
    r"\bnot available\b",
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

# Buy-box ATC only — never match related-product carousels.
BUYBOX_ATC_SELECTORS = [
    '[data-test="@web/AddToCart/FulfillmentSection"] [data-test="shippingButton"]',
    '[data-test="@web/AddToCart/FulfillmentSection"] [data-test="shipItButton"]',
    '[data-test="@web/AddToCart/FulfillmentSection"] [data-test="orderPickupButton"]',
    '[data-test="@web/AddToCart/FulfillmentSection"] [data-test="fulfillment-add-to-cart"]',
    '[data-test="@web/AddToCart/FulfillmentSection"] button[data-test*="addToCart" i]',
    '[data-test="@web/AddToCart/FulfillmentSection"] button[data-test*="add-to-cart" i]',
]

OOS_SELECTORS = [
    '[data-test="@web/AddToCart/FulfillmentSection"] [data-test="outOfStockMessage"]',
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
    """Classify from buy-box text only (not full-page related products)."""
    excerpt = " ".join(text.split())[:500]
    head = text[:4000].lower()

    if page_indicates_out_of_stock(head):
        return StockCheckResult(StockStatus.OUT_OF_STOCK, "oos_text", excerpt)
    # Strong buyable cues only — avoid "only at Target" / policy blurbs.
    if any(
        needle in head
        for needle in (
            "add to cart",
            "ship it",
            "order pickup",
            "deliver it",
            " in cart",
            "ready within",
            "arrives by",
        )
    ):
        # Still reject if OOS wording appears alongside (mixed section).
        if page_indicates_out_of_stock(head):
            return StockCheckResult(StockStatus.OUT_OF_STOCK, "oos_text", excerpt)
        return StockCheckResult(StockStatus.IN_STOCK, "atc_text", excerpt)
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


def _buybox_stock_probe(driver: Driver) -> dict:
    """Single JS pass over the buy box — ignores related-product carousels."""
    try:
        result = driver.run_js(
            """
            const root = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]');
            const text = root ? ((root.innerText || '') + '') : '';
            const low = text.toLowerCase();
            const oosSel = document.querySelector(
              '[data-test="@web/AddToCart/FulfillmentSection"] [data-test="outOfStockMessage"],'
              + '[data-test="outOfStockMessage"], [data-test="shipItUnavailable"]'
            );
            const isDisabled = (el) => {
              if (!el) return true;
              if (el.disabled) return true;
              if ((el.getAttribute('aria-disabled') || '') === 'true') return true;
              if ((el.getAttribute('aria-hidden') || '') === 'true') return true;
              const st = window.getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0)
                return true;
              const r = el.getBoundingClientRect();
              if (r.width < 8 || r.height < 8) return true;
              return false;
            };
            const atcSels = [
              '[data-test="shippingButton"]',
              '[data-test="shipItButton"]',
              '[data-test="orderPickupButton"]',
              '[data-test="fulfillment-add-to-cart"]',
              'button[data-test*="addToCart" i]',
              'button[data-test*="add-to-cart" i]',
            ];
            let enabledAtc = null;
            let disabledAtc = null;
            if (root) {
              for (const s of atcSels) {
                const el = root.querySelector(s);
                if (!el) continue;
                if (isDisabled(el)) { disabledAtc = disabledAtc || s; continue; }
                enabledAtc = s; break;
              }
            }
            const cellSels = [
              '[data-test="fulfillment-cell-pickup"]',
              '[data-test="fulfillment-cell-shipping"]',
              '[data-test="fulfillment-cell-delivery"]',
            ];
            let enabledCell = null;
            let anyCell = null;
            if (root) {
              for (const s of cellSels) {
                const el = root.querySelector(s);
                if (!el) continue;
                anyCell = anyCell || s;
                // Cells marked unavailable / disabled are not buyable.
                const blob = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
                if (blob.includes('unavailable') || blob.includes('out of stock') || isDisabled(el))
                  continue;
                enabledCell = s; break;
              }
            }
            const oosText = (
              low.includes('out of stock')
              || low.includes('sold out')
              || low.includes('currently unavailable')
              || !!oosSel
            );
            const buyableText = (
              /\\d+\\s+in cart/.test(low)
              || low.includes('ready within')
              || low.includes('arrives by')
              || low.includes('as soon as')
            );
            return {
              hasRoot: !!root,
              text: text.slice(0, 400),
              oosText,
              buyableText,
              enabledAtc,
              disabledAtc,
              enabledCell,
              anyCell,
            };
            """
        )
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _wait_for_pdp(driver: Driver, wait: float = 0.45) -> None:
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
        time.sleep(min(0.12, wait))


def _wait_for_buybox_signal(driver: Driver, *, timeout: float = 0.55) -> dict:
    """Poll buy box until OOS / enabled ATC / enabled cell appears."""
    elapsed = 0.0
    last: dict = {}
    while elapsed < timeout:
        last = _buybox_stock_probe(driver)
        if last.get("oosText") or last.get("enabledAtc") or last.get("buyableText"):
            return last
        # Enabled fulfillment cell is a weaker signal — keep waiting a beat for OOS text.
        if last.get("enabledCell") and elapsed >= 0.18:
            return last
        if last.get("hasRoot") and last.get("text") and elapsed >= 0.35:
            return last
        time.sleep(0.03)
        elapsed += 0.03
    return last


def open_product(driver: Driver, item: ItemConfig, *, force_navigate: bool = True) -> None:
    """Navigate to the PDP (or reload if already there)."""
    ensure_mobile_viewport(driver)
    target = item.normalized_url
    already_there = False
    try:
        current = driver.current_url or ""
        already_there = item.tcin is not None and item.tcin in current and "/p/" in current
    except Exception:
        already_there = False

    if force_navigate or not already_there:
        driver.get(target)
        _wait_for_pdp(driver, wait=0.4)
    else:
        try:
            driver.reload()
        except Exception:
            driver.get(target)
        # Reloads should resolve faster — don't sit on a long wait_for.
        _wait_for_pdp(driver, wait=0.25)

    # Target sometimes restores cart from the profile; ensure we stayed on PDP.
    try:
        current = driver.current_url or ""
        if item.tcin and item.tcin not in current:
            driver.get(target)
            _wait_for_pdp(driver, wait=0.35)
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

    probe = _wait_for_buybox_signal(driver)
    excerpt = str(probe.get("text") or "")[:300]

    if probe.get("oosText"):
        return StockCheckResult(StockStatus.OUT_OF_STOCK, "buybox:oos", excerpt)

    # Enabled ATC inside the buy box is the only hard IN_STOCK signal.
    if probe.get("enabledAtc"):
        return StockCheckResult(
            StockStatus.IN_STOCK,
            f"buybox:atc:{probe['enabledAtc']}",
            excerpt,
        )

    if probe.get("buyableText"):
        return StockCheckResult(StockStatus.IN_STOCK, "buybox:buyable_text", excerpt)

    # Fulfillment cells alone are NOT enough — OOS pages can still render them
    # briefly, and related rails must never count. Require enabled cell AND no OOS.
    if probe.get("enabledCell") and probe.get("hasRoot") and not probe.get("oosText"):
        # One more OOS selector check before trusting the cell.
        oos_el, oos_sel = _first_present(driver, OOS_SELECTORS, wait=0)
        if oos_el is not None:
            return StockCheckResult(StockStatus.OUT_OF_STOCK, f"selector:{oos_sel}", excerpt)
        # Prefer waiting for ATC after cell select rather than declaring stock.
        # Cells without an enabled ATC → UNKNOWN (keep polling).
        return StockCheckResult(
            StockStatus.UNKNOWN,
            f"buybox:cell_without_atc:{probe['enabledCell']}",
            excerpt,
        )

    oos_el, oos_sel = _first_present(driver, OOS_SELECTORS, wait=0)
    if oos_el is not None:
        return StockCheckResult(StockStatus.OUT_OF_STOCK, f"selector:{oos_sel}", excerpt)

    # Scoped buy-box ATC selectors only (never page-wide — related products).
    atc_el, atc_sel = _first_present(driver, BUYBOX_ATC_SELECTORS, wait=0)
    if atc_el is not None:
        # Verify enabled via JS
        enabled = driver.run_js(
            f"""
            const el = document.querySelector({atc_sel!r});
            if (!el) return false;
            if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            return true;
            """
        )
        if enabled:
            return StockCheckResult(StockStatus.IN_STOCK, f"selector:{atc_sel}", excerpt)
        return StockCheckResult(StockStatus.OUT_OF_STOCK, f"disabled_atc:{atc_sel}", excerpt)

    # Fall back to buy-box text only — never full page (related "Add to cart").
    section = excerpt or _fulfillment_section_text(driver)
    if section.strip():
        return classify_stock_from_text(section)

    return StockCheckResult(StockStatus.UNKNOWN, "no_buybox_signal", excerpt)


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
    # Prefer buy-box ATC so we never click a related-product "Add to cart".
    ordered = BUYBOX_ATC_SELECTORS + SHIP_ATC_SELECTORS + PICKUP_ATC_SELECTORS
    _, selector = _first_present(driver, ordered, wait=0)
    if selector:
        try:
            # Human-ish jittered JS click when possible; skip disabled buttons.
            try:
                clicked = driver.run_js(
                    f"""
                    const el = document.querySelector({selector!r});
                    if (!el) return false;
                    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) return false;
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
                if clicked:
                    return True
            except Exception:
                driver.click(selector)
                return True
        except Exception:
            pass

    # Last resort: text click, but only inside the fulfillment section.
    try:
        hit = driver.run_js(
            """
            const root = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]') || document;
            const labels = ['Add to cart', 'Ship it', 'Order Pickup', 'Deliver it'];
            const nodes = Array.from(root.querySelectorAll('button, a[role="button"], [role="button"]'));
            for (const want of labels) {
              const w = want.toLowerCase();
              for (const n of nodes) {
                const t = ((n.innerText || n.value || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
                if (t !== w && !t.includes(w)) continue;
                const test = (n.getAttribute('data-test') || '').toLowerCase();
                if (test.includes('chooseoptions')) continue;
                if (n.disabled || n.getAttribute('aria-disabled') === 'true') continue;
                n.click();
                return t;
              }
            }
            return null;
            """
        )
        if hit:
            return True
    except Exception:
        pass
    return False


def api_add_to_cart(
    driver: Driver,
    item: ItemConfig,
    *,
    prefer_pickup: bool = False,
    quantity: int | None = None,
    variant: str = "mobile_web",
) -> dict:
    """POST carts.target.com cart_items via the live browser session.

    Delegates to ``scalping.bots.target.api`` (multi-variant payloads).
    Returns {ok, status, error, variant, ...}.
    """
    del prefer_pickup  # reserved for future pickup-specific cart payloads
    from scalping.bots.target.api import cart_api_add

    qty = int(quantity or item.max_quantity or 1)
    result = cart_api_add(
        driver, tcin=item.tcin or "", quantity=qty, variant=variant
    )
    return {
        "ok": result.ok,
        "status": result.status,
        "error": result.error,
        "variant": result.variant,
        "data": result.data,
    }


def ensure_mobile_viewport(driver: Driver) -> None:
    """Force a phone viewport so Target serves mobile PDP / buy box."""
    try:
        driver.run_js(
            """
            if (!window.__scalpingMobileViewport) {
              window.__scalpingMobileViewport = true;
              try {
                Object.defineProperty(navigator, 'userAgentData', {
                  get: () => ({ mobile: true, platform: 'iOS' }),
                  configurable: true,
                });
              } catch (e) {}
            }
            return true;
            """
        )
    except Exception:
        pass
    try:
        # Botasaurus / CDP viewport when available
        if hasattr(driver, "run_cdp_command"):
            driver.run_cdp_command(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 390,
                    "height": 844,
                    "deviceScaleFactor": 3,
                    "mobile": True,
                },
            )
    except Exception:
        pass


def cart_looks_updated(driver: Driver) -> bool:
    """ATC success — require real cart signals, not related-product copy."""
    if _already_in_cart(driver):
        return True
    try:
        return bool(
            driver.run_js(
                """
                // Strong signals only — avoid related rails / marketing copy.
                if (document.querySelector('[data-test="cartItem-checkoutButton"]')) return true;
                if (document.querySelector('[data-test="checkout-button"]')) return true;
                const el = document.querySelector('[data-test="@web/CartLinkQuantity"]');
                if (el) {
                  const n = parseInt((el.textContent || '').replace(/[^0-9]/g, ''), 10);
                  if (Number.isFinite(n) && n > 0) return true;
                }
                // Fulfillment buy-box "N in cart" only.
                const box = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]');
                if (box) {
                  const t = (box.innerText || '').toLowerCase();
                  const m = t.match(/(\\d+)\\s+in cart/);
                  if (m && parseInt(m[1], 10) >= 1) return true;
                }
                return false;
                """
            )
        )
    except Exception:
        return False


def _buybox_debug(driver: Driver) -> str:
    """One-line buy-box state for ATC UI debugging."""
    try:
        probe = _buybox_stock_probe(driver)
        return (
            f"root={probe.get('hasRoot')} atc={probe.get('enabledAtc')} "
            f"cell={probe.get('enabledCell')} oos={probe.get('oosText')} "
            f"text={str(probe.get('text') or '')[:80]!r}"
        )
    except Exception as exc:
        return f"probe_err={exc}"


def _install_cart_fetch_hook(driver: Driver) -> None:
    """Capture cart_items status/body from Target's own fetch (UI path)."""
    try:
        driver.run_js(
            """
            if (!window.__scalpingCartHook) {
              window.__scalpingCartHook = true;
              window.__scalpingCartCalls = [];
              const orig = window.fetch.bind(window);
              window.fetch = async function(input, init) {
                const url = (typeof input === 'string') ? input : (input && input.url) || '';
                const res = await orig(input, init);
                if (url.includes('/cart_items')) {
                  let resp = '';
                  try { resp = await res.clone().text(); } catch (e) {}
                  window.__scalpingCartCalls.push({
                    status: res.status,
                    ok: res.status === 200 || res.status === 201,
                    body: init && init.body ? String(init.body).slice(0, 500) : null,
                    resp: resp.slice(0, 500),
                    t: Date.now(),
                  });
                }
                return res;
              };
            }
            return true;
            """
        )
    except Exception:
        pass


def _last_cart_call(driver: Driver) -> dict | None:
    try:
        calls = driver.run_js("return window.__scalpingCartCalls || []")
        if isinstance(calls, list) and calls:
            return calls[-1] if isinstance(calls[-1], dict) else None
    except Exception:
        pass
    return None


def _reload_pdp_soft(driver: Driver, item: ItemConfig) -> None:
    """Reload PDP to refresh Akamai / session sensors after AUTH_DENIED."""
    try:
        open_product(driver, item, force_navigate=False)
        time.sleep(0.6)
        _install_cart_fetch_hook(driver)
    except Exception:
        try:
            open_product(driver, item, force_navigate=True)
            time.sleep(0.6)
            _install_cart_fetch_hook(driver)
        except Exception:
            pass


def add_to_cart(
    driver: Driver,
    item: ItemConfig,
    *,
    prefer_pickup: bool = False,
) -> bool:
    """UI-first ATC (channel 90 path), then careful API. Survives 429 / AUTH_DENIED.

    Live capture showed Target's own Add to cart posts channel_id=90. Under drop
    load we see empty-body 429 and T83072242 (_ERR_AUTH_DENIED). Strategy:
      1) Let the page fire the real fetch (UI click)
      2) On 429 → long backoff + reload, do NOT spam
      3) On 401 AUTH_DENIED → reload sensors, wait, retry UI
      4) API web90 only as sparse backup
    """
    from scalping.bots.target.api import cart_api_add, warm_cart_session

    # Desktop viewport — matches DESKTOP_UA in runtime (avoid mobile spoof mismatch).
    try:
        if hasattr(driver, "run_cdp_command"):
            driver.run_cdp_command(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1280,
                    "height": 900,
                    "deviceScaleFactor": 2,
                    "mobile": False,
                },
            )
    except Exception:
        pass

    if _already_in_cart(driver) or cart_looks_updated(driver):
        print("[ATC] already in cart")
        return True

    _install_cart_fetch_hook(driver)
    warm = warm_cart_session(driver)
    print(
        f"[ATC] cart warm status={warm.get('status')} "
        f"cart_id={warm.get('cart_id')} items={warm.get('item_count')}"
    )

    # Guest / blocked session: buy box shows Sign in — don't dig AUTH_DENIED hole.
    try:
        guestish = driver.run_js(
            """
            const root = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]');
            const t = ((root && root.innerText) || '').toLowerCase();
            return t.includes('sign in to buy') || !!document.querySelector('[data-test="sign-in-to-buy-now-button"]');
            """
        )
        if guestish:
            print(
                "[ATC] 'Sign in to buy now' visible — session not usable for cart. "
                "Run ./scripts/session-target.sh --force once after a cooldown; "
                "skipping ATC spam."
            )
            return False
    except Exception:
        pass

    set_quantity(driver, 1)  # land qty=1 first under allocation
    print(f"[ATC] UI-first — buybox {_buybox_debug(driver)}")

    # Prefer shipping cell when present (desktop layout).
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
        try:
            hit = driver.run_js(
                f"""
                const el = document.querySelector({selector!r});
                if (!el) return false;
                el.click();
                return true;
                """
            )
            if hit:
                chosen = name
                break
        except Exception:
            continue

    auth_denies = 0
    rate_limits = 0
    for attempt in range(1, 16):
        if _already_in_cart(driver) or cart_looks_updated(driver):
            print(f"[ATC] confirmed ({chosen or 'ui'}) try={attempt}")
            return True

        before_n = 0
        try:
            before_n = int(
                driver.run_js("return (window.__scalpingCartCalls||[]).length") or 0
            )
        except Exception:
            before_n = 0

        clicked = _click_visible_atc(driver)
        if clicked:
            print(f"[ATC] UI click try={attempt} via={chosen or 'default'}")
        else:
            if attempt % 3 == 1:
                print(f"[ATC] no ATC button — {_buybox_debug(driver)}")

        time.sleep(0.55)
        if _already_in_cart(driver) or cart_looks_updated(driver):
            print(f"[ATC] landed via UI try={attempt}")
            # Optional qty bump via API once landed
            if (item.max_quantity or 1) > 1:
                time.sleep(0.4)
                bump = cart_api_add(
                    driver,
                    tcin=item.tcin or "",
                    quantity=item.max_quantity,
                    variant="web90",
                )
                print(
                    f"[ATC] qty bump → {item.max_quantity} "
                    f"status={bump.status} err={bump.error!r}"
                )
            return True

        last = _last_cart_call(driver)
        if last and before_n is not None:
            # Only react to a new call
            try:
                after_n = int(
                    driver.run_js("return (window.__scalpingCartCalls||[]).length") or 0
                )
            except Exception:
                after_n = before_n
            if after_n > before_n:
                st = int(last.get("status") or 0)
                print(
                    f"[ATC] page fetch status={st} resp={str(last.get('resp') or '')[:160]!r}"
                )
                if last.get("ok"):
                    return True
                if st == 429:
                    rate_limits += 1
                    wait = min(20.0, 3.0 * rate_limits + random.uniform(0.5, 1.5))
                    print(f"[ATC] page 429 — cool {wait:.1f}s + reload")
                    time.sleep(wait)
                    _reload_pdp_soft(driver, item)
                    continue
                if st == 401 or (
                    isinstance(last.get("resp"), str)
                    and ("_ERR_AUTH_DENIED" in last["resp"] or "T83072242" in last["resp"])
                ):
                    auth_denies += 1
                    wait = min(25.0, 4.0 * auth_denies + random.uniform(1.0, 2.0))
                    print(
                        f"[ATC] AUTH_DENIED/T83072242 — cool {wait:.1f}s + full reload "
                        f"({auth_denies})"
                    )
                    time.sleep(wait)
                    open_product(driver, item, force_navigate=True)
                    time.sleep(0.8)
                    _install_cart_fetch_hook(driver)
                    continue

        # Sparse API backup every few tries (never a storm).
        if attempt in (4, 8, 12):
            variant = ("web", "tempo", "web90")[(attempt // 4) % 3]
            res = cart_api_add(driver, tcin=item.tcin or "", quantity=1, variant=variant)
            print(
                f"[ATC] api backup variant={variant} status={res.status} err={res.error!r}"
            )
            if res.ok:
                return True
            if res.status == 429:
                rate_limits += 1
                wait = min(20.0, 4.0 * rate_limits)
                print(f"[ATC] api 429 — cool {wait:.1f}s")
                time.sleep(wait)
                _reload_pdp_soft(driver, item)
            elif res.status == 401:
                auth_denies += 1
                wait = min(25.0, 5.0 * auth_denies)
                print(f"[ATC] api AUTH_DENIED — cool {wait:.1f}s + reload")
                time.sleep(wait)
                open_product(driver, item, force_navigate=True)
                time.sleep(0.8)
                _install_cart_fetch_hook(driver)

        time.sleep(0.15)

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
            hit = driver.run_js(
                f"""
                const want = {label!r}.toLowerCase();
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (const n of nodes) {{
                  const t = ((n.innerText || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
                  if (t === want || t.includes(want)) {{ n.click(); return true; }}
                }}
                return false;
                """
            )
            if hit:
                time.sleep(0.05)
        except Exception:
            continue

