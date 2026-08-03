# Driving the app from an AI agent

Every stage is reachable over REST, so an agent can take material in and hand a
finished MP4 back without a human touching the UI. The web interface exists for
occasional manual review, not as a required step.

The intended split: **you send panels and a recap to your agent, the agent does
the rest.**

Default render contract: English text + American English voice-over (`en-US`). Pass `language: "id"` explicitly for Indonesian.

## Base URL

| Caller | URL | Notes |
|---|---|---|
| Agent on the same VPS | `http://127.0.0.1:8000` | Recommended. No TLS, no proxy, ~1ms |
| Anything off-box | `https://<your-tunnel-host>` | Through Cloudflare |

Prefer loopback when the agent runs beside the app. It avoids the tunnel
entirely, which is both faster and one less thing to break.

## Two gotchas that will cost you an hour

**Cloudflare blocks the default Python User-Agent.** `Python-urllib/3.x` gets a
`403` before it ever reaches the app. Any client going through the tunnel must
send its own UA:

```python
req.add_header("User-Agent", "MyAgent/1.0")
```

Verified: identical request, default UA → `403`, custom UA → `200`. Irrelevant on
loopback, but it looks exactly like an auth failure if you hit it.

**Session cookies are `Secure` only over real HTTPS.** The flag is decided per
request from `X-Forwarded-Proto`, not from `MS_ENVIRONMENT`. A cookie marked
`Secure` is never sent back over `http://127.0.0.1`, which would make login
return `200` and the next call `401`. Because the decision is per request, the
browser (HTTPS, Secure) and a local agent (HTTP, not Secure) both work at the
same time. Covered by `tests/test_agent_api.py`.

## Auth

Session cookie. Log in once and keep a cookie jar:

```python
import json, urllib.request
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:8000"
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(CookieJar())
)

def call(method, path, body=None):
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": "MyAgent/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, payload, headers, method=method)
    with opener.open(req, timeout=120) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)

call("POST", "/api/auth/login",
     {"email": "agent@example.com", "password": "..."})
```

No API-key auth yet. If you want a separate machine identity, register a second
account for the agent.

## The whole flow

```python
# 1. is the machine able to render at all?
_, health = call("GET", "/api/health")
assert not health["problems"]

# 2. create the project
_, project = call("POST", "/api/projects", {
    "title": "Menara Kelabu ch.7",
    "manhwa_title": "Menara Kelabu",
    "chapter": "7",
    "target_duration": 40,          # seconds, max 60
    "narration_style": "dramatic",  # dramatic|casual|mysterious|fast|informative
    "spoiler_level": "medium",      # minimal|medium|full
})
pid = project["id"]

# 3. the recap, with a rights declaration
call("POST", f"/api/projects/{pid}/assets/text", {
    "text": recap,
    "title": "recap.txt",
    "rights": {
        "rights_owner": "Your Name",
        "license_type": "owned",     # owned|licensed|permission_granted|...
        "source_name": "written myself",
        "attribution": "Your Name",
        "declared": True,
    },
})

# 4. panels (multipart; the same declaration covers the batch)
#    Upload in story order — that decides which panel lands in which scene.

# 5. analyse -> script -> voice -> timeline, one call
_, draft = call("POST", f"/api/projects/{pid}/draft?seed=42")

# 6. read the script, then approve. Rendering is blocked until you do.
_, script = call("GET", f"/api/projects/{pid}/script")
call("POST", f"/api/projects/{pid}/script/approve")

# 7. quality gate. errors block; warnings need a recorded reason to override.
_, quality = call("POST", f"/api/projects/{pid}/quality")
assert quality["errors"] == 0, quality["error_codes"]

# 8. render, then poll
_, job = call("POST", f"/api/projects/{pid}/render",
              {"kind": "final", "encoder": "auto"})
while True:
    time.sleep(5)
    _, j = call("GET", f"/api/projects/{pid}/render/{job['id']}")
    if j["status"] in ("succeeded", "failed"):
        break

# 9. download
#    GET /api/projects/{pid}/download/{job_id}  -> MP4 bytes
```

Measured on a 2 vCPU box: draft ~1.7s, render ~45s for a 25s video, whole flow
about a minute.

`seed` makes the draft reproducible — same input, same script.

## Uploading panels

`POST /api/projects/{id}/assets/upload`, `multipart/form-data`, field name
`files` (repeat it per panel). Rights fields go in the same form and apply to the
whole batch.

Things worth knowing:

- **Tall pages are sliced automatically.** A webtoon strip (taller than 1:2.5) is
  split into consecutive 9:16 pieces, each stored as its own asset named
  `<page>_p01`, `_p02`, … Cropping such a page to one frame would keep under a
  third of it. So one uploaded page can return several assets — read the response
  rather than assuming one asset per file.
- **Order matters.** `order_index` follows upload order (and slice order within a
  page), and that decides which panel appears in which scene. Upload in story
  order.
- **Content is verified, not the filename.** Pillow opens the actual bytes, so a
  renamed non-image is rejected.
- **Duplicates are free.** Storage is content-addressed by SHA-256, so
  re-uploading the same panel does not consume more disk.
- **~1 panel per 5 seconds** of video. Fewer panels means images repeat rather
  than leaving black frames.
- 25 MB per file. JPG, PNG, WebP. TXT/MD/PDF/DOCX also accepted and text-extracted.

## What blocks an agent, by design

**Rights.** Every asset needs an owner and a concrete licence basis. Ticking
`declared` alone is not enough. Missing either one produces
`rights.undeclared_assets` and the final render is refused outright.

**Script approval.** `POST /script/approve` is mandatory before a final render.
An agent can call it, but the call is explicit — nothing approves itself.

**Public upload.** Needs `MS_ALLOW_PUBLIC_PUBLISH=true` *and*
`confirm_public: true` in the request. Two independent gates.

These are deliberate. An agent that can publish copyrighted panels to a public
channel with no checkpoint is a liability, not a feature.

## Discovering the surface

```
GET /openapi.json    full machine-readable spec
GET /docs            Swagger UI
```

66 endpoints. An agent can introspect rather than rely on this document.

## Useful reads while working

| Endpoint | Use |
|---|---|
| `GET /api/health` | ffmpeg present, active encoder, disk usage |
| `GET /api/encoders` | which CPU/GPU encoders actually work here |
| `GET /api/projects/{id}/analysis` | extracted facts; `PATCH` to correct them |
| `GET /api/projects/{id}/timeline` | scene boundaries and which asset each uses |
| `GET /api/projects/{id}/quality` | last gate result |
| `GET /api/projects/{id}/publish/readiness` | `{ready, reason, checks}` |

Correcting the analysis is the cheapest quality win: the script is generated from
it, so fixing a misdetected character there improves the final video.

## Current limits

- **Panel-to-scene mapping is automatic.** `PATCH /timeline/{scene_id}` can move
  an asset, but there is no content matching — the app does not know what is in
  your images. Upload order is the only control that matters.
- **One render at a time** (`MS_RENDER_WORKERS=1`). Queue your own work; two
  concurrent renders on 2 vCPU are slower than two sequential ones.
- **No API keys.** Session cookie only.
- **No webhooks.** Poll the render job.
- **Analytics needs a live upload.** Dry-run reports `available: false` rather
  than inventing numbers.
