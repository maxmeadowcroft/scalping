# Architecture

Scalping is a multi-bot drop platform: a shared core plus one package per bot.

## Layout

```
Scalping/
├── scalping/
│   ├── core/                 # logging, paths, HTTP, captcha, BaseBot, registry
│   ├── bots/
│   │   ├── target/           # Target implementation + adapter
│   │   │   ├── bot.py        # @register_bot("target")
│   │   │   ├── cli.py
│   │   │   ├── config.py
│   │   │   ├── stock.py
│   │   │   ├── checkout.py
│   │   │   ├── session.py
│   │   │   ├── gmail_otp.py
│   │   │   └── runtime.py
│   │   └── round1/
│   │       ├── bot.py
│   │       ├── cli.py
│   │       └── api.py
│   ├── cli.py
│   └── run.sh
├── configs/
│   ├── target/
│   └── round1/
├── sessions/
├── scripts/
├── docs/
└── tests/
```

Runtime data lives under `~/.scalping/` (Chrome profiles, cookies, logs).

## Layers

| Layer | Responsibility |
|-------|----------------|
| `scalping.cli` | Discover bots, session / health / run |
| `scalping.bots.<id>` | Everything for that bot |
| `configs/<id>/` | Per-bot JSON |
| `.env` | Secrets only |

## Bot contract

Every bot subclasses `BaseBot` and is registered with `@register_bot("id")`:

- `run(ctx)` — main work
- `ensure_session(ctx)` — optional auth refresh
- `healthcheck(ctx)` — readiness

See [ADDING_A_BOT.md](ADDING_A_BOT.md).

## Data paths

| Path | Purpose |
|------|---------|
| `configs/<bot>/` | Checked-in JSON configs |
| `~/.scalping/chrome-profiles/<bot>/` | Persistent Chrome profiles |
| `~/.scalping/sessions/<bot>_cookies.json` | Cookie backups |
| `~/.scalping/logs/` | Optional log files |

Helpers: `scalping.core.paths`.
