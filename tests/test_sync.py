from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync.auth import RefreshTokenExpiredError
from sync.client import RateLimitError, SpotifyAPIError, SpotifyClient
from sync.config import Config
from sync.sync import run_sync


def _make_mock_client(
    liked: list[str],
    current: list[str],
    playlist_id: str = "pl1",
) -> MagicMock:
    client = MagicMock(spec=SpotifyClient)
    client.find_or_create_playlist.return_value = playlist_id
    client.get_liked_songs.return_value = liked
    client.get_playlist_tracks.return_value = current
    return client


def test_no_diff_skips_write(config: Config) -> None:
    uris = ["spotify:track:A", "spotify:track:B"]
    client = _make_mock_client(uris, uris)
    run_sync(config, client)
    client.replace_playlist.assert_not_called()


def test_adds_triggers_replace(config: Config) -> None:
    liked = ["spotify:track:A", "spotify:track:B"]
    current = ["spotify:track:A"]
    client = _make_mock_client(liked, current)
    run_sync(config, client)
    client.replace_playlist.assert_called_once()
    args = client.replace_playlist.call_args[0]
    assert args[1] == liked


def test_removes_triggers_replace(config: Config) -> None:
    liked = ["spotify:track:A"]
    current = ["spotify:track:A", "spotify:track:B"]
    client = _make_mock_client(liked, current)
    run_sync(config, client)
    client.replace_playlist.assert_called_once()


def test_order_change_triggers_replace(config: Config) -> None:
    liked = ["spotify:track:B", "spotify:track:A"]
    current = ["spotify:track:A", "spotify:track:B"]
    client = _make_mock_client(liked, current)
    run_sync(config, client)
    client.replace_playlist.assert_called_once()


def test_wal_warning_on_startup(config: Config, caplog: pytest.LogCaptureFixture) -> None:
    wal_path = config.state_dir / "pending_write.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    wal_path.write_text(json.dumps({"playlist_id": "pl1", "intended": [], "snapshot": []}))

    client = _make_mock_client([], [])

    with caplog.at_level(logging.WARNING):
        run_sync(config, client)

    assert any("WAL" in r.message for r in caplog.records)


def test_rate_limit_does_not_increment_failure_counter(config: Config) -> None:
    # Rate-limiting is infrastructure noise, not a script failure; incrementing the counter
    # would trigger the consecutive-failures warning when the cause is transient, not a bug.
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RateLimitError(60)

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"consecutive_failures": 2}))

    run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state["consecutive_failures"] == 2  # unchanged


def test_consecutive_failures_warning_on_api_error(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    # The consecutive-failures warning fires for genuine API errors, not rate-limits.
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = SpotifyAPIError(500, "Internal Server Error")

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"consecutive_failures": 2}))

    with caplog.at_level(logging.WARNING), contextlib.suppress(SpotifyAPIError):
        run_sync(config, client)

    assert any("consecutive" in r.message.lower() for r in caplog.records)


def test_rate_limit_does_not_fire_consecutive_failures_warning(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RateLimitError(60)

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"consecutive_failures": 2}))

    with caplog.at_level(logging.WARNING):
        run_sync(config, client)

    assert not any("consecutive" in r.message.lower() for r in caplog.records)


def test_success_resets_failure_counter(config: Config) -> None:
    uris = ["spotify:track:A"]
    client = _make_mock_client(uris, uris)

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"consecutive_failures": 5}))

    run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state["consecutive_failures"] == 0


def test_run_sync_passes_config_path_to_persist(config: Config, tmp_path: Path) -> None:
    """run_sync must forward config.config_path so persist_playlist_id writes the right file."""
    cfg_path = tmp_path / "custom_config.toml"
    cfg_path.write_text('[spotify]\nclient_id = "x"\nclient_secret = "y"\n')
    config.config_path = cfg_path

    client = _make_mock_client(["spotify:track:A"], [])
    run_sync(config, client)

    assert "target_playlist_id" in cfg_path.read_text()


