"""Offline tests for the parts that do not need a login.

    ./.venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import logging
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config  # noqa: E402
from bot.main import _filter_and_sort  # noqa: E402
from bot.models import Assignment  # noqa: E402
from bot.sources.canvas import _next_link, _status  # noqa: E402
from bot.sources.gradescope import Gradescope  # noqa: E402
from bot.sources.gradescope import _course_label  # noqa: E402
from bot.util import (  # noqa: E402
    days_until,
    fmt_due_display,
    fmt_due_on,
    fmt_points,
    parse_due,
    subject_code,
    term_label,
)

TZ = ZoneInfo("America/Chicago")
CFG = config.Config(raw={"timezone": "America/Chicago", "lookahead_days": 60, "lookback_days": 7})


class ParseDue(unittest.TestCase):
    def test_iso_with_zone_becomes_local(self):
        due = parse_due("2026-10-20T04:59:00Z", TZ)
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M"), "2026-10-19 23:59")

    def test_naive_is_treated_as_school_time(self):
        due = parse_due("2026-10-20 23:59:00", TZ)
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M"), "2026-10-20 23:59")
        self.assertEqual(due.tzinfo, TZ)

    def test_epoch_milliseconds(self):
        # Blackboard hands back milliseconds in places.
        due = parse_due(1760979540000, TZ)
        self.assertEqual(due.year, 2025)

    def test_scraped_text(self):
        due = parse_due("Due: Oct 20, 2026 at 11:59pm", TZ)
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M"), "2026-10-20 23:59")

    def test_junk_is_none(self):
        for value in (None, "", "   ", "no due date", "TBD"):
            self.assertIsNone(parse_due(value, TZ), value)


class FormatPoints(unittest.TestCase):
    def test_whole_numbers_lose_the_decimal(self):
        self.assertEqual(fmt_points(20.0), "20")
        self.assertEqual(fmt_points("100.00"), "100")

    def test_tens_are_not_mangled(self):
        # A rstrip("0") here would turn 10 into "1".
        self.assertEqual(fmt_points(10), "10")
        self.assertEqual(fmt_points(100), "100")

    def test_fractions_survive(self):
        self.assertEqual(fmt_points(2.5), "2.5")

    def test_empty(self):
        self.assertEqual(fmt_points(None), "")
        self.assertEqual(fmt_points(""), "")

    def test_non_numeric_passes_through(self):
        self.assertEqual(fmt_points("Complete/Incomplete"), "Complete/Incomplete")


class CredentialFiles(unittest.TestCase):
    """People paste values with quotes and stray spaces. Take them anyway."""

    def _read(self, text):
        import tempfile

        from bot import creds

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "demo.txt").write_text(text, encoding="utf-8")
            original = creds.CREDENTIALS
            creds.CREDENTIALS = folder
            try:
                return creds.read("demo")
            finally:
                creds.CREDENTIALS = original

    def test_quotes_and_spaces_are_stripped(self):
        values = self._read('# a comment\n\nusername= "student@example.edu"\npassword=  "p@ss w0rd"  \n')
        self.assertEqual(values["username"], "student@example.edu")
        self.assertEqual(values["password"], "p@ss w0rd")

    def test_blank_values_are_dropped_not_returned_empty(self):
        values = self._read("url=\ntoken=\nusername= someone\n")
        self.assertNotIn("url", values)
        self.assertNotIn("token", values)
        self.assertEqual(values["username"], "someone")

    def test_values_containing_equals_survive(self):
        values = self._read("token= abc=def==\n")
        self.assertEqual(values["token"], "abc=def==")

    def test_missing_file_is_empty(self):
        from bot import creds

        self.assertEqual(creds.read("definitely-not-a-real-source"), {})


class AppsScriptErrorPages(unittest.TestCase):
    """Apps Script reports failures as HTML. Each one should come back as a fix."""

    def test_unsaved_deployment(self):
        from bot.sheet import explain_html

        message = explain_html("<html><title>Error</title>Script function not found: doPost</html>")
        self.assertIn("deployed before the code was saved", message)
        self.assertIn("New version", message)

    def test_private_deployment(self):
        from bot.sheet import explain_html

        message = explain_html('<html>Please <a href="https://accounts.google.com">sign in</a></html>')
        self.assertIn("Who has access", message)

    def test_script_exception_strips_the_markup(self):
        from bot.sheet import explain_html

        message = explain_html("<html><body><div>Exception: Cannot read x</div></body></html>")
        self.assertIn("Executions", message)
        self.assertIn("Exception: Cannot read x", message)
        self.assertNotIn("<div>", message)

    def test_unrecognised_page_is_quoted_raw(self):
        from bot.sheet import explain_html

        self.assertIn("something else entirely", explain_html("something else entirely"))


class PearsonTitleFilter(unittest.TestCase):
    """The first live run pulled the page's clock in as an assignment."""

    def test_rejects_timestamps(self):
        from bot.sources.pearson import _looks_like_a_title

        for junk in ("10/13/26 5:08am", "5:08 AM", "10/13/26", "2026-10-13 05:08", "", "a"):
            self.assertFalse(_looks_like_a_title(junk), junk)

    def test_rejects_navigation(self):
        from bot.sources.pearson import _looks_like_a_title

        for junk in ("Course Home", "Calendar", "Sign out", "Gradebook"):
            self.assertFalse(_looks_like_a_title(junk), junk)

    def test_accepts_real_assignment_names(self):
        from bot.sources.pearson import _looks_like_a_title

        for good in (
            "Chapter 4 Homework",
            "Section 2.3 Practice",
            "MOM Homework 4",
            "Quiz 8",
            "Exam 2 Review",
            "Homework Sec 4.7",
        ):
            self.assertTrue(_looks_like_a_title(good), good)


