"""Live Target drop probe — dump stock + cart ATC responses while inventory is hot.

Usage:
  uv run python -m scalping.bots.target.diagnose --config configs/target/tonight.json
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from botasaurus.browser import Driver, browser

from scalping.bots.target.api import (
    VARIANT_ORDER,
    cart_api_add,
    poll_fulfillment_api,
    warm_cart_session,
)
from scalping.bots.target.config import load_config
from scalping.bots.target.runtime import (
    CHROME_ADD_ARGUMENTS,
    PROFILE_DIR,
    prepare_runtime,
)
from scalping.bots.target.stock import (
    _buybox_stock_probe,
    check_stock,
    open_product,
)

OUT_DIR = Path.home() / ".scalping" / "logs" / "target"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe(obj, limit: int = 4000):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)[:limit]


@browser(
    profile=str(PROFILE_DIR),
    tiny_profile=False,
    headless=False,
    block_images=False,
    output=None,
    reuse_driver=True,
    add_arguments=CHROME_ADD_ARGUMENTS,
    close_on_crash=True,
)
def run_diagnose(driver: Driver, data: dict):
    config = data["config"]
    item = config.enabled_items[0]
    pause = float(data.get("pause") or 1.25)
    rounds = int(data.get("rounds") or 3)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"diagnose_{item.tcin}_{_ts()}.json"
    report: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tcin": item.tcin,
        "url": item.normalized_url,
        "label": item.label,
        "rounds": [],
    }

    print(f"[DIAG] login check skipped (data probe) — using existing profile cookies")
    report["login_ok"] = None
    # Do NOT call ensure_signed_in — auto OTP spam trips Target's error wall.
    # Manually: ./scripts/session-target.sh when you need a fresh login.

    print(f"[DIAG] open PDP {item.normalized_url}")
    open_product(driver, item, force_navigate=True)
    time.sleep(0.4)

    try:
        report["page_url"] = driver.current_url
    except Exception:
        report["page_url"] = None

    for r in range(1, rounds + 1):
        print(f"\n[DIAG] ===== round {r}/{rounds} =====")
        round_data: dict = {"round": r, "at": datetime.now(timezone.utc).isoformat()}

        # DOM buy box
        probe = _buybox_stock_probe(driver)
        round_data["buybox"] = _safe(probe)
        print(
            f"[DIAG] buybox root={probe.get('hasRoot')} atc={probe.get('enabledAtc')} "
            f"cell={probe.get('enabledCell')} oos={probe.get('oosText')}"
        )
        print(f"[DIAG] buybox text={str(probe.get('text') or '')[:160]!r}")

        # DOM stock classifier
        dom = check_stock(driver, item, navigate=False)
        round_data["dom_stock"] = {
            "status": dom.status.value,
            "reason": dom.reason,
            "excerpt": dom.page_text_excerpt[:300],
        }
        print(f"[DIAG] dom_stock={dom.status.value} reason={dom.reason}")

        # Redsky
        api = poll_fulfillment_api(
            driver,
            tcin=item.tcin or "",
            zip_code=config.shipping_address.zip or "87111",
            state=config.shipping_address.state or "NM",
            prefer_pickup=config.prefer_pickup,
        )
        round_data["redsky"] = {
            "status": api.status.value,
            "reason": api.reason,
            "excerpt": api.page_text_excerpt,
        }
        print(f"[DIAG] redsky={api.status.value} {api.reason} ({api.page_text_excerpt})")

        # Raw redsky dump (fuller)
        try:
            raw = driver.run_js(
                f"""
                return (async () => {{
                  const key = (window.__CONFIG__ && window.__CONFIG__.apiKey)
                    || '9f36aeafbe60771e321a7cc95a78140772ab3e96';
                  const params = new URLSearchParams({{
                    key,
                    tcins: {str(item.tcin)!r},
                    zip: {config.shipping_address.zip!r},
                    state: {config.shipping_address.state!r},
                  }});
                  const url = 'https://redsky.target.com/redsky_aggregations/v1/web/'
                    + 'product_summary_with_fulfillment_v1?' + params.toString();
                  const res = await fetch(url, {{
                    method: 'GET', mode: 'cors', credentials: 'include',
                    headers: {{ 'Accept': 'application/json' }},
                  }});
                  const data = await res.json();
                  return {{ status: res.status, data }};
                }})();
                """
            )
            round_data["redsky_raw_status"] = (
                raw.get("status") if isinstance(raw, dict) else None
            )
            # Keep fulfillment subtree only to limit size
            ful = None
            try:
                prod = (raw or {}).get("data", {}).get("data", {}).get("product") or {}
                if not prod:
                    summaries = (
                        (raw or {}).get("data", {}).get("data", {}).get("product_summaries")
                        or []
                    )
                    prod = summaries[0] if summaries else {}
                ful = {
                    "tcin": prod.get("tcin"),
                    "fulfillment": prod.get("fulfillment"),
                    "item": {
                        k: (prod.get("item") or {}).get(k)
                        for k in ("product_description", "dpci", "tcin")
                        if (prod.get("item") or {}).get(k) is not None
                    },
                }
            except Exception:
                ful = raw
            round_data["redsky_fulfillment"] = _safe(ful, 8000)
            print(f"[DIAG] redsky_raw http={round_data['redsky_raw_status']}")
        except Exception as exc:
            round_data["redsky_raw_error"] = str(exc)

        # Cart warm
        warm = warm_cart_session(driver)
        round_data["cart_warm"] = _safe(warm)
        print(f"[DIAG] cart_warm={warm}")

        # Cookie / api key hints (names only)
        try:
            meta = driver.run_js(
                """
                const names = document.cookie.split(';').map(s => s.trim().split('=')[0]).filter(Boolean);
                const interesting = names.filter(n =>
                  /access|auth|token|cart|visitor|login|id|abck|bm_|_dd/i.test(n)
                );
                return {
                  cookie_names: interesting,
                  apiKey: (window.__CONFIG__ && window.__CONFIG__.apiKey) || null,
                  href: location.href,
                };
                """
            )
            round_data["page_meta"] = _safe(meta)
            print(f"[DIAG] cookies_interesting={meta.get('cookie_names') if isinstance(meta, dict) else meta}")
        except Exception as exc:
            round_data["page_meta_error"] = str(exc)

        # Sequential ATC variants — full bodies
        round_data["atc"] = []
        atc_landed = False
        for variant in VARIANT_ORDER:
            res = cart_api_add(
                driver, tcin=item.tcin or "", quantity=1, variant=variant
            )
            entry = {
                "variant": res.variant,
                "ok": res.ok,
                "status": res.status,
                "error": res.error,
                "retry_after": res.retry_after,
                "data": _safe(res.data, 3000),
            }
            round_data["atc"].append(entry)
            print(
                f"[DIAG] ATC {variant}: status={res.status} ok={res.ok} "
                f"err={res.error!r} retry_after={res.retry_after}"
            )
            if res.data is not None:
                snippet = (
                    json.dumps(res.data, default=str)[:500]
                    if not isinstance(res.data, str)
                    else res.data[:500]
                )
                print(f"[DIAG]   body: {snippet}")
            if res.ok:
                print("[DIAG] ATC SUCCESS — stopping variants this round")
                atc_landed = True
                break
            wait = 1.2
            if res.status == 429:
                wait = float(res.retry_after or 2.5)
            elif res.status == 401:
                wait = 2.0
            time.sleep(min(6.0, wait))

        # UI click path while buy box has shippingButton
        if not atc_landed and probe.get("enabledAtc"):
            print("[DIAG] trying UI click on buy-box ATC…")
            try:
                clicked = driver.run_js(
                    """
                    const root = document.querySelector('[data-test="@web/AddToCart/FulfillmentSection"]');
                    if (!root) return {ok:false, reason:'no_root'};
                    const btn = root.querySelector('[data-test="shippingButton"], [data-test="shipItButton"]');
                    if (!btn) return {ok:false, reason:'no_btn'};
                    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true')
                      return {ok:false, reason:'disabled'};
                    btn.click();
                    return {ok:true, text:(btn.innerText||'').slice(0,40)};
                    """
                )
                print(f"[DIAG] UI click result={clicked}")
                time.sleep(1.2)
                after = _buybox_stock_probe(driver)
                warm2 = warm_cart_session(driver)
                round_data["ui_click"] = {
                    "click": _safe(clicked),
                    "buybox_after": _safe(after),
                    "cart_after": _safe(warm2),
                }
                print(
                    f"[DIAG] after UI: atc={after.get('enabledAtc')} "
                    f"text={str(after.get('text') or '')[:120]!r} "
                    f"cart_items={warm2.get('item_count')}"
                )
            except Exception as exc:
                round_data["ui_click_error"] = str(exc)
                print(f"[DIAG] UI click error: {exc}")

        report["rounds"].append(round_data)
        out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[DIAG] wrote {out_path}")

        if r < rounds:
            # Reload PDP between rounds to refresh tokens / UI
            open_product(driver, item, force_navigate=False)
            time.sleep(pause)

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n[DIAG] DONE → {out_path}")
    return {"path": str(out_path), "login_ok": ok}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Live Target ATC/stock diagnose")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--pause", type=float, default=1.25)
    args = parser.parse_args(argv)

    prepare_runtime()
    config = load_config(args.config)
    if not config.enabled_items:
        raise SystemExit("No enabled items")
    print(f"[DIAG] item={config.enabled_items[0].label} tcin={config.enabled_items[0].tcin}")
    print(f"[DIAG] rounds={args.rounds} profile={PROFILE_DIR}")
    run_diagnose(
        {
            "config": config,
            "rounds": args.rounds,
            "pause": args.pause,
        }
    )


if __name__ == "__main__":
    main()
