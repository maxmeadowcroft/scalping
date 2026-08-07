"""Target.com drop bot — platform adapter + implementation package.

Implementation modules live here (`cli`, `checkout`, `stock`, `session`, …).
`TargetBot` registers with the platform CLI.
"""

from __future__ import annotations

from scalping.bots.target.bot import TargetBot

__all__ = ["TargetBot"]
