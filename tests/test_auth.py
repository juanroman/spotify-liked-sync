from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from conftest import write_tokens
from pytest_httpx import HTTPXMock

from sync.auth import (
    _build_auth_url,
    _CallbackHandler,
    _pkce_pair,
    _refresh_tokens,
    _tokens_path,
    _wait_for_callback,
    get_valid_token,
    run_auth_flow,
)
from sync.config import Config


@pytest.fixture(autouse=True)
def reset_callback_handler_state() -> None:
    _CallbackHandler.code = None
    _CallbackHandler.state = None
    _CallbackHandler.error = None


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


def _make_handler(path: str) -> _CallbackHandler:
    handler = _CallbackHandler.__new__(_CallbackHandler)
    handler.path = path
    handler.send_response = MagicMock()  # type: ignore[method-assign]
    handler.end_headers = MagicMock()  # type: ignore[method-assign]
    handler.wfile = MagicMock()
    return handler


def test_callback_handler_parses_code_and_state() -> None:
    handler = _make_handler("/callback?code=auth_code_123&state=secure_state")
    handler.do_GET()
    assert _CallbackHandler.code == "auth_code_123"
    assert _CallbackHandler.state == "secure_state"
    assert _CallbackHandler.error is None


def test_callback_handler_captures_error() -> None:
    handler = _make_handler("/callback?error=access_denied&state=secure_state")
    handler.do_GET()
    assert _CallbackHandler.error == "access_denied"
    assert _CallbackHandler.code is None


def test_wait_for_callback_raises_on_auth_error() -> None:
    with patch("sync.auth.http.server.HTTPServer") as mock_server_cls:
        mock_server = MagicMock()
        mock_server_cls.return_value = mock_server

        def set_error(*_: object) -> None:
            _CallbackHandler.error = "access_denied"

        mock_server.handle_request.side_effect = set_error

        with pytest.raises(RuntimeError, match="Spotify auth error: access_denied"):
            _wait_for_callback()


def test_wait_for_callback_raises_on_missing_code() -> None:
    with patch("sync.auth.http.server.HTTPServer") as mock_server_cls:
        mock_server = MagicMock()
        mock_server_cls.return_value = mock_server

        with pytest.raises(RuntimeError, match="No code/state received"):
            _wait_for_callback()


def test_run_auth_flow_raises_on_state_mismatch(config: Config) -> None:
    with (
        patch("sync.auth._pkce_pair", return_value=("verifier", "challenge")),
        patch("sync.auth.secrets.token_urlsafe", return_value="expected_state"),
        patch("sync.auth.webbrowser.get"),
        patch("sync.auth._wait_for_callback", return_value=("code_xyz", "wrong_state")),
        pytest.raises(RuntimeError, match="State mismatch"),
    ):
        run_auth_flow(config)


def test_run_auth_flow_saves_tokens(
    config: Config, tokens_file: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://accounts.spotify.com/api/token",
        json={"access_token": "flow_access", "refresh_token": "flow_refresh", "expires_in": 3600},
    )

    with (
        patch("sync.auth._pkce_pair", return_value=("verifier", "challenge")),
        patch("sync.auth.secrets.token_urlsafe", return_value="good_state"),
        patch("sync.auth.webbrowser.get"),
        patch("sync.auth._wait_for_callback", return_value=("auth_code", "good_state")),
    ):
        run_auth_flow(config)

    saved = json.loads(tokens_file.read_text())
    assert saved["access_token"] == "flow_access"
    assert saved["refresh_token"] == "flow_refresh"
    assert "expires_at" in saved
