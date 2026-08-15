# ManhwaShorts interruption-safe handoff

Authoritative local checkpoint on 2026-08-15. Read before changing code or running the pipeline.

## Repository

- Workspace: `B:\Project\manhwashorts-studio`; work locally because the VPS is off.
- Branch: `codex/final-production-silent-acceptance`.
- Latest proven checkpoint: `f00d822`, pushed to the matching origin branch; local verified equal to origin on 2026-08-15.
- Earlier checkpoints: `0c5d1e7` strict gates; `46d5b9c` exact-font karaoke; `69f0415` strict design; `7c6a197` initial review workflow.
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

## Runtime state after 2026-08-15 strict rebuild attempt (BLOCKED, not weakened)

- Local verified equal to `origin/codex/final-production-silent-acceptance` at
  `f00d822`; the 69-test matrix re-ran GREEN (`69 passed`) before any pipeline call.
- `pipeline.build_timeline` was called through the normal service boundary (no DB
  edits/monkeypatches), with `MS_DATA_DIR`/`MS_STORAGE_DIR`/`MS_DATABASE_URL`
  pointed at `data/_final_acceptance_live`, output root
  `data/_final_acceptance_strict_v2`, policy
  `review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID`,
  `review_source_root=final_test`, `provisional_duration_s=51.29`, and section
  panel IDs / integer citations read from the latest `ScriptVersion` exactly like
  `cloud_multimodal.py`. It fail-closed with
  `reference_planning_failed: visual.visual_unavailable` and persisted **zero**
  timeline rows; `render_silent_review_preview` was never reached.
- Root cause is genuine provider balloon/protected geometry, not a code path:
  a deterministic per-panel crop sweep (~16 zoom scales x 13x13 positions, and a
  31x17x17 refinement for the four cited panels) through
  `framing_analysis.candidate_is_feasible` with the review upscale warning enabled
  found:
  - hook -> beat_1_interrogation (14 opening panels): **0 feasible crops**; every
    crop fails `visual.balloon_mask_overlap` or `visual.protected_subject_coverage`.
  - setup -> beat_2_energy_clash: planner capacity 0 (the one geometrically clean
    crop at order 25, blank 0.0000, is missed by the 3 enumerated ROI phases).
  - conflict -> beat_3+beat_4: capacity 8 (strict crops at orders 49/52 in beat_3).
  - twist -> beat_5: capacity 1 (order 85, blank 0.0000).
  - cta -> beat_6: capacity 7 (order 108, blank 0.0000).
  - The four script-cited evidence panels (orders 35, 83, 54, 81) are each
    infeasible across ~8,400 crops: balloon overlap or protected-subject coverage.
- Full audit numbers are in the ignored
  `data/_final_acceptance_strict_v2_diagnostic/feasibility-audit.txt`.
- `data/_final_acceptance_live/live.db` remains `timeline_scenes=0`,
  `render_jobs=0`; no half-created job, no FFmpeg process. Old rejected MP4 at
  `data/_final_acceptance_live/output/final-test-repaired/final_test_silent_preview.mp4`
  must still not be delivered.
- Project ID `5a839c82f30841a7811d557913575f71`; 118 panel regions; repaired
  script v1 = 118 words / 51.29 s. Do not kill observed Python processes without
  checking command lines (they belonged to Codex/uv infrastructure).

## Why this is a hard stop (and what is NOT allowed)

The only section with zero feasible crops under a full deterministic sweep is the
opening (`hook` -> `beat_1_interrogation`). Producing an MP4 would require one of
the forbidden moves, all of which were refused:

- Move later-beat evidence into the opening beat (violates chronology/evidence).
- Weaken balloon, protected-art, blank (3%), lineage, font, or subtitle gates.
- Fabricate a mask or relabel a balloon/protected region.
- Bypass `pipeline.build_timeline` with a manual DB write or monkeypatch.

None were done. The gates are intact and the render correctly fails closed.

## Exact resume (only after the opening-beat evidence problem is resolved)

1. Confirm `git status --short --branch`, `git log -3 --oneline`; expect HEAD at or
   after `f00d822` and only `?? final_test/`.
2. Re-run:

```powershell
& .venv\Scripts\python.exe -m pytest tests\test_regular_render_karaoke.py tests\test_sentence_karaoke_preview.py tests\test_reference_visual_review.py tests\test_review_source_upscale.py tests\test_editorial_qc.py -q
```

Expected checkpoint: 69 passed.

3. The real prerequisite is a truthful visual-evidence correction for the opening
   beat (or an alternate evidence-covered source). Two sanctioned options:
   - Re-observe the opening panels with the authorized provider and persist balloon/
     protected geometry that admits a clean crop, then re-run the repair + timeline.
   - Supply alternate source art for the opening beat that is genuinely balloon/
     protected/blank-clean.
   Do NOT fake this. Until `beat_1_interrogation` has >=1 feasible crop, every
   later step is moot.
4. Only once the opening beat is feasible, rebuild via normal `pipeline.build_timeline`
   with `MS_DATA_DIR`/`MS_STORAGE_DIR`/`MS_DATABASE_URL` on
   `data/_final_acceptance_live`, output root `data\_final_acceptance_strict_v2`,
   policy `review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID`,
   `review_source_root=final_test`, `provisional_duration_s=51.29`, section panel
   IDs from `evidence_panel_ids` and citations from integer `citations` (mirror
   `cloud_multimodal.py`). Verify scenes persisted, then call
   `pipeline.render_silent_review_preview`.
5. On success verify sidecar: font `Barber Chop` and hash; max active width <= safe
   width; clearance >=120; max lines <=2; every blank fraction <=0.03. Then FFprobe,
   blackdetect, contact sheet, and boundary/key-frame inspection.
6. Commit/push every green correction. Update this file with exact results/paths.
   Merge to `main` only after user accepts the replacement preview.

Stop if strict render is blocked or unreviewed. Never relax gates just to emit MP4.
Rollback: `46d5b9c` subtitle-only; `0c5d1e7` strict gates; `f00d822` strict handoff.
