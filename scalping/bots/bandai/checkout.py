"""Bandai checkout — cart → Global-E order details → place order.

Payment runs through Global-E (`glegem` / GEM_Components) on `/us/orderdetails`.
Saved card on the Bandai/Global-E account is preferred; `BANDAI_CARD_CVV` fills
CVV when the widget asks.

Whenever the bot stops on a page (dry-run, error, timeout, ATC failure), it dumps
HTML + visible text under `~/.scalping/logs/bandai/` for diagnosis.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botasaurus.browser import Driver, browser
from dotenv import load_dotenv

from scalping.bots.bandai.api import BandaiApi, BandaiConfig, atc_from_stock, poll_until_in_stock
from scalping.bots.bandai.runtime import (
    CHROME_ADD_ARGUMENTS,
    PROFILE_DIR,
    capture_dir,
    new_capture_stem,
    prepare_runtime,
)
from scalping.bots.bandai.session import (
    dismiss_overlays,
    ensure_signed_in_on_driver,
    load_bandai_cvv,
    save_session,
)
from scalping.core.paths import REPO_ROOT

BANDAI_CART = "https://p-bandai.com/us/cart"
BANDAI_ORDER_DETAILS = "https://p-bandai.com/us/orderdetails"
ORDER_NO_RE = re.compile(r"/ordercomplete/([A-Za-z0-9_-]+)", re.I)


@dataclass
class CheckoutResult:
    dry_run: bool
    placed_order: bool
    message: str
    order_number: str | None = None
    checkout_sn: str | int | None = None
    details: dict[str, Any] | None = None
    capture_path: str | None = None


def _sleep(a: float = 0.08, b: float = 0.2) -> None:
    time.sleep(random.uniform(a, b))


def capture_stop_page(driver: Driver, reason: str) -> dict[str, Any]:
    """Save HTML + visible text when stopping so messages/errors can be inspected."""
    stem = new_capture_stem(reason)
    out_dir = capture_dir()
    html_path = out_dir / f"{stem}.html"
    text_path = out_dir / f"{stem}.txt"
    meta_path = out_dir / f"{stem}.json"
    iframe_html_path = out_dir / f"{stem}.globale.html"
    iframe_text_path = out_dir / f"{stem}.globale.txt"

    html = ""
    text = ""
    url = ""
    title = ""
    alerts: list[str] = []
    iframe_meta: dict[str, Any] = {}
    try:
        url = str(driver.current_url or "")
    except Exception:
        pass
    try:
        snap = driver.run_js(
            """
            const alerts = [];
            const nodes = Array.from(document.querySelectorAll(
              '[role="alert"], [class*="error" i], [class*="caution" i], [class*="note" i], .p-note-frame'
            ));
            for (const n of nodes) {
              const t = ((n.innerText || '') + '').replace(/\\s+/g, ' ').trim();
              if (t && t.length < 400) alerts.push(t);
            }
            const iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
              src: f.src || '',
              id: f.id || '',
              title: f.title || '',
              w: f.clientWidth, h: f.clientHeight,
            }));
            return {
              html: document.documentElement ? document.documentElement.outerHTML : '',
              text: document.body ? document.body.innerText : '',
              title: document.title || '',
              alerts: [...new Set(alerts)].slice(0, 20),
              iframes,
            };
            """
        )
        if isinstance(snap, dict):
            html = str(snap.get("html") or "")
            text = str(snap.get("text") or "")
            title = str(snap.get("title") or "")
            raw_alerts = snap.get("alerts") or []
            if isinstance(raw_alerts, list):
                alerts = [str(a) for a in raw_alerts if a]
            iframe_meta["iframes"] = snap.get("iframes") or []
    except Exception as exc:
        print(f"[BANDAI] page capture JS failed: {exc}")
        try:
            html = str(getattr(driver, "page_html", "") or "")
        except Exception:
            html = ""

    # Global-E checkout lives in a cross-origin iframe — capture it separately.
    try:
        frame = None
        try:
            frame = driver.get_iframe_by_link(r"global-e\.com/Checkout", wait=2)
        except Exception:
            frame = None
        if frame is None:
            try:
                frame = driver.get_iframe_by_link(r"webservices\.global-e\.com", wait=1)
            except Exception:
                frame = None
        if frame is not None:
            iframe_html = ""
            iframe_text = ""
            try:
                iframe_html = str(getattr(frame, "page_html", None) or getattr(frame, "html", "") or "")
            except Exception:
                pass
            try:
                iframe_text = str(
                    getattr(frame, "page_text", None)
                    or frame.run_js("return document.body ? document.body.innerText : ''")
                    or ""
                )
            except Exception:
                pass
            try:
                frame_alerts = frame.run_js(
                    """
                    const alerts = [];
                    const nodes = Array.from(document.querySelectorAll(
                      '[role="alert"], [class*="error" i], [class*="warning" i], .error, .validation'
                    ));
                    for (const n of nodes) {
                      const t = ((n.innerText || '') + '').replace(/\\s+/g, ' ').trim();
                      if (t && t.length < 400) alerts.push(t);
                    }
                    return [...new Set(alerts)].slice(0, 20);
                    """
                )
                if isinstance(frame_alerts, list):
                    alerts.extend(str(a) for a in frame_alerts if a)
            except Exception:
                pass
            if iframe_html:
                iframe_html_path.write_text(iframe_html, encoding="utf-8")
                iframe_meta["globale_html_path"] = str(iframe_html_path)
            if iframe_text:
                iframe_text_path.write_text(iframe_text, encoding="utf-8")
                iframe_meta["globale_text_path"] = str(iframe_text_path)
                iframe_meta["globale_text_preview"] = iframe_text[:1200]
                # Prefer iframe text for diagnosis when parent is chrome-only.
                if len(iframe_text.strip()) > 40:
                    text = (text[:400] + "\n\n--- GLOBAL-E IFRAME ---\n\n" + iframe_text)
    except Exception as exc:
        iframe_meta["globale_error"] = str(exc)

    try:
        html_path.write_text(html, encoding="utf-8")
        text_path.write_text(text, encoding="utf-8")
        meta = {
            "reason": reason,
            "url": url,
            "title": title,
            "alerts": alerts,
            "html_path": str(html_path),
            "text_path": str(text_path),
            "text_preview": text[:1500],
            **iframe_meta,
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[BANDAI] page capture write failed: {exc}")
        return {"reason": reason, "url": url, "error": str(exc)}

    preview = " | ".join(alerts[:3]) if alerts else text[:240].replace("\n", " | ")
    print(f"[BANDAI] captured stop page → {html_path}")
    if iframe_meta.get("globale_html_path"):
        print(f"[BANDAI] captured Global-E iframe → {iframe_meta['globale_html_path']}")
    if preview:
        print(f"[BANDAI] page preview: {preview}")
    return {
        "reason": reason,
        "url": url,
        "title": title,
        "alerts": alerts,
        "html_path": str(html_path),
        "text_path": str(text_path),
        "meta_path": str(meta_path),
        "text_preview": text[:1500],
        **iframe_meta,
    }


def _globale_frame(driver: Driver):
    """Return Global-E checkout iframe element if present."""
    for pattern in (
        r"global-e\.com/Checkout",
        r"webservices\.global-e\.com",
        r"global-e\.com",
    ):
        try:
            frame = driver.get_iframe_by_link(pattern, wait=2)
            if frame is not None:
                return frame
        except Exception:
            continue
    return None


def _fill_cvv_in_context(ctx, cvv: str) -> bool:
    if not cvv:
        return False
    try:
        filled = ctx.run_js(
            f"""
            const cvv = {cvv!r};
            const inputs = Array.from(document.querySelectorAll('input'));
            const match = inputs.find(el => {{
              const blob = ((el.name||'') + (el.id||'') + (el.placeholder||'')
                + (el.autocomplete||'') + (el.getAttribute('aria-label')||'')).toLowerCase();
              const t = (el.type || '').toLowerCase();
              return blob.includes('cvv') || blob.includes('cvc') || blob.includes('security')
                || blob.includes('cid') || (t === 'password' && blob.includes('card'));
            }});
            if (!match) return false;
            match.focus();
            const proto = window.HTMLInputElement && window.HTMLInputElement.prototype;
            const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(match, cvv); else match.value = cvv;
            match.dispatchEvent(new Event('input', {{bubbles: true}}));
            match.dispatchEvent(new Event('change', {{bubbles: true}}));
            return true;
            """
        )
        if filled:
            print("[BANDAI] filled CVV field")
            return True
    except Exception as exc:
        print(f"[BANDAI] CVV fill failed: {exc}")
    return False


def _click_text_in_context(ctx, text: str) -> bool:
    try:
        hit = ctx.run_js(
            f"""
            const want = {text!r}.toLowerCase();
            const nodes = Array.from(document.querySelectorAll(
              'button, a, [role="button"], input[type="submit"], label, span'
            ));
            for (const n of nodes) {{
              const t = ((n.innerText || n.value || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
              if (!t) continue;
              if (t === want || t.includes(want)) {{
                n.click();
                return t;
              }}
            }}
            return null;
            """
        )
        if hit:
            print(f"[BANDAI] click → {hit!r}")
            _sleep()
            return True
    except Exception as exc:
        print(f"[BANDAI] click_text failed: {exc}")
    return False


def _context_has(ctx, *needles: str) -> bool:
    try:
        text = (ctx.run_js("return document.body ? document.body.innerText : ''") or "").lower()
    except Exception:
        text = ""
    return any(n.lower() in text for n in needles)


def _click_text(driver: Driver, text: str) -> bool:
    try:
        hit = driver.run_js(
            f"""
            const want = {text!r}.toLowerCase();
            const nodes = Array.from(document.querySelectorAll(
              'button, a, [role="button"], input[type="submit"], label, span'
            ));
            for (const n of nodes) {{
              const t = ((n.innerText || n.value || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
              if (!t) continue;
              if (t === want || t.includes(want)) {{
                n.click();
                return t;
              }}
            }}
            return null;
            """
        )
        if hit:
            print(f"[BANDAI] click → {hit!r}")
            _sleep()
            return True
    except Exception as exc:
        print(f"[BANDAI] click_text failed: {exc}")
    return False


def _fill_cvv_if_present(driver: Driver, cvv: str) -> bool:
    if not cvv:
        return False
    try:
        filled = driver.run_js(
            f"""
            const cvv = {cvv!r};
            const inputs = Array.from(document.querySelectorAll('input'));
            const match = inputs.find(el => {{
              const blob = ((el.name||'') + (el.id||'') + (el.placeholder||'')
                + (el.autocomplete||'') + (el.getAttribute('aria-label')||'')).toLowerCase();
              const t = (el.type || '').toLowerCase();
              return blob.includes('cvv') || blob.includes('cvc') || blob.includes('security')
                || blob.includes('cid') || (t === 'password' && blob.includes('card'));
            }});
            if (!match) return false;
            match.focus();
            const proto = window.HTMLInputElement && window.HTMLInputElement.prototype;
            const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(match, cvv); else match.value = cvv;
            match.dispatchEvent(new Event('input', {{bubbles: true}}));
            match.dispatchEvent(new Event('change', {{bubbles: true}}));
            return true;
            """
        )
        if filled:
            print("[BANDAI] filled CVV field")
            return True
    except Exception as exc:
        print(f"[BANDAI] CVV fill failed: {exc}")
    return False


def _page_has(driver: Driver, *needles: str) -> bool:
    try:
        text = (
            driver.run_js("return document.body ? document.body.innerText : ''") or ""
        ).lower()
    except Exception:
        text = ""
    return any(n.lower() in text for n in needles)


def browser_add_to_cart(driver: Driver, config: BandaiConfig) -> bool:
    url = config.item_url or f"https://p-bandai.com/us/item/{config.product_code()}"
    try:
        driver.get(url, wait=1, timeout=35)
    except Exception as exc:
        print(f"[BANDAI] PDP load warning: {exc}")
    time.sleep(1.5)
    dismiss_overlays(driver)

    # Prefer API ATC via in-page fetch (same cookies / CSRF as SPA).
    code = config.product_code()
    result = driver.run_js(
        f"""
        return (async () => {{
          const csrf = (window.USER_DATA && window.USER_DATA.csrfToken) || '';
          const headers = {{
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-G1-Area-Code': 'us',
            ...(csrf ? {{'X-CSRF-TOKEN': csrf}} : {{}}),
          }};
          const prodRes = await fetch('/api/products/' + encodeURIComponent({code!r}), {{
            credentials: 'include', headers,
          }});
          const product = await prodRes.json();
          const areas = product.areaItemNos || [];
          if (!areas.length) {{
            return {{ok: false, reason: 'no_area', status: prodRes.status, productKeys: Object.keys(product||{{}})}};
          }}
          const body = [{{areaItemNo: areas[0], qty: {int(config.qty)}}}];
          const atc = await fetch('/api/cart/addToCart', {{
            method: 'POST', credentials: 'include', headers,
            body: JSON.stringify(body),
          }});
          const text = await atc.text();
          let data = null;
          try {{ data = JSON.parse(text); }} catch (e) {{ data = text.slice(0, 400); }}
          const err = (data && data.error) || '';
          // Already at max qty in cart = fine for checkout path.
          const already = err === 'CouldNotAddToCartByMaxPurchaseQty';
          return {{
            ok: (atc.status >= 200 && atc.status < 300) || already,
            status: atc.status, data, area: areas[0], already_in_cart: already,
          }};
        }})();
        """
    )
    print(f"[BANDAI] in-page ATC → {result}")
    if isinstance(result, dict) and result.get("ok"):
        return True

    # UI fallback
    if _click_text(driver, "add to cart"):
        time.sleep(1.2)
        return True
    return False


def browser_proceed_checkout(driver: Driver) -> dict[str, Any]:
    """From cart: POST checkout API then open orderdetails."""
    try:
        driver.get(BANDAI_CART, wait=1, timeout=30)
    except Exception as exc:
        print(f"[BANDAI] cart load warning: {exc}")
    time.sleep(1.2)
    dismiss_overlays(driver)

    result = driver.run_js(
        """
        return (async () => {
          const csrf = (window.USER_DATA && window.USER_DATA.csrfToken) || '';
          const headers = {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-G1-Area-Code': 'us',
            ...(csrf ? {'X-CSRF-TOKEN': csrf} : {}),
          };
          const detailRes = await fetch('/api/cart/detail', {credentials: 'include', headers});
          const detail = await detailRes.json();
          const suffix = (window.PRELOAD_DATA && window.PRELOAD_DATA.globaleMerchantCartTokenSuffix) || '';
          const subCarts = detail.subCarts || [];
          // Prefer a cart with AvailableForPurchase items (skip dead preorders).
          const ranked = [...subCarts].sort((a, b) => {
            const score = (c) => {
              let n = 0;
              for (const g of (c.combinedShippings || [])) {
                for (const li of (g.lineItems || [])) {
                  const st = (li.product && li.product.cartItemStatus) || '';
                  if (st === 'AvailableForPurchase') n += 10;
                  else n -= 1;
                }
              }
              return n;
            };
            return score(b) - score(a);
          });
          const pick = ranked[0] || subCarts[0];
          if (!pick) {
            return {ok: false, reason: 'empty_cart', status: detailRes.status, keys: Object.keys(detail||{})};
          }
          const cartSn = pick.cartSn;
          const cartId = pick.cartId || '';
          const token = cartId ? `${cartId}_Checkout_${suffix}` : '';
          const lineItems = [];
          const unavailable = [];
          for (const g of (pick.combinedShippings || [])) {
            for (const li of (g.lineItems || [])) {
              const sn = (li.product && li.product.cartItemSn) || li.cartLineItemSn;
              const st = (li.product && li.product.cartItemStatus) || '';
              if (!sn) continue;
              if (st === 'AvailableForPurchase') lineItems.push({cartItemSn: sn});
              else unavailable.push({cartItemSn: sn, status: st});
            }
          }
          // If nothing available, still try all lines so API can return a clear error.
          const items = lineItems.length ? lineItems : unavailable.map(u => ({cartItemSn: u.cartItemSn}));
          if (!cartSn || !token || !items.length) {
            return {
              ok: false, reason: 'missing_cart_fields',
              status: detailRes.status,
              cartSn, token: !!token, lines: items.length,
              unavailable, keys: Object.keys(detail || {}),
            };
          }
          const body = {
            merchantCartToken: token,
            shippingAreaCode: null,
            defaultAreaCode: null,
            items,
          };
          const co = await fetch('/api/cart/' + cartSn + '/checkout', {
            method: 'POST', credentials: 'include', headers,
            body: JSON.stringify(body),
          });
          const text = await co.text();
          let data = null;
          try { data = JSON.parse(text); } catch (e) { data = text.slice(0, 400); }
          const checkoutSn = data && (data.checkoutSn || data.checkout_sn);
          if (checkoutSn) {
            try { sessionStorage.setItem('bsp_checkout_sn', String(checkoutSn)); } catch (e) {}
          }
          return {
            ok: co.status >= 200 && co.status < 300 && !!checkoutSn,
            status: co.status, data, cartSn, checkoutSn, lines: items.length,
            unavailable_count: unavailable.length, token_suffix: !!suffix,
          };
        })();
        """
    )
    print(f"[BANDAI] proceed checkout → {result}")
    if isinstance(result, dict) and result.get("checkoutSn"):
        try:
            driver.get(BANDAI_ORDER_DETAILS, wait=1, timeout=40)
        except Exception as exc:
            print(f"[BANDAI] orderdetails load: {exc}")
        time.sleep(2.0)
    elif not (isinstance(result, dict) and result.get("ok")):
        # UI fallback — click Proceed on the available cart group if present
        _click_text(driver, "proceed to checkout")
        time.sleep(2.5)
    return result if isinstance(result, dict) else {"ok": False, "raw": result}


def wait_for_order_complete(driver: Driver, *, timeout: float = 180.0) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = driver.current_url or ""
        m = ORDER_NO_RE.search(url)
        if m:
            return m.group(1)
        if _page_has(driver, "order complete", "thank you for your order", "order number"):
            # Try extract order number from page
            num = driver.run_js(
                """
                const t = document.body ? document.body.innerText : '';
                const m = t.match(/Order\\s*(?:No\\.?|Number|#)\\s*[:#]?\\s*([A-Z0-9-]+)/i);
                return m ? m[1] : null;
                """
            )
            if num:
                return str(num)
        time.sleep(0.6)
    return None


def complete_globale_checkout(
    driver: Driver,
    *,
    dry_run: bool,
    place_order: bool,
    cvv: str,
    timeout: float = 180.0,
) -> CheckoutResult:
    """Drive Global-E widget on orderdetails until confirmation or dry-run stop."""
    dismiss_overlays(driver)
    deadline = time.time() + timeout
    filled_cvv = False
    clicked_pay = False

    # Wait briefly for Global-E iframe to mount.
    for _ in range(20):
        if _globale_frame(driver) is not None:
            break
        time.sleep(0.4)

    while time.time() < deadline:
        url = (driver.current_url or "").lower()
        if "/ordercomplete/" in url:
            m = ORDER_NO_RE.search(url)
            capture = capture_stop_page(driver, "order_complete")
            return CheckoutResult(
                dry_run=False,
                placed_order=True,
                message="order complete",
                order_number=m.group(1) if m else None,
                details=capture,
                capture_path=capture.get("html_path"),
            )
        if "/ordererror" in url:
            capture = capture_stop_page(driver, "order_error")
            return CheckoutResult(
                dry_run=dry_run,
                placed_order=False,
                message="order error page",
                details=capture,
                capture_path=capture.get("html_path"),
            )

        frame = _globale_frame(driver)
        contexts = [c for c in (frame, driver) if c is not None]

        if cvv and not filled_cvv:
            for ctx in contexts:
                if _fill_cvv_in_context(ctx, cvv):
                    filled_cvv = True
                    break

        # Dry-run: stop once checkout UI is visible, before final submit.
        if dry_run or not place_order:
            ready = False
            if frame is not None and _context_has(
                frame,
                "place order",
                "pay now",
                "confirm",
                "payment",
                "credit card",
                "billing",
                "shipping",
                "total",
            ):
                ready = True
            elif _page_has(
                driver,
                "place order",
                "pay now",
                "confirm and pay",
                "complete order",
                "order details",
                "payment method",
                "checkout",
            ) or "orderdetails" in url:
                ready = True
            if ready:
                print("[BANDAI] dry_run — stopping before final submit")
                capture = capture_stop_page(driver, "dry_run_stop")
                return CheckoutResult(
                    dry_run=True,
                    placed_order=False,
                    message="dry_run stop at checkout",
                    details=capture,
                    capture_path=capture.get("html_path"),
                )

        if place_order and not clicked_pay:
            for label in (
                "place order",
                "pay now",
                "confirm and pay",
                "complete purchase",
                "submit order",
                "confirm payment",
                "confirm",
            ):
                for ctx in contexts:
                    if _click_text_in_context(ctx, label):
                        clicked_pay = True
                        break
                if clicked_pay:
                    break
            if not clicked_pay and frame is not None:
                # Fallback: Botasaurus text click inside iframe
                try:
                    frame.click_element_containing_text("Place Order")
                    clicked_pay = True
                    print("[BANDAI] click → Place Order (iframe helper)")
                except Exception:
                    pass

        order_no = wait_for_order_complete(driver, timeout=2.0)
        if order_no:
            capture = capture_stop_page(driver, "order_complete")
            return CheckoutResult(
                dry_run=False,
                placed_order=True,
                message="order complete",
                order_number=order_no,
                details=capture,
                capture_path=capture.get("html_path"),
            )
        _sleep(0.4, 0.8)

    capture = capture_stop_page(driver, "checkout_timeout")
    return CheckoutResult(
        dry_run=dry_run,
        placed_order=False,
        message="checkout timeout",
        details=capture,
        capture_path=capture.get("html_path"),
    )


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    wait_for_complete_page_load=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
    raise_exception=True,
)
def _browser_buy(driver: Driver, data: dict) -> dict[str, Any]:
    config: BandaiConfig = data["config"]
    dry_run = bool(data.get("dry_run", True))
    place_order = bool(data.get("place_order", False))
    cvv = load_bandai_cvv()

    # Same Chrome profile — only re-login when session is actually stale.
    if not ensure_signed_in_on_driver(driver, force=bool(data.get("force_login"))):
        raise RuntimeError("Bandai login failed before buy")

    # Fast path: if HTTP already ATCed, skip; else ATC in-page
    if not data.get("skip_atc"):
        if not browser_add_to_cart(driver, config):
            capture = capture_stop_page(driver, "atc_failed")
            return {
                "ok": False,
                "message": "add to cart failed",
                "dry_run": dry_run,
                "placed_order": False,
                "capture_path": capture.get("html_path"),
                "details": capture,
            }

    if dry_run and data.get("stop_after_atc"):
        capture = capture_stop_page(driver, "dry_run_after_atc")
        save_session(driver, known_signed_in=True, navigate_home=False)
        return {
            "ok": True,
            "message": "dry_run stop after ATC",
            "dry_run": True,
            "placed_order": False,
            "capture_path": capture.get("html_path"),
            "details": capture,
        }

    co = browser_proceed_checkout(driver)
    result = complete_globale_checkout(
        driver,
        dry_run=dry_run,
        place_order=place_order,
        cvv=cvv,
    )
    save_session(driver, known_signed_in=True, navigate_home=False)
    return {
        "ok": result.placed_order or result.dry_run,
        "message": result.message,
        "dry_run": result.dry_run,
        "placed_order": result.placed_order,
        "order_number": result.order_number,
        "checkout": co,
        "details": result.details,
        "capture_path": result.capture_path,
    }


def run_buy_pipeline(
    config: BandaiConfig,
    *,
    dry_run: bool | None = None,
    place_order: bool | None = None,
    ensure_session: bool = False,
    use_http_poll: bool = True,
) -> CheckoutResult:
    """Poll stock (HTTP) → ATC + Global-E checkout in the Bandai Chrome profile.

    Login is checked inside the buy browser (`ensure_signed_in_on_driver`) — same
    pattern as Target. A separate pre-flight browser is optional via ensure_session.
    """
    prepare_runtime()
    load_dotenv(REPO_ROOT / ".env", override=True)
    dry = config.dry_run if dry_run is None else dry_run
    place = config.place_order if place_order is None else place_order
    if place:
        dry = False

    if ensure_session:
        from scalping.bots.bandai.session import ensure_bandai_session

        ensure_bandai_session(force=False)

    api = BandaiApi()
    api.bootstrap_csrf()
    stock: dict[str, Any] | None = None
    if use_http_poll:
        if not api.is_logged_in():
            print("[BANDAI] HTTP cookie jar guest — stock poll still works; browser holds session")
        else:
            try:
                polled = poll_until_in_stock(config, api=api)
                stock = polled["stock"]
                # Attempt HTTP ATC first (milliseconds); browser continues checkout.
                for i in range(max(1, config.max_atc_retries)):
                    st, data = atc_from_stock(api, stock, qty=config.qty)
                    print(f"[BANDAI] HTTP ATC try={i+1} status={st}")
                    if st >= 200 and st < 300:
                        break
                    time.sleep(0.2)
            except TimeoutError as exc:
                print(f"[BANDAI] poll ended: {exc}")

    raw = _browser_buy(
        {
            "config": config,
            "dry_run": dry,
            "place_order": place,
            "skip_atc": False,
            "stop_after_atc": False,
            "force_login": False,
        }
    )
    return CheckoutResult(
        dry_run=bool(raw.get("dry_run")),
        placed_order=bool(raw.get("placed_order")),
        message=str(raw.get("message") or ""),
        order_number=raw.get("order_number"),
        checkout_sn=(raw.get("checkout") or {}).get("checkoutSn")
        if isinstance(raw.get("checkout"), dict)
        else None,
        details=raw if isinstance(raw, dict) else None,
        capture_path=raw.get("capture_path") if isinstance(raw, dict) else None,
    )