class PearsonTable(unittest.TestCase):
    """MyLab presents a real table: Due | Assignment | ... | Gradebook Score."""

    HEADER = ["Due", "", "Assignment", "Time Limit", "Attempts", "Gradebook Score"]

    def test_finds_the_columns_by_name(self):
        from bot.sources.pearson import _column_with

        self.assertEqual(_column_with(self.HEADER, "due"), 0)
        self.assertEqual(_column_with(self.HEADER, "assignment"), 2)
        self.assertEqual(_column_with(self.HEADER, "score"), 5)
        self.assertIsNone(_column_with(self.HEADER, "nonsense"))

    def test_due_cell_parses_once_the_line_break_is_collapsed(self):
        # The cell is "09/06/26<br>11:59pm"; collapsed it must still parse.
        due = parse_due("09/06/26 11:59pm", TZ)
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M"), "2026-09-06 23:59")

    def test_score_link_label_is_not_a_status(self):
        from bot.sources.pearson import _score_text

        self.assertEqual(_score_text("see score for Sec 1.2"), "")
        self.assertEqual(_score_text(""), "")
        self.assertEqual(_score_text("18/20"), "18/20")


class FirstVisibleBudget(unittest.TestCase):
    """A login page with seven candidate selectors once cost two minutes,
    because each miss waited out its own timeout."""

    class _Element:
        def is_visible(self):
            return True

    class _Page:
        def __init__(self, appears_after):
            self.polls = 0
            self.appears_after = appears_after
            self.asked = []

        def query_selector(self, selector):
            self.asked.append(selector)
            if selector == "input#real" and self.polls >= self.appears_after:
                return FirstVisibleBudget._Element()
            return None

        def wait_for_timeout(self, _ms):
            self.polls += 1

    def test_every_selector_is_retried_each_pass(self):
        from bot.browser import first_visible

        page = self._Page(appears_after=3)
        found = first_visible(page, "a", "b", "c", "input#real", timeout=60_000)

        self.assertEqual(found, "input#real")
        # The misses were re-checked on each pass rather than blocking once each.
        self.assertGreater(page.asked.count("a"), 1)
        self.assertEqual(page.polls, 3)

    def test_gives_up_on_the_whole_list_not_per_selector(self):
        from bot.browser import first_visible

        page = self._Page(appears_after=10_000)
        self.assertIsNone(first_visible(page, "a", "b", "c", timeout=0))


class Keys(unittest.TestCase):
    def test_same_assignment_keeps_its_key_when_the_deadline_moves(self):
        early = Assignment("Canvas", "CS 251", "Homework 3", parse_due("2026-10-20", TZ))
        late = Assignment("Canvas", "CS 251", "Homework 3", parse_due("2026-10-27", TZ))
        self.assertEqual(early.key, late.key)

    def test_punctuation_and_case_do_not_split_a_row(self):
        a = Assignment("Canvas", "CS 251", "Homework 3")
        b = Assignment("canvas", "CS  251", "homework #3!")
        self.assertEqual(a.key, b.key)

    def test_different_courses_are_different_rows(self):
        a = Assignment("Canvas", "CS 251", "Homework 3")
        b = Assignment("Canvas", "MATH 180", "Homework 3")
        self.assertNotEqual(a.key, b.key)


class IsDone(unittest.TestCase):
    def test_finished_statuses(self):
        for status in ("Submitted", "Graded 18/20", "Complete", "Done", "Late", "46.5 / 50.0"):
            self.assertTrue(Assignment("s", "c", "t", status=status).is_done, status)

    def test_outstanding_statuses(self):
        for status in ("", "No Submission", "Open", "Missing", "Past due", "Not started"):
            self.assertFalse(Assignment("s", "c", "t", status=status).is_done, status)


