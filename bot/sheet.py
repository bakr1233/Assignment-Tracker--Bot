"""Talks to the Apps Script web app that writes into the Google Sheet."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List

import requests

from .config import Config
from .models import Assignment
from .util import log, term_label

TIMEOUT = 60


class SheetError(RuntimeError):
    pass


def ping(cfg: Config) -> Dict[str, Any]:
    """Cheap round trip to prove the URL and secret are right."""
    return _post(cfg, {"action": "ping", "tab": cfg.sheet_tab})


def push(cfg: Config, assignments: List[Assignment]) -> Dict[str, Any]:
    payload = {
        "action": "upsert",
        "tab": cfg.sheet_tab,
        "data_tab": cfg.data_tab,
        "display_within_days": cfg.display_within_days,
        "overdue_grace_days": cfg.overdue_grace_days,
        "done_linger_minutes": cfg.done_linger_minutes,
        "adopt": cfg.adopt_existing_tab,
        "term": term_label(datetime.now(cfg.tz)),
        "items": [a.to_payload() for a in assignments],
    }
    return _post(cfg, payload)


def _post(cfg: Config, body: Dict[str, Any]) -> Dict[str, Any]:
    if not cfg.sheet_url:
        raise SheetError("No sheet_url in config.json yet.")
    if not cfg.sheet_url.endswith("/exec"):
        raise SheetError(
            f"sheet_url should end in /exec, got: {cfg.sheet_url}\n"
            "Copy the Web app URL from Deploy -> Manage deployments, not the editor URL."
        )

    body = dict(body, secret=cfg.shared_secret)

    try:
        # Apps Script answers with a 302 to script.googleusercontent.com;
        # requests follows that for us. Content-Type stays text/plain so the
        # redirect is not turned into a CORS preflight.
        response = requests.post(
            cfg.sheet_url,
            data=json.dumps(body),
            headers={"Content-Type": "text/plain;charset=utf-8"},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise SheetError(f"Could not reach the sheet: {exc}") from exc

    if response.status_code != 200:
        raise SheetError(
            f"The sheet answered with HTTP {response.status_code}. "
            "Check that 'Who has access' is set to Anyone on the deployment."
        )

    text = response.text.strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        raise SheetError(explain_html(text)) from None

    if not result.get("ok"):
        raise SheetError(f"The script refused the request: {result.get('error', result)}")

    log("sheet").debug("Sheet replied: %s", result)
    return result


def explain_html(text: str) -> str:
    """Apps Script reports its own failures as HTML pages. Turn the ones that
    actually happen into the fix, rather than showing someone raw markup."""
    low = text.lower()

    if "script function not found" in low:
        return (
            "The deployed version has no doPost in it - it was deployed before the "
            "code was saved, so it captured the empty starter project.\n"
            "Fix, keeping the same URL:\n"
            "  1. In Apps Script, check the code is really there, then click Save.\n"
            "  2. Deploy -> Manage deployments.\n"
            "  3. Click the pencil (Edit) on the deployment.\n"
            "  4. Set Version to 'New version'.  <- this is the step that matters\n"
            "  5. Deploy.\n"
            "Editing and saving alone never changes what a deployment serves; it "
            "keeps serving the version it was created from."
        )

    if "accounts.google.com" in low or "sign in" in low or "google drive" in low:
        return (
            "The sheet returned a Google sign-in page, so the deployment is private.\n"
            "Deploy -> Manage deployments -> pencil -> set 'Who has access' to "
            "Anyone -> Deploy."
        )

    if "exception" in low or "error" in low:
        detail = re.sub(r"<[^>]+>", " ", text)
        detail = re.sub(r"\s+", " ", detail).strip()
        return (
            "The script itself threw an error. Open Apps Script -> Executions to see "
            f"the stack trace.\nPage said: {detail[:300]}"
        )

    return f"Unexpected answer from the sheet: {text[:300]}"
