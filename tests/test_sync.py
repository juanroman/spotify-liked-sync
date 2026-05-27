from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

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
    import logging

    with caplog.at_level(logging.WARNING):
        run_sync(config, client)

    assert any("WAL" in r.message for r in caplog.records)


def test_rate_limit_increments_failure_counter(config: Config) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RateLimitError(60)

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)

    run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state["consecutive_failures"] == 1


def test_consecutive_failures_warning(config: Config, caplog: pytest.LogCaptureFixture) -> None:
    client = _make_mock_client([], [])
    client.get_liked_songs.side_effect = RateLimitError(60)

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"consecutive_failures": 2}))

    import logging

    with caplog.at_level(logging.WARNING):
        run_sync(config, client)

    assert any("consecutive" in r.message.lower() for r in caplog.records)


def test_success_resets_failure_counter(config: Config) -> None:
    uris = ["spotify:track:A"]
    client = _make_mock_client(uris, uris)

    state_path = config.state_dir / "state.json"
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"consecutive_failures": 5}))

    run_sync(config, client)

    state = json.loads(state_path.read_text())
    assert state["consecutive_failures"] == 0


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
