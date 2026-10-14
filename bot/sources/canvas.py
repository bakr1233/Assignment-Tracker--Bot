"""Canvas.

Uses the Canvas REST API. With a token that needs no browser at all.

Without one - some schools stop students generating their own tokens - we
log in with Playwright, second factor and all, then call the very same
API endpoints through the browser session, because Canvas accepts session
cookies on /api/v1. Still far steadier than scraping its pages.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from .. import browser, creds
from ..models import Assignment
from ..util import fmt_points, parse_due
from .base import Source

PER_PAGE = 100
TIMEOUT = 45

#: Canvas's own form first, then Microsoft Entra / Shibboleth behind the SSO
#: hop, which is where a school's sign-on lives.
USERNAME_SELECTORS = (
    "input#pseudonym_session_unique_id",
    "input[name='pseudonym_session[unique_id]']",
    "input[name='loginfmt']",
    "input#i0116",
    "input#j_username",
    "input[name='j_username']",
    "input#username",
    "input[name='username']",
    "input[type='email']",
)
PASSWORD_SELECTORS = (
    "input#pseudonym_session_password",
    "input[name='pseudonym_session[password]']",
    "input[name='passwd']",
    "input#i0118",
    "input#j_password",
    "input[name='j_password']",
    "input[type='password']",
)


class Canvas(Source):
    name = "canvas"
    label = "Canvas"
    # Shared with Blackboard: at a school that uses one single sign-on for
    # both, approving Duo for one signs the bot into the other too.
    profile = "sso"

    def __init__(self, cfg, visible) -> None:
        super().__init__(cfg, visible)
        self.creds = creds.read("canvas")
        self.base = (self.creds.get("url") or "").rstrip("/")
        # Only the browser fallback can need a human.
        self.interactive = not self.creds.get("token")

    def ready(self) -> Tuple[bool, str]:
        if not self.base:
            return False, "no url= in credentials/canvas.txt"
        if not self.base.startswith("http"):
            return False, f"url= should start with https:// (got {self.base!r})"
        if self.creds.get("token"):
            return True, ""
        if creds.has(self.creds, "username", "password"):
            return True, ""
        return False, "need either token= or username= and password= in credentials/canvas.txt"

    def fetch(self) -> List[Assignment]:
        if self.creds.get("token"):
            self.log.info("   Using the API token.")
            return self._with_token()
        self.log.info("   No token, logging in with a browser instead.")
        return self._with_browser()

    # -- token path ------------------------------------------------------

    def _with_token(self) -> List[Assignment]:
        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {self.creds['token']}",
                "Accept": "application/json",
            }
        )

        def get(url: str, params: Optional[Dict[str, Any]] = None):
            response = session.get(url, params=params, timeout=TIMEOUT)
            if response.status_code == 401:
                raise RuntimeError(
                    "Canvas rejected the token (401). Make a fresh one under "
                    "Account -> Settings -> New Access Token."
                )
            response.raise_for_status()
            return response.json(), _next_link(response.headers.get("Link", ""))

        return self._collect(get)

    # -- browser path ----------------------------------------------------

    def _with_browser(self) -> List[Assignment]:
        with browser.context(self.profile_name, self.cfg, self.visible) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            self._login(ctx, page)

            def get(url: str, params: Optional[Dict[str, Any]] = None):
                full = url
                if params:
                    query = "&".join(
                        f"{k}={v}" for k, v in _flatten(params)
                    )
                    full = f"{url}{'&' if '?' in url else '?'}{query}"
                response = ctx.request.get(full, headers={"Accept": "application/json"})
                if not response.ok:
                    raise RuntimeError(
                        f"Canvas returned HTTP {response.status} for {full}"
                    )
                return response.json(), _next_link(response.headers.get("link", ""))

            return self._collect(get)

    def _login(self, ctx, page) -> None:
        if self._signed_in(ctx):
            self.log.info("   Still signed in from last time.")
            return

        # As with Blackboard, the single sign-on session usually carries this
        # through with no interaction, so a headless run must still try.
        page.goto(f"{self.base}/login", wait_until="domcontentloaded")

        # Opening the page can be enough on its own when another site has
        # already established the single sign-on session in this profile.
        if self._signed_in(ctx):
            self.log.info("   Signed in through the shared single sign-on session.")
            return

        # Schools often bounce an unauthenticated visitor to a landing page
        # with no form on it at all; the way in is its /login/saml link.
        if not browser.first_visible(page, *USERNAME_SELECTORS, timeout=4_000):
            browser.follow_sso_link(page, self.log, self.options().get("sso_link"))
            if self._signed_in(ctx):
                self.log.info("   Signed in through the shared single sign-on session.")
                return

        if not browser.fill_credentials(
            page,
            self.creds["username"],
            self.creds["password"],
            USERNAME_SELECTORS,
            PASSWORD_SELECTORS,
            self.log,
            signed_in=lambda: self._signed_in(ctx),
        ):
            browser.dump(page, "canvas-login")
            raise browser.LoginRequired(
                "Could not complete the Canvas login. See debug/canvas-login.png."
            )

        seconds = self.cfg.duo_wait_seconds if self.visible else 30
        if not browser.wait_for_duo(
            page,
            lambda: self._signed_in(ctx),
            seconds,
            self.log,
            announce=self.visible,
        ):
            browser.dump(page, "canvas-login-failed")
            raise browser.LoginRequired(
                "Canvas sign-in did not complete. "
                + (
                    "See debug/canvas-login-failed.png."
                    if self.visible
                    else "Run run_visible.command once to sign in again."
                )
            )

    def _signed_in(self, ctx) -> bool:
        """Canvas accepts session cookies on /api/v1, so this doubles as proof
        that the API calls below will work."""
        try:
            response = ctx.request.get(
                f"{self.base}/api/v1/users/self", headers={"Accept": "application/json"}
            )
        except Exception:  # noqa: BLE001
            return False
        if not response.ok:
            return False
        try:
            return bool(response.json().get("id"))
        except Exception:  # noqa: BLE001 - an HTML login page
            return False

    # -- shared ----------------------------------------------------------

    def _collect(self, get) -> List[Assignment]:
        courses = list(
            _pages(
                get,
                f"{self.base}/api/v1/courses",
                {
                    "enrollment_state": "active",
                    "enrollment_type": "student",
                    "per_page": PER_PAGE,
                },
            )
        )
        self.log.info("   %d active course(s).", len(courses))

        out: List[Assignment] = []
        for course in courses:
            course_name = course.get("name") or course.get("course_code") or "Canvas course"
            course_id = course.get("id")
            if course_id is None:
                continue
            try:
                items = list(
                    _pages(
                        get,
                        f"{self.base}/api/v1/courses/{course_id}/assignments",
                        {
                            "per_page": PER_PAGE,
                            "include[]": ["submission"],
                            "order_by": "due_at",
                        },
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one bad course is not fatal
                self.log.warning("   Skipped %s: %s", course_name, exc)
                continue

            for item in items:
                out.append(self._to_assignment(course_name, item))
            self.log.debug("   %s: %d assignment(s)", course_name, len(items))

        return out

    def _to_assignment(self, course: str, item: Dict[str, Any]) -> Assignment:
        due = parse_due(item.get("due_at"), self.cfg.tz)
        return Assignment(
            source=self.label,
            course=course,
            title=item.get("name") or "(untitled)",
            due=due,
            status=_status(item, due, self.cfg.tz),
            points=fmt_points(item.get("points_possible")),
            url=item.get("html_url") or "",
        )


def _status(item: Dict[str, Any], due, tz) -> str:
    from datetime import datetime

    submission = item.get("submission") or {}
    state = submission.get("workflow_state")
    score = submission.get("score")

    if state == "graded" and score is not None:
        total = fmt_points(item.get("points_possible"))
        got = fmt_points(score)
        return f"Graded {got}/{total}" if total else f"Graded {got}"
    if submission.get("submitted_at"):
        return "Late" if submission.get("late") else "Submitted"
    if submission.get("missing"):
        return "Missing"
    if due and due < datetime.now(tz):
        return "Past due"
    if item.get("locked_for_user"):
        return "Locked"
    return "Open"


def _pages(get, url: str, params: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Walk Canvas's Link-header pagination."""
    next_url: Optional[str] = url
    next_params: Optional[Dict[str, Any]] = params
    while next_url:
        data, following = get(next_url, next_params)
        if isinstance(data, dict) and data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        if not isinstance(data, list):
            return
        yield from data
        next_url, next_params = following, None


def _next_link(header: str) -> Optional[str]:
    for part in (header or "").split(","):
        chunk = part.strip()
        if chunk.endswith('rel="next"') and "<" in chunk:
            return chunk[chunk.index("<") + 1 : chunk.index(">")]
    return None


def _flatten(params: Dict[str, Any]):
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            for v in value:
                yield key, v
        else:
            yield key, value
