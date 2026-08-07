# Configuration & secrets

## JSON configs (`configs/`)

| Path | Bot |
|------|-----|
| `configs/target/*.json` | Target |
| `configs/round1/*.json` | Round1 |

Pass any file with `--config`:

```bash
./scripts/run-target.sh --config configs/target/tonight.json
```

Defaults resolve via `scalping.core.paths` (`target_config()`, `round1_config()`).

Always use paths under `configs/` (e.g. `configs/target/default.json`).

## Secrets (`.env`)

Copy `.env.example` → `.env` (gitignored). Never commit real card or app-password values.

### Target — buy

| Variable | Required? | Purpose |
|----------|-----------|---------|
| `CARD_CVV` | Yes for `--place-order` | CVV (saved or new card) |
| `USE_SAVED_CARD` | Recommended `true` | CVV only on wallet card |
| `CARD_NUMBER` / `CARD_HOLDER_NAME` / `CARD_EXPIRATION_DATE` | If not saved card | Add card at checkout |
| `SHIPPING_NAME` / `SHIPPING_PHONE` | Recommended | Contact |
| `SHIPPING_STREET`…`ZIP` | Optional | Override JSON address |
| `BILLING_*` | Optional | New-card billing |
| `GMAIL_LOGIN` / `GMAIL_APP_PASSWORD` | Recommended | Auto OTP for login + checkout |
| `TARGET_OTP` | Optional | Manual paste while bot waits |
| `CHECKOUT_AUTH_TIMEOUT_SECONDS` | Optional | Default `300` |

### Round1

| Variable | Purpose |
|----------|---------|
| `CAPSOLVER_API_KEY` | Turnstile (preferred) |
| `TWOCAPTCHA_API_KEY` | Fallback |
| `ROUND1_SOLVER` | `capsolver` \| `twocaptcha` |

## Runtime dirs (`~/.scalping/`)

Created automatically. Safe to delete parallel clones if stale; re-run session capture after login expiry.
