# Operations

Run heavy work on the designated execution host. In the current deployment the
bridge/VPS is orchestration/transport only and Oracle is the execution machine for
provider calls, FFmpeg, renders, and full regression tests.

## Start and environment

```bash
.venv/bin/python -c "from app.services.render import check_environment; print(check_environment() or 'OK')"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Keep the service behind an authenticated tunnel/SSH forward rather than exposing a
raw development server publicly. A standalone render worker is available with
`.venv/bin/python scripts/worker.py`.

## Production workflow

1. Ingest source material and preserve source/rights metadata.
2. Run the visual/story analysis flow and inspect any review boundary.
3. Generate/edit the grounded script and approve the exact script identity.
4. Generate voice/timing and timeline through the normal pipeline facade.
5. Run quality checks; resolve blocking evidence/media/QC failures.
6. Queue or resume final render rather than duplicating a valid successful job.
7. Verify final media integrity and post-render QC.
8. Verify the automatic thumbnail package and thumbnail QC.
9. Upload/publish only through the supported explicit operator/API boundary.

## Rights behavior

Rights fields/status remain useful audit metadata. The current default is
`MS_REQUIRE_RIGHTS_DECLARATION=false`; missing declarations do **not** block
render/publish under that default. Do not turn enforcement on as an incidental
operational fix. If enforcement is intentionally enabled, policy findings become
blocking according to `app/services/policy.py`.

## Verification commands

Fast/targeted checks:

```bash
.venv/bin/ruff check app tests
.venv/bin/python -m compileall -q app tests
.venv/bin/pytest -q tests/contracts/test_service_dependency_graph.py
.venv/bin/pytest -q <affected test paths>
git diff --check
```

Cross-service/refactor/release changes also require `.venv/bin/pytest -q`.
Use current test paths documented in `MAINTAINER_GUIDE.md`; do not revive removed
historical monolithic test files.

## Recovery

- Check for an already-running long process before starting another one.
- Reuse durable visual/story/narration/render state when identity checks say it is
  current. Do not repeat expensive provider work solely because a chat resumed.
- Expired render leases are recovered through the normal worker boundary.
- Retry preserves previous job/audit records.
- Never repair state by hand-editing the DB, QC JSON, or final artifact.

## Repository hygiene

Before commit/push:

```bash
git status --short
git diff --check
git diff --cached --stat
```

`data/`, `manhwa/`, and `ms_env.sh` are expected runtime-only paths in the current
checkout. Never stage them. Secrets/tokens must not appear in shell commands,
patches, logs, or documentation.

If the execution host lacks GitHub credentials, create a Git bundle for the source
commits and push it from the authenticated bridge. Do not install/copy a personal
token into the execution host just to make a push succeed.
