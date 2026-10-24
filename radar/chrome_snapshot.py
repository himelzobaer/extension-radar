"""Module D: weekly snapshot of every tracked Chrome extension's store page.

Run alone: python -m radar.chrome_snapshot [--force] [--limit N]
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import time
from datetime import datetime
from typing import Any

from radar.chrome_search import ChromeSearcher
from radar.chrome_seeder import add_ids, chrome_searcher
from radar.utils import HttpError, load_config, load_env, now_iso, setup_logging

log = logging.getLogger(__name__)

MODULE = "chrome_snapshot"

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_USERS_RE = re.compile(r"([\d,]+)\+?\s+users?\b")
_RATING_RE = re.compile(r"(\d(?:\.\d+)?) out of 5")
_RATING_COUNT_RE = re.compile(r">\s*([\d.,]+\s*[KkMm]?)\s+ratings?\s*<")
_VERSION_RE = re.compile(r">\s*Version\s*</div>\s*<div[^>]*>\s*([^<]+?)\s*<")
_UPDATED_RE = re.compile(r">\s*Updated\s*</div>\s*<div[^>]*>\s*([^<]+?)\s*<")
_PUBLISHER_RE = re.compile(r'data-publisher-id="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>')
_OFFERED_RE = re.compile(r"Offered by\s*(?:<[^>]+>\s*)*([^<]+?)\s*<")
_RELATED_RE = re.compile(r'data-item-id="([a-p]{32})"|/detail/[^/"\s]+/([a-p]{32})')


def _to_int(text: str) -> int | None:
    """'7,000,000' -> 7000000, '13.3K' -> 13300."""
    t = text.replace(",", "").strip().upper()
    mult = 1000 if t.endswith("K") else 1_000_000 if t.endswith("M") else 1
    try:
        return int(round(float(t.rstrip("KM").strip()) * mult))
    except ValueError:
        return None


def _to_iso_date(text: str) -> str:
    """'September 24, 2026' -> '2026-09-24'; unknown formats are kept as-is."""
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text.strip()


def parse_detail(page: str, final_url: str, ext_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Snapshot fields from a detail page. Never raises; problems go into status/error."""
    row: dict[str, Any] = {
        "ext_id": ext_id, "name": "", "users": "", "rating": "", "rating_count": "",
        "last_updated": "", "version": "", "developer": "", "developer_id": "",
        "status": "", "error": "", "related": [],
    }
    if cfg["chrome"]["removed_url_marker"] in (final_url or ""):
        row["status"] = "removed"
        return row
    try:
        m = _TITLE_RE.search(page)
        title = html.unescape(m.group(1)).strip() if m else ""
        row["name"] = re.sub(r"\s*-\s*Chrome Web Store\s*$", "", title)
        if row["name"] == "Chrome Web Store":
            row["name"] = ""
        if m := _USERS_RE.search(page):
            row["users"] = _to_int(m.group(1)) or 0
        if m := _RATING_RE.search(page):
            row["rating"] = float(m.group(1))
        if m := _RATING_COUNT_RE.search(page):
            row["rating_count"] = _to_int(m.group(1)) or ""
        if m := _VERSION_RE.search(page):
            row["version"] = html.unescape(m.group(1))
        if m := _UPDATED_RE.search(page):
            row["last_updated"] = _to_iso_date(html.unescape(m.group(1)))
        if m := _PUBLISHER_RE.search(page):
            row["developer_id"], row["developer"] = m.group(1), html.unescape(m.group(2))
        elif m := _OFFERED_RE.search(page):
            row["developer"] = html.unescape(m.group(1))
        row["related"] = sorted({a or b for a, b in _RELATED_RE.findall(page)} - {ext_id})
    except (re.error, ValueError, TypeError) as e:
        row["status"], row["error"] = "parse_error", str(e)[:200]
        return row

    if row["name"] and (row["users"] != "" or row["version"]):
        row["status"] = "ok"
    else:
        row["status"] = "parse_error"
        row["error"] = "name/users/version not found"
    return row


def fetch_snapshot(searcher: ChromeSearcher, cfg: dict[str, Any], ext_id: str) -> dict[str, Any]:
    """Fetch and parse one extension. HTTP failures become status 'fetch_error'."""
    url = cfg["chrome"]["detail_url"].format(id=ext_id)
    try:
        resp = searcher.fetch(url)
    except HttpError as e:
        return {"ext_id": ext_id, "status": "fetch_error", "error": str(e)[:200], "related": []}
    if resp.status_code == 404:
        return {"ext_id": ext_id, "status": "removed", "error": "", "related": []}
    return parse_detail(resp.text, str(getattr(resp, "url", "") or url), ext_id, cfg)


