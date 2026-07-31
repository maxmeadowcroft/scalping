"""Capture a Target.com browser session for reuse in future scrapers.

Run once, log in manually in the opened browser (including any 2FA),
then press Enter in the terminal. The session is saved under the
`target` tiny profile and reused automatically by bots that use the
same profile name.
"""

from __future__ import annotations

import json
from pathlib import Path

from botasaurus import browser
from botasaurus.browser import Driver

TARGET_LOGIN_URL = "https://www.target.com/login"
PROFILE_NAME = "target"
SESSION_DIR = Path(__file__).resolve().parent
COOKIES_PATH = SESSION_DIR / "target_cookies.json"


@browser(
    profile=PROFILE_NAME,
    tiny_profile=True,
    headless=False,
    block_images=False,
    output=None,
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
    print("=" * 60 + "\n")

    driver.prompt("Logged in to Target? Press Enter to save the session...")

    cookies, local_storage = driver.get_cookies_and_local_storage()
    session = {
        "url": driver.current_url,
        "profile": PROFILE_NAME,
        "cookies": cookies,
        "local_storage": local_storage,
    }

    COOKIES_PATH.write_text(json.dumps(session, indent=2), encoding="utf-8")

    print(f"\nSaved {len(cookies)} cookies to {COOKIES_PATH}")
    print(f"Tiny profile '{PROFILE_NAME}' persisted for future scrapers.")
    print("Reuse with: @browser(profile='target', tiny_profile=True)")

    return {
        "profile": PROFILE_NAME,
        "cookies_path": str(COOKIES_PATH),
        "cookie_count": len(cookies),
        "final_url": driver.current_url,
    }


if __name__ == "__main__":
    capture_target_session()
