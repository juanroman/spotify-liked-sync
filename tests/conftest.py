from __future__ import annotations

import json
from pathlib import Path

import pytest

from sync.config import Config, LoggingConfig, NotificationsConfig, SpotifyConfig


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    return Config(
        spotify=SpotifyConfig(
            client_id="test_client_id",
            client_secret="test_client_secret",
            target_playlist_name="Test Playlist",
            target_playlist_id="",
        ),
        notifications=NotificationsConfig(),
        logging=LoggingConfig(level="DEBUG", file=str(tmp_path / "sync.log")),
    )


@pytest.fixture()
def tokens_file(config: Config) -> Path:
    path = config.state_dir / "tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_tokens(path: Path, expires_in_seconds: int = 3600) -> None:
    from datetime import UTC, datetime, timedelta

    expires_at = datetime.now(tz=UTC) + timedelta(seconds=expires_in_seconds)
    path.write_text(
        json.dumps(
            {
                "access_token": "access_tok",
                "refresh_token": "refresh_tok",
                "expires_at": expires_at.isoformat(),
            }
        )
    )
