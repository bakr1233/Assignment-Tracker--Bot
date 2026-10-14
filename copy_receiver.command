#!/bin/bash
# Puts sheet_receiver.gs on the clipboard with your shared secret filled in,
# ready to paste into Apps Script.
#
# Use this instead of copying out of an editor. The file in the repo carries a
# placeholder, so the secret never ends up in git - and because the clipboard
# is always built from the current file, you cannot accidentally paste a stale
# copy. (That happened once, and it silently removed a safety check.)
cd "$(dirname "$0")"

if [ ! -f config.json ]; then
  echo "No config.json yet. Run setup.command first."
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi

./.venv/bin/python - <<'PY' | pbcopy
import json
import pathlib
import sys

secret = json.loads(pathlib.Path("config.json").read_text())["shared_secret"]
source = pathlib.Path("sheet_receiver.gs").read_text()

if "PASTE_THE_SECRET_FROM_config.json_HERE" not in source:
    sys.exit("sheet_receiver.gs has no placeholder to fill in.")

sys.stdout.write(source.replace("PASTE_THE_SECRET_FROM_config.json_HERE", secret))
PY

echo
echo "Copied. Now, in Apps Script:"
echo "  1. Select all the existing code and paste over it."
echo "  2. Save."
echo "  3. Deploy -> Manage deployments -> pencil -> Version: New version -> Deploy."
echo
echo "Before deploying, it is worth running:  node tests/receiver_harness.js"
echo
read -n 1 -s -r -p "Press any key to close."
