# Premium Bandai US bot

Login → poll stock → add to cart → Global-E checkout with the card saved on the Bandai account.

Code lives in `scalping/bots/bandai/`.

## Quick start

```bash
./scripts/run-bandai.sh --ensure-session
./scripts/run-bandai.sh --probe
./scripts/run-bandai.sh --dry-run
./scripts/run-bandai.sh --place-order --item-id F2621339003
```

```bash
./scalping/run.sh run bandai -- --probe
./scalping/run.sh run bandai -- --config configs/bandai/default.json --dry-run
```

## Config

[`configs/bandai/default.json`](../configs/bandai/default.json)

| Field | Meaning |
|-------|---------|
| `item_url` / `item_id` | Product to buy (default test SKU `N2904549002`) |
| `qty` | Quantity (usually 1) |
| `dry_run` | Stop on checkout before placing the order |
| `place_order` | Place a real order when true |
| `refresh_interval_seconds` | Stock poll interval |
| `max_atc_retries` | HTTP ATC retries after stock is seen |

Prefer the configured SKU when it is in stock. If it is OOS / login-gated, pass another cheap in-stock id:

```bash
./scripts/run-bandai.sh --dry-run --item-id F2621339003
```

## Env

| Variable | Purpose |
|----------|---------|
| `BANDAI_USERNAME` | Premium Bandai email / member id |
| `BANDAI_PASSWORD` | Account password |
| `BANDAI_CARD_CVV` | Optional; only if Global-E re-asks CVV for the saved card |

Never commit `.env` or `~/.scalping/sessions/bandai_cookies.json`.

## Modules

| Module | Role |
|--------|------|
| `session.py` | Browser login (`POST /login` + member check) + cookie save |
| `api.py` | HTTP stock / ATC / cart / checkout kickoff |
| `checkout.py` | Cart → `/orderdetails` Global-E payment → place order |
| `cli.py` | CLI orchestration |
| `bot.py` | Platform adapter (`@register_bot("bandai")`) |

## Runtime paths

| Path | Use |
|------|-----|
| `~/.scalping/chrome-profiles/bandai` | Chrome profile |
| `~/.scalping/sessions/bandai_cookies.json` | Cookie dump |
| `~/.scalping/logs/bandai/` | Stop-page HTML / text captures |

## Page captures

Whenever the bot stops on a page (dry-run, ATC failure, order error, timeout, or order complete), it writes:

- `*.html` — parent page HTML
- `*.txt` — visible text
- `*.json` — url / alerts / paths
- `*.globale.html` / `*.globale.txt` — Global-E checkout iframe (when present)

Inspect those files if checkout stops unexpectedly.

## Discovered APIs

| Call | Path |
|------|------|
| Member | `GET /api/context/member` |
| Product / stock | `GET /api/products/{code}` |
| Add to cart | `POST /api/cart/addToCart` `[{areaItemNo, qty}]` |
| Cart | `GET /api/cart/detail` |
| Start checkout | `POST /api/cart/{cartSn}/checkout` |
| Finish after Global-E | `POST /api/checkout/{sn}/preComplete` |

Payment UI is Global-E (`webservices.global-e.com/Checkout/...`) inside `/us/orderdetails`.

## Safety

- Default config: `dry_run: true`, `place_order: false`
- Real charges only with `--place-order` (or `place_order: true` in config)
