"""Pearson MyLab / Mastering. EXPERIMENTAL.

Unlike the other three, Pearson has no stable page structure to target: every
MyLab product and course template lays its assignment list out differently,
and much of it is drawn by JavaScript inside nested frames. So this source
does something deliberately crude - it opens the assignment page you point it
at and pulls out every row that looks like "a title next to a date".

Expect to correct it. Run `python -m bot.main --only pearson --visible
--debug-dump` and the page it saw lands in debug/ so the selectors can be
tightened to your actual course.
"""

from __future__ import annotations

import re
import time
from typing import List, Optional, Tuple

from .. import browser, creds
from ..models import Assignment
from ..util import clean, parse_due, truncate
from .base import Source

DATE_PATTERNS = [
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:,?\s+\d{1,2}:\d{2}\s*[APap]\.?[Mm]\.?)?"),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}"
        r"(?:,?\s+\d{4})?(?:,?\s+\d{1,2}:\d{2}\s*[APap]\.?[Mm]\.?)?"
    ),
]

USERNAME_SELECTORS = (
    "input#username",
    "input[name='username']",
    "input[name='userid']",
    "input[autocomplete='username']",
    "input[type='email']",
)
PASSWORD_SELECTORS = (
    "input#password",
    "input[name='password']",
    "input[autocomplete='current-password']",
    "input[type='password']",
)

ROW_SELECTORS = [
    "table tbody tr",
    "[role='row']",
    "[class*='assignment'][class*='row']",
    "li[class*='assignment']",
    "div[class*='assignment-item']",
    "[data-automation-id*='assignment']",
]


#: Page furniture the heuristic keeps mistaking for an assignment.
CHROME_WORDS = {
    "course home",
    "home",
    "calendar",
    "sign out",
    "log out",
    "help",
    "results",
    "gradebook",
    "today",
}


def _cell_texts(row) -> List[str]:
    """A row's cells as collapsed text. Collapsing matters: MyLab splits the
    due date across a <br>, so "09/06/26<br>11:59pm" has to come back as
    "09/06/26 11:59pm" for it to parse at all."""
    try:
        cells = row.query_selector_all("th, td")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for cell in cells:
        try:
            out.append(clean(cell.inner_text()))
        except Exception:  # noqa: BLE001
            out.append("")
    return out


def _column_with(header: List[str], word: str) -> Optional[int]:
    for index, text in enumerate(header):
        if word in text.lower():
            return index
    return None


def _score_text(cell: str) -> str:
    """"see score for Sec 1.2" is a link label, not a status."""
    if not cell or cell.lower().startswith("see score"):
        return ""
    return cell


def _looks_like_a_title(title: str) -> bool:
    """Reject the clock, the date stamp and the navigation.

    The first live run pulled in "10/13/26 5:08am" as an assignment, which is
    the page's own clock sitting next to a date the regex happily matched.
    """
    if len(title) < 4:
        return False
    # Needs real words, not just digits, slashes and a meridiem.
    if not re.search(r"[A-Za-z]{3,}", title):
        return False
    if title.strip().lower() in CHROME_WORDS:
        return False
    # A title that is nothing but a date or time is a timestamp, not work.
    stripped = re.sub(r"[\d/:\-,.]|\b[AaPp]\.?[Mm]\.?\b", "", title).strip()
    if len(stripped) < 4:
        return False
    return True


