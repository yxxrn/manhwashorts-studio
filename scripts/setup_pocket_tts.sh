#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="${POCKET_TTS_RUNTIME_DIR:-$HOME/pocket-tts-runtime}"
PORT="${POCKET_TTS_PORT:-8790}"
VOICE="${POCKET_TTS_VOICE:-alba}"
SERVICE="manhwashorts-pocket-tts.service"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$ROOT/scripts/setup_pocket_tts_runtime.py" --runtime-dir "$RUNTIME_DIR"
if [[ -f "$RUNTIME_DIR/mode.txt" ]]; then MODE="$(tr -d '\r\n' < "$RUNTIME_DIR/mode.txt")"; else MODE="fp32"; fi
QUANTIZE_ARG=""
[[ "$MODE" == "int8" ]] && QUANTIZE_ARG=" --quantize"

UNIT_FILE="$(mktemp)"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=ManhwaShorts Pocket TTS local CPU service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$RUNTIME_DIR
ExecStart=$RUNTIME_DIR/.venv/bin/pocket-tts serve --host 127.0.0.1 --port $PORT --language english --default-voice $VOICE$QUANTIZE_ARG
Restart=on-failure
RestartSec=3
CPUQuota=250%
MemoryMax=2G
Environment=OMP_NUM_THREADS=3
Environment=MKL_NUM_THREADS=3

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 0644 "$UNIT_FILE" "/etc/systemd/system/$SERVICE"
rm -f "$UNIT_FILE"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/openapi.json" >/dev/null; then
    echo "Pocket TTS ready: voice=$VOICE port=$PORT mode=$MODE"
    systemctl --no-pager --full status "$SERVICE" | head -12
    exit 0
  fi
  sleep 1
done

echo "Pocket TTS failed to become ready" >&2
sudo journalctl -u "$SERVICE" -n 40 --no-pager >&2
exit 1