class FilterAndSort(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(TZ)

    def test_drops_items_outside_the_window(self):
        items = [
            Assignment("Canvas", "A", "ancient", self.now - timedelta(days=90)),
            Assignment("Canvas", "A", "far future", self.now + timedelta(days=400)),
            Assignment("Canvas", "A", "soon", self.now + timedelta(days=3)),
        ]
        kept = _filter_and_sort(items, CFG)
        self.assertEqual([a.title for a in kept], ["soon"])

    def test_undated_items_are_dropped_by_default(self):
        # Finished exams have no due date and were cluttering the sheet.
        items = [
            Assignment("Gradescope", "Math 218", "Exam #1", None, "46.5 / 50.0"),
            Assignment("Canvas", "A", "soon", self.now + timedelta(days=1)),
        ]
        self.assertEqual([a.title for a in _filter_and_sort(items, CFG)], ["soon"])

    def test_undated_items_can_be_switched_back_on(self):
        cfg = config.Config(raw=dict(CFG.raw, filters={"include_undated": True}))
        items = [
            Assignment("Gradescope", "Math 218", "Exam #1"),
            Assignment("Canvas", "A", "soon", self.now + timedelta(days=1)),
        ]
        self.assertEqual([a.title for a in _filter_and_sort(items, cfg)], ["soon", "Exam #1"])

    def test_past_work_already_handed_in_is_dropped(self):
        items = [Assignment("Gradescope", "Math 220", "Homework 6", self.now - timedelta(days=2), "Submitted")]
        self.assertEqual(_filter_and_sort(items, CFG), [])

    def test_past_work_never_handed_in_is_kept(self):
        # This is the case actually worth seeing.
        items = [
            Assignment("Gradescope", "Math 220", "Homework 6", self.now - timedelta(days=2), "No Submission")
        ]
        self.assertEqual([a.title for a in _filter_and_sort(items, CFG)], ["Homework 6"])

    def test_future_work_already_submitted_is_kept(self):
        items = [
            Assignment("Gradescope", "Math 218", "Written Homework #3", self.now + timedelta(days=2), "Submitted")
        ]
        self.assertEqual(len(_filter_and_sort(items, CFG)), 1)

    def test_completed_past_can_be_switched_back_on(self):
        cfg = config.Config(raw=dict(CFG.raw, filters={"include_completed_past": True}))
        items = [Assignment("Gradescope", "Math 220", "Homework 6", self.now - timedelta(days=2), "Submitted")]
        self.assertEqual(len(_filter_and_sort(items, cfg)), 1)

    def test_sorted_by_due_date(self):
        items = [
            Assignment("Canvas", "A", "third", self.now + timedelta(days=9)),
            Assignment("Canvas", "A", "first", self.now + timedelta(days=1)),
            Assignment("Canvas", "A", "second", self.now + timedelta(days=5)),
        ]
        self.assertEqual(
            [a.title for a in _filter_and_sort(items, CFG)], ["first", "second", "third"]
        )

    def test_duplicate_across_sources_prefers_the_one_with_a_status(self):
        due = self.now + timedelta(days=2)
        blank = Assignment("Blackboard", "CS 251", "Homework 3", due, status="")
        with_status = Assignment("blackboard", "CS 251", "Homework 3", due, status="Submitted")
        kept = _filter_and_sort([blank, with_status], CFG)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].status, "Submitted")

    def test_dedupe_keeps_the_right_row_when_many_are_present(self):
        due = self.now + timedelta(days=2)
        items = [
            Assignment("Canvas", "CS 251", "Alpha", due, status="Open"),
            Assignment("Blackboard", "CS 251", "Beta", due, status=""),
            Assignment("blackboard", "CS 251", "Beta", due, status="Submitted"),
            Assignment("Canvas", "CS 251", "Gamma", due, status="Open"),
        ]
        kept = _filter_and_sort(items, CFG)
        self.assertEqual(len(kept), 3)
        self.assertEqual({a.title for a in kept}, {"Alpha", "Beta", "Gamma"})
        self.assertEqual(next(a for a in kept if a.title == "Beta").status, "Submitted")


class TrackerFields(unittest.TestCase):
    """The tracker view wants a short code and a long date."""

    def test_subject_code_from_real_course_names(self):
        cases = {
            "CS277 Lab (48669) Thurs 1pm FA26 CC": "CS 277",
            "Math 218 Applied Linear Algebra (50629) 2026": "MATH 218",
            "Math 220 -- Fall 2026": "MATH 220",
            "BIOS 120 Biology of Populations": "BIOS 120",
            "POLS 101 Intro to American Government (13190)": "POLS 101",
            "Math 220 Fall 2026 - Math 220 Fall 2026": "MATH 220",
            "Math 218": "MATH 218",
        }
        for course, expected in cases.items():
            self.assertEqual(subject_code(course), expected, course)

    def test_subject_code_falls_back_to_the_name(self):
        self.assertEqual(subject_code("Independent Study"), "Independent S…")
        self.assertEqual(subject_code(""), "")

    def test_due_on_is_the_long_day_form(self):
        self.assertEqual(fmt_due_on(parse_due("2026-10-18T23:59:00-05:00", TZ)), "Sunday, 10/18")
        self.assertEqual(fmt_due_on(parse_due("2026-10-13T23:59:00-05:00", TZ)), "Tuesday, 10/13")
        self.assertEqual(fmt_due_on(None), "")

    def test_term_label(self):
        self.assertEqual(term_label(parse_due("2026-10-13T09:00:00-05:00", TZ)), "Fall 2026")
        self.assertEqual(term_label(parse_due("2026-02-13T09:00:00-06:00", TZ)), "Spring 2026")
        self.assertEqual(term_label(parse_due("2026-06-13T09:00:00-05:00", TZ)), "Summer 2026")

    def test_payload_carries_the_tracker_fields(self):
        due = parse_due("2026-10-18T23:59:00-05:00", TZ)
        now = parse_due("2026-10-13T09:00:00-05:00", TZ)
        payload = Assignment(
            "Canvas", "Math 218 Applied Linear Algebra", "Written Homework #4", due, "Submitted"
        ).to_payload(now)
        self.assertEqual(payload["subject"], "MATH 218")
        self.assertEqual(payload["due_on"], "Sunday, 10/18")
        self.assertEqual(payload["days_left"], 5)
        self.assertTrue(payload["done"])


