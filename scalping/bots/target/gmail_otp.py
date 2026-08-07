"""Fetch Target checkout one-time codes from Gmail via IMAP.

Requires an App Password (not your normal Google password):
https://myaccount.google.com/apppasswords

Env:
  GMAIL_LOGIN=you@gmail.com
  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
"""

from __future__ import annotations

import email
import imaplib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message

from dotenv import load_dotenv

from scalping.bots.target.config import PROJECT_ROOT

# Target verification codes are typically 6 digits; allow 4–8.
OTP_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")
TARGET_FROM_RE = re.compile(r"target\.com|@target\b", re.IGNORECASE)
TARGET_SUBJECT_RE = re.compile(
    r"code|verif|sign.?in|security|passcode|one.?time|otp",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GmailCredentials:
    login: str
    app_password: str

    @property
    def is_configured(self) -> bool:
        return bool(self.login and self.app_password)


def load_gmail_credentials(env_path=None) -> GmailCredentials:
    load_dotenv(env_path or (PROJECT_ROOT / ".env"), override=True)
    login = os.getenv("GMAIL_LOGIN", "").strip()
    # App passwords are often copied with spaces; IMAP wants them stripped.
    password = re.sub(r"\s+", "", os.getenv("GMAIL_APP_PASSWORD", "").strip())
    return GmailCredentials(login=login, app_password=password)


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _message_body_text(msg: Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype not in {"text/plain", "text/html"}:
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                parts.append(payload.decode(charset, errors="replace"))
            except Exception:
                continue
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            pass
    return "\n".join(parts)


def extract_otp_candidates(text: str) -> list[str]:
    """Return likely OTP codes from email subject/body, most-likely first."""
    if not text:
        return []
    # Prefer codes near verification wording.
    preferred: list[str] = []
    for match in re.finditer(
        r"(?:code|passcode|one[ -]?time|otp|is)[^\d]{0,40}(\d{4,8})",
        text,
        flags=re.IGNORECASE,
    ):
        preferred.append(match.group(1))
    general = OTP_RE.findall(text)
    ordered: list[str] = []
    for code in preferred + general:
        if code not in ordered and not _looks_like_year_or_zip(code):
            ordered.append(code)
    # Prefer 6-digit codes (Target's usual length)
    ordered.sort(key=lambda c: (0 if len(c) == 6 else 1, -len(c)))
    return ordered


def _looks_like_year_or_zip(code: str) -> bool:
    if len(code) == 4 and code.startswith(("19", "20")):
        return True
    return False


def _is_target_otp_message(from_addr: str, subject: str, body: str) -> bool:
    blob = f"{from_addr}\n{subject}\n{body[:4000]}"
    if not TARGET_FROM_RE.search(from_addr) and "target" not in subject.lower():
        # Still allow if body clearly mentions Target verification.
        if "target" not in body[:2000].lower():
            return False
    if TARGET_SUBJECT_RE.search(subject) or TARGET_SUBJECT_RE.search(body[:1500]):
        return True
    return bool(extract_otp_candidates(f"{subject}\n{body}"))


def fetch_latest_target_otp(
    creds: GmailCredentials | None = None,
    *,
    newer_than: datetime | None = None,
    mailbox: str = "INBOX",
) -> str | None:
    """IMAP-search recent mail for a Target verification code.

    `newer_than` should be set just before clicking "Get a code" so we ignore
    older OTPs. Returns the best matching code or None.
    """
    creds = creds or load_gmail_credentials()
    if not creds.is_configured:
        return None

    since = newer_than or (datetime.now(timezone.utc) - timedelta(minutes=10))
    # IMAP SINCE is date-only (no time); filter more precisely after fetch.
    since_date = since.astimezone(timezone.utc).strftime("%d-%b-%Y")

    try:
        client = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        client.login(creds.login, creds.app_password)
    except Exception as exc:
        print(f"[GMAIL] IMAP login failed: {exc}")
        return None

    try:
        client.select(mailbox)
        # Gmail IMAP: keep SEARCH simple — OR is binary and easy to break.
        # Prefer Gmail's extended raw query when available.
        ids: list[bytes] = []
        queries = [
            f'(X-GM-RAW "newer_than:1d (from:target.com OR subject:code OR subject:verification OR subject:Target)")',
            f'(SINCE {since_date} FROM "target.com")',
            f'(SINCE {since_date} SUBJECT "Target")',
            f'(SINCE {since_date} SUBJECT "code")',
            f"(SINCE {since_date})",
        ]
        for query in queries:
            try:
                status, data = client.search(None, query)
            except Exception:
                continue
            if status == "OK" and data and data[0]:
                ids = data[0].split()
                if ids:
                    break
        if not ids:
            return None
        # Newest first
        for msg_id in reversed(ids[-25:]):
            status, fetched = client.fetch(msg_id, "(RFC822)")
            if status != "OK" or not fetched or not fetched[0]:
                continue
            raw = fetched[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(raw)
            from_addr = _decode_header_value(msg.get("From"))
            subject = _decode_header_value(msg.get("Subject"))
            body = _message_body_text(msg)
            if not _is_target_otp_message(from_addr, subject, body):
                continue

            # Date filter (IMAP SINCE is coarse)
            try:
                parsed = email.utils.parsedate_to_datetime(msg.get("Date", ""))
                if parsed is not None:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    if parsed < since - timedelta(seconds=30):
                        continue
            except Exception:
                pass

            candidates = extract_otp_candidates(f"{subject}\n{body}")
            if candidates:
                print(f"[GMAIL] Found Target OTP in: {subject[:80]!r}")
                return candidates[0]
        return None
    finally:
        try:
            client.logout()
        except Exception:
            pass


def wait_for_gmail_otp(
    *,
    newer_than: datetime,
    timeout_seconds: float = 90.0,
    poll_seconds: float = 1.5,
    creds: GmailCredentials | None = None,
) -> str | None:
    """Poll Gmail until a fresh Target OTP appears or timeout."""
    import time

    creds = creds or load_gmail_credentials()
    if not creds.is_configured:
        return None

    deadline = time.time() + max(5.0, timeout_seconds)
    while time.time() < deadline:
        code = fetch_latest_target_otp(creds, newer_than=newer_than)
        if code:
            return code
        time.sleep(poll_seconds)
    return None
