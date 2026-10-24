import base64
from unittest.mock import MagicMock

from conftest import fake_response
from radar import github_module as gm


def _b64(text: str) -> dict:
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def test_parse_manifest_version():
    assert gm.parse_manifest_version('{\n // comment\n "manifest_version": 2 }') == 2
    assert gm.parse_manifest_version('{"name": "x"}') is None


def test_extract_cws_link():
    readme = "Install from [store](https://chrome.google.com/webstore/detail/my-ext/abcdefghijklmnopabcdefghijklmnop) now"
    assert gm.extract_cws_link(readme).endswith("abcdefghijklmnopabcdefghijklmnop")
    new = "https://chromewebstore.google.com/detail/foo/abcdefghijklmnopabcdefghijklmnop?hl=en"
    assert "chromewebstore.google.com" in gm.extract_cws_link(new)
    assert gm.extract_cws_link("no link here") == ""


def test_manifest_candidates_prefers_shallow_and_skips_node_modules():
    paths = ["node_modules/x/manifest.json", "src/ext/manifest.json", "src/manifest.json", "README.md"]
    assert gm.manifest_candidates(paths, ["node_modules"]) == ["src/manifest.json", "src/ext/manifest.json"]
    assert gm.manifest_candidates(["firefox/manifest.json", "src/manifest.json"], [], ["firefox"])[0] == "src/manifest.json"


def test_run_end_to_end_mocked(cfg, client):
    cfg["github"]["queries"] = ["topic:chrome-extension"]
    item = {
        "full_name": "alice/old-ext", "name": "old-ext", "html_url": "https://github.com/alice/old-ext",
        "stargazers_count": 900, "forks_count": 50, "pushed_at": "2023-01-01T00:00:00Z",
        "license": {"spdx_id": "MIT"}, "archived": False, "open_issues_count": 12,
        "description": "An old extension", "default_branch": "main",
    }

    def get(url, params=None, timeout=None):
        if "/search/repositories" in url:
            return fake_response(200, {"items": [item]})
        if "/git/trees/" in url:
            return fake_response(200, {"tree": [{"path": "src/manifest.json", "type": "blob"}]})
        if url.endswith("/contents/src/manifest.json"):
            return fake_response(200, _b64('{"manifest_version": 2}'))
        if url.endswith("/readme"):
            return fake_response(200, _b64("https://chrome.google.com/webstore/detail/x/abcdefghijklmnopabcdefghijklmnop"))
        return fake_response(404)

    session = MagicMock()
    session.get.side_effect = get
    stats = gm.run(cfg, client, session=session)

    assert stats == {"processed": 1, "errors": 0}
    repo = client.read_records("GitHub_Repos")[0]
    assert repo["manifest_version"] == "2" and repo["license"] == "mit" and repo["cws_link"]
    master = client.read_records("Master")[0]
    assert master["source"] == "github" and "MV2" in master["flags"]
    alerts = client.read_records("Alerts")
    assert alerts[0]["flag_type"] == "MV2_FORK"
    assert client.read_records("Logs")[0]["module"] == "github"


def test_enrich_survives_errors(cfg):
    session = MagicMock()
    session.get.return_value = fake_response(401)
    repo = gm.enrich(session, cfg, gm.parse_repo({"full_name": "a/b", "default_branch": "main"}))
    assert repo["error"] and repo["manifest_version"] == ""
