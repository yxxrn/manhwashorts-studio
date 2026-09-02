# YouTube Studio Browser Publishing

ManhwaShorts publishes through the YouTube Studio web UI using Playwright and a dedicated persistent Google Chrome profile. The YouTube Data API/OAuth publisher is archived under `archive/youtube_api/` and is not imported by the runtime.

## Authentication model

The browser profile lives outside the repository:

```text
~/.config/manhwashorts/youtube-browser/
```

Log in to Google once with normal Chrome using this profile. Chrome stores the Google/YouTube cookies, local storage, and other session state there. Later Playwright opens the same profile headlessly, so the uploader normally starts already signed in.

Do **not** put Google passwords, cookies, storage-state JSON, or session tokens in Git, `.env`, chat, logs, or source code. If Google asks for 2FA, CAPTCHA, or account re-verification, automation stops and a human completes it in the browser.

## Runtime settings

```bash
MS_YOUTUBE_BROWSER_ENABLED=true
# Defaults are resolved from the current OS user home. Override only if needed.
# MS_YOUTUBE_BROWSER_PROFILE_DIR=~/.config/manhwashorts/youtube-browser-runtime
# MS_YOUTUBE_BROWSER_ACCOUNTS_DIR=~/.config/manhwashorts/youtube-accounts
MS_YOUTUBE_BROWSER_EXECUTABLE=google-chrome
MS_YOUTUBE_BROWSER_HEADLESS=true
MS_YOUTUBE_BROWSER_TIMEOUT_SECONDS=120
MS_YOUTUBE_VIDEO_LANGUAGE=English
MS_YOUTUBE_METADATA_LANGUAGE=English
MS_YOUTUBE_CATEGORY=Film & Animation
MS_YOUTUBE_TRUST_CHANNEL_DEFAULTS=false
```

Public visibility remains double-gated:

Visibility is request-driven. If `privacy_status` is omitted it defaults to `private`; sending `privacy_status=public` publishes publicly without a second confirmation flag.

## First login

The first login should use normal Google Chrome, not Playwright:

```bash
DISPLAY=:99 ./scripts/youtube_browser_login.sh
```

Use a temporary VNC/noVNC display to interact with that browser. Log in, complete 2FA if requested, confirm YouTube Studio loads, then close Chrome. Afterward check:

```text
GET /api/youtube/browser/status
```

Expected result:

```json
{
  "publisher": "youtube_studio_browser",
  "available": true,
  "authenticated": true,
  "action_required": null
}
```

## Publish flow

```text
final.mp4 + thumbnail + metadata
        ↓
quality gate
        ↓
Playwright persistent Chrome profile
        ↓
YouTube Studio upload wizard
        ↓
title / description / thumbnail / audience
        ↓
visibility
        ↓
Publish
```

The browser publisher supports `private`, `unlisted`, and `public`. Scheduled publishing intentionally fails closed until the current Studio schedule UI has its own acceptance test.

### Trust channel Upload defaults

For channels already configured under **YouTube Studio → Settings → Upload defaults**, enable trust mode per account from the UI or account API. In this mode ManhwaShorts still sets title, description, tags, thumbnail, audience, and visibility, but skips per-upload automation for video language, title/description language, and category.

The global fallback is `MS_YOUTUBE_TRUST_CHANNEL_DEFAULTS=false`; per-account settings override it. A publish request can also send `trust_channel_defaults` as a one-off override.

## Failure behavior

Browser diagnostics are written under ignored runtime storage:

```text
data/tmp/youtube_browser/<timestamp>/failure.png
```

Common action states:

- `youtube_reauthentication`: log in or complete a Google security prompt.
- `browser_busy`: another browser publisher is using the persistent profile.
- `studio_automation_failed`: Studio UI changed or a selector/timing assumption failed.
- `manual_schedule`: schedule UI has not yet been acceptance-tested.

Never bypass CAPTCHA or Google security challenges programmatically.


## Multi-account Chrome profiles

ManhwaShorts isolates each YouTube account in its own persistent Chrome user-data directory.
The existing pre-migration browser session remains available as account `default`; it is not moved or reauthenticated.

```bash
# list profiles
PYTHONPATH=. .venv/bin/python scripts/youtube_browser_account.py list

# add a profile and give it a human-readable label
PYTHONPATH=. .venv/bin/python scripts/youtube_browser_account.py add account-b "Channel B"

# during a temporary noVNC/X11 session, log in only this profile
./scripts/youtube_browser_login.sh account-b

# optionally make it the default publishing account
PYTHONPATH=. .venv/bin/python scripts/youtube_browser_account.py default account-b
```

Publishing requests may set `youtube_account_id`. The resolved account ID is persisted on the Publication row, so retrying a failed upload always reuses the same Chrome profile even if the global default changes later.
