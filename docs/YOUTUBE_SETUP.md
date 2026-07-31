# YouTube setup

The app runs in **dry-run mode** by default: the whole publish path works, but
nothing is uploaded. A JSON receipt is written to
`data/output/dry_run_uploads/` instead. That is deliberate — you can exercise and
test the full flow without risking your channel.

Follow this only when you actually want to upload.

## 1. Create a Google Cloud project

1. Open <https://console.cloud.google.com/>.
2. Create a project (e.g. `manhwashorts-studio`).
3. Enable two APIs under **APIs & Services → Library**:
   - **YouTube Data API v3** — uploading
   - **YouTube Analytics API** — retention metrics

## 2. Configure the OAuth consent screen

Under **APIs & Services → OAuth consent screen**:

- User type: **External**
- Fill in app name, support email, developer email
- Add these scopes and no others:

```
https://www.googleapis.com/auth/youtube.upload
https://www.googleapis.com/auth/youtube.readonly
https://www.googleapis.com/auth/yt-analytics.readonly
```

These are the least privileges that let the app upload and read its own stats. It
never requests permission to delete videos or manage your account.

- Add your own Google account under **Test users**.

Leave the app in **Testing** status. Publishing it would require Google
verification, which is unnecessary for personal use. Testing mode means refresh
tokens expire after 7 days, so you will reconnect the channel weekly — an
acceptable trade for not submitting a verification request.

## 3. Create OAuth credentials

**APIs & Services → Credentials → Create credentials → OAuth client ID**

- Application type: **Web application**
- Authorised redirect URI — must match exactly:

```
http://127.0.0.1:8000/api/youtube/callback
```

Use `127.0.0.1`, not `localhost`; Google treats them as different origins. If you
run behind a domain, use the full HTTPS URL instead and keep the two in sync.

Copy the client ID and client secret.

## 4. Configure the app

In `.env`:

```bash
MS_YOUTUBE_ENABLED=true
MS_YOUTUBE_CLIENT_ID=1234567890-abcdef.apps.googleusercontent.com
MS_YOUTUBE_CLIENT_SECRET=GOCSPX-your-secret-here
MS_YOUTUBE_REDIRECT_URI=http://127.0.0.1:8000/api/youtube/callback
```

Restart the app. `GET /api/health` should now report `"youtube_enabled": true`.

Never commit `.env`. It is gitignored.

## 5. Connect your channel

In the UI: **7. Publikasi → Hubungkan YouTube**. Or via API:

```bash
curl -b ck.txt localhost:8000/api/youtube/connect
# {"authorization_url": "https://accounts.google.com/o/oauth2/auth?...", "state": "..."}
```

Open the URL, grant access, and you are redirected back with the channel
connected.

Verify:

```bash
curl -b ck.txt localhost:8000/api/youtube/channels
```

Tokens are Fernet-encrypted before storage and never returned by the API.

## 6. First upload

Keep it private and watch it back before doing anything public:

```bash
curl -b ck.txt -X POST localhost:8000/api/projects/$PJ/publish \
  -H 'Content-Type: application/json' \
  -d '{"privacy_status":"private"}'
```

Check it at <https://studio.youtube.com> under Content.

### Going public

Two gates, both required:

```bash
# 1. config, needs a restart
MS_ALLOW_PUBLIC_PUBLISH=true

# 2. per request
-d '{"privacy_status":"public","confirm_public":true}'
```

This exists so a scripting mistake cannot publish to your channel. The worst-case
failure of an over-eager automation is an unlisted video.

### Scheduling

```json
{"privacy_status": "private", "scheduled_at": "2026-08-05T10:00:00Z"}
```

YouTube requires scheduled videos to be private with a `publishAt` timestamp; the
app sets that automatically. Use UTC.

## Quota

The default daily quota is 10,000 units.

| Operation | Cost |
|---|---|
| Upload a video | ~1,600 |
| List channels | 1 |
| Read statistics | 1 |
| Analytics query | 1 |

That is roughly **6 uploads per day**. Quota resets at midnight Pacific Time.

A 403 on upload is usually exhausted quota, not a permissions problem. The error
message says so, and the failure is marked retryable:

```bash
curl -b ck.txt -X POST localhost:8000/api/publications/$PUB/retry
```

Retry reuses the existing render — it never re-encodes.

You can request more quota via the Cloud Console, but Google requires a
compliance audit for meaningful increases.

## Analytics

```bash
curl -b ck.txt -X POST localhost:8000/api/publications/$PUB/stats/sync
```

Views, likes, and comments come from the Data API and are available quickly.
Retention metrics (`averageViewDuration`, `averageViewPercentage`,
`subscribersGained`) come from the Analytics API and can take 24–48 hours to
appear on a new video.

When no data exists the app returns:

```json
{"available": false, "detail": "No analytics available yet…"}
```

It does not store a row of zeros, because zeros would be indistinguishable from a
real result in the dashboard.

## Disconnecting

```bash
curl -b ck.txt -X DELETE localhost:8000/api/youtube/channels/$CHANNEL_ID
```

Marks the channel revoked and erases the stored credentials immediately. To also
revoke access on Google's side, visit
<https://myaccount.google.com/permissions>.

## Troubleshooting

**`redirect_uri_mismatch`** — the URI in Google Cloud must match
`MS_YOUTUBE_REDIRECT_URI` character for character, including scheme, host form
(`127.0.0.1` vs `localhost`), port, and path.

**`access_blocked: app not verified`** — add your account under **Test users** on
the consent screen.

**`Invalid or expired OAuth state`** — the state token is held in memory and is
single-use. Restarting the app mid-flow invalidates it; start the connect flow
again.

**Upload succeeds but the video is not visible** — check `upload_status`. YouTube
processes uploads asynchronously, and private videos only appear in YouTube Studio
under Content.

**`could not decrypt credentials`** — `data/.fernet_key` changed. Disconnect and
reconnect the channel; back that file up alongside the database.

**Weekly reconnect prompts** — expected in Testing mode: refresh tokens expire
after 7 days. Submitting the app for Google verification removes this, at the cost
of a review process.
