"""Premium Bandai US drop bot adapter."""

from __future__ import annotations

from pathlib import Path

from scalping.core.bot import BaseBot, BotContext, BotResult
from scalping.core.paths import CONFIG_DIR
from scalping.core.registry import register_bot


@register_bot("bandai")
class BandaiBot(BaseBot):
    id = "bandai"
    name = "Premium Bandai US Bot"
    description = "p-bandai.com/us — login, ATC, checkout with saved card"

    def ensure_session(self, ctx: BotContext) -> None:
        if ctx.extra.get("skip_login_check"):
            return
        from scalping.bots.bandai.session import ensure_bandai_session

        force = bool(ctx.extra.get("force_login"))
        meta = ensure_bandai_session(force=force)
        self.log.info("bandai session cookies=%s", meta.get("cookie_count"))

    def run(self, ctx: BotContext) -> BotResult:
        argv = list(ctx.argv)
        if ctx.config_path:
            argv = ["--config", str(ctx.config_path), *argv]
        if ctx.place_order and "--place-order" not in argv:
            argv.append("--place-order")
        if ctx.dry_run and "--dry-run" not in argv and "--place-order" not in argv:
            argv.append("--dry-run")
        if ctx.extra.get("force_login") and "--force-login" not in argv:
            argv.append("--force-login")

        from scalping.bots.bandai.cli import main as bandai_main

        code = bandai_main(argv)
        exit_code = int(code or 0)
        return BotResult(
            ok=exit_code == 0,
            bot_id=self.id,
            message="completed" if exit_code == 0 else f"exit={exit_code}",
            exit_code=exit_code,
        )

    def healthcheck(self, ctx: BotContext) -> BotResult:
        from scalping.bots.bandai.session import is_bandai_session_logged_in

        signed = is_bandai_session_logged_in()
        return BotResult(
            ok=signed,
            bot_id=self.id,
            message="signed_in" if signed else "logged_out",
            exit_code=0 if signed else 2,
            details={"signed_in": signed},
        )


def default_config_path() -> Path:
    return CONFIG_DIR / "bandai" / "default.json"
