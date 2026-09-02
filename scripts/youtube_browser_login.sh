#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACCOUNT_ID="${1:-}"
CHROME="${MS_YOUTUBE_BROWSER_EXECUTABLE:-/usr/bin/google-chrome}"
PYTHON="${ROOT}/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

readarray -t ACCOUNT_INFO < <(
  cd "$ROOT"
  PYTHONPATH="$ROOT" "$PYTHON" - "$ACCOUNT_ID" <<'PY'
import sys
from app.services.youtube_accounts import YouTubeBrowserAccountRegistry

registry = YouTubeBrowserAccountRegistry()
account = registry.get(sys.argv[1] or None)
print(account.account_id)
print(account.label)
print(account.profile_dir)
PY
)

RESOLVED_ID="${ACCOUNT_INFO[0]}"
LABEL="${ACCOUNT_INFO[1]}"
PROFILE_DIR="${ACCOUNT_INFO[2]}"
mkdir -p "$PROFILE_DIR"
chmod 700 "$PROFILE_DIR"

if [[ -z "${DISPLAY:-}" ]]; then
  echo "DISPLAY is not set. Start this from the temporary noVNC/X11 login session." >&2
  exit 2
fi

echo "Opening YouTube account: ${LABEL} (${RESOLVED_ID})"
echo "Chrome profile: ${PROFILE_DIR}"
echo "Log in manually, finish any 2FA/security prompt, verify Studio opens, then close Chrome."
exec "$CHROME" \
  --user-data-dir="$PROFILE_DIR" \
  --class="manhwashorts-youtube-${RESOLVED_ID}" \
  --no-sandbox \
  --disable-dev-shm-usage \
  --no-first-run \
  --no-default-browser-check \
  https://studio.youtube.com
