#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
WITH_SUWAYOMI=1
WITH_POCKET_TTS=0
WITH_SYSTEMD=0
PRODUCTION_MODE=0
INSTALL_PACKAGES=1
INSTALL_CHROME=1
DRY_RUN=0
YOUTUBE_ACCOUNT=""
YOUTUBE_COOKIES=""
YOUTUBE_LABEL=""
CONFIG_IMPORT=""

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]
  --systemd              install and start a systemd service
  --production           set the single .env config to production defaults
  --with-pocket-tts      install/start local Pocket TTS (Alba, INT8 CPU)
  --without-suwayomi     skip Java/Suwayomi and disable the sidecar in .env
  --skip-system-packages do not run apt (useful when dependencies already exist)
  --skip-chrome          do not download/install Google Chrome
  --youtube-account ID   create/use this YouTube account profile
  --youtube-cookies PATH  import Netscape cookies.txt headlessly into that profile
  --youtube-label LABEL   optional display label for the YouTube account
  --config PATH            seed .env from an existing deployment config on a fresh machine
  --dry-run              print the intended installation steps only
EOF
}

while (($#)); do
  case "$1" in
    --systemd) WITH_SYSTEMD=1 ;;
    --production) PRODUCTION_MODE=1 ;;
    --with-pocket-tts) WITH_POCKET_TTS=1 ;;
    --without-suwayomi) WITH_SUWAYOMI=0 ;;
    --skip-system-packages) INSTALL_PACKAGES=0 ;;
    --skip-chrome) INSTALL_CHROME=0 ;;
    --youtube-account) shift; YOUTUBE_ACCOUNT="${1:-}" ;;
    --youtube-cookies) shift; YOUTUBE_COOKIES="${1:-}" ;;
    --youtube-label) shift; YOUTUBE_LABEL="${1:-}" ;;
    --config) shift; CONFIG_IMPORT="${1:-}" ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\n==> %s\n' "$*"; }
run() { if ((DRY_RUN)); then printf '+ '; printf '%q ' "$@"; printf '\n'; else "$@"; fi; }
root_run() {
  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then run "$@"
  elif command -v sudo >/dev/null 2>&1; then run sudo "$@"
  else echo "sudo is required for system package installation." >&2; exit 2
  fi
}

APP_USER="${SUDO_USER:-${USER:-$(id -un)}}"
APP_HOME="$(getent passwd "$APP_USER" 2>/dev/null | cut -d: -f6 || true)"
APP_HOME="${APP_HOME:-$HOME}"

if ((INSTALL_PACKAGES)); then
  command -v apt-get >/dev/null 2>&1 || { echo "Automatic install currently supports Debian/Ubuntu (apt)." >&2; exit 2; }
  say "Installing native dependencies"
  root_run apt-get update
  packages=(python3 python3-venv python3-pip ffmpeg espeak-ng tesseract-ocr fonts-dejavu-core curl ca-certificates git)
  ((WITH_SUWAYOMI)) && packages+=(openjdk-21-jre-headless)
  root_run apt-get install -y "${packages[@]}"
fi

PYTHON_BIN="$(command -v python3 || true)"
[[ -n "$PYTHON_BIN" ]] || { echo "Python 3.11+ is required." >&2; exit 2; }
if ! "$PYTHON_BIN" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  echo "Python 3.11+ is required; found $($PYTHON_BIN --version 2>&1)." >&2
  exit 2
fi

if ((INSTALL_CHROME)) && ! command -v google-chrome >/dev/null 2>&1 && ! command -v google-chrome-stable >/dev/null 2>&1; then
  arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  if [[ "$arch" == "amd64" || "$arch" == "x86_64" ]]; then
    say "Installing Google Chrome for YouTube Studio publishing"
    chrome_deb="$(mktemp --suffix=.deb)"
    trap 'rm -f "${chrome_deb:-}"' EXIT
    run curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o "$chrome_deb"
    root_run apt-get install -y "$chrome_deb"
    rm -f "$chrome_deb"; trap - EXIT
  else
    echo "WARNING: automatic Google Chrome install supports amd64 only; set MS_YOUTUBE_BROWSER_EXECUTABLE manually." >&2
  fi
fi

say "Preparing Python virtual environment"
if ((DRY_RUN)); then
  echo "+ python3 scripts/bootstrap_operator_cli.py [ensure runtime only]"
else
  PYTHONPATH="$ROOT" "$PYTHON_BIN" - <<PY
from scripts.bootstrap_operator_cli import ensure_runtime
ensure_runtime(${ROOT@Q}, python_command=("python3",))
PY
fi

if [[ ! -f .env ]]; then
  if [[ -n "$CONFIG_IMPORT" ]]; then
    [[ -f "$CONFIG_IMPORT" ]] || { echo "Config file not found: $CONFIG_IMPORT" >&2; exit 2; }
    say "Importing single deployment config .env"
    run cp "$CONFIG_IMPORT" .env
  else
    say "Creating single deployment config .env from portable defaults"
    run cp .env.example .env
  fi
