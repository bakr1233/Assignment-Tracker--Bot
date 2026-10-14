"""Pushes notifications to your phone through ntfy.

ntfy needs no account: the bot POSTs to a topic, and the ntfy app on your
phone subscribes to the same topic. The topic name IS the credential, which is
why config.json holds a long random one - a short topic could be guessed, and
anyone who guessed it would see your assignment names.
"""

from __future__ import annotations

from typing import Optional

import requests

from .config import Config
from .util import log

TIMEOUT = 15


def configured(cfg: Config) -> bool:
    return cfg.push_enabled


def send(
    cfg: Config,
    title: str,
    message: str,
    priority: str = "default",
    tags: Optional[str] = None,
) -> bool:
    """Best effort: a phone notification must never fail a run."""
    if not configured(cfg):
        return False

    url = f"{cfg.push_server.rstrip('/')}/{cfg.push_topic}"
    headers = {"Title": _ascii(title), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if cfg.sheet_link:
        # Tapping the notification opens the tracker.
        headers["Click"] = cfg.sheet_link

    try:
        response = requests.post(
            url, data=message.encode("utf-8"), headers=headers, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        log("push").warning("Could not reach %s: %s", cfg.push_server, exc)
        return False

    if not response.ok:
        log("push").warning("ntfy answered HTTP %s: %s", response.status_code, response.text[:200])
        return False

    log("push").debug("Pushed to %s", cfg.push_topic)
    return True


def _ascii(text: str) -> str:
    """ntfy sends headers as latin-1, so a smart quote or an en dash in a
    title would otherwise be rejected outright."""
    return text.encode("ascii", "replace").decode("ascii")
