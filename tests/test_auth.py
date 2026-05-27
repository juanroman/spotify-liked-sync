from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from conftest import write_tokens
from pytest_httpx import HTTPXMock

from sync.auth import (
    _build_auth_url,
    _pkce_pair,
    _refresh_tokens,
    _tokens_path,
    get_valid_token,
)
from sync.config import Config


def test_get_valid_token_fresh(config: Config, tokens_file: Path) -> None:
    write_tokens(tokens_file, expires_in_seconds=3600)
    token = get_valid_token(config)
    assert token == "access_tok"


def test_get_valid_token_no_file(config: Config) -> None:
    with pytest.raises(FileNotFoundError, match="tokens"):
        get_valid_token(config)


def test_get_valid_token_refreshes_when_expiring(
    config: Config, tokens_file: Path, httpx_mock: HTTPXMock
) -> None:
    write_tokens(tokens_file, expires_in_seconds=30)

    httpx_mock.add_response(
        method="POST",
        url="https://accounts.spotify.com/api/token",
        json={
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_in": 3600,
        },
    )

    token = get_valid_token(config, http_client=httpx.Client())
    assert token == "new_access"

    saved = json.loads(tokens_file.read_text())
    assert saved["access_token"] == "new_access"
    assert saved["refresh_token"] == "new_refresh"


def test_refresh_tokens(config: Config, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://accounts.spotify.com/api/token",
        json={
            "access_token": "refreshed",
            "refresh_token": "new_rt",
            "expires_in": 3600,
        },
    )
    result = _refresh_tokens(config, "old_refresh", httpx.Client())
    assert result["access_token"] == "refreshed"
    assert result["refresh_token"] == "new_rt"
    assert "expires_at" in result


def test_refresh_tokens_preserves_refresh_token_if_absent(
    config: Config, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://accounts.spotify.com/api/token",
        json={"access_token": "new_acc", "expires_in": 3600},
    )
    result = _refresh_tokens(config, "kept_refresh", httpx.Client())
    assert result["refresh_token"] == "kept_refresh"


def test_tokens_path(config: Config) -> None:
    path = _tokens_path(config)
    assert path.name == "tokens.json"
    assert path.parent == config.state_dir


def test_pkce_pair_produces_valid_challenge() -> None:
    import base64
    import hashlib

    verifier, challenge = _pkce_pair()
    assert len(verifier) > 40
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert challenge == expected


def test_build_auth_url_contains_required_params() -> None:
    url = _build_auth_url("my_client", "my_challenge", "my_state")
    assert "client_id=my_client" in url
    assert "code_challenge=my_challenge" in url
    assert "state=my_state" in url
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert url.startswith("https://accounts.spotify.com/authorize")
