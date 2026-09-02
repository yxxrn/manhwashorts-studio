> **MAINTAINER NOTE — CURRENT ARCHITECTURE:** This document describes the HTTP
> journey. Coding agents must also read `../AGENTS.md` and `MAINTAINER_GUIDE.md`
> before modifying implementation. Use the stable `app.services.pipeline` facade;
> do not call `pipeline_stages` directly to bypass orchestration.

# Driving the app from an AI agent

Every stage is reachable over REST, so an agent can take source material through a finished MP4 or an explicitly requested verified YouTube publish without requiring the UI. The web interface exists for
occasional manual review, not as a required step.

The intended split: **you give the agent a title/chapter corpus or ordered source material, and the agent drives the resumable pipeline.** Source may be uploaded directly or imported through the optional Suwayomi connector.

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
same time. Covered by `tests/api/test_agent_api.py`.

## Auth

Session cookie. Log in once and keep a cookie jar:

```python
import json, time, urllib.request
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

For a local agent, the preferred high-level boundary is the resumable project runner rather than manually replaying every stage.

```python
# 1. health + create project
_, health = call("GET", "/api/health")
assert not health["problems"]
_, project = call("POST", "/api/projects", {
    "title": "Infinite Mage ch.20-25",
    "manhwa_title": "Infinite Mage",
    "chapter": "20-25",
    "target_duration": 55,
})
pid = project["id"]

# 2. ingest source before analysis:
#    - upload ordered files/text, OR
#    - POST /api/projects/{pid}/sources/suwayomi/import

# 3a. manual/review path: advance only as far as needed
_, state = call("POST", f"/api/projects/{pid}/run", {"until": "draft"})
# inspect/edit/approve script, then call /run again for voice/timeline/render.

# 3b. explicit user-requested publish path for a trusted local agent
publish_req = {
    "until": "publish",
    "approval_mode": "trusted_agent",
    "confirm_publish_intent": True,
    "youtube_account_id": "main",
    "privacy_status": "private",
}
_, state = call("POST", f"/api/projects/{pid}/run", publish_req)

if state.get("action_required") in {"quality_blocked", "render_retry", "publish_retry"}:
    raise RuntimeError(state)

# Render is asynchronous. If it was just queued, poll until it finishes.
while state.get("render_status") not in ("succeeded", "failed"):
    time.sleep(5)
    _, state = call("GET", f"/api/projects/{pid}/status")
    if state.get("action_required") in {"quality_blocked", "render_retry"}:
        raise RuntimeError(state)

if state.get("render_status") == "failed":
    raise RuntimeError(state)

# Resume the same request: valid stages are reused, then publish runs.
_, state = call("POST", f"/api/projects/{pid}/run", publish_req)
```

`/run` reuses current analysis/script/audio/timeline/render state when its identity remains valid. It does not turn a normal unapproved draft into a publish automatically: trusted-agent approval is only created for the exact `until="publish"` + `approval_mode="trusted_agent"` + `confirm_publish_intent=true` combination.

Manual stage endpoints remain available when you want explicit editorial control. The current production duration contract is a 55s target and 50–60s accepted final window.

### Optional Suwayomi source import

Agents can ask ManhwaShorts to resolve an exact title and chapter range through its localhost Suwayomi sidecar before analysis:

```python
call("POST", f"/api/projects/{pid}/sources/suwayomi/import", {
    "title": "Infinite Mage",
    "chapter_from": 20,
    "chapter_to": 25,
    "language": "en",
})
```

The imported pages are ordinary ordered source assets afterward. The connector is idempotent by provenance/page identity and refuses corpus mutation after analysis exists.

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
- **Order remains evidence.** `order_index` follows upload/import order (and slice order within a page), so ingest in reading order. The multimodal story/visual pipeline then scores content, chronology, lineage, framing, faces, protected regions, repetition, and grounded narration evidence; order is a constraint, not the only selector.
- **Content is verified, not the filename.** Pillow opens the actual bytes, so a
  renamed non-image is rejected.
- **Duplicates are free.** Storage is content-addressed by SHA-256, so
  re-uploading the same panel does not consume more disk.
- **Final cadence is bounded.** Production reference shots are capped at 4 seconds; insufficient grounded visual capacity must be repaired/fail closed rather than stretched into long repeated holds.
- 25 MB per file. JPG, PNG, WebP. TXT/MD/PDF/DOCX also accepted and text-extracted.

## What blocks an agent, by design

**Rights metadata.** Keep owner/licence/source fields when available because they
remain part of the audit trail. In the current default configuration
`MS_REQUIRE_RIGHTS_DECLARATION=false`, missing rights metadata produces a
non-blocking policy finding rather than refusing the final render. Do not enable
enforcement implicitly; that is a separate product/configuration decision.

**Script approval.** Normal/manual production requires explicit script approval. The only automated approval path is the trusted local-agent `/run` publish boundary, and it requires an explicit user publish request (`until=publish`, `approval_mode=trusted_agent`, `confirm_publish_intent=true`).

**Public upload.** Visibility is explicit: omitted visibility defaults to `private`; `privacy_status: public` publishes publicly.

These are deliberate. An agent that can publish copyrighted panels to a public
channel with no checkpoint is a liability, not a feature.

## Discovering the surface

```
GET /openapi.json    full machine-readable spec
GET /docs            Swagger UI
```

Agents should introspect the current OpenAPI surface rather than rely on a hard-coded endpoint count.

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

- **Source corpus is immutable after analysis for Suwayomi import.** Create a new project when changing the imported title/chapter range after vision analysis exists.
- **Visual mapping is automatic but content-aware.** Exact persisted panels/ROIs are selected from multimodal evidence and chronology; upload/import order still matters as source lineage, but it is not the only control.
- **One render worker by default** (`MS_RENDER_WORKERS=1`). Queue/resume through the app rather than launching duplicate render jobs.
- **No API-key auth yet.** REST agents use a normal authenticated session cookie.
- **No webhooks.** Poll `/api/projects/{id}/status` / render status and call `/run` again to resume.
- **Scheduled YouTube publishing is not acceptance-tested.** Supplying `scheduled_at` fails closed with manual action required.
- **Browser publishing does not fetch analytics.** Stats sync reports unavailable rather than fabricating values.
