# Adding a bot

## Checklist

1. Create `scalping/bots/<name>/` with implementation modules (`cli.py`, APIs, etc.).
2. Register an adapter in `bot.py`:

```python
from scalping.core.bot import BaseBot, BotContext, BotResult
from scalping.core.registry import register_bot

@register_bot("acme")
class AcmeBot(BaseBot):
    name = "Acme Drop Bot"
    description = "Short one-liner for `scalping list`"

    def run(self, ctx: BotContext) -> BotResult:
        from scalping.bots.acme.cli import main

        code = main(list(ctx.argv))
        return BotResult(ok=code == 0, bot_id=self.id, exit_code=int(code or 0))
```

3. Export from `scalping/bots/<name>/__init__.py` and ensure
   `scalping.core.registry._load_builtin_bots` imports the package.
4. Add `configs/<name>/default.json`.
5. Verify: `uv run python -m scalping list` shows `acme`.

## Optional hooks

| Hook | When to override |
|------|------------------|
| `ensure_session` | Cookie / login must be valid before `run` |
| `healthcheck` | Fast signed-in / API-reachable check |

## Shared utilities

- **Captcha** — `scalping.core.captcha`
- **HTTP** — `scalping.core.http`
- **Logging** — `scalping.core.logging`
- **Paths** — `scalping.core.paths`
