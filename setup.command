#!/bin/bash
# One-time setup. Double-click this first.
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "Assignment tracker - setup"
echo "=========================="
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed."
  echo "Install it from https://www.python.org/downloads/macos/ then run this again."
  echo
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')
if [ "$PY_OK" != "1" ]; then
  echo "Python 3.9 or newer is needed. You have: $(python3 --version)"
  echo
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi

echo "1/5  Creating a private Python environment in .venv ..."
python3 -m venv .venv

echo "2/5  Installing the packages it needs ..."
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt

echo "3/5  Downloading the browser Playwright drives (this is the slow part) ..."
./.venv/bin/python -m playwright install chromium

echo "4/5  Creating your credentials files ..."
for example in credentials/*.example.txt; do
  target="credentials/$(basename "$example" .example.txt).txt"
  if [ ! -f "$target" ]; then
    cp "$example" "$target"
    echo "      created $target"
  else
    echo "      kept $target (already filled in)"
  fi
done

echo "5/5  Creating config.json ..."
./.venv/bin/python -m bot.main --init-config

chmod 600 credentials/*.txt 2>/dev/null || true

echo
echo "Done. Next:"
echo "  1. Fill in the files in the credentials folder."
echo "  2. Follow step 2 in README.md to deploy sheet_receiver.gs."
echo "  3. Double-click run_visible.command."
echo
read -n 1 -s -r -p "Press any key to close."
