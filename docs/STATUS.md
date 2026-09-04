# Current Status

Last synchronized with current production behavior on 2026-09-04. Current code, tests, runtime health, and accepted artifacts override older benchmark/handoff notes below.

## Current verified state

- The stable orchestration boundaries remain `app.services.pipeline` → `pipeline_stages/` and `app.services.cloud_multimodal` → `cloud_runner_parts/`; the application import graph is contract-tested for cycles.
- Production final duration remains 50–60s with a 55s default target; 1080×1920, 60 FPS H.264/AAC is the accepted final media profile.
- Visual/story analysis is durable and resumable. Valid segmentation, visual/story, narration/repair, TTS, timeline, render, and thumbnail identities are reused instead of repeating expensive work after interruption.
- Vision observation now uses fail-closed bounded concurrency of 3 while preserving original chunk ordering, per-chunk retry/cache boundaries, adjacent-overlap reconciliation, and full schema/lineage/evidence coverage gates. Exact production frameability/section-safety results use a persistent content/evidence/profile/version-keyed cache; corrupt or stale entries are recomputed rather than trusted.
- Visual planning is evidence-first and lineage-preserving: exact persisted panels/ROIs are selected under chronology/framing/face/protected-region constraints, capped at 4s per shot, animated with deterministic in-shot motion, and joined with editorial fades. Repetition, static holds, jitter, black frames, subtitle timing, A/V drift, and artifact integrity are QC'd.
- Source acquisition supports ordinary uploads plus an optional localhost Suwayomi sidecar. Suwayomi imports preserve chapter/page order and enter the same source/evidence pipeline; a corpus cannot be mutated through that connector after analysis exists.
- Fresh-machine lifecycle is Alembic-first and reproducible through `install.sh`/`scripts/manhwashorts doctor`; Chrome and Java/Suwayomi readiness are included when enabled.
- YouTube publishing is browser-first through YouTube Studio. The runtime Data API publisher is archived; each account/channel uses an isolated persistent Chrome profile.
- Visibility defaults to `private`. Explicit `unlisted` and `public` requests are honored directly; `confirm_public` remains accepted only as a legacy compatibility field and has no effect.
- Per-account `trust_channel_defaults` may trust channel Upload defaults for video language, title/description language, and category. Title, description, tags, thumbnail, audience, and visibility remain uploader-controlled. The global fallback defaults to false.
- Video publish success requires post-click verification of the matching Studio Content/Shorts row and requested visibility. Thumbnail failure is non-blocking; standalone thumbnail retry through the archived Data API path is not supported.
- Local agents can advance `/api/projects/{id}/run` through `until: "publish"`. Trusted-agent approval is allowed only for an explicit publish request with `approval_mode: "trusted_agent"` and `confirm_publish_intent: true`; ordinary UI/manual approval is unchanged.
- Rights/source metadata remains auditable. `MS_REQUIRE_RIGHTS_DECLARATION=false` is the production default, so missing declarations do not block render/publish unless a deployment intentionally enables enforcement.
- The production service was verified healthy after the browser-account/trust-defaults update (`GET /api/health` HTTP 200, version 1.7.0, YouTube enabled, no reported problems).

## 2026-09-04 Run 4 production performance checkpoint

- Fresh project `9c89e873dfc648b08c7cac14982914a9`: Infinite Mage chapters 174-177, Asura Scans (EN), 87 downloaded pages, 249 source assets, 411/411 reconciled panels, and 41 observation chunks. No YouTube publication was performed.
- Cold observation completed in 762.131s (12m42s) with concurrency 3, 44 actual provider calls for 41 chunks, three transient-invalid single-chunk retries, peak concurrency 3, and effective concurrency 2.885. The Run 3 serial baseline was 1,960.684s (32m41s) for 34 chunks.
- Cold exact frameability evaluated 193 eligible panels in 359.473s with 193 misses. A post-benchmark exact warm replay hit 193/193 entries and completed the frameability call in 0.062s; the cold benchmark itself remains uncontaminated.
- Visual analysis completed in 1,331.376s (22m11s), synthesis in 29.894s, TTS in 15.820s, timeline planning in 68.562s, final render in 203.747s, and production orchestration in 325.498s. Total clean wall time was 1,809.949s (30m10s).
- Final artifact `data/output/9c89e873dfc648b08c7cac14982914a9/final.mp4` is 50.700s, 1080x1920, 60 FPS, H.264/AAC, SHA-256 `cf24e35914943de0d11101ba696fc5aa00f227e2dc91ed6ff87b53262276102a`. Post-render QC has zero failures; thumbnail and manual-upload metadata packages passed; timeline has 16 scenes and zero consecutive panel repeats.
- Run 5 profiling candidates, measured but intentionally not changed during Run 4: ~179.878s of non-observation/non-frameability/non-synthesis analysis CPU work (segmentation/panel transport/window materialization needs finer profiling); 68.562s timeline exact-ROI planning; 203.747s render across per-scene encode/join/subtitle passes; duplicate thumbnail generation (23.435s inside render plus 17.695s post-QC); and 15.297s metadata generation dominated by the grounded title-model call.
- Release validation after Run 4: the synthesis-provider boundary now preserves analysis-domain reconciliation failures instead of misclassifying them as transport failures. Targeted vision/story/production regressions, Ruff, compileall, dependency-graph checks, diff-check, and the clean-environment full 1,693-test suite all pass. Production ms_env.sh must not be sourced for the BYOK test suite because those tests intentionally assert the no-environment-key fallback.