def test_uses_config_playlist_id_when_set(config: Config) -> None:
    config.spotify.target_playlist_id = "preconfigured_id"
    client = _make_mock_client([], [], playlist_id="preconfigured_id")
    run_sync(config, client)
    client.find_or_create_playlist.assert_not_called()


def test_api_error_reraises(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = SpotifyAPIError(403, "Forbidden")

    with pytest.raises(SpotifyAPIError):
        run_sync(config, client)


def test_generic_exception_reraises_and_increments_counter(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RuntimeError("unexpected")

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError):
        run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state["consecutive_failures"] == 1


def test_non_auth_api_error_increments_counter(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = SpotifyAPIError(500, "Internal Server Error")

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(SpotifyAPIError):
        run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state["consecutive_failures"] == 1


def test_push_called_on_sync_with_adds(config: Config) -> None:
    liked = ["spotify:track:A", "spotify:track:B"]
    current = ["spotify:track:A"]
    client = _make_mock_client(liked, current)

    with patch("sync.sync.push") as mock_push:
        run_sync(config, client)

    mock_push.assert_called_once()
    message = mock_push.call_args[0][1]
    assert "+1 added" in message


def test_push_called_with_removed_count(config: Config) -> None:
    liked = ["spotify:track:A"]
    current = ["spotify:track:A", "spotify:track:B"]
    client = _make_mock_client(liked, current)

    with patch("sync.sync.push") as mock_push:
        run_sync(config, client)

    mock_push.assert_called_once()
    message = mock_push.call_args[0][1]
    assert "-1 removed" in message


def test_push_not_called_on_no_changes(config: Config) -> None:
    uris = ["spotify:track:A"]
    client = _make_mock_client(uris, uris)

    with patch("sync.sync.push") as mock_push:
        run_sync(config, client)

    mock_push.assert_not_called()


def test_push_called_on_consecutive_failures(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = SpotifyAPIError(500, "Server Error")

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"consecutive_failures": 2}))

    with patch("sync.sync.push") as mock_push, contextlib.suppress(SpotifyAPIError):
        run_sync(config, client)

    mock_push.assert_called_once()
    message = mock_push.call_args[0][1]
    assert "consecutive" in message.lower()


