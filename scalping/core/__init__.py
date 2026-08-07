"""Shared platform primitives for all bots."""

from scalping.core.bot import BaseBot, BotContext, BotResult, run_bot
from scalping.core.registry import get_bot, list_bots, register_bot

__all__ = [
    "BaseBot",
    "BotContext",
    "BotResult",
    "get_bot",
    "list_bots",
    "register_bot",
    "run_bot",
]
