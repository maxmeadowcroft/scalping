"""Bot protocol + execution helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scalping.core.logging import get_logger
from scalping.core.paths import REPO_ROOT, ensure_data_dirs


@dataclass
class BotContext:
    """Runtime knobs passed into every bot run."""

    config_path: Path | None = None
    dry_run: bool = True
    place_order: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    argv: list[str] = field(default_factory=list)


@dataclass
class BotResult:
    ok: bool
    bot_id: str
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0


class BaseBot(ABC):
    """Contract every retailer/campaign bot implements."""

    id: str = "base"
    name: str = "Base Bot"
    description: str = ""

    def __init__(self) -> None:
        self.log = get_logger(f"scalping.bots.{self.id}")

    @abstractmethod
    def run(self, ctx: BotContext) -> BotResult:
        """Execute one bot run (monitor, submit, etc.)."""

    def ensure_session(self, ctx: BotContext) -> None:
        """Optional: refresh auth before run. Override per bot."""
        return None

    def healthcheck(self, ctx: BotContext) -> BotResult:
        """Lightweight readiness check (default: ok)."""
        return BotResult(ok=True, bot_id=self.id, message="ok")


def run_bot(bot: BaseBot, ctx: BotContext | None = None) -> BotResult:
    ensure_data_dirs()
    ctx = ctx or BotContext()
    bot.log.info("starting bot=%s dry_run=%s", bot.id, ctx.dry_run)
    try:
        bot.ensure_session(ctx)
        result = bot.run(ctx)
    except Exception as exc:
        bot.log.exception("bot=%s crashed: %s", bot.id, exc)
        return BotResult(
            ok=False,
            bot_id=bot.id,
            message=str(exc),
            exit_code=1,
        )
    bot.log.info(
        "finished bot=%s ok=%s exit=%s msg=%s",
        bot.id,
        result.ok,
        result.exit_code,
        result.message,
    )
    return result


def default_config_dir() -> Path:
    return REPO_ROOT / "configs"
