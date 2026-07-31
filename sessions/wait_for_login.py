"""Wait for a manual Target login, then save cookies into the bot profile."""

from __future__ import annotations

import json
import time

from botasaurus.browser import Driver, browser

from scraping.runtime import CHROME_ADD_ARGUMENTS, COOKIES_PATH, PROFILE_DIR, prepare_runtime


def _signed_in(driver: Driver) -> bool:
    try:
        return bool(
            driver.run_js(
                """
                const t = (document.body && document.body.innerText) || '';
                if (/Sign in or create account/i.test(t)) return false;
                if (/Hi,\\s+\\w+/i.test(t)) return true;
                if (/Sign out/i.test(t) && /account/i.test(t)) return true;
                return false;
                """
            )
        )
    except Exception:
        return False


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def wait_for_login(driver: Driver, data):
    driver.get("https://www.target.com/login")
    print("\n" + "=" * 60)
    print("TARGET RE-LOGIN")
    print("=" * 60)
    print("1. Sign in in the browser (email / passkey / code).")
    print("2. Wait until you see Hi, <name> on Target.")
    print("3. This script will detect login and save the session.")
    print(f"Profile: {PROFILE_DIR}")
    print("=" * 60 + "\n")

    deadline = time.time() + 600
    while time.time() < deadline:
        if _signed_in(driver):
            driver.get("https://www.target.com/")
            driver.sleep(2)
            if not _signed_in(driver):
                time.sleep(2)
                continue
            cookies, local_storage = driver.get_cookies_and_local_storage()
            COOKIES_PATH.write_text(
                json.dumps(
                    {
                        "url": driver.current_url,
                        "profile": str(PROFILE_DIR),
                        "cookies": cookies,
                        "local_storage": local_storage,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"[OK] Logged in — saved {len(cookies)} cookies")
            return {"ok": True, "cookies": len(cookies)}
        time.sleep(3)

    print("[FAIL] Timed out waiting for login")
    return {"ok": False}


if __name__ == "__main__":
    prepare_runtime()
    print(wait_for_login())