class DisplayFormatting(unittest.TestCase):
    def test_due_text_is_what_the_sheet_shows(self):
        due = parse_due("2026-10-18T23:59:00-05:00", TZ)
        self.assertEqual(fmt_due_display(due), "Sun 18 Oct, 11:59 PM")

    def test_midnight_and_noon(self):
        self.assertEqual(fmt_due_display(parse_due("2026-10-18T00:30:00-05:00", TZ)), "Sun 18 Oct, 12:30 AM")
        self.assertEqual(fmt_due_display(parse_due("2026-10-18T12:05:00-05:00", TZ)), "Sun 18 Oct, 12:05 PM")

    def test_no_due_date(self):
        self.assertEqual(fmt_due_display(None), "")

    def test_days_until_counts_calendar_days(self):
        now = parse_due("2026-10-13T09:00:00-05:00", TZ)
        self.assertEqual(days_until(parse_due("2026-10-13T23:59:00-05:00", TZ), now), 0)
        self.assertEqual(days_until(parse_due("2026-10-14T00:01:00-05:00", TZ), now), 1)
        self.assertEqual(days_until(parse_due("2026-10-11T23:59:00-05:00", TZ), now), -2)
        self.assertIsNone(days_until(None, now))

    def test_payload_shape(self):
        due = parse_due("2026-10-18T23:59:00-05:00", TZ)
        now = parse_due("2026-10-13T09:00:00-05:00", TZ)
        payload = Assignment("Gradescope", "Math 220", "Homework 7", due, "No Submission", "20").to_payload(now)
        self.assertEqual(payload["due"], "Sun 18 Oct, 11:59 PM")
        self.assertEqual(payload["days_left"], 5)
        self.assertTrue(payload["sort"].startswith("2026-10-18T23:59"))

    def test_undated_payload_sorts_last(self):
        payload = Assignment("Gradescope", "Math 218", "Exam #1").to_payload()
        self.assertEqual(payload["due"], "")
        self.assertEqual(payload["days_left"], "")
        self.assertEqual(payload["sort"], "9999-12-31")


class CanvasBits(unittest.TestCase):
    def test_next_link_header(self):
        header = (
            '<https://x/api/v1/courses?page=1>; rel="current",'
            '<https://x/api/v1/courses?page=2>; rel="next",'
            '<https://x/api/v1/courses?page=9>; rel="last"'
        )
        self.assertEqual(_next_link(header), "https://x/api/v1/courses?page=2")

    def test_no_next_link(self):
        self.assertIsNone(_next_link('<https://x>; rel="current"'))
        self.assertIsNone(_next_link(""))

    def test_status_from_submission(self):
        future = datetime.now(TZ) + timedelta(days=5)
        past = datetime.now(TZ) - timedelta(days=5)

        graded = {"points_possible": 20, "submission": {"workflow_state": "graded", "score": 18}}
        self.assertEqual(_status(graded, past, TZ), "Graded 18/20")

        submitted = {"submission": {"submitted_at": "2026-10-01T00:00:00Z"}}
        self.assertEqual(_status(submitted, future, TZ), "Submitted")

        late = {"submission": {"submitted_at": "2026-10-01T00:00:00Z", "late": True}}
        self.assertEqual(_status(late, past, TZ), "Late")

        self.assertEqual(_status({"submission": {"missing": True}}, past, TZ), "Missing")
        self.assertEqual(_status({}, past, TZ), "Past due")
        self.assertEqual(_status({}, future, TZ), "Open")
        self.assertEqual(_status({}, None, TZ), "Open")


ACCOUNT_HTML = """
<div class="courseList">
  <div class="courseList--term">Fall 2026</div>
  <div class="courseList--coursesForTerm">
    <a class="courseBox" href="/courses/100">
      <h3 class="courseBox--shortname">CS 251</h3>
      <div class="courseBox--name">Data Structures</div>
    </a>
    <a class="courseBox" href="/courses/101">
      <h3 class="courseBox--shortname">MATH 180</h3>
      <div class="courseBox--name">Calculus I</div>
    </a>
  </div>
  <div class="courseList--term">Spring 2026</div>
  <div class="courseList--coursesForTerm">
    <a class="courseBox" href="/courses/9">
      <h3 class="courseBox--shortname">OLD 101</h3>
      <div class="courseBox--name">Last Term</div>
    </a>
  </div>
</div>
"""

COURSE_HTML = """
<table id="assignments-student-table"><tbody>
  <tr>
    <th class="table--primaryLink"><a href="/courses/100/assignments/1/submissions/5">Homework 3</a></th>
    <td class="submissionStatus"><div class="submissionStatus--score">18.0 / 20.0</div></td>
    <td><div class="submissionTimeChart--dueDate" datetime="2026-10-20 23:59:00 -0500">Oct 20</div>
        <div class="submissionTimeChart--dueDate" datetime="2026-10-23 23:59:00 -0500">Oct 23</div></td>
  </tr>
  <tr>
    <th class="table--primaryLink">Project Proposal</th>
    <td class="submissionStatus"><div class="submissionStatus--text">No Submission</div></td>
    <td><div class="submissionTimeChart--dueDate" datetime="2026-11-05 17:00:00 -0600">Nov 5</div></td>
  </tr>
</tbody></table>
"""


class _StubResponse:
    def __init__(self, text):
        self.text = text
        self.url = "https://www.gradescope.com/account"

    def raise_for_status(self):
        return None


class _StubSession:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, timeout=None):  # noqa: ARG002
        for fragment, html in self.pages.items():
            if fragment in url:
                return _StubResponse(html)
        raise AssertionError(f"unexpected GET {url}")


