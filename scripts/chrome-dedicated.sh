#!/usr/bin/env bash
# Start a throwaway automation Chrome for Jev and Playwright, separate from the everyday profile.
# .env points Browser Harness at it with BU_CDP_URL; BU_CDP_WS is printed for other CDP clients.
set -euo pipefail

PORT="${JEV_CHROME_PORT:-9223}"
PROFILE="${JEV_CHROME_PROFILE:-$HOME/.jev-chrome}"
CHROME_APP="${JEV_CHROME_APP:-Google Chrome}"
VERSION_URL="http://127.0.0.1:$PORT/json/version"

if ! curl -sf "$VERSION_URL" >/dev/null; then
  mkdir -p "$PROFILE"
  # launchd owns the process, so Chrome outlives the shell that started it.
  open -na "$CHROME_APP" --args --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check about:blank
  for _ in $(seq 50); do
    curl -sf "$VERSION_URL" >/dev/null && break
    sleep 0.2
  done
fi

BU_CDP_WS=$(curl -sf "$VERSION_URL" | python3 -c 'import sys, json; print(json.load(sys.stdin)["webSocketDebuggerUrl"])')
echo "Dedicated Chrome on port $PORT (profile $PROFILE)"
echo "BU_CDP_WS=$BU_CDP_WS"
