#!/usr/bin/env sh
# Start (or reuse) the dedicated automation Chrome that jev-ultrafast drives.
# Keep PORT and PROFILE in sync with BU_CDP_URL in the project .env.
# The anti-throttling flags keep background/occluded windows rendering, which
# Page.captureScreenshot needs (the inspector UI captures screenshots).
set -eu

PORT="${JEV_CHROME_PORT:-9222}"
PROFILE="${JEV_CHROME_PROFILE:-$HOME/.config/browser-harness/chrome-automation}"

if command -v curl >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "Automation Chrome is already listening on port $PORT."
  exit 0
fi

case "$(uname -s)" in
  Darwin) CHROME="${JEV_CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}" ;;
  *)      CHROME="${JEV_CHROME:-$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser || true)}" ;;
esac

if [ -z "${CHROME:-}" ] || [ ! -x "$CHROME" ]; then
  echo "ERROR: Chrome not found. Set JEV_CHROME=/path/to/chrome and retry." >&2
  exit 1
fi

mkdir -p "$PROFILE"
"$CHROME" --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check \
  --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
  --disable-background-timer-throttling --disable-features=CalculateNativeWinOcclusion \
  about:blank >/dev/null 2>&1 &

echo "Started automation Chrome on port $PORT (profile: $PROFILE)."
echo "Set BU_CDP_URL=http://127.0.0.1:$PORT in the project .env, and close that window to stop it."
