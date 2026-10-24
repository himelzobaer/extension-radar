"""Run the whole Phase 1 pipeline: GitHub -> Firefox -> Scoring.

python -m radar.main [--only github,firefox,scoring] [--force]
A failing module is logged and the next one still runs.
"""

from __future__ import annotations

import argparse
import logging
import sys

from radar import firefox_module, github_module, scoring
from radar.sheets import SheetClient
from radar.utils import load_config, load_env, setup_logging

log = logging.getLogger("radar.main")

STEPS = ("github", "firefox", "scoring")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extension Opportunity Radar pipeline")
    parser.add_argument("--config", default=None)
    parser.add_argument("--only", default=",".join(STEPS), help="comma-separated steps")
    parser.add_argument("--force", action="store_true", help="ignore the firefox once-a-week guard")
    args = parser.parse_args()
    load_env()
    setup_logging()
    cfg = load_config(args.config)
    client = SheetClient.from_env()
    client.ensure_tabs()

    failed = []
    for step in [s.strip() for s in args.only.split(",") if s.strip()]:
        try:
            if step == "github":
                stats = github_module.run(cfg, client)
            elif step == "firefox":
                stats = firefox_module.run(cfg, client, force=args.force)
            elif step == "scoring":
                stats = scoring.run(cfg, client)
            else:
                log.error("unknown step %r", step)
                failed.append(step)
                continue
            log.info("%s: %s", step, stats)
        except Exception:
            log.exception("%s failed", step)
            failed.append(step)
    if failed:
        log.error("failed steps: %s", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
