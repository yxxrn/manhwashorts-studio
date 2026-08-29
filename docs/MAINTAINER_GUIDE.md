# Maintainer Guide

This document explains how to extend ManhwaShorts Studio without undoing the
maintainability refactor or changing production behavior accidentally.

## Stable boundaries

### Pipeline facade

Callers import `app.services.pipeline`. Treat it as a compatibility facade. Its
public function names and signatures are deliberately stable because routers,
workers, scripts, and tests patch/call that surface.

New stage implementation belongs in:

```text
app/services/pipeline_stages/
  analysis.py
  script.py
  media.py
  quality.py
  production.py
  rendering.py
```

Do not move callers directly to `pipeline_stages` merely to remove a wrapper.
That would defeat the compatibility boundary and make future refactors harder.

### Cloud multimodal facade

`app.services.cloud_multimodal.CloudStageRunner` and `CloudBatchService` remain
compatibility surfaces. Their implementation is split by responsibility:

```text
app/services/cloud_runner_parts/
  provider.py       provider calls/checkpoints/boundaries
  visual.py         visual evidence stage
  story.py          story-map stage
  narration.py      narration stage
  repair.py         narration repair/identity/closure
  visual_repair.py  visual-narrative repair
  streaming.py      bounded visual streaming/session accounting
  batch.py          durable batch/project orchestration
  runtime.py        facade runtime binding without reverse imports
```

The runtime binding is intentional. Do not replace it with imports from a part
module back into `cloud_multimodal.py`; that recreates circular dependencies.

### Shared contracts

Dependency-light contracts live outside high-level orchestration:
`db_base.py`, `subtitle_contracts.py`, `visual_contracts.py`, and
`visual_planning.py`. If two services need the same type, move the type toward a
contract module instead of making the services import each other.

## Product gates

Current default rights enforcement is OFF. Rights declarations/status remain
useful audit metadata and may produce warnings. Do not make missing rights a
blocking error unless the product owner explicitly changes the policy and the
configuration/tests/docs are updated together.

The following remain fail-closed boundaries unless explicitly redesigned:

- exact script approval/approved identity
- visual evidence and source lineage
- narration/evidence grounding contracts
- subtitle/timeline/media validity
- post-render QC and artifact integrity

Do not "solve" a production failure by editing persisted DB rows, rewriting a QC
artifact, bypassing a facade, or weakening a validator without a product decision.

## Test organization and migration map

Historical documents may contain these old paths:

| Historical path | Current location |
|---|---|
| former monolithic cloud mass-production test (removed) | split across `tests/cloud/test_*.py` |
| former root pipeline test | `tests/production/test_pipeline.py` |
| former root operator CLI test | `tests/production/test_operator_cli.py` |
| former root strip-segmentation test | `tests/unit/test_strip_segmentation.py` |
| former root vision-pipeline test | `tests/integration/test_vision_pipeline.py` |
| former root thumbnail test | `tests/render/test_thumbnail.py` |

Never recreate a deleted monolithic test file just to make an old command work.
Select the current domain suite instead.

Pytest markers are defined in `pyproject.toml`: `unit`, `contracts`, `integration`,
`render`, `production`, `migrations`, `api`, `cloud`, `slow`, and `ffmpeg`.

## Verification strategy

For a small isolated change, run the affected test file(s), architecture guard,
Ruff, compileall, and diff check. For a refactor, release, or cross-service change,
run the full suite before merging to `main`.

```bash
.venv/bin/ruff check app tests
.venv/bin/python -m compileall -q app tests
.venv/bin/pytest -q tests/contracts/test_service_dependency_graph.py
.venv/bin/pytest -q <affected paths>
.venv/bin/pytest -q
git diff --check
```

A full test count written in an old document is evidence for that checkpoint, not
a target. Use `pytest --collect-only -q` when the current count matters.

## Runtime and Git safety

The designated execution host owns heavy provider/render/test work. The bridge is
transport/orchestration only. Check for an existing process before starting a
long provider or render job; resume valid checkpoints rather than duplicating work.

Never commit runtime data, media, DBs, provider caches, secrets, or source input.
In the current deployment `data/`, `manhwa/`, and `ms_env.sh` are intentionally
untracked. Before committing, inspect both `git status --short` and the staged diff.

## Documentation policy

Active/current documents are `README.md`, `AGENTS.md`, `docs/STATUS.md`,
`docs/ARCHITECTURE.md`, this guide, `docs/OPERATIONS.md`, and
`docs/RELEASE_RUNBOOK.md`. Historical plans/handoffs are retained for forensic
context and must be visibly marked HISTORICAL. Update active docs in the same
change whenever paths, gates, contracts, or operator workflow change.
