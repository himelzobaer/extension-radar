from radar.sheets import TABS
from scripts.setup_sheet import setup


def test_setup_creates_all_tabs_and_drops_sheet1(client):
    setup(client)
    assert set(client.ss.sheets) == set(TABS)
    for name, header in TABS.items():
        assert client.worksheet(name).row_values(1) == header


def test_upsert_dedupes_and_keeps_manual_columns(client):
    new = client.upsert("Scored", [{"key": "a/b", "name": "x", "auto_score": 1}], "t1")
    assert new == {"a/b"}
    rows = client.read_records("Scored")
    rows_ws = client.worksheet("Scored").values
    # user fills a manual column by hand
    rows_ws[1][TABS["Scored"].index("manual_notes")] = "good one"

    new = client.upsert("Scored", [{"key": "a/b", "name": "y", "auto_score": 2}, {"key": "c/d"}], "t2")
    assert new == {"c/d"}
    rows = {r["key"]: r for r in client.read_records("Scored")}
    assert len(rows) == 2
    assert rows["a/b"]["name"] == "y"
    assert rows["a/b"]["manual_notes"] == "good one"


def test_upsert_only_new_does_not_touch_existing(client):
    client.upsert("Alerts", [{"alert_key": "F:1", "name": "old"}], "t1")
    ws = client.worksheet("Alerts")
    ws.values[1][TABS["Alerts"].index("sent")] = "TRUE"
    new = client.upsert("Alerts", [{"alert_key": "F:1", "name": "new", "sent": ""}, {"alert_key": "F:2"}], "t2", only_new=True)
    assert new == {"F:2"}
    rows = {r["alert_key"]: r for r in client.read_records("Alerts")}
    assert rows["F:1"]["sent"] == "TRUE" and rows["F:1"]["name"] == "old"


def test_log_run_and_last_run(client):
    assert client.last_run("firefox") is None
    client.log_run("2026-10-01T00:00:00+00:00", "firefox", 5, 0, 1.23)
    client.log_run("2026-10-20T00:00:00+00:00", "firefox", 5, 0, 1.23)
    assert client.last_run("firefox").day == 20
