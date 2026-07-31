"""Load and validate scraping/configuration.json (+ .env payment / contact).

Secrets and card data live in the project-root `.env` (gitignored). Address
defaults can live in configuration.json; env vars override when set.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

TCIN_RE = re.compile(r"/A-(\d+)", re.IGNORECASE)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "configuration.json"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ShippingAddress:
    name: str
    street: str
    street2: str
    city: str
    state: str
    zip: str
    phone: str

    def as_single_line(self) -> str:
        parts = [self.street]
        if self.street2:
            parts.append(self.street2)
        parts.append(f"{self.city}, {self.state} {self.zip}")
        return ", ".join(parts)


@dataclass(frozen=True)
class BillingAddress:
    street: str
    street2: str
    city: str
    state: str
    zip: str
    phone: str

    @property
    def is_complete(self) -> bool:
        return bool(self.street and self.city and self.state and self.zip)


@dataclass(frozen=True)
class PaymentInfo:
    card_number: str
    card_holder_name: str
    card_expiration_date: str
    card_cvv: str
    card_type: str
    use_saved_card: bool = True
    billing: BillingAddress = field(
        default_factory=lambda: BillingAddress("", "", "", "", "", "")
    )

    @property
    def has_cvv(self) -> bool:
        return bool(self.card_cvv)

    @property
    def is_complete(self) -> bool:
        """Enough to place an order: CVV always; full card if not using saved."""
        if not self.card_cvv:
            return False
        if self.use_saved_card:
            return True
        return all(
            [
                self.card_number,
                self.card_holder_name,
                self.card_expiration_date,
                self.card_cvv,
            ]
        )

    @property
    def expiration_month_year(self) -> tuple[str, str]:
        """Parse CARD_EXPIRATION_DATE into (MM, YY) or (MM, YYYY)."""
        raw = self.card_expiration_date.strip()
        for sep in ("/", "-", " "):
            if sep in raw:
                left, right = raw.split(sep, 1)
                return left.strip(), right.strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 4:
            return digits[:2], digits[2:]
        if len(digits) == 6:
            return digits[:2], digits[2:]
        return raw, ""


@dataclass(frozen=True)
class ItemConfig:
    url: str
    max_quantity: int = 1
    label: str = ""
    enabled: bool = True

    @property
    def tcin(self) -> str | None:
        match = TCIN_RE.search(self.url)
        return match.group(1) if match else None

    @property
    def normalized_url(self) -> str:
        url = self.url.strip()
        if not url.startswith("http"):
            url = "https://" + url.lstrip("/")
        if "#" in url:
            url = url.split("#", 1)[0]
        return url


@dataclass(frozen=True)
class AppConfig:
    items: list[ItemConfig]
    refresh_interval_seconds: float = 5.0
    refresh_jitter_seconds: float = 1.5
    dry_run: bool = True
    place_order: bool = False
    prefer_pickup: bool = True
    preferred_store_name: str = "Albuquerque Wyoming"
    max_atc_retries: int = 3
    checkout_auth_timeout_seconds: float = 300.0
    shipping_address: ShippingAddress = field(
        default_factory=lambda: ShippingAddress(
            name="",
            street="3604 Garcia St NE",
            street2="",
            city="Albuquerque",
            state="NM",
            zip="87111",
            phone="",
        )
    )
    payment: PaymentInfo = field(
        default_factory=lambda: PaymentInfo("", "", "", "", "")
    )

    @property
    def enabled_items(self) -> list[ItemConfig]:
        return [item for item in self.items if item.enabled]

    def with_place_order(self, enabled: bool = True) -> AppConfig:
        return replace(self, dry_run=not enabled, place_order=enabled)

    def as_dry_run(self) -> AppConfig:
        return replace(self, dry_run=True, place_order=False)


def extract_tcin(url: str) -> str | None:
    match = TCIN_RE.search(url)
    return match.group(1) if match else None


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def reload_dotenv(env_path: Path | None = None) -> None:
    """Re-read .env so TARGET_OTP can be pasted while the bot waits."""
    load_dotenv(env_path or (PROJECT_ROOT / ".env"), override=True)


def load_payment_from_env(env_path: Path | None = None) -> PaymentInfo:
    reload_dotenv(env_path)
    billing = BillingAddress(
        street=_env("BILLING_STREET") or _env("SHIPPING_STREET"),
        street2=_env("BILLING_STREET2") or _env("SHIPPING_STREET2"),
        city=_env("BILLING_CITY") or _env("SHIPPING_CITY"),
        state=_env("BILLING_STATE") or _env("SHIPPING_STATE"),
        zip=_env("BILLING_ZIP") or _env("SHIPPING_ZIP"),
        phone=_env("BILLING_PHONE") or _env("SHIPPING_PHONE"),
    )
    return PaymentInfo(
        card_number=re.sub(r"\s+", "", _env("CARD_NUMBER")),
        card_holder_name=_env("CARD_HOLDER_NAME"),
        card_expiration_date=_env("CARD_EXPIRATION_DATE"),
        card_cvv=_env("CARD_CVV"),
        card_type=_env("CARD_TYPE"),
        use_saved_card=_env_bool("USE_SAVED_CARD", True),
        billing=billing,
    )


def read_target_otp(env_path: Path | None = None) -> str:
    reload_dotenv(env_path)
    return re.sub(r"\s+", "", _env("TARGET_OTP"))


def clear_target_otp(env_path: Path | None = None) -> None:
    """Remove TARGET_OTP from .env after successful use (best-effort)."""
    path = env_path or (PROJECT_ROOT / ".env")
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        if line.strip().startswith("TARGET_OTP="):
            out.append("TARGET_OTP=")
        else:
            out.append(line)
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    reload_dotenv()

    items_raw = raw.get("items") or raw.get("Items") or []
    items: list[ItemConfig] = []
    for entry in items_raw:
        items.append(
            ItemConfig(
                url=entry["url"],
                max_quantity=int(entry.get("max_quantity", 1)),
                label=str(entry.get("label") or extract_tcin(entry["url"]) or "item"),
                enabled=bool(entry.get("enabled", True)),
            )
        )

    shipping_raw = raw.get("shipping_address") or {}
    shipping = ShippingAddress(
        name=_env("SHIPPING_NAME") or str(shipping_raw.get("name", "")),
        street=_env("SHIPPING_STREET")
        or str(shipping_raw.get("street", "3604 Garcia St NE")),
        street2=_env("SHIPPING_STREET2") or str(shipping_raw.get("street2", "")),
        city=_env("SHIPPING_CITY") or str(shipping_raw.get("city", "Albuquerque")),
        state=_env("SHIPPING_STATE") or str(shipping_raw.get("state", "NM")),
        zip=_env("SHIPPING_ZIP") or str(shipping_raw.get("zip", "87111")),
        phone=_env("SHIPPING_PHONE") or str(shipping_raw.get("phone", "")),
    )

    auth_timeout = float(
        _env("CHECKOUT_AUTH_TIMEOUT_SECONDS")
        or raw.get("checkout_auth_timeout_seconds", 300)
    )

    return AppConfig(
        items=items,
        refresh_interval_seconds=float(raw.get("refresh_interval_seconds", 5)),
        refresh_jitter_seconds=float(raw.get("refresh_jitter_seconds", 1.5)),
        dry_run=bool(raw.get("dry_run", True)),
        place_order=bool(raw.get("place_order", False)),
        prefer_pickup=bool(raw.get("prefer_pickup", True)),
        preferred_store_name=str(
            raw.get("preferred_store_name", "Albuquerque Wyoming")
        ),
        max_atc_retries=int(raw.get("max_atc_retries", 3)),
        checkout_auth_timeout_seconds=auth_timeout,
        shipping_address=shipping,
        payment=load_payment_from_env(),
    )
