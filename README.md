# spotify-liked-sync

[![CI](https://github.com/juanroman/spotify-liked-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/juanroman/spotify-liked-sync/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Python CLI that mirrors your Spotify Liked Songs into a regular playlist — newest first, removes included. Because Liked Songs can't be synced to Apple Music or other services directly, this creates a proper playlist you can hand off to tools like SongShift.

## Features

- Mirrors Liked Songs → target playlist (newest first, exact order)
- Propagates removes — unlike songs disappear from the playlist
- Idempotent — skips the write if nothing changed
- Safe multi-batch write with WAL-based rollback on failure
- Auto-refreshes access tokens before expiry
- Consecutive failure tracking with configurable warning threshold

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A Spotify app with `http://127.0.0.1:8888/callback` as an allowed redirect URI

## Setup

**1. Create a Spotify app**

Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create an app, and add `http://127.0.0.1:8888/callback` as a redirect URI.

**2. Configure credentials**

Copy the example config and fill in your client ID and secret:

```bash
cp config.toml.example config.toml
```

Or set environment variables:

```bash
export SPOTIFY_CLIENT_ID=your_client_id
export SPOTIFY_CLIENT_SECRET=your_client_secret
```

**3. Install dependencies**

```bash
uv sync --extra dev
```

**4. Authenticate**

```bash
uv run python -m sync auth
```

This opens a browser for Spotify authorization and saves tokens to `~/.local/share/spotify-sync/tokens.json`.

## Usage

```bash
uv run python -m sync run
```

On the first run, the target playlist is created automatically and its ID is written back to `config.toml` so subsequent runs skip the lookup.

## Configuration

```toml
[spotify]
client_id     = ""              # or SPOTIFY_CLIENT_ID env var
client_secret = ""              # or SPOTIFY_CLIENT_SECRET env var
target_playlist_name = "Liked Playlist"
target_playlist_id   = ""       # auto-populated after first run

[notifications]
errors    = true
warnings  = true
adds      = true
consecutive_failures_threshold = 3
pushover_token = ""             # or PUSHOVER_TOKEN env var
pushover_user  = ""             # or PUSHOVER_USER env var

[logging]
level = "INFO"
file  = "~/.local/share/spotify-sync/sync.log"
```

## Running tests

```bash
uv run pytest
```

## Code quality

```bash
uv run ruff check .       # linting
uv run black --check .    # formatting
uv run mypy src           # type checking
uv run pip-audit          # dependency vulnerability scan
```

## License

MIT
