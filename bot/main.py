"""Command line entry point.

    python -m bot.main --visible        first run, or whenever Duo is needed
    python -m bot.main                  headless, what the schedule runs
    python -m bot.main --test-sheet     just prove the Google Sheet link works
    python -m bot.main --only canvas    one source
"""

from __future__ import annotations

import argparse
import secrets
import shutil
import sys
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from . import browser, config, notify, paths, sheet, state
from .models import Assignment
from .sources import REGISTRY
from .util import fmt_due_display, setup_logging, truncate


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])

    paths.ensure_dirs()
    log = setup_logging(verbose=args.verbose or args.debug_dump)
    browser.FORCE_DUMP = args.debug_dump

    if args.init_config:
        return _init_config(log)

    if args.set_sheet_url:
        return _set_sheet_url(args.set_sheet_url, log)

    cfg = config.load()
    if not paths.CONFIG.exists():
        log.warning("No config.json yet - using config.example.json. Run --init-config.")

    log.info("")
    log.info("Assignment tracker  -  %s", datetime.now(cfg.tz).strftime("%a %d %b %Y, %H:%M"))
    log.info("=" * 72)

    if args.test_push:
        return _test_push(cfg, log)

    if args.test_sheet:
        return _test_sheet(cfg, log)

    assignments, failures = _run_sources(cfg, args, log)

    assignments = _filter_and_sort(assignments, cfg)
    _print_table(assignments, log)
    _print_changes(assignments, log)

    exit_code = 0

    if not assignments:
        log.info("")
        log.info("Nothing to write.")
    elif args.dry_run:
        log.info("")
        log.info("--dry-run, so the sheet was left alone.")
    elif not cfg.sheet_configured:
        log.info("")
        log.warning(
            "The sheet is not configured yet (sheet_url / shared_secret in config.json), "
            "so nothing was written."
        )
        exit_code = 1
    else:
        try:
            result = sheet.push(cfg, assignments)
            log.info("")
            log.info(
                "Sheet updated: %s added, %s changed, %s already current.",
                result.get("added", "?"),
                result.get("updated", "?"),
                result.get("unchanged", "?"),
            )
            for note in result.get("notes") or []:
                log.info("%s", note)
            if "shown" in result:
                log.info(
                    "Tracker shows %s due within %d days; %s waiting in %s.",
                    result.get("shown"),
                    cfg.display_within_days,
                    result.get("hidden"),
                    cfg.data_tab,
                )
        except sheet.SheetError as exc:
            log.error("")
            log.error("Could not write to the sheet: %s", exc)
            _notify_failure("Sheet write failed", str(exc), args, cfg)
            exit_code = 1

    if not args.dry_run and assignments:
        state.save(assignments)

    if not args.dry_run:
        _notify_due(assignments, cfg, args, log)

    if failures:
        exit_code = exit_code or 1
        log.info("")
        log.warning("Sources that did not finish:")
        for name, reason in failures:
            log.warning("  %-12s %s", name, reason)

        # Only mention what has not already been reported. A source that is
        # stuck for the same reason every run must not keep interrupting.
        fresh = [(n, r) for n, r in failures if state.should_alert(n, r)]
        if fresh:
            _notify_failure(
                "Assignment bot needs you",
                "; ".join(f"{n}: {truncate(r, 60)}" for n, r in fresh),
                args,
                cfg,
            )
        else:
            log.info("  (already reported, so no notification)")

    log.info("")
    return exit_code


# -- running sources -----------------------------------------------------


