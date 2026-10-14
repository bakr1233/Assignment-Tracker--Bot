"""Logging, date parsing, and small shared helpers."""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from .paths import LOGS

LOG_NAME = "bot"


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Console output for the human, plus a rolling file log for debugging."""
    LOGS.mkdir(exist_ok=True)
    logger = logging.getLogger(LOG_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    logfile = logging.FileHandler(LOGS / f"{datetime.now():%Y-%m-%d}.log", encoding="utf-8")
    logfile.setLevel(logging.DEBUG)
    logfile.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"))
    logger.addHandler(logfile)

    return logger


def log(name: str = "") -> logging.Logger:
    return logging.getLogger(LOG_NAME + (f".{name}" if name else ""))


def parse_due(raw, tz: ZoneInfo) -> Optional[datetime]:
    """Turn whatever a site gave us into an aware datetime, or None.

    Handles ISO strings, epoch milliseconds (Blackboard does this in places),
    and the free-text dates that show up in scraped HTML.
    """
    if raw is None or raw == "":
        return None

    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, (int, float)):
        # Blackboard hands back epoch milliseconds; anything past year 3000 in
        # seconds is really milliseconds.
        seconds = raw / 1000 if raw > 10_000_000_000 else raw
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    else:
        text = str(raw).strip()
        if not text:
            return None
        # "Due: Mar 4 at 11:59pm" and friends - drop the label, normalise "at".
        text = re.sub(r"^(due|closes|available until)\s*:?\s*", "", text, flags=re.I)
        text = re.sub(r"\s+at\s+", " ", text, flags=re.I)
        try:
            dt = date_parser.parse(text, fuzzy=True)
        except (ValueError, OverflowError, TypeError):
            return None

    if dt.tzinfo is None:
        # A naive timestamp from a course site is in the school's local time.
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def fmt_due(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_due_display(dt: Optional[datetime]) -> str:
    """"Sun 18 Oct, 11:59 PM" - what actually goes in the sheet.

    Built by hand rather than with %-I, which is not portable.
    """
    if dt is None:
        return ""
    hour = dt.hour % 12 or 12
    meridiem = "AM" if dt.hour < 12 else "PM"
    return f"{dt:%a %d %b}, {hour}:{dt:%M} {meridiem}"


#: "CS277 Lab (48669)" -> CS 277, "Math 218 Applied Linear Algebra" -> MATH 218
_SUBJECT = re.compile(r"\b([A-Za-z]{2,6})\s*-?\s*(\d{3})\b")


def subject_code(course: Optional[str]) -> str:
    """The short course code, for a narrow Subject column."""
    text = clean(course or "")
    found = _SUBJECT.search(text)
    if found:
        return f"{found.group(1).upper()} {found.group(2)}"
    return truncate(text, 14)


def fmt_due_on(dt: Optional[datetime]) -> str:
    """"Sunday, 10/18" - the long-day format the tracker uses."""
    if dt is None:
        return ""
    return f"{dt:%A}, {dt.month}/{dt.day}"


def term_label(now: datetime) -> str:
    if now.month >= 8:
        return f"Fall {now.year}"
    if now.month <= 5:
        return f"Spring {now.year}"
    return f"Summer {now.year}"


def days_until(dt: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    """Whole calendar days from today to dt. 0 means today, -1 means yesterday.

    Counting calendar days rather than 24-hour blocks is what makes "due today"
    read as 0 for something due at 11:59 tonight.
    """
    if dt is None:
        return None
    if now is None:
        now = datetime.now(dt.tzinfo)
    return (dt.date() - now.astimezone(dt.tzinfo).date()).days


def fmt_points(value) -> str:
    """Tidy a point total: 20.0 -> "20", 10 -> "10", 2.5 -> "2.5", None -> ""."""
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def clean(text: Optional[str]) -> str:
    """Collapse whitespace in scraped text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def truncate(text: str, width: int) -> str:
    text = clean(text)
    return text if len(text) <= width else text[: width - 1] + "…"
