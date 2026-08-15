# ManhwaShorts interruption-safe handoff

Authoritative local checkpoint on 2026-08-15. Read before changing code or running the pipeline.

## Repository

- Workspace: `B:\Project\manhwashorts-studio`; work locally because the VPS is off.
- Branch: `codex/final-production-silent-acceptance`.
- Latest proven checkpoint: `0c5d1e7`, pushed to the matching origin branch.
- Earlier checkpoints: `46d5b9c` exact-font karaoke; `69f0415` strict design; `7c6a197` initial review workflow.
- Do not merge `main` until a replacement preview passes visual review.
- `final_test\` is user source and intentionally untracked. Never commit it, media, DBs, caches, keys, `.env`, provider payloads, or credentials.

## User-approved acceptance

The old MP4 is technically valid but visually rejected: captions cross the frame, font appearance is wrong/fallback, and blank bands remain. It is not a baseline or success proof.

Read `docs/superpowers/specs/2026-08-15-strict-visual-acceptance-design.md` and `docs/superpowers/plans/2026-08-15-strict-visual-acceptance.md`.

- English uppercase punctuation-free sentence-held karaoke; yellow active word at 1.08 scale; maximum two lines.
- Exact `assets/fonts/BarberChop.otf` for Pillow and libass. Its embedded family is `Barber Chop`; `BarberChop` is the invalid alias that caused fallback.
- Every active state stays inside a 120 px horizontal safe margin at 1080x1920.
- Edge-connected blank target is at most 3%. Never treat `visual.blank_infeasible` as a warning/pass.
- Do not weaken balloon, protected-art, chronology, evidence, or lineage gates.
- Success requires a new 50-60 second silent MP4, measured QC, FFprobe/blackdetect, contact sheet, and human frame inspection. MP4 existence alone is not success.

## Proven implementation

`46d5b9c`:

- `app/config.py` uses embedded family `Barber Chop`.
- `app/services/render.py` rejects missing/mismatched fonts, measures active scaling, splits overflowing chunks, removes unsafe `\\pos(...)`, and records font hash, maximum width, safe width, and clearance.
- Verification: karaoke suites, **21 passed**.

`0c5d1e7`:

- Reference blank target is `0.03`.
- Renderer raises `visual.blank_infeasible` above target.
- QC cannot bypass failure via `fallback_reason`.
- Review bundle requires measured subtitle evidence and per-shot blank telemetry.
- Verification matrix: **69 passed**, plus strict fallback-bypass regression **1 passed**.

A broader `test_reference_profile_integration.py` run was not fully green: four pre-existing Windows SQLite safety-path setup errors (`sqlite:///B:\...` does not contain `/data/test_runs/`) and four fallback-ledger failures in broad mixed order. They were not skipped or claimed green; reproduce in isolation before changing product logic.

## Runtime state after explicit stop

- Attempted service resume was interrupted during `build_timeline` before persistence.
- `data/_final_acceptance_live/live.db`: `timeline_scenes=0`, `render_jobs=0`; there is no half-created job.
- No FFmpeg process was active at stop.
- Old rejected diagnostic: `data/_final_acceptance_live/output/final-test-repaired/final_test_silent_preview.mp4` (50 s, 1080x1920, H.264, 60 FPS). Do not deliver it.
- Old contact sheet: `data/_qa_latest/contact.jpg`.
- Project ID: `5a839c82f30841a7811d557913575f71`; 118 panel regions; repaired script v1 is 118 words / 51.29 seconds.
- Do not kill existing Python processes without checking command lines; observed processes belonged to Codex/uv infrastructure.

## Exact resume

1. Confirm `git status --short --branch`, `git log -3 --oneline`; expect HEAD at or after `0c5d1e7` and only `?? final_test/`.
2. Re-run:

```powershell
& .venv\Scripts\python.exe -m pytest tests\test_regular_render_karaoke.py tests\test_sentence_karaoke_preview.py tests\test_reference_visual_review.py tests\test_review_source_upscale.py tests\test_editorial_qc.py -q
```

Expected checkpoint: 69 passed.

3. Rebuild via normal `pipeline.build_timeline`, not manual DB edits/monkeypatches. Use the latest `ScriptVersion`; build section panel IDs from `evidence_panel_ids` and citations from integer `citations`, exactly like `app/services/cloud_multimodal.py` around its review preview call.

- `MS_DATA_DIR=B:\Project\manhwashorts-studio\data\_final_acceptance_live`
- `MS_STORAGE_DIR=B:\Project\manhwashorts-studio\data\_final_acceptance_live\storage`
- `MS_DATABASE_URL=sqlite:///B:/Project/manhwashorts-studio/data/_final_acceptance_live/live.db`
- NEW output root, e.g. `data\_final_acceptance_strict_v2`
- policy `review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID`
- `review_source_root=B:\Project\manhwashorts-studio\final_test`
- `provisional_duration_s=51.29`

Verify scenes persisted, then call `pipeline.render_silent_review_preview` through the service boundary.

4. Likely next blocker: old protected geometry permits broad crops. Resolve through same-panel tighter ROIs and evidence-grounded alternative panels. Do not revert the 3% target or restore the fallback-pass condition.
5. On success verify sidecar: font `Barber Chop` and hash; max active width <= safe width; clearance >=120; max lines <=2; every blank fraction <=0.03. Then FFprobe, blackdetect, contact sheet, and boundary/key-frame inspection.
6. Commit/push every green correction. Update this file with exact results/paths. Merge to `main` only after user accepts the replacement preview.

Stop if strict render is blocked or unreviewed. Never relax gates just to emit MP4. No provider call is currently needed because analysis/script/panel evidence exist locally. Rollback: `46d5b9c` subtitle-only; `0c5d1e7` complete strict gates.
