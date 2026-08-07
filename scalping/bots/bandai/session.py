"""Premium Bandai US session — persist Chrome profile; re-login only when stale.

Mirrors Target: reuse `~/.scalping/chrome-profiles/bandai`, check member context,
and only POST /login when the session is actually logged out.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from botasaurus.browser import Driver, browser
from dotenv import load_dotenv

from scalping.bots.bandai.runtime import (
    CHROME_ADD_ARGUMENTS,
    COOKIES_PATH,
    PROFILE_DIR,
    prepare_runtime,
    save_cookies_payload,
)
from scalping.core.paths import REPO_ROOT

BANDAI_HOME = "https://p-bandai.com/us"
BANDAI_LOGIN = "https://p-bandai.com/us/login"
BANDAI_ACCOUNT = "https://p-bandai.com/us/mypage"


def load_bandai_credentials() -> tuple[str, str]:
    load_dotenv(REPO_ROOT / ".env", override=True)
    user = (os.getenv("BANDAI_USERNAME") or "").strip()
    password = (os.getenv("BANDAI_PASSWORD") or "").strip()
    return user, password


def load_bandai_cvv() -> str:
    load_dotenv(REPO_ROOT / ".env", override=True)
    return (os.getenv("BANDAI_CARD_CVV") or "").strip()


def _page_text(driver: Driver) -> str:
    try:
        return str(
            driver.run_js("return document.body ? document.body.innerText : ''") or ""
        )
    except Exception:
        return ""


def dismiss_overlays(driver: Driver) -> None:
    """Cookie consent / age gates that block clicks."""
    try:
        clicked = driver.run_js(
            """
            const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            for (const n of nodes) {
              const t = ((n.innerText || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
              if (t.includes('accept all cookies') || t === 'accept all'
                  || t.includes('accept cookies')) {
                n.click(); return t;
              }
            }
            return null;
            """
        )
        if clicked:
            print(f"[BANDAI] overlay → {clicked!r}")
            time.sleep(0.25)
    except Exception:
        pass


def wait_for_spa(driver: Driver, *, timeout: float = 8.0) -> bool:
    """Wait until Bandai Vue USER_DATA (or document) is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = driver.run_js(
            """
            return !!(window.USER_DATA || document.querySelector('#app') || document.body);
            """
        )
        if ready:
            time.sleep(0.15)
            return True
        time.sleep(0.2)
    return False


def user_data_logged_in(driver: Driver) -> bool | None:
    """Read window.USER_DATA if present. None = unknown / not hydrated yet."""
    try:
        result = driver.run_js(
            """
            const u = window.USER_DATA;
            if (!u) return {known: false};
            const m = u.loggedInMember || u.member || null;
            if (m && typeof m === 'object' && typeof m.isLoggedIn === 'boolean') {
              return {known: true, loggedIn: !!m.isLoggedIn};
            }
            if (typeof u.isLoggedIn === 'boolean') {
              return {known: true, loggedIn: !!u.isLoggedIn};
            }
            // USER_DATA present but no member flag yet
            return {known: false};
            """
        )
        if isinstance(result, dict) and result.get("known"):
            return bool(result.get("loggedIn"))
    except Exception:
        pass
    return None


def fetch_member_context(driver: Driver) -> dict[str, Any]:
    """GET /api/context/member — authoritative logged-in check."""
    try:
        result = driver.run_js(
            """
            return (async () => {
              const csrf = (window.USER_DATA && window.USER_DATA.csrfToken) || '';
              const res = await fetch('/api/context/member', {
                credentials: 'include',
                headers: {
                  'X-Requested-With': 'XMLHttpRequest',
                  'X-G1-Area-Code': 'us',
                  ...(csrf ? {'X-CSRF-TOKEN': csrf} : {}),
                },
              });
              const text = await res.text();
              let data = null;
              try { data = JSON.parse(text); } catch (e) { data = text.slice(0, 400); }
              return {status: res.status, data, csrfHeader: res.headers.get('x-csrf-token')};
            })();
            """
        )
        return result if isinstance(result, dict) else {"status": 0, "data": result}
    except Exception as exc:
        return {"status": 0, "error": str(exc)}


def is_member_logged_in(ctx: dict[str, Any] | None) -> bool:
    if not isinstance(ctx, dict):
        return False
    data = ctx.get("data")
    if not isinstance(data, dict):
        return False
    member = data.get("loggedInMember") or data.get("member") or {}
    if isinstance(member, dict) and member.get("isLoggedIn") is True:
        return True
    if data.get("isLoggedIn") is True:
        return True
    return False


def looks_signed_in_ui(driver: Driver) -> bool:
    """Cheap UI heuristic (My Page / no Sign In CTA)."""
    url = (driver.current_url or "").lower()
    if "/login" in url:
        return False
    text = _page_text(driver).lower()
    has_sign_in_cta = bool(
        driver.run_js(
            """
            const nodes = Array.from(document.querySelectorAll('a, button'));
            for (const n of nodes) {
              const t = ((n.innerText || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
              if (t === 'sign in' || t === 'log in') return true;
            }
            return false;
            """
        )
    )
    if "sign out" in text or "log out" in text or "my page" in text:
        if not has_sign_in_cta:
            return True
    if has_sign_in_cta:
        return False
    if "/mypage" in url and "login" not in url:
        return True
    return False


def looks_signed_in(driver: Driver) -> bool:
    """Prefer USER_DATA / member API; fall back to UI."""
    ud = user_data_logged_in(driver)
    if ud is True:
        return True
    if ud is False:
        return False
    ctx = fetch_member_context(driver)
    if ctx.get("status") == 200:
        return is_member_logged_in(ctx)
    return looks_signed_in_ui(driver)


def check_signed_in(driver: Driver) -> bool:
    """Navigate home (or stay on bandai) and report whether profile is authenticated."""
    url = (driver.current_url or "").lower()
    if "p-bandai.com" not in url:
        try:
            driver.get(BANDAI_HOME, wait=1, timeout=25)
        except Exception:
            pass
        time.sleep(0.5)
    wait_for_spa(driver)
    dismiss_overlays(driver)

    ud = user_data_logged_in(driver)
    if ud is True:
        print("[BANDAI] session OK (USER_DATA)")
        return True

    # Retry member context — first paint sometimes returns guest csrf only.
    for i in range(3):
        ctx = fetch_member_context(driver)
        if ctx.get("status") == 200 and is_member_logged_in(ctx):
            print("[BANDAI] session OK (member context)")
            return True
        if i < 2:
            time.sleep(0.45)

    if looks_signed_in_ui(driver):
        print("[BANDAI] session OK (UI)")
        return True

    # Last resort: mypage redirect check
    try:
        driver.get(BANDAI_ACCOUNT, wait=1, timeout=20)
        time.sleep(0.6)
        wait_for_spa(driver)
        dismiss_overlays(driver)
    except Exception:
        pass
    ctx = fetch_member_context(driver)
    ok = is_member_logged_in(ctx) or looks_signed_in_ui(driver)
    if ok:
        print("[BANDAI] session OK (mypage)")
    else:
        print(f"[BANDAI] session stale member_status={ctx.get('status')} url={driver.current_url}")
    return ok


def quick_signed_in(driver: Driver) -> bool:
    """Fast probe — current page first, then home, then full check."""
    url = (driver.current_url or "").lower()
    if "p-bandai.com" in url and "/login" not in url:
        wait_for_spa(driver, timeout=3.0)
        if looks_signed_in(driver):
            return True
    try:
        if "p-bandai.com" not in url:
            driver.get(BANDAI_HOME, wait=1, timeout=20)
            time.sleep(0.35)
            wait_for_spa(driver, timeout=4.0)
            dismiss_overlays(driver)
            if looks_signed_in(driver):
                return True
    except Exception:
        pass
    return check_signed_in(driver)


def login_via_fetch(driver: Driver, *, username: str, password: str) -> dict[str, Any]:
    """POST /login with CSRF — autoLogin keeps the Chrome profile signed in."""
    result = driver.run_js(
        f"""
        return (async () => {{
          const csrf = (window.USER_DATA && window.USER_DATA.csrfToken) || '';
          const body = new URLSearchParams({{
            grantType: 'password',
            memberId: {username!r},
            password: {password!r},
            saveLoginId: 'true',
            autoLogin: 'true',
          }});
          const res = await fetch('/login', {{
            method: 'POST',
            credentials: 'include',
            headers: {{
              'Content-Type': 'application/x-www-form-urlencoded',
              'X-Requested-With': 'XMLHttpRequest',
              'X-G1-Area-Code': 'us',
              ...(csrf ? {{'X-CSRF-TOKEN': csrf}} : {{}}),
            }},
            body: body.toString(),
          }});
          const text = await res.text();
          let data = null;
          try {{ data = JSON.parse(text); }} catch (e) {{ data = text.slice(0, 500); }}
          // Refresh member context so USER_DATA / cookies settle.
          try {{
            await fetch('/api/context/member', {{
              credentials: 'include',
              headers: {{
                'X-Requested-With': 'XMLHttpRequest',
                'X-G1-Area-Code': 'us',
                ...(csrf ? {{'X-CSRF-TOKEN': csrf}} : {{}}),
              }},
            }});
          }} catch (e) {{}}
          return {{
            status: res.status,
            data,
            csrf: res.headers.get('x-csrf-token'),
            restricted: res.headers.get('x-restricted-type'),
          }};
        }})();
        """
    )
    return result if isinstance(result, dict) else {"status": 0, "data": result}


def login_with_password(driver: Driver, *, username: str, password: str) -> bool:
    try:
        driver.get(BANDAI_LOGIN, wait=1, timeout=30)
    except Exception as exc:
        print(f"[BANDAI] login page load warning: {exc}")
    time.sleep(0.8)
    wait_for_spa(driver)
    dismiss_overlays(driver)

    if looks_signed_in(driver):
        print("[BANDAI] already signed in — skip password login")
        return True

    last: dict[str, Any] = {}
    for attempt in range(1, 6):
        last = login_via_fetch(driver, username=username, password=password)
        status = int(last.get("status") or 0)
        restricted = last.get("restricted")
        print(
            f"[BANDAI] POST /login attempt={attempt} status={status} "
            f"restricted={restricted!r}"
        )
        time.sleep(0.5)
        if looks_signed_in(driver):
            print("[BANDAI] signed_in=True (after login fetch)")
            return True
        if status == 200:
            time.sleep(0.7)
            if looks_signed_in(driver):
                print("[BANDAI] signed_in=True (member refresh)")
                return True
        if status in (429, 502, 503):
            time.sleep(1.2 * attempt)
            continue
        if status in (401, 403):
            data = last.get("data")
            snippet = str(data)[:200] if data is not None else ""
            print(f"[BANDAI] login rejected: {snippet}")
            break
        time.sleep(0.6)

    print("[BANDAI] fetch login inconclusive — trying UI submit")
    try:
        driver.type("#e-mail_address", username, wait=3)
        driver.type("#password", password, wait=2)
        driver.run_js(
            """
            const nodes = Array.from(document.querySelectorAll('button, input[type="submit"]'));
            for (const n of nodes) {
              const t = ((n.innerText || n.value || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
              if (t === 'sign in' || t === 'login' || t === 'log in') { n.click(); return t; }
            }
            return null;
            """
        )
        time.sleep(2.0)
    except Exception as exc:
        print(f"[BANDAI] UI login fallback failed: {exc}")

    deadline = time.time() + 20
    while time.time() < deadline:
        dismiss_overlays(driver)
        if looks_signed_in(driver):
            print("[BANDAI] signed_in=True")
            return True
        time.sleep(0.4)

    print(f"[BANDAI] signed_in=False last_login={last.get('status')} url={driver.current_url}")
    return False


def restore_session_cookies(driver: Driver) -> int:
    """Re-inject cookies from disk so SESSION survives Chrome restarts.

    Bandai's SESSION cookie is session-scoped (expiry=-1). Botasaurus closes
    Chrome between runs, which drops it. We persist the jar to disk and restore
    it after the first navigation to p-bandai.com.
    """
    if not COOKIES_PATH.exists():
        return 0
    try:
        payload = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return 0
    raw = payload.get("cookies")
    if not isinstance(raw, list) or not raw:
        return 0

    # Must be on the domain before add_cookies.
    url = (driver.current_url or "").lower()
    if "p-bandai.com" not in url:
        try:
            driver.get(BANDAI_HOME, wait=1, timeout=25)
            time.sleep(0.35)
        except Exception as exc:
            print(f"[BANDAI] restore nav failed: {exc}")
            return 0

    added = 0
    # Prefer Botasaurus bulk helper when available.
    try:
        if hasattr(driver, "add_cookies"):
            # Normalize session cookies to a long-lived expiry so the profile keeps them.
            far = int(time.time()) + 60 * 60 * 24 * 30
            normalized = []
            for c in raw:
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                row = dict(c)
                domain = str(row.get("domain") or "p-bandai.com")
                # Only restore first-party Bandai cookies (skip trackers).
                if "p-bandai.com" not in domain and domain.lstrip(".") != "p-bandai.com":
                    continue
                exp = row.get("expiry") or row.get("expires")
                if exp in (None, -1, "-1") or row.get("session") is True:
                    row["expiry"] = far
                    row["expires"] = far
                    row["session"] = False
                normalized.append(row)
            if normalized:
                driver.add_cookies(normalized)
                added = len(normalized)
        else:
            for c in raw:
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                domain = str(c.get("domain") or "")
                if "p-bandai.com" not in domain:
                    continue
                try:
                    driver.add_cookie(c)  # type: ignore[attr-defined]
                    added += 1
                except Exception:
                    pass
    except Exception as exc:
        print(f"[BANDAI] cookie restore failed: {exc}")
        return 0

    if added:
        print(f"[BANDAI] restored {added} cookies from {COOKIES_PATH.name}")
        try:
            driver.get(BANDAI_HOME, wait=1, timeout=25)
            time.sleep(0.4)
            wait_for_spa(driver, timeout=5.0)
        except Exception:
            pass
    return added


def ensure_signed_in_on_driver(driver: Driver, *, force: bool = False) -> bool:
    """Ensure this browser is logged in. No-op when session is still good (like Target)."""
    # Always try cookie restore first — SESSION dies when Chrome exits.
    restore_session_cookies(driver)

    if not force and quick_signed_in(driver):
        print("[BANDAI] session OK — already signed in")
        return True

    username, password = load_bandai_credentials()
    if not username or not password:
        raise RuntimeError(
            "Logged out of Bandai and BANDAI_USERNAME / BANDAI_PASSWORD missing — cannot auto-login"
        )
    print("[BANDAI] session stale — password re-login…")
    ok = login_with_password(driver, username=username, password=password)
    if ok:
        try:
            # Force a home hit so restored cookies bind, then dump jar.
            try:
                driver.get(BANDAI_HOME, wait=1, timeout=20)
                time.sleep(0.35)
            except Exception:
                pass
            save_session(driver, known_signed_in=True, navigate_home=False)
        except Exception as exc:
            print(f"[BANDAI] cookie save skipped: {exc}")
    return ok


def _dump_cookies_and_storage(driver: Driver) -> tuple[list, dict]:
    cookies: list = []
    local_storage: dict = {}
    try:
        raw = driver.get_cookies_and_local_storage()
        # Botasaurus may return a dict — do NOT unpack (that yields key names).
        if isinstance(raw, dict):
            cookies = list(raw.get("cookies") or [])
            ls = raw.get("local_storage") or raw.get("localStorage") or {}
            local_storage = dict(ls) if isinstance(ls, dict) else {}
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            c0, c1 = raw
            cookies = list(c0) if isinstance(c0, (list, tuple)) else []
            local_storage = dict(c1) if isinstance(c1, dict) else {}
    except Exception as exc:
        print(f"[BANDAI] cookie dump helper failed: {exc}")
    if not cookies:
        try:
            cookies = list(driver.get_cookies() or [])
        except Exception:
            cookies = []
    if not local_storage:
        try:
            local_storage = (
                driver.run_js(
                    """
                    const out = {};
                    try {
                      for (let i = 0; i < localStorage.length; i++) {
                        const k = localStorage.key(i);
                        out[k] = localStorage.getItem(k);
                      }
                    } catch (e) {}
                    return out;
                    """
                )
                or {}
            )
        except Exception:
            local_storage = {}
    if cookies and isinstance(cookies[0], str) and cookies[0] == "cookies":
        cookies = []
    return cookies, local_storage if isinstance(local_storage, dict) else {}


def save_session(
    driver: Driver,
    *,
    known_signed_in: bool | None = None,
    navigate_home: bool = True,
) -> dict[str, Any]:
    if navigate_home:
        try:
            url = (driver.current_url or "").lower()
            if "p-bandai.com" not in url:
                driver.get(BANDAI_HOME, wait=1, timeout=20)
                time.sleep(0.35)
        except Exception as exc:
            print(f"[BANDAI] navigate before save: {exc}")
    cookies, local_storage = _dump_cookies_and_storage(driver)
    # Persist session-scoped auth cookies with a long expiry so restore works
    # after Chrome exits (SESSION otherwise dies with the process).
    far = int(time.time()) + 60 * 60 * 24 * 30
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "")
        domain = str(c.get("domain") or "")
        if "p-bandai.com" not in domain:
            continue
        exp = c.get("expiry") if "expiry" in c else c.get("expires")
        if name in {"SESSION", "TS01ce3887", "TS01dc4fc6", "_BSP_CART_TOKEN_"} or exp in (
            None,
            -1,
            "-1",
        ):
            c["expiry"] = far
            c["expires"] = far
            c["session"] = False
    payload = {
        "url": driver.current_url,
        "profile": str(PROFILE_DIR),
        "cookies": cookies,
        "local_storage": local_storage,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "signed_in": bool(known_signed_in)
        if known_signed_in is not None
        else looks_signed_in(driver),
    }
    save_cookies_payload(payload)
    meta = {
        "saved_at": payload["saved_at"],
        "cookie_count": len(cookies),
        "final_url": driver.current_url,
        "signed_in": payload["signed_in"],
    }
    print(f"[BANDAI] saved {len(cookies)} cookies → {COOKIES_PATH}")
    return meta


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    wait_for_complete_page_load=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
    raise_exception=True,
)
def _browser_ensure_session(driver: Driver, data: dict) -> dict[str, Any]:
    force = bool(data.get("force"))
    ok = ensure_signed_in_on_driver(driver, force=force)
    if not ok:
        raise RuntimeError("Bandai login failed — check credentials / captcha")
    return save_session(driver, known_signed_in=True, navigate_home=False)


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    wait_for_complete_page_load=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def _browser_check_only(driver: Driver, data: dict) -> dict[str, Any]:
    ok = check_signed_in(driver)
    return {"signed_in": ok, "url": driver.current_url}


def ensure_bandai_session(*, force: bool = False) -> dict[str, Any]:
    """Open Bandai Chrome profile and ensure login (only re-auth if stale)."""
    prepare_runtime()
    user, password = load_bandai_credentials()
    if not user or not password:
        raise RuntimeError("Set BANDAI_USERNAME and BANDAI_PASSWORD in .env")
    return _browser_ensure_session({"force": force})


def is_bandai_session_logged_in() -> bool:
    prepare_runtime()
    result = _browser_check_only({})
    return bool(result.get("signed_in"))
