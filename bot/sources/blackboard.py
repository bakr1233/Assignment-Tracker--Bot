"""Blackboard Ultra, behind a university single sign-on.

Logging in needs a real browser once: NetID, password, then you approve the
Duo push. The session cookies live in browser_profiles/blackboard, so later
headless runs reuse them and stay quiet. When they do expire, the run says so
and asks you to launch run_visible.command again.

Course data comes from Blackboard's own internal JSON endpoints (the ones its
web UI calls), using those session cookies. Two strategies are tried in order:
the calendar, which gives every due date in one request, then the per-course
gradebook columns.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .. import browser, creds
from ..models import Assignment
from ..util import clean, fmt_points, parse_due
from .base import Source

ASSIGNMENT_TYPES = {"GradebookColumn", "Assignment", "Test", "Survey", ""}

#: Microsoft Entra first - many universities put it in front of Blackboard -
#: then Shibboleth, then Blackboard's own form.
USERNAME_SELECTORS = (
    "input[name='loginfmt']",
    "input#i0116",
    "input#j_username",
    "input[name='j_username']",
    "input#username",
    "input[name='username']",
    "input#user_id",
    "input[name='user_id']",
    "input[type='email']",
)
PASSWORD_SELECTORS = (
    "input[name='passwd']",
    "input#i0118",
    "input#j_password",
    "input[name='j_password']",
    "input#password",
    "input[name='password']",
    "input[type='password']",
)

#: The calendar prefixes every course with its internal id, e.g.
#: "2026.fall.cs.277.48669: CS277 Lab (48669) Thurs 1pm FA26 CC". Dropping it
#: also makes these names match the ones the gradebook returns, so the same
#: assignment from both strategies collapses into one row.
_COURSE_PREFIX = re.compile(r"^[0-9a-z._\-]+:\s*", re.I)


def _course_name(raw: Optional[str]) -> str:
    name = clean(raw or "")
    if not name:
        return "Blackboard"
    return _COURSE_PREFIX.sub("", name) or name


class Blackboard(Source):
    name = "blackboard"
    label = "Blackboard"
    interactive = True
    # Shared with Canvas. Where a school puts both behind the same sign-on,
    # one approval gets the bot into both.
    profile = "sso"

    def __init__(self, cfg, visible) -> None:
        super().__init__(cfg, visible)
        self.creds = creds.read("blackboard")
        self.base = (self.options().get("base_url") or "").rstrip("/")

    def ready(self) -> Tuple[bool, str]:
        if not self.base:
            return False, (
                "set sources.blackboard.base_url in config.json "
                "(e.g. https://yourschool.blackboard.com)"
            )
        if creds.has(self.creds, "username", "password"):
            return True, ""
        return False, "fill in username= and password= in credentials/blackboard.txt"

    def fetch(self) -> List[Assignment]:
        with browser.context(self.profile_name, self.cfg, self.visible) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            self._login(ctx, page)

            # Both, not one or the other: the calendar reaches courses whose
            # gradebook a student may not list, and the gradebook carries
            # items the calendar leaves out. Duplicates collapse on the way
            # into the sheet.
            items = self._via_calendar(ctx)
            self.log.info("   %d item(s) from the calendar.", len(items))

            from_gradebook = self._via_gradebook(ctx)
            self.log.info("   %d item(s) from course gradebooks.", len(from_gradebook))
            items.extend(from_gradebook)

            if not items:
                browser.dump(page, "blackboard-empty")
            return items

    # -- login -----------------------------------------------------------

    def _login(self, ctx, page) -> None:
        if self._signed_in(ctx):
            self.log.info("   Still signed in from last time.")
            return

        # Blackboard's own session is a session cookie, so it is gone the
        # moment the browser closes - every run has to sign in again. The
        # single sign-on session behind it *does* persist, so this normally
        # completes without anyone touching anything, which is exactly what
        # makes the unattended 8 AM run possible. Never give up before trying.
        page.goto(f"{self.base}/", wait_until="domcontentloaded")
        if self._signed_in(ctx):
            self.log.info("   Signed in silently through the saved single sign-on.")
            return

        # Blackboard shows its own username box, but at most schools real
        # logins go through a provider link next to it. Take
        # that hop first; the local box is only a fallback for schools that
        # authenticate against Blackboard directly.
        browser.follow_sso_link(page, self.log, self.options().get("sso_link"))
        if self._signed_in(ctx):
            self.log.info("   Signed in silently through the saved single sign-on.")
            return

        filled = browser.fill_credentials(
            page,
            self.creds["username"],
            self.creds["password"],
            USERNAME_SELECTORS,
            PASSWORD_SELECTORS,
            self.log,
            signed_in=lambda: self._signed_in(ctx),
        )
        if not filled:
            browser.dump(page, "blackboard-login")
            raise browser.LoginRequired(
                "Could not find the NetID box on the login page. "
                "See debug/blackboard-login.png and send it over."
            )

        # Nobody can tap a phone during a scheduled run, so headless waits only
        # long enough for a silent redirect to land.
        seconds = self.cfg.duo_wait_seconds if self.visible else 30
        ok = browser.wait_for_duo(
            page,
            lambda: self._signed_in(ctx),
            seconds,
            self.log,
            announce=self.visible,
        )
        if not ok:
            browser.dump(page, "blackboard-duo-timeout")
            if not self.visible:
                raise browser.LoginRequired(
                    "The saved single sign-on has expired, and approving Duo needs "
                    "your phone. Run run_visible.command once to sign in again."
                )
            raise browser.LoginRequired(
                "Duo was not approved in time. Run run_visible.command again and tap "
                "Approve when the push arrives."
            )

    def _signed_in(self, ctx) -> bool:
        data = self._json(ctx, f"{self.base}/learn/api/v1/users/me")
        return bool(data and data.get("id"))

    # -- strategy 1: the calendar ----------------------------------------

    def _via_calendar(self, ctx) -> List[Assignment]:
        """Every due date in one request.

        Note the `public` in the path. The non-public /learn/api/v1/calendars/
        returns the list of *calendars* - five objects describing colours and
        permissions - not the items in them, which is why this quietly found
        nothing for a while.
        """
        start, end = self.window
        url = (
            f"{self.base}/learn/api/public/v1/calendars/items"
            f"?since={start.isoformat()}&until={end.isoformat()}"
        )
        data = self._json(ctx, url)
        if data is None:
            return []

        rows = data.get("results") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []

        out: List[Assignment] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            # This endpoint calls it `type`; older ones say `itemSourceType`.
            kind = row.get("type") or row.get("itemSourceType") or ""
            if kind not in ASSIGNMENT_TYPES:
                continue

            due = parse_due(row.get("end") or row.get("dueDate") or row.get("start"), self.cfg.tz)
            if due is None:
                continue

            out.append(
                Assignment(
                    source=self.label,
                    course=_course_name(row.get("calendarName")),
                    title=clean(row.get("title") or "(untitled)"),
                    due=due,
                    status="",
                    url=self._course_url(row.get("calendarId") or ""),
                )
            )
        return out

    # -- strategy 2: gradebook columns -----------------------------------

    def _via_gradebook(self, ctx) -> List[Assignment]:
        me = self._json(ctx, f"{self.base}/learn/api/v1/users/me") or {}
        user_id = me.get("id")
        if not user_id:
            return []

        memberships = (
            self._json(
                ctx,
                f"{self.base}/learn/api/v1/users/{user_id}/memberships"
                "?expand=course&includeCount=true&limit=200",
            )
            or {}
        )
        rows = memberships.get("results") or []
        courses = [(row or {}).get("course") or {} for row in rows]
        courses = [c for c in courses if c.get("id")]
        current = [c for c in courses if self._is_current(c)]
        self.log.info(
            "   %d course membership(s), %d still running.", len(courses), len(current)
        )

        out: List[Assignment] = []
        refused = 0

        for course in current:
            course_id = course["id"]
            course_name = clean(
                course.get("displayName") or course.get("name") or course.get("courseId") or "Course"
            )

            columns = self._json(
                ctx,
                f"{self.base}/learn/api/v1/courses/{course_id}/gradebook/columns?limit=200",
            )
            if columns is None:
                # Plenty of courses simply do not let a student list the
                # gradebook; the calendar covers those.
                refused += 1
                continue

            for column in columns.get("results") or []:
                column = column or {}
                grading = column.get("grading") or {}
                # This Blackboard puts it at the top level as `dueDate`;
                # others nest it under grading.due.
                due = parse_due(column.get("dueDate") or grading.get("due"), self.cfg.tz)
                if due is None:
                    continue

                score = column.get("score") or {}
                points = (
                    column.get("points")
                    or column.get("possible")
                    or (score.get("possible") if isinstance(score, dict) else None)
                )
                out.append(
                    Assignment(
                        source=self.label,
                        course=course_name,
                        # ...and names it `columnName`, not `name`.
                        title=clean(column.get("columnName") or column.get("name") or "(untitled)"),
                        due=due,
                        status="",
                        points=fmt_points(points),
                        url=self._course_url(course_id),
                    )
                )

        if refused:
            self.log.debug("%d course(s) would not show their gradebook.", refused)
        return out

    def _is_current(self, course: Dict[str, Any]) -> bool:
        """Skip courses that have already finished - most of the 34 are old."""
        end = course.get("endDate")
        if not end:
            return True
        parsed = parse_due(end, self.cfg.tz)
        return parsed is None or parsed >= datetime.now(self.cfg.tz)

    # -- plumbing --------------------------------------------------------

    def _course_url(self, course_id: str) -> str:
        if not course_id:
            return f"{self.base}/ultra/course"
        return f"{self.base}/ultra/courses/{course_id}/outline"

    def _json(self, ctx, url: str) -> Optional[Dict[str, Any]]:
        """GET some JSON with the browser's cookies. None on any failure."""
        try:
            response = ctx.request.get(url, headers={"Accept": "application/json"})
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Request failed for %s: %s", url, exc)
            return None

        if not response.ok:
            self.log.debug("HTTP %s for %s", response.status, url)
            return None
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001 - an HTML login page, usually
            self.log.debug("Non-JSON answer from %s: %s", url, exc)
            return None
