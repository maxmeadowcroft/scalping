# scalping

Botasaurus-powered stock monitors that buy limited Target drops as soon as they
come back in stock.

**Project path:** `/Users/max/Dev/Scalping`  
Keep this off Desktop/Documents/Downloads — macOS privacy protections break
Botasaurus/`getcwd()` there.

---

## What it does

For each enabled product URL in [`scraping/configuration.json`](scraping/configuration.json):

1. **Clear cart** — so previous dry-runs / other items do not mix into checkout.
2. **Open the PDP** and classify stock (`in_stock` / `out_of_stock` / `unknown`).
3. **If OOS or unknown** — sleep `refresh_interval_seconds` (+ jitter), reload, repeat.
4. **If in stock** — set quantity, click Ship it / Order Pickup / Add to cart.
5. **Checkout**
   - Prefer **Order Pickup** at `preferred_store_name` when available.
   - Otherwise **Shipping** to `shipping_address` (default: 3604 Garcia St NE, Albuquerque NM 87111).
6. **Checkout auth** — Target often asks for Face ID / passkey / emailed code before
   checkout. The bot requests a code and waits (type it in the browser, or set
   `TARGET_OTP` in `.env` while it waits).
7. **Payment + place order** — only when `--place-order` / `place_order: true`.
   Uses saved Target card + `CARD_CVV` by default, or full `CARD_*` to add a card.
8. **Safety stop** — with `dry_run: true` (default) the bot never clicks **Place order**.

Items are handled independently: as soon as one is buyable it is purchased;
other OOS items keep refreshing.

```
configuration.json
        │
        ▼
┌───────────────────┐     OOS / unknown      ┌──────────────┐
│  open PDP / reload │ ─────────────────────► │ sleep+jitter │
└─────────┬─────────┘                         └──────┬───────┘
          │ in stock                                 │
          ▼                                          │
┌───────────────────┐                                │
│  add to cart      │◄───────────────────────────────┘
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ pickup or ship    │
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ dry_run? stop     │──yes──► leave review page open
│ else place order  │
└───────────────────┘
```

---

## Setup

```bash
cd ~/Dev/Scalping
uv sync --all-groups
./sessions/run_target_session.sh   # log in to Target once (2FA ok)
cp .env.example .env               # only needed for real orders
```

Session / Chrome profile / cookie dump live under `~/.scalping/`:

| Path | Purpose |
|------|---------|
| `~/.scalping/chrome-profiles/target` | Logged-in Chrome profile reused by the bot |
| `~/.scalping/chrome-profiles/target-parallel/` | Cloned profiles for `--parallel` |
| `~/.scalping/sessions/target_cookies.json` | Cookie backup from session capture |

---

## Configure products

Edit [`scraping/configuration.json`](scraping/configuration.json):

| Field | Meaning |
|-------|---------|
| `items[].url` | Target product URL (`/A-TCIN`) |
| `items[].max_quantity` | Desired qty on ATC |
| `items[].label` | Short name in logs |
| `items[].enabled` | `false` to skip without deleting |
| `refresh_interval_seconds` | Base wait between OOS polls |
| `refresh_jitter_seconds` | Extra random 0..N seconds per poll |
| `max_atc_retries` | ATC click retries before giving up on that item |
| `prefer_pickup` | Prefer Order Pickup when the cart offers it |
| `preferred_store_name` | Store label to select (e.g. `Albuquerque Wyoming`) |
| `shipping_address` | Fallback ship-to |
| `dry_run` / `place_order` | Keep dry_run true until you intentionally buy |

Example items already in the config (handy for dry-run testing):

- In-stock snack: Drizzilicious lemon rice cake (`A-95049011`)
- Often OOS collectible: One Piece starter deck (`A-95120838`)

---

## Run the bot

```bash
./scraping/run_bot.sh                  # dry-run; 2+ items → parallel browsers
./scraping/run_bot.sh --sequential     # one browser; item 2 waits for item 1
./scraping/run_bot.sh --parallel       # force one browser per item
./scraping/run_bot.sh --max-attempts 3 # stop after 3 polls per item
./scraping/run_bot.sh --place-order    # REAL purchase — needs .env CARD_*
./scraping/run_bot.sh --no-clear-cart  # keep existing cart lines (rare)
```

With multiple enabled items, each product gets its **own Chrome window** (cloned
logged-in profile) so an in-stock buy never blocks OOS refresh on another item.
Omit `--max-attempts` to poll the OOS PDPs forever.

Equivalent module form:

```bash
uv run python -m scraping --max-attempts 2
uv run python -m scraping.target_bot --parallel
```

### Status meanings

