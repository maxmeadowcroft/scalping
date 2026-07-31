"""Shared Botasaurus runtime helpers (macOS Desktop-safe cwd + Chrome profile care).

Botasaurus calls getcwd() for profiles/output. Launching from Desktop/Documents
under macOS TCC often breaks that, so we always chdir into ~/.scalping and keep
Chrome profiles + cookie dumps there.

Chrome shows "Something went wrong when opening your profile" when the last run
was killed uncleanly (exit_type=Crashed) or two processes share one profile.
We repair that on every launch.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import shutil
import subprocess
import time
from pathlib import Path

DATA_ROOT = Path.home() / ".scalping"
PROFILE_DIR = DATA_ROOT / "chrome-profiles" / "target"
COOKIES_PATH = DATA_ROOT / "sessions" / "target_cookies.json"
PARALLEL_PROFILES_DIR = DATA_ROOT / "chrome-profiles" / "target-parallel"
PROFILE_LOCK_PATH = DATA_ROOT / "chrome-profiles" / "target.browser.lock"

# Stable Chrome flags for automation profiles (passed via Botasaurus add_arguments).
CHROME_ADD_ARGUMENTS = [
    "--disable-session-crashed-bubble",
    # Bundle WebAuthn-related flags with existing disable-features (Chrome keeps last wins messy).
    "--disable-features=InfiniteSessionRestore,TabRestore,WebAuthenticationConditionalUI",
    "--hide-crash-restore-bubble",
    "--noerrdialogs",
    "--disable-popup-blocking",
]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _chrome_pids_using_profile(profile_dir: Path) -> list[int]:
    """Best-effort: find Chrome PIDs whose command line mentions this profile."""
    pids: list[int] = []
    needle = str(profile_dir)
    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid=,command="], text=True)
    except Exception:
        return pids
    for line in out.splitlines():
        line = line.strip()
        if not line or needle not in line:
            continue
        if "Chrome" not in line and "Chromium" not in line and "chrome" not in line:
            continue
        try:
            pid = int(line.split(None, 1)[0])
        except ValueError:
            continue
        pids.append(pid)
    return pids


def _pid_from_singleton_lock(profile_dir: Path) -> int | None:
    lock = profile_dir / "SingletonLock"
    try:
        if lock.is_symlink():
            target = os.readlink(lock)
            # e.g. "Maximuss-MacBook-Air.local-28033"
            tail = target.rsplit("-", 1)[-1]
            return int(tail)
    except Exception:
        return None
    return None


def release_profile_processes(profile_dir: Path = PROFILE_DIR, *, graceful_seconds: float = 3.0) -> None:
    """Ask any Chrome still holding this profile to exit, then clear locks."""
    pids = set(_chrome_pids_using_profile(profile_dir))
    singleton_pid = _pid_from_singleton_lock(profile_dir)
    if singleton_pid and _pid_alive(singleton_pid):
        pids.add(singleton_pid)

    for pid in sorted(pids):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue

    deadline = time.time() + graceful_seconds
    while time.time() < deadline and any(_pid_alive(pid) for pid in pids):
        time.sleep(0.2)

    for pid in sorted(pids):
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    # Let the OS release file handles before we delete lock symlinks
    if pids:
        time.sleep(0.5)


def _clear_lock_files(profile_dir: Path) -> None:
    for name in (
        "SingletonLock",
        "SingletonSocket",
        "SingletonCookie",
        "DevToolsActivePort",
        "RunningChromeVersion",
        "LockFile",
    ):
        path = profile_dir / name
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except OSError:
            pass


def _clear_crash_session_files(profile_dir: Path) -> None:
    """Remove crash-restore session files (keeps cookies / login)."""
    default = profile_dir / "Default"
    if not default.exists():
        return
    for name in (
        "Current Session",
        "Current Tabs",
        "Last Session",
        "Last Tabs",
    ):
        path = default / name
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
        except OSError:
            pass

    sessions_dir = default / "Sessions"
    if sessions_dir.is_dir():
        shutil.rmtree(sessions_dir, ignore_errors=True)


def _mark_preferences_clean(profile_dir: Path) -> bool:
    prefs_path = profile_dir / "Default" / "Preferences"
    if not prefs_path.exists():
        return False
    try:
        data = json.loads(prefs_path.read_text(encoding="utf-8"))
        profile = data.setdefault("profile", {})
        changed = False
        if profile.get("exit_type") != "Normal":
            profile["exit_type"] = "Normal"
            changed = True
        if profile.get("exited_cleanly") is not True:
            profile["exited_cleanly"] = True
            changed = True
        # Open new tab instead of restoring crashed session
        session = data.setdefault("session", {})
        if session.get("restore_on_startup") != 5:
            session["restore_on_startup"] = 5
            changed = True
        # Silence some restore / signin bubbles when possible
        browser = data.setdefault("browser", {})
        if browser.get("has_seen_welcome_page") is not True:
            browser["has_seen_welcome_page"] = True
            changed = True
        if changed:
            prefs_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        return changed
    except Exception as exc:
        print(f"[RUNTIME] preferences repair skipped: {exc}")
        return False


def repair_chrome_profile(profile_dir: Path = PROFILE_DIR, *, verbose: bool = True) -> None:
    """Make a profile safe to open after crashes / forced kills."""
    if not profile_dir.exists():
        return

    release_profile_processes(profile_dir)
    _clear_lock_files(profile_dir)
    _clear_crash_session_files(profile_dir)
    changed = _mark_preferences_clean(profile_dir)
    if verbose and changed:
        print(f"[RUNTIME] repaired Chrome profile ({profile_dir.name})")


def acquire_profile_lock(timeout_seconds: float = 30.0) -> bool:
    """Prevent two bot runs from opening the same Chrome profile."""
    PROFILE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            fd = os.open(str(PROFILE_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)

            def _release() -> None:
                try:
                    if PROFILE_LOCK_PATH.exists():
                        stored = PROFILE_LOCK_PATH.read_text(encoding="utf-8").strip()
                        if stored == str(os.getpid()):
                            PROFILE_LOCK_PATH.unlink()
                except OSError:
                    pass
                repair_chrome_profile(PROFILE_DIR, verbose=False)

            atexit.register(_release)
            return True
        except FileExistsError:
            try:
                old_pid = int(PROFILE_LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                old_pid = 0
            if old_pid and not _pid_alive(old_pid):
                try:
                    PROFILE_LOCK_PATH.unlink()
                    continue
                except OSError:
                    pass
            time.sleep(0.5)
    print(f"[RUNTIME] could not acquire profile lock at {PROFILE_LOCK_PATH}")
    return False


def prepare_browser_profile(profile_dir: Path = PROFILE_DIR) -> Path:
    """Call immediately before launching Botasaurus Chrome."""
    repair_chrome_profile(profile_dir)
    return profile_dir


def browser_launch_kwargs(profile: Path | str = PROFILE_DIR) -> dict:
    """Common @browser(...) kwargs so every entrypoint uses the same safe flags."""
    prepare_browser_profile(Path(profile))
    return {
        "profile": str(profile),
        "tiny_profile": False,
        "headless": False,
        "block_images": False,
        "output": None,
        "add_arguments": list(CHROME_ADD_ARGUMENTS),
        "close_on_crash": True,
    }


def prepare_runtime() -> Path:
    """Create data dirs, repair profile, chdir into ~/.scalping."""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARALLEL_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    acquire_profile_lock()
    repair_chrome_profile(PROFILE_DIR)
    os.chdir(DATA_ROOT)
    return DATA_ROOT


def parallel_profile_dir(item_label: str, index: int) -> Path:
    """Clone the logged-in Target profile for one parallel browser worker."""
    repair_chrome_profile(PROFILE_DIR)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in item_label)[:40]
    dest = PARALLEL_PROFILES_DIR / f"{index:02d}-{safe or 'item'}"
    if dest.exists():
        release_profile_processes(dest, graceful_seconds=1.0)
        shutil.rmtree(dest, ignore_errors=True)
    if PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir()):
        shutil.copytree(PROFILE_DIR, dest, dirs_exist_ok=False)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    repair_chrome_profile(dest)
    return dest
