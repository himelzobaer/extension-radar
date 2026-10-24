import logging
from unittest.mock import MagicMock

import pytest
import requests

from conftest import fake_response
from radar.utils import HttpError, SecretMaskFilter, request_with_retry


def test_retry_then_success(cfg):
    s = MagicMock()
    s.get.side_effect = [fake_response(500), requests.ConnectionError("boom"), fake_response(200, {"ok": 1})]
    assert request_with_retry(s, "http://x", cfg).json() == {"ok": 1}
    assert s.get.call_count == 3


def test_retry_exhausted(cfg):
    cfg["http"]["max_retries"] = 1
    s = MagicMock()
    s.get.return_value = fake_response(503)
    with pytest.raises(HttpError):
        request_with_retry(s, "http://x", cfg)
    assert s.get.call_count == 2


def test_non_retryable_raises_immediately(cfg):
    s = MagicMock()
    s.get.return_value = fake_response(401)
    with pytest.raises(HttpError):
        request_with_retry(s, "http://x", cfg)
    assert s.get.call_count == 1


def test_secret_masked(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "supersecretvalue123")
    rec = logging.LogRecord("x", logging.INFO, "", 0, "token is %s", ("supersecretvalue123",), None)
    SecretMaskFilter().filter(rec)
    assert "supersecretvalue123" not in rec.getMessage()