| Status | Meaning |
|--------|---------|
| `out_of_stock` | PDP showed OOS / sold out (keeps polling unless max-attempts hit) |
| `unknown` | No clear buy-box signal (keeps polling) |
| `ready` | Reached checkout review; dry-run stopped (or place_order failed softly) |
| `purchased` | Place order was clicked |
| `atc_failed` | Could not click Add to cart after retries — stops that item |

---

## Payment & contact (.env)

Copy [`.env.example`](.env.example) → `.env` (gitignored). Your card is **never**
stored in git.

### Minimum to actually buy (saved Target card)

If your card is already on the Target account (usual case):

```
USE_SAVED_CARD=true
CARD_CVV=123
SHIPPING_NAME=Maximus Lastname
SHIPPING_PHONE=5055551234
```

Then run:

```bash
./scraping/run_bot.sh --place-order
```

When Target’s checkout sign-in modal appears, the bot clicks **Get a code** and
reads the emailed OTP from Gmail (`GMAIL_LOGIN` + `GMAIL_APP_PASSWORD`). You can
still complete Face ID / passkey manually, or paste `TARGET_OTP` into `.env`.

### Full card (add / replace card at checkout)

```
USE_SAVED_CARD=false
CARD_NUMBER=4111111111111111
CARD_HOLDER_NAME=Maximus Lastname
CARD_EXPIRATION_DATE=12/28
CARD_CVV=123
CARD_TYPE=visa
```

Optional billing overrides (defaults to shipping if blank):

```
BILLING_STREET=3604 Garcia St NE
BILLING_CITY=Albuquerque
BILLING_STATE=NM
BILLING_ZIP=87111
BILLING_PHONE=5055551234
```

### Field reference

| Variable | Required? | Purpose |
|----------|-----------|---------|
| `CARD_CVV` | **Yes** for `--place-order` | Security code (saved or new card) |
| `USE_SAVED_CARD` | Recommended `true` | Only fill CVV on a card already in Target wallet |
| `CARD_NUMBER` | If not using saved card | Digits only or spaces ok |
| `CARD_HOLDER_NAME` | If not using saved card | Name on card |
| `CARD_EXPIRATION_DATE` | If not using saved card | `MM/YY` (e.g. `12/28`) |
| `CARD_TYPE` | Optional | `visa` / `mastercard` / `amex` / … |
| `SHIPPING_NAME` | Recommended | Pickup contact / ship-to name |
| `SHIPPING_PHONE` | Recommended | Required by Target on many checkouts |
| `SHIPPING_STREET`…`ZIP` | Optional | Overrides `configuration.json` address |
| `BILLING_*` | Optional | Used when adding a new card |
| `GMAIL_LOGIN` | Recommended | Gmail address that receives Target codes |
| `GMAIL_APP_PASSWORD` | Recommended | Google [App Password](https://myaccount.google.com/apppasswords) (not your normal password) |
| `TARGET_OTP` | Optional fallback | Paste emailed checkout code while bot waits |
| `CHECKOUT_AUTH_TIMEOUT_SECONDS` | Optional | Default `300` |

Without `CARD_CVV` (and full card details when `USE_SAVED_CARD=false`),
`--place-order` reaches checkout and reports missing payment — it will not guess.

---

## Tests

```bash
# Fast unit tests (no browser)
uv run python -m pytest tests/test_config_and_stock.py -q

# Live dry-run against Target (needs captured session)
uv run python -m pytest tests/test_target_live.py -m live -s
```

Live tests force `dry_run` — they never place an order.

---

## Module map

| Module | Role |
|--------|------|
| [`scraping/config.py`](scraping/config.py) | Load / validate JSON + `.env` |
| [`scraping/runtime.py`](scraping/runtime.py) | `~/.scalping` dirs, parallel profile clones |
| [`scraping/target_stock.py`](scraping/target_stock.py) | Stock classification + ATC |
| [`scraping/gmail_otp.py`](scraping/gmail_otp.py) | Read Target email OTP via Gmail IMAP |
| [`scraping/target_checkout.py`](scraping/target_checkout.py) | Clear cart, auth, fulfillment, checkout |
| [`scraping/target_bot.py`](scraping/target_bot.py) | Poll loop + CLI |
| [`sessions/target.py`](sessions/target.py) | One-time login capture |

---

## macOS permissions (optional)

System Settings → Privacy & Security → **Full Disk Access** for Cursor / Terminal
is only needed if you insist on keeping the repo under Desktop/Documents.
Prefer `~/Dev/Scalping`.

---

## Notes / limits

- Target UI changes; selectors are best-effort with text fallbacks.
- Parallel mode clones the Chrome profile — re-run session capture if login
  expires, then delete `~/.scalping/chrome-profiles/target-parallel/` if clones
  go stale.
- Use responsibly and within Target’s terms; this is for personal restock buys
  on your own account.