class GradescopeParsing(unittest.TestCase):
    def setUp(self):
        self.source = Gradescope(CFG, visible=False)

    def test_current_term_only_by_default(self):
        session = _StubSession({"/account": ACCOUNT_HTML})
        courses = self.source._courses(session)
        self.assertEqual(
            sorted(courses), ["CS 251 - Data Structures", "MATH 180 - Calculus I"]
        )

    def test_all_terms_when_configured(self):
        source = Gradescope(
            config.Config(raw={"sources": {"gradescope": {"terms": "all"}}}), visible=False
        )
        courses = source._courses(_StubSession({"/account": ACCOUNT_HTML}))
        self.assertIn("OLD 101 - Last Term", courses)

    def test_course_label_does_not_repeat_itself(self):
        # Gradescope gave "Math 220 Fall 2026 - Math 220 Fall 2026 (...)".
        self.assertEqual(_course_label("Math 220 Fall 2026", "Math 220 Fall 2026 (Section A)"), "Math 220 Fall 2026")
        self.assertEqual(_course_label("Math 218", "Math 218 Applied Linear Algebra"), "Math 218")
        self.assertEqual(_course_label("CS 251", "Data Structures"), "CS 251 - Data Structures")
        self.assertEqual(_course_label("", "Only Full"), "Only Full")
        self.assertEqual(_course_label("Only Short", ""), "Only Short")

    def test_assignment_rows(self):
        session = _StubSession({"/courses/100": COURSE_HTML})
        items = self.source._course_assignments(
            session, "CS 251", "https://www.gradescope.com/courses/100"
        )
        self.assertEqual(len(items), 2)

        first = items[0]
        self.assertEqual(first.title, "Homework 3")
        self.assertEqual(first.status, "18.0 / 20.0")
        # The first due date wins; the second one is the late deadline.
        self.assertEqual(first.due.strftime("%Y-%m-%d %H:%M"), "2026-10-20 23:59")
        self.assertTrue(first.url.endswith("/submissions/5"))

        second = items[1]
        self.assertEqual(second.title, "Project Proposal")
        self.assertEqual(second.status, "No Submission")
        self.assertEqual(second.due.strftime("%Y-%m-%d %H:%M"), "2026-11-05 17:00")


COURSES_PAGE_1 = [{"id": 1, "name": "CS 251 Data Structures"}]
COURSES_PAGE_2 = [{"id": 2, "name": "MATH 180", "course_code": "MATH180"}]
ASSIGNMENTS = {
    1: [
        {
            "name": "Homework 3",
            "due_at": "2026-10-21T04:59:00Z",
            "html_url": "https://canvas.test/courses/1/assignments/11",
            "points_possible": 20.0,
            "submission": {"workflow_state": "graded", "score": 18},
        },
        {
            "name": "Quiz 1",
            "due_at": None,
            "html_url": "https://canvas.test/courses/1/assignments/12",
            "points_possible": 10,
            "submission": {"workflow_state": "unsubmitted"},
        },
    ],
    2: [
        {
            "name": "Problem Set 5",
            "due_at": "2026-11-02T05:00:00Z",
            "html_url": "https://canvas.test/courses/2/assignments/21",
            "points_possible": None,
            "submission": {"submitted_at": "2026-10-30T12:00:00Z"},
        }
    ],
}


class CanvasOverHttp(unittest.TestCase):
    """Runs the real token path against a stand-in Canvas, so pagination,
    the Link header walk and status mapping are all exercised together."""

    @classmethod
    def setUpClass(cls):
        import json as jsonlib
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import parse_qs, urlparse

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer test-token":
                    return self._send(401, {"errors": "unauthorised"})

                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)

                if parsed.path == "/api/v1/courses":
                    page = query.get("page", ["1"])[0]
                    if page == "1":
                        base = f"http://127.0.0.1:{cls.port}/api/v1/courses"
                        return self._send(
                            200,
                            COURSES_PAGE_1,
                            link=f'<{base}?page=2>; rel="next"',
                        )
                    return self._send(200, COURSES_PAGE_2)

                for course_id, items in ASSIGNMENTS.items():
                    if parsed.path == f"/api/v1/courses/{course_id}/assignments":
                        return self._send(200, items)

                return self._send(404, {"errors": "not found"})

            def _send(self, code, payload, link=None):
                body = jsonlib.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if link:
                    self.send_header("Link", link)
                self.end_headers()
                self.wfile.write(body)

        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _source(self, token="test-token"):
        from bot.sources.canvas import Canvas

        source = Canvas(CFG, visible=False)
        source.base = f"http://127.0.0.1:{self.port}"
        source.creds = {"url": source.base, "token": token}
        return source

    def test_walks_both_pages_and_maps_everything(self):
        items = self._source().fetch()
        self.assertEqual(len(items), 3)

        by_title = {a.title: a for a in items}

        homework = by_title["Homework 3"]
        self.assertEqual(homework.course, "CS 251 Data Structures")
        self.assertEqual(homework.status, "Graded 18/20")
        self.assertEqual(homework.points, "20")
        self.assertEqual(homework.due.strftime("%Y-%m-%d %H:%M"), "2026-10-20 23:59")

        self.assertIsNone(by_title["Quiz 1"].due)
        self.assertEqual(by_title["Quiz 1"].status, "Open")

        # Second page was reached.
        self.assertEqual(by_title["Problem Set 5"].course, "MATH 180")
        self.assertEqual(by_title["Problem Set 5"].status, "Submitted")

    def test_bad_token_says_so_plainly(self):
        with self.assertRaises(RuntimeError) as caught:
            self._source(token="wrong").fetch()
        self.assertIn("rejected the token", str(caught.exception))


# Shaped like the real /learn/api/public/v1/calendars/items response: the kind
# is in `type`, and the course name carries its internal id as a prefix.
CALENDAR = [
    {
        "type": "GradebookColumn",
        "title": "Exam 1",
        "calendarName": "2026.fall.cs.107.12345: CS 107 Intro",
        "calendarId": "_12345_1",
        "end": "2026-10-25T23:59:00.000Z",
    },
    {
        "type": "Course",
        "title": "Lecture",
        "calendarName": "2026.fall.cs.107.12345: CS 107 Intro",
        "calendarId": "_12345_1",
        "end": "2026-10-14T15:00:00.000Z",
    },
    {
        "type": "GradebookColumn",
        "title": "No deadline set",
        "calendarName": "2026.fall.cs.107.12345: CS 107 Intro",
        "calendarId": "_12345_1",
    },
    # Older instances use the other field name.
    {
        "itemSourceType": "GradebookColumn",
        "title": "Legacy shape",
        "calendarName": "CS 107 Intro",
        "calendarId": "_12345_1",
        "end": "2026-10-26T23:59:00.000Z",
    },
]

