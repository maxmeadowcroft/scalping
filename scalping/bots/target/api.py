"""Target Redsky + carts.target.com helpers (in-browser fetch).

Drop strategy
-------------
Under load, UI ATC often clicks but never lands. The same session cookies can
POST ``carts.target.com/web_checkouts/v1/cart_items`` directly — that is what
Tempo / PS5-era bookmarklets use.

**Critical:** do NOT fire parallel cart POSTs. Target rate-limits hard (429);
our earlier 4-way Promise.all was self-inflicting throttling. One request at a
time, with real backoff, then UI.

Also note: a 401 / "access denied" on cart often means allocation lost (or
Akamai shape challenge), not always a bad session — ``cart_views`` can still
be 200.

Endpoints (from Target __CONFIG__):
  POST https://carts.target.com/web_checkouts/v1/cart_items
  GET  https://carts.target.com/web_checkouts/v1/cart_views
  GET  https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from botasaurus.browser import Driver

from scalping.bots.target.config import ItemConfig
from scalping.bots.target.stock import StockCheckResult, StockStatus

DEFAULT_API_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
REDSKY_SUMMARY = (
    "https://redsky.target.com/redsky_aggregations/v1/web/"
    "product_summary_with_fulfillment_v1"
)
REDSKY_FULFILLMENT = (
    "https://redsky.target.com/redsky_aggregations/v1/web/"
    "product_fulfillment_v1"
)
CART_ITEMS = "https://carts.target.com/web_checkouts/v1/cart_items"
CART_VIEWS = "https://carts.target.com/web_checkouts/v1/cart_views"

# Historical PS5-era bypass still accepted by some cart paths.
FULFILLMENT_TEST_MODE = {"fulfillment_test_mode": "grocery_opu_team_member_test"}

# Desktop PDP (captured Aug 2026) posts channel_id/item_channel_id **10**.
# Mobile / earlier sessions sometimes used **90**. Prefer 10 with DESKTOP_UA.
VARIANT_ORDER = ("web", "tempo", "web90", "tempo90", "test_mode")


@dataclass(frozen=True)
class ApiAtcResult:
    ok: bool
    status: int
    variant: str
    error: str | None = None
    data: Any = None
    retry_after: float | None = None


def _resolve_api_key_js() -> str:
    return f"""
      (window.__CONFIG__ && window.__CONFIG__.apiKey)
      || (window.__TGT_DATA__ && window.__TGT_DATA__.apiKey)
      || {DEFAULT_API_KEY!r}
    """


def _summarize_error(data: Any) -> str | None:
    if data is None:
        return None
    if isinstance(data, str):
        return data[:240] if data.strip() else None
    if not isinstance(data, dict):
        return str(data)[:240]
    # Target shapes: errorKey/errorMessage (T83072242), Error.Detail, alerts…
    if data.get("errorKey") or data.get("errorMessage") or data.get("errorCode"):
        parts = [
            str(data[k])
            for k in ("errorCode", "errorKey", "errorMessage")
            if data.get(k)
        ]
        return " | ".join(parts)[:280]
    err = data.get("Error") or data.get("error")
    if isinstance(err, dict):
        detail = err.get("Detail") or err.get("detail") or err.get("Message")
        if detail:
            return str(detail)[:240]
        return str(err)[:240]
    if data.get("message"):
        return str(data["message"])[:240]
    if data.get("code"):
        return str(data["code"])[:120]
    alerts = data.get("alerts")
    if isinstance(alerts, list) and alerts:
        a0 = alerts[0]
        if isinstance(a0, dict) and a0.get("message"):
            return str(a0["message"])[:240]
    return None


def warm_cart_session(driver: Driver) -> dict[str, Any]:
    """GET cart_views so cookies / cart id are warm before a drop burst."""
    try:
        result = driver.run_js(
            f"""
            return (async () => {{
              const key = {_resolve_api_key_js()};
              const url = {CART_VIEWS!r}
                + '?field_groups=CART%2CCART_ITEMS%2CSUMMARY&key=' + encodeURIComponent(key);
              try {{
                const res = await fetch(url, {{
                  method: 'GET', mode: 'cors', credentials: 'include',
                  headers: {{
                    'Accept': 'application/json',
                    'x-application-name': 'web',
                  }},
                }});
                const text = await res.text();
                let data = null;
                try {{ data = JSON.parse(text); }} catch (e) {{ data = text.slice(0, 200); }}
                return {{
                  status: res.status,
                  ok: res.status >= 200 && res.status < 300,
                  cart_id: data && data.cart_id,
                  item_count: data && data.summary && data.summary.cart_count,
                }};
              }} catch (e) {{
                return {{ status: 0, ok: false, error: String(e) }};
              }}
            }})();
            """
        )
        return result if isinstance(result, dict) else {"ok": False}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def cart_api_add(
    driver: Driver,
    *,
    tcin: str,
    quantity: int = 1,
    variant: str = "tempo",
) -> ApiAtcResult:
    """Single cart_items POST — never parallelize these under drop traffic."""
    qty = max(1, int(quantity))
    # Live PDP (captured Aug 2026) uses channel_id/item_channel_id "90".
    # Older Tempo/PS5 bookmarklets used "10" — keep as fallbacks.
    variants = {
        "web90": {
            "app": "web",
            "mobile": "?1",
            "referrer": None,
            "body": {
                "cart_type": "REGULAR",
                "channel_id": "90",
                "shopping_context": "DIGITAL",
                "cart_item": {
                    "tcin": str(tcin),
                    "quantity": qty,
                    "item_channel_id": "90",
                },
            },
        },
        "tempo90": {
            "app": "tempo",
            "mobile": "?0",
            "referrer": "https://www.target.com/tempo",
            "body": {
                "cart_type": "REGULAR",
                "channel_id": "90",
                "shopping_context": "DIGITAL",
                "cart_item": {
                    "tcin": str(tcin),
                    "quantity": qty,
                    "item_channel_id": "90",
                },
            },
        },
        "tempo": {
            "app": "tempo",
            "mobile": "?0",
            "referrer": "https://www.target.com/tempo",
            "body": {
                "cart_type": "REGULAR",
                "channel_id": "10",
                "shopping_context": "DIGITAL",
                "cart_item": {
                    "tcin": str(tcin),
                    "quantity": qty,
                    "item_channel_id": "10",
                },
            },
        },
        "web": {
            "app": "web",
            "mobile": "?0",
            "referrer": None,
            "body": {
                "cart_type": "REGULAR",
                "channel_id": "10",
                "shopping_context": "DIGITAL",
                "cart_item": {
                    "tcin": str(tcin),
                    "quantity": qty,
                    "item_channel_id": "10",
                },
            },
        },
        "mobile_web": {
            "app": "web",
            "mobile": "?1",
            "referrer": None,
            "body": {
                "cart_type": "REGULAR",
                "channel_id": "10",
                "shopping_context": "DIGITAL",
                "cart_item": {
                    "tcin": str(tcin),
                    "quantity": qty,
                    "item_channel_id": "10",
                },
            },
        },
        "test_mode": {
            "app": "web",
            "mobile": "?0",
            "referrer": None,
            "body": {
                "cart_type": "REGULAR",
                "channel_id": "90",
                "shopping_context": "DIGITAL",
                "cart_item": {
                    "tcin": str(tcin),
                    "quantity": qty,
                    "item_channel_id": "90",
                },
                "fulfillment": dict(FULFILLMENT_TEST_MODE),
            },
        },
    }
    cfg = variants.get(variant) or variants["web90"]
    body_json = json.dumps(cfg["body"])
    # Must be JS null / string — Python None becomes invalid `None` identifier in JS.
    referrer_js = json.dumps(cfg.get("referrer"))
    try:
        result = driver.run_js(
            f"""
            return (async () => {{
              const key = {_resolve_api_key_js()};
              const url = {CART_ITEMS!r}
                + '?field_groups=CART%2CCART_ITEMS%2CSUMMARY&key=' + encodeURIComponent(key);
              const body = {body_json};
              const headers = {{
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'x-application-name': {cfg["app"]!r},
                'sec-ch-ua-mobile': {cfg["mobile"]!r},
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-site',
              }};
              // Do NOT attach Authorization — live PDP fetch only sends
              // Accept / Content-Type / x-application-name (Bearer caused noise).
              const init = {{
                method: 'POST',
                mode: 'cors',
                credentials: 'include',
                headers,
                body: JSON.stringify(body),
              }};
              const referrer = {referrer_js};
              if (referrer) {{
                try {{ init.referrer = referrer; }} catch (e) {{}}
              }}
              try {{
                const res = await fetch(url, init);
                const text = await res.text();
                let data = null;
                try {{ data = JSON.parse(text); }} catch (e) {{ data = text.slice(0, 500); }}
                const retryAfter = res.headers.get('retry-after');
                const hdrs = {{}};
                try {{
                  for (const [k, v] of res.headers.entries()) {{
                    if (/retry|rate|auth|www-|x-/i.test(k)) hdrs[k] = v;
                  }}
                }} catch (e) {{}}
                return {{
                  ok: res.status === 200 || res.status === 201,
                  status: res.status,
                  data,
                  retry_after: retryAfter,
                  response_headers: hdrs,
                  variant: {variant!r},
                  cart_id: data && data.cart_id,
                }};
              }} catch (e) {{
                return {{ ok: false, status: 0, error: String(e), variant: {variant!r} }};
              }}
            }})();
            """
        )
        if not isinstance(result, dict):
            return ApiAtcResult(False, 0, variant, error=str(result))
        data = result.get("data")
        err = result.get("error") or _summarize_error(data)
        retry_raw = result.get("retry_after")
        retry_after = None
        if retry_raw is not None:
            try:
                retry_after = float(retry_raw)
            except (TypeError, ValueError):
                retry_after = None
        return ApiAtcResult(
            ok=bool(result.get("ok")),
            status=int(result.get("status") or 0),
            variant=str(result.get("variant") or variant),
            error=(str(err) if err else None),
            data=data,
            retry_after=retry_after,
        )
    except Exception as exc:
        return ApiAtcResult(False, 0, variant, error=str(exc))


def cart_api_burst(
    driver: Driver,
    *,
    tcin: str,
    quantity: int = 1,
    wave: int = 1,
) -> ApiAtcResult:
    """One sequential ATC attempt (rotating variant). Name kept for callers.

    Previously Promise.all'd 4 variants and self-inflicted 429s — never do that.
    """
    variant = VARIANT_ORDER[(max(1, wave) - 1) % len(VARIANT_ORDER)]
    return cart_api_add(driver, tcin=tcin, quantity=quantity, variant=variant)


def diagnose_cart_api(driver: Driver, *, tcin: str) -> dict[str, Any]:
    """Single-shot probe: warm + one tempo POST + body dump (for live debugging)."""
    warm = warm_cart_session(driver)
    atc = cart_api_add(driver, tcin=tcin, quantity=1, variant="tempo")
    return {
        "warm": warm,
        "atc": {
            "ok": atc.ok,
            "status": atc.status,
            "variant": atc.variant,
            "error": atc.error,
            "retry_after": atc.retry_after,
            "data_excerpt": (
                json.dumps(atc.data)[:500]
                if not isinstance(atc.data, str)
                else atc.data[:500]
            )
            if atc.data is not None
            else None,
        },
    }


def poll_fulfillment_api(
    driver: Driver,
    *,
    tcin: str,
    zip_code: str = "87111",
    state: str = "NM",
    store_id: str | None = None,
    prefer_pickup: bool = False,
) -> StockCheckResult:
    """In-browser Redsky fulfillment poll — authoritative vs related-product DOM.

    Tries ``product_summary_with_fulfillment_v1`` then ``product_fulfillment_v1``
    (``pdp_fulfillment_v1`` is 410/retired).
    """
    store = store_id or ""
    try:
        result = driver.run_js(
            f"""
            return (async () => {{
              const key = {_resolve_api_key_js()};
              const tcin = {str(tcin)!r};
              const zip = {str(zip_code)!r};
              const state = {str(state)!r};
              let visitor = '';
              try {{
                const m = document.cookie.match(/(?:^|;\\s*)visitorId=([^;]+)/);
                if (m) visitor = decodeURIComponent(m[1]);
              }} catch (e) {{}}
              const attempts = [
                {{
                  url: {REDSKY_SUMMARY!r},
                  params: {{ key, tcins: tcin, zip, state }},
                }},
                {{
                  url: {REDSKY_FULFILLMENT!r},
                  params: {{
                    key, tcin, zip, state,
                    store_id: {store!r} || '357',
                    pricing_store_id: {store!r} || '357',
                  }},
                }},
              ];
              if (visitor) {{
                attempts[0].params.visitor_id = visitor;
                attempts[1].params.visitor_id = visitor;
              }}
              for (const a of attempts) {{
                const params = new URLSearchParams(a.params);
                try {{
                  const res = await fetch(a.url + '?' + params.toString(), {{
                    method: 'GET', mode: 'cors', credentials: 'include',
                    headers: {{ 'Accept': 'application/json' }},
                  }});
                  if (res.status !== 200) continue;
                  const data = await res.json();
                  return {{ status: res.status, data, via: a.url }};
                }} catch (e) {{}}
              }}
              return {{ status: 0, error: 'all_redsky_failed' }};
            }})();
            """
        )
    except Exception as exc:
        return StockCheckResult(StockStatus.UNKNOWN, f"api_error:{exc}")

    if not isinstance(result, dict):
        return StockCheckResult(StockStatus.UNKNOWN, "api_bad_result")
    if int(result.get("status") or 0) == 403:
        return StockCheckResult(StockStatus.UNKNOWN, "api_captcha_or_403")
    if int(result.get("status") or 0) != 200:
        return StockCheckResult(
            StockStatus.UNKNOWN, f"api_http:{result.get('status')}"
        )

    data = result.get("data") or {}
    product = None
    root = data.get("data") if isinstance(data, dict) else None
    if isinstance(root, dict):
        if isinstance(root.get("product"), dict):
            product = root["product"]
        summaries = root.get("product_summaries") or root.get("products")
        if isinstance(summaries, list) and summaries:
            product = summaries[0]
    if not isinstance(product, dict):
        return StockCheckResult(StockStatus.UNKNOWN, "api_no_product")

    ful = product.get("fulfillment") or {}
    ship = ful.get("shipping_options") or {}
    ship_status = str(ship.get("availability_status") or "").upper()
    # Some responses put qty at shipping_options.available_to_promise_quantity
    # or nested under services / locations — treat explicit 0 as not buyable.
    qty = ship.get("available_to_promise_quantity")
    if qty is None:
        qty = ship.get("available_quantity")
    stores = ful.get("store_options") or []
    pickup_status = ""
    if isinstance(stores, list) and stores:
        first = stores[0] or {}
        opu = first.get("order_pickup") or first.get("in_store_only") or {}
        pickup_status = str(opu.get("availability_status") or "").upper()

    excerpt = (
        f"ship={ship_status or '-'} pickup={pickup_status or '-'} "
        f"qty={qty}"
    )

    def _buyable(status: str) -> bool:
        return status in {"IN_STOCK", "LIMITED_STOCK", "PRE_ORDER", "BACKORDER"}

    # Explicit zero ATP → not actually addable even if status string looks good.
    if qty is not None:
        try:
            if float(qty) <= 0 and not prefer_pickup:
                return StockCheckResult(
                    StockStatus.OUT_OF_STOCK, f"api:ship:qty0:{ship_status}", excerpt
                )
        except (TypeError, ValueError):
            pass

    if prefer_pickup and _buyable(pickup_status):
        return StockCheckResult(StockStatus.IN_STOCK, f"api:pickup:{pickup_status}", excerpt)
    if _buyable(ship_status):
        return StockCheckResult(StockStatus.IN_STOCK, f"api:ship:{ship_status}", excerpt)
    if prefer_pickup is False and _buyable(pickup_status):
        return StockCheckResult(StockStatus.IN_STOCK, f"api:pickup_alt:{pickup_status}", excerpt)

    if ship_status in {"OUT_OF_STOCK", "UNAVAILABLE"} or pickup_status == "OUT_OF_STOCK":
        if ship_status in {"OUT_OF_STOCK", "UNAVAILABLE"} and not prefer_pickup:
            return StockCheckResult(StockStatus.OUT_OF_STOCK, f"api:ship:{ship_status}", excerpt)
        if prefer_pickup and pickup_status in {"OUT_OF_STOCK", "UNAVAILABLE"}:
            return StockCheckResult(StockStatus.OUT_OF_STOCK, f"api:pickup:{pickup_status}", excerpt)

    return StockCheckResult(StockStatus.UNKNOWN, "api:no_buyable", excerpt)


def aggressive_api_atc(
    driver: Driver,
    item: ItemConfig,
    *,
    desired_qty: int | None = None,
    max_waves: int = 8,
) -> ApiAtcResult:
    """Drop-oriented ATC: warm → sequential single POSTs → optional qty bump.

    Never parallelize cart_items. On 429, wait (Retry-After or growing backoff).
    """
    tcin = item.tcin or ""
    if not tcin:
        return ApiAtcResult(False, 0, "no_tcin", error="missing tcin")
    want = max(1, int(desired_qty or item.max_quantity or 1))

    warm = warm_cart_session(driver)
    print(
        f"[ATC] cart warm status={warm.get('status')} "
        f"cart_id={warm.get('cart_id')} items={warm.get('item_count')}"
    )

    last = ApiAtcResult(False, 0, "none")
    for wave in range(1, max_waves + 1):
        last = cart_api_burst(driver, tcin=tcin, quantity=1, wave=wave)
        print(
            f"[ATC] try={wave} ok={last.ok} status={last.status} "
            f"variant={last.variant} err={last.error!r} "
            f"retry_after={last.retry_after}"
        )
        if last.ok:
            if want > 1:
                time.sleep(0.35)  # don't immediately double-hit cart
                bump = cart_api_add(
                    driver, tcin=tcin, quantity=want, variant=last.variant or "tempo"
                )
                print(
                    f"[ATC] qty bump → {want} ok={bump.ok} status={bump.status} "
                    f"err={bump.error!r}"
                )
            return last

        if last.status == 429:
            wait = last.retry_after if last.retry_after and last.retry_after > 0 else (
                1.2 + 0.9 * wave
            )
            wait = min(8.0, float(wait))
            print(f"[ATC] 429 — backing off {wait:.1f}s (do not parallelize)")
            time.sleep(wait)
            continue
        if last.status == 401:
            # Often "access denied" under allocation / bot edge — rotate variant, pause.
            print("[ATC] 401 access denied — rotate variant, short pause")
            time.sleep(0.8 + 0.2 * wave)
            continue
        if last.status in (503, 502, 500, 0):
            time.sleep(0.4 * wave)
            continue
        time.sleep(0.25)
    return last
