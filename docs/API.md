# API reference

Base URL: `http://127.0.0.1:8000`. Interactive docs at `/docs` (Swagger) and
`/redoc`.

All `/api/projects/*` routes require an authenticated session cookie. A project
belonging to another user returns **404**, not 403 — the API does not confirm
that an id exists.

Errors use `{"detail": "human readable message"}`. Validation failures are 422
with the offending field named.

## Conventions

| Code | Meaning |
|---|---|
| 200 / 201 | Success |
| 401 | Not authenticated, or session invalid/expired |
| 404 | Not found, or not yours |
| 409 | Conflict (duplicate email, no output file) |
| 410 | Rendered file no longer on disk |
| 422 | Validation failure, or a pipeline/policy refusal |
| 503 | Feature not configured (e.g. YouTube OAuth) |

## System

### `GET /api/health`

Reports whether this machine can actually render.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "environment": "local",
  "ffmpeg": true,
  "tts_provider": "espeak",
  "llm_provider": "rules",
  "youtube_enabled": false,
  "problems": []
}
```

`status` is `degraded` when `problems` is non-empty (missing FFmpeg, missing
subtitle font, an FFmpeg build without `zoompan` or `libass`).

### `GET /api/voices`

```json
{"provider": "espeak", "voices": [{"id": "id", "label": "Indonesian (espeak)"}]}
```

## Auth

### `POST /api/auth/register`

```bash
curl -c cookies.txt -X POST localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"atleast8chars","name":"You"}'
```

201 with `{id, email, name}` and sets the session cookie. Creates a default
workspace. 409 if the email is taken. Note that reserved TLDs such as `.test` are
rejected by the email validator.

### `POST /api/auth/login`

Same body minus `name`. Wrong password and unknown account return the **same**
401 message, so the endpoint does not reveal which accounts exist.

### `POST /api/auth/logout` · `GET /api/auth/me` · `GET /api/auth/workspace`

Logout clears the cookie. The other two return the current user / workspace.

## Projects

### `POST /api/projects`

```json
{
  "title": "Rian Chapter 12",
  "manhwa_title": "Peringkat Terakhir",
  "chapter": "12",
  "content_type": "chapter_recap",
  "language": "id",
  "spoiler_level": "medium",
  "narration_style": "dramatic",
  "target_duration": 60,
  "voice_id": "id",
  "cta_text": "Komentar di bawah.",
  "banned_words": [],
  "pronunciations": {"Rian": "Ri-an"}
}
```

Only `title` is required. `target_duration` must be 10–60.

Enums: `content_type` ∈ `chapter_recap | character_profile | fun_facts | theory |
cliffhanger`; `spoiler_level` ∈ `minimal | medium | full`; `narration_style` ∈
`dramatic | casual | mysterious | fast | informative`.

### `GET /api/projects` · `GET /api/projects/{id}` · `PATCH /api/projects/{id}`

List (add `?include_archived=true`), fetch, and partial update.

`status` moves through: `draft → generating → review → rendering → ready →
scheduled → published`, or `failed`.

### `POST /api/projects/{id}/duplicate`

Copies settings and assets (with their rights declarations) into a new draft.
Assets are content-addressed, so blobs are shared rather than duplicated.

### `DELETE /api/projects/{id}`

Deletes the project and every blob no other project references.

```json
{"detail": "Project deleted. 7 stored file(s) removed."}
```

## Source material

### `POST /api/projects/{id}/assets/text`

```json
{
  "text": "Bab ini dibuka dengan…",
  "title": "recap_ch12.txt",
  "rights": {
    "rights_owner": "You",
    "license_type": "owned",
    "source_name": "Written in-house",
    "permission_reference": "",
    "attribution": "You",
    "declared": true
  }
}
```

Minimum 40 characters. `license_type` ∈ `owned | licensed | permission_granted |
public_domain | creative_commons | unknown`.

**`declared: true` alone is not enough.** Without `rights_owner` and a licence
other than `unknown`, the asset stays `undeclared` and will block rendering.

### `POST /api/projects/{id}/assets/upload`

Multipart. One rights declaration applies to all files in the request.

```bash
curl -b cookies.txt -X POST localhost:8000/api/projects/$PJ/assets/upload \
  -F "files=@panel01.jpg" -F "files=@panel02.jpg" \
  -F "rights_owner=You" -F "license_type=owned" -F "declared=true"