## 2026-09-04 Run 5 production performance checkpoint

- Fresh project `b4e1260a851c4a9384efa1fec8f15c42`: Infinite Mage chapters 169-173, Asura Scans (EN), 119 downloaded pages, 332 source assets, 523/523 reconciled panels, and 53 observation chunks. No YouTube publication was performed.
- Source import took 223.482s total: resolve 1.207s, download 147.807s, ingest 74.468s. The first cold analysis did not produce a valid uninterrupted clean benchmark because two real fail-closed production blockers were discovered; repair/debug wall time is intentionally not folded into a fake clean total.
- Cold exact frameability now evaluates 225 eligible panels in 198.671s with 225 misses, versus Run 4 at 359.473s for 193 panels. Normalized cost improved from 1.863s/panel to 0.883s/panel, a 2.109x throughput improvement. Warm replay hit 225/225 entries in 0.079s.
- Panel transport uses bounded local concurrency 3 and measured 60.773s for all 523 panels during recovery. Observation recovery reused 53/53 validated chunk caches in 0.030s with zero provider calls; the initial cold observation completed all 53 chunks under the existing concurrency-3 provider contract without a retry storm.
- Synthesis initially failed closed on `production_visual_selection_insufficient` even though exact section-safe capacity was sufficient (generic 84; hook/conflict 56; setup/twist/cta 47). Corrective synthesis now deterministically completes only grounded section-safe visual-support evidence while preserving narration, claims, roles, ordering, and original claim evidence; it still fails closed when truthful capacity is insufficient. Recovery synthesis completed in 107.520s.
- Final script is 120 words with trusted-agent approval. TTS produced five segments in 23.447s. Final timeline has 16 scenes, zero consecutive panel repeats, and a release-candidate timing measurement of 42.579s after the final safety fixes, versus 68.562s in Run 4 (37.9% faster despite the larger corpus).
- The first render attempt failed closed on `visual.blank_infeasible` because two ROIs from the same panel passed planner geometry but failed render-time exact pixel blank refinement. ROI enumeration now records the minimum exact pixel-refinement threshold, both feasibility capacity and final selection enforce that same threshold, and the final timeline passed 16/16 exact pixel preflight frames before render. The gate was not relaxed.
- Release-candidate timeline rebuild reproduced the same 16 panel/ROI selections recorded in `final.render.json`; `run_production` therefore correctly reused the already-QC-passed render identity rather than re-encoding an identical video. Final render recovery had taken 206.046s. The final artifact `data/output/b4e1260a851c4a9384efa1fec8f15c42/final.mp4` is 50.017s, 1080x1920, 60 FPS, H.264/AAC, SHA-256 `de2351e383872361db85371b2a7b6f1d195bef46ad4059fb21ccc488be5c45a0`. Post-render QC and thumbnail QC pass, manual-upload metadata passes, and publication count is zero.
- Thumbnail generation is now idempotent before headline/provider work: the first final thumbnail pass took 30.519s and the second orchestration call reused the valid package in 0.067s, versus Run 4's redundant second pass of 17.695s (about 266x faster for the repeat call). Metadata generation measured 13.208s.
- Run 6 performance candidates: reduce the ~206s multi-pass render path; cache/reuse exact ROI pixel-refinement preflight identities; decode each source image once for source-group panel transport rather than only parallelizing per-panel work; evaluate bounded parallel TTS where provider semantics permit; and cache grounded metadata/title generation by approved-script identity.
- Run 6 quality experiments (not implemented in Run 5): eliminate repeated thumbnail headlines across production history with a persisted headline-history/novelty gate; reject exact duplicates and strongly penalize/reject near-duplicates while retaining story-grounded hook strength. Choose one safe text placement from top/middle/bottom using reproducible pseudo-random selection only among placements that pass face/protected-subject/text-safe overlap checks. Choose thumbnail text color from exactly yellow/red/blue/green based on measured contrast/readability and panel harmony instead of defaulting to yellow. Do not manufacture three copies merely to vary placement. Also investigate the current nonblocking `subtitle.too_fast`, repeated-camera-curve/ROI, restless-camera, and asset-cooldown warnings as quality experiments without weakening evidence or render QC.
- Run 5 release validation is complete: the final targeted dependency/vision/reference/thumbnail suite passed, the clean-environment full pytest suite reached 100% with exit code 0 in 595.02s, and full Ruff, compileall, staged diff-check, final artifact/QC/database verification, and runtime health checks passed. Production code commit `5887819ec05a929cb75ea162a78ba7d20a0dbb95` was pushed to GitHub `main`; runtime-only `data/`, `manhwa/`, and `ms_env.sh` remain untracked.

