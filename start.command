#!/usr/bin/env bash
# Double-clickable LocalDeploy launcher for macOS.
#
# macOS Finder opens a plain .sh in a text editor, but runs a .command file in
# Terminal on double-click - this is the macOS equivalent of start.bat. It just
# hands off to scripts/start.sh from the repo root. If the launch fails, the
# window stays open with the error instead of vanishing.
#
# First time only: macOS may need this marked executable. From Terminal, run
#   chmod +x start.command
# (a `git clone` already sets that bit; a ZIP download strips it).
set -uo pipefail
cd "$(dirname "$0")" || exit 1

# start.sh runs the server in the foreground, so this returns only once the
# server stops. Ctrl+C (130) and SIGTERM (143) are normal, intentional stops -
# only a real startup failure should keep the window open with the error.
./scripts/start.sh
status=$?
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ] && [ "$status" -ne 143 ]; then
  echo
  echo "LocalDeploy did not start (exit $status). Read the message above for what to fix."
  echo "Press any key to close this window."
  read -r -n 1 -s || true
fi