```

Accepts JPG, PNG, WebP, TXT, MD, PDF, DOCX. Max 25 MB each. Images are re-parsed
with Pillow, so a renamed non-image is rejected with 422 regardless of its
extension or declared MIME type.

### `PATCH /api/projects/{id}/assets/{asset_id}/rights`

Correct a declaration after upload. Same body as the `rights` object above.

### `GET /api/projects/{id}/assets` · `DELETE …/assets/{asset_id}`

## Pipeline

### `POST /api/projects/{id}/draft?seed=42`

Runs analysis → script → voice-over → timeline in one call. This is the "draft in
under 10 minutes" path. It stops before approval and rendering.

```json
{
  "script_id": "7f6ad5c4…",
  "script_version": 1,
  "estimated_duration": 43.92,
  "audio_duration": 48.37,
  "segments": 5,
  "scenes": 9,
  "cues": 16,
  "warnings": []
}
```

`seed` makes generation reproducible — useful for tests and for comparing hooks.

### Analysis — `POST` / `GET` / `PATCH /api/projects/{id}/analysis`

`POST` extracts story facts, replacing any previous analysis. `PATCH` applies your
corrections and sets `edited_by_user: true`.

422 with "no text material to analyse" if the project has no text asset.

### Script

`POST /api/projects/{id}/script` — generate the next version.

```json
{"keep_locked": true, "hook_count": 3, "seed": 42}
```

`GET …/script` returns the latest; `GET …/scripts` returns all versions.

```json
{
  "version": 1,
  "sections": [
    {"section": "hook", "text": "…", "locked": false,
     "estimated_duration": 4.3, "citations": [0]}
  ],
  "hook_options": ["…", "…", "…"],
  "estimated_duration": 43.92,
  "approved_at": null
}
```

`PATCH …/script` applies edits. **Editing clears approval** — the review cannot be
bypassed. Unknown section names are rejected.

`POST …/script/approve` marks it reviewed. 422 if any section is empty or a
blocking script warning is unresolved.

Locked sections survive regeneration when `keep_locked` is true.

### Voice-over — `POST` / `GET /api/projects/{id}/voice`

```json
{"speed": 1.0, "provider": null}
```

`speed` 0.5–2.0. Replaces previous audio and deletes the old files. 422 if no
script exists.

### Timeline — `POST` / `GET /api/projects/{id}/timeline`

`POST` derives scenes and cues from the current voice-over. `PATCH
…/timeline/{scene_id}` edits one scene:

```json
{"asset_id": "…", "focus_x": 0.5, "focus_y": 0.35, "effect": "pan_left"}
```

Effects: `kenburns_in | kenburns_out | pan_right | pan_left | static`.
`focus_x`/`focus_y` are 0–1 and drive the 9:16 crop. An asset from another project
is rejected; `end_time <= start_time` is rejected.

### Subtitles

`GET /api/projects/{id}/subtitles` — cues.
`PATCH …/subtitles/{cue_id}` — edit text or timing; sets `edited_by_user`.
`GET …/subtitles.srt` — SRT download.

### Quality

`POST /api/projects/{id}/quality` runs every gate and stores the results.

```json
{
  "total": 7, "errors": 0, "warnings": 1, "can_publish": true,
  "error_codes": [],
  "warning_codes": ["policy.high_similarity"],
  "checks": [{"code": "…", "severity": "warning", "message": "…", "passed": false}]
}
```

`POST …/quality/override` accepts a **warning** with a recorded reason:

```json
{"code": "policy.high_similarity", "reason": "sudah saya periksa manual"}
```

Reason must be ≥5 characters. Attempting to override an `error` returns 422.

Common blocking codes: `rights.undeclared_assets`, `policy.not_transformative`,
`script.not_approved`, `audio.missing`, `timeline.no_scenes`,
`subtitle.overlap`, `duration.too_long`, `render.wrong_aspect`.

### Render

`POST /api/projects/{id}/render` with `{"kind": "final"}` or `"preview"`.

A **final** render requires quality checks to pass; blocking errors return 422
listing them. Returns a job immediately and renders in the background.

`GET …/render/{job_id}` to poll:

```json
{
  "status": "running", "progress": 65, "stage": "burning subtitles",
  "duration": 0, "error_code": "", "error_message": ""
}
```

`status` ∈ `queued | running | succeeded | failed`. On success you also get
`duration`, `width`, `height`, `checksum`, `output_key`, `subtitle_key`.

`POST …/render/{job_id}/retry` queues a fresh attempt and preserves the failed
job for the audit trail.

`GET /api/projects/{id}/download/{job_id}` streams the MP4. 409 if the render did
not succeed; 410 if the file has since been removed.

## Publishing

### `GET /api/projects/{id}/metadata`

Drafts title, description, and tags from the approved script. Always editable. The
description includes a rights notice automatically.

### `GET /api/projects/{id}/publish/readiness`

```json
{"ready": true, "reason": "", "checks": {"errors": 0, "warnings": 1}}
```

### `POST /api/projects/{id}/publish`

```json
{
  "video_title": "Peringkat Terakhir Chapter 12 #shorts",
  "description": "…",
  "tags": ["manhwa", "shorts"],
  "privacy_status": "private",
  "scheduled_at": null,
  "confirm_public": false
}
```

Empty fields fall back to the generated metadata.

Before uploading, the server re-verifies: public gating, a successful final
render, the file's SHA-256 against its render-time checksum, and the quality
gates. A file modified after rendering is refused.

Public uploads require `MS_ALLOW_PUBLIC_PUBLISH=true` **and**
`confirm_public: true`.

With YouTube unconfigured, the dry-run provider writes a receipt to
`data/output/dry_run_uploads/` and returns a `dryrun_…` video id.

### `POST /api/publications/{id}/retry`

Retries a failed upload. Reuses the same `Publication` row, so it never
re-renders.

### YouTube channels

`GET /api/youtube/channels` — connected channels (never includes tokens).
`GET /api/youtube/connect` — returns `authorization_url`; 503 if unconfigured.
`GET /api/youtube/callback` — OAuth redirect target; rejects unknown `state`.
`DELETE /api/youtube/channels/{id}` — revokes and erases stored credentials.

### Analytics

`POST /api/publications/{id}/stats/sync`

```json
{"available": false, "detail": "No analytics available yet…"}
```

Returns `available: false` rather than fabricating zeros when the API has no data
(always the case in dry-run mode).

`GET /api/publications/{id}/stats` — snapshot history, newest first.

## Complete worked example

```bash
BASE=http://127.0.0.1:8000

