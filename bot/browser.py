"""Shared Playwright setup.

Sites that share a single sign-on share a profile folder, so cookies - and
Duo's "remember this device" - survive between runs, and one Duo approval
covers every site behind that same login. That is what lets the scheduled 8 AM
run work without anyone touching a phone.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from .config import Config
from .paths import DEBUG, PROFILES
from .util import log


class LoginRequired(RuntimeError):
    """Raised when a site needs a human: a password prompt or a Duo push."""


#: Set by --debug-dump. Saves every page before the browser closes.
FORCE_DUMP = False

#: Saying yes to these is what keeps the scheduled runs from needing a phone.
#: The last two are Entra's "Stay signed in?" prompt, which appears after the
#: second factor and blocks the redirect back until it is answered.
DUO_TRUST_SELECTORS = (
    "#trust-browser-button",
    "button:has-text('Yes, trust browser')",
    "button:has-text('Yes, this is my device')",
    "#remember_me_label_text",
    "input#idSIButton9[value='Yes']",
    "#idSIButton9",
)


@contextmanager
def context(profile: str, cfg: Config, visible: bool) -> Iterator["BrowserContext"]:  # noqa: F821
    from playwright.sync_api import sync_playwright

    profile_dir = PROFILES / profile
    profile_dir.mkdir(parents=True, exist_ok=True)

    def launch(pw):
        return pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=not visible,
            slow_mo=cfg.slow_mo_ms,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )

    with sync_playwright() as pw:
        try:
            ctx = launch(pw)
        except Exception as exc:  # noqa: BLE001
            if not _is_profile_locked(exc):
                raise
            # A run that ended badly can leave its window open, and Chromium
            # allows only one process per profile directory. Close the old one
            # and try again rather than making this the user's problem.
            log("browser").info(
                "   A browser from an earlier run still had this profile open; closing it."
            )
            if not release_profile(profile_dir):
                raise LoginRequired(
                    "A browser window from an earlier run is still using "
                    f"{profile_dir.name}. Close any 'Chrome for Testing' windows and "
                    "run it again."
                ) from exc
            ctx = launch(pw)

        ctx.set_default_timeout(30_000)
        try:
            yield ctx
        finally:
            if FORCE_DUMP:
                for index, page in enumerate(ctx.pages):
                    dump(page, f"{profile}-final-{index}")
            try:
                ctx.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass


def _is_profile_locked(exc: Exception) -> bool:
    text = str(exc).lower()
    return "existing browser session" in text or "already in use" in text


def release_profile(profile_dir: Path) -> bool:
    """Close any browser still holding this profile directory.

    Matches on the profile path itself, which appears in the browser's
    --user-data-dir argument, so this can only ever hit a browser this bot
    started - never the user's own Chrome.
    """
    pids = _pids_using(profile_dir)
    if not pids:
        return False

    for pid in pids:
        _signal(pid, signal.SIGTERM)

    for _ in range(20):
        remaining = _pids_using(profile_dir)
        if not remaining:
            return True
        time.sleep(0.25)

    for pid in _pids_using(profile_dir):
        _signal(pid, signal.SIGKILL)

    time.sleep(0.5)
    return not _pids_using(profile_dir)


def _pids_using(profile_dir: Path):
    try:
        found = subprocess.run(
            ["pgrep", "-f", str(profile_dir)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return []

    mine = os.getpid()
    return [
        int(line)
        for line in found.stdout.split()
        if line.isdigit() and int(line) not in (mine, os.getppid())
    ]


def _signal(pid: int, sig) -> None:
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def first_visible(page, *selectors: str, timeout: int = 8_000) -> Optional[str]:
    """Return the first selector that is actually on screen, or None.

    Course sites reskin their login pages regularly, so every form field is
    looked up through a list of candidates rather than one brittle selector.

    `timeout` is the budget for the whole list, not for each entry. Waiting on
    each selector in turn meant a page with seven candidates could burn seven
    full timeouts - two minutes, in a real run - before finding the one that
    worked. Here every candidate is checked on each pass instead.
    """
    deadline = time.monotonic() + timeout / 1000.0

    while True:
        for selector in selectors:
            try:
                element = page.query_selector(selector)
                if element and element.is_visible():
                    return selector
            except Exception:  # noqa: BLE001 - detached node, keep looking
                continue

        if time.monotonic() >= deadline:
            return None

        try:
            page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001 - page went away
            return None


#: Buttons that submit a login step. Microsoft Entra reuses #idSIButton9 for
#: "Next", "Sign in" and "Yes", which is why it leads.
SUBMIT_SELECTORS = (
    "#idSIButton9",
    "#submitBttn",
    "input[type='submit']",
    "button[type='submit']",
    "button:has-text('Sign in')",
    "button:has-text('Next')",
    "button:has-text('Log in')",
    "#submit_button",
    "#entry-login",
)


#: Consent banners sit over the form and can re-render it underneath.
COOKIE_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "#accept-recommended-btn-handler",
    "button:has-text('Allow and Continue')",
    "button:has-text('Accept All')",
    "button:has-text('Allow All')",
    "button:has-text('Accept all cookies')",
)


def dismiss_cookie_banner(page, logger) -> bool:
    for selector in COOKIE_SELECTORS:
        try:
            page.click(selector, timeout=1_500)
        except Exception:  # noqa: BLE001 - usually there is no banner
            continue
        logger.debug("Dismissed a cookie banner (%s)", selector)
        try:
            page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            pass
        return True
    return False


def set_field(page, selector: str, value: str) -> bool:
    """Put a value in a field and make sure it stayed there.

    Pearson's sign-in re-renders after the first keystrokes, which silently
    emptied the username again - the run then failed with "Username is
    required" and a password that had been typed in fine.
    """
    for _ in range(3):
        try:
            page.fill(selector, "")
            page.fill(selector, value)
            if (page.input_value(selector) or "").strip() == value.strip():
                return True
        except Exception:  # noqa: BLE001 - try typing instead
            pass

        try:
            page.click(selector)
            page.type(selector, value, delay=25)
            if (page.input_value(selector) or "").strip() == value.strip():
                return True
        except Exception:  # noqa: BLE001
            pass

        try:
            page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            return False

    return False


def click_submit(page) -> bool:
    """Press the button that moves the login on.

    Entra will not submit on Enter alone - a run got as far as filling the
    password, pressed Enter, and sat on the same screen until it timed out.
    """
    for selector in SUBMIT_SELECTORS:
        try:
            page.click(selector, timeout=1_200)
            return True
        except Exception:  # noqa: BLE001 - try the next shape
            continue

    try:
        page.keyboard.press("Enter")
    except Exception:  # noqa: BLE001
        pass
    return False


def pick_account(page, username: str, logger) -> bool:
    """Click through Microsoft's "Pick an account" tile screen.

    Once one site has signed in, Entra stops asking for an email and offers
    the remembered account instead - so the second site finds no username box
    at all, only a list of tiles.
    """
    selectors = (
        f'div[data-test-id="{username}"]',
        f'#tilesHolder [data-test-id="{username}"]',
        f'#tilesHolder div[role="button"]:has-text("{username}")',
        f'#tilesHolder [role="button"]:has-text("{username}")',
    )
    for selector in selectors:
        try:
            page.click(selector, timeout=1_500)
        except Exception:  # noqa: BLE001 - no picker on this page
            continue
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:  # noqa: BLE001
            pass
        logger.info("   Picked the remembered account.")
        return True
    return False


def fill_credentials(
    page,
    username: str,
    password: str,
    username_selectors,
    password_selectors,
    logger,
    timeout: int = 15_000,
    signed_in: Optional[Callable[[], bool]] = None,
) -> bool:
    """Work through whichever login screen is in front of us.

    Microsoft Entra, common at universities, has three shapes: an
    email step followed by a password step, a single form with both, or - once
    another site has signed in - an account picker and nothing else. Picking
    the remembered account can land straight on the far side of the login,
    which is what `signed_in` is for.
    """
    if signed_in and signed_in():
        return True

    # A banner over the form swallows clicks and can re-render the inputs.
    dismiss_cookie_banner(page, logger)

    pick_account(page, username, logger)
    if signed_in and signed_in():
        return True

    user_field = first_visible(page, *username_selectors, timeout=timeout)
    if user_field:
        set_field(page, user_field, username)

    # If the password box is already here this is a one-stage form; otherwise
    # the username has to be submitted to reach the password step.
    password_field = first_visible(page, *password_selectors, timeout=2_000)
    if not password_field:
        if not user_field:
            return bool(signed_in and signed_in())
        click_submit(page)
        logger.debug("Submitted the username, waiting for the password step")
        password_field = first_visible(page, *password_selectors, timeout=timeout)

    if not password_field:
        return bool(signed_in and signed_in())

    set_field(page, password_field, password)

    # Filling the password can re-render the form and blank the username, so
    # check it one more time before submitting.
    if user_field:
        try:
            if (page.input_value(user_field) or "").strip() != username.strip():
                logger.debug("Username was cleared by a re-render; filling it again")
                set_field(page, user_field, username)
        except Exception:  # noqa: BLE001 - field gone, nothing to repair
            pass

    click_submit(page)
    logger.info("   Sent the credentials.")
    return True


#: Schools put their real login behind a "sign in with your university
#: account" hop. These are the shapes that hop takes, most specific first.
SSO_ENTRY_SELECTORS = (
    "a[href*='/login/saml']",
    "a[href*='/login/cas']",
    "a[href*='/login/oauth']",
    "a:has-text('Shibboleth')",
    "button:has-text('Shibboleth')",
    "a:has-text('Sign in with third-party account')",
    "#redirectProvidersDropdownButton",
    "a:has-text('Sign In')",
    "a:has-text('Log In')",
)


def follow_sso_link(page, logger, extra: Optional[str] = None) -> bool:
    """Click through to the institution's real login page.

    Returns True if something was clicked. Blackboard and Canvas commonly show
    a landing page first - Blackboard behind a "<School> Login" provider link,
    Canvas behind a /login/saml link - so the username box simply is not on the
    page the bot lands on.
    """
    selectors = ([extra] if extra else []) + list(SSO_ENTRY_SELECTORS)

    for selector in selectors:
        try:
            page.click(selector, timeout=2_500)
        except Exception:  # noqa: BLE001 - most of these will not be present
            continue

        try:
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception:  # noqa: BLE001
            pass

        # A dropdown trigger only reveals the provider list; the provider
        # itself still has to be clicked.
        if "Dropdown" in selector or "third-party" in selector:
            for inner in ("a:has-text('Shibboleth')", "button:has-text('Shibboleth')"):
                try:
                    page.click(inner, timeout=2_500)
                    page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    break
                except Exception:  # noqa: BLE001
                    continue

        logger.info("   Followed the single sign-on link (%s).", selector)
        return True

    return False


def wait_for_duo(
    page,
    signed_in: Callable[[], bool],
    seconds: int,
    logger,
    grace: int = 6,
    announce: bool = True,
) -> bool:
    """Poll until the login actually completes, clicking Duo's trust button.

    Polling an "am I signed in yet" check rather than waiting on a particular
    page or URL, because the Shibboleth and Duo screens differ per site and get
    reskinned; whether the session works is the only thing that matters.

    Stays quiet for the first few seconds, so a login that needs no second
    factor does not print a confusing instruction to reach for your phone.
    """
    deadline = time.monotonic() + seconds
    quiet_until = time.monotonic() + grace
    announced = False
    trusted = False

    while time.monotonic() < deadline:
        if signed_in():
            if announced:
                logger.info("   Signed in.")
            return True

        if announce and not announced and time.monotonic() >= quiet_until:
            logger.info("")
            logger.info("   >>> Approve the Duo push on your phone now. <<<")
            logger.info("   Waiting up to %d seconds...", seconds)
            announced = True

        if not trusted:
            for selector in DUO_TRUST_SELECTORS:
                try:
                    page.click(selector, timeout=1_000)
                    trusted = True
                    logger.info("   Told Duo to remember this browser.")
                    break
                except Exception:  # noqa: BLE001 - button may not be there yet
                    continue

        time.sleep(2)

    return False


def dump(page, label: str) -> None:
    """Save HTML + a screenshot so a broken selector can be diagnosed later."""
    DEBUG.mkdir(exist_ok=True)
    try:
        (DEBUG / f"{label}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(DEBUG / f"{label}.png"), full_page=True)
        log("browser").info("   Saved debug/%s.html and .png", label)
    except Exception as exc:  # noqa: BLE001
        log("browser").debug("Could not write debug dump %s: %s", label, exc)