class Pearson(Source):
    name = "pearson"
    label = "Pearson"
    interactive = True
    profile = "pearson"

    def __init__(self, cfg, visible) -> None:
        super().__init__(cfg, visible)
        self.creds = creds.read("pearson")

    def ready(self) -> Tuple[bool, str]:
        if not self.creds.get("url"):
            return False, "no url= in credentials/pearson.txt (the assignment list page)"
        if not creds.has(self.creds, "username", "password"):
            return False, "fill in username= and password= in credentials/pearson.txt"
        return True, ""

    def fetch(self) -> List[Assignment]:
        self.log.warning("   Pearson support is experimental - check what it finds.")

        with browser.context("pearson", self.cfg, self.visible) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(self.creds["url"], wait_until="domcontentloaded")
            self._login_if_asked(page)

            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:  # noqa: BLE001 - these pages often never go idle
                pass

            # The table lives in a nested frame that is still being drawn long
            # after the outer page calls itself loaded. Without this wait the
            # extractor runs against an empty document and finds nothing.
            self._wait_for_rows(page)

            items = self._extract(page)
            if not items:
                self._explain_empty(page)
            return items

    def _explain_empty(self, page) -> None:
        """Say what is actually wrong instead of dumping a megabyte of HTML.

        MyLab draws the assignment list inside an LTI iframe, so a course home
        page genuinely contains no assignments - no tables, no dates, nothing
        to find. The usual cause is `url=` pointing at the course menu.
        """
        frames = [f for f in page.frames if f != page.main_frame]
        self.log.warning("   Found nothing on %s", clean(page.title()) or page.url)
        self.log.warning(
            "   That page has %d embedded frame(s). MyLab puts assignments inside one "
            "of them, so point url= in credentials/pearson.txt at the page that "
            "actually lists your assignments, not the course home.",
            len(frames),
        )
        for frame in frames[:5]:
            self.log.debug("      frame: %s", (frame.url or "")[:120])

        # The page is over a megabyte; only keep it when it was asked for.
        if self.visible or browser.FORCE_DUMP:
            browser.dump(page, "pearson-nothing-found")

    def _login_if_asked(self, page) -> None:
        # MyLab bounces to login.pearson.com when the session has lapsed. That
        # redirect lands after domcontentloaded, so checking for the form
        # straight away finds nothing and wrongly concludes we are signed in.
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:  # noqa: BLE001 - these pages rarely go fully idle
            pass

        on_signin = "signin" in (page.url or "").lower()
        user_field = browser.first_visible(page, *USERNAME_SELECTORS, timeout=4_000)

        if not on_signin and not user_field:
            self.log.info("   No sign-in page, the saved session still works.")
            return

        self.log.info("   Session has lapsed, signing in.")
        filled = browser.fill_credentials(
            page,
            self.creds["username"],
            self.creds["password"],
            USERNAME_SELECTORS,
            PASSWORD_SELECTORS,
            self.log,
            timeout=20_000,
        )
        if not filled:
            browser.dump(page, "pearson-login")
            raise browser.LoginRequired(
                "Could not fill the Pearson sign-in form. See debug/pearson-login.png."
            )

        # The button flips to "Signing in..." and the redirect lands a good
        # while later, so poll for it rather than checking once.
        self._wait_until_signed_in(page, 20)

        # Pearson puts a reCAPTCHA image challenge in front of the sign-in.
        # There is no honest way to answer that without a person, so a visible
        # run hands it over and an unattended run stops and says so.
        if self._captcha_showing(page):
            if not self.visible:
                browser.dump(page, "pearson-captcha")
                raise browser.LoginRequired(
                    "Pearson is showing a CAPTCHA, which an unattended run cannot "
                    "answer. Run run_visible.command once and solve it yourself - the "
                    "session is then reused until Pearson expires it."
                )
            self.log.info("")
            self.log.info("   >>> Solve the Pearson CAPTCHA in the browser window. <<<")
            self.log.info("   Waiting up to %d seconds...", self.cfg.duo_wait_seconds)
            self._wait_until_signed_in(page, self.cfg.duo_wait_seconds)

        if "signin" in (page.url or "").lower():
            browser.dump(page, "pearson-login-failed")
            complaint = self._page_complaint(page)
            raise browser.LoginRequired(
                "Pearson did not accept the sign-in"
                + (f": {complaint}" if complaint else "")
                + ". See debug/pearson-login-failed.png."
            )

        # Land back on the assignment list rather than wherever login sent us.
        try:
            page.goto(self.creds["url"], wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=25_000)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Could not return to the assignment page: %s", exc)

    @staticmethod
    def _captcha_showing(page) -> bool:
        """True when the image challenge is actually on screen. The invisible
        reCAPTCHA frame is always present, so its mere existence proves
        nothing - only whether it is being shown."""
        for selector in (
            "iframe[src*='recaptcha'][src*='bframe']",
            "iframe[title*='recaptcha challenge' i]",
        ):
            try:
                node = page.query_selector(selector)
                if node and node.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    @staticmethod
    def _wait_until_signed_in(page, seconds: int = 60) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if "signin" not in (page.url or "").lower():
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                except Exception:  # noqa: BLE001
                    pass
                return True
            try:
                page.wait_for_timeout(1_000)
            except Exception:  # noqa: BLE001
                return False
        return False

    @staticmethod
    def _page_complaint(page) -> str:
        """Whatever the sign-in page is complaining about, so the failure says
        something more useful than "did not accept"."""
        for selector in (
            "[role='alert']",
            ".error-message",
            "[class*='error']:not(:empty)",
            "[id*='error']",
        ):
            try:
                node = page.query_selector(selector)
                if node:
                    text = clean(node.inner_text())
                    if text:
                        return truncate(text, 120)
            except Exception:  # noqa: BLE001
                continue
        return ""

    def _wait_for_rows(self, page, seconds: int = 25) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for frame in page.frames:
                try:
                    if len(frame.query_selector_all("table tr")) >= 3:
                        return True
                except Exception:  # noqa: BLE001 - frame still navigating
                    continue
            try:
                page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                return False
        return False

    # -- extraction ------------------------------------------------------

    def _extract(self, page) -> List[Assignment]:
        course = self._course_name(page)

        # Try the real table first; the heuristic is only for course layouts
        # that do not present one.
        for frame in page.frames:
            items = self._from_table(frame, course)
            if items:
                self.log.info("   Read the assignment table (%d row(s)).", len(items))
                return items

        return self._by_heuristic(page, course)

    def _from_table(self, frame, course: str) -> List[Assignment]:
        """Parse MyLab's assignment grid: Due | Assignment | ... | Score."""
        try:
            tables = frame.query_selector_all("table")
        except Exception:  # noqa: BLE001 - detached frame
            return []

        for table in tables:
            try:
                rows = table.query_selector_all("tr")
            except Exception:  # noqa: BLE001
                continue
            if len(rows) < 3:
                continue

            header = _cell_texts(rows[0])
            due_at = _column_with(header, "due")
            title_at = _column_with(header, "assignment")
            if due_at is None or title_at is None:
                continue

            score_at = _column_with(header, "score")
            out: List[Assignment] = []

            for row in rows[1:]:
                cells = _cell_texts(row)
                if len(cells) <= max(due_at, title_at):
                    continue

                # The date and time sit either side of a <br>, so the text
                # arrives as "09/06/26 11:59pm" once whitespace is collapsed.
                due = parse_due(cells[due_at], self.cfg.tz)
                title = cells[title_at]
                if due is None or not _looks_like_a_title(title):
                    continue

                status = ""
                if score_at is not None and score_at < len(cells):
                    status = _score_text(cells[score_at])

                out.append(
                    Assignment(
                        source=self.label,
                        course=course,
                        title=title,
                        due=due,
                        status=status,
                        url=self.creds["url"],
                    )
                )

            if out:
                return out

        return []

    def _course_name(self, page) -> str:
        """Prefer what you configured, then the name MyLab prints on the page."""
        configured = self.creds.get("course")
        if configured:
            return configured

        for frame in page.frames:
            try:
                header = frame.query_selector(".printheadermaintable, table.printheadermaintable")
            except Exception:  # noqa: BLE001
                continue
            if not header:
                continue
            cells = _cell_texts(header.query_selector_all("tr")[0]) if header else []
            for cell in cells:
                # "Math 220 -- Fall 2026" sits next to the student's name.
                if re.search(r"[A-Za-z]{2,}\s*\d", cell) and "@" not in cell:
                    return cell

        return clean(page.title()) or "Pearson course"

    # -- the crude fallback ----------------------------------------------

    def _by_heuristic(self, page, course: str) -> List[Assignment]:
        found: List[Assignment] = []
        seen = set()

        for frame in page.frames:
            for selector in ROW_SELECTORS:
                try:
                    rows = frame.query_selector_all(selector)
                except Exception:  # noqa: BLE001 - detached frame
                    continue

                for row in rows[:400]:
                    parsed = self._parse_row(row)
                    if parsed is None:
                        continue
                    title, due, link = parsed
                    fingerprint = (title.lower(), due.isoformat() if due else "")
                    if fingerprint in seen:
                        continue
                    seen.add(fingerprint)
                    found.append(
                        Assignment(
                            source=self.label,
                            course=course,
                            title=title,
                            due=due,
                            status="",
                            url=link or self.creds["url"],
                        )
                    )

            if found:
                break

        return found

    def _parse_row(self, row) -> Optional[Tuple[str, object, str]]:
        try:
            text = clean(row.inner_text())
        except Exception:  # noqa: BLE001
            return None
        if not text or len(text) > 400:
            return None

        match = None
        for pattern in DATE_PATTERNS:
            match = pattern.search(text)
            if match:
                break
        if not match:
            return None

        due = parse_due(match.group(0), self.cfg.tz)
        if due is None:
            return None

        title = ""
        try:
            link = row.query_selector("a")
            if link:
                title = clean(link.inner_text())
        except Exception:  # noqa: BLE001
            link = None

        if not title:
            # Fall back to whatever text sits before the date.
            title = clean(text[: match.start()]) or clean(text)
        title = truncate(title, 120)

        if not _looks_like_a_title(title):
            return None

        href = ""
        try:
            if link:
                href = link.get_attribute("href") or ""
        except Exception:  # noqa: BLE001
            href = ""

        return title, due, href
