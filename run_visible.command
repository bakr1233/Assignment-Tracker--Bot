#!/bin/bash
# Run the bot with the browser on screen, so you can approve the Duo push.
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Run setup.command first."
  echo
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi

./.venv/bin/python -m bot.main --visible "$@"
STATUS=$?

echo
if [ $STATUS -eq 0 ]; then
  echo "Finished cleanly."
else
  echo "Finished with problems (exit $STATUS). See the list above, or logs/."
fi
echo
read -n 1 -s -r -p "Press any key to close."
exit $STATUS
