"""Loads config.json, falling back to the shipped defaults."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict
from zoneinfo import ZoneInfo

from .paths import CONFIG, CONFIG_EXAMPLE

PLACEHOLDER_SECRET = "CHANGE_ME_TO_A_LONG_RANDOM_STRING"


@dataclass
class Config:
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def sheet_url(self) -> str:
        return (self.raw.get("sheet_url") or "").strip()

    @property
    def shared_secret(self) -> str:
        return (self.raw.get("shared_secret") or "").strip()

    @property
    def sheet_tab(self) -> str:
        # Deliberately not "Assignments": the bot must land on a tab of its
        # own rather than adopt one someone already built by hand.
        return self.raw.get("sheet_tab") or "Bot Assignments"

    @property
    def data_tab(self) -> str:
        """Hidden tab holding every assignment, including ones not yet shown."""
        return self.raw.get("data_tab") or "Bot Data"

    @property
    def adopt_existing_tab(self) -> bool:
        """Let the bot take over a tab it did not create. It copies the tab to
        a dated backup before touching it."""
        return bool(self.raw.get("adopt_existing_tab", False))

    @property
    def _push(self) -> Dict[str, Any]:
        return self.raw.get("push") or {}

    @property
    def push_enabled(self) -> bool:
        return bool(self._push.get("enabled")) and bool(self.push_topic)

    @property
    def push_server(self) -> str:
        return self._push.get("server") or "https://ntfy.sh"

    @property
    def push_topic(self) -> str:
        return (self._push.get("topic") or "").strip()

    @property
    def sheet_link(self) -> str:
        """Opened when you tap a phone notification."""
        return (self.raw.get("sheet_link") or "").strip()

    @property
    def snapshot_stale_days(self) -> int:
        """Warn when a stored snapshot is older than this."""
        return int(self.raw.get("snapshot_stale_days", 45))

    @property
    def notify_within_days(self) -> int:
        """A scheduled run pushes a desktop notification about anything due
        inside this many days. Nothing imminent means no notification."""
        return int(self.raw.get("notify_within_days", 2))

    @property
    def done_linger_minutes(self) -> int:
        """How long something stays on the tracker after it is marked Done.
        It only disappears on a run that happens after this has elapsed."""
        return int(self.raw.get("done_linger_minutes", 30))

    @property
    def overdue_grace_days(self) -> int:
        """How long a missed deadline stays on the tracker. 1 means something
        due yesterday is still listed, but anything older drops off - it stays
        in the data tab, it just stops cluttering the view."""
        return int(self.raw.get("overdue_grace_days", 1))

    @property
    def display_within_days(self) -> int:
        """Only assignments due within this many days appear on the tracker.
        The rest stay in the data tab until they come into range."""
        return int(self.raw.get("display_within_days", 7))

    @property
    def include_undated(self) -> bool:
        """Assignments with no due date at all - usually finished exams."""
        return bool(self.raw.get("filters", {}).get("include_undated", False))

    @property
    def include_completed_past(self) -> bool:
        """Past work that was already submitted or graded."""
        return bool(self.raw.get("filters", {}).get("include_completed_past", False))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.raw.get("timezone") or "America/Chicago")

    @property
    def lookahead_days(self) -> int:
        return int(self.raw.get("lookahead_days", 60))

    @property
    def lookback_days(self) -> int:
        return int(self.raw.get("lookback_days", 7))

    @property
    def duo_wait_seconds(self) -> int:
        return int(self.raw.get("browser", {}).get("duo_wait_seconds", 180))

    @property
    def slow_mo_ms(self) -> int:
        return int(self.raw.get("browser", {}).get("slow_mo_ms", 0))

    def source(self, name: str) -> Dict[str, Any]:
        return self.raw.get("sources", {}).get(name, {})

    def enabled(self, name: str) -> bool:
        return bool(self.source(name).get("enabled", True))

    @property
    def sheet_configured(self) -> bool:
        return self.sheet_url.endswith("/exec") and self.shared_secret not in (
            "",
            PLACEHOLDER_SECRET,
        )


def load() -> Config:
    path = CONFIG if CONFIG.exists() else CONFIG_EXAMPLE
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Config(raw=raw)


def save(cfg: Config) -> None:
    CONFIG.write_text(json.dumps(cfg.raw, indent=2) + "\n", encoding="utf-8")
