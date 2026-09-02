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
MS_YOUTUBE_BROWSER_PROFILE_DIR=/home/ubuntu/.config/manhwashorts/youtube-browser-runtime
MS_YOUTUBE_BROWSER_EXECUTABLE=/usr/bin/google-chrome
MS_YOUTUBE_BROWSER_HEADLESS=true
MS_YOUTUBE_BROWSER_TIMEOUT_SECONDS=120
```

Public visibility remains double-gated:

```bash
MS_ALLOW_PUBLIC_PUBLISH=true
```

and the publish request must still send `confirm_public=true`.

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
