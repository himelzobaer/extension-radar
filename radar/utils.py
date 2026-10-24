"""Shared helpers: config, logging, HTTP retry with backoff, random delay."""

from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "queries.yaml"

_SECRET_ENV_NAMES = ("GITHUB_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON", "TELEGRAM_BOT_TOKEN")

log = logging.getLogger("radar")


def load_env() -> None:
    """Load a local .env file if python-dotenv is available and the file exists."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read the YAML config file."""
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


class SecretMaskFilter(logging.Filter):
    """Replace any secret env value that leaks into a log line with ***."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        masked = msg
        for name in _SECRET_ENV_NAMES:
            value = os.environ.get(name)
            if value and len(value) > 6:
                masked = masked.replace(value, "***")
        masked = re.sub(r"(gh[pousr]_[A-Za-z0-9]{20,})", "***", masked)
        if masked != msg:
            record.msg, record.args = masked, ()
        return True


def setup_logging(level: str | None = None) -> None:
    """Configure root logging once, with secret masking."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(SecretMaskFilter())
    root.addHandler(handler)
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))


def now_iso() -> str:
    """Current UTC time as ISO string (seconds precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    """Current UTC date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def random_delay(min_s: float, max_s: float) -> None:
    """Sleep a random amount between min_s and max_s seconds."""
    time.sleep(random.uniform(min_s, max_s))


def make_session(cfg: dict[str, Any], headers: dict[str, str] | None = None) -> requests.Session:
    """requests.Session with the configured User-Agent and extra headers."""
    s = requests.Session()
    s.headers["User-Agent"] = cfg["http"]["user_agent"]
    if headers:
        s.headers.update(headers)
    return s


class HttpError(Exception):
    """Raised when a request fails after all retries."""

    def __init__(self, url: str, status: int | None, msg: str = "") -> None:
        super().__init__(f"{status} {url} {msg}".strip())
        self.url = url
        self.status = status


def request_with_retry(
    session: requests.Session,
    url: str,
    cfg: dict[str, Any],
    params: dict[str, Any] | None = None,
    ok_statuses: tuple[int, ...] = (200,),
    passthrough_statuses: tuple[int, ...] = (404,),
) -> requests.Response:
    """GET with exponential backoff.

    Retries on network errors, 429, 5xx and GitHub's 403 rate-limit response.
    Statuses in passthrough_statuses are returned to the caller without retry.
    """
    http = cfg["http"]
    retries = int(http["max_retries"])
    base = float(http["backoff_base_seconds"])
    last_status: int | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=http["timeout_seconds"])
        except requests.RequestException as e:
            log.warning("request error %s (attempt %d/%d): %s", url, attempt + 1, retries + 1, e)
            last_status = None
        else:
            if resp.status_code in ok_statuses or resp.status_code in passthrough_statuses:
                return resp
            last_status = resp.status_code
            retryable = resp.status_code == 429 or resp.status_code >= 500 or _is_rate_limited(resp)
            if not retryable:
                raise HttpError(url, resp.status_code)
            wait = _rate_limit_wait(resp)
            if wait is not None and attempt < retries:
                log.warning("rate limited on %s, sleeping %.0fs", url, wait)
                time.sleep(wait)
                continue
        if attempt < retries:
            time.sleep(base * (2**attempt) + random.uniform(0, 1))
    raise HttpError(url, last_status, "retries exhausted")


def _is_rate_limited(resp: requests.Response) -> bool:
    return resp.status_code == 403 and (
        resp.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in resp.text.lower()
    )


def _rate_limit_wait(resp: requests.Response, cap: float = 120.0) -> float | None:
    """Seconds to wait from Retry-After / X-RateLimit-Reset headers, capped."""
    retry_after = resp.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), cap)
    reset = resp.headers.get("X-RateLimit-Reset")
    if reset and reset.isdigit() and resp.headers.get("X-RateLimit-Remaining") == "0":
        return min(max(float(reset) - time.time() + 1, 1.0), cap)
    return None
