from __future__ import annotations

import logging

import httpx

from sync.config import NotificationsConfig

log = logging.getLogger(__name__)

_PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def push(config: NotificationsConfig, message: str, title: str = "Spotify Sync") -> None:
    if not config.pushover_token or not config.pushover_user:
        log.debug("Pushover credentials not configured — skipping notification")
        return

    try:
        response = httpx.post(
            _PUSHOVER_URL,
            data={
                "token": config.pushover_token,
                "user": config.pushover_user,
                "title": title,
                "message": message,
            },
        )
        if response.status_code != 200:
            errors = response.json().get("errors", [response.text])
            log.warning("Pushover notification failed: %s", errors)
    except Exception as exc:
        log.warning("Pushover notification error: %s", exc)
