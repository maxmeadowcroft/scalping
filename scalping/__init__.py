"""Multi-bot drop platform.

Layout
------
scalping/
  core/          shared logging, paths, HTTP, captcha, BaseBot, registry
  bots/target/   Target implementation + adapter
  bots/round1/   Round1 implementation + adapter
  cli.py         python -m scalping

configs/         JSON configs
scripts/         launchers
sessions/        session CLIs
"""

from __future__ import annotations

__version__ = "0.3.0"
