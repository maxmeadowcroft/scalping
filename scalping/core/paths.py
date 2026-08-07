"""Filesystem paths for profiles, sessions, and configs."""

from __future__ import annotations

from pathlib import Path

# Repo root (…/Scalping)
REPO_ROOT = Path(__file__).resolve().parents[2]

# Durable runtime data (outside the repo; survives git clean)
DATA_ROOT = Path.home() / ".scalping"
PROFILES_DIR = DATA_ROOT / "chrome-profiles"
SESSIONS_DIR = DATA_ROOT / "sessions"
LOGS_DIR = DATA_ROOT / "logs"

# Target Chrome profile paths (stable under ~/.scalping)
TARGET_PROFILE_DIR = PROFILES_DIR / "target"
TARGET_COOKIES_PATH = SESSIONS_DIR / "target_cookies.json"
TARGET_PARALLEL_PROFILES_DIR = PROFILES_DIR / "target-parallel"

# In-repo configs
CONFIG_DIR = REPO_ROOT / "configs"
TARGET_CONFIG_DIR = CONFIG_DIR / "target"
ROUND1_CONFIG_DIR = CONFIG_DIR / "round1"


def ensure_data_dirs() -> None:
    for path in (DATA_ROOT, PROFILES_DIR, SESSIONS_DIR, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def bot_profile_dir(bot_id: str) -> Path:
    """Chrome profile directory for a named bot."""
    return PROFILES_DIR / bot_id


def bot_cookies_path(bot_id: str) -> Path:
    return SESSIONS_DIR / f"{bot_id}_cookies.json"


def target_config(name: str = "default") -> Path:
    """Resolve configs/target/<name>.json (with or without .json suffix)."""
    stem = name if name.endswith(".json") else f"{name}.json"
    return TARGET_CONFIG_DIR / stem


def round1_config(name: str = "default") -> Path:
    stem = name if name.endswith(".json") else f"{name}.json"
    return ROUND1_CONFIG_DIR / stem
