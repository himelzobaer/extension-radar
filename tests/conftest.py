"""Shared test fixtures: config and an in-memory fake of the Sheets client."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.sheets import TABS, SheetClient  # noqa: E402
from radar.utils import load_config  # noqa: E402


class FakeWorksheet:
    """Just enough of gspread.Worksheet for SheetClient."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.values: list[list[Any]] = []
        self.row_count, self.col_count = 1000, 26

    def get_all_values(self) -> list[list[Any]]:
        return [[str(c) for c in r] for r in self.values]

    def row_values(self, n: int) -> list[str]:
        return [str(c) for c in self.values[n - 1]] if len(self.values) >= n else []

    def col_values(self, n: int) -> list[str]:
        return [str(r[n - 1]) if len(r) >= n else "" for r in self.values]

    def update(self, values: list[list[Any]], range_name: str = "A1", **_: Any) -> None:
        assert range_name == "A1"
        if len(values) == 1 and self.values:
            self.values[0] = list(values[0])
        else:
            self.values = [list(r) for r in values]

    def append_rows(self, rows: list[list[Any]], **_: Any) -> None:
        self.values.extend(list(r) for r in rows)

    def resize(self, rows: int, cols: int) -> None:
        self.row_count, self.col_count = rows, cols

    def freeze(self, rows: int) -> None:
        pass


class FakeSpreadsheet:
    def __init__(self) -> None:
        self.sheets: dict[str, FakeWorksheet] = {"Sheet1": FakeWorksheet("Sheet1")}

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self.sheets.values())

    def worksheet(self, name: str) -> FakeWorksheet:
        return self.sheets[name]

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        self.sheets[title] = FakeWorksheet(title)
        return self.sheets[title]

    def del_worksheet(self, ws: FakeWorksheet) -> None:
        del self.sheets[ws.title]


@pytest.fixture
def cfg() -> dict[str, Any]:
    c = load_config()
    c["github"]["search_delay_seconds"] = 0
    c["github"]["core_delay_seconds"] = 0
    c["firefox"]["api_delay_seconds"] = 0
    c["chrome_search"]["delay_min_seconds"] = 0
    c["chrome_search"]["delay_max_seconds"] = 0
    c["http"]["backoff_base_seconds"] = 0
    return c


@pytest.fixture
def client() -> SheetClient:
    c = SheetClient(FakeSpreadsheet())
    c.ensure_tabs()
    return c


def fake_response(status: int = 200, json_data: Any = None, text: str = "", headers: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.text = text
    r.headers = headers or {}
    return r


__all__ = ["TABS", "fake_response"]
