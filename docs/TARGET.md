# Target bot

Poll Target product pages, add to cart, checkout (shipping preferred, pickup fallback), optionally place an order.

Code lives in `scalping/bots/target/`.

## Tonight (data probe — do not buy)

```bash
./scripts/session-target.sh --check
uv run python -m scalping.bots.target.hunt_data --config configs/target/tonight.json --no-checkout
```

Config: `configs/target/tonight.json` — Pitch Black ETB `A-1011483406`, qty 1, **`dry_run: true` / `place_order: false`**.

Manual login (when soft-blocked): `uv run python sessions/wait_for_login.py`  
Burned profile reset: `rm -rf ~/.scalping/chrome-profiles/target` then wait_for_login again.
## Configs

| File | Use |
|------|-----|
| [`configs/target/default.json`](../configs/target/default.json) | Default product set |
| `tonight.json` | Drop-night SKU / qty |
| `smoke.json` / `shipping-smoke.json` | ATC / shipping dry-runs |
| `live-buy.json` | Intentional buy profile |

### Important fields

| Field | Meaning |
|-------|---------|
| `items[].url` | Target PDP (`/A-TCIN`) |
| `items[].max_quantity` | Desired ATC qty |
| `items[].enabled` | Skip without deleting |
| `refresh_interval_seconds` | Base OOS poll wait |
| `prefer_pickup` | Default **false** (prefer shipping) |
| `preferred_store_name` | Pickup store label |
| `dry_run` / `place_order` | Safety: keep dry_run until you mean it |

## Field notes — 2026-08-07 (live drops)

Logs under `~/.scalping/logs/target/` (not committed).

### Soft-block / session

- Spam Continue / Get a code → Target banner **“Something went wrong on our end”** on login (bot **and** manual in same profile).
- Auto-login must **stop** on that banner; CLI must not crash when Botasaurus returns `null`.
- Fix that worked: wipe `~/.scalping/chrome-profiles/target` + `target_cookies.json`, then `sessions/wait_for_login.py` once. Session `--check` → `signed_in: true`.

### SKUs probed tonight

| TCIN | Product | Stock signals | ATC API | Notes |
|------|---------|---------------|---------|-------|
| `1011209273` | Mega Greninja ex Premium | Often DOM OOS while Redsky `ship=IN_STOCK` | All variants `401 T83072242` | Buy-box lag vs API; `cart_views` warm `200` |
| `1011483406` | Pitch Black ETB | **Stable** DOM + API `IN_STOCK` (~19/19 polls) | **90/90** `401 T83072242` across all variants | `cart_views` warm mostly **200** after early 429; `cart_looks_updated` false-positives skip UI ATC — cart_empty flip-flops |

