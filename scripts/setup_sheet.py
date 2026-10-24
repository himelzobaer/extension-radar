"""Create all tabs and header rows in the Google Sheet.

python scripts/setup_sheet.py
Safe to run again: existing tabs and data are kept.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.sheets import TABS, SheetClient  # noqa: E402
from radar.utils import load_env, setup_logging  # noqa: E402

log = logging.getLogger("setup_sheet")


def setup(client: SheetClient) -> list[str]:
    """Ensure tabs exist; drop the default empty 'Sheet1' if present."""
    changed = client.ensure_tabs()
    for ws in client.ss.worksheets():
        if ws.title in ("Sheet1", "শিট1") and ws.title not in TABS and not any(ws.get_all_values()):
            client.ss.del_worksheet(ws)
            log.info("removed empty default tab %s", ws.title)
    return changed


def main() -> None:
    load_env()
    setup_logging()
    changed = setup(SheetClient.from_env())
    log.info("tabs created/updated: %s", ", ".join(changed) or "none (already set up)")


if __name__ == "__main__":
    main()
