"""Reads the key=value files in credentials/."""

from __future__ import annotations

import os
import stat
from typing import Dict

from .paths import CREDENTIALS
from .util import log


def read(name: str) -> Dict[str, str]:
    """Parse credentials/<name>.txt into a dict. Missing file -> empty dict."""
    path = CREDENTIALS / f"{name}.txt"
    if not path.exists():
        return {}

    _warn_if_world_readable(path)

    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        # Be forgiving about pasted quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip().lower()] = value

    return {k: v for k, v in values.items() if v}


def has(values: Dict[str, str], *keys: str) -> bool:
    return all(values.get(k) for k in keys)


def _warn_if_world_readable(path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        try:
            os.chmod(path, 0o600)
            log("creds").debug("Tightened permissions on %s to owner-only.", path.name)
        except OSError:
            log("creds").warning(
                "%s is readable by other accounts on this Mac. Run: chmod 600 %s",
                path.name,
                path,
            )
