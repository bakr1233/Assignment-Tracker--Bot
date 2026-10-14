"""Gradescope.

Plain form login plus HTML parsing - no browser needed, so this source is
fast and works fine on the scheduled headless runs.
"""

from __future__ import annotations

from typing import Dict, List, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .. import creds
from ..models import Assignment
from ..util import clean, parse_due
from .base import Source

BASE = "https://www.gradescope.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
TIMEOUT = 45


class Gradescope(Source):
    name = "gradescope"
    label = "Gradescope"

    def __init__(self, cfg, visible) -> None:
        super().__init__(cfg, visible)
        self.creds = creds.read("gradescope")

    def ready(self) -> Tuple[bool, str]:
        if creds.has(self.creds, "email", "password"):
            return True, ""
        return False, "fill in email= and password= in credentials/gradescope.txt"

    def fetch(self) -> List[Assignment]:
        session = requests.Session()
        session.headers.update({"User-Agent": UA})

        self._login(session)

        courses = self._courses(session)
        self.log.info("   %d course(s).", len(courses))

        out: List[Assignment] = []
        for name, href in courses.items():
            try:
                out.extend(self._course_assignments(session, name, href))
            except Exception as exc:  # noqa: BLE001 - keep going for other courses
                self.log.warning("   Skipped %s: %s", name, exc)
        return out

    # -- login -----------------------------------------------------------

    def _login(self, session: requests.Session) -> None:
        page = session.get(f"{BASE}/login", timeout=TIMEOUT)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        token_field = soup.find("input", {"name": "authenticity_token"})
        if not token_field or not token_field.get("value"):
            raise RuntimeError("Gradescope's login form changed - no authenticity_token found.")

        response = session.post(
            f"{BASE}/login",
            data={
                "utf8": "✓",
                "authenticity_token": token_field["value"],
                "session[email]": self.creds["email"],
                "session[password]": self.creds["password"],
                "session[remember_me]": "0",
                "commit": "Log In",
                "session[remember_me_sso]": "0",
            },
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()

        if "/login" in response.url or "Log In" in response.text[:4000]:
            raise RuntimeError(
                "Gradescope rejected the email or password in credentials/gradescope.txt. "
                "(If you normally click 'School Credentials' to sign in, this source "
                "cannot be used - leave the file blank.)"
            )

    # -- scraping --------------------------------------------------------

    def _courses(self, session: requests.Session) -> Dict[str, str]:
        response = session.get(f"{BASE}/account", timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        want_all = str(self.options().get("terms", "current")).lower() == "all"
        courses: Dict[str, str] = {}

        # The student dashboard groups courses under term headings. The first
        # group is the current term, which is normally all anyone wants.
        lists = soup.select(".courseList--coursesForTerm") or [soup]
        for index, group in enumerate(lists):
            if index > 0 and not want_all:
                break
            for link in group.select("a.courseBox"):
                href = link.get("href")
                if not href:
                    continue
                short = clean(_text(link, ".courseBox--shortname"))
                full = clean(_text(link, ".courseBox--name"))
                courses[_course_label(short, full) or href] = urljoin(BASE, href)

        return courses

    def _course_assignments(
        self, session: requests.Session, course: str, url: str
    ) -> List[Assignment]:
        response = session.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        rows = soup.select("#assignments-student-table tbody tr")
        if not rows:
            rows = soup.select("table tbody tr")

        out: List[Assignment] = []
        for row in rows:
            head = row.find("th")
            if head is None:
                continue
            link = head.find("a")
            title = clean(link.get_text() if link else head.get_text())
            if not title:
                continue

            # Two of these means (due date, late due date); the first is the
            # real deadline.
            due_nodes = row.select(".submissionTimeChart--dueDate")
            due_raw = None
            if due_nodes:
                due_raw = due_nodes[0].get("datetime") or due_nodes[0].get_text()

            status = clean(
                _text(row, ".submissionStatus--score")
                or _text(row, ".submissionStatus--text")
                or _text(row, ".submissionStatus")
            )
            if not status:
                status = "Open" if link else "Not open for submission"

            out.append(
                Assignment(
                    source=self.label,
                    course=course,
                    title=title,
                    due=parse_due(due_raw, self.cfg.tz),
                    status=status,
                    url=urljoin(BASE, link["href"]) if link and link.get("href") else url,
                )
            )

        self.log.debug("   %s: %d assignment(s)", course, len(out))
        return out


def _text(node, selector: str) -> str:
    found = node.select_one(selector)
    return found.get_text() if found else ""


def _course_label(short: str, full: str) -> str:
    """One readable course name.

    Gradescope often repeats itself - short "Math 220 Fall 2026" alongside full
    "Math 220 Fall 2026 (Section ...)" - and joining the two gives the useless
    "Math 220 Fall 2026 - Math 220 Fall 2026 (...)". When one contains the
    other, keep the short one; it is the course code, which is what reads well
    in a narrow column.
    """
    short, full = clean(short), clean(full)
    if not short:
        return full
    if not full:
        return short
    if short.lower() in full.lower() or full.lower() in short.lower():
        return short
    return f"{short} - {full}"
