"""Module C: build the baseline list of Chrome extension IDs to track.

Sources: category pages, keyword searches, Chrome Web Store links found in
GitHub READMEs, and Chrome IDs matched by the Firefox module.
Run once, then occasionally: python -m radar.chrome_seeder
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from typing import Any

from radar.chrome_search import ChromeSearcher
from radar.utils import HttpError, load_config, load_env, make_session, now_iso, setup_logging

log = logging.getLogger(__name__)

MODULE = "chrome_seeder"
_ID_RE = re.compile(r"([a-p]{32})")


def chrome_searcher(cfg: dict[str, Any]) -> ChromeSearcher:
    """ChromeSearcher with a browser User-Agent."""
    return ChromeSearcher(cfg, make_session(cfg, {"User-Agent": cfg["chrome_search"]["user_agent"]}))


def ids_from_sheet(client: Any) -> list[dict[str, str]]:
    """IDs already known from Phase 1: GitHub cws_link and Firefox matches."""
    found: list[dict[str, str]] = []
    for r in client.read_records("GitHub_Repos"):
        m = _ID_RE.search(r.get("cws_link", ""))
        if m:
            found.append({"ext_id": m.group(1), "name": r.get("name", ""), "source": "github_readme"})
    for r in client.read_records("Master"):
        if r.get("source") == "firefox" and _ID_RE.fullmatch(r.get("chrome_id", "")):
            found.append({"ext_id": r["chrome_id"], "name": r.get("name", ""), "source": "firefox_match"})
    return found


def ids_from_store(cfg: dict[str, Any], searcher: ChromeSearcher) -> tuple[list[dict[str, str]], int]:
    """IDs from category pages and keyword searches. Returns (ids, error_count)."""
    ch = cfg["chrome"]
    found: list[dict[str, str]] = []
    errors = 0
    pages = [(f"category:{s}", ch["category_url"].format(slug=s)) for s in ch["categories"]]
    for source, url in pages:
        try:
            for r in searcher.list_page(url):
                found.append({"ext_id": r["id"], "name": r["name"], "source": source})
        except HttpError as e:
            errors += 1
            log.warning("category page failed %s: %s", url, e)
    for kw in ch["seed_keywords"]:
        try:
            for r in searcher.search(kw):
                found.append({"ext_id": r["id"], "name": r["name"], "source": f"search:{kw}"})
        except HttpError as e:
            errors += 1
            log.warning("search failed %r: %s", kw, e)
    return found, errors


def add_ids(client: Any, cfg: dict[str, Any], candidates: list[dict[str, str]], ts: str, limit: int | None = None) -> int:
    """Add new unique IDs to Chrome_IDs without passing chrome.max_ids. Returns how many were added."""
    existing = {r["ext_id"] for r in client.read_records("Chrome_IDs") if r.get("ext_id")}
    room = int(cfg["chrome"]["max_ids"]) - len(existing)
    if limit is not None:
        room = min(room, limit)
    new: list[dict[str, Any]] = []
    seen = set(existing)
    for c in candidates:
        if room <= len(new):
            break
        if c["ext_id"] in seen:
            continue
        seen.add(c["ext_id"])
        new.append({**c, "active": "TRUE"})
    if new:
        client.upsert("Chrome_IDs", new, ts, only_new=True)
    return len(new)


def run(cfg: dict[str, Any], client: Any, searcher: ChromeSearcher | None = None) -> dict[str, int]:
    """Collect IDs from the sheet and the store, write new ones to Chrome_IDs."""
    started, ts = time.time(), now_iso()
    searcher = searcher or chrome_searcher(cfg)
    added = errors = 0
    note = ""
    try:
        # Sheet sources first: these are items we already care about.
        candidates = ids_from_sheet(client)
        store, errors = ids_from_store(cfg, searcher)
        candidates += store
        added = add_ids(client, cfg, candidates, ts)
        note = f"{len(candidates)} candidates, {added} new ids"
        log.info(note)
    except Exception as e:
        errors += 1
        note = f"FAILED: {e}"
        raise
    finally:
        client.log_run(ts, MODULE, added, errors, time.time() - started, note)
    return {"processed": added, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Module C: seed Chrome extension IDs")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    load_env()
    setup_logging()
    from radar.sheets import SheetClient

    client = SheetClient.from_env()
    client.ensure_tabs()
    log.info("seeder done: %s", run(load_config(args.config), client))


if __name__ == "__main__":
    main()
