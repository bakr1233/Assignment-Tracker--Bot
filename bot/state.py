"""Remembers what was seen last run, so the console can say what is new, and
which problems have already been reported."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Tuple

from .models import Assignment
from .paths import STATE
from .util import log, truncate

SEEN = STATE / "seen.json"
ALERTS = STATE / "alerts.json"


def load() -> Dict[str, Dict[str, str]]:
    if not SEEN.exists():
        return {}
    try:
        return json.loads(SEEN.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log("state").debug("Ignoring unreadable state file: %s", exc)
        return {}


def save(assignments: List[Assignment]) -> None:
    STATE.mkdir(exist_ok=True)
    snapshot = {a.key: {"due": a.to_payload()["due"], "status": a.status} for a in assignments}
    SEEN.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")


def should_alert(source: str, reason: str) -> bool:
    """True the first time a source fails this way, False while it keeps
    failing the same way.

    Pearson needs a CAPTCHA solved roughly daily. Notifying every single time
    would train you to swipe the notification away, and then a real failure
    somewhere else would go unread too.
    """
    reason = truncate(reason, 160)
    alerts = _load_alerts()
    previous = alerts.get(source)

    if previous and previous.get("reason") == reason:
        log("state").debug("Already reported %s: %s", source, reason)
        return False

    alerts[source] = {
        "reason": reason,
        "since": datetime.now().isoformat(timespec="seconds"),
    }
    _save_alerts(alerts)
    return True


def clear_alert(source: str) -> None:
    """Called when a source works again, so its next failure is reported."""
    alerts = _load_alerts()
    if alerts.pop(source, None) is not None:
        _save_alerts(alerts)


def _load_alerts() -> Dict[str, Dict[str, str]]:
    if not ALERTS.exists():
        return {}
    try:
        return json.loads(ALERTS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_alerts(alerts: Dict[str, Dict[str, str]]) -> None:
    STATE.mkdir(exist_ok=True)
    ALERTS.write_text(json.dumps(alerts, indent=2) + "\n", encoding="utf-8")


def diff(
    assignments: List[Assignment], previous: Dict[str, Dict[str, str]]
) -> Tuple[List[Assignment], List[Assignment]]:
    """Split into (brand new, changed since last run)."""
    new: List[Assignment] = []
    changed: List[Assignment] = []
    for a in assignments:
        before = previous.get(a.key)
        if before is None:
            new.append(a)
        elif before.get("due") != a.to_payload()["due"] or before.get("status") != a.status:
            changed.append(a)
    return new, changed
