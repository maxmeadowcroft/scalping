"""Target drop bot adapter (registers with the platform CLI)."""

from __future__ import annotations

from pathlib import Path

from scalping.core.bot import BaseBot, BotContext, BotResult
from scalping.core.paths import CONFIG_DIR
from scalping.core.registry import register_bot


@register_bot("target")
class TargetBot(BaseBot):
    id = "target"
    name = "Target Drop Bot"
    description = "Poll Target PDPs, ATC, checkout (shipping/pickup), optional place-order"

    def ensure_session(self, ctx: BotContext) -> None:
        if ctx.extra.get("skip_login_check"):
            self.log.info("skip_login_check set — not refreshing session")
            return
        from scalping.bots.target.session import ensure_target_session

        timeout = float(ctx.extra.get("login_timeout") or 120)
        force = bool(ctx.extra.get("force_login"))
        self.log.info("ensuring Target session force=%s", force)
        meta = ensure_target_session(force=force, timeout=timeout)
        self.log.info("session cookies=%s", meta.get("cookie_count"))

    def run(self, ctx: BotContext) -> BotResult:
        # Build argv for the existing CLI so behavior stays identical.
        argv = list(ctx.argv)
        if ctx.config_path:
            argv = ["--config", str(ctx.config_path), *argv]
        if ctx.place_order and "--place-order" not in argv:
            argv.append("--place-order")
        if ctx.dry_run and "--dry-run" not in argv and "--place-order" not in argv:
            argv.append("--dry-run")
        if ctx.extra.get("skip_login_check") and "--skip-login-check" not in argv:
            argv.append("--skip-login-check")
        # Session already handled in ensure_session — avoid double login.
        if "--skip-login-check" not in argv:
            argv.append("--skip-login-check")

        from scalping.bots.target.cli import main as target_main

        code = target_main(argv)
        # target_main returns None historically — treat as 0
        exit_code = int(code or 0)
        return BotResult(
            ok=exit_code == 0,
            bot_id=self.id,
            message="completed" if exit_code == 0 else f"exit={exit_code}",
            exit_code=exit_code,
            details={"config": str(ctx.config_path) if ctx.config_path else None},
        )

    def healthcheck(self, ctx: BotContext) -> BotResult:
        from scalping.bots.target.session import is_target_session_logged_in

        signed = is_target_session_logged_in()
        return BotResult(
            ok=signed,
            bot_id=self.id,
            message="signed_in" if signed else "logged_out",
            exit_code=0 if signed else 2,
            details={"signed_in": signed},
        )


def default_config_path() -> Path:
    return CONFIG_DIR / "target" / "default.json"
