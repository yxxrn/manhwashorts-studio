# Fresh-machine installation

ManhwaShorts uses one portable deployment contract on Linux and Windows. `.env`
is the single machine config; runtime media/database state stays outside Git.

## One-command setup

Linux/Ubuntu:

```bash
./bootstrap.sh
```

Windows native:

```text
bootstrap.cmd
```

Bootstrap installs or verifies Python 3.11+, FFmpeg/FFprobe with libass,
Tesseract, Chrome, Java 21, the pinned Suwayomi JAR, Asura Scans, Read Comics
Online, Pocket TTS, database migrations, and machine readiness. Linux also
installs systemd services; Windows creates login-startup launchers.

Pocket TTS uses `alba`. INT8 is preferred when the host supports `torchao`; local
FP32 is the automatic fallback on platforms where quantization is unavailable.

## Move an existing deployment

Copy only the configuration you intentionally want to preserve, then seed it:

```bash
./bootstrap.sh --config /secure/path/manhwashorts.env
```

```text
bootstrap.cmd -ConfigPath "C:\secure\manhwashorts.env"
```

The supplied file becomes `.env` only when the target clone has no `.env`. A
rerun never replaces an existing deployment config. On Linux `.env` is locked to
mode `0600`.

Legacy `ms_env.sh` is migration-only. If found, bootstrap merges safe `MS_*`
assignments into `.env` without printing values, then moves the legacy file to a
private runtime backup. New deployments must not create a second env file.

## YouTube account bootstrap

Recommended path: export Netscape `cookies.txt` from a browser already signed in
to the target YouTube account, then run bootstrap with account arguments. Example:

```bash
./bootstrap.sh --youtube-account rurushortss --youtube-cookies /secure/cookies.txt
```

```text
bootstrap.cmd -YouTubeAccount rurushortss -YouTubeCookies "C:\secure\cookies.txt"
```

ManhwaShorts imports only Google/YouTube cookies, requires a complete login
session, verifies YouTube Studio headlessly, and persists the authenticated
Chrome profile. Cookie values are never returned/logged by the import path. The
source `cookies.txt` is not copied into the repo and may be deleted after a
successful import. Interactive Chrome login is only a fallback for expired
cookies, 2FA, CAPTCHA, or explicit Google re-verification.

## Machine check

```bash
scripts/manhwashorts doctor
```

The required checks include `.env`, Python/venv packages, FFmpeg filters,
Tesseract, Pocket TTS, Chrome/Playwright, Java/Suwayomi, exact Asura Scans and
Read Comics Online source IDs, writable runtime paths, and the Alembic schema.
YouTube authentication is reported separately because it is optional until
publishing is requested.

## Upgrade

Pull the new code and rerun the same bootstrap. Existing `.env`, runtime data,
database, browser profiles, and provider credentials are preserved. Setup steps
are idempotent and Alembic applies only pending migrations.
