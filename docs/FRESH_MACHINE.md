# Fresh-machine installation

ManhwaShorts treats a new machine and an upgrade as the same reproducible bootstrap path. The supported automatic host setup is Debian/Ubuntu; Ubuntu 24.04 amd64 is the primary production target.

## One-command setup

```bash
git clone https://github.com/yxxrn/manhwashorts-studio.git
cd manhwashorts-studio
./install.sh
```

`install.sh` installs Python/venv, FFmpeg + FFprobe, libass-capable Ubuntu FFmpeg, espeak-ng, Tesseract, the checked-in subtitle font dependencies, Google Chrome on amd64, and Java 21. It then creates/repairs `.venv`, installs exact Python requirements, creates `.env` only when missing, installs the pinned/checksummed Suwayomi JAR, applies `alembic upgrade head`, and runs the readiness check.

Use `./install.sh --systemd` on a server that should start ManhwaShorts after reboot. Use `--without-suwayomi` when source acquisition is managed elsewhere. `--dry-run` shows the plan without modifying the host.

## Database rule

Normal runtime startup never bootstraps schema with SQLAlchemy `create_all`. Alembic is the single source of truth for both an empty database and future upgrades. Test databases retain `create_all` only inside `MS_TEST_MODE=1` for speed and isolation.

A fresh SQLite database therefore receives a real `alembic_version` row on its first startup. An existing database is upgraded to the checked-in head before the API begins serving requests.

## Machine check

```bash
scripts/manhwashorts doctor
```

The check covers Python/venv packages, FFmpeg/FFprobe and required filters, subtitle font, TTS executable, Chrome/Playwright, Java/Suwayomi when enabled, writable runtime directories, and exact database revision. YouTube login is intentionally reported separately because Google login/2FA remains a human action.

For agents or scripts:

```bash
scripts/manhwashorts doctor --json
```

## YouTube state

Chrome profiles remain outside Git under the current OS user's home directory. Moving to a new machine does not copy Google credentials automatically; create or select the account profile and perform one normal interactive Google login. Never transfer passwords or raw cookies through Git.

## Upgrade an existing machine

Pull the new code and rerun the installer. It preserves `.env`, `data/`, browser profiles, and provider credentials; package installation and Suwayomi setup are idempotent, and Alembic applies only pending migrations.
