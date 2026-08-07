"""Captcha solver interfaces (CapSolver / 2Captcha Turnstile)."""

from __future__ import annotations

import os
import random
import time
import urllib.parse
from dataclasses import dataclass
from typing import Protocol

from scalping.core.http import http_json
from scalping.core.logging import get_logger

log = get_logger("scalping.captcha")


class CaptchaSolver(Protocol):
    def solve_turnstile(self, *, website_url: str, site_key: str) -> str: ...


@dataclass
class CapSolver:
    api_key: str
    max_polls: int = 60

    def solve_turnstile(self, *, website_url: str, site_key: str) -> str:
        if not site_key:
            raise RuntimeError("turnstile site_key is empty")
        log.info("CapSolver AntiTurnstileTaskProxyLess")
        status, created, _ = http_json(
            "POST",
            "https://api.capsolver.com/createTask",
            payload={
                "clientKey": self.api_key,
                "task": {
                    "type": "AntiTurnstileTaskProxyLess",
                    "websiteURL": website_url,
                    "websiteKey": site_key,
                },
            },
        )
        if status >= 400 or not isinstance(created, dict):
            raise RuntimeError(f"CapSolver createTask failed: {status} {created}")
        if created.get("errorId"):
            raise RuntimeError(f"CapSolver createTask error: {created}")
        task_id = created.get("taskId")
        if not task_id:
            raise RuntimeError(f"CapSolver missing taskId: {created}")

        for i in range(1, self.max_polls + 1):
            time.sleep(2.0 if i > 1 else 1.0)
            _, result, _ = http_json(
                "POST",
                "https://api.capsolver.com/getTaskResult",
                payload={"clientKey": self.api_key, "taskId": task_id},
            )
            if not isinstance(result, dict):
                continue
            if result.get("status") == "ready":
                token = (result.get("solution") or {}).get("token")
                if not token:
                    raise RuntimeError(f"CapSolver ready but no token: {result}")
                log.info("CapSolver token ok len=%s", len(token))
                return str(token)
            if result.get("errorId"):
                raise RuntimeError(f"CapSolver getTaskResult error: {result}")
            log.debug("CapSolver waiting (%s)", i)
        raise TimeoutError("CapSolver timed out")


@dataclass
class TwoCaptcha:
    api_key: str
    max_polls: int = 60

    def solve_turnstile(self, *, website_url: str, site_key: str) -> str:
        log.info("2Captcha TurnstileTaskProxyless")
        create_url = (
            "https://2captcha.com/in.php"
            f"?key={self.api_key}&method=turnstile"
            f"&sitekey={urllib.parse.quote(site_key)}"
            f"&pageurl={urllib.parse.quote(website_url)}"
            "&json=1"
        )
        _, created, _ = http_json("GET", create_url)
        if not isinstance(created, dict) or created.get("status") != 1:
            raise RuntimeError(f"2Captcha create failed: {created}")
        req_id = created.get("request")
        for i in range(1, self.max_polls + 1):
            time.sleep(5.0 if i > 1 else 3.0)
            _, result, _ = http_json(
                "GET",
                f"https://2captcha.com/res.php?key={self.api_key}&action=get&id={req_id}&json=1",
            )
            if isinstance(result, dict) and result.get("status") == 1:
                token = str(result.get("request") or "")
                log.info("2Captcha token ok len=%s", len(token))
                return token
            if isinstance(result, dict) and result.get("status") == 0:
                req = str(result.get("request") or "")
                if "NOT_READY" not in req:
                    raise RuntimeError(f"2Captcha error: {result}")
            log.debug("2Captcha waiting (%s)", i)
        raise TimeoutError("2Captcha timed out")


@dataclass
class RetryingSolver:
    inner: CaptchaSolver
    retries: int = 3

    def solve_turnstile(self, *, website_url: str, site_key: str) -> str:
        last: Exception | None = None
        attempts = max(1, self.retries)
        for attempt in range(1, attempts + 1):
            try:
                return self.inner.solve_turnstile(
                    website_url=website_url, site_key=site_key
                )
            except Exception as exc:
                last = exc
                log.warning("solver attempt %s/%s failed: %s", attempt, attempts, exc)
                if attempt < attempts:
                    time.sleep(0.4 * attempt + random.uniform(0, 0.3))
        assert last is not None
        raise last


def load_solver(
    name: str | None = None,
    *,
    api_key: str | None = None,
    retries: int = 3,
) -> CaptchaSolver:
    """Build a solver from env / explicit args.

    Env: CAPSOLVER_API_KEY, TWOCAPTCHA_API_KEY, ROUND1_SOLVER / CAPTCHA_SOLVER
    """
    solver_name = (name or os.getenv("CAPTCHA_SOLVER") or os.getenv("ROUND1_SOLVER") or "capsolver").lower()
    key = (
        api_key
        or os.getenv("CAPSOLVER_API_KEY")
        or os.getenv("TWOCAPTCHA_API_KEY")
        or os.getenv("CAPTCHA_API_KEY")
        or ""
    )
    if not key:
        raise RuntimeError(
            "No captcha API key. Set CAPSOLVER_API_KEY or TWOCAPTCHA_API_KEY in .env"
        )
    if solver_name in {"capsolver", "cap"}:
        inner: CaptchaSolver = CapSolver(api_key=key)
    elif solver_name in {"2captcha", "twocaptcha", "2cap"}:
        inner = TwoCaptcha(api_key=key)
    else:
        raise RuntimeError(f"Unknown solver={solver_name!r}")
    return RetryingSolver(inner=inner, retries=retries)
