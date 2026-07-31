# Operations

Running, troubleshooting, and maintaining a local install.

## Starting up

```bash
cd manhwashorts-studio
.venv/bin/python -m uvicorn app.main:app --reload
```

Startup logs tell you whether the machine can render:

```
INFO manhwashorts: environment OK: ffmpeg, ffprobe, and subtitle font present
INFO manhwashorts: tts=espeak llm=rules youtube=dry-run
INFO: Uvicorn running on http://127.0.0.1:8000
```

If you see `environment: ...` warnings instead, rendering will fail later. Fix
them first:

```bash
.venv/bin/python -c "from app.services.render import check_environment; print(check_environment() or 'OK')"
```

### With a separate render worker

Rendering runs inline in a background task by default. To move it off the web
process:

```bash
# terminal 1
.venv/bin/python -m uvicorn app.main:app
# terminal 2
.venv/bin/python scripts/worker.py
```

`execute_render` only accepts jobs still in `QUEUED` state, so running both the
inline task and the worker cannot double-render a job.

### As a systemd service

```ini
# /etc/systemd/system/manhwashorts.service
[Unit]
Description=ManhwaShorts Studio
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/manhwashorts
Environment="MS_ENVIRONMENT=production"
ExecStart=/home/ubuntu/manhwashorts/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

# Rendering is CPU-heavy; keep it from starving everything else on a small VPS.
CPUWeight=50
MemoryMax=2G

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now manhwashorts
journalctl -u manhwashorts -f
```

**Keep `--host 127.0.0.1`.** See "Exposing it safely" below.

## Resource expectations

Measured on 2 vCPU / 3.6 GB (Xeon 8255C), rendering a ~48s Short from 7 assets:

| Stage | Time |
|---|---|
| Analysis + script | <1s |
| Voice-over (5 segments, espeak) | ~8s |
| Timeline + subtitles | <1s |
| Render (9 scenes) | ~75s |
| **Total draft → MP4** | **~90s** |

Roughly 1.5× realtime for the render. Per-scene motion rendering is ~1× realtime;
subtitle burn-in adds another pass.

Disk per finished project: ~4 MB MP4, plus source assets. The scratch directory is
removed after a successful render.

## Troubleshooting

### Render fails with `ffmpeg_missing`

```bash
sudo apt-get install -y ffmpeg
ffmpeg -filters | grep -E 'zoompan|subtitles'
```

Both filters must be present. A minimal or static FFmpeg build often lacks
`libass`, which means captions cannot be burned in.

### Captions missing from the video

Check the font exists:

```bash
ls -l /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf || sudo apt-get install fonts-dejavu-core
```

If it lives elsewhere, set `MS_SUBTITLE_FONT`. Note that a frame sampled during
the silence between two beats legitimately has no caption — sample mid-sentence
before concluding something is broken.

### Render is extremely slow

If a short scene takes minutes, something is wrong with the filter chain rather
than the machine. Two known traps, both already fixed in this codebase but worth
recognising if you modify `render.py`:

- `-t` used as an **input** option with `-loop 1` before `zoompan` multiplies the
  output length by the frame count. Use `-frames:v` on the output.
- Pre-scaling before `zoompan` (e.g. `scale=2160:3840`) pushes every frame to
  ~8 MP. `crop_to_vertical` already oversamples to 1.15×.

Sanity check a single scene:

```bash
.venv/bin/python -c "
from pathlib import Path
from app.services import render as R
import time
src = sorted(Path('data/fixtures/panels').glob('*.jpg'))[0]
d = Path('data/tmp/bench'); d.mkdir(parents=True, exist_ok=True)
prep = R.crop_to_vertical(src, d/'p.jpg', 1080, 1920, 0.5, 0.4)
sc = R.SceneInput(image_path=src, start_time=0, end_time=4.0, effect='kenburns_in')
t=time.time(); R.render_scene_clip(sc, prep, d/'c.mp4', 1080, 1920, 30); el=time.time()-t
print('4s scene rendered in %.1fs -> %.2fs' % (el, R.probe(d/'c.mp4')['duration']))
"
```

Expect ~4s render producing exactly 4.00s. A duration of 400s means the `-t` bug
is back.

### Everyone logged out after a restart

The session key is persisted to `data/.secret_key`. If that file is deleted or the
data directory changed, all cookies become invalid. Set `MS_SECRET_KEY` explicitly
for a stable value across deployments.

### `could not decrypt credentials`

`data/.fernet_key` changed or was lost. Stored OAuth tokens **and BYOK API keys**
are unrecoverable — reconnect the channel and paste the API keys in again. Back
this file up with the database, or set `MS_FERNET_KEY` explicitly.

The pipeline degrades rather than dying here: a credential that cannot be
decrypted falls back to the offline engine and says so, so a lost key file does
not make projects un-analysable.

### BYOK key stopped working

Press **Muat ulang model** on the credential, or:

```bash
curl -b ck.txt -X POST localhost:8000/api/credentials/$CRED_ID/refresh
```

