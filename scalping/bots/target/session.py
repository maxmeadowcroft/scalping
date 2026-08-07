"""Target session login + capture (email OTP via Gmail).

Used by:
  ./scripts/session-target.sh
  scalping.bots.target (auto ensure before buying)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from botasaurus.browser import Driver, browser
from dotenv import load_dotenv

from scalping.bots.target.gmail_otp import load_gmail_credentials, wait_for_gmail_otp
from scalping.bots.target.runtime import CHROME_ADD_ARGUMENTS, COOKIES_PATH, PROFILE_DIR, prepare_runtime
from scalping.bots.target.checkout import (
    _otp_entry_visible,
    click_get_a_code_button,
    disable_webauthn_prompts,
    request_email_code,
    submit_otp,
)

TARGET_HOME = "https://www.target.com/"
TARGET_ACCOUNT = "https://www.target.com/account"
TARGET_LOGIN = (
    "https://www.target.com/login?client_id=ecom-web-1.0.0"
    "&ui_namespace=ui-default&back_button_action=browser"
    "&keep_me_signed_in=true&kmsi_default=true"
    "&actions=create_session_request_username&signin_amr=true"
)
SESSION_META_PATH = COOKIES_PATH.with_name("target_session_meta.json")


def _page_text(driver: Driver) -> str:
    try:
        return str(
            driver.run_js("return document.body ? document.body.innerText : ''") or ""
        )
    except Exception:
        return ""


def _page_lower(driver: Driver) -> str:
    return _page_text(driver).lower()


def looks_signed_in(driver: Driver) -> bool:
    url = (driver.current_url or "").lower()
    if "/login" in url:
        return False
    text = _page_lower(driver)
    if "sign in or create account" in text[:1500] and "sign out" not in text:
        if "hi," not in text and "account settings" not in text:
            return False
    if "sign out" in text or "log out" in text:
        return True
    if "account settings" in text:
        return True
    if "hi," in text and ("order" in text or "account" in text):
        return True
    # Cookie presence alone is weak; require account URL without login redirect.
    if "/account" in url and "login" not in url:
        return "sign in" not in text[:400]
    return False


def check_signed_in(driver: Driver) -> bool:
    """Navigate to /account and report whether the profile is authenticated."""
    try:
        driver.get(TARGET_ACCOUNT)
        time.sleep(1.5)
    except Exception:
        pass
    if looks_signed_in(driver):
        return True
    # Sometimes account redirects slowly
    time.sleep(1.0)
    return looks_signed_in(driver)


def _find_username_selector(driver: Driver) -> str | None:
    return driver.run_js(
        """
        const reject = (el) => {
          if (!el) return true;
          const id=(el.id||'').toLowerCase();
          const name=(el.name||'').toLowerCase();
          const ph=(el.placeholder||'').toLowerCase();
          const al=(el.getAttribute('aria-label')||'').toLowerCase();
          const role=(el.getAttribute('role')||'').toLowerCase();
          const t=(el.type||'').toLowerCase();
          if (t==='search' || role==='searchbox') return true;
          if (id.includes('search') || name.includes('search')) return true;
          if (ph.includes('search') || ph.includes('help you find')) return true;
          if (al.includes('search') || al.includes('find')) return true;
          if (id==='email-address') return true; // homepage newsletter
          if (id.includes('newsletter') || name.includes('subscribe')) return true;
          const st=window.getComputedStyle(el);
          if (st.display==='none'||st.visibility==='hidden'||Number(st.opacity)===0) return true;
          const r=el.getBoundingClientRect();
          if (r.width<40||r.height<16) return true;
          return false;
        };
        const prefs = [
          'input#username',
          'input[name="username"]',
          'input[autocomplete="username"]',
          'input[type="email"]',
          'input[data-test*="username" i]',
          'input[data-test*="email" i]',
        ];
        for (const s of prefs) {
          const el = document.querySelector(s);
          if (el && !reject(el)) return s;
        }
        const dialog = document.querySelector('[role="dialog"]');
        const root = dialog || document.body;
        let best=null, bestScore=-1;
        for (const el of root.querySelectorAll('input')) {
          if (reject(el)) continue;
          const id=(el.id||'').toLowerCase();
          const name=(el.name||'').toLowerCase();
          const ph=(el.placeholder||'').toLowerCase();
          const al=(el.getAttribute('aria-label')||'').toLowerCase();
          const ac=(el.autocomplete||'').toLowerCase();
          const t=(el.type||'').toLowerCase();
          let sc=0;
          if (t==='email') sc+=50;
          if (ac==='username'||ac==='email') sc+=40;
          if (id==='username'||name==='username') sc+=50;
          if (id.includes('user')||name.includes('user')||id.includes('email')||name.includes('email')) sc+=30;
          if (ph.includes('email')||al.includes('email')||ph.includes('phone')||al.includes('phone')) sc+=20;
          if (dialog && dialog.contains(el)) sc+=25;
          if (sc>bestScore){best=el;bestScore=sc;}
        }
        if (!best || bestScore < 20) return null;
        if (best.id) return '#' + CSS.escape(best.id);
        if (best.name) return `input[name="${best.name}"]`;
        return null;
        """
    )


def _wait_for_username(driver: Driver, *, timeout: float = 20.0) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        disable_webauthn_prompts(driver)
        sel = _find_username_selector(driver)
        if sel:
            return sel
        text = _page_lower(driver)
        if "get a code" in text or _otp_entry_visible(driver):
            return None  # already past username
        time.sleep(0.35)
    return _find_username_selector(driver)


def _fill_selector(driver: Driver, selector: str, value: str) -> bool:
    try:
        ok = driver.run_js(
            f"""
            const el = document.querySelector({selector!r});
            if (!el) return false;
            el.focus({{preventScroll:true}});
            const proto = window.HTMLInputElement && window.HTMLInputElement.prototype;
            const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(el, {value!r});
            else el.value = {value!r};
            for (const ev of ['input','change','blur']) {{
              el.dispatchEvent(new Event(ev, {{bubbles:true}}));
            }}
            return (el.value || '') === {value!r}
              || (el.value || '').toLowerCase() === {value!r}.toLowerCase();
            """
        )
        return bool(ok)
    except Exception:
        try:
            driver.type(selector, value)
            return True
        except Exception:
            return False


def _click_login_continue(driver: Driver) -> bool:
    clicked = driver.run_js(
        """
        const bad = (t) => t.includes('create account') || t.includes('passkey');
        const nodes = Array.from(document.querySelectorAll(
          'button, [role="button"], input[type="submit"]'
        ));
        const dialog = document.querySelector('[role="dialog"]');
        const ranked = [];
        for (const n of nodes) {
          const t = ((n.innerText || n.value || '') + '').replace(/\\s+/g,' ').trim().toLowerCase();
          if (!t || bad(t)) continue;
          let score = 0;
          if (t === 'continue' || t === 'next') score = 30;
          else if (t.startsWith('continue') || t.startsWith('next')) score = 20;
          else if (t === 'sign in' && (dialog && dialog.contains(n) || n.closest('form'))) score = 15;
          else continue;
          if (dialog && dialog.contains(n)) score += 10;
          ranked.push({n,t,score});
        }
        ranked.sort((a,b)=>b.score-a.score);
        if (ranked[0]) { ranked[0].n.click(); return ranked[0].t; }
        for (const s of [
          'button[type="submit"]',
          '[data-test="login-continue"]',
          '[data-test*="continue" i]',
        ]) {
          const el = document.querySelector(s);
          if (!el) continue;
          const t = ((el.innerText||el.value||'')+'').toLowerCase();
          if (bad(t)) continue;
          el.click(); return s;
        }
        return null;
        """
    )
    if clicked:
        print(f"[LOGIN] continue → {clicked!r}")
        return True
    return False


def open_login(driver: Driver) -> None:
    disable_webauthn_prompts(driver)
    driver.get(TARGET_LOGIN)
    sel = _wait_for_username(driver, timeout=18)
    if sel:
        print(f"[LOGIN] username field: {sel}")
        return
    # Fallback: account → may bounce to login
    print("[LOGIN] retry via /account")
    driver.get(TARGET_ACCOUNT)
    sel = _wait_for_username(driver, timeout=12)
    if sel:
        print(f"[LOGIN] username field: {sel}")
        return
    print("[LOGIN] warning: username field not found yet")


def login_with_email_otp(
    driver: Driver,
    *,
    email: str,
    timeout: float = 120.0,
) -> bool:
    """Full email → Get a code → Gmail OTP → verify. Returns True if signed in."""
    if check_signed_in(driver):
        print("[LOGIN] already signed in")
        return True

    open_login(driver)
    if looks_signed_in(driver):
        return True

    # Username / email step
    for attempt in range(1, 5):
        if _otp_entry_visible(driver) or "get a code" in _page_lower(driver):
            break
        sel = _wait_for_username(driver, timeout=8)
        if not sel:
            print(f"[LOGIN] no username field (attempt {attempt})")
            open_login(driver)
            continue
        if not _fill_selector(driver, sel, email):
            print(f"[LOGIN] could not fill {sel}")
            continue
        print(f"[LOGIN] entered email via {sel}")
        time.sleep(0.2)
        _click_login_continue(driver)
        time.sleep(0.9)
        disable_webauthn_prompts(driver)
        if _otp_entry_visible(driver) or "get a code" in _page_lower(driver):
            break
        _click_login_continue(driver)
        time.sleep(0.7)

    disable_webauthn_prompts(driver)
    code_requested_at = datetime.now(timezone.utc)
    print("[LOGIN] Get a code (email OTP, not passkey)")
    if not request_email_code(driver, force_new=False):
        for _ in range(12):
            if _otp_entry_visible(driver):
                break
            click_get_a_code_button(driver)
            time.sleep(0.12)
    if not _otp_entry_visible(driver):
        driver.run_js(
            """
            const nodes = Array.from(document.querySelectorAll('button,a,[role="button"]'));
            for (const n of nodes) {
              const t=((n.innerText||'')+'').replace(/\\s+/g,' ').trim().toLowerCase();
              if (t.includes('get a code') || t.includes('email me a code')
                  || t.includes('send a code') || t.includes('email code')) {
                n.click(); return t;
              }
            }
            return null;
            """
        )
        time.sleep(0.6)
        request_email_code(driver, force_new=False)

    print(f"[LOGIN] waiting ≤{int(timeout)}s for Gmail OTP…")
    code = wait_for_gmail_otp(newer_than=code_requested_at, timeout_seconds=timeout)
    if not code:
        from scalping.bots.target.config import read_target_otp

        deadline = time.time() + 45
        while time.time() < deadline and not code:
            code = read_target_otp() or wait_for_gmail_otp(
                newer_than=code_requested_at, timeout_seconds=4
            )
            time.sleep(1.5)
    if not code:
        print("[LOGIN] no OTP received from Gmail / TARGET_OTP")
        return False

    print(f"[LOGIN] submitting OTP ({len(code)} digits)")
    submit_otp(driver, code)
    deadline = time.time() + 30
    while time.time() < deadline:
        if looks_signed_in(driver):
            break
        url = (driver.current_url or "").lower()
        if "target.com" in url and "/login" not in url:
            break
        time.sleep(0.4)

    ok = check_signed_in(driver)
    print(f"[LOGIN] signed_in={ok}")
    return ok


def save_session(driver: Driver, *, known_signed_in: bool | None = None) -> dict[str, Any]:
    """Persist cookies from a stable www.target.com context (avoids CDP context errors)."""
    try:
        driver.get(TARGET_HOME)
        time.sleep(1.0)
    except Exception as exc:
        print(f"[LOGIN] navigate home before save: {exc}")

    cookies: list = []
    local_storage: dict = {}
    try:
        cookies, local_storage = driver.get_cookies_and_local_storage()
    except Exception as exc:
        print(f"[LOGIN] get_cookies_and_local_storage failed: {exc}")
        try:
            cookies = driver.get_cookies() or []
        except Exception as exc2:
            print(f"[LOGIN] get_cookies failed: {exc2}")
            cookies = []

    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": driver.current_url,
        "profile": str(PROFILE_DIR),
        "cookies": cookies,
        "local_storage": local_storage,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    COOKIES_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    signed = (
        bool(known_signed_in)
        if known_signed_in is not None
        else looks_signed_in(driver)
    )
    meta = {
        "saved_at": payload["saved_at"],
        "cookie_count": len(cookies),
        "final_url": driver.current_url,
        "signed_in": signed,
    }
    SESSION_META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[LOGIN] saved {len(cookies)} cookies → {COOKIES_PATH}")
    return meta


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def _browser_ensure_session(driver: Driver, data: dict) -> dict[str, Any]:
    email = str(data.get("email") or "")
    timeout = float(data.get("timeout") or 120)
    force = bool(data.get("force"))

    if not force and check_signed_in(driver):
        print("[LOGIN] session OK — already signed in")
        return save_session(driver, known_signed_in=True)

    if not email:
        raise RuntimeError("GMAIL_LOGIN missing — cannot auto-login")

    ok = login_with_email_otp(driver, email=email, timeout=timeout)
    if not ok:
        raise RuntimeError(
            "Auto-login failed. Check GMAIL_APP_PASSWORD / Target email code delivery."
        )
    return save_session(driver, known_signed_in=True)


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def _browser_check_only(driver: Driver, data: dict) -> dict[str, Any]:
    ok = check_signed_in(driver)
    return {"signed_in": ok, "url": driver.current_url}


def ensure_target_session(
    *,
    force: bool = False,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Ensure bot Chrome profile is logged into Target; login + save if stale."""
    prepare_runtime()
    from scalping.bots.target.config import PROJECT_ROOT

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    creds = load_gmail_credentials()
    if not creds.is_configured:
        raise RuntimeError(
            "Set GMAIL_LOGIN and GMAIL_APP_PASSWORD in .env for auto Target login"
        )
    return _browser_ensure_session(
        {"email": creds.login, "timeout": timeout, "force": force}
    )


def is_target_session_logged_in() -> bool:
    prepare_runtime()
    result = _browser_check_only({})
    return bool(result.get("signed_in"))
