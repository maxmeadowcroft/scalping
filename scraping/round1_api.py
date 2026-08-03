"""Round1 / Shortstack (cmpgn.page) API client.

Flow
----
1. Poll the campaign HTML until bootData looks healthy (not gray/empty).
2. Solve Cloudflare Turnstile via CapSolver / 2Captcha (optional for --probe).
3. POST /campaigns/start  → session_id, client_key, campaign_submit_token
4. POST /campaigns/{id}/lists/{list_id}/entries with form fields + session

This campaign's form (Phase 3) fields:
  first_name / last_name, email, custom_list_1 (location),
  custom_text_1 (phase #), custom_boolean_1 (terms)
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BOOTDATA_RE = re.compile(r"window\.bootData\s*=\s*(\{.*?\})\s*;\s*</script>", re.S)
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class EntryPerson:
    first_name: str
    last_name: str
    email: str


@dataclass
class Round1Config:
    url: str
    first_name: str = "Max"
    last_name: str = "Meadowcroft"
    email: str = "maxmeadowcroft61@gmail.com"
    # If non-empty, submit one entry per person (unique emails). Else use first/last/email above.
    entries: list[EntryPerson] = field(default_factory=list)
    parallel: bool = True
    stagger_seconds: float = 0.35  # delay between launching each parallel worker
    location_match: str = r"albuquerque|coronado|nm\t"
    phase: str = "3"
    agree_terms: bool = True
    marketing_opt_in: bool = False
    submit: bool = True
    refresh_interval_seconds: float = 0.35
    refresh_jitter_seconds: float = 0.15
    # CapSolver or 2captcha
    solver: str = "capsolver"  # capsolver | 2captcha | none
    solver_api_key: str = ""
    solver_retries: int = 3  # fresh CapSolver/2Captcha tasks on flake
    submit_retries: int = 2  # full solve→start→entry retries (not for limit_reached)
    poll_timeout_seconds: float = 0.0  # 0 = forever

    def people(self) -> list[EntryPerson]:
        if self.entries:
            return list(self.entries)
        return [
            EntryPerson(
                first_name=self.first_name,
                last_name=self.last_name,
                email=self.email,
            )
        ]


@dataclass
class BootData:
    raw: dict[str, Any]
    campaign_id: int
    token: str
    install_id: int
    server_host: str
    turnstile_site_key: str
    list_ids: list[int]
    form_list_id: int
    form_widget_id: int
    location_choices: list[str] = field(default_factory=list)

    @property
    def api_base(self) -> str:
        return (self.server_host or "https://api.lndg.page").rstrip("/")


def load_round1_config(path: Path | None = None) -> Round1Config:
    from dotenv import load_dotenv
    import os

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    cfg_path = path or (Path(__file__).resolve().parent / "configuration.round1.json")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    solver = str(raw.get("solver") or os.getenv("ROUND1_SOLVER") or "capsolver").lower()
    key = (
        os.getenv("CAPSOLVER_API_KEY")
        or os.getenv("TWOCAPTCHA_API_KEY")
        or os.getenv("CAPTCHA_API_KEY")
        or str(raw.get("solver_api_key") or "")
    )
    people: list[EntryPerson] = []
    for row in raw.get("entries") or []:
        if not isinstance(row, dict):
            continue
        email = str(row.get("email") or "").strip()
        if not email or email.startswith("..."):
            continue
        people.append(
            EntryPerson(
                first_name=str(row.get("first_name") or raw.get("first_name") or "Max"),
                last_name=str(row.get("last_name") or raw.get("last_name") or "Meadowcroft"),
                email=email,
            )
        )

    return Round1Config(
        url=str(raw["url"]),
        first_name=str(raw.get("first_name", "Max")),
        last_name=str(raw.get("last_name", "Meadowcroft")),
        email=str(raw.get("email", "")),
        entries=people,
        parallel=bool(raw.get("parallel", True)),
        stagger_seconds=float(raw.get("stagger_seconds", 0.35)),
        location_match=str(raw.get("location_match", r"albuquerque|coronado|nm\t")),
        phase=str(raw.get("phase", "3")),
        agree_terms=bool(raw.get("agree_terms", True)),
        marketing_opt_in=bool(raw.get("marketing_opt_in", False)),
        submit=bool(raw.get("submit", True)),
        refresh_interval_seconds=float(raw.get("refresh_interval_seconds", 0.35)),
        refresh_jitter_seconds=float(raw.get("refresh_jitter_seconds", 0.15)),
        solver=solver,
        solver_api_key=key,
        solver_retries=max(1, int(raw.get("solver_retries", 3) or 3)),
        submit_retries=max(1, int(raw.get("submit_retries", 2) or 2)),
        poll_timeout_seconds=float(raw.get("poll_timeout_seconds", 0) or 0),
    )


def _http_json(
    method: str,
    url: str,
    *,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: float = 25.0,
) -> tuple[int, Any, dict[str, str]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            if not raw:
                return resp.status, None, hdrs
            try:
                return resp.status, json.loads(raw.decode("utf-8")), hdrs
            except json.JSONDecodeError:
                return resp.status, raw.decode("utf-8", errors="replace"), hdrs
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = raw.decode("utf-8", errors="replace")
        return exc.code, data, {}


def fetch_campaign_html(url: str, *, timeout: float = 20.0) -> tuple[int, str]:
    status, data, _ = _http_json(
        "GET",
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": DEFAULT_UA,
            "Cache-Control": "no-cache",
        },
        timeout=timeout,
    )
    # GET returns HTML string when not JSON
    if isinstance(data, str):
        return status, data
    if data is None:
        return status, ""
    return status, json.dumps(data)


def parse_boot_data(html: str) -> BootData | None:
    m = BOOTDATA_RE.search(html or "")
    if not m:
        return None
    try:
        raw = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    app = raw.get("app") or {}
    campaign = raw.get("campaign") or {}
    lists = raw.get("lists") or []
    widgets = ((raw.get("widgets") or {}).get("collection")) or {}

    form_list_id = 0
    form_widget_id = 0
    locations: list[str] = []
    for wid, w in widgets.items():
        if w.get("type") in {"form-container-widget", "form-widget"} and w.get("list_id"):
            form_list_id = int(w["list_id"])
            form_widget_id = int(w.get("id") or wid)
        field = w.get("field") or {}
        if field.get("field_map") == "custom_list_1" and field.get("choices"):
            locations = list(field["choices"])

    list_ids = [int(x["id"]) for x in lists if "id" in x]
    if not form_list_id and list_ids:
        # Prefer the master / larger list when form widget missing
        form_list_id = list_ids[-1]

    if not campaign.get("id") or not app.get("token"):
        return None

    return BootData(
        raw=raw,
        campaign_id=int(campaign["id"]),
        token=str(app["token"]),
        install_id=int(app.get("install_id") or 0),
        server_host=str(app.get("server_host") or "https://api.lndg.page"),
        turnstile_site_key=str(app.get("turnstile_site_key") or ""),
        list_ids=list_ids,
        form_list_id=int(form_list_id),
        form_widget_id=int(form_widget_id),
        location_choices=locations,
    )


def page_looks_ready(html: str, boot: BootData | None) -> bool:
    if not boot:
        return False
    if not boot.turnstile_site_key and "turnstile" in (html or "").lower():
        return False
    if boot.form_list_id <= 0:
        return False
    # Gray/empty shells sometimes still embed bootData — require form fields.
    if not boot.location_choices:
        return False
    return True


def pick_location(choices: list[str], pattern: str) -> str | None:
    rx = re.compile(pattern, re.I)
    for choice in choices:
        # Stored as "NM\tCoronado Mall"
        flat = choice.replace("\t", " ")
        if rx.search(choice) or rx.search(flat):
            return choice
    # Albuquerque Round1 is Coronado Mall even when "albuquerque" isn't in the label.
    for choice in choices:
        if "coronado" in choice.lower() and choice.upper().startswith("NM"):
            return choice
    return None


def poll_until_ready(config: Round1Config) -> BootData:
    deadline = (
        time.time() + config.poll_timeout_seconds
        if config.poll_timeout_seconds > 0
        else None
    )
    attempt = 0
    while True:
        attempt += 1
        try:
            status, html = fetch_campaign_html(config.url)
        except Exception as exc:
            print(f"[POLL {attempt}] fetch error: {exc}")
            status, html = 0, ""
        boot = parse_boot_data(html) if html else None
        ready = page_looks_ready(html, boot)
        loc_n = len(boot.location_choices) if boot else 0
        print(
            f"[POLL {attempt}] http={status} boot={'yes' if boot else 'no'} "
            f"locations={loc_n} ready={ready}"
        )
        if ready and boot:
            return boot
        if deadline is not None and time.time() >= deadline:
            raise TimeoutError("Timed out waiting for campaign page / bootData")
        delay = config.refresh_interval_seconds + random.uniform(
            0, max(0.0, config.refresh_jitter_seconds)
        )
        time.sleep(delay)


def solve_turnstile(config: Round1Config, *, website_url: str, site_key: str) -> str:
    if not config.solver_api_key:
        raise RuntimeError(
            "No solver API key. Set CAPSOLVER_API_KEY (or TWOCAPTCHA_API_KEY) in .env"
        )
    if not site_key:
        raise RuntimeError("turnstile site_key is empty")

    attempts = max(1, int(config.solver_retries))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if config.solver in {"capsolver", "cap"}:
                token = _solve_capsolver(config.solver_api_key, website_url, site_key)
            elif config.solver in {"2captcha", "twocaptcha", "2cap"}:
                token = _solve_2captcha(config.solver_api_key, website_url, site_key)
            else:
                raise RuntimeError(
                    f"Unknown solver={config.solver!r} (use capsolver|2captcha|none)"
                )
            return token
        except Exception as exc:
            last_exc = exc
            print(f"[SOLVER] attempt {attempt}/{attempts} failed: {exc}")
            if attempt < attempts:
                time.sleep(0.4 * attempt + random.uniform(0, 0.3))
    assert last_exc is not None
    raise last_exc


def _solve_capsolver(api_key: str, website_url: str, site_key: str) -> str:
    print("[SOLVER] CapSolver AntiTurnstileTaskProxyLess…")
    status, created, _ = _http_json(
        "POST",
        "https://api.capsolver.com/createTask",
        payload={
            "clientKey": api_key,
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

    for i in range(1, 60):
        time.sleep(2.0 if i > 1 else 1.0)
        st, result, _ = _http_json(
            "POST",
            "https://api.capsolver.com/getTaskResult",
            payload={"clientKey": api_key, "taskId": task_id},
        )
        if not isinstance(result, dict):
            continue
        if result.get("status") == "ready":
            token = (result.get("solution") or {}).get("token")
            if not token:
                raise RuntimeError(f"CapSolver ready but no token: {result}")
            print(f"[SOLVER] CapSolver token ok (len={len(token)})")
            return str(token)
        if result.get("errorId"):
            # Expired / missing tasks should abort this attempt so outer retry
            # can create a fresh CapSolver task instead of polling forever.
            raise RuntimeError(f"CapSolver getTaskResult error: {result}")
        print(f"[SOLVER] waiting… ({i})")
    raise TimeoutError("CapSolver timed out")


def _solve_2captcha(api_key: str, website_url: str, site_key: str) -> str:
    print("[SOLVER] 2Captcha TurnstileTaskProxyless…")
    create_url = (
        "https://2captcha.com/in.php"
        f"?key={api_key}&method=turnstile"
        f"&sitekey={urllib.parse.quote(site_key)}"
        f"&pageurl={urllib.parse.quote(website_url)}"
        "&json=1"
    )
    status, created, _ = _http_json("GET", create_url)
    if not isinstance(created, dict) or created.get("status") != 1:
        raise RuntimeError(f"2Captcha create failed: {created}")
    req_id = created.get("request")
    for i in range(1, 60):
        time.sleep(5.0 if i > 1 else 3.0)
        st, result, _ = _http_json(
            "GET",
            f"https://2captcha.com/res.php?key={api_key}&action=get&id={req_id}&json=1",
        )
        if isinstance(result, dict) and result.get("status") == 1:
            token = str(result.get("request") or "")
            print(f"[SOLVER] 2Captcha token ok (len={len(token)})")
            return token
        if isinstance(result, dict) and result.get("request") not in {
            "CAPCHA_NOT_READY",
            "CAPTCHA_NOT_READY",
        }:
            # still waiting or hard error
            if result.get("status") == 0 and "NOT_READY" not in str(result.get("request")):
                raise RuntimeError(f"2Captcha error: {result}")
        print(f"[SOLVER] waiting… ({i})")
    raise TimeoutError("2Captcha timed out")


def api_headers(boot: BootData) -> dict[str, str]:
    origin = f"{urlparse(boot.raw.get('app', {}).get('campaign_url') or 'https://round1usa.cmpgn.page').scheme}://{urlparse(boot.raw.get('app', {}).get('campaign_url') or 'https://round1usa.cmpgn.page').netloc}"
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-SS-Campaign-Token": boot.token,
        "Origin": origin,
        "Referer": str((boot.raw.get("app") or {}).get("campaign_url") or origin),
        "User-Agent": DEFAULT_UA,
    }


def campaign_start(boot: BootData, *, turnstile_token: str | None, page_url: str) -> dict:
    payload = {
        "cached": True,
        "campaign_id": boot.campaign_id,
        "install_id": boot.install_id,
        "list_ids": boot.list_ids or [boot.form_list_id],
        "referer": "",
        "render_type": "web",
        "token": boot.token,
        "source_ref": page_url,
        "turnstile": turnstile_token,
        "ts_site": (boot.turnstile_site_key or "")[-4:],
        "country": "US",
    }
    status, data, _ = _http_json(
        "POST",
        f"{boot.api_base}/campaigns/start",
        payload=payload,
        headers=api_headers(boot),
    )
    print(f"[API] POST /campaigns/start → {status}")
    if status == 204 or data is None:
        return {"_status": status, "_note": "empty body (campaign offline or gated)"}
    if isinstance(data, dict):
        data["_status"] = status
        return data
    return {"_status": status, "_raw": data}


def build_entry_payload(
    config: Round1Config,
    boot: BootData,
    start: dict,
    *,
    location: str,
    query_only: bool = False,
    person: EntryPerson | None = None,
) -> dict:
    who = person or EntryPerson(
        first_name=config.first_name,
        last_name=config.last_name,
        email=config.email,
    )
    app = start.get("app") or start
    viewed_at = int(time.time())
    return {
        "first_name": who.first_name,
        "last_name": who.last_name,
        "email": who.email,
        "custom_list_1": location,
        "custom_text_1": config.phase,
        "custom_boolean_1": bool(config.agree_terms),
        "custom_boolean_3": bool(config.marketing_opt_in),
        # system fields from submitFormAddSystemData
        "campaign_submit_token": app.get("campaign_submit_token")
        or start.get("campaign_submit_token"),
        "country": (start.get("visitor") or {}).get("country") or "US",
        "client_key": app.get("client_key") or start.get("client_key"),
        "install_id": boot.install_id,
        "language": "en",
        "query_only": query_only,
        "recaptcha_token": "not_enabled",
        "recaptcha_key": (boot.raw.get("app") or {}).get("recaptcha_public_key"),
        "session_id": app.get("session_id") or start.get("session_id"),
        "viewed_at": viewed_at,
        "widget_id": boot.form_widget_id,
        "form_rendered_at": viewed_at,
        "behavior_signals": "",
        "client_session": {},
        "referer": "",
        "shared_platform": None,
    }


def submit_entry(boot: BootData, payload: dict) -> tuple[int, Any]:
    url = (
        f"{boot.api_base}/campaigns/{boot.campaign_id}"
        f"/lists/{boot.form_list_id}/entries"
    )
    status, data, _ = _http_json(
        "POST",
        url,
        payload=payload,
        headers=api_headers(boot),
    )
    print(f"[API] POST .../entries → {status} ({payload.get('email')})")
    return status, data


def _entry_is_limit_reached(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("entry_limit"):
        return True
    errors = data.get("errors") or {}
    base = errors.get("base") if isinstance(errors, dict) else None
    return base == "widgets.form.limit_reached"


def _should_retry_submit(*, status: int | None, data: Any, error: str | None) -> bool:
    """Retry solver/network/session flakes; do not retry hard form rejections."""
    if error:
        err_l = error.lower()
        if "limit_reached" in err_l:
            return False
        return True
    if status is None:
        return True
    if status >= 500:
        return True
    if status == 204 or status == 429:
        return True
    if _entry_is_limit_reached(data):
        return False
    if status >= 400:
        # validation / duplicate / etc. — unlikely to succeed on retry
        return False
    return False


def submit_one_person(
    config: Round1Config,
    boot: BootData,
    *,
    person: EntryPerson,
    location: str,
    query_only: bool = False,
    label: str = "",
) -> dict[str, Any]:
    """Solve Turnstile → /campaigns/start → entry for one person (with retries)."""
    tag = label or person.email
    attempts = max(1, int(config.submit_retries))
    last: dict[str, Any] = {"ok": False, "email": person.email, "error": "no_attempt"}

    for attempt in range(1, attempts + 1):
        try:
            token = solve_turnstile(
                config, website_url=config.url, site_key=boot.turnstile_site_key
            )
            start = campaign_start(boot, turnstile_token=token, page_url=config.url)
            app = start.get("app") or start
            session_id = app.get("session_id") or start.get("session_id")
            if start.get("_status") == 204 or not session_id:
                last = {
                    "ok": False,
                    "email": person.email,
                    "error": "no_session",
                    "start": start,
                    "attempt": attempt,
                }
                if attempt < attempts and _should_retry_submit(
                    status=start.get("_status"), data=start, error="no_session"
                ):
                    print(f"[{tag}] no session — retry {attempt}/{attempts}")
                    time.sleep(0.35 * attempt)
                    continue
                return last

            payload = build_entry_payload(
                config,
                boot,
                start,
                location=location,
                query_only=query_only,
                person=person,
            )
            status, data = submit_entry(boot, payload)
            ok = status < 400 and not (
                isinstance(data, dict) and (data.get("errors") or data.get("entry_limit"))
            )
            last = {
                "ok": ok,
                "email": person.email,
                "status": status,
                "data": data,
                "attempt": attempt,
            }
            print(f"[{tag}] status={status} ok={ok} attempt={attempt}/{attempts}")
            if ok:
                return last
            if attempt < attempts and _should_retry_submit(
                status=status, data=data, error=None
            ):
                print(f"[{tag}] retrying submit…")
                time.sleep(0.35 * attempt)
                continue
            return last
        except Exception as exc:
            last = {
                "ok": False,
                "email": person.email,
                "error": str(exc),
                "attempt": attempt,
            }
            print(f"[{tag}] FAILED attempt {attempt}/{attempts}: {exc}")
            if attempt < attempts and _should_retry_submit(
                status=None, data=None, error=str(exc)
            ):
                time.sleep(0.4 * attempt + random.uniform(0, 0.25))
                continue
            return last
    return last

def submit_all_people(
    config: Round1Config,
    boot: BootData,
    *,
    location: str,
    query_only: bool = False,
) -> list[dict[str, Any]]:
    """Submit for every person in config (parallel CapSolver + entry by default)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    people = config.people()
    print(f"[RUN] submitting {len(people)} entr{'y' if len(people) == 1 else 'ies'} "
          f"parallel={config.parallel} query_only={query_only}")

    if not config.parallel or len(people) == 1:
        results = []
        for i, person in enumerate(people):
            if i and config.stagger_seconds > 0:
                time.sleep(config.stagger_seconds)
            results.append(
                submit_one_person(
                    config,
                    boot,
                    person=person,
                    location=location,
                    query_only=query_only,
                    label=f"{i + 1}/{len(people)} {person.email}",
                )
            )
        return results

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(people))) as pool:
        futures = []
        for i, person in enumerate(people):
            if i and config.stagger_seconds > 0:
                time.sleep(config.stagger_seconds)
            futures.append(
                pool.submit(
                    submit_one_person,
                    config,
                    boot,
                    person=person,
                    location=location,
                    query_only=query_only,
                    label=f"{i + 1}/{len(people)} {person.email}",
                )
            )
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


