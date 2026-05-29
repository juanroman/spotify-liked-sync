from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SpotifyConfig:
    client_id: str
    client_secret: str
    target_playlist_name: str = "Liked Playlist"
    target_playlist_id: str = ""


@dataclass
class NotificationsConfig:
    errors: bool = True
    warnings: bool = True
    adds: bool = True
    consecutive_failures_threshold: int = 3
    pushover_token: str = ""
    pushover_user: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "~/.local/share/spotify-sync/sync.log"


@dataclass
class Config:
    spotify: SpotifyConfig
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    # Stores the resolved path of the config file so callers (e.g. persist_playlist_id)
    # can write back to the same file even when --config points to a non-CWD location.
    config_path: Path | None = None

    @property
    def log_file(self) -> Path:
        return Path(self.logging.file).expanduser()

    @property
    def state_dir(self) -> Path:
        return self.log_file.parent


def persist_playlist_id(playlist_id: str, config_path: Path | None = None) -> None:
    # tomllib (stdlib) is read-only by design; writing TOML requires an external dep we want to
    # avoid, so we patch the file with regex instead.
    import re

    path = config_path or Path("config.toml")
    if not path.exists():
        path.write_text(f'[spotify]\ntarget_playlist_id = "{playlist_id}"\n')
        return

    text = path.read_text()

    # TOML keys are section-local; scope all lookups to the [spotify] block (everything from
    # [spotify] up to the next section header or EOF) to avoid touching identically-named keys
    # in other sections.
    spotify_block = re.search(r"(\[spotify\][^\[]*)", text, re.DOTALL)

    if spotify_block and re.search(
        r"^target_playlist_id\s*=", spotify_block.group(1), re.MULTILINE
    ):
        start, end = spotify_block.span(1)
        new_block = re.sub(
            r"^(target_playlist_id\s*=\s*).*$",
            f'target_playlist_id = "{playlist_id}"',
            spotify_block.group(1),
            flags=re.MULTILINE,
        )
        text = text[:start] + new_block + text[end:]
    elif spotify_block:
        start, end = spotify_block.span(1)
        new_block = re.sub(
            r"(\[spotify\]\n?)",
            f'\\1target_playlist_id = "{playlist_id}"\n',
            spotify_block.group(1),
            count=1,
        )
        text = text[:start] + new_block + text[end:]
    else:
        text += f'\n[spotify]\ntarget_playlist_id = "{playlist_id}"\n'

    path.write_text(text)


def load_config(config_path: Path | None = None) -> Config:
    path = config_path or Path("config.toml")

    raw: dict[str, object] = {}
    if path.exists():
        with path.open("rb") as f:
            raw = tomllib.load(f)

    spotify_raw: dict[str, object] = raw.get("spotify", {})  # type: ignore[assignment]

    client_id = os.environ.get("SPOTIFY_CLIENT_ID") or str(spotify_raw.get("client_id", ""))
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or str(
        spotify_raw.get("client_secret", "")
    )

    if not client_id:
        raise ValueError(
            "Spotify client_id is required. "
            "Set SPOTIFY_CLIENT_ID env var or spotify.client_id in config.toml."
        )
    if not client_secret:
        raise ValueError(
            "Spotify client_secret is required. "
            "Set SPOTIFY_CLIENT_SECRET env var or spotify.client_secret in config.toml."
        )

    spotify = SpotifyConfig(
        client_id=client_id,
        client_secret=client_secret,
        target_playlist_name=str(spotify_raw.get("target_playlist_name", "Liked Playlist")),
        target_playlist_id=str(spotify_raw.get("target_playlist_id", "")),
    )

    notif_raw: dict[str, object] = raw.get("notifications", {})  # type: ignore[assignment]
    notifications = NotificationsConfig(
        errors=bool(notif_raw.get("errors", True)),
        warnings=bool(notif_raw.get("warnings", True)),
        adds=bool(notif_raw.get("adds", True)),
        consecutive_failures_threshold=int(str(notif_raw.get("consecutive_failures_threshold", 3))),
        pushover_token=os.environ.get("PUSHOVER_TOKEN") or str(notif_raw.get("pushover_token", "")),
        pushover_user=os.environ.get("PUSHOVER_USER") or str(notif_raw.get("pushover_user", "")),
    )

    log_raw: dict[str, object] = raw.get("logging", {})  # type: ignore[assignment]
    logging_cfg = LoggingConfig(
        level=str(log_raw.get("level", "INFO")),
        file=str(log_raw.get("file", "~/.local/share/spotify-sync/sync.log")),
    )

    return Config(
        spotify=spotify,
        notifications=notifications,
        logging=logging_cfg,
        config_path=path if path.exists() else None,
    )
