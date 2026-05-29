from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sync.client import RateLimitError, SpotifyAPIError, SpotifyClient
from sync.config import Config, persist_playlist_id

log = logging.getLogger(__name__)


def run_sync(config: Config, client: SpotifyClient) -> None:
    state_dir = config.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    wal_path = state_dir / "pending_write.json"
    state_path = state_dir / "state.json"

    if wal_path.exists():
        log.warning(
            "WAL file found at %s — previous run may have died mid-write. "
            "Inspect the file and delete it once you've verified playlist state.",
            wal_path,
        )

    state = _load_state(state_path)

    try:
        if config.spotify.target_playlist_id:
            playlist_id = config.spotify.target_playlist_id
        else:
            playlist_id = client.find_or_create_playlist(config.spotify.target_playlist_name)
            persist_playlist_id(playlist_id, config.config_path)
            log.info("Persisted target_playlist_id=%s to config.toml", playlist_id)

        liked_uris = client.get_liked_songs()
        current_uris = client.get_playlist_tracks(playlist_id)

        # List equality, not set: Liked Songs order (most-recent-first) is meaningful and preserved.
        if liked_uris == current_uris:
            log.info("Playlist already in sync (%d tracks). Nothing to do.", len(liked_uris))
            _save_state(state_path, consecutive_failures=0)
            return

        liked_set = set(liked_uris)
        current_set = set(current_uris)
        added = liked_set - current_set
        removed = current_set - liked_set

        client.replace_playlist(playlist_id, liked_uris, wal_path)

        if config.notifications.adds and added:
            log.info("+%d track(s) added, -%d track(s) removed", len(added), len(removed))
        else:
            log.info("Sync complete: %d tracks in playlist", len(liked_uris))

        _save_state(state_path, consecutive_failures=0)

    except RateLimitError as exc:
        log.warning(
            "Rate limited by Spotify (retry after %ds). Skipping this run.", exc.retry_after
        )
        # Preserve the existing failure count rather than incrementing it: rate-limiting is
        # infrastructure noise, not a script failure, and incrementing would fire the
        # consecutive-failures warning when the root cause is transient throttling, not a bug.
        _save_state(state_path, consecutive_failures=state.get("consecutive_failures", 0))

    except SpotifyAPIError as exc:
        if exc.status_code in (400, 403, 404):
            log.error(
                "Spotify API error %d — requires manual intervention: %s",
                exc.status_code,
                exc,
            )
        else:
            log.error("Spotify API error: %s", exc)
        failures = state.get("consecutive_failures", 0) + 1
        _save_state(state_path, consecutive_failures=failures)
        _check_consecutive_failures(failures, config)
        raise

    except Exception as exc:
        log.error("Sync failed: %s", exc)
        failures = state.get("consecutive_failures", 0) + 1
        _save_state(state_path, consecutive_failures=failures)
        _check_consecutive_failures(failures, config)
        raise


def _check_consecutive_failures(failures: int, config: Config) -> None:
    threshold = config.notifications.consecutive_failures_threshold
    if config.notifications.warnings and failures >= threshold:
        log.warning(
            "%d consecutive sync failures (threshold=%d). Check logs for details.",
            failures,
            threshold,
        )


def _load_state(state_path: Path) -> dict[str, int]:
    if state_path.exists():
        data: dict[str, int] = json.loads(state_path.read_text())
        return data
    return {}


def _save_state(state_path: Path, consecutive_failures: int) -> None:
    state: dict[str, int | str] = {
        "consecutive_failures": consecutive_failures,
        "last_run": datetime.now(tz=UTC).isoformat(),
    }
    state_path.write_text(json.dumps(state, indent=2))
