# Target bot

Poll Target product pages, add to cart, checkout (shipping preferred, pickup fallback), optionally place an order.

Code lives in `scalping/bots/target/`.

## Tonight (live buy)

```bash
./scripts/session-target.sh --check
./scripts/run-target.sh --config configs/target/tonight.json --place-order --max-attempts 50
```

Config: `configs/target/tonight.json` — First Partner S3 `A-1011960739`, qty **2**, `place_order: true`, Place order clicker window 30 min.

Manual login: `uv run python sessions/wait_for_login.py`  
Burned profile: `rm -rf ~/.scalping/chrome-profiles/target` then wait_for_login.

## Consensus buy path (Discord Aug 2026)

Community consensus under saturation (not our own confirmed run):

1. **Mobile app** — spam ATC until line(s) land in cart (native SDK trust >> desktop web ATC).
2. **PC** — open `https://www.target.com/checkout` on the same account.
3. **F5** until the cart hydrates / Place order is visible.
4. **Free Mouse Clicker** on Place order (settings shared in Buy-Success).

Split layers: mash ATC on **mobile**, mash Place order on **PC after cart exists**. Desktop web `401 T83072242` is a different problem (do not treat every `401` as Shape).

Bot mirrors steps 2–4 once a cart line exists. Mobile ATC remains manual / app-side for now (emulator plan shelved).

## Shape / F5 + commerce model (research Aug 2026)

### Session trust is multi-signal — not a magic header

“Shape session trust” = correlated client state, not one cookie/token/header:

\[
Decision_r = f(telemetry_r,\ client\ session,\ cookie\ age,\ sequence,\ fingerprints,\ endpoint\ policy,\ recent\ behavior)
\]

F5 web: JS collects telemetry → attach as **headers and/or POST body** → evaluate at edge before origin. Correlates via client token (shared for protected requests from a page that ran the JS), bot cookie + age, fingerprints, referer, channel, endpoint policy.

F5 mobile SDK: separate stack; unique token **per header set**; client token ~4h window; init early or race.

**`TELEM_BEFORE_ATC` vs `TELEM_AFTER_ATC`** is a real race: F5 documents legitimate “Token Missing” when the protected request fires before JS loads. Warm vs cold cache changes timing. Telemetry may live in the body — header-name-only captures can miss it.

**Never infer `TOKEN_MISSING` from HTTP status alone** — that label needs operator-side diagnostics. Edge can return app-like `401` / invalid-credential / spinner / `202`. A client HAR shows sequencing and correlations; it usually **cannot prove** which internal layer produced a generic denial.

Historical Target `ssx.mod.js` / `X-GyJwza5Z-*` = dated snapshots, not current proof. Header harvest/replay is a weak model.

Web ≠ app: separate policies/infra possible; success on one surface does not imply the other.

### Commerce states (cart ≠ order)

| State | Means | Does not mean |
|-------|--------|----------------|
| `PRODUCT_VISIBLE` | Offer on PDP | Real-time allocation |
| `ATC_ACCEPTED` / `CART_MUTATED` | Cart write / read shows item | Inventory reservation |
| `CHECKOUT_ENTERED` | Checkout UI loaded | Payment or accept |
| `PLACE_ORDER_SUBMITTED` | Client sent place-order | Processing succeeded |
| `PLACE_ORDER_HANG` | Past hang threshold, no terminal result | Cause known |
| `CHECKOUT_BUSY` | Busy / high-demand UI | Shape vs saturation |
| `CART_EVICTED` | Item left cart without us removing it | Edge deleted it |
| `PAYMENT_AUTH_PENDING` | Bank/wallet hold | Order confirmed (Target: hold ≠ charge) |
| `PAYMENT_DECLINED` | Explicit issuer/payment fail | Bot block |
| `ORDER_ACKNOWLEDGED` | Request received | Accepted / available / shipped |
| `ORDER_CONFIRMED` | Confirmation + order id | Final fulfillment |
| `BUSINESS_RULE_DENIED` | Qty / address / account policy message | Shape |