This re-checks the key against the provider and updates `status`. Common causes:
revoked upstream, out of quota, or billing lapsed. A `status` of `invalid` means
the stage has silently reverted to the offline engine — check
`GET /api/credentials/active` to confirm what is actually running.

### BYOK model disappeared from the list

Providers retire models. `refresh` clears a selection that is no longer offered
and says so in `status_message`; pick a current model from the dropdown. The app
deliberately does not substitute a replacement, because a different model bills
differently.

### `database is locked`

SQLite with WAL and a 5s busy timeout handles one writer at a time. If you run
several workers, move to PostgreSQL:

```bash
MS_DATABASE_URL=postgresql+psycopg://user:pass@localhost/manhwashorts
```

### Quality gate blocks with `policy.not_transformative`

Working as intended: narration is ≥50% verbatim from your source. Rewrite it as
your own commentary, or add an LLM key via BYOK (see `docs/BYOK.md`) for better
paraphrasing. Do not disable the gate.

### `subtitle.overlap` errors

Cue timings collided. Regenerate the voice-over and timeline:

```bash
curl -b ck.txt -X POST localhost:8000/api/projects/$PJ/voice -d '{"speed":1.0}' -H 'Content-Type: application/json'
curl -b ck.txt -X POST localhost:8000/api/projects/$PJ/timeline
```

### Upload fails with a 403 from YouTube

Usually the daily quota (10,000 units; an upload costs ~1,600, so ~6 uploads/day).
Quota resets at midnight Pacific. Retry with
`POST /api/publications/{id}/retry` — it reuses the render and does not
re-encode.

## Backups

Three things matter:

```bash
#!/usr/bin/env bash
# backup.sh
set -euo pipefail
STAMP=$(date +%Y%m%d_%H%M%S)
DEST="$HOME/backups/manhwashorts/$STAMP"
mkdir -p "$DEST"

# 1. database (sqlite3 .backup is safe against a live writer)
sqlite3 data/manhwashorts.db ".backup '$DEST/manhwashorts.db'"

# 2. keys — without these, sessions die and OAuth tokens plus BYOK API keys
#    become permanently unreadable
cp data/.secret_key data/.fernet_key "$DEST/" 2>/dev/null || true
chmod 600 "$DEST"/.*key 2>/dev/null || true

# 3. source assets
tar czf "$DEST/storage.tar.gz" data/storage

echo "backed up to $DEST"
```

Rendered outputs in `data/output/` are reproducible from the database and assets,
so they are optional. Treat `.secret_key` and `.fernet_key` as secrets: 0600, and
never in the repo (both are gitignored).

## Housekeeping

```bash
# scratch directories from interrupted renders
find data/tmp -mindepth 1 -maxdepth 1 -type d -mtime +1 -exec rm -rf {} +

# old renders (keep the database record; the file is reproducible)
find data/output -name '*.mp4' -mtime +30 -delete

# check what is using space
du -sh data/*
```

Storage is content-addressed, so identical uploads share one blob. Deleting a
project only removes blobs no other project references.

## Monitoring

Health endpoint for an uptime check:

```bash
curl -sf localhost:8000/api/health | python3 -c "
import sys, json
h = json.load(sys.stdin)
sys.exit(0 if h['status'] == 'ok' else 1)
"
```

Failed renders in the last day:

```bash
sqlite3 data/manhwashorts.db "
SELECT created_at, error_code, substr(error_message,1,60)
FROM render_jobs WHERE status='failed'
AND created_at > datetime('now','-1 day');"
```

Audit trail for a project:

```bash
sqlite3 data/manhwashorts.db "
SELECT created_at, action, substr(detail,1,80)
FROM audit_logs WHERE entity_id='<project-id>'
ORDER BY created_at;"
```

## Exposing it safely

The app is built for `127.0.0.1`. It has **no** login rate limiting, **no** CSRF
tokens, and **no** TLS. Do not put it on a public address directly.

If you need remote access, prefer an SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 user@your-vps
```

If you must expose it, front it with a reverse proxy that adds TLS and rate
limiting, and set `MS_ENVIRONMENT=production` so session cookies become `Secure`:

```nginx
server {
    listen 443 ssl;
    server_name studio.example.com;

    ssl_certificate     /etc/letsencrypt/live/studio.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/studio.example.com/privkey.pem;

    # The app does not rate limit logins itself.
    limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

    location /api/auth/login {
        limit_req zone=login burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 30M;   # must exceed MS_MAX_UPLOAD_MB
    }
}
```

Also update `MS_YOUTUBE_REDIRECT_URI` to the public HTTPS URL and add it to your
Google Cloud OAuth client.

## Upgrading

```bash
git pull
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -m "not slow"     # confirm nothing broke
sudo systemctl restart manhwashorts
```

Schema changes ship as Alembic migrations. `init_db()` creates missing tables on
startup but does not alter existing ones, so run migrations when they appear:

```bash
.venv/bin/alembic upgrade head
```
