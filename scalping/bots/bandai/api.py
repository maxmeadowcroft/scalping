"""Premium Bandai US HTTP APIs (stock / ATC / cart / checkout kickoff).

Discovered under authenticated session (Vue SPA):

  GET  /api/products/{productCode}
  GET  /api/context/member
  GET  /api/cart/detail
  GET  /api/cart/summary
  POST /api/cart/addToCart          body: [{areaItemNo, qty, ...}]
  POST /api/cart/{cartSn}/checkout  → {checkoutSn} then /us/orderdetails (Global-E)
  POST /api/checkout/{sn}/preComplete  (after Global-E confirmation)

Headers: X-CSRF-TOKEN, X-G1-Area-Code: us, X-Requested-With: XMLHttpRequest
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any

from botasaurus.browser import Driver, browser

from scalping.bots.bandai.runtime import (
    CHROME_ADD_ARGUMENTS,
    COOKIES_PATH,
    PROFILE_DIR,
    prepare_runtime,
)
from scalping.core.http import DEFAULT_UA
from scalping.core.paths import bandai_config

ORIGIN = "https://p-bandai.com"
AREA = "us"
PRODUCT_RE = re.compile(r"/item/([A-Za-z0-9_-]+)")


@dataclass
class BandaiConfig:
    item_url: str = "https://p-bandai.com/us/item/N2904549002"
    item_id: str = "N2904549002"
    qty: int = 1
    label: str = "bandai"
    dry_run: bool = True
    place_order: bool = False
    refresh_interval_seconds: float = 0.25
    refresh_jitter_seconds: float = 0.05
    max_atc_retries: int = 5
    max_attempts: int = 0  # 0 = forever
    poll_timeout_seconds: float = 0.0

    def product_code(self) -> str:
        if (self.item_id or "").strip():
            return self.item_id.strip()
        m = PRODUCT_RE.search(self.item_url or "")
        return m.group(1) if m else ""


def load_bandai_config(path: Path | None = None) -> BandaiConfig:
    cfg_path = path or bandai_config("default")
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    return BandaiConfig(
        item_url=str(raw.get("item_url") or BandaiConfig.item_url),
        item_id=str(raw.get("item_id") or ""),
        qty=int(raw.get("qty") or 1),
        label=str(raw.get("label") or "bandai"),
        dry_run=bool(raw.get("dry_run", True)),
        place_order=bool(raw.get("place_order", False)),
        refresh_interval_seconds=float(raw.get("refresh_interval_seconds") or 0.25),
        refresh_jitter_seconds=float(raw.get("refresh_jitter_seconds") or 0.05),
        max_atc_retries=int(raw.get("max_atc_retries") or 5),
        max_attempts=int(raw.get("max_attempts") or 0),
        poll_timeout_seconds=float(raw.get("poll_timeout_seconds") or 0.0),
    )


def _cookie_from_dict(d: dict[str, Any]) -> Cookie | None:
    name = d.get("name")
    value = d.get("value")
    if not name or value is None:
        return None
    domain = str(d.get("domain") or ".p-bandai.com")
    path = str(d.get("path") or "/")
    secure = bool(d.get("secure", True))
    expires = d.get("expiry") or d.get("expires")
    rest = {"HttpOnly": bool(d.get("httpOnly"))}
    return Cookie(
        version=0,
        name=str(name),
        value=str(value),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=secure,
        expires=int(expires) if expires else None,
        discard=False,
        comment=None,
        comment_url=None,
        rest=rest,
        rfc2109=False,
    )


class BandaiApi:
    """Cookie-jar HTTP client for Bandai US APIs."""

    def __init__(self, cookies_path: Path | None = None) -> None:
        self.cookies_path = cookies_path or COOKIES_PATH
        self.jar = CookieJar()
        self.csrf: str = ""
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self._load_cookies()

    def _load_cookies(self) -> None:
        if not self.cookies_path.exists():
            return
        try:
            payload = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        except Exception:
            return
        raw = payload.get("cookies")
        if not isinstance(raw, list):
            return
        for row in raw:
            if not isinstance(row, dict):
                continue
            c = _cookie_from_dict(row)
            if c is not None:
                try:
                    self.jar.set_cookie(c)
                except Exception:
                    pass

    def cookie_header(self) -> str:
        parts = []
        for c in self.jar:
            parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)

    def _headers(self, *, form: bool = False) -> dict[str, str]:
        h = {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": ORIGIN,
            "Referer": f"{ORIGIN}/{AREA}/",
            "X-Requested-With": "XMLHttpRequest",
            "X-G1-Area-Code": AREA,
            "Accept-Language": "en-US,en;q=0.9",
        }
        if form:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            h["Content-Type"] = "application/json"
        if self.csrf:
            h["X-CSRF-TOKEN"] = self.csrf
        cookie = self.cookie_header()
        if cookie:
            h["Cookie"] = cookie
        return h

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        form_body: dict[str, str] | None = None,
        timeout: float = 25.0,
    ) -> tuple[int, Any, dict[str, str]]:
        url = path if path.startswith("http") else f"{ORIGIN}{path}"
        data: bytes | None = None
        headers = self._headers(form=form_body is not None)
        if form_body is not None:
            data = urllib.parse.urlencode(form_body).encode("utf-8")
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method.upper())
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                csrf = hdrs.get("x-csrf-token")
                if csrf:
                    self.csrf = csrf
                if not raw:
                    return resp.status, None, hdrs
                try:
                    return resp.status, json.loads(raw.decode("utf-8")), hdrs
                except json.JSONDecodeError:
                    return resp.status, raw.decode("utf-8", errors="replace"), hdrs
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
            csrf = hdrs.get("x-csrf-token")
            if csrf:
                self.csrf = csrf
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = raw.decode("utf-8", errors="replace")
            return exc.code, body, hdrs

    def bootstrap_csrf(self) -> str:
        """Hit home HTML and scrape USER_DATA.csrfToken if cookies alone lack it."""
        status, data, _ = self.request("GET", f"/{AREA}/")
        if isinstance(data, str):
            m = re.search(r'csrfToken["\']?\s*[:=]\s*["\']([^"\']+)', data)
            if m:
                self.csrf = m.group(1)
        # Prefer member endpoint which also returns csrf header
        status, data, hdrs = self.request("GET", "/api/context/member")
        if self.csrf:
            return self.csrf
        if isinstance(data, dict) and data.get("csrfToken"):
            self.csrf = str(data["csrfToken"])
        return self.csrf

    def member_context(self) -> tuple[int, Any]:
        return self.request("GET", "/api/context/member")[:2]

    def is_logged_in(self) -> bool:
        status, data = self.member_context()
        if status != 200 or not isinstance(data, dict):
            return False
        member = data.get("loggedInMember") or {}
        return bool(isinstance(member, dict) and member.get("isLoggedIn") is True)

    def get_product(self, product_code: str) -> tuple[int, Any]:
        code = urllib.parse.quote(product_code, safe="")
        return self.request("GET", f"/api/products/{code}")[:2]

    def product_stock(self, product: dict[str, Any]) -> dict[str, Any]:
        """Normalize stock fields from product detail payload."""
        info = product.get("infoSection") or product.get("info") or {}
        general = info.get("generalProdInfo") or {}
        qty_info = info.get("quantityInfo") or {}
        area_nos = product.get("areaItemNos") or []
        out_of_stock = bool(general.get("outOfStock"))
        availability = general.get("availabilityStatus") or info.get("availabilityStatus")
        purchase_available = product.get("purchaseAvailable")
        inv_map = product.get("areaItemInventoryInfoMap") or {}
        has_inventory = False
        for v in inv_map.values() if isinstance(inv_map, dict) else []:
            if isinstance(v, dict):
                if any(int(x or 0) > 0 for x in v.values()):
                    has_inventory = True
                    break
            elif isinstance(v, (int, float)) and int(v) > 0:
                has_inventory = True
                break
        # purchaseAvailable can be false on limited drops even when inventory > 0;
        # treat OOS=false + areaItemNos + inventory as purchasable for polling.
        if purchase_available is True:
            purchasable = not out_of_stock and bool(area_nos)
        elif purchase_available is False and not has_inventory:
            purchasable = False
        else:
            purchasable = (not out_of_stock) and bool(area_nos) and (
                has_inventory or purchase_available is not False
            )
        if availability and str(availability).upper() in {
            "OUT_OF_STOCK",
            "SOLDOUT",
            "SOLD_OUT",
            "UNAVAILABLE",
        }:
            purchasable = False
        return {
            "product_code": product.get("productCode") or product.get("product_code"),
            "area_item_nos": list(area_nos),
            "out_of_stock": out_of_stock,
            "availability": availability,
            "purchase_available": purchase_available,
            "purchasable": purchasable,
            "quantity_info": qty_info,
            "inventory": product.get("areaItemInventoryInfoMap"),
            "name": (info.get("productName") or {}).get("en")
            or info.get("productName")
            or product.get("productName"),
        }

    def add_to_cart(
        self, *, area_item_no: str, qty: int = 1, pickup_sn: int | None = None
    ) -> tuple[int, Any]:
        item: dict[str, Any] = {"areaItemNo": area_item_no, "qty": qty}
        if pickup_sn is not None:
            item["eventPickupSpecifiedPickupSn"] = pickup_sn
        return self.request("POST", "/api/cart/addToCart", json_body=[item])[:2]

    def cart_detail(self) -> tuple[int, Any]:
        return self.request("GET", "/api/cart/detail")[:2]

    def cart_summary(self) -> tuple[int, Any]:
        return self.request("GET", "/api/cart/summary")[:2]

    def proceed_checkout(
        self,
        *,
        cart_sn: str | int,
        merchant_cart_token: str,
        items: list[dict[str, Any]],
        shipping_area_code: str | None = None,
        default_area_code: str | None = None,
    ) -> tuple[int, Any]:
        body = {
            "merchantCartToken": merchant_cart_token,
            "shippingAreaCode": shipping_area_code,
            "defaultAreaCode": default_area_code,
            "items": items,
        }
        return self.request("POST", f"/api/cart/{cart_sn}/checkout", json_body=body)[:2]

    def pre_complete(self, checkout_sn: int | str, payload: dict[str, Any]) -> tuple[int, Any]:
        return self.request(
            "POST", f"/api/checkout/{checkout_sn}/preComplete", json_body=payload
        )[:2]


def probe_apis(config: BandaiConfig) -> dict[str, Any]:
    """CLI --probe: exercise member + product + cart endpoints."""
    api = BandaiApi()
    api.bootstrap_csrf()
    out: dict[str, Any] = {
        "csrf": bool(api.csrf),
        "cookie_count": sum(1 for _ in api.jar),
    }
    st, member = api.member_context()
    out["member"] = {
        "status": st,
        "logged_in": api.is_logged_in(),
        "restricted": (member or {}).get("restrictedType")
        if isinstance(member, dict)
        else None,
    }
    code = config.product_code()
    st, product = api.get_product(code)
    stock = api.product_stock(product) if isinstance(product, dict) else {}
    out["product"] = {
        "status": st,
        "code": code,
        "stock": stock,
        "error": None if isinstance(product, dict) else str(product)[:300],
    }
    st, cart = api.cart_detail()
    out["cart"] = {
        "status": st,
        "keys": list(cart.keys())[:20] if isinstance(cart, dict) else None,
        "snippet": None
        if isinstance(cart, dict)
        else str(cart)[:300],
    }
    st, summary = api.cart_summary()
    out["cart_summary"] = {"status": st}
    print(json.dumps(out, indent=2, default=str))
    return out


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
def _browser_probe(driver: Driver, data: dict) -> dict[str, Any]:
    """In-browser fetch probe — uses live Chrome profile session."""
    from scalping.bots.bandai.session import (
        dismiss_overlays,
        ensure_signed_in_on_driver,
        save_session,
    )

    code = str(data.get("product_code") or "")
    ensure_signed_in_on_driver(driver, force=False)
    try:
        driver.get(f"https://p-bandai.com/us/item/{code}", wait=1, timeout=35)
    except Exception as exc:
        print(f"[BANDAI] probe PDP warning: {exc}")
    time.sleep(0.8)
    dismiss_overlays(driver)
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
          const get = async (path) => {{
            const res = await fetch(path, {{credentials: 'include', headers}});
            const text = await res.text();
            let body = null;
            try {{ body = JSON.parse(text); }} catch (e) {{ body = text.slice(0, 300); }}
            return {{status: res.status, data: body}};
          }};
          const member = await get('/api/context/member');
          const product = await get('/api/products/' + encodeURIComponent({code!r}));
          const cart = await get('/api/cart/detail');
          const summary = await get('/api/cart/summary');
          const p = product.data || {{}};
          const info = p.infoSection || {{}};
          const general = info.generalProdInfo || {{}};
          const logged = !!(member.data && member.data.loggedInMember
            && member.data.loggedInMember.isLoggedIn);
          return {{
            member_logged_in: logged,
            member_status: member.status,
            product_status: product.status,
            product_code: p.productCode,
            area_item_nos: p.areaItemNos,
            out_of_stock: general.outOfStock,
            purchase_available: p.purchaseAvailable,
            cart_status: cart.status,
            cart_keys: cart.data && typeof cart.data === 'object' ? Object.keys(cart.data) : null,
            summary_status: summary.status,
            endpoints: {{
              product: 'GET /api/products/{{code}}',
              atc: 'POST /api/cart/addToCart',
              cart: 'GET /api/cart/detail',
              checkout: 'POST /api/cart/{{cartSn}}/checkout',
              preComplete: 'POST /api/checkout/{{sn}}/preComplete',
            }},
          }};
        }})();
        """
    )
    save_session(
        driver,
        known_signed_in=bool(isinstance(result, dict) and result.get("member_logged_in")),
        navigate_home=False,
    )
    out = result if isinstance(result, dict) else {"raw": result}
    print(json.dumps(out, indent=2, default=str))
    return out


