from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync.config import Config


def test_setup_logging_does_not_accumulate_handlers(tmp_path: Path) -> None:
    # Each call to _setup_logging must produce exactly 2 handlers (file + stderr).
    # Without clearing first, repeated calls accumulate handlers and duplicate every log record.
    from sync.__main__ import _setup_logging

    root = logging.getLogger()
    root.handlers.clear()

    log_file = tmp_path / "sync.log"
    _setup_logging("INFO", log_file)
    _setup_logging("INFO", log_file)

    assert len(root.handlers) == 2
    root.handlers.clear()


def test_main_config_flag_without_value_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "csec")
    with patch.object(sys, "argv", ["sync", "run", "--config"]):
        from sync.__main__ import main

        with pytest.raises(SystemExit):
            main()


def test_main_no_args_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "csec")
    with patch.object(sys, "argv", ["sync"]):
        from sync.__main__ import main

        with pytest.raises(SystemExit):
            main()


def test_main_invalid_command_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "csec")
    with patch.object(sys, "argv", ["sync", "invalid"]):
        from sync.__main__ import main

        with pytest.raises(SystemExit):
            main()


def test_main_run_invokes_sync(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "csec")

    with (
        patch("sync.__main__.load_config", return_value=config),
        patch("sync.__main__.get_valid_token", return_value="tok"),
        patch("sync.__main__.run_sync") as mock_sync,
        patch("sync.__main__.SpotifyClient") as mock_client_cls,
        patch.object(sys, "argv", ["sync", "run"]),
    ):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from sync.__main__ import main

        main()

    mock_sync.assert_called_once()


def test_main_auth_invokes_flow(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "csec")

    with (
        patch("sync.__main__.load_config", return_value=config),
        patch("sync.__main__.run_auth_flow") as mock_auth,
        patch.object(sys, "argv", ["sync", "auth"]),
    ):
        from sync.__main__ import main

        main()

    mock_auth.assert_called_once_with(config)
