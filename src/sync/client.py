from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import tenacity

from sync.config import Config

log = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"


class RateLimitError(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limited by Spotify. Retry after {retry_after}s.")


class SpotifyAPIError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Spotify API error {status_code}: {message}")


class SpotifyClient:
    def __init__(self, config: Config, get_token: Callable[[], str]) -> None:
        self._config = config
        self._get_token = get_token
        self._http = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SpotifyClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        url = f"{SPOTIFY_API_BASE}{path}"

        @tenacity.retry(
            retry=tenacity.retry_if_exception_type(httpx.HTTPStatusError),
            stop=tenacity.stop_after_attempt(3),
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=16),
            reraise=True,
        )
        def _do() -> httpx.Response:
            resp = self._http.request(method, url, headers=self._headers(), **kwargs)  # type: ignore[arg-type]

            if resp.status_code == 401:
                self._get_token()
                resp = self._http.request(method, url, headers=self._headers(), **kwargs)  # type: ignore[arg-type]

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "60"))
                raise RateLimitError(retry_after)

            if resp.status_code >= 500:
                resp.raise_for_status()

            if resp.status_code >= 400:
                raise SpotifyAPIError(resp.status_code, resp.text)

            return resp

        return _do()

    def get_liked_songs(self) -> list[str]:
        uris: list[str] = []
        url = "/me/tracks"
        params: dict[str, int | str] = {"limit": 50}

        while True:
            resp = self._request("GET", url, params=params)
            data = resp.json()
            for item in data.get("items", []):
                track = item.get("track")
                if track and track.get("uri"):
                    uris.append(track["uri"])
            next_url: str | None = data.get("next")
            if not next_url:
                break
            parsed = next_url.removeprefix(SPOTIFY_API_BASE)
            url = parsed.split("?")[0]
            params = dict(p.split("=") for p in parsed.split("?")[1].split("&"))  # type: ignore[assignment]

        log.info("Fetched %d liked songs", len(uris))
        return uris

    def get_playlist_tracks(self, playlist_id: str) -> list[str]:
        uris: list[str] = []
        url = f"/playlists/{playlist_id}/items"
        params: dict[str, int | str] = {"limit": 100, "fields": "next,items(track(uri))"}

        while True:
            resp = self._request("GET", url, params=params)
            data = resp.json()
            for item in data.get("items", []):
                track = item.get("track")
                if track and track.get("uri"):
                    uris.append(track["uri"])
            next_url: str | None = data.get("next")
            if not next_url:
                break
            parsed = next_url.removeprefix(SPOTIFY_API_BASE)
            url = parsed.split("?")[0]
            params = dict(p.split("=") for p in parsed.split("?")[1].split("&"))  # type: ignore[assignment]

        return uris

    def get_current_user_id(self) -> str:
        resp = self._request("GET", "/me")
        return str(resp.json()["id"])

    def find_or_create_playlist(self, name: str) -> str:
        url = "/me/playlists"
        params: dict[str, int] = {"limit": 50}
        while True:
            resp = self._request("GET", url, params=params)
            data = resp.json()
            for pl in data.get("items", []):
                if pl.get("name") == name:
                    log.info("Found existing playlist '%s' (id=%s)", name, pl["id"])
                    return str(pl["id"])
            next_url: str | None = data.get("next")
            if not next_url:
                break
            parsed = next_url.removeprefix(SPOTIFY_API_BASE)
            url = parsed.split("?")[0]
            params = dict(p.split("=") for p in parsed.split("?")[1].split("&"))  # type: ignore[assignment]

        description = "Auto-synced from Spotify Liked Songs"
        resp = self._request(
            "POST",
            "/me/playlists",
            json={"name": name, "public": False, "description": description},
        )
        playlist_id = resp.json()["id"]
        log.info("Created new playlist '%s' (id=%s)", name, playlist_id)
        return str(playlist_id)

    def replace_playlist(self, playlist_id: str, track_uris: list[str], wal_path: Path) -> None:
        snapshot = self.get_playlist_tracks(playlist_id)

        wal = {"playlist_id": playlist_id, "intended": track_uris, "snapshot": snapshot}
        wal_path.write_text(json.dumps(wal))
        log.debug("WAL written to %s", wal_path)

        try:
            self._write_playlist(playlist_id, track_uris)
        except Exception as exc:
            log.error("Playlist write failed: %s — attempting restore", exc)
            try:
                self._write_playlist(playlist_id, snapshot)
                log.warning("Playlist restored to pre-write state after failed write")
            except Exception as restore_exc:
                log.critical(
                    "Write failed AND restore failed. WAL preserved at %s for manual recovery. "
                    "Restore error: %s",
                    wal_path,
                    restore_exc,
                )
                raise
            raise

        wal_path.unlink(missing_ok=True)
        log.debug("WAL removed after successful write")

    def _write_playlist(self, playlist_id: str, uris: list[str]) -> None:
        if not uris:
            self._request("PUT", f"/playlists/{playlist_id}/items", json={"uris": []})
            return

        self._request(
            "PUT",
            f"/playlists/{playlist_id}/items",
            json={"uris": uris[:100]},
        )

        for i in range(100, len(uris), 100):
            self._request(
                "POST",
                f"/playlists/{playlist_id}/items",
                json={"uris": uris[i : i + 100]},
            )