def probe_browser(config: BandaiConfig) -> dict[str, Any]:
    prepare_runtime()
    return _browser_probe({"product_code": config.product_code()})


def poll_until_in_stock(
    config: BandaiConfig,
    *,
    api: BandaiApi | None = None,
) -> dict[str, Any]:
    """Poll product API until purchasable; return stock summary + product."""
    client = api or BandaiApi()
    if not client.csrf:
        client.bootstrap_csrf()
    code = config.product_code()
    attempt = 0
    started = time.time()
    while True:
        attempt += 1
        st, product = client.get_product(code)
        stock = client.product_stock(product) if isinstance(product, dict) else {}
        purchasable = bool(stock.get("purchasable"))
        print(
            f"[POLL {attempt}] {config.label} {code} status={st} "
            f"purchasable={purchasable} oos={stock.get('out_of_stock')} "
            f"areas={stock.get('area_item_nos')}"
        )
        if st == 200 and purchasable and isinstance(product, dict):
            return {"product": product, "stock": stock, "attempts": attempt}
        if config.max_attempts and attempt >= config.max_attempts:
            raise TimeoutError(f"max_attempts={config.max_attempts} reached for {code}")
        if config.poll_timeout_seconds and (
            time.time() - started >= config.poll_timeout_seconds
        ):
            raise TimeoutError(f"poll timeout for {code}")
        delay = config.refresh_interval_seconds
        if config.refresh_jitter_seconds:
            delay += (time.time() % 1) * config.refresh_jitter_seconds
        time.sleep(max(0.05, delay))


def atc_from_stock(
    api: BandaiApi,
    stock: dict[str, Any],
    *,
    qty: int = 1,
) -> tuple[int, Any]:
    areas = stock.get("area_item_nos") or []
    if not areas:
        raise RuntimeError("no areaItemNo on product — cannot ATC")
    area = str(areas[0])
    return api.add_to_cart(area_item_no=area, qty=qty)
