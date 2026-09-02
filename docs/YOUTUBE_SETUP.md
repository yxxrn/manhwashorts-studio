# YouTube Studio browser publishing

ManhwaShorts publishes through the YouTube Studio web UI with Playwright and persistent Chrome profiles. The legacy YouTube Data API publisher is archived under `archive/youtube_api/` and is not imported by runtime publishing.

## Authentication and account model

Each YouTube account/channel uses its own Chrome user-data directory. Runtime account metadata lives under `~/.config/manhwashorts/youtube-accounts/`; the pre-migration/default profile remains `~/.config/manhwashorts/youtube-browser-runtime`.

Google authentication stays inside Chrome. Do not export/commit cookies, storage state, passwords, or session tokens. If Google requests 2FA, CAPTCHA, or re-verification, stop automation and complete it interactively in that account profile.

## Runtime settings

```bash
MS_YOUTUBE_BROWSER_ENABLED=true
MS_YOUTUBE_BROWSER_EXECUTABLE=google-chrome
MS_YOUTUBE_BROWSER_HEADLESS=true
MS_YOUTUBE_BROWSER_TIMEOUT_SECONDS=120
MS_YOUTUBE_VIDEO_LANGUAGE=English
MS_YOUTUBE_METADATA_LANGUAGE=English
MS_YOUTUBE_CATEGORY=Film & Animation
MS_YOUTUBE_TRUST_CHANNEL_DEFAULTS=false
```

Profile/account directories are resolved from the current OS user's home unless explicitly overridden by `MS_YOUTUBE_BROWSER_PROFILE_DIR` / `MS_YOUTUBE_BROWSER_ACCOUNTS_DIR`.

## Visibility contract

Visibility is request-driven and uploader-controlled:

- omitted `privacy_status` → `private`
- explicit `private` → Private
- explicit `unlisted` → Unlisted
- explicit `public` → Public

There is no second Public confirmation gate. `confirm_public` remains accepted only as a legacy request field and has no effect. The uploader explicitly selects/verifies requested visibility, so a channel whose YouTube Upload defaults are Public still becomes Private when the request omits privacy.

## Account operations

REST:

- `GET /api/youtube/browser/accounts`
- `POST /api/youtube/browser/accounts`
- `PATCH /api/youtube/browser/accounts/{account_id}`
- `GET /api/youtube/browser/status?account_id=<id>`

CLI helpers inside this repository:

```bash
PYTHONPATH=. .venv/bin/python scripts/youtube_browser_account.py list
PYTHONPATH=. .venv/bin/python scripts/youtube_browser_account.py add account-b "Channel B"
./scripts/youtube_browser_login.sh account-b
PYTHONPATH=. .venv/bin/python scripts/youtube_browser_account.py default account-b
```

The account registry stores labels/profile paths/settings, not raw authentication secrets. Publishing persists the resolved `youtube_account_id` on the Publication row so retries keep using the same channel even if the global default later changes.

## Trust channel Upload defaults

`trust_channel_defaults` is optional per account and can also be overridden per direct publish request. Resolution order is one-off publisher override → per-account value → `MS_YOUTUBE_TRUST_CHANNEL_DEFAULTS` → false.

When effective trust is true, ManhwaShorts trusts the channel's YouTube Studio Upload defaults for video language, title/description language, and category. It still controls title, description, tags, thumbnail, audience, and visibility for every upload.

Use trust mode only after those static defaults are configured correctly on that channel. Existing accounts default to inherited/global false, so enabling the feature does not silently change established behavior.

## Publish/verification contract

```text
final.mp4 + generated metadata + thumbnail
  → re-check final artifact + blocking QC
  → open selected persistent Chrome profile
  → YouTube Studio upload wizard
  → exact title/description/tags + audience
  → optional static metadata automation (unless trust defaults)
  → requested visibility
  → click final action
  → verify matching Content/Shorts row is not Draft and has requested visibility
  → persist verified video ID/result
```

A final click alone is never success. The publisher must verify the resulting Studio row. Scheduled publishing currently fails closed until its Studio flow is separately acceptance-tested.

Thumbnail handling is non-blocking for an otherwise verified video. The publisher attempts the custom thumbnail during upload and can verify/retry persistence after creation. If that still fails, the Publication records the failure and asks for manual Studio correction; the video is not re-uploaded. The old standalone thumbnail-retry endpoint remains only as compatibility surface and refuses the archived behavior.

## Failure behavior

Diagnostics are written under ignored runtime storage (`data/tmp/youtube_browser/...`). Common action states include `youtube_reauthentication`, `browser_busy`, `studio_automation_failed`, and `manual_schedule`.

Never bypass Google security challenges programmatically. Browser profiles are authenticated sessions and must be protected like credentials.

## Initial login

Login must be completed interactively for each new profile. Use `scripts/youtube_browser_login.sh <account-id>` from a temporary X11/VNC/noVNC session, complete Google security steps, verify Studio loads, close Chrome, then check `/api/youtube/browser/status?account_id=<id>`.
