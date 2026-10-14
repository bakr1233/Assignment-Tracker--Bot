"""The one shape every source has to produce."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from .util import clean, days_until, fmt_due_display, fmt_due_on, subject_code

#: Statuses that mean there is nothing left to do.
_DONE_WORDS = re.compile(r"\b(submitted|graded|complete|completed|done|late)\b", re.I)
#: Gradescope reports a finished assignment as a bare score, "46.5 / 50.0".
_SCORE = re.compile(r"^\s*[\d.]+\s*/\s*[\d.]+\s*$")

#: Sorts after every real date, for the undated items when they are shown.
NO_DUE_SORT = "9999-12-31"


@dataclass
class Assignment:
    source: str
    course: str
    title: str
    due: Optional[datetime] = None
    status: str = ""
    points: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        self.course = clean(self.course)
        self.title = clean(self.title)
        self.status = clean(self.status)
        self.points = clean(self.points)

    @property
    def key(self) -> str:
        """Stable id so re-runs update a row instead of adding a new one.

        Deliberately excludes the due date and status - a professor moving a
        deadline should edit the existing row, not create a second one.
        """
        basis = "|".join(
            (
                self.source.lower(),
                _normalise(self.course),
                _normalise(self.title),
            )
        )
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    @property
    def is_done(self) -> bool:
        """Nothing left to hand in. Used to keep finished past work out of the
        sheet, while past work that was never submitted stays visible."""
        return bool(_DONE_WORDS.search(self.status) or _SCORE.match(self.status))

    def days_left(self, now: Optional[datetime] = None) -> Optional[int]:
        """Whole days from today to the due date. 0 is today, negative is late."""
        return days_until(self.due, now)

    def to_payload(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        remaining = self.days_left(now)
        return {
            "key": self.key,
            "source": self.source,
            "course": self.course,
            "title": self.title,
            # A real date would be re-rendered in the spreadsheet's own time
            # zone, which is not necessarily the school's. Sending the text we
            # want shown removes that whole class of off-by-an-hour confusion,
            # and `sort` keeps the column orderable.
            "due": fmt_due_display(self.due),
            "sort": self.due.isoformat() if self.due else NO_DUE_SORT,
            "days_left": "" if remaining is None else remaining,
            # For the tracker view, which wants a short code and a long date.
            "subject": subject_code(self.course),
            "due_on": fmt_due_on(self.due),
            "done": self.is_done,
            "status": self.status,
            "points": self.points,
            "url": self.url,
        }


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, so trivial title edits don't orphan a row."""
    return re.sub(r"[^a-z0-9]+", " ", clean(text).lower()).strip()