## 2026-08-31 local aggregate benchmark checkpoint

- Frozen code baseline for this run: `be01cda00c1bd90b3abc910273bf86598d5a38fd`
  (`main` matched `origin/main` before the run).
- Fresh aggregate namespace: `data/production-benchmark-crazy-demon-20260831-cold-v1`;
  project `eeae59a27baf421590c034522e619903`; source chapters 207, 208, 209, 210,
  and 211 were imported as one ordered project with 77 image assets.
- Visual stage checkpoint: 410/410 admitted panels, 327 provider requests, 16
  retries, concurrency 8, one writer, and no rejected panels. Preparation elapsed
  1,856.937 seconds. No visual requests were repeated during resume.
- Resume reached `STORY_MAPPED` using the durable visual checkpoint. Narration then
  stopped closed with `cloud.narrative_not_grounded` (`field=passage_evidence;count=5`);
  the provider response was not retained as a production narration artifact.
- No MP4, TTS, audio, or QC delivery artifact exists from this run. The benchmark is
  therefore not a production-success claim. A local adapter fix now derives an
  omitted passage evidence list from its claim evidence while retaining strict
  foreign/incomplete-reference rejection; its regression and related cloud tests
  are the next gate before resuming narration.
- The post-fix resume reused the visual/story checkpoints with zero new visual
  requests and two narration requests, then failed closed on `field=ending_kind`.
  Retry guidance now explicitly requires the v3 outline keys, supported ending
  kinds, and matching final punctuation; this focused regression is green but has
  not yet produced a valid narration or preview artifact.

## 2026-08-31 review preview artifact checkpoint

- The same fresh aggregate job completed through the normal operator review
  entrypoint at `9371a16268b5cf24428450f657248b3b8bc897ae`. The source was the
  ordered five-chapter prefix `output/The Return of the Crazy Demon`, chapters
  207 through 211: 77 image assets, 77 reconciled source assets, and 410
  admitted visual panels. The visual/story/narration stages were resumed from
  durable checkpoints; no visual request was repeated.
- Final review state is `REVIEW_PREVIEW_READY` /
  `VISUAL_ONLY_WAITING_FOR_VOICE`, with `PENDING_EDITORIAL_REVIEW` and
  `publish_allowed=false`. The review artifact is:
  `data/production-benchmark-crazy-demon-20260831-cold-v1/output/eeae59a27baf421590c034522e619903/review/silent_preview.mp4`
  (24,419,747 bytes, SHA-256
  `A0564A0F5A057E5C6E0A50CC681F96E9E2C7815C1276B684340049A529DA4747`).
- FFprobe/QC: 53.483333 seconds, 1080x1920, 60/1 FPS, H.264 High,
  yuv420p, one video stream, zero audio streams, and zero blackdetect events.
  QC has zero blocking codes. The two explicit warnings are
  `review.source_upscale_non_native` and `visual_review_pending`.
- Visual audit: 15 shots and 15 unique panels (source orders
  196, 252, 195, 212, 213, 231, 241, 244, 245, 256, 259, 307, 317, 321,
  330), 3.333-3.913 seconds per shot (median 3.623, p95 3.913, max 3.913),
  maximum unchanged hold 0.787 seconds, reuse streak 1, no immediate/near
  repeats, four motion modes, zero jitter violations, and 14 planned/14
  visible transitions. Corroborated maximum edge blank fraction is 0.08.
- Subtitle audit: 123 word cues, punctuation-free display, maximum two lines,
  827.52px maximum active width within an 828px safe width, spoken text
  unchanged, and review-provisional display pacing. Narration is 123 words,
  estimated at 53.48 seconds across five passages.
