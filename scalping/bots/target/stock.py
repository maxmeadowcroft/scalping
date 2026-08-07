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


def set_quantity(driver: Driver, quantity: int) -> int | None:
    """Set PDP buy-box qty to `quantity`, or highest offered ≤ want.

    Call this *after* selecting the fulfillment cell — Target rebuilds the
    buy box on Shipping/Pickup click and resets Qty to 1.
    Returns the qty actually selected, or None if the picker was not found.
    """
    want = max(1, int(quantity))

    try:
        chosen = driver.run_js(
            f"""
            const want = {want};
            const parseN = (raw) => {{
              const m = String(raw || '').match(/\\b([1-9][0-9]?)\\b/);
              if (!m) return null;
              const n = parseInt(m[1], 10);
              return (Number.isFinite(n) && n >= 1 && n <= 99) ? n : null;
            }};
            const root = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]')
              || document.querySelector('[data-test="@web/AddToContentLayout"]')
              || document;

            const pickFromSelect = (sel) => {{
              if (!sel || sel.tagName !== 'SELECT') return null;
              let best = null, bestOpt = null, exact = null, maxOffered = null;
              for (const opt of Array.from(sel.options || [])) {{
                const n = parseN(opt.value) ?? parseN(opt.textContent);
                if (n == null) continue;
                if (maxOffered === null || n > maxOffered) maxOffered = n;
                if (n === want) exact = opt;
                if (n <= want && (best === null || n > best)) {{ best = n; bestOpt = opt; }}
              }}
              const use = exact || bestOpt;
              if (!use) return null;
              const n = parseN(use.value) ?? parseN(use.textContent);
              sel.value = use.value;
              sel.dispatchEvent(new Event('input', {{bubbles:true}}));
              sel.dispatchEvent(new Event('change', {{bubbles:true}}));
              return {{n, maxOffered, options: (sel.options || []).length}};
            }};

            // Native <select>
            for (const sel of root.querySelectorAll('select')) {{
              const meta = (
                (sel.getAttribute('data-test') || '') + ' ' +
                (sel.id || '') + ' ' +
                (sel.name || '') + ' ' +
                (sel.getAttribute('aria-label') || '')
              ).toLowerCase();
              const qtyish = meta.includes('quant') || meta.includes('qty');
              if (!qtyish && root.querySelectorAll('select').length > 1) continue;
              const hit = pickFromSelect(sel);
              if (hit) return {{via:'select', ...hit}};
            }}

            // Button / listbox ("Qty 1") — common on modern Target PDPs
            const openers = Array.from(root.querySelectorAll(
              'button, [role="button"], [role="combobox"], select, ' +
              '[data-test*="quantity" i], [data-test*="Quantity" i], ' +
              '[aria-label*="quantity" i], [aria-label*="Quantity" i], [id*="quantity" i]'
            ));
            let opener = null;
            for (const el of openers) {{
              if (el.tagName === 'SELECT') continue;
              const t = ((el.innerText || el.getAttribute('aria-label') || '') + '')
                .replace(/\\s+/g, ' ').trim().toLowerCase();
              const test = (el.getAttribute('data-test') || '').toLowerCase();
              const id = (el.id || '').toLowerCase();
              if (
                test.includes('quantity') || id.includes('quantity') ||
                /^qty\\b/.test(t) || t.includes('quantity') || /\\bqty\\b/.test(t)
              ) {{
                opener = el;
                break;
              }}
            }}
            if (opener) {{
              try {{ opener.click(); }} catch (e) {{}}
              const deadline = Date.now() + 1200;
              let best = null, bestEl = null, maxOffered = null, optionCount = 0;
              while (Date.now() < deadline) {{
                const opts = Array.from(document.querySelectorAll(
                  '[role="listbox"] [role="option"], [role="option"], ' +
                  '[data-test*="quantity" i] li, [data-test*="Quantity" i] li, ' +
                  'ul[role="listbox"] li, [id*="quantity" i] [role="option"], ' +
                  '[class*="Quantity"] [role="option"], [class*="quantity"] li'
                ));
                optionCount = opts.length;
                for (const o of opts) {{
                  const n = parseN(o.innerText) ?? parseN(o.getAttribute('aria-label'));
                  if (n == null) continue;
                  if (maxOffered === null || n > maxOffered) maxOffered = n;
                  if (n === want) {{
                    o.click();
                    return {{via:'listbox', n, maxOffered, options: optionCount}};
                  }}
                  if (n <= want && (best === null || n > best)) {{
                    best = n; bestEl = o;
                  }}
                }}
                if (bestEl && Date.now() > deadline - 200) {{
                  bestEl.click();
                  return {{via:'listbox', n: best, maxOffered, options: optionCount}};
                }}
              }}
              if (bestEl) {{
                bestEl.click();
                return {{via:'listbox', n: best, maxOffered, options: optionCount}};
              }}
              // Close stray menu if nothing selectable
              try {{ document.body.click(); }} catch (e) {{}}
            }}

            // Number / stepper input
            for (const inp of root.querySelectorAll(
              'input[type="number"], input[name*="quant" i], input[id*="quant" i]'
            )) {{
              const maxAttr = parseN(inp.getAttribute('max'));
              const use = (maxAttr != null && maxAttr < want) ? maxAttr : want;
              const proto = window.HTMLInputElement && window.HTMLInputElement.prototype;
              const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(inp, String(use));
              else inp.value = String(use);
              inp.dispatchEvent(new Event('input', {{bubbles:true}}));
              inp.dispatchEvent(new Event('change', {{bubbles:true}}));
              return {{via:'input', n: use, maxOffered: maxAttr}};
            }}
            return null;
            """
        )
        if isinstance(chosen, dict) and chosen.get("n"):
            n = int(chosen["n"])
            max_offered = chosen.get("maxOffered")
            via = chosen.get("via")
            extra = ""
            if max_offered is not None and int(max_offered) < want:
                extra = f" (max offered {max_offered})"
            print(
                f"[QTY] PDP set to {n} via {via} (wanted {want}{extra})"
            )
            time.sleep(0.2)
            return n
    except Exception as exc:
        print(f"[QTY] JS set failed: {exc}")

    # Fallback: Botasaurus selectors — pick highest available ≤ want
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
        for q in range(want, 0, -1):
            try:
                driver.select_option(selector, str(q))
                print(f"[QTY] select_option {q} (wanted {want})")
                return q
            except Exception:
                continue
        try:
            driver.click(selector)
            time.sleep(0.15)
            for q in range(want, 0, -1):
                if driver.get_element_with_exact_text(str(q), wait=0.2):
                    driver.click_element_containing_text(str(q))
                    print(f"[QTY] picker set to {q} (wanted {want})")
                    return q
        except Exception:
            pass
        try:
            driver.clear(selector)
            driver.type(selector, str(want))
            print(f"[QTY] typed {want}")
            return want
        except Exception:
            continue
    print(f"[QTY] could not set PDP quantity to {want}")
    return None


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
    """ATC success — require buy-box 'N in cart' or checkout CTA after a real add.

    Do NOT trust the header cart badge alone (stale / empty-cart ghosts caused
    false ATC success during Pitch Black probes).
    """
    if _already_in_cart(driver):
        return True
    try:
        return bool(
            driver.run_js(
                """
                // Checkout CTAs that only appear after a successful add.
                if (document.querySelector('[data-test="cartItem-checkoutButton"]')) return true;
                // Fulfillment buy-box "N in cart" only.
                const box = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]');
                if (box) {
                  const t = (box.innerText || '').toLowerCase();
                  const m = t.match(/(\\d+)\\s+in cart/);
                  if (m && parseInt(m[1], 10) >= 1) return true;
                  if (t.includes('view cart & check out') || t.includes('view cart and check out')) return true;
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


def _is_traffic_delay_text(text: str | None) -> bool:
    """Target's popular-item / high-traffic cart queue (hits humans too)."""
    if not text:
        return False
    t = text.lower()
    return (
        ("popular item" in t and "delay" in t)
        or ("managing high traffic" in t)
        or ("high traffic right now" in t)
        or ("high demand" in t and ("try again" in t or "temporarily" in t))
    )


def _traffic_delay_visible(driver: Driver) -> bool:
    try:
        return bool(
            driver.run_js(
                """
                const nodes = Array.from(document.querySelectorAll(
                  '[role="alert"], [data-test*="alert" i], [data-test*="toast" i], '
                  + '[class*="Alert"], [class*="Toast"], [class*="Banner"], div, p, span'
                )).slice(0, 80);
                for (const el of nodes) {
                  const t = ((el.innerText || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
                  if (!t || t.length > 280) continue;
                  if (t.includes('popular item') && t.includes('delay')) return true;
                  if (t.includes('managing high traffic')) return true;
                  if (t.includes('high traffic right now')) return true;
                }
                return false;
                """
            )
        )
    except Exception:
        return False


def _cart_call_is_traffic_delay(last: dict | None) -> bool:
    if not last:
        return False
    blob = " ".join(
        str(last.get(k) or "")
        for k in ("resp", "error", "body", "message")
    )
    return _is_traffic_delay_text(blob)


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


def _reload_pdp_soft(
    driver: Driver,
    item: ItemConfig,
    *,
    quantity: int | None = None,
    prefer_pickup: bool = False,
) -> None:
    """Reload PDP to refresh session sensors after AUTH_DENIED / 429."""
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
    try:
        _select_fulfillment_cell(driver, prefer_pickup)
        time.sleep(0.3)
    except Exception:
        pass
    if quantity and quantity > 0:
        try:
            set_quantity(driver, quantity)
        except Exception:
            pass


def add_to_cart(
    driver: Driver,
    item: ItemConfig,
    *,
    prefer_pickup: bool = False,
    auth_timeout: float = 120.0,
) -> bool:
    """UI-first ATC. Avoid 'Sign in to buy now' unless Add to cart is unavailable.

    That step-up burns OTP and often isn't required when shippingButton is live.
    """
    from scalping.bots.target.api import cart_api_add, warm_cart_session
    from scalping.bots.target.checkout import (
        sign_in_to_buy_visible,
        wait_for_pdp_purchase_auth,
    )

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

    # Prefer Add to cart even when "Sign in to buy now" is also visible.
    # Only escalate to that step-up if ATC is impossible (no ATC control).
    want_qty = max(1, int(item.max_quantity or 1))
    if sign_in_to_buy_visible(driver):
        probe = _buybox_stock_probe(driver)
        has_atc = bool(probe.get("enabledAtc") or probe.get("enabledCell"))
        if has_atc:
            print(
                "[ATC] Sign in to buy visible — ignoring for now; using Add to cart"
            )
        else:
            print(
                "[ATC] no ATC control, only Sign in to buy — step-up required"
            )
            if not wait_for_pdp_purchase_auth(driver, timeout_seconds=auth_timeout):
                print("[ATC] PDP purchase auth failed — cannot ATC yet")
                return False
            try:
                open_product(driver, item, force_navigate=True)
                time.sleep(0.8)
                _install_cart_fetch_hook(driver)
            except Exception:
                pass

    # Prefer shipping cell when present (desktop layout). Qty picker lives in
    # the rebuilt buy box *after* this click — set quantity only afterward.
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
    if chosen:
        time.sleep(0.35)

    got_qty = set_quantity(driver, want_qty)
    print(
        f"[ATC] UI-first qty={got_qty or '?'} (want {want_qty}) "
        f"via={chosen or 'default'} — buybox {_buybox_debug(driver)}"
    )

    auth_denies = 0
    rate_limits = 0
    traffic_delays = 0
    # Paced retries — not a mash. Outer poll loop re-enters when still in stock.
    max_attempts = 12
    for attempt in range(1, max_attempts + 1):
        if _already_in_cart(driver) or cart_looks_updated(driver):
            print(f"[ATC] confirmed ({chosen or 'ui'}) try={attempt}")
            return True

        # Last resort only: several failed ATC tries AND no Add to cart control left.
        if attempt == 8 and sign_in_to_buy_visible(driver):
            probe = _buybox_stock_probe(driver)
            if not (probe.get("enabledAtc") or probe.get("enabledCell")):
                print(
                    "[ATC] still no ATC after many tries — Sign in to buy as last resort"
                )
                if wait_for_pdp_purchase_auth(
                    driver, timeout_seconds=min(90.0, auth_timeout)
                ):
                    _install_cart_fetch_hook(driver)
                    try:
                        open_product(driver, item, force_navigate=True)
                        time.sleep(0.6)
                        _install_cart_fetch_hook(driver)
                        if chosen:
                            _select_fulfillment_cell(driver, prefer_pickup)
                            time.sleep(0.3)
                        set_quantity(driver, want_qty)
                    except Exception:
                        pass

        before_n = 0
        try:
            before_n = int(
                driver.run_js("return (window.__scalpingCartCalls||[]).length") or 0
            )
        except Exception:
            before_n = 0

        # Re-apply qty before each click (buy box can reset after failed ATC).
        set_quantity(driver, want_qty)

        clicked = _click_visible_atc(driver)
        if clicked:
            print(f"[ATC] UI click try={attempt} via={chosen or 'default'}")
        else:
            if attempt % 3 == 1:
                print(f"[ATC] no ATC button — {_buybox_debug(driver)}")

        time.sleep(0.85)
        if _already_in_cart(driver) or cart_looks_updated(driver):
            print(f"[ATC] landed via UI try={attempt} (PDP qty, no API bump)")
            return True

        last = _last_cart_call(driver)
        traffic = _traffic_delay_visible(driver) or _cart_call_is_traffic_delay(last)
        if last and before_n is not None:
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
                if last.get("ok") and st and 200 <= st < 300:
                    return True
                if traffic or _is_traffic_delay_text(str(last.get("resp") or "")):
                    traffic_delays += 1
                    wait = 1.2 + random.uniform(0.3, 1.0)
                    if traffic_delays <= 3 or traffic_delays % 4 == 1:
                        print(
                            f"[ATC] traffic/popular delay — paced retry "
                            f"(hit {traffic_delays}, gap ~{wait:.1f}s)"
                        )
                    time.sleep(wait)
                    if traffic_delays % 5 == 0:
                        _reload_pdp_soft(driver, item, quantity=want_qty, prefer_pickup=prefer_pickup)
                    continue
                if st == 429:
                    rate_limits += 1
                    wait = min(45.0, 5.0 * rate_limits + random.uniform(1.0, 3.0))
                    print(f"[ATC] page 429 — cool {wait:.1f}s + soft reload")
                    time.sleep(wait)
                    _reload_pdp_soft(driver, item, quantity=want_qty, prefer_pickup=prefer_pickup)
                    continue
                if st == 401 or (
                    isinstance(last.get("resp"), str)
                    and ("_ERR_AUTH_DENIED" in last["resp"] or "T83072242" in last["resp"])
                ):
                    auth_denies += 1
                    wait = min(12.0, 1.5 * auth_denies + random.uniform(0.5, 1.5))
                    print(
                        f"[ATC] AUTH_DENIED — cool {wait:.1f}s "
                        f"(deny#{auth_denies}/{max_attempts})"
                    )
                    time.sleep(wait)
                    if auth_denies >= 4:
                        _reload_pdp_soft(driver, item, quantity=want_qty, prefer_pickup=prefer_pickup)
                    if auth_denies >= 8:
                        print("[ATC] AUTH_DENIED budget exhausted — stop this ATC pass")
                        break
                    continue

        if traffic:
            traffic_delays += 1
            wait = 1.2 + random.uniform(0.3, 1.0)
            if traffic_delays <= 3 or traffic_delays % 4 == 1:
                print(f"[ATC] traffic toast — paced retry (hit {traffic_delays})")
            time.sleep(wait)
            continue

        # One sparse API backup with desired qty (not qty=1 + bump).
        if traffic_delays == 0 and auth_denies < 2 and attempt == 6:
            res = cart_api_add(
                driver,
                tcin=item.tcin or "",
                quantity=want_qty,
                variant="web",
            )
            print(
                f"[ATC] api backup qty={want_qty} status={res.status} err={res.error!r}"
            )
            if res.ok:
                return True
            if _is_traffic_delay_text(res.error):
                traffic_delays += 1
            elif res.status == 429:
                rate_limits += 1
                time.sleep(min(20.0, 4.0 * rate_limits))
                _reload_pdp_soft(driver, item, quantity=want_qty, prefer_pickup=prefer_pickup)
            elif res.status == 401:
                auth_denies += 1

        time.sleep(0.6 + random.uniform(0.2, 0.6))

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