fi
if [[ -f ms_env.sh ]]; then
  say "Migrating legacy ms_env.sh into .env"
  run "$ROOT/.venv/bin/python" scripts/migrate_legacy_env.py
fi
if (( ! DRY_RUN )); then chmod 600 .env; fi

upsert_env() {
  local key="$1" value="$2"
  if ((DRY_RUN)); then echo "+ set $key=$value in .env"; return; fi
  "$PYTHON_BIN" - "$key" "$value" <<'PY'
from pathlib import Path
import sys
path=Path('.env'); key=sys.argv[1]; value=sys.argv[2]
lines=path.read_text().splitlines() if path.exists() else []
out=[]; replaced=False
for line in lines:
    stripped=line.strip()
    if stripped.startswith(key+'='):
        out.append(f'{key}={value}'); replaced=True
    else:
        out.append(line)
if not replaced: out.extend(['', f'{key}={value}'])
path.write_text('\n'.join(out).rstrip()+'\n')
PY
}

if ((WITH_SUWAYOMI)); then
  say "Installing pinned Suwayomi sidecar"
  run "$ROOT/.venv/bin/python" scripts/setup_suwayomi.py
  run "$ROOT/.venv/bin/python" scripts/setup_suwayomi_extensions.py
else
  upsert_env MS_SUWAYOMI_ENABLED false
  upsert_env MS_SUWAYOMI_AUTO_START false
fi

if ((PRODUCTION_MODE)); then
  upsert_env MS_ENVIRONMENT production
  upsert_env MS_DEBUG false
fi

if ((WITH_POCKET_TTS)); then
  say "Installing local Pocket TTS service"
  run "$ROOT/scripts/setup_pocket_tts.sh"
  pocket_runtime="${POCKET_TTS_RUNTIME_DIR:-$APP_HOME/pocket-tts-runtime}"
  if [[ -f "$pocket_runtime/mode.txt" ]]; then
    pocket_mode="$(tr -d '\r\n' < "$pocket_runtime/mode.txt")"
  else
    pocket_mode="fp32"
  fi
  pocket_model="pocket-tts-3.1.0-${pocket_mode}"
  upsert_env MS_TTS_LOCAL_FIRST true
  upsert_env MS_TTS_POCKET_URL http://127.0.0.1:8790
  upsert_env MS_TTS_POCKET_VOICE alba
  upsert_env MS_TTS_POCKET_MODEL "$pocket_model"
  upsert_env MS_TTS_POCKET_PRODUCTION_SPEED 0.90
fi

say "Migrating database to Alembic head"
run "$ROOT/.venv/bin/alembic" upgrade head

if ((WITH_SYSTEMD)); then
  say "Installing systemd service"
  unit="/etc/systemd/system/manhwashorts.service"
  if ((DRY_RUN)); then
    echo "+ write $unit for user $APP_USER and WorkingDirectory=$ROOT"
  else
    tmp_unit="$(mktemp)"
    cat > "$tmp_unit" <<EOF
[Unit]
Description=ManhwaShorts Studio
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$ROOT
Environment=HOME=$APP_HOME
ExecStart=$ROOT/scripts/manhwashorts serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
    root_run install -m 0644 "$tmp_unit" "$unit"
    rm -f "$tmp_unit"
    root_run systemctl daemon-reload
    root_run systemctl enable --now manhwashorts.service
  fi
fi

if [[ -n "$YOUTUBE_ACCOUNT" || -n "$YOUTUBE_COOKIES" ]]; then
  [[ -n "$YOUTUBE_ACCOUNT" && -n "$YOUTUBE_COOKIES" ]] || { echo "--youtube-account and --youtube-cookies must be provided together." >&2; exit 2; }
  [[ -f "$YOUTUBE_COOKIES" ]] || { echo "YouTube cookies file not found: $YOUTUBE_COOKIES" >&2; exit 2; }
  say "Bootstrapping YouTube Studio account from cookies.txt"
  run "$ROOT/.venv/bin/python" scripts/youtube_browser_account.py ensure "$YOUTUBE_ACCOUNT" "$YOUTUBE_LABEL"
  run "$ROOT/.venv/bin/python" scripts/youtube_browser_account.py default "$YOUTUBE_ACCOUNT"
  run "$ROOT/.venv/bin/python" scripts/youtube_browser_account.py import-cookies "$YOUTUBE_ACCOUNT" "$YOUTUBE_COOKIES"
fi

say "Running machine doctor"
if ((DRY_RUN)); then
  echo "+ scripts/manhwashorts doctor"
else
  scripts/manhwashorts doctor
fi

cat <<EOF

Installation complete.
  Start manually:  scripts/manhwashorts serve
  Check machine:   scripts/manhwashorts doctor
  YouTube account: scripts/manhwashorts youtube-account list
EOF
if ((WITH_SYSTEMD)); then echo "  Service:         systemctl status manhwashorts"; fi
