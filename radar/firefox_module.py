"""Module B: popular Firefox add-ons that have no Chrome Web Store equivalent.

Run alone: python -m radar.firefox_module [--force]
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

import requests

from radar.chrome_search import ChromeSearcher
from radar.utils import load_config, load_env, make_session, now_iso, request_with_retry, setup_logging

log = logging.getLogger(__name__)

MODULE = "firefox"


def _text(value: Any, lang: str) -> str:
    """AMO translated fields come as {'en-US': '...'}; pick lang, else any."""
    if isinstance(value, dict):
        return value.get(lang) or next(iter(value.values()), "") or ""
    return value or ""


def fetch_addons(session: requests.Session, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Top add-ons sorted by users, up to firefox.max_addons."""
    ff = cfg["firefox"]
    limit, size = int(ff["max_addons"]), int(ff["page_size"])
    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < limit:
        resp = request_with_retry(
            session, f"{ff['api_base']}/addons/search/", cfg,
            params={"sort": "users", "type": "extension", "app": "firefox",
                    "page_size": size, "page": page, "lang": ff["lang"]},
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        out.extend(data.get("results", []))
        if not data.get("next"):
            break
        page += 1
        time.sleep(float(ff["api_delay_seconds"]))
    return out[:limit]


def parse_addon(item: dict[str, Any], lang: str) -> dict[str, Any]:
    """Fields we store from one AMO search result."""
    ratings = item.get("ratings") or {}
    lic = ((item.get("current_version") or {}).get("license") or {})
    return {
        "slug": item.get("slug", ""),
        "name": _text(item.get("name"), lang),
        "url": item.get("url", ""),
        "users": item.get("average_daily_users", 0),
        "rating": round(float(ratings.get("average") or 0), 2),
        "rating_count": ratings.get("count", 0),
        "last_updated": item.get("last_updated", ""),
        "category": ",".join(item.get("categories") or []) if isinstance(item.get("categories"), list) else "",
        "license": (lic.get("slug") or "").lower(),
        "description": _text(item.get("summary"), lang)[:500],
    }


def to_master(addon: dict[str, Any]) -> dict[str, Any]:
    """Master tab row for an add-on."""
    flags = {"not_found": "FIREFOX_ONLY", "unknown": "CHROME_UNKNOWN"}.get(addon.get("chrome_check", ""), "")
    return {
        "key": addon["slug"], "source": "firefox", "name": addon["name"], "url": addon["url"],
        "users_or_stars": addon["users"], "rating": addon["rating"],
        "last_updated": addon["last_updated"], "license": addon["license"],
        "manifest_version": "", "flags": flags, "chrome_id": addon.get("chrome_id", ""),
    }


def to_alert(addon: dict[str, Any], cfg: dict[str, Any], date: str) -> dict[str, Any] | None:
    """Firefox-only add-on with enough users becomes an alert."""
    al = cfg["alerts"]
    if addon.get("chrome_check") != "not_found" or int(addon["users"] or 0) < int(al["firefox_only_min_users"]):
        return None
    flag = al["firefox_only_flag"]
    return {
        "alert_key": f"{flag}:{addon['slug']}", "flag_type": flag, "date": date,
        "source": "firefox", "item_key": addon["slug"], "name": addon["name"],
        "users": addon["users"], "link": addon["url"], "sent": "",
    }


def ran_recently(client: Any, cfg: dict[str, Any]) -> bool:
    """True if this module ran within firefox.min_days_between_runs (scrape guard)."""
    return client.ran_within(MODULE, float(cfg["firefox"]["min_days_between_runs"]))


def run(
    cfg: dict[str, Any],
    client: Any,
    amo_session: requests.Session | None = None,
    searcher: ChromeSearcher | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Fetch add-ons, check Chrome, write Firefox_Only / Master / Alerts, log the run."""
    if not force and ran_recently(client, cfg):
        log.warning("firefox module ran in the last %s days, skipping (use --force)",
                    cfg["firefox"]["min_days_between_runs"])
        return {"processed": 0, "errors": 0, "skipped": 1}

    started, ts = time.time(), now_iso()
    lang = cfg["firefox"]["lang"]
    amo_session = amo_session or make_session(cfg)
    if searcher is None:
        searcher = ChromeSearcher(cfg, make_session(cfg, {"User-Agent": cfg["chrome_search"]["user_agent"]}))
    addons: list[dict[str, Any]] = []
    errors = 0
    note = ""
    try:
        for item in fetch_addons(amo_session, cfg):
            try:
                addons.append(parse_addon(item, lang))
            except (TypeError, ValueError, AttributeError) as e:
                errors += 1
                log.warning("bad AMO item %s: %s", item.get("slug"), e)
        log.info("fetched %d add-ons", len(addons))

        if searcher.parser_works():
            for i, addon in enumerate(addons, 1):
                addon.update(searcher.check(addon["name"]))
                errors += addon["chrome_check"] == "unknown"
                if i % 25 == 0:
                    log.info("chrome checked %d/%d", i, len(addons))
        else:
            log.error("chrome search canary failed: parser may be broken, marking all unknown")
            note = "chrome canary failed; "
            for addon in addons:
                addon.update(chrome_check="unknown", chrome_match="", chrome_id="", ratio=0.0)
            errors += 1

        only = [{k: v for k, v in a.items() if k not in ("ratio", "chrome_id")}
                for a in addons if a["chrome_check"] == "not_found"]
        client.upsert("Firefox_Only", only, ts)
        client.upsert("Master", [to_master(a) for a in addons], ts)
        alerts = [x for x in (to_alert(a, cfg, ts[:10]) for a in addons) if x]
        new_alerts = client.upsert("Alerts", alerts, ts, only_new=True)
        note += f"{len(only)} firefox-only, {len(new_alerts)} new alerts"
    except Exception as e:
        errors += 1
        note += f"FAILED: {e}"
        raise
    finally:
        client.log_run(ts, MODULE, len(addons), errors, time.time() - started, note)
    return {"processed": len(addons), "errors": errors, "firefox_only": len([a for a in addons if a.get("chrome_check") == "not_found"])}


def main() -> None:
    parser = argparse.ArgumentParser(description="Module B: Firefox-only add-ons")
    parser.add_argument("--config", default=None)
    parser.add_argument("--force", action="store_true", help="ignore the once-a-week guard")
    args = parser.parse_args()
    load_env()
    setup_logging()
    from radar.sheets import SheetClient

    stats = run(load_config(args.config), SheetClient.from_env(), force=args.force)
    log.info("firefox done: %s", stats)


if __name__ == "__main__":
    main()
