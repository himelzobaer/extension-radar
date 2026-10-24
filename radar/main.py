"""Run the pipeline: GitHub -> Firefox -> Chrome snapshot -> diff -> scoring.

python -m radar.main [--only github,firefox,snapshot,diff,scoring] [--force]
The seeder is not in the default list: run it once with --only seed.
A failing module is logged and the next one still runs.
"""

from __future__ import annotations

import argparse
import logging
import sys

from radar import chrome_seeder, chrome_snapshot, diff_engine, firefox_module, github_module, scoring
from radar.sheets import SheetClient
from radar.utils import load_config, load_env, setup_logging

log = logging.getLogger("radar.main")

STEPS = ("github", "firefox", "snapshot", "diff", "scoring")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extension Opportunity Radar pipeline")
    parser.add_argument("--config", default=None)
    parser.add_argument("--only", default=",".join(STEPS), help="comma-separated steps (also: seed)")
    parser.add_argument("--force", action="store_true", help="ignore the once-a-week scrape guards")
    args = parser.parse_args()
    load_env()
    setup_logging()
    cfg = load_config(args.config)
    client = SheetClient.from_env()
    client.ensure_tabs()

    runners = {
        "github": lambda: github_module.run(cfg, client),
        "firefox": lambda: firefox_module.run(cfg, client, force=args.force),
        "seed": lambda: chrome_seeder.run(cfg, client),
        "snapshot": lambda: chrome_snapshot.run(cfg, client, force=args.force),
        "diff": lambda: diff_engine.run(cfg, client),
        "scoring": lambda: scoring.run(cfg, client),
    }
    failed = []
    for step in [s.strip() for s in args.only.split(",") if s.strip()]:
        if step not in runners:
            log.error("unknown step %r", step)
            failed.append(step)
            continue
        try:
            log.info("%s: %s", step, runners[step]())
        except Exception:
            log.exception("%s failed", step)
            failed.append(step)
    if failed:
        log.error("failed steps: %s", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
