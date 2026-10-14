"""macOS notifications, used when a scheduled run needs a human."""

from __future__ import annotations

import shutil
import subprocess

from .util import log


def send(title: str, message: str, cfg=None, priority: str = "default", tags=None) -> None:
    """Tell the person. Desktop always, phone too when ntfy is configured."""
    _desktop(title, message)

    if cfg is not None:
        from . import push

        push.send(cfg, title, message, priority=priority, tags=tags)


def _desktop(title: str, message: str) -> None:
    osascript = shutil.which("osascript")
    if not osascript:
        return
    script = (
        f'display notification {_quote(message)} '
        f'with title {_quote(title)} sound name "Submarine"'
    )
    try:
        subprocess.run([osascript, "-e", script], timeout=10, check=False)
    except (subprocess.SubprocessError, OSError) as exc:
        log("notify").debug("Notification failed: %s", exc)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
