"""Auto-score every Master item into the Scored tab. Manual columns stay untouched.

Run alone: python -m radar.scoring
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from radar.utils import load_config, load_env, now_iso, setup_logging

log = logging.getLogger(__name__)

MODULE = "scoring"
GPL_NOTE = "Copyleft (GPL): নিজের version ও open source রাখতে হবে"


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def check_license(license_id: str, source: str, cfg: dict[str, Any]) -> tuple[str, str]:
    """(license_ok, license_note) for an SPDX-ish license id."""
    sc = cfg["scoring"]
    lic = (license_id or "").strip().lower()
    if not lic:
        return ("n/a", "closed source store item") if source == "chrome" else ("unknown", "license নাই, manual চেক")
    if lic in sc["permissive_licenses"]:
        return "yes", ""
    if lic in sc["copyleft_licenses"] or "gpl" in lic:
        return "yes", GPL_NOTE
    return "no", f"license '{lic}' fork এর জন্য অনিশ্চিত"


def score_row(row: dict[str, str], cfg: dict[str, Any], ts: str) -> dict[str, Any]:
    """Scored tab row (auto columns only) for one Master row."""
    sc = cfg["scoring"]
    source = row.get("source", "")
    if source == "github":
        users_ok = "n/a (stars)"
    else:
        users = _int(row.get("users_or_stars"))
        users_ok = "unknown" if users is None else ("yes" if users >= int(sc["min_users"]) else "no")
    flags = [f.strip() for f in (row.get("flags") or "").split(",") if f.strip()]
    signals = [f for f in flags if f in sc["signal_flags"]]
    license_ok, note = check_license(row.get("license", ""), source, cfg)
    score = int(users_ok == "yes") + int(bool(signals)) + int(license_ok in ("yes", "n/a"))
    return {
        "key": row["key"], "source": source, "name": row.get("name", ""), "link": row.get("url", ""),
        "users_10k_plus": users_ok, "signal_flags": ",".join(signals), "license": row.get("license", ""),
        "license_ok": license_ok, "license_note": note, "auto_score": score, "last_scored": ts,
    }


def run(cfg: dict[str, Any], client: Any) -> dict[str, int]:
    """Score every Master row and upsert into Scored."""
    started, ts = time.time(), now_iso()
    rows: list[dict[str, Any]] = []
    errors = 0
    for r in client.read_records("Master"):
        if not r.get("key"):
            continue
        try:
            rows.append(score_row(r, cfg, ts))
        except (KeyError, TypeError, ValueError) as e:
            errors += 1
            log.warning("scoring failed for %s: %s", r.get("key"), e)
    client.upsert("Scored", rows, ts)
    client.log_run(ts, MODULE, len(rows), errors, time.time() - started)
    return {"processed": len(rows), "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Master items")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    load_env()
    setup_logging()
    from radar.sheets import SheetClient

    log.info("scoring done: %s", run(load_config(args.config), SheetClient.from_env()))


if __name__ == "__main__":
    main()