def _run_sources(cfg, args, log) -> Tuple[List[Assignment], List[Tuple[str, str]]]:
    wanted = args.only or list(REGISTRY)
    collected: List[Assignment] = []
    failures: List[Tuple[str, str]] = []

    for name in wanted:
        if name not in REGISTRY:
            log.warning("Unknown source %r - known: %s", name, ", ".join(REGISTRY))
            continue
        if not args.only and not cfg.enabled(name):
            log.info("%-12s disabled in config.json", name)
            continue

        source = REGISTRY[name](cfg, args.visible)
        ok, why = source.ready()
        if not ok:
            log.info("%-12s skipped - %s", name, why)
            continue

        if source.interactive and not args.visible:
            log.info("%-12s (headless - will use the saved session)", name)
        log.info("%-12s working...", name)

        try:
            items = source.fetch()
        except browser.LoginRequired as exc:
            log.warning("   Needs you: %s", exc)
            failures.append((name, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - one site must not sink the run
            log.error("   Failed: %s", _short(exc))
            log.debug("%s", traceback.format_exc())
            failures.append((name, _short(exc)))
            continue

        log.info("   Found %d.", len(items))
        state.clear_alert(name)
        collected.extend(items)

    return collected, failures


def _short(exc: Exception) -> str:
    """One readable line. Playwright errors carry a fifty-line call log with
    the whole Chromium command line in it, which buries the actual problem."""
    first = str(exc).strip().splitlines()
    message = first[0] if first else exc.__class__.__name__
    return truncate(message, 200)


def _filter_and_sort(assignments: List[Assignment], cfg) -> List[Assignment]:
    """Narrow to what is actually worth looking at, then order it.

    The sheet is a to-do list, not an archive, so by default it leaves out
    anything with no due date (finished exams, mostly) and anything already
    past and already handed in. Past work that was *never* handed in stays,
    because that is the case worth seeing.
    """
    now = datetime.now(cfg.tz)
    window_start = now - timedelta(days=cfg.lookback_days)
    window_end = now + timedelta(days=cfg.lookahead_days)

    kept: List[Assignment] = []
    position: Dict[str, int] = {}

    for a in assignments:
        if a.due is None:
            if not cfg.include_undated:
                continue
        else:
            if not (window_start <= a.due <= window_end):
                continue
            if a.due < now and a.is_done and not cfg.include_completed_past:
                continue

        # The same assignment can surface from two sources. Keep one row, and
        # prefer the copy that actually says something about its status.
        index = position.get(a.key)
        if index is None:
            position[a.key] = len(kept)
            kept.append(a)
        elif not kept[index].status and a.status:
            kept[index] = a

    kept.sort(key=lambda a: (a.due is None, a.due or now, a.course, a.title))
    return kept


# -- output --------------------------------------------------------------


def _print_table(assignments: List[Assignment], log) -> None:
    log.info("")
    if not assignments:
        log.info("No assignments found in the date window.")
        return

    header = (
        f"{'DUE':<21}  {'LEFT':>6}  {'COURSE':<20}  {'ASSIGNMENT':<36}  "
        f"{'STATUS':<16}  SOURCE"
    )
    log.info(header)
    log.info("-" * len(header))
    for a in assignments:
        remaining = a.days_left()
        log.info(
            "%-21s  %6s  %-20s  %-36s  %-16s  %s",
            fmt_due_display(a.due) or "no due date",
            _days_label(remaining),
            truncate(a.course, 20),
            truncate(a.title, 36),
            truncate(a.status, 16),
            truncate(a.source, 11),
        )
    log.info("-" * len(header))
    log.info("%d assignment(s).", len(assignments))


def _days_label(remaining) -> str:
    if remaining is None:
        return "-"
    if remaining == 0:
        return "today"
    if remaining < 0:
        return f"{-remaining}d ago"
    return f"{remaining}d"


def _print_changes(assignments: List[Assignment], log) -> None:
    previous = state.load()
    if not previous:
        return
    new, changed = state.diff(assignments, previous)
    if new:
        log.info("")
        log.info("New since the last run:")
        for a in new:
            log.info("  + %s  -  %s (%s)", fmt_due_display(a.due) or "no due date", a.title, a.course)
    if changed:
        log.info("")
        log.info("Changed since the last run:")
        for a in changed:
            log.info("  ~ %s  -  %s (%s)", fmt_due_display(a.due) or "no due date", a.title, a.course)


def _notify_due(assignments: List[Assignment], cfg, args, log) -> None:
    """Push what is due shortly to the desktop.

    The sheet only helps if you remember to open it. A scheduled run that
    found three things due tomorrow should say so without being asked.
    """
    if args.visible or args.no_notify:
        return   # you are watching the window; it already told you

    soon = []
    for a in assignments:
        if a.is_done:
            continue
        remaining = a.days_left()
        if remaining is None or remaining > cfg.notify_within_days or remaining < 0:
            continue
        soon.append((remaining, a))

    if not soon:
        return   # nothing imminent: stay quiet rather than cry wolf

    soon.sort(key=lambda pair: pair[0])
    parts = [f"{truncate(a.title, 38)} ({_days_label(d)})" for d, a in soon[:3]]
    more = len(soon) - len(parts)
    if more > 0:
        parts.append(f"+{more} more")

    title = f"{len(soon)} due in the next {cfg.notify_within_days} day" + (
        "" if cfg.notify_within_days == 1 else "s"
    )
    body = " · ".join(parts)
    notify.send(title, body, cfg=cfg, priority="default", tags="books")
    log.info("")
    log.info("Notified: %s - %s", title, body)


def _notify_failure(title: str, message: str, args, cfg) -> None:
    # When you ran it yourself you can see the window; a silent scheduled run
    # is the case that needs a nudge.
    if not args.visible:
        notify.send(title, message, cfg=cfg, priority="high", tags="warning")


# -- small commands ------------------------------------------------------


def _init_config(log) -> int:
    if paths.CONFIG.exists():
        log.info("config.json already exists - leaving it alone.")
        return 0
    shutil.copy(paths.CONFIG_EXAMPLE, paths.CONFIG)
    cfg = config.load()
    cfg.raw["shared_secret"] = secrets.token_urlsafe(24)
    config.save(cfg)
    log.info("Wrote config.json with a fresh shared secret:")
    log.info("")
    log.info("    %s", cfg.raw["shared_secret"])
    log.info("")
    log.info("Paste that same value into SHARED_SECRET at the top of sheet_receiver.gs.")
    return 0


def _set_sheet_url(url: str, log) -> int:
    url = url.strip()
    if not url.endswith("/exec"):
        log.error("That does not look like a Web app URL - it should end in /exec.")
        return 1
    if not paths.CONFIG.exists():
        shutil.copy(paths.CONFIG_EXAMPLE, paths.CONFIG)
    cfg = config.load()
    cfg.raw["sheet_url"] = url
    if cfg.raw.get("shared_secret") in (None, "", config.PLACEHOLDER_SECRET):
        cfg.raw["shared_secret"] = secrets.token_urlsafe(24)
        log.info("Also generated a shared secret: %s", cfg.raw["shared_secret"])
    config.save(cfg)
    log.info("Saved. Now run: python -m bot.main --test-sheet")
    return 0


def _test_push(cfg, log) -> int:
    if not cfg.push_enabled:
        log.error("No push topic in config.json. See the Notifications section of README.md.")
        return 1

    log.info("Sending a test notification to %s/%s ...", cfg.push_server, cfg.push_topic)
    from . import push

    ok = push.send(
        cfg,
        "Assignment tracker",
        "If this reached your phone, push notifications are working.",
        tags="white_check_mark",
    )
    if ok:
        log.info("Sent. It should arrive on any device subscribed to that topic.")
        return 0
    log.error("Could not send. See the message above.")
    return 1


def _test_sheet(cfg, log) -> int:
    if not cfg.sheet_url:
        log.error("No sheet_url in config.json. Run --set-sheet-url <your /exec URL> first.")
        return 1
    log.info("Pinging %s ...", cfg.sheet_url)
    try:
        result = sheet.ping(cfg)
    except sheet.SheetError as exc:
        log.error("Failed: %s", exc)
        return 1
    log.info("Works. The script is attached to: %s", result.get("spreadsheet", "(unnamed)"))
    log.info("Writing to tab: %s", result.get("tab", cfg.sheet_tab))
    return 0


# -- args ----------------------------------------------------------------


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bot", description="Collect assignments and write them to a Google Sheet."
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="show the browser so you can approve Duo and see what happens",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="SOURCE",
        help=f"run just these sources ({', '.join(REGISTRY)})",
    )
    parser.add_argument("--dry-run", action="store_true", help="collect but do not touch the sheet")
    parser.add_argument("--test-sheet", action="store_true", help="check the sheet link and exit")
    parser.add_argument(
        "--test-push", action="store_true", help="send a test notification to your phone and exit"
    )
    parser.add_argument("--init-config", action="store_true", help="create config.json and a secret")
    parser.add_argument("--set-sheet-url", metavar="URL", help="store the Apps Script /exec URL")
    parser.add_argument(
        "--debug-dump",
        action="store_true",
        help="save page HTML and screenshots into debug/ for diagnosing selectors",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="do not push a desktop notification about what is due",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="more detail on screen")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
