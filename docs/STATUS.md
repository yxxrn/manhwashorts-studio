# Current Status

Last synchronized with the maintainability refactor baseline published on
2026-08-29. For live truth, current `main`, tests, and persisted production
artifacts override historical benchmark notes.

## Current verified state

- Maintainability refactor is merged to `main`; baseline commit: `4749633`.
- `app/services/pipeline.py` is now a stable facade over
  `app/services/pipeline_stages/`.
- `app/services/cloud_multimodal.py` is a stable facade/orchestration surface over
  `app/services/cloud_runner_parts/`.
- Application import graph has zero circular dependency groups and is protected by
  `tests/contracts/test_service_dependency_graph.py`.
- Tests are organized by unit/contracts/integration/render/production/cloud/API/
  migrations, with shared builders in `tests/factories/`.
- Refactor release gate collected 1,520 tests and completed 100% with exit code 0.
- Documentation synchronization added five contract checks; duration-policy hardening adds four more. Current collection is 1,529 tests.
- The current duration-policy release gate completed the full 1,529-test suite at 100% with exit code 0; Ruff, compileall, and `git diff --check` are green.

## Production behavior

The production pipeline remains functionally unchanged by the refactor. Script
approval, evidence/lineage validation, media integrity, and strict QC are blocking
contracts. Rights metadata remains recorded for audit, but enforcement is disabled
by default (`require_rights_declaration=False`) and therefore does not block
render/publish unless explicitly enabled by configuration.

Automatic thumbnail generation is enabled for successful production. The package
contains `thumbnail.jpg`, `thumbnail_clean.jpg`, up to three ranked variants,
`thumbnail_meta.json`, and `thumbnail.qc.json`. Text uses outline/shadow styling
without the old wide black banner.

Duration policy is now explicit and single-sourced: new projects default to 55s, the normal final-production acceptance window is 50-60s, and the general schema/ceiling remains 10-90s. `coherent_capacity_adaptive_v1` may shorten review/diagnostic pacing when grounded visual capacity is insufficient, but a sub-50s adaptive policy is now blocked before TTS/timeline/render in final production.

A historical adaptive production artifact used during the refactor no-op verification remains on disk:

- project: `acdb918636ee4797b759113627432f08`
- render job: `9b35e931ee814f03a2c3f61efbe82a51`
- final size: 13,411,158 bytes
- final SHA-256: `0a75579e7dddecb526453ce2c6ab558711a2cc1cba772437ea4f74e16665406b`
- thumbnail QC: PASS
- measured duration: 25.083s (historical adaptive artifact; no longer valid as a new final-production duration)

Preserved acceptance artifacts in `data/p0-aws-acceptance/` remain above 50 seconds (for example a 53.033s silent preview and voiced renders around 51.1-51.2s). They are separate from the later `acdb...` project whose adaptive path produced the 25.083s historical final. The 25.083s file therefore must not be interpreted as the product target. Under the corrected duration contract a new sub-50s adaptive artifact fails final production.

The final MP4 remained byte-identical across the refactor. A last direct remote
invocation of the production operator entrypoint was prevented by the automation
tool's execution safety layer, not by an application failure; the full regression
suite and persisted artifact integrity checks are green.

## Current repository hygiene

Expected local runtime-only paths are `data/`, `manhwa/`, and `ms_env.sh`. They
must not be staged. Heavy tests/renders run on the execution host, not the bridge.

## Historical records

Verbose pre-refactor status/handoff logs remain available in Git history at the
`4749633` baseline instead of being duplicated in the working tree. Historical
standalone plans remain visibly marked HISTORICAL. See `docs/history/README.md` and
`docs/MAINTAINER_GUIDE.md`.
