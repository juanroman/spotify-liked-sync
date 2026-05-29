from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from sync.client import RateLimitError, SpotifyAPIError, SpotifyClient
from sync.config import Config

API = "https://api.spotify.com/v1"


def make_client(config: Config, token: str = "tok") -> SpotifyClient:
    return SpotifyClient(config, lambda: token)


# ──────────────────────────── get_liked_songs ────────────────────────────


def test_get_liked_songs_single_page(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50",
        json={
            "items": [
                {"track": {"uri": "spotify:track:A"}},
                {"track": {"uri": "spotify:track:B"}},
            ],
            "next": None,
        },
    )
    with make_client(config) as client:
        uris = client.get_liked_songs()
    assert uris == ["spotify:track:A", "spotify:track:B"]


def test_get_liked_songs_skips_null_tracks(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50",
        json={"items": [{"track": None}, {"track": {"uri": "spotify:track:A"}}], "next": None},
    )
    with make_client(config) as client:
        uris = client.get_liked_songs()
    assert uris == ["spotify:track:A"]


def test_get_liked_songs_pagination(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50",
        json={
            "items": [{"track": {"uri": "spotify:track:A"}}],
            "next": f"{API}/me/tracks?limit=50&offset=50",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50&offset=50",
        json={
            "items": [{"track": {"uri": "spotify:track:B"}}],
            "next": None,
        },
    )
    with make_client(config) as client:
        uris = client.get_liked_songs()
    assert uris == ["spotify:track:A", "spotify:track:B"]


# ──────────────────────────── error handling ────────────────────────────


def test_rate_limit_raises(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50",
        status_code=429,
        headers={"Retry-After": "30"},
    )
    with make_client(config) as client, pytest.raises(RateLimitError) as exc_info:
        client.get_liked_songs()
    assert exc_info.value.retry_after == 30


def test_4xx_raises_api_error(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50",
        status_code=403,
        text="Forbidden",
    )
    with make_client(config) as client, pytest.raises(SpotifyAPIError) as exc_info:
        client.get_liked_songs()
    assert exc_info.value.status_code == 403


def test_5xx_retries_and_raises(config: Config, httpx_mock: HTTPXMock) -> None:
    for _ in range(3):
        httpx_mock.add_response(
            method="GET",
            url=f"{API}/me/tracks?limit=50",
            status_code=503,
        )
    with make_client(config) as client, pytest.raises(httpx.HTTPStatusError):
        client.get_liked_songs()


# ──────────────────────────── replace_playlist / WAL ────────────────────────────


def test_replace_playlist_success(config: Config, httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    playlist_id = "pl1"
    wal_path = tmp_path / "wal.json"

    httpx_mock.add_response(
        method="GET",
        url=f"{API}/playlists/{playlist_id}/items?limit=100&fields=next%2Citems%28item%28uri%29%29",
        json={"items": [{"item": {"uri": "spotify:track:OLD"}}], "next": None},
    )
    httpx_mock.add_response(
        method="PUT",
        url=f"{API}/playlists/{playlist_id}/items",
        json={"snapshot_id": "snap1"},
    )

    with make_client(config) as client:
        client.replace_playlist(playlist_id, ["spotify:track:NEW"], wal_path)

    assert not wal_path.exists()


def test_replace_playlist_wal_preserved_when_restore_also_fails(
    config: Config, httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    # WAL must stay on disk when both write and restore fail — it's the only recovery artifact.
    playlist_id = "pl2"
    wal_path = tmp_path / "wal.json"

    httpx_mock.add_response(
        method="GET",
        url=f"{API}/playlists/{playlist_id}/items?limit=100&fields=next%2Citems%28item%28uri%29%29",
        json={"items": [{"item": {"uri": "spotify:track:OLD"}}], "next": None},
    )
    # write fails (3 tenacity attempts)
    for _ in range(3):
        httpx_mock.add_response(
            method="PUT",
            url=f"{API}/playlists/{playlist_id}/items",
            status_code=500,
        )
    # restore also fails (3 tenacity attempts)
    for _ in range(3):
        httpx_mock.add_response(
            method="PUT",
            url=f"{API}/playlists/{playlist_id}/items",
            status_code=500,
        )

    with make_client(config) as client, pytest.raises(httpx.HTTPStatusError):
        client.replace_playlist(playlist_id, ["spotify:track:NEW"], wal_path)

    assert wal_path.exists()
    wal = json.loads(wal_path.read_text())
    assert wal["snapshot"] == ["spotify:track:OLD"]
    assert wal["intended"] == ["spotify:track:NEW"]


def test_replace_playlist_batches_over_100(
    config: Config, httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    playlist_id = "pl3"
    wal_path = tmp_path / "wal.json"
    uris = [f"spotify:track:{i}" for i in range(150)]

    httpx_mock.add_response(
        method="GET",
        url=f"{API}/playlists/{playlist_id}/items?limit=100&fields=next%2Citems%28item%28uri%29%29",
        json={"items": [], "next": None},
    )
    httpx_mock.add_response(method="PUT", url=f"{API}/playlists/{playlist_id}/items", json={})
    httpx_mock.add_response(method="POST", url=f"{API}/playlists/{playlist_id}/items", json={})

    with make_client(config) as client:
        client.replace_playlist(playlist_id, uris, wal_path)

    assert not wal_path.exists()


# ──────────────────────────── find_or_create_playlist ────────────────────────────


def test_find_existing_playlist(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/playlists?limit=50",
        json={
            "items": [{"id": "found_id", "name": "Test Playlist"}],
            "next": None,
        },
    )
    with make_client(config) as client:
        pl_id = client.find_or_create_playlist("Test Playlist")
    assert pl_id == "found_id"


def test_create_playlist_when_not_found(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/playlists?limit=50",
        json={"items": [{"id": "other", "name": "Other Playlist"}], "next": None},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/me/playlists",
        json={"id": "new_pl_id"},
    )
    with make_client(config) as client:
        pl_id = client.find_or_create_playlist("Test Playlist")
    assert pl_id == "new_pl_id"


def test_replace_playlist_empty_list(config: Config, httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    playlist_id = "pl_empty"
    wal_path = tmp_path / "wal.json"

    httpx_mock.add_response(
        method="GET",
        url=f"{API}/playlists/{playlist_id}/items?limit=100&fields=next%2Citems%28item%28uri%29%29",
        json={"items": [], "next": None},
    )
    httpx_mock.add_response(method="PUT", url=f"{API}/playlists/{playlist_id}/items", json={})

    with make_client(config) as client:
        client.replace_playlist(playlist_id, [], wal_path)

    assert not wal_path.exists()


def test_replace_playlist_wal_deleted_after_restore_success(
    config: Config, httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    playlist_id = "pl_restore"
    wal_path = tmp_path / "wal.json"

    httpx_mock.add_response(
        method="GET",
        url=f"{API}/playlists/{playlist_id}/items?limit=100&fields=next%2Citems%28item%28uri%29%29",
        json={"items": [{"item": {"uri": "spotify:track:OLD"}}], "next": None},
    )
    # PUT fails (3 attempts via tenacity)
    for _ in range(3):
        httpx_mock.add_response(
            method="PUT",
            url=f"{API}/playlists/{playlist_id}/items",
            status_code=500,
        )
    # restore PUT succeeds
    httpx_mock.add_response(
        method="PUT",
        url=f"{API}/playlists/{playlist_id}/items",
        json={"snapshot_id": "restored"},
    )

    with make_client(config) as client, pytest.raises(httpx.HTTPStatusError):
        client.replace_playlist(playlist_id, ["spotify:track:NEW"], wal_path)

    # Restore succeeded — WAL should be gone so subsequent runs don't see a false alarm
    assert not wal_path.exists()


def test_get_playlist_tracks_empty(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/playlists/pl1/items?limit=100&fields=next%2Citems%28item%28uri%29%29",
        json={"items": [], "next": None},
    )
    with make_client(config) as client:
        uris = client.get_playlist_tracks("pl1")
    assert uris == []


def test_pagination_next_url_with_equals_in_param_value(
    config: Config, httpx_mock: HTTPXMock
) -> None:
    # next URL whose query string has a param value containing '=' (e.g. a base64 cursor).
    # The old split('=') parser raised ValueError; urllib.parse.parse_qs handles it correctly.
    next_url = f"{API}/me/tracks?limit=50&cursor=abc%3Ddef%3Dextra"
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50",
        json={
            "items": [{"track": {"uri": "spotify:track:A"}}],
            "next": next_url,
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=next_url,
        json={"items": [{"track": {"uri": "spotify:track:B"}}], "next": None},
    )

    with make_client(config) as client:
        uris = client.get_liked_songs()

    assert uris == ["spotify:track:A", "spotify:track:B"]


def test_401_triggers_token_refresh(config: Config, httpx_mock: HTTPXMock) -> None:
    call_count = 0

    def token_fn() -> str:
        nonlocal call_count
        call_count += 1
        return "tok"

    client = SpotifyClient(config, token_fn)

    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50",
        status_code=401,
    )
    # After 401, retry with fresh token
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/me/tracks?limit=50",
        json={"items": [], "next": None},
    )

    uris = client.get_liked_songs()
    assert uris == []
    assert call_count >= 2  # original + refresh call
    client.close()
