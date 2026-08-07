# scalping

Multi-bot drop platform (Target, Round1/Shortstack, …) with a shared core and one package per bot.

**Keep the repo off Desktop/Documents/Downloads** — macOS privacy protections break Botasaurus/`getcwd()` there. Prefer `~/Dev/Scalping`.

## Docs

| Doc | Topic |
|-----|--------|
| [Architecture](docs/ARCHITECTURE.md) | Package layout, layers, data paths |
| [Adding a bot](docs/ADDING_A_BOT.md) | Registry + `BaseBot` checklist |
| [Target](docs/TARGET.md) | PDP poll → ATC → checkout |
| [Round1](docs/ROUND1.md) | Shortstack entries + Turnstile |
| [Configuration](docs/CONFIGURATION.md) | `configs/` + `.env` reference |

## Layout

```
scalping/core/          shared platform utilities
scalping/bots/target/   Target bot
scalping/bots/round1/   Round1 bot
configs/                JSON configs
scripts/                launchers
sessions/               login helpers
```

Runtime data: `~/.scalping/`.

## Setup

```bash
cd ~/Dev/Scalping
uv sync --all-groups
cp .env.example .env
./scripts/session-target.sh
```

## Common commands

```bash
./scalping/run.sh list
./scalping/run.sh health target
./scalping/run.sh session target

./scripts/run-target.sh
./scripts/run-target.sh --config configs/target/tonight.json --place-order
./scripts/run-round1.sh --probe
```

## Tests

```bash
uv run python -m pytest tests/test_config_and_stock.py -q
uv run python -m pytest tests/test_target_live.py -m live -s
```
