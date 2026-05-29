from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from sync.config import NotificationsConfig
from sync.notify import push


def _config(token: str = "tok123", user: str = "usr456") -> NotificationsConfig:
    return NotificationsConfig(pushover_token=token, pushover_user=user)


def test_push_no_op_when_token_missing() -> None:
    with patch("sync.notify.httpx.post") as mock_post:
        push(_config(token=""), "hello")
    mock_post.assert_not_called()


def test_push_no_op_when_user_missing() -> None:
    with patch("sync.notify.httpx.post") as mock_post:
        push(_config(user=""), "hello")
    mock_post.assert_not_called()


def test_push_posts_to_pushover() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("sync.notify.httpx.post", return_value=mock_response) as mock_post:
        push(_config(), "2 added, 1 removed")

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "https://api.pushover.net/1/messages.json"
    data = call_kwargs[1]["data"]
    assert data["token"] == "tok123"
    assert data["user"] == "usr456"
    assert data["message"] == "2 added, 1 removed"
    assert data["title"] == "Spotify Sync"


def test_push_custom_title() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("sync.notify.httpx.post", return_value=mock_response) as mock_post:
        push(_config(), "something broke", title="Alert")

    data = mock_post.call_args[1]["data"]
    assert data["title"] == "Alert"


def test_push_logs_warning_on_http_error(caplog: pytest.LogCaptureFixture) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"errors": ["invalid token"], "request": "abc"}

    with (
        patch("sync.notify.httpx.post", return_value=mock_response),
        caplog.at_level(logging.WARNING, logger="sync.notify"),
    ):
        push(_config(), "test")

    assert any("invalid token" in r.message for r in caplog.records)


def test_push_swallows_network_exception(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("sync.notify.httpx.post", side_effect=OSError("connection refused")),
        caplog.at_level(logging.WARNING, logger="sync.notify"),
    ):
        push(_config(), "test")  # must not raise

    assert any("connection refused" in r.message for r in caplog.records)