Affiliate PDP examples: [Greninja `A-1011209273`](https://www.target.com/p/zephyr/-/A-1011209273), [Pitch Black ETB `A-1011483406`](https://www.target.com/p/zephyr/-/A-1011483406).

### Progressive note (~02:20 MT)

Pitch Black stayed buyable for ~15+ minutes with shipping ATC button visible. Direct `cart_items` never cleared AUTH_DENIED. Hunt was over-probing (5 variants × every buyable poll) — throttle ATC probes; stock-only most ticks.

### Chaos Rising earlier (`A-95298172` / blister `95298174`)

Full dump: `~/.scalping/logs/target/deep_probe_95298172_*.json`

| Signal | Result |
|--------|--------|
| Stock | `product_summary_with_fulfillment_v1` + `product_fulfillment_v1` → ship `IN_STOCK` |
| Retired | `pdp_fulfillment_v1` → **410** |
| Real UI ATC body | `channel_id` / `item_channel_id` **`"10"`** on desktop (mobile may use `"90"`) |
| Cart write | `T83072242` / `_ERR_AUTH_DENIED` — PerimeterX (`_px*`) + `__CONFIG__.shape.enabled` |
| Checkout APIs | Guest `pre_checkout` / `checkout` → `403 INVALID_GUEST_STATUS` |
| `cart_views` | `204` empty guest; `200` with cart; can **429** under load |

### Traffic / bot-score patterns

1. **Reads succeed, writes fail** — Redsky stock + often `cart_views` OK; `cart_items` POST returns `_ERR_AUTH_DENIED` even when UI shows Add to cart / signed in.
2. **DOM ≠ API** — drop pages flip; buy-box can stay “Out of stock” while fulfillment API says `IN_STOCK` (and the reverse). Poll both.
3. **Shape + PX** — `__CONFIG__.shape.enabled`; cookies include `_pxhd` / related. Payload shape alone doesn’t clear AUTH_DENIED.
4. **Self-inflicted pressure** — parallel ATC / login spam worsens soft-block and 429s. Gentle poll + spaced ATC probes.
5. **Desktop UA** — prefer real Chrome UA + channel `"10"`; mobile UA spoof on Chromium correlated with worse cart scores earlier.

### How to improve (priority)

1. **Healthy signed-in session** — don’t spam login; wipe profile if soft-blocked.
2. **UI-first ATC** with fetch hook — capture real `cart_items` request/response; API variants only as sparse backup.
3. **Dual stock** — Redsky + buy-box; don’t trust one.
4. **429 / AUTH_DENIED** — long cool + PDP reload for sensors; never parallel `cart_items`.
5. **Verify cart** — require cart line / badge, not `cart_looks_updated` alone after 401 storms.
6. **Data hunt** — `hunt_data --no-checkout`; keep `place_order: false` unless explicitly buying.
## Session

```bash
./scripts/session-target.sh
./scripts/session-target.sh --check
./scripts/session-target.sh --force
```

Uses `GMAIL_LOGIN` + `GMAIL_APP_PASSWORD`. Profile: `~/.scalping/chrome-profiles/target`.

**Do not spam login.** If Target shows “Something went wrong on our end”, wait several minutes and run `session-target.sh --force` **once**. Auto-login now stops on that banner (max ~3 gentle Get a code clicks). Diagnose / hunt_data skip auto-login on purpose.


## Run flags

```bash
./scripts/run-target.sh
./scripts/run-target.sh --sequential
./scripts/run-target.sh --parallel
./scripts/run-target.sh --max-attempts 3
./scripts/run-target.sh --place-order
./scripts/run-target.sh --no-clear-cart
```

## Drop ATC strategies

Under load, desktop UI “Add to cart” often clicks but never lands (same failure mode many people hit on drops). The bot uses several paths that share the signed-in Chrome session cookies:

1. **Cart API (sequential)** (`POST carts.target.com/web_checkouts/v1/cart_items`)  
   Prefer live PDP shape: desktop `channel_id` / `item_channel_id` **`"10"`** (captured from real Add to cart).  
   Fallbacks: Tempo / `"90"` / `fulfillment_test_mode`. Rotate one request at a time.  
   Always try **qty=1 first**, then bump.  
   Do **not** send `fulfillment.type: SHIPPING`. Do **not** parallelize POSTs (self-429).
2. **`T83072242` / `_ERR_AUTH_DENIED`** — Target bot/edge block (same family as login blocks).  
   `cart_views` can still be 200. Cool down; prefer real UI click (same payload path).
3. **Warm `cart_views`** before ATC.
4. **429 backoff** — wait 1–8s, then UI.
5. **Desktop Chrome UA** + buy-box UI first (avoid mobile spoof on Chromium).
6. **Soft-fail ATC** — keep polling.
7. **Stock via Redsky in-browser**.

Diagnose dump: `uv run python -m scalping.bots.target.diagnose --config configs/target/tonight.json`  
Logs: `~/.scalping/logs/target/`.

Code: `scalping/bots/target/api.py` (cart + redsky), wired from `stock.add_to_cart` and `cli` poll loop.

## Modules

| Module | Role |
|--------|------|
| `scalping/bots/target/config.py` | Load JSON + `.env` |
| `scalping/bots/target/api.py` | Cart API burst + Redsky stock poll |
| `scalping/bots/target/stock.py` | Buy-box stock + ATC orchestration |
| `scalping/bots/target/checkout.py` | Cart, auth, fulfillment, checkout |
| `scalping/bots/target/session.py` | Login / cookies |
| `scalping/bots/target/cli.py` | Poll loop + CLI |
| `scalping/bots/target/bot.py` | Platform adapter |

## Tests

```bash
uv run python -m pytest tests/test_config_and_stock.py -q
uv run python -m pytest tests/test_target_live.py -m live -s
```

See [CONFIGURATION.md](CONFIGURATION.md) for `.env` fields.
