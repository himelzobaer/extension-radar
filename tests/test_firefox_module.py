from unittest.mock import MagicMock

from conftest import fake_response
from radar import firefox_module as fm
from radar.chrome_search import ChromeSearcher, best_match, normalize_name, parse_search_results, search_query

CWS_PAGE = """
<div data-item-id="eimadpbcbfnmbkopoojfekhnkhdbieeh"><a href="./detail/dark-reader/eimadpbcbfnmbkopoojfekhnkhdbieeh"></a>
<div><h2 class="CiI2if">Dark Reader</h2></div></div>
<div data-item-id="kpfoifbkkdiemfgihpofpdmmmlanapeb"><h2 class="CiI2if">Best Dark Reader &amp; Night Mode</h2></div>
"""


def _addon(slug, name, users):
    return {
        "slug": slug, "name": {"en-US": name}, "url": f"https://addons.mozilla.org/addon/{slug}/",
        "average_daily_users": users, "ratings": {"average": 4.5, "count": 100},
        "last_updated": "2025-01-01T00:00:00Z", "categories": ["privacy-security"],
        "summary": {"en-US": "desc"}, "current_version": {"license": {"slug": "MPL-2.0"}},
    }


def test_parse_search_results():
    res = parse_search_results(CWS_PAGE)
    assert res[0] == {"id": "eimadpbcbfnmbkopoojfekhnkhdbieeh", "name": "Dark Reader"}
    assert res[1]["name"] == "Best Dark Reader & Night Mode"
    assert parse_search_results("<html>changed layout</html>") == []


def test_normalize_and_match(cfg):
    strip = cfg["chrome_search"]["strip_words"]
    assert normalize_name("Dark Reader for Firefox", strip) == "dark reader"
    assert normalize_name("uBlock Origin – fast blocker", strip) == "ublock origin"
    match, ratio = best_match("Dark Reader", parse_search_results(CWS_PAGE), cfg)
    assert match["id"] == "eimadpbcbfnmbkopoojfekhnkhdbieeh" and ratio == 1.0


def _m(ff_name, cws_name, cfg):
    return best_match(ff_name, [{"id": "a" * 32, "name": cws_name}], cfg)[1] >= cfg["chrome_search"]["match_threshold"]


def test_real_world_pairs(cfg):
    # same product on both stores (were false FIREFOX_ONLY before)
    assert _m("SponsorBlock - Skip Sponsorships on YouTube", "SponsorBlock for YouTube - Skip Sponsorships", cfg)
    assert _m("Ghostery – Privacy Ad Blocker", "Ghostery AdBlocker for Privacy", cfg)
    assert _m("NordVPN - a VPN proxy extension for Firefox", "VPN for Chrome: NordVPN proxy protection", cfg)
    assert _m("Keepa.com - Amazon Price Tracker", "Keepa - Amazon Price Tracker", cfg)
    assert _m("FoxyProxy Standard", "FoxyProxy", cfg)
    assert _m("DeepL: AI translator and writing assistant", "DeepL: translate and write with AI", cfg)
    # different products
    assert _m("AdBlock for Firefox", "AdBlock — block ads across the web", cfg)
    assert not _m("uBlock Origin", "uBlock Origin Lite", cfg)
    assert not _m("uBlock Origin", "uBlock", cfg)
    assert not _m("uBlock Origin", "uBlock Origin Scope", cfg)
    assert not _m("1Password: Password Manager", "Dacmos Password Manager", cfg)
    assert not _m("Facebook Container", "WebCopilot.ai - Use GPT on any input box", cfg)
    assert not _m("VPN", "Free VPN for Chrome - VPN Proxy VeePN", cfg)


def test_search_query_is_short(cfg):
    strip = cfg["chrome_search"]["strip_words"]
    assert search_query("NordVPN - a VPN proxy extension for Firefox", strip) == "NordVPN"
    assert search_query("PayPal Honey: Automated Coupons & Rewards", strip) == "PayPal Honey"
    assert search_query("Google Translator for Firefox", strip) == "Google Translator"


def test_run_end_to_end_mocked(cfg, client):
    cfg["firefox"]["max_addons"] = 2
    amo = MagicMock()
    amo.get.return_value = fake_response(200, {
        "results": [_addon("darkreader", "Dark Reader", 900000), _addon("multi-account-containers", "Firefox Multi-Account Containers", 500000)],
        "next": None,
    })

    def cws_get(url, params=None, timeout=None):
        return fake_response(200, text=CWS_PAGE if "dark" in url.lower() else "<html>no results</html>")

    cws = MagicMock()
    cws.get.side_effect = cws_get
    stats = fm.run(cfg, client, amo_session=amo, searcher=ChromeSearcher(cfg, cws))

    assert stats["processed"] == 2 and stats["firefox_only"] == 1
    only = client.read_records("Firefox_Only")
    assert [r["slug"] for r in only] == ["multi-account-containers"]
    assert only[0]["license"] == "mpl-2.0"
    alerts = client.read_records("Alerts")
    assert alerts[0]["flag_type"] == "FIREFOX_ONLY"
    master = {r["key"]: r for r in client.read_records("Master")}
    assert master["darkreader"]["flags"] == "" and master["multi-account-containers"]["flags"] == "FIREFOX_ONLY"

    # second run inside a week is skipped (scrape guard)
    assert fm.run(cfg, client, amo_session=amo, searcher=ChromeSearcher(cfg, cws)).get("skipped") == 1


def test_broken_parser_marks_unknown(cfg, client):
    cfg["firefox"]["max_addons"] = 1
    amo = MagicMock()
    amo.get.return_value = fake_response(200, {"results": [_addon("x", "X", 50000)], "next": None})
    cws = MagicMock()
    cws.get.return_value = fake_response(200, text="<html>new layout</html>")
    fm.run(cfg, client, amo_session=amo, searcher=ChromeSearcher(cfg, cws))
    assert client.read_records("Firefox_Only") == []
    assert client.read_records("Master")[0]["flags"] == "CHROME_UNKNOWN"
