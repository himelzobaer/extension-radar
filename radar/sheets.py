"""Google Sheets read/write helper (gspread + service account)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Iterable

log = logging.getLogger(__name__)

# Tab name -> header row. Unique key is always the first column.
TABS: dict[str, list[str]] = {
    "Master": [
        "key", "source", "name", "url", "users_or_stars", "rating", "last_updated",
        "license", "manifest_version", "flags", "first_seen", "last_seen",
    ],
    "Snapshots": [
        "snapshot_key", "date", "ext_id", "name", "users", "rating", "rating_count",
        "last_updated", "version", "developer", "status",
    ],
    "GitHub_Repos": [
        "full_name", "name", "url", "stars", "forks", "last_push", "license", "archived",
        "open_issues", "description", "manifest_version", "manifest_path", "cws_link",
        "error", "first_seen", "last_seen",
    ],
    "Firefox_Only": [
        "slug", "name", "url", "users", "rating", "rating_count", "last_updated",
        "category", "license", "description", "chrome_check", "chrome_match",
        "first_seen", "last_seen",
    ],
    "Alerts": [
        "alert_key", "flag_type", "date", "source", "item_key", "name", "users", "link",
        "sent", "sent_at",
    ],
    "Scored": [
        "key", "source", "name", "link", "users_10k_plus", "signal_flags", "license",
        "license_ok", "license_note", "auto_score", "last_scored",
        "manual_one_week_build", "manual_no_heavy_backend", "manual_not_builtin_chrome",
        "manual_keyword_search", "manual_notes",
    ],
    "Logs": ["run_at", "module", "processed", "errors", "duration_sec", "note"],
}


def _cell(value: Any) -> Any:
    """Convert a Python value to something the Sheets API accepts."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


class SheetClient:
    """Thin wrapper around a gspread Spreadsheet."""

    def __init__(self, spreadsheet: Any) -> None:
        self.ss = spreadsheet

    @classmethod
    def from_env(cls) -> "SheetClient":
        """Open the sheet using SHEET_ID and service account credentials from env.

        Credentials: GOOGLE_SERVICE_ACCOUNT_JSON (raw JSON) or
        GOOGLE_SERVICE_ACCOUNT_FILE (path to the JSON key file).
        """
        import gspread

        sheet_id = os.environ.get("SHEET_ID")
        if not sheet_id:
            raise RuntimeError("SHEET_ID env var is not set")
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        if raw:
            gc = gspread.service_account_from_dict(json.loads(raw))
        elif path:
            gc = gspread.service_account(filename=path)
        else:
            raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE")
        return cls(gc.open_by_key(sheet_id))

    def worksheet(self, name: str) -> Any:
        return self.ss.worksheet(name)

    def ensure_tabs(self) -> list[str]:
        """Create missing tabs and fill headers. Returns names of tabs that changed."""
        existing = {ws.title: ws for ws in self.ss.worksheets()}
        changed: list[str] = []
        for name, header in TABS.items():
            ws = existing.get(name)
            if ws is None:
                ws = self.ss.add_worksheet(title=name, rows=1000, cols=max(len(header), 10))
                changed.append(name)
            current = ws.row_values(1)
            if current[: len(header)] != header:
                merged = header + [c for c in current if c and c not in header]
                ws.update(values=[merged], range_name="A1", value_input_option="RAW")
                ws.freeze(rows=1)
                if name not in changed:
                    changed.append(name)
        return changed

    def read_records(self, name: str) -> list[dict[str, str]]:
        """All rows of a tab as dicts keyed by header."""
        values = self.worksheet(name).get_all_values()
        if not values:
            return []
        header = values[0]
        return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in values[1:]]

    def upsert(
        self, name: str, rows: Iterable[dict[str, Any]], timestamp: str, only_new: bool = False
    ) -> set[str]:
        """Insert or update rows by the tab's key column (first header).

        Existing rows keep columns that the new dict doesn't mention (so manual
        columns survive). first_seen is set only on insert, last_seen always.
        With only_new=True existing rows are left untouched.
        Returns the set of keys that were newly inserted.
        """
        ws = self.worksheet(name)
        values = ws.get_all_values()
        header = values[0] if values else list(TABS[name])
        table = [list(r) + [""] * (len(header) - len(r)) for r in values[1:]]
        key_col = header[0]
        index = {r[0]: i for i, r in enumerate(table) if r and r[0]}
        new_keys: set[str] = set()

        for row in rows:
            key = str(row.get(key_col, "")).strip()
            if not key or (only_new and key in index):
                continue
            for col in row:
                if col not in header:
                    header.append(col)
                    for r in table:
                        r.append("")
            data = dict(row)
            if "last_seen" in header:
                data["last_seen"] = timestamp
            if key in index:
                target = table[index[key]]
            else:
                target = [""] * len(header)
                if "first_seen" in header:
                    data["first_seen"] = timestamp
                table.append(target)
                index[key] = len(table) - 1
                new_keys.add(key)
            for col, val in data.items():
                target[header.index(col)] = _cell(val)

        full = [header] + table
        if len(full) > ws.row_count or len(header) > ws.col_count:
            ws.resize(rows=max(len(full), ws.row_count), cols=max(len(header), ws.col_count))
        ws.update(values=full, range_name="A1", value_input_option="RAW")
        log.info("%s: %d rows written (%d new)", name, len(table), len(new_keys))
        return new_keys

    def append(self, name: str, rows: list[dict[str, Any]]) -> None:
        """Append rows in header order."""
        if not rows:
            return
        ws = self.worksheet(name)
        header = ws.row_values(1) or TABS[name]
        ws.append_rows(
            [[_cell(r.get(c)) for c in header] for r in rows], value_input_option="RAW"
        )

    def log_run(
        self, run_at: str, module: str, processed: int, errors: int, duration: float, note: str = ""
    ) -> None:
        """Write one line to the Logs tab."""
        self.append("Logs", [{
            "run_at": run_at, "module": module, "processed": processed,
            "errors": errors, "duration_sec": round(duration, 1), "note": note[:500],
        }])

    def last_run(self, module: str) -> datetime | None:
        """Most recent run_at for a module in Logs (any outcome)."""
        latest: datetime | None = None
        for r in self.read_records("Logs"):
            if r.get("module") != module or not r.get("run_at"):
                continue
            try:
                ts = datetime.fromisoformat(r["run_at"])
            except ValueError:
                continue
            if latest is None or ts > latest:
                latest = ts
        return latest
