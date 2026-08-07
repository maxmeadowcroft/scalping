"""Optional session-provider protocol for bots that need durable auth."""

from __future__ import annotations

from typing import Any, Protocol


class SessionProvider(Protocol):
    """Check / refresh a bot's logged-in state."""

    def is_logged_in(self) -> bool: ...

    def ensure(self, *, force: bool = False, timeout: float = 120.0) -> dict[str, Any]: ...