def test_push_called_on_fatal_4xx(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = SpotifyAPIError(403, "Forbidden")

    with patch("sync.sync.push") as mock_push, contextlib.suppress(SpotifyAPIError):
        run_sync(config, client)

    mock_push.assert_called_once()
    message = mock_push.call_args[0][1]
    assert "403" in message


def test_push_not_called_on_rate_limit(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RateLimitError(30)

    with patch("sync.sync.push") as mock_push:
        run_sync(config, client)

    mock_push.assert_not_called()


# --- RefreshTokenExpiredError tests ---


def test_refresh_token_expired_sends_push_immediately(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RefreshTokenExpiredError("expired")

    with patch("sync.sync.push") as mock_push, contextlib.suppress(RefreshTokenExpiredError):
        run_sync(config, client)

    mock_push.assert_called_once()
    call_kwargs = mock_push.call_args[1]
    call_args = mock_push.call_args[0]
    title = call_kwargs.get("title") or (call_args[2] if len(call_args) > 2 else "")
    assert "Re-auth" in title


def test_refresh_token_expired_does_not_increment_counter(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RefreshTokenExpiredError("expired")

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"consecutive_failures": 2}))

    with contextlib.suppress(RefreshTokenExpiredError):
        run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state["consecutive_failures"] == 2


def test_refresh_token_expired_reraises(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RefreshTokenExpiredError("expired")

    with pytest.raises(RefreshTokenExpiredError):
        run_sync(config, client)


def test_refresh_token_expired_no_push_when_notifications_disabled(config: Config) -> None:
    config.notifications.errors = False
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RefreshTokenExpiredError("expired")

    with patch("sync.sync.push") as mock_push, contextlib.suppress(RefreshTokenExpiredError):
        run_sync(config, client)

    mock_push.assert_not_called()


# --- Proactive warning tests ---


def test_proactive_warning_fires_at_14_days(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    client = _make_mock_client([], [])

    with (
        patch("sync.sync.days_until_token_expiry", return_value=14),
        patch("sync.sync.push") as mock_push,
        caplog.at_level(logging.WARNING),
    ):
        run_sync(config, client)

    assert any("14 day" in r.message for r in caplog.records)
    mock_push.assert_called_once()
    msg = mock_push.call_args[0][1]
    assert "14 day" in msg


def test_proactive_warning_fires_at_0_days(config: Config) -> None:
    client = _make_mock_client([], [])

    with (
        patch("sync.sync.days_until_token_expiry", return_value=0),
        patch("sync.sync.push") as mock_push,
    ):
        run_sync(config, client)

    mock_push.assert_called_once()
    msg = mock_push.call_args[0][1]
    assert "0 day" in msg


def test_proactive_warning_skipped_at_15_days(config: Config) -> None:
    client = _make_mock_client([], [])

    with (
        patch("sync.sync.days_until_token_expiry", return_value=15),
        patch("sync.sync.push") as mock_push,
    ):
        run_sync(config, client)

    mock_push.assert_not_called()


def test_proactive_warning_skipped_when_authorized_at_absent(config: Config) -> None:
    client = _make_mock_client([], [])

    with (
        patch("sync.sync.days_until_token_expiry", return_value=None),
        patch("sync.sync.push") as mock_push,
    ):
        run_sync(config, client)

    mock_push.assert_not_called()


def test_proactive_warning_logs_even_without_pushover(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    config.notifications.pushover_token = ""
    config.notifications.pushover_user = ""
    client = _make_mock_client([], [])

    with (
        patch("sync.sync.days_until_token_expiry", return_value=7),
        caplog.at_level(logging.WARNING),
    ):
        run_sync(config, client)

    assert any("7 day" in r.message for r in caplog.records)


# --- Rate-limit backoff persistence tests ---


def test_rate_limit_persists_rate_limited_until(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RateLimitError(600)

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)

    before = datetime.now(tz=UTC)
    run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state.get("rate_limited_until") is not None
    stored = datetime.fromisoformat(state["rate_limited_until"])
    assert stored >= before + timedelta(seconds=595)
    assert stored <= before + timedelta(seconds=605)


def test_rate_limit_skips_run_when_within_backoff(config: Config) -> None:
    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=UTC) + timedelta(seconds=60)).isoformat()
    state_path.write_text(json.dumps({"consecutive_failures": 0, "rate_limited_until": future}))

    client = _make_mock_client([], [])
    run_sync(config, client)

    client.get_liked_songs.assert_not_called()


def test_rate_limit_does_not_skip_after_backoff_expires(config: Config) -> None:
    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    past = (datetime.now(tz=UTC) - timedelta(seconds=60)).isoformat()
    state_path.write_text(json.dumps({"consecutive_failures": 0, "rate_limited_until": past}))

    uris = ["spotify:track:A"]
    client = _make_mock_client(uris, uris)
    run_sync(config, client)

    client.get_liked_songs.assert_called_once()


def test_success_clears_rate_limited_until(config: Config) -> None:
    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    past = (datetime.now(tz=UTC) - timedelta(seconds=60)).isoformat()
    state_path.write_text(json.dumps({"consecutive_failures": 0, "rate_limited_until": past}))

    uris = ["spotify:track:A"]
    client = _make_mock_client(uris, uris)
    run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state.get("rate_limited_until") is None


def test_rate_limit_logs_backoff_skip(config: Config, caplog: pytest.LogCaptureFixture) -> None:
    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=UTC) + timedelta(seconds=60)).isoformat()
    state_path.write_text(json.dumps({"consecutive_failures": 0, "rate_limited_until": future}))

    client = _make_mock_client([], [])
    with caplog.at_level(logging.INFO):
        run_sync(config, client)

    assert any(
        "rate-limit" in r.message.lower() or "backoff" in r.message.lower()
        for r in caplog.records
    )
