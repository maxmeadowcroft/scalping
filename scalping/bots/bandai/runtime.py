"""Bandai Chrome profile + cookie paths under ~/.scalping."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from scalping.core.paths import DATA_ROOT, LOGS_DIR, PROFILES_DIR, SESSIONS_DIR, ensure_data_dirs

PROFILE_DIR = PROFILES_DIR / "bandai"
COOKIES_PATH = SESSIONS_DIR / "bandai_cookies.json"
PROFILE_LOCK_PATH = PROFILES_DIR / "bandai.browser.lock"
CAPTURE_DIR = LOGS_DIR / "bandai"

CHROME_ADD_ARGUMENTS = [
    "--disable-session-crashed-bubble",
    "--disable-features=InfiniteSessionRestore,TabRestore,WebAuthenticationConditionalUI",
    "--hide-crash-restore-bubble",
    "--noerrdialogs",
    "--disable-popup-blocking",
]


def prepare_runtime() -> None:
    """macOS-safe cwd + ensure Bandai profile dirs exist."""
    ensure_data_dirs()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    # Botasaurus uses getcwd() for profiles — stay out of Desktop/Documents.
    data = Path.home() / ".scalping"
    data.mkdir(parents=True, exist_ok=True)
    try:
        os.chdir(data)
    except OSError:
        pass
    _repair_profile(PROFILE_DIR)


def _repair_profile(profile_dir: Path) -> None:
    prefs = profile_dir / "Preferences"
    if not prefs.exists():
        return
    try:
        data = json.loads(prefs.read_text(encoding="utf-8"))
    except Exception:
        return
    changed = False
    profile = data.setdefault("profile", {})
    if profile.get("exit_type") != "Normal":
        profile["exit_type"] = "Normal"
        changed = True
    if changed:
        prefs.write_text(json.dumps(data), encoding="utf-8")
        print(f"[RUNTIME] repaired Chrome profile ({profile_dir.name})")


def save_cookies_payload(payload: dict) -> None:
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def capture_dir() -> Path:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    return CAPTURE_DIR


def new_capture_stem(reason: str = "stop") -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (reason or "stop"))[:48]
    return f"{int(time.time())}_{safe or 'stop'}"