- Provider accounting for this job is 327 visual requests with 16 retries at
  concurrency 8/peak in-flight 8, plus two narration requests and no narration
  repair requests. The observed wall clock from the accepted cold start
  `2026-08-31T11:15:16.3334999Z` to final QC completion at approximately
  `2026-08-31T12:26:09Z` was about 70m52.7s, including one safe durable resume
  after a no-progress watchdog stop. This is an artifact-success checkpoint,
  not a claim of an uninterrupted zero-intervention cold benchmark.
- Required review files are adjacent to the MP4: `artifact_manifest.json`,
  `qc_report.json`, `visual_diversity_metrics.json`,
  `edit_shot_plan.json`, `causal_map.json`, `narration_spoken.txt`,
  `display_cues.json`, `silent_preview.srt`, `ffprobe.json`,
  `blackdetect.txt`, `contact-sheet-69-frame.jpg`, and frame-audit folders.
  TTS, audio, voiced rendering, and publication remain intentionally deferred
  until editorial review; this silent artifact must not be treated as final
  upload-ready output.

## 2026-08-31 approved final voiced artifact checkpoint

- Editorial approval was recorded through the normal pipeline boundary after
  review of the silent artifact: actor `local-editorial-review`, script
  `0c5945d743294d2482adae4f6b34ef25`, version `1`, approved script hash
  `0f3cb8e2615473f0c4a524c23af15123cb88062b5db790a872b0ca7c585a849a`.
  Approval was recorded at `2026-08-31T23:45:07.8751637+07:00` and completed
  at `2026-08-31T23:45:09.8873458+07:00`.
- The subsequent normal production run completed with exit code 0 in
  `434.9s` (`2026-08-31T23:47:42.8857619+07:00` to
  `2026-08-31T23:54:57.7866174+07:00`). It reused the approved script and
  existing visual/story checkpoints; it did not repeat vision/story work.
  Render job `5eed31543e784026acf29eb9e0175ca7` is `succeeded`.
- Final voiced MP4:
  `data/production-benchmark-crazy-demon-20260831-cold-v1/output/eeae59a27baf421590c034522e619903/final.mp4`,
  25,133,638 bytes, SHA-256
  `98899E680E8100E60954326D52EC20F47D0AE97BE4600F63BE1D2D86BB18CB59`.
  Independent FFprobe reports `50.667000s`, 1080x1920, 60/1 FPS, H.264
  High/yuv420p video and AAC LC, 48 kHz, stereo audio. No external upload or
  publication was performed.
- The configured voice profile is provider `http`, model `grok-voice-latest`,
  voice `the-explainer-american`, locale `en`, with one persisted profile hash
  `74ebde6c9084e8e62e766e52197bcf39c993851828f155315a3fb7d07ec52d40`.
  Five section outputs were persisted (word-timing counts 17, 24, 30, 24,
  and 23; 118 total). The runtime audit records one `voice.generate` event
  with five sections; the provider implementation makes one request per
  section, and no retry was observed in the completed run.
- Voice master:
  `data/production-benchmark-crazy-demon-20260831-cold-v1/tmp/eeae59a27baf421590c034522e619903/audio/voice_master.wav`;
  50.650208s, PCM s16le, 48 kHz, stereo. Independent audio checks measured
  `-14.1 LUFS` integrated loudness and `-2.6 dBFS` true peak. Four intentional
  inter-section pauses measured 0.44–0.53s; no long trailing silence or
  clipping was detected.
- Final QC is PASS with no failure codes: 14 unique crops, maximum same-panel
  same-crop dwell 3.674s, motion-mode diversity 4, zero low-difference motion
  ratio, zero black-frame duration, 0.0003s audio/video drift, 118 active-word
  events, maximum two subtitle lines, 77px subtitle font, and
  `sentence_chunked_word_karaoke_v2`. Actual start/middle/end frames and the
  final video contact sheet were inspected. The generated frame contact sheet
  is `data/production-benchmark-crazy-demon-20260831-cold-v1/tmp/eeae59a27baf421590c034522e619903/final-frame-audit/final-video-contact-sheet.jpg`
  (SHA-256
  `FCEDFB9DAAFF0F288F5C6C331B2F0DA35BF880917FD334BFAE904C8E8AE9B210`).
- `final.qc.json` records `qc_pass=true` and `publish_allowed=true` under the
  current local rights configuration. This is a local technical acceptance
  artifact only; rights/publication side effects remain intentionally
  unperformed and must be reviewed separately.
- The earlier accepted cold review run was resumed after a watchdog stop and
  is documented above; the voiced production interval above is the measured
  final-stage timing, not an uninterrupted cold benchmark. Manual voice/TTS is
  no longer deferred for this approved project, while external publication
  remains outside the executed scope.

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
