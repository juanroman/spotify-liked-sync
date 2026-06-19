from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import urllib.parse
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from sync.config import Config

TOKEN_EXPIRY_DAYS = 180  # Spotify's 6-month refresh token lifetime


class RefreshTokenExpiredError(Exception):
    """Spotify rejected the refresh token with invalid_grant.

    User must re-run `python -m sync auth` on a machine with a browser.
    """


SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-library-read playlist-read-private playlist-modify-public playlist-modify-private"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _build_auth_url(client_id: str, code_challenge: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": state,
        "scope": SCOPES,
    }
    return f"{SPOTIFY_AUTH_URL}?{urllib.parse.urlencode(params)}"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        codes = qs.get("code")
        _CallbackHandler.code = codes[0] if codes else None
        states = qs.get("state")
        _CallbackHandler.state = states[0] if states else None
        errors = qs.get("error")
        _CallbackHandler.error = errors[0] if errors else None
        self.send_response(200)
        self.end_headers()
        msg = b"<html><body><h2>Auth complete. You can close this tab.</h2></body></html>"
        self.wfile.write(msg)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # BaseHTTPRequestHandler prints every request to stdout by default; suppress it.
        pass


def _wait_for_callback() -> tuple[str, str]:
    server = http.server.HTTPServer(("127.0.0.1", 8888), _CallbackHandler)
    server.handle_request()
    if _CallbackHandler.error:
        raise RuntimeError(f"Spotify auth error: {_CallbackHandler.error}")
    if not _CallbackHandler.code or not _CallbackHandler.state:
        raise RuntimeError("No code/state received from Spotify callback")
    return _CallbackHandler.code, _CallbackHandler.state


def run_auth_flow(config: Config) -> None:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = _build_auth_url(config.spotify.client_id, challenge, state)

    print(f"Opening browser for Spotify authorization...\n{auth_url}")
    # Use Chrome on macOS to avoid Safari's HTTPS-Only mode blocking the localhost callback
    try:
        webbrowser.get("chrome").open(auth_url)
    except webbrowser.Error:
        webbrowser.open(auth_url)

    code, returned_state = _wait_for_callback()

    if returned_state != state:
        raise RuntimeError("State mismatch — possible CSRF attack")

    resp = httpx.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": config.spotify.client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    token_data = resp.json()

    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(seconds=token_data["expires_in"])
    tokens = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "expires_at": expires_at.isoformat(),
        "authorized_at": now.isoformat(),
    }

    tokens_path = _tokens_path(config)
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    tokens_path.write_text(json.dumps(tokens, indent=2))
    print(f"Tokens saved to {tokens_path}")


def get_valid_token(config: Config, http_client: httpx.Client | None = None) -> str:
    tokens_path = _tokens_path(config)
    if not tokens_path.exists():
        raise FileNotFoundError(
            f"No tokens found at {tokens_path}. Run `python -m sync auth` first."
        )

    tokens = json.loads(tokens_path.read_text())
    expires_at = datetime.fromisoformat(tokens["expires_at"])
    # Refresh 60s early to avoid a race where the token expires between this check and the API call.
    soon = datetime.now(tz=UTC) + timedelta(seconds=60)

    if expires_at <= soon:
        tokens = _refresh_tokens(config, tokens["refresh_token"], http_client)
        tokens_path.write_text(json.dumps(tokens, indent=2))

    return str(tokens["access_token"])


def _refresh_tokens(
    config: Config, refresh_token: str, http_client: httpx.Client | None = None
) -> dict[str, str]:
    client = http_client or httpx.Client()
    resp = client.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": config.spotify.client_id,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code == 400:
        body = resp.json()
        if body.get("error") == "invalid_grant":
            raise RefreshTokenExpiredError(
                "Spotify refresh token has expired. "
                "Run `python -m sync auth` on your Mac to re-authenticate."
            )
    resp.raise_for_status()
    data = resp.json()
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=data["expires_in"])
    return {
        "access_token": data["access_token"],
        # Spotify may omit a new refresh token (RFC 6749 §6); keep the existing one when absent.
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_at": expires_at.isoformat(),
    }


def days_until_token_expiry(config: Config) -> int | None:
    """Returns days remaining before the refresh token expires, or None if unknown."""
    tokens_path = _tokens_path(config)
    if not tokens_path.exists():
        return None
    tokens = json.loads(tokens_path.read_text())
    authorized_at_str = tokens.get("authorized_at")
    if not authorized_at_str:
        return None
    authorized_at = datetime.fromisoformat(authorized_at_str)
    expiry = authorized_at + timedelta(days=TOKEN_EXPIRY_DAYS)
    remaining = (expiry - datetime.now(tz=UTC)).days
    return max(remaining, 0)


def _tokens_path(config: Config) -> Path:
    return config.state_dir / "tokens.json"
