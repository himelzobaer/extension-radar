"""Module E: compare the latest Chrome snapshot with the previous one and raise flags.

Flags: REMOVED, RATING_DROP, DEV_CHANGE, STALE.
Run alone: python -m radar.diff_engine
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime
from typing import Any

from radar.utils import load_config, load_env, now_iso, setup_logging

log = logging.getLogger(__name__)

MODULE = "diff_engine"


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _months_since(iso_day: str, today: date) -> float | None:
    try:
        d = datetime.strptime(iso_day[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return (today - d).days / 30.44


def latest_pairs(rows: list[dict[str, str]]) -> tuple[str, list[tuple[dict[str, str], dict[str, str] | None]]]:
    """(latest snapshot date, [(current_row, previous_usable_row_or_None)]) per extension.

    The previous row is the newest earlier snapshot with status ok/removed,
    so a week with a fetch or parse error doesn't hide a change.
    """
    by_id: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        if r.get("ext_id") and r.get("date"):
            by_id.setdefault(r["ext_id"], []).append(r)
    if not by_id:
        return "", []
    current_date = max(r["date"] for rs in by_id.values() for r in rs)
    pairs = []
    for rs in by_id.values():
        rs.sort(key=lambda r: r["date"])
        cur = rs[-1]
        if cur["date"] != current_date:
            continue
        prev = next((r for r in reversed(rs[:-1]) if r.get("status") in ("ok", "removed")), None)
        pairs.append((cur, prev))
    return current_date, pairs


def flags_for(cur: dict[str, str], prev: dict[str, str] | None, cfg: dict[str, Any], today: date) -> dict[str, str]:
    """{flag: detail} for one extension."""
    d = cfg["diff"]
    out: dict[str, str] = {}
    if prev and prev.get("status") == "ok" and cur.get("status") == "removed":
        out["REMOVED"] = f"last seen {prev.get('date')}"
    if not (cur.get("status") == "ok"):
        return out

    if prev and prev.get("status") == "ok":
        old_r, new_r = _float(prev.get("rating")), _float(cur.get("rating"))
        if old_r is not None and new_r is not None and old_r - new_r >= float(d["rating_drop_threshold"]):
            out["RATING_DROP"] = f"{old_r} -> {new_r}"
        old_id, new_id = prev.get("developer_id", ""), cur.get("developer_id", "")
        old_dev, new_dev = (prev.get("developer") or "").strip(), (cur.get("developer") or "").strip()
        if (old_id and new_id and old_id != new_id) or (old_dev and new_dev and old_dev.lower() != new_dev.lower()):
            out["DEV_CHANGE"] = f"{old_dev} -> {new_dev}"

    months = _months_since(cur.get("last_updated", ""), today)
    users = _float(cur.get("users")) or 0
    if months is not None and float(d["stale_min_months"]) <= months <= float(d["stale_max_months"]) \
            and users > float(d["stale_min_users"]):
        out["STALE"] = f"updated {cur.get('last_updated')} ({months:.0f} months)"
    return out


def run(cfg: dict[str, Any], client: Any, today: date | None = None) -> dict[str, int]:
    """Flag changes, write flags to Master and new items to Alerts."""
    started, ts = time.time(), now_iso()
    today = today or date.today()
    errors = 0
    note = ""
    flagged: dict[str, int] = {}
    pairs: list = []
    try:
        current_date, pairs = latest_pairs(client.read_records("Snapshots"))
        if not pairs:
            note = "no snapshots yet"
            return {"processed": 0, "errors": 0}
        detail_url = cfg["chrome"]["detail_url"]
        masters, alerts = [], []
        for cur, prev in pairs:
            flags = flags_for(cur, prev, cfg, today)
            masters.append({"key": cur["ext_id"], "source": "chrome", "flags": ",".join(sorted(flags))})
            for flag, detail in flags.items():
                flagged[flag] = flagged.get(flag, 0) + 1
                # STALE is a state (alert once); the others are events (alert per occurrence)
                key = f"{flag}:{cur['ext_id']}" if flag == "STALE" else f"{flag}:{cur['ext_id']}:{current_date}"
                alerts.append({
                    "alert_key": key, "flag_type": flag, "date": current_date, "source": "chrome",
                    "item_key": cur["ext_id"], "name": cur.get("name") or (prev or {}).get("name", ""),
                    "users": cur.get("users") or (prev or {}).get("users", ""),
                    "link": detail_url.format(id=cur["ext_id"]), "sent": "", "detail": detail,
                })
        client.upsert("Master", masters, ts)
        new_alerts = client.upsert("Alerts", alerts, ts, only_new=True)
        note = f"{current_date}: {flagged}; {len(new_alerts)} new alerts"
        log.info(note)
    except Exception as e:
        errors += 1
        note = f"FAILED: {e}"
        raise
    finally:
        client.log_run(ts, MODULE, len(pairs), errors, time.time() - started, note)
    return {"processed": len(pairs), "errors": errors, **flagged}


def main() -> None:
    parser = argparse.ArgumentParser(description="Module E: snapshot diff engine")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    load_env()
    setup_logging()
    from radar.sheets import SheetClient

    client = SheetClient.from_env()
    client.ensure_tabs()
    log.info("diff done: %s", run(load_config(args.config), client))


if __name__ == "__main__":
    main()