def probe(config: Round1Config, *, try_solver: bool = False) -> dict:
    """Safe plumbing test against whatever the URL currently returns."""
    print(f"[PROBE] GET {config.url}")
    status, html = fetch_campaign_html(config.url)
    boot = parse_boot_data(html)
    summary = {
        "http_status": status,
        "html_bytes": len(html or ""),
        "boot_parsed": boot is not None,
    }
    if not boot:
        print("[PROBE] No bootData — page gray/empty or not a Shortstack campaign")
        return summary

    loc = pick_location(boot.location_choices, config.location_match)
    summary.update(
        {
            "campaign_id": boot.campaign_id,
            "install_id": boot.install_id,
            "api_base": boot.api_base,
            "form_list_id": boot.form_list_id,
            "form_widget_id": boot.form_widget_id,
            "turnstile_site_key": boot.turnstile_site_key,
            "location_count": len(boot.location_choices),
            "picked_location": loc,
            "phase": config.phase,
        }
    )
    print(json.dumps(summary, indent=2))

    token = None
    if try_solver:
        if not config.solver_api_key:
            print("[PROBE] try_solver set but no CAPSOLVER_API_KEY / TWOCAPTCHA_API_KEY")
        elif not boot.turnstile_site_key:
            print("[PROBE] no turnstile_site_key in bootData")
        else:
            token = solve_turnstile(
                config, website_url=config.url, site_key=boot.turnstile_site_key
            )

    start = campaign_start(boot, turnstile_token=token, page_url=config.url)
    start_keys = sorted(start.keys()) if isinstance(start, dict) else []
    print(f"[PROBE] start keys: {start_keys}")
    if start.get("_status") == 204:
        print(
            "[PROBE] start returned 204 empty — campaign is offline/ended "
            "(expected for a finished drop). Plumbing to api.lndg.page works."
        )
    elif start.get("app") or start.get("session_id") or (start.get("app") or {}).get(
        "session_id"
    ):
        print("[PROBE] start returned session data — campaign looks LIVE")
        if loc and (start.get("app") or start).get("session_id"):
            # query_only probe — should not create a real entry
            payload = build_entry_payload(
                config, boot, start, location=loc, query_only=True
            )
            st, data = submit_entry(boot, payload)
            print(f"[PROBE] query_only entry → {st}: {str(data)[:400]}")
    return {"summary": summary, "start": start}
