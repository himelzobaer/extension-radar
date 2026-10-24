from radar import scoring


def test_license_checks(cfg):
    assert scoring.check_license("mit", "github", cfg) == ("yes", "")
    ok, note = scoring.check_license("gpl-3.0", "github", cfg)
    assert ok == "yes" and "open source" in note
    assert scoring.check_license("", "github", cfg)[0] == "unknown"
    assert scoring.check_license("proprietary", "firefox", cfg)[0] == "no"


def test_run_scores_and_keeps_manual(cfg, client):
    client.upsert("Master", [
        {"key": "ff-addon", "source": "firefox", "name": "FF", "users_or_stars": 50000,
         "license": "mpl-2.0", "flags": "FIREFOX_ONLY"},
        {"key": "a/b", "source": "github", "name": "ab", "users_or_stars": 900,
         "license": "gpl-3.0", "flags": "MV2,STALE"},
    ], "t1")
    scoring.run(cfg, client)
    ws = client.worksheet("Scored")
    header = ws.values[0]
    ws.values[1][header.index("manual_one_week_build")] = "yes"
    scoring.run(cfg, client)

    rows = {r["key"]: r for r in client.read_records("Scored")}
    assert rows["ff-addon"]["users_10k_plus"] == "yes"
    assert rows["ff-addon"]["auto_score"] == "2"
    assert rows["a/b"]["signal_flags"] == "STALE" and "open source" in rows["a/b"]["license_note"]
    assert list(rows.values())[0]["manual_one_week_build"] == "yes"
