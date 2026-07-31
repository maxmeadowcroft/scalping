"""Capture a Target.com browser session for the stock/checkout bot.

Run once via ./sessions/run_target_session.sh, log in manually in the opened
browser (including any 2FA), then press Enter in the terminal. Session data
is stored under ~/.scalping (macOS blocks Botasaurus profile I/O on Desktop).

The bot reuses ~/.scalping/chrome-profiles/target for authenticated checkout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from botasaurus.browser import Driver, browser

# Allow `python sessions/target.py` without installing the package first.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraping.runtime import CHROME_ADD_ARGUMENTS, COOKIES_PATH, PROFILE_DIR, prepare_runtime

TARGET_LOGIN_URL = "https://www.target.com/login"


@browser(
    # Absolute path (contains "/") so Botasaurus skips cwd-relative profiles/
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def capture_target_session(driver: Driver, data):
    driver.get(TARGET_LOGIN_URL)

    print("\n" + "=" * 60)
    print("TARGET SESSION CAPTURE")
    print("=" * 60)
    print("1. Log in to your Target account in the browser window.")
    print("2. Complete any 2FA / captcha prompts if shown.")
    print("3. Wait until you are fully signed in (account page / home).")
    print("4. Return here and press Enter to save the session.")
    print(f"\nProfile dir: {PROFILE_DIR}")
    print(f"Cookies:     {COOKIES_PATH}")
    print("=" * 60 + "\n")

    driver.prompt("Logged in to Target? Press Enter to save the session...")

    cookies, local_storage = driver.get_cookies_and_local_storage()
    session = {
        "url": driver.current_url,
        "profile": str(PROFILE_DIR),
        "cookies": cookies,
        "local_storage": local_storage,
    }

    COOKIES_PATH.write_text(json.dumps(session, indent=2), encoding="utf-8")

    print(f"\nSaved {len(cookies)} cookies to {COOKIES_PATH}")
    print(f"Chrome profile persisted at {PROFILE_DIR}")
    print(f"Reuse with: @browser(profile={str(PROFILE_DIR)!r})")

    return {
        "profile": str(PROFILE_DIR),
        "cookies_path": str(COOKIES_PATH),
        "cookie_count": len(cookies),
        "final_url": driver.current_url,
    }


if __name__ == "__main__":
    prepare_runtime()
    capture_target_session()
