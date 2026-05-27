from __future__ import annotations

from pathlib import Path

import pytest

from sync.config import load_config


def _write_toml(path: Path, content: str) -> None:
    path.write_text(content)


def test_load_from_toml(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    _write_toml(
        cfg_path,
        """
[spotify]
client_id = "cid"
client_secret = "csecret"
target_playlist_name = "My Playlist"
target_playlist_id = "pid123"

[notifications]
errors = false
warnings = true
adds = false
consecutive_failures_threshold = 5

[logging]
level = "DEBUG"
file = "/tmp/test-sync.log"
""",
    )
    cfg = load_config(cfg_path)
    assert cfg.spotify.client_id == "cid"
    assert cfg.spotify.client_secret == "csecret"
    assert cfg.spotify.target_playlist_name == "My Playlist"
    assert cfg.spotify.target_playlist_id == "pid123"
    assert cfg.notifications.errors is False
    assert cfg.notifications.warnings is True
    assert cfg.notifications.adds is False
    assert cfg.notifications.consecutive_failures_threshold == 5
    assert cfg.logging.level == "DEBUG"


def test_env_vars_override_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, '[spotify]\nclient_id = "from_file"\nclient_secret = "from_file_s"')
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "from_env")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "from_env_s")
    cfg = load_config(cfg_path)
    assert cfg.spotify.client_id == "from_env"
    assert cfg.spotify.client_secret == "from_env_s"


def test_missing_client_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, '[spotify]\nclient_secret = "s"')
    with pytest.raises(ValueError, match="client_id"):
        load_config(cfg_path)


def test_missing_client_secret_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, '[spotify]\nclient_id = "c"')
    with pytest.raises(ValueError, match="client_secret"):
        load_config(cfg_path)


def test_no_config_file_uses_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env_cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "env_csecret")
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.spotify.client_id == "env_cid"


def test_log_file_expands_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "csec")
    cfg_path = tmp_path / "config.toml"
    _write_toml(cfg_path, '[spotify]\n[logging]\nfile = "~/some/path/sync.log"')
    cfg = load_config(cfg_path)
    assert not str(cfg.log_file).startswith("~")
    assert "some/path/sync.log" in str(cfg.log_file)


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "csec")
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.spotify.target_playlist_name == "Liked Playlist"
    assert cfg.notifications.errors is True
    assert cfg.notifications.consecutive_failures_threshold == 3
    assert cfg.logging.level == "INFO"