FUTURE_END = "2099-12-20T05:59:00.000Z"
MEMBERSHIPS = {
    "results": [
        {"course": {"id": "_999_1", "displayName": "PHYS 141", "name": "Physics", "endDate": FUTURE_END}},
        {"course": {}},
        # Already finished - must be skipped.
        {"course": {"id": "_111_1", "displayName": "OLD 100", "endDate": "2020-05-01T05:00:00.000Z"}},
    ]
}
# Real shape: dueDate at the top level, name in columnName.
COLUMNS = {
    "results": [
        {"columnName": "Lab 2", "points": 25.0, "dueDate": "2026-10-30T04:59:00.000Z"},
        {"columnName": "Participation", "points": 10},
        {"name": "Legacy column", "possible": 5, "grading": {"due": "2026-11-01T04:59:00.000Z"}},
    ]
}


class BlackboardParsing(unittest.TestCase):
    def _source(self, responses):
        from bot.sources.blackboard import Blackboard

        source = Blackboard(CFG, visible=False)
        source.creds = {"username": "netid", "password": "pw"}

        def fake_json(ctx, url):  # noqa: ARG001
            for fragment, payload in responses.items():
                if fragment in url:
                    return payload
            return None

        # Set on the instance, so it is called unbound - no self argument.
        source._json = fake_json
        return source

    def test_calendar_uses_the_public_items_endpoint(self):
        # The non-public /calendars/ returns the list of calendars, not their
        # contents, which is why this silently found nothing.
        asked = []

        source = self._source({"/calendars/items": CALENDAR})
        original = source._json

        def spy(ctx, url):
            asked.append(url)
            return original(ctx, url)

        source._json = spy
        source._via_calendar(None)
        self.assertTrue(any("/public/v1/calendars/items" in u for u in asked), asked)

    def test_calendar_keeps_only_dated_assignments(self):
        source = self._source({"/calendars/items": CALENDAR})
        items = source._via_calendar(None)
        self.assertEqual([a.title for a in items], ["Exam 1", "Legacy shape"])
        self.assertEqual(items[0].due.strftime("%Y-%m-%d %H:%M"), "2026-10-25 18:59")
        self.assertIn("/ultra/courses/_12345_1/outline", items[0].url)

    def test_calendar_strips_the_internal_course_prefix(self):
        source = self._source({"/calendars/items": CALENDAR})
        items = source._via_calendar(None)
        # Matching the gradebook's name is what lets the two strategies merge.
        self.assertEqual(items[0].course, "CS 107 Intro")

    def test_calendar_handles_the_wrapped_shape(self):
        source = self._source({"/calendars/items": {"results": CALENDAR}})
        self.assertEqual(len(source._via_calendar(None)), 2)

    def test_calendar_missing_is_empty_not_an_error(self):
        self.assertEqual(self._source({})._via_calendar(None), [])

    def test_gradebook_reads_the_real_field_names(self):
        source = self._source(
            {
                "/users/me": {"id": "_777_1"},
                "/memberships": MEMBERSHIPS,
                "/gradebook/columns": COLUMNS,
            }
        )
        items = source._via_gradebook(None)
        self.assertEqual([a.title for a in items], ["Lab 2", "Legacy column"])
        self.assertEqual(items[0].course, "PHYS 141")
        self.assertEqual(items[0].points, "25")
        self.assertEqual(items[0].due.strftime("%Y-%m-%d"), "2026-10-29")

    def test_gradebook_skips_finished_courses(self):
        source = self._source(
            {
                "/users/me": {"id": "_777_1"},
                "/memberships": MEMBERSHIPS,
                "/gradebook/columns": COLUMNS,
            }
        )
        self.assertNotIn("OLD 100", {a.course for a in source._via_gradebook(None)})

    def test_calendar_and_gradebook_names_agree(self):
        from bot.sources.blackboard import _course_name

        self.assertEqual(
            _course_name("2026.fall.cs.277.48669: CS277 Lab (48669) Thurs 1pm FA26 CC"),
            "CS277 Lab (48669) Thurs 1pm FA26 CC",
        )
        self.assertEqual(_course_name("CS277 Lab"), "CS277 Lab")
        self.assertEqual(_course_name(None), "Blackboard")

    def test_signed_in_check(self):
        self.assertTrue(self._source({"/users/me": {"id": "_777_1"}})._signed_in(None))
        self.assertFalse(self._source({})._signed_in(None))


if __name__ == "__main__":
    unittest.main()


