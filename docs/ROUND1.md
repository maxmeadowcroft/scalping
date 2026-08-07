# Round1 / Shortstack bot

Submit Round1 campaign entries via the Shortstack (`api.lndg.page`) API, with Cloudflare Turnstile solved through CapSolver (or 2Captcha).

Code lives in `scalping/bots/round1/`.

## Quick start

```bash
./scripts/run-round1.sh --probe
./scripts/run-round1.sh
```

```bash
./scalping/run.sh run round1 -- --probe
./scalping/run.sh run round1 -- --config configs/round1/default.json
```

## Config

[`configs/round1/default.json`](../configs/round1/default.json)

| Field | Meaning |
|-------|---------|
| `url` | Campaign page URL |
| `phase` | Campaign phase id |
| `location_match` | Regex for store / location selection |
| `entries[]` | `first_name`, `last_name`, `email` |
| `parallel` / `stagger_seconds` | Multi-entry concurrency |
| `solver` | `capsolver` (default) or `twocaptcha` |
| `submit` | `false` to stop before POST |

## Env

| Variable | Purpose |
|----------|---------|
| `CAPSOLVER_API_KEY` | Preferred Turnstile solver |
| `TWOCAPTCHA_API_KEY` | Fallback |
| `ROUND1_SOLVER` | Optional override of config `solver` |

## Modules

| Module | Role |
|--------|------|
| `scalping/bots/round1/api.py` | Shortstack HTTP + Turnstile |
| `scalping/bots/round1/cli.py` | CLI / orchestration |
| `scalping/bots/round1/bot.py` | Platform adapter |
| `scalping/core/captcha.py` | Shared CapSolver / 2Captcha client |
