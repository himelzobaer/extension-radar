"""Module A: find abandoned / MV2 extension repos on GitHub.

Run alone: python -m radar.github_module
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import re
import time
from typing import Any

import requests

from radar.utils import (
    HttpError, load_config, load_env, make_session, now_iso, request_with_retry, setup_logging,
)

log = logging.getLogger(__name__)

MODULE = "github"
_MV_RE = re.compile(r'"manifest_version"\s*:\s*(\d+)')
_CWS_RE = re.compile(
    r"https?://(?:chrome\.google\.com/webstore/detail|chromewebstore\.google\.com/detail)"
    r"/[^\s)\]\"'<>]*?([a-p]{32})",
    re.IGNORECASE,
)


def github_session(cfg: dict[str, Any]) -> requests.Session:
    """Session with GitHub API headers and the token (if set)."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        log.warning("GITHUB_TOKEN not set: search rate limit drops to 10 req/min")
    return make_session(cfg, headers)


def search_repos(session: requests.Session, cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Run every configured query, return raw repo items keyed by full_name."""
    gh = cfg["github"]
    found: dict[str, dict[str, Any]] = {}
    for query in gh["queries"]:
        for page in range(1, int(gh["max_pages_per_query"]) + 1):
            resp = request_with_retry(
                session, f"{gh['api_base']}/search/repositories", cfg,
                params={"q": query, "sort": "stars", "order": "desc",
                        "per_page": gh["per_page"], "page": page},
                passthrough_statuses=(422,),
            )
            time.sleep(float(gh["search_delay_seconds"]))
            if resp.status_code == 422:  # past the 1000-result window
                break
            items = resp.json().get("items", [])
            for item in items:
                found.setdefault(item["full_name"], item)
            log.info("query %r page %d: %d items", query, page, len(items))
            if len(items) < int(gh["per_page"]):
                break
    return found


def parse_repo(item: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields we store from a search result item."""
    lic = item.get("license") or {}
    return {
        "full_name": item["full_name"],
        "name": item.get("name", ""),
        "url": item.get("html_url", ""),
        "stars": item.get("stargazers_count", 0),
        "forks": item.get("forks_count", 0),
        "last_push": item.get("pushed_at", ""),
        "license": (lic.get("spdx_id") or lic.get("key") or "").lower(),
        "archived": bool(item.get("archived")),
        "open_issues": item.get("open_issues_count", 0),
        "description": (item.get("description") or "")[:500],
        "default_branch": item.get("default_branch") or "HEAD",
    }


def parse_manifest_version(text: str) -> int | None:
    """manifest_version from manifest.json text (regex, tolerant of comments)."""
    m = _MV_RE.search(text or "")
    return int(m.group(1)) if m else None


def extract_cws_link(readme: str) -> str:
    """First Chrome Web Store detail link in a README, or ''."""
    m = _CWS_RE.search(readme or "")
    return m.group(0) if m else ""


def manifest_candidates(
    paths: list[str], ignore_dirs: list[str], deprioritize: list[str] | None = None
) -> list[str]:
    """manifest.json paths sorted shallow-first, skipping ignored directories.

    Paths under a deprioritized dir (e.g. firefox/) go last.
    """
    ignore = {d.lower() for d in ignore_dirs}
    late = {d.lower() for d in deprioritize or []}
    out = [
        p for p in paths
        if p.split("/")[-1].lower() == "manifest.json"
        and not any(seg.lower() in ignore for seg in p.split("/")[:-1])
    ]
    return sorted(
        out,
        key=lambda p: (any(seg.lower() in late for seg in p.split("/")[:-1]), p.count("/"), p),
    )


def _get_json(session: requests.Session, url: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    resp = request_with_retry(session, url, cfg)
    time.sleep(float(cfg["github"]["core_delay_seconds"]))
    return None if resp.status_code == 404 else resp.json()


def fetch_file(session: requests.Session, cfg: dict[str, Any], full_name: str, path: str) -> str | None:
    """Decoded text of a file via the contents API, or None."""
    data = _get_json(session, f"{cfg['github']['api_base']}/repos/{full_name}/contents/{path}", cfg)
    if not data or data.get("encoding") != "base64":
        return None
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


def enrich(session: requests.Session, cfg: dict[str, Any], repo: dict[str, Any]) -> dict[str, Any]:
    """Add manifest_version, manifest_path and cws_link. Errors go into repo['error']."""
    gh = cfg["github"]
    full = repo["full_name"]
    repo.update(manifest_version="", manifest_path="", cws_link="", error="")
    try:
        tree = _get_json(
            session, f"{gh['api_base']}/repos/{full}/git/trees/{repo['default_branch']}?recursive=1", cfg
        )
        paths = [t["path"] for t in (tree or {}).get("tree", []) if t.get("type") == "blob"]
        candidates = manifest_candidates(paths, gh["manifest_ignore_dirs"], gh.get("manifest_deprioritize_dirs"))
        for path in candidates[:3]:
            mv = parse_manifest_version(fetch_file(session, cfg, full, path) or "")
            if mv is not None:
                repo["manifest_version"], repo["manifest_path"] = mv, path
                break
        readme = _get_json(session, f"{gh['api_base']}/repos/{full}/readme", cfg)
        if readme and readme.get("encoding") == "base64":
            text = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
            repo["cws_link"] = extract_cws_link(text)
    except (HttpError, ValueError, KeyError) as e:
        log.warning("enrich failed for %s: %s", full, e)
        repo["error"] = str(e)[:300]
    return repo


def to_master(repo: dict[str, Any]) -> dict[str, Any]:
    """Master tab row for a repo."""
    flags = []
    if repo.get("manifest_version") == 2:
        flags.append("MV2")
    if repo.get("archived"):
        flags.append("ARCHIVED")
    return {
        "key": repo["full_name"], "source": "github", "name": repo["name"], "url": repo["url"],
        "users_or_stars": repo["stars"], "rating": "", "last_updated": repo["last_push"],
        "license": repo["license"], "manifest_version": repo.get("manifest_version", ""),
        "flags": ",".join(flags),
    }


def to_alert(repo: dict[str, Any], cfg: dict[str, Any], date: str) -> dict[str, Any] | None:
    """MV2 repo with a fork-friendly license becomes an alert."""
    sc = cfg["scoring"]
    allowed = set(sc["permissive_licenses"]) | set(sc["copyleft_licenses"])
    if repo.get("manifest_version") != 2 or repo.get("license") not in allowed:
        return None
    flag = cfg["alerts"]["github_mv2_flag"]
    return {
        "alert_key": f"{flag}:{repo['full_name']}", "flag_type": flag, "date": date,
        "source": "github", "item_key": repo["full_name"], "name": repo["full_name"],
        "users": repo["stars"], "link": repo["url"], "sent": "",
    }


def run(cfg: dict[str, Any], client: Any, session: requests.Session | None = None) -> dict[str, int]:
    """Search, enrich, write GitHub_Repos / Master / Alerts, log the run."""
    started, ts = time.time(), now_iso()
    session = session or github_session(cfg)
    errors = 0
    repos: list[dict[str, Any]] = []
    note = ""
    try:
        raw = search_repos(session, cfg)
        log.info("found %d unique repos", len(raw))
        for i, item in enumerate(list(raw.values())[: int(cfg["github"]["max_repos"])], 1):
            repo = enrich(session, cfg, parse_repo(item))
            errors += bool(repo["error"])
            repos.append(repo)
            if i % 25 == 0:
                log.info("enriched %d/%d", i, len(raw))
        rows = [{k: v for k, v in r.items() if k != "default_branch"} for r in repos]
        client.upsert("GitHub_Repos", rows, ts)
        client.upsert("Master", [to_master(r) for r in repos], ts)
        alerts = [a for a in (to_alert(r, cfg, ts[:10]) for r in repos) if a]
        new_alerts = client.upsert("Alerts", alerts, ts, only_new=True)
        note = f"{len(new_alerts)} new alerts"
    except Exception as e:  # log the failure to Logs, then re-raise
        errors += 1
        note = f"FAILED: {e}"[:500]
        raise
    finally:
        client.log_run(ts, MODULE, len(repos), errors, time.time() - started, note)
    return {"processed": len(repos), "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Module A: GitHub extension repos")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    load_env()
    setup_logging()
    from radar.sheets import SheetClient

    stats = run(load_config(args.config), SheetClient.from_env())
    log.info("github done: %s", stats)


if __name__ == "__main__":
    main()