Saturation layers that look similar: edge mitigation, auth/session, cart/inventory reconciliation, checkout queue, payment/order commit. OWASP: holding scarce goods without purchase ≠ DoS of servers.

### Attribution cheat sheet

| Cluster | Lean edge/telemetry | Lean commerce |
|---------|---------------------|---------------|
| ATC before suspected telemetry; fast deny; no cart change | Higher | Still possible |
| Cart OK, later only scarce SKU vanishes | Weak | Inventory / cart reconcile |
| Broad slow/busy across checkout | Possible | Saturation / dependency |
| Explicit payment decline / qty message | Weak | Payment / business rule |
| Generic `401` / `T83072242` | **Indeterminate** | **Indeterminate** |
| Auth hold, empty order history | Low value | Matches Target pre-auth docs |

Log with **confidence + alternatives**; don’t code Discord “bot protection” as fact.

### Our live probes

| Signal | Result |
|--------|--------|
| Redsky stock | Often works while writes fail |
| Desktop `cart_items` | Repeated `401 T83072242` — indeterminate layer; weak vs mobile ATC consensus |
| Mobile ATC | Consensus path for cart under drop load |
| PC `/checkout` + F5 | Cart may hydrate late; then Place order clicker |
| Qty | Prefer **2** for FP3; set **after** Shipping cell on web |
| Login / Sign in to buy | Soft-block from OTP spam; don’t auto Sign-in if ATC exists |

### Bot strategy (ops)

1. Prefer **mobile cart** when desktop ATC is denied; same account on PC for checkout.
2. PC: `/checkout` → reload until Place order ready → clicker-paced Place order (stop on sold-out / `CART_EVICTED`).
3. Desktop ATC: in-page only after buy-box ready; paced cool on `AUTH_DENIED`; no header forge.
4. Outcome codes above in logs so edge vs commerce stay separated.

### Useful next measurements (when you want data)

- Control-SKU HAR: `TELEM_BEFORE_ATC` rate vs outcome (header **and** body shape, names only).
- Cookie-name / age bands at ATC — not values.
- Web vs app symptom matrix (separate channels).

## Configs

| File | Use |
|------|-----|
| [`configs/target/default.json`](../configs/target/default.json) | Default product set |
| `tonight.json` | Drop-night SKU / qty / Place-order window |
| `smoke.json` / `shipping-smoke.json` | ATC / shipping dry-runs |
| `live-buy.json` | Intentional buy profile |

| Field | Meaning |
|-------|---------|
| `items[].url` | Target PDP (`/A-TCIN`) |
| `items[].max_quantity` | PDP qty target (FP3 consensus used **2**) |
| `place_order_spam_seconds` | Place order clicker window after cart (default 1800) |
| `dry_run` / `place_order` | Safety |

## Session

```bash
./scripts/session-target.sh
./scripts/session-target.sh --check
./scripts/session-target.sh --force
```

Profile: `~/.scalping/chrome-profiles/target`. Uses `GMAIL_LOGIN` + `GMAIL_APP_PASSWORD`.

## Run

```bash
./scripts/run-target.sh --config configs/target/tonight.json --place-order --max-attempts 50
uv run python -m scalping.bots.target.hunt_data --config configs/target/tonight.json --no-checkout
```

Logs: `~/.scalping/logs/target/`.

## Modules

| Module | Role |
|--------|------|
| `api.py` | Redsky + sequential cart API (sparse backup only) |
| `stock.py` | Buy-box stock + UI-first ATC |
| `checkout.py` | Cart, auth, `/checkout` F5 hydrate, Place order clicker |
| `session.py` | Login / cookies |
| `cli.py` | Poll loop |

See [CONFIGURATION.md](CONFIGURATION.md) for `.env` fields.
