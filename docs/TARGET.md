# Target bot

Poll Target product pages, add to cart, checkout (shipping preferred, pickup fallback), optionally place an order.

Code lives in `scalping/bots/target/`.

## Quick start

```bash
cp .env.example .env
./scripts/session-target.sh
./scripts/run-target.sh
```

Real purchase:

```bash
./scripts/run-target.sh --config configs/target/tonight.json --place-order
# or
./scalping/run.sh run target -- --config configs/target/tonight.json --place-order
```

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

## Session

```bash
./scripts/session-target.sh
./scripts/session-target.sh --check
./scripts/session-target.sh --force
```

Uses `GMAIL_LOGIN` + `GMAIL_APP_PASSWORD`. Profile: `~/.scalping/chrome-profiles/target`.

## Run flags

```bash
./scripts/run-target.sh
./scripts/run-target.sh --sequential
./scripts/run-target.sh --parallel
./scripts/run-target.sh --max-attempts 3
./scripts/run-target.sh --place-order
./scripts/run-target.sh --no-clear-cart
```

## Modules

| Module | Role |
|--------|------|
| `scalping/bots/target/config.py` | Load JSON + `.env` |
| `scalping/bots/target/stock.py` | Stock + ATC |
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
