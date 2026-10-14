#!/bin/bash
# What the Mon/Wed/Fri schedule runs. No window, no prompts.
# It relies on the browser sessions saved by run_visible.command; if they have
# expired you get a notification asking you to run that again.
cd "$(dirname "$0")"

mkdir -p logs
exec ./.venv/bin/python -m bot.main "$@" >> "logs/scheduled.log" 2>&1
