"""Assignments captured once and replayed from disk.

Some sources are not worth asking every hour. A semester's MyLab homework is
decided before the term starts and barely moves, while Pearson's sign-in
expires daily and can demand a CAPTCHA - so checking it hourly buys nothing
and costs an interruption.

A snapshot captures that source once. Later runs read the file, so the
assignments still reach the sheet and their countdowns stay accurate, without
anyone signing in. Refresh it when the syllabus changes:

    python -m bot.main --snapshot pearson
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional, Tuple

from .models import Assignment
from .paths import STATE
from .util import log, parse_due

FOLDER = STATE / "snapshots"


def path_for(source: str) -> "object":
    return FOLDER / f"{source}.json"


def save(source: str, assignments: List[Assignment], tz) -> int:
    FOLDER.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source,
        "captured_at": datetime.now(tz).isoformat(timespec="seconds"),
        "assignments": [
            {
                "source": a.source,
                "course": a.course,
                "title": a.title,
                "due": a.due.isoformat() if a.due else "",
                "status": a.status,
                "points": a.points,
                "url": a.url,
            }
            for a in assignments
        ],
    }
    path_for(source).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(assignments)


def load(source: str, tz) -> Tuple[List[Assignment], Optional[datetime]]:
    """(assignments, when it was captured). Empty list when there is none."""
    path = path_for(source)
    if not path.exists():
        return [], None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log("snapshot").warning("Could not read %s: %s", path.name, exc)
        return [], None

    captured = parse_due(payload.get("captured_at"), tz)
    out: List[Assignment] = []
    for row in payload.get("assignments") or []:
        out.append(
            Assignment(
                source=row.get("source") or source.title(),
                course=row.get("course") or "",
                title=row.get("title") or "",
                # Re-parsed each run, so "days left" counts down from today.
                due=parse_due(row.get("due"), tz),
                status=row.get("status") or "",
                points=row.get("points") or "",
                url=row.get("url") or "",
            )
        )
    return out, captured


def age_days(captured: Optional[datetime], tz) -> Optional[int]:
    if captured is None:
        return None
    return (datetime.now(tz) - captured).days
