from datetime import date
from unittest.mock import MagicMock

from conftest import fake_response
from radar import chrome_seeder, chrome_snapshot, diff_engine
from radar.chrome_search import ChromeSearcher

ID_A = "a" * 32
ID_B = "b" * 32
CANARY = "eimadpbcbfnmbkopoojfekhnkhdbieeh"


def detail_page(name, users, rating, dev, dev_id, version="1.0", updated="September 24, 2026", related=()):
    rel = "".join(f'<div data-item-id="{r}"></div>' for r in related)
    return f"""<html><head><title>{name} - Chrome Web Store</title></head><body>
    <a data-publisher-id="{dev_id}" href="./publisher/x/{dev_id}">{dev}</a>
    <div>Extension<a href="./category/extensions">Tools</a>{users} users</div>
    <span aria-label="Rated {rating} out of 5 stars"></span><p class="x">1.2K ratings</p>
    <div class="q">Version</div><div class="n">{version}</div>
    <div class="q">Updated</div><div>{updated}</div>{rel}</body></html>"""


def test_parse_detail_fields(cfg):
    page = detail_page("Foo &amp; Bar", "12,345", "4.5", "Acme Ltd", "u1", related=[ID_B, ID_A])
    r = chrome_snapshot.parse_detail(page, "https://x/detail/foo/" + ID_A, ID_A, cfg)
    assert r["status"] == "ok" and r["name"] == "Foo & Bar"
    assert (r["users"], r["rating"], r["rating_count"]) == (12345, 4.5, 1200)
    assert (r["version"], r["last_updated"], r["developer"], r["developer_id"]) == ("1.0", "2026-09-24", "Acme Ltd", "u1")
    assert r["related"] == [ID_B]


def test_parse_detail_removed_and_broken(cfg):
    assert chrome_snapshot.parse_detail("", "https://x/detail/empty-title/" + ID_A, ID_A, cfg)["status"] == "removed"
    assert chrome_snapshot.parse_detail("<html>new layout</html>", "https://x", ID_A, cfg)["status"] == "parse_error"


def test_seeder_collects_and_caps(cfg, client):
    cfg["chrome"]["categories"] = ["productivity/tools"]
    cfg["chrome"]["seed_keywords"] = ["dark"]
    cfg["chrome"]["max_ids"] = 2
    client.upsert("GitHub_Repos", [{"full_name": "a/b", "cws_link": f"https://chrome.google.com/webstore/detail/x/{ID_A}"}], "t")
    page = f'<div data-item-id="{ID_A}"><h2>A</h2></div><div data-item-id="{ID_B}"><h2>B</h2></div><div data-item-id="{"c" * 32}"><h2>C</h2></div>'
    session = MagicMock()
    session.get.return_value = fake_response(200, text=page)
    stats = chrome_seeder.run(cfg, client, ChromeSearcher(cfg, session))
    ids = client.read_records("Chrome_IDs")
    assert stats["processed"] == 2 and len(ids) == 2
    assert ids[0]["ext_id"] == ID_A and ids[0]["source"] == "github_readme"


def _session_for(pages):
    def get(url, params=None, timeout=None):
        for ext_id, (page, final) in pages.items():
            if ext_id in url:
                r = fake_response(200, text=page)
                r.url = final or url
                return r
        return fake_response(404)
    s = MagicMock()
    s.get.side_effect = get
    return s


def test_snapshot_run_writes_rows_and_related(cfg, client):
    client.upsert("Chrome_IDs", [{"ext_id": ID_A, "active": "TRUE"}, {"ext_id": ID_B, "active": "TRUE"}], "t")
    pages = {
        CANARY: (detail_page("Dark Reader", "7,000,000", "4.3", "DR", "u0"), None),
        ID_A: (detail_page("A", "50,000", "4.0", "Acme", "u1", related=["c" * 32]), None),
        ID_B: ("", "https://x/detail/empty-title/" + ID_B),
    }
    stats = chrome_snapshot.run(cfg, client, ChromeSearcher(cfg, _session_for(pages)))
    assert stats["ok"] == 1 and stats["removed"] == 1
    snaps = {r["ext_id"]: r for r in client.read_records("Snapshots")}
    assert snaps[ID_A]["users"] == "50000" and snaps[ID_B]["status"] == "removed"
    assert {r["ext_id"] for r in client.read_records("Chrome_IDs")} == {ID_A, ID_B, "c" * 32}
    master = {r["key"]: r for r in client.read_records("Master")}
    assert master[ID_A]["source"] == "chrome" and master[ID_A]["users_or_stars"] == "50000"
    # once-a-week guard
    assert chrome_snapshot.run(cfg, client, ChromeSearcher(cfg, _session_for(pages))).get("skipped") == 1


def test_snapshot_stops_when_canary_breaks(cfg, client):
    client.upsert("Chrome_IDs", [{"ext_id": ID_A, "active": "TRUE"}], "t")
    pages = {CANARY: ("<html>new layout</html>", None)}
    try:
        chrome_snapshot.run(cfg, client, ChromeSearcher(cfg, _session_for(pages)))
        raise AssertionError("should have failed")
    except RuntimeError as e:
        assert "canary" in str(e)
    assert client.read_records("Snapshots") == []


def _snap(day, ext_id, **kw):
    row = {"snapshot_key": f"{day}:{ext_id}", "date": day, "ext_id": ext_id, "status": "ok", "name": ext_id[:3],
           "users": "50000", "rating": "4.5", "developer": "Acme", "developer_id": "u1", "last_updated": "2026-09-01"}
    row.update(kw)
    return row


def test_diff_flags(cfg, client):
    ids = {k: k * 32 for k in "cdefg"}
    client.append("Snapshots", [
        _snap("2026-10-17", ids["c"]), _snap("2026-10-24", ids["c"], status="removed"),
        _snap("2026-10-17", ids["d"]), _snap("2026-10-24", ids["d"], rating="3.9"),
        _snap("2026-10-17", ids["e"]), _snap("2026-10-24", ids["e"], developer="Shady Corp", developer_id="u9"),
        _snap("2026-10-17", ids["f"]), _snap("2026-10-24", ids["f"], last_updated="2025-04-01"),
        _snap("2026-10-17", ids["g"]), _snap("2026-10-24", ids["g"]),
    ])
    stats = diff_engine.run(cfg, client, today=date(2026, 10, 24))
    assert stats["REMOVED"] == stats["RATING_DROP"] == stats["DEV_CHANGE"] == stats["STALE"] == 1
    alerts = {r["flag_type"]: r for r in client.read_records("Alerts")}
    assert alerts["REMOVED"]["users"] == "50000"
    assert alerts["DEV_CHANGE"]["detail"] == "Acme -> Shady Corp"
    master = {r["key"]: r for r in client.read_records("Master")}
    assert master[ids["g"]]["flags"] == "" and master[ids["f"]]["flags"] == "STALE"
    # running again does not duplicate alerts
    diff_engine.run(cfg, client, today=date(2026, 10, 24))
    assert len(client.read_records("Alerts")) == 4
