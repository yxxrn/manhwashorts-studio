#!/usr/bin/env bash
set -euo pipefail

PROFILE_DIR="${MS_YOUTUBE_BROWSER_PROFILE_DIR:-$HOME/.config/manhwashorts/youtube-browser-runtime}"
CHROME="${MS_YOUTUBE_BROWSER_EXECUTABLE:-/usr/bin/google-chrome}"

mkdir -p "$PROFILE_DIR"
chmod 700 "$PROFILE_DIR"

if [[ -z "${DISPLAY:-}" ]]; then
  echo "DISPLAY is not set. Start this from the temporary noVNC/X11 login session." >&2
  exit 2
fi

echo "Opening YouTube Studio in the persistent ManhwaShorts Chrome profile."
echo "Log in manually, finish any 2FA/security prompt, verify Studio opens, then close Chrome."
exec "$CHROME" \
  --user-data-dir="$PROFILE_DIR" \
  --no-sandbox \
  --disable-dev-shm-usage \
  --no-first-run \
  --no-default-browser-check \
  https://studio.youtube.com