def to_snapshot_row(snap: dict[str, Any], date: str) -> dict[str, Any]:
    """Snapshots tab row."""
    row = {k: v for k, v in snap.items() if k != "related"}
    row.update(snapshot_key=f"{date}:{snap['ext_id']}", date=date)
    return row


def to_master(snap: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Master row. Removed/failed items only refresh last_seen, so old users/rating stay."""
    base = {"key": snap["ext_id"], "source": "chrome"}
    if snap["status"] != "ok":
        return base
    return {
        **base, "name": snap["name"], "url": cfg["chrome"]["detail_url"].format(id=snap["ext_id"]),
        "users_or_stars": snap["users"], "rating": snap["rating"], "last_updated": snap["last_updated"],
    }


def run(
    cfg: dict[str, Any], client: Any, searcher: ChromeSearcher | None = None,
    force: bool = False, limit: int | None = None,
) -> dict[str, int]:
    """Snapshot every active ID in Chrome_IDs into Snapshots, refresh Master."""
    ch = cfg["chrome"]
    if not force and client.ran_within(MODULE, float(ch["min_days_between_runs"])):
        log.warning("chrome snapshot ran in the last %s days, skipping (use --force)", ch["min_days_between_runs"])
        return {"processed": 0, "errors": 0, "skipped": 1}

    started, ts = time.time(), now_iso()
    date = ts[:10]
    searcher = searcher or chrome_searcher(cfg)
    ids = [r["ext_id"] for r in client.read_records("Chrome_IDs")
           if r.get("ext_id") and str(r.get("active", "TRUE")).upper() != "FALSE"]
    ids = ids[: limit or int(ch["max_ids"])]
    processed = errors = 0
    note = ""
    batch: list[dict[str, Any]] = []
    masters: list[dict[str, Any]] = []
    related: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    try:
        if not ids:
            note = "Chrome_IDs is empty: run radar.chrome_seeder first"
            log.warning(note)
            return {"processed": 0, "errors": 0}
        canary = fetch_snapshot(searcher, cfg, ch["canary_id"])
        if canary["status"] != "ok" or canary.get("users") in ("", None):
            raise RuntimeError(f"canary parse failed ({canary['status']}): store HTML may have changed")

        for i, ext_id in enumerate(ids, 1):
            snap = fetch_snapshot(searcher, cfg, ext_id)
            counts[snap["status"]] = counts.get(snap["status"], 0) + 1
            if snap["status"] in ("parse_error", "fetch_error"):
                errors += 1
                log.warning("%s %s: %s", snap["status"], ext_id, snap.get("error"))
            related += [{"ext_id": r, "name": "", "source": f"related:{ext_id}"} for r in snap.get("related", [])]
            batch.append(to_snapshot_row(snap, date))
            masters.append(to_master(snap, cfg))
            processed += 1
            if len(batch) >= int(ch["write_batch_size"]):
                client.append_new("Snapshots", batch)
                batch = []
                log.info("snapshot %d/%d %s", i, len(ids), counts)
        client.append_new("Snapshots", batch)
        client.upsert("Master", masters, ts)
        added = add_ids(client, cfg, related, ts, int(ch["max_new_related_per_run"])) if ch.get("add_related_ids") else 0
        note = f"{counts}; {added} related ids added"
    except Exception as e:
        errors += 1
        note = f"FAILED: {e}"
        if batch:  # keep what we already fetched
            client.append_new("Snapshots", batch)
        raise
    finally:
        client.log_run(ts, MODULE, processed, errors, time.time() - started, note)
    return {"processed": processed, "errors": errors, **counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Module D: weekly Chrome snapshot")
    parser.add_argument("--config", default=None)
    parser.add_argument("--force", action="store_true", help="ignore the once-a-week guard")
    parser.add_argument("--limit", type=int, default=None, help="only the first N ids (testing)")
    args = parser.parse_args()
    load_env()
    setup_logging()
    from radar.sheets import SheetClient

    client = SheetClient.from_env()
    client.ensure_tabs()
    log.info("snapshot done: %s", run(load_config(args.config), client, force=args.force, limit=args.limit))


if __name__ == "__main__":
    main()