class AlertSuppression(unittest.TestCase):
    """A source stuck for the same reason must not notify on every run, or you
    stop reading notifications and miss a real one."""

    def setUp(self):
        import tempfile

        from bot import paths, state

        self._tmp = tempfile.TemporaryDirectory()
        self._original = paths.STATE
        paths.STATE = Path(self._tmp.name)
        state.ALERTS = paths.STATE / "alerts.json"
        self.state = state

    def tearDown(self):
        from bot import paths

        paths.STATE = self._original
        self.state.ALERTS = self._original / "alerts.json"
        self._tmp.cleanup()

    def test_first_failure_is_reported(self):
        self.assertTrue(self.state.should_alert("pearson", "CAPTCHA"))

    def test_the_same_failure_is_not_reported_again(self):
        self.state.should_alert("pearson", "CAPTCHA")
        self.assertFalse(self.state.should_alert("pearson", "CAPTCHA"))
        self.assertFalse(self.state.should_alert("pearson", "CAPTCHA"))

    def test_a_different_failure_is_reported(self):
        self.state.should_alert("pearson", "CAPTCHA")
        self.assertTrue(self.state.should_alert("pearson", "password rejected"))

    def test_other_sources_are_unaffected(self):
        self.state.should_alert("pearson", "CAPTCHA")
        self.assertTrue(self.state.should_alert("blackboard", "CAPTCHA"))

    def test_recovering_re_arms_the_alert(self):
        self.state.should_alert("pearson", "CAPTCHA")
        self.state.clear_alert("pearson")
        self.assertTrue(self.state.should_alert("pearson", "CAPTCHA"))

    def test_clearing_a_source_that_never_failed_is_harmless(self):
        self.state.clear_alert("canvas")


class DueSoonNotification(unittest.TestCase):
    """What the morning notification picks out."""

    def setUp(self):
        self.now = datetime.now(TZ)
        self.sent = []

        from bot import main, notify

        self.main = main
        self._original_send = notify.send
        notify.send = lambda title, message, **kw: self.sent.append((title, message))

    def tearDown(self):
        from bot import notify

        notify.send = self._original_send

    class _Args:
        visible = False
        no_notify = False
        dry_run = False

    def _run(self, assignments, days=2):
        cfg = config.Config(raw=dict(CFG.raw, notify_within_days=days))
        self.main._notify_due(assignments, cfg, self._Args(), logging.getLogger("quiet"))

    def test_silent_when_nothing_is_imminent(self):
        self._run([Assignment("Canvas", "A", "far off", self.now + timedelta(days=20))])
        self.assertEqual(self.sent, [])

    def test_silent_when_there_is_nothing_at_all(self):
        self._run([])
        self.assertEqual(self.sent, [])

    def test_names_what_is_due(self):
        self._run([
            Assignment("Canvas", "MATH 218", "Homework 4", self.now + timedelta(days=1)),
            Assignment("Canvas", "MATH 220", "Homework 7", self.now + timedelta(hours=2)),
        ])
        self.assertEqual(len(self.sent), 1)
        title, message = self.sent[0]
        self.assertIn("2 due", title)
        # Soonest first.
        self.assertTrue(message.index("Homework 7") < message.index("Homework 4"), message)

    def test_finished_work_is_not_nagged_about(self):
        self._run([
            Assignment("Gradescope", "MATH 218", "Handed in", self.now + timedelta(days=1), "Submitted"),
        ])
        self.assertEqual(self.sent, [])

    def test_overdue_is_left_to_the_failure_path(self):
        self._run([Assignment("Canvas", "A", "late", self.now - timedelta(days=2))])
        self.assertEqual(self.sent, [])

    def test_long_lists_are_summarised(self):
        items = [
            Assignment("Canvas", "A", f"Task {i}", self.now + timedelta(days=1))
            for i in range(6)
        ]
        self._run(items)
        self.assertIn("+3 more", self.sent[0][1])

    def test_visible_runs_do_not_notify(self):
        args = self._Args()
        args.visible = True
        cfg = config.Config(raw=dict(CFG.raw, notify_within_days=2))
        self.main._notify_due(
            [Assignment("Canvas", "A", "soon", self.now + timedelta(days=1))],
            cfg, args, logging.getLogger("quiet"),
        )
        self.assertEqual(self.sent, [])


class PhonePush(unittest.TestCase):
    """ntfy sending. The topic name is the only credential, so the guard
    against sending to an unconfigured/blank topic matters."""

    def setUp(self):
        from bot import push

        self.push = push
        self.calls = []

        def fake_post(url, data=None, headers=None, timeout=None):
            self.calls.append({"url": url, "data": data, "headers": headers})

            class Response:
                ok = True
                status_code = 200
                text = ""

            return Response()

        self._original = push.requests.post
        push.requests.post = fake_post

    def tearDown(self):
        self.push.requests.post = self._original

    def _cfg(self, **over):
        raw = dict(CFG.raw)
        raw["push"] = {"enabled": True, "server": "https://ntfy.sh", "topic": "secret-topic"}
        raw.update(over)
        return config.Config(raw=raw)

    def test_sends_to_the_topic(self):
        self.assertTrue(self.push.send(self._cfg(), "Due soon", "Homework 7"))
        self.assertEqual(self.calls[0]["url"], "https://ntfy.sh/secret-topic")
        self.assertEqual(self.calls[0]["data"], b"Homework 7")
        self.assertEqual(self.calls[0]["headers"]["Title"], "Due soon")

    def test_silent_when_not_enabled(self):
        cfg = config.Config(raw=dict(CFG.raw, push={"enabled": False, "topic": "t"}))
        self.assertFalse(self.push.send(cfg, "x", "y"))
        self.assertEqual(self.calls, [])

    def test_silent_when_the_topic_is_blank(self):
        # Posting to a blank topic would hit the ntfy root, not a private feed.
        cfg = config.Config(raw=dict(CFG.raw, push={"enabled": True, "topic": "   "}))
        self.assertFalse(self.push.send(cfg, "x", "y"))
        self.assertEqual(self.calls, [])

    def test_tap_through_link_is_attached(self):
        cfg = self._cfg(sheet_link="https://docs.google.com/spreadsheets/d/abc/edit")
        self.push.send(cfg, "Due soon", "Homework 7")
        self.assertEqual(
            self.calls[0]["headers"]["Click"], "https://docs.google.com/spreadsheets/d/abc/edit"
        )

    def test_no_click_header_without_a_link(self):
        self.push.send(self._cfg(), "Due soon", "Homework 7")
        self.assertNotIn("Click", self.calls[0]["headers"])

    def test_non_ascii_titles_do_not_break_the_headers(self):
        # ntfy sends headers as latin-1; an en dash would be rejected.
        self.push.send(self._cfg(), "2 due — don’t forget", "body")
        self.calls[0]["headers"]["Title"].encode("ascii")

    def test_body_keeps_its_unicode(self):
        self.push.send(self._cfg(), "t", "Homework · Sec 4.7")
        self.assertIn("Sec 4.7", self.calls[0]["data"].decode("utf-8"))

    def test_a_network_failure_never_raises(self):
        def boom(*a, **k):
            raise self.push.requests.RequestException("no network")

        self.push.requests.post = boom
        self.assertFalse(self.push.send(self._cfg(), "x", "y"))

    def test_priority_and_tags_are_passed_through(self):
        self.push.send(self._cfg(), "x", "y", priority="high", tags="warning")
        self.assertEqual(self.calls[0]["headers"]["Priority"], "high")
        self.assertEqual(self.calls[0]["headers"]["Tags"], "warning")


