# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --extra dev          # install all dependencies
uv run python -m sync auth   # OAuth flow (requires browser — run on Mac, not Pi)
uv run python -m sync run    # execute a sync

uv run pytest                           # all tests (80% coverage enforced)
uv run pytest tests/test_sync.py        # single test file
uv run pytest -k test_no_diff_skips_write  # single test

uv run ruff check .          # lint
uv run black --check .       # format check
uv run mypy src              # type check (strict)
uv run pip-audit             # dependency vulnerability scan
```

CI runs all of the above on every push to main and weekly (canary against live Spotify API drift).

## Architecture

The CLI entry point is `src/sync/__main__.py`, which parses `auth` or `run` commands and wires up dependencies.

**Data flow for `sync run`:**
1. `config.py` — loads `config.toml` + env vars into typed dataclasses (`Config`, `SpotifyConfig`, `NotificationsConfig`)
2. `auth.py` — `get_valid_token()` reads `tokens.json`, refreshes via Spotify token endpoint if expiring within 60s, persists new token. `run_auth_flow()` is PKCE OAuth — requires browser, must run on a machine with a display
3. `client.py` — `SpotifyClient` wraps Spotify API with auto-retry (tenacity), 401 token refresh, and 429 rate-limit detection. Playlist writes use PUT for the first 100 URIs then POST for subsequent batches (Spotify API cap)
4. `sync.py` — `run_sync()` compares liked URIs to playlist URIs. Skips write if identical (list equality, not set — order matters). Writes via WAL: snapshots current state to `pending_write.json` before writing; restores on failure; removes WAL on success
5. `notify.py` — `push()` POSTs to Pushover. Called from `sync.py` on: sync with changes, fatal API errors (400/403/404), and consecutive failure threshold breach. Silently skips if credentials are not configured

**State files** (all under `~/.local/share/spotify-sync/`):
- `tokens.json` — OAuth tokens, never commit
- `state.json` — `consecutive_failures` counter + `last_run` timestamp
- `pending_write.json` — WAL, present only during a write or after a crash
- `sync.log` — rotating log (5 MB × 3 backups)

**Key invariant:** Rate limit errors do not increment `consecutive_failures` — they preserve the existing count. Only genuine API errors and exceptions increment it.

## Production deployment

Runs headless on a Raspberry Pi 5 via systemd timer (every 15 minutes). See `docs/DEPLOY.md` for full setup. OAuth must be run on a Mac (browser required) and tokens copied to the Pi via `scp`.
