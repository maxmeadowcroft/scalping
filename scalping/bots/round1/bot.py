"""Round1 / Shortstack campaign bot."""

from __future__ import annotations

from pathlib import Path

from scalping.core.bot import BaseBot, BotContext, BotResult
from scalping.core.paths import CONFIG_DIR
from scalping.core.registry import register_bot


@register_bot("round1")
class Round1Bot(BaseBot):
    id = "round1"
    name = "Round1 Shortstack Bot"
    description = "Poll cmpgn.page drops, CapSolver Turnstile, API entry submit"

    def run(self, ctx: BotContext) -> BotResult:
        argv = list(ctx.argv)
        if ctx.config_path:
            argv = ["--config", str(ctx.config_path), *argv]
        if ctx.dry_run and "--dry-run" not in argv:
            argv.append("--dry-run")

        from scalping.bots.round1.cli import main as round1_main

        code = round1_main(argv)
        exit_code = int(code or 0)
        return BotResult(
            ok=exit_code == 0,
            bot_id=self.id,
            message="completed" if exit_code == 0 else f"exit={exit_code}",
            exit_code=exit_code,
        )

    def healthcheck(self, ctx: BotContext) -> BotResult:
        from scalping.bots.round1.api import load_round1_config, probe

        cfg_path = ctx.config_path or default_config_path()
        config = load_round1_config(cfg_path)
        result = probe(config, try_solver=False)
        http = (result.get("summary") or {}).get("http_status")
        ok = http == 200
        return BotResult(
            ok=ok,
            bot_id=self.id,
            message=f"http={http}",
            exit_code=0 if ok else 2,
            details=result.get("summary") or {},
        )


def default_config_path() -> Path:
    return CONFIG_DIR / "round1" / "default.json"