# 1. account
curl -sS -c ck.txt -X POST $BASE/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"studio12345"}'

# 2. project
PJ=$(curl -sS -b ck.txt -X POST $BASE/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"title":"Ch12","manhwa_title":"Peringkat Terakhir","chapter":"12"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 3. material, with rights declared
curl -sS -b ck.txt -X POST $BASE/api/projects/$PJ/assets/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"<your recap, 40+ chars>","rights":{"rights_owner":"You","license_type":"owned","declared":true}}'

curl -sS -b ck.txt -X POST $BASE/api/projects/$PJ/assets/upload \
  -F "files=@panel01.jpg" -F "files=@panel02.jpg" \
  -F "rights_owner=You" -F "license_type=owned" -F "declared=true"

# 4. draft everything
curl -sS -b ck.txt -X POST "$BASE/api/projects/$PJ/draft?seed=42"

# 5. review, then approve
curl -sS -b ck.txt $BASE/api/projects/$PJ/script
curl -sS -b ck.txt -X POST $BASE/api/projects/$PJ/script/approve

# 6. quality gate
curl -sS -b ck.txt -X POST $BASE/api/projects/$PJ/quality

# 7. render, then poll
JOB=$(curl -sS -b ck.txt -X POST $BASE/api/projects/$PJ/render \
  -H 'Content-Type: application/json' -d '{"kind":"final"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -sS -b ck.txt $BASE/api/projects/$PJ/render/$JOB

# 8. download and upload (private)
curl -sS -b ck.txt -o short.mp4 $BASE/api/projects/$PJ/download/$JOB
curl -sS -b ck.txt -X POST $BASE/api/projects/$PJ/publish \
  -H 'Content-Type: application/json' -d '{"privacy_status":"private"}'
```
