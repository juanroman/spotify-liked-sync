from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from sync.auth import get_valid_token, run_auth_flow
from sync.client import SpotifyClient
from sync.config import load_config
from sync.sync import run_sync


def _setup_logging(level: str, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Both handlers are always active: file captures output when running unattended (cron/launchd)
    # where stderr is discarded; stderr gives immediate feedback during interactive runs.
    # 5 MB / 3 backups keeps disk use predictable on space-constrained hosts (e.g. Raspberry Pi).
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("auth", "run"):
        print("Usage: python -m sync <auth|run> [--config path/to/config.toml]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    config_path: Path | None = None
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        config_path = Path(sys.argv[idx + 1])

    config = load_config(config_path)
    _setup_logging(config.logging.level, config.log_file)

    log = logging.getLogger(__name__)

    if command == "auth":
        log.info("Starting OAuth authorization flow")
        run_auth_flow(config)
        log.info("Authorization complete")

    elif command == "run":
        log.info("Starting sync run")
        with SpotifyClient(config, lambda: get_valid_token(config)) as client:
            run_sync(config, client)
        log.info("Sync run complete")


if __name__ == "__main__":
    main()
