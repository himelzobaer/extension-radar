"""Chrome Web Store search-page scraping (no official API).

Used by Module B to check whether a Firefox add-on exists on Chrome.
The parser is defensive: any parse failure returns an empty result instead of raising.
"""

from __future__ import annotations

import difflib
import html
import logging
import re
from typing import Any
from urllib.parse import quote

import requests

from radar.utils import HttpError, random_delay, request_with_retry

log = logging.getLogger(__name__)

_ITEM_RE = re.compile(r'data-item-id="([a-p]{32})"')
_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_search_results(page: str, window: int = 4000) -> list[dict[str, str]]:
    """[{id, name}] from a search results page, in page order."""
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        for m in _ITEM_RE.finditer(page or ""):
            ext_id = m.group(1)
            if ext_id in seen:
                continue
            seen.add(ext_id)
            h2 = _H2_RE.search(page, m.end(), m.end() + window)
            name = html.unescape(_TAG_RE.sub("", h2.group(1))).strip() if h2 else ""
            results.append({"id": ext_id, "name": name})
    except (TypeError, re.error) as e:
        log.warning("chrome search parse error: %s", e)
    return results


_SEPARATOR_RE = re.compile(r"\s*[:|·•]\s|\s[-–—]\s")


def core_name(name: str) -> str:
    """Brand part of a store name: 'Foo: the best X' / 'Foo - X' -> 'Foo'."""
    name = re.sub(r"[\(\[].*?[\)\]]", " ", name or "")
    return _SEPARATOR_RE.split(name)[0].strip()


def _tokens(name: str, strip_words: list[str]) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", (name or "").lower())
    strip = {w.lower() for w in strip_words}
    return [t for t in tokens if t not in strip] or tokens


def normalize_name(name: str, strip_words: list[str]) -> str:
    """Lowercase core name without punctuation and filler words."""
    return " ".join(_tokens(core_name(name), strip_words))


def search_query(name: str, strip_words: list[str]) -> str:
    """Short query for the store search (long names return no results)."""
    core = core_name(name)
    kept = [w for w in core.split() if w.lower() not in {"for", "firefox", "add-on", "addon"}]
    return " ".join(kept) or core or name


def _inside(a_core: list[str], a_full: list[str], b_core: list[str], b_full: list[str], generic: set[str]) -> bool:
    """a's core is inside b's full name, and b's core adds only words a also has or generic ones.

    'FoxyProxy' vs 'FoxyProxy Standard' passes; 'uBlock' vs 'uBlock Origin' fails.
    """
    return (
        sum(len(t) for t in a_core) >= 4
        and set(a_core) <= set(b_full)
        and set(b_core) - set(a_core) <= generic | set(a_full)
    )


def _same_product(a_core: list[str], a_full: list[str], b_core: list[str], b_full: list[str], generic: set[str]) -> bool:
    """Containment match in either direction."""
    return _inside(a_core, a_full, b_core, b_full, generic) or _inside(b_core, b_full, a_core, a_full, generic)


def best_match(
    name: str, results: list[dict[str, str]], cfg: dict[str, Any]
) -> tuple[dict[str, str] | None, float]:
    """Closest result and its score (1.0 for exact/containment match)."""
    cs = cfg["chrome_search"]
    strip = cs["strip_words"]
    variants = {w.lower() for w in cs.get("variant_words", [])}
    generic = {w.lower() for w in cs.get("generic_words", [])}
    target_core = _tokens(core_name(name), strip)
    target_full = _tokens(name, strip)
    best, best_ratio = None, 0.0
    for r in results[: int(cs["max_results_to_compare"])]:
        cand_core = _tokens(core_name(r["name"]), strip)
        cand_full = _tokens(r["name"], strip)
        ratio = difflib.SequenceMatcher(None, " ".join(target_core), " ".join(cand_core)).ratio()
        # "uBlock Origin Lite" is a different (MV3) product, never a match
        if (set(cand_full) & variants) - set(target_full):
            ratio = min(ratio, 0.5)
        elif cand_core == target_core or _same_product(target_core, target_full, cand_core, cand_full, generic):
            ratio = 1.0
        if ratio > best_ratio:
            best, best_ratio = r, ratio
    return best, best_ratio


class ChromeSearcher:
    """Search the Chrome Web Store by name with random delays between requests."""

    def __init__(self, cfg: dict[str, Any], session: requests.Session) -> None:
        self.cfg = cfg
        self.cs = cfg["chrome_search"]
        self.session = session
        self._first = True

    def fetch(self, url: str, passthrough_statuses: tuple[int, ...] = (404,)) -> requests.Response:
        """GET a store page, with a random delay before every request except the first."""
        if not self._first:
            random_delay(float(self.cs["delay_min_seconds"]), float(self.cs["delay_max_seconds"]))
        self._first = False
        return request_with_retry(self.session, url, self.cfg, passthrough_statuses=passthrough_statuses)

    def list_page(self, url: str) -> list[dict[str, str]]:
        """[{id, name}] from a search or category page. Raises HttpError if it can't be fetched."""
        resp = self.fetch(url)
        if resp.status_code != 200:
            raise HttpError(url, resp.status_code)
        return parse_search_results(resp.text)

    def search(self, query: str) -> list[dict[str, str]]:
        """Results for a query. Raises HttpError if the page can't be fetched."""
        return self.list_page(self.cs["search_url"].format(query=quote(query, safe="")))

    def parser_works(self) -> bool:
        """Canary: a very common query must return parsed results."""
        try:
            return bool(self.search(self.cs["canary_query"]))
        except HttpError as e:
            log.error("chrome canary request failed: %s", e)
            return False

    def check(self, name: str) -> dict[str, Any]:
        """{'chrome_check': found|not_found|unknown, 'chrome_match': 'name (id)', 'chrome_id', 'ratio'}."""
        try:
            results = self.search(search_query(name, self.cs["strip_words"]))
        except HttpError as e:
            log.warning("chrome search failed for %r: %s", name, e)
            return {"chrome_check": "unknown", "chrome_match": "", "chrome_id": "", "ratio": 0.0}
        match, ratio = best_match(name, results, self.cfg)
        if match and ratio >= float(self.cs["match_threshold"]):
            return {"chrome_check": "found", "chrome_match": f"{match['name']} ({match['id']})",
                    "chrome_id": match["id"], "ratio": round(ratio, 3)}
        return {"chrome_check": "not_found",
                "chrome_match": f"closest: {match['name']} ({ratio:.2f})" if match else "",
                "chrome_id": "", "ratio": round(ratio, 3)}