class Snapshots(unittest.TestCase):
    """A source captured once and replayed from disk, for sites not worth
    signing into every hour."""

    def setUp(self):
        import tempfile

        from bot import paths, snapshot

        self._tmp = tempfile.TemporaryDirectory()
        self._original = paths.STATE
        paths.STATE = Path(self._tmp.name)
        snapshot.FOLDER = paths.STATE / "snapshots"
        self.snapshot = snapshot
        self.now = datetime.now(TZ)

    def tearDown(self):
        from bot import paths

        paths.STATE = self._original
        self.snapshot.FOLDER = self._original / "snapshots"
        self._tmp.cleanup()

    def _items(self):
        return [
            Assignment("Pearson", "Math 220", "Homework Sec 4.7",
                       parse_due("2026-10-18T23:59:00-05:00", TZ), "", "", "https://x/1"),
            Assignment("Pearson", "Math 220", "Homework Sec 5.2",
                       parse_due("2026-10-25T23:59:00-05:00", TZ)),
        ]

    def test_round_trip_keeps_every_field(self):
        self.snapshot.save("pearson", self._items(), TZ)
        loaded, captured = self.snapshot.load("pearson", TZ)

        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].title, "Homework Sec 4.7")
        self.assertEqual(loaded[0].course, "Math 220")
        self.assertEqual(loaded[0].url, "https://x/1")
        self.assertEqual(loaded[0].due.strftime("%Y-%m-%d %H:%M"), "2026-10-18 23:59")
        self.assertIsNotNone(captured)

    def test_keys_survive_the_round_trip(self):
        """The whole point: a replayed row must update the sheet row the live
        scrape created, not add a second one."""
        original = self._items()
        self.snapshot.save("pearson", original, TZ)
        loaded, _ = self.snapshot.load("pearson", TZ)
        self.assertEqual([a.key for a in loaded], [a.key for a in original])

    def test_countdowns_are_recomputed_not_stored(self):
        # Saved days_left would go stale; due dates are re-parsed each load.
        self.snapshot.save("pearson", self._items(), TZ)
        loaded, _ = self.snapshot.load("pearson", TZ)
        expected = days_until(parse_due("2026-10-18T23:59:00-05:00", TZ))
        self.assertEqual(loaded[0].days_left(), expected)

    def test_missing_snapshot_is_empty_not_an_error(self):
        loaded, captured = self.snapshot.load("nothing-here", TZ)
        self.assertEqual(loaded, [])
        self.assertIsNone(captured)

    def test_corrupt_snapshot_is_empty_not_an_error(self):
        self.snapshot.FOLDER.mkdir(parents=True, exist_ok=True)
        self.snapshot.path_for("broken").write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.snapshot.load("broken", TZ)[0], [])

    def test_undated_items_survive(self):
        self.snapshot.save("pearson", [Assignment("Pearson", "c", "no deadline")], TZ)
        loaded, _ = self.snapshot.load("pearson", TZ)
        self.assertIsNone(loaded[0].due)

    def test_age_in_days(self):
        self.assertEqual(self.snapshot.age_days(self.now - timedelta(days=50), TZ), 50)
        self.assertIsNone(self.snapshot.age_days(None, TZ))

    def test_saving_again_replaces_the_previous_capture(self):
        self.snapshot.save("pearson", self._items(), TZ)
        self.snapshot.save("pearson", [Assignment("Pearson", "c", "only one")], TZ)
        loaded, _ = self.snapshot.load("pearson", TZ)
        self.assertEqual([a.title for a in loaded], ["only one"])


class SourceMode(unittest.TestCase):
    def test_defaults_to_live(self):
        from bot.sources import REGISTRY

        self.assertEqual(REGISTRY["pearson"](CFG, visible=False).mode, "live")

    def test_reads_the_configured_mode(self):
        from bot.sources import REGISTRY

        cfg = config.Config(raw=dict(CFG.raw, sources={"pearson": {"mode": "SNAPSHOT"}}))
        self.assertEqual(REGISTRY["pearson"](cfg, visible=False).mode, "snapshot")
