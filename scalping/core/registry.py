"""Bot registry — discover and construct bots by id."""

from __future__ import annotations

from typing import Callable, TypeVar

from scalping.core.bot import BaseBot

_BOTS: dict[str, Callable[[], BaseBot]] = {}
T = TypeVar("T", bound=BaseBot)


def register_bot(bot_id: str | None = None):
    """Decorator to register a BaseBot subclass."""

    def deco(cls: type[T]) -> type[T]:
        key = (bot_id or getattr(cls, "id", "") or cls.__name__).lower()
        if not key:
            raise ValueError(f"Bot {cls} missing id")
        _BOTS[key] = cls  # type: ignore[assignment]
        cls.id = key
        return cls

    return deco


def register_factory(bot_id: str, factory: Callable[[], BaseBot]) -> None:
    _BOTS[bot_id.lower()] = factory


def get_bot(bot_id: str) -> BaseBot:
    key = bot_id.lower()
    if key not in _BOTS:
        # Lazy-import built-ins
        _load_builtin_bots()
    if key not in _BOTS:
        known = ", ".join(sorted(list_bots())) or "(none)"
        raise KeyError(f"Unknown bot {bot_id!r}. Known: {known}")
    return _BOTS[key]()


def list_bots() -> list[str]:
    _load_builtin_bots()
    return sorted(_BOTS.keys())


_LOADED = False


def _load_builtin_bots() -> None:
    global _LOADED
    if _LOADED:
        return
    # Import side-effects register bots
    from scalping.bots import round1 as _round1  # noqa: F401
    from scalping.bots import target as _target  # noqa: F401

    _LOADED = True
