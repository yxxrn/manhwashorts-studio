# CURRENT ORACLE REPAIR HANDOFF - 2026-08-21

## FRESH BOUNDED RETRY RESULT

This retry started from published parent
`813ec6e342584b38e4a5e379a25391406df5440e`. It reused the exact durable
candidate and existing 701-panel visual/story identities and issued exactly
one `grok-4.3` repair request. It failed closed again with
`cloud.narrative_repair_slot_contract_invalid`; no provider prose or payload
was stored or printed, no repair result was admitted, and no automatic retry
is permitted. The sanitized runtime report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-immutable-slot-schema-retry.json`.
No narration, MP4, voice, or QC artifact is proven. Further progress requires
an explicit response-schema diagnosis and a new bounded authorization.

## SLOT SCHEMA FOLLOW-UP (published)

Published correction `25f1d6598643b0217504520d3e28f58994b41688` has parent
`945770e75fc2483fc854fc0f7bf411993ee90f9a` and is now on Oracle/GitHub
`main`. The targeted repair prompt names the exact provider row shape
`{\"slot_id\": \"...\", \"text\": \"...\"}`; local lineage ownership and
fail-closed reconciliation are unchanged.

The one post-publication real repair request used the existing pinned
`grok-4.3` configuration, issued exactly one request, and failed closed with
`cloud.narrative_repair_slot_contract_invalid`. No provider payload was
stored or printed, no repair result was admitted, and no second request was
made under the one-request budget. The body-level schema regression and the
full focused matrix are GREEN at 74 tests (67 cloud-multimodal and 7
prepared-manifest), with five existing Pillow warnings; static/no-churn/
secret gates pass. No narration, MP4, voice, or QC artifact is proven.

## IMMUTABLE SLOT CHECKPOINT (published)

Published commit is
`170ae52f9e9a821d37a8ba025d44f09b0ad44187` with rollback parent
`161e26807050bda6f3e764811e0a1f92e93ec6b2`. It changes only the targeted
narration repair boundary and its focused regression. Local code now derives
`narration_slot_v1_*` identities from the already grounded candidate/story
map. The provider may return only exact slot IDs, revised spoken text, and
retained/dropped slot order; local reconciliation restores all claim and
evidence lineage and rejects unknown, duplicate, missing, reordered, or
provider-authored identifiers.

TDD evidence is collection-clean RED with four intended body failures at the
missing slot helper, followed by GREEN focused verification: 73 tests passed
(66 cloud-multimodal and 7 prepared-manifest) with five existing Pillow
deprecation warnings. Ruff, compileall, `git diff --check`, no-churn, and the
scoped secret scan are clean. No real repair request has been made after this
contract change; the durable candidate remains 160 words / 64.35 seconds and
is repair-only. The next bounded operation is at most one real repair request
using the same pinned model and matching visual/story identities. Do not rerun
visual/story stages, admit the candidate to the final cache, or claim a
narration/MP4/voice/QC artifact until the repaired result passes 115-125 words,
50-60 seconds, grounding, and display derivation.

Authoritative repository: Oracle `/home/ubuntu/manhwashorts`, `main`, published
at `170ae52f9e9a821d37a8ba025d44f09b0ad44187` with parent
`161e26807050bda6f3e764811e0a1f92e93ec6b2`. The source/test/docs checkpoint
contains only
`app/services/cloud_multimodal.py` and
`tests/test_cloud_multimodal_mass_production.py` are authored changes. The
untracked `data` symlink and `ms_env.sh` are protected runtime/credential
paths and must never be staged, printed, copied, or committed.

The 701-row visual cache and `prepared-panel-manifest-v1` are reusable and
must not trigger a visual rerun. The durable typed candidate is
`/data/data/p0-aws-acceptance/cloud-stage-cache/2fc948cc2946867c605ea14b6210a234.json`:
160 words, 64.35 seconds, candidate hash
`c4662073d9aa1e51de1620c7d4b0edfe5a51ebf7fc3f7bdda6233789f93d7310`, with
`cloud.narrative_duration_out_of_range` and
`cloud.narrative_word_count_out_of_range`. It is never a final cache entry.

This checkpoint adds a compact repair harness: it filters the durable
candidate to its exact selected panel/evidence/story identities, validates
lineage before calling the provider, and routes an invalid durable candidate
directly to bounded repair without a repeat normal-narration call. Provider
prose cannot alter passage/claim IDs, evidence panel IDs, claim type, ending,
observations, story spine, or causal scope. Safe failures now expose only a
stable field and count, never provider text or credentials.

Collection-clean RED was one body failure at the compact identity boundary;
GREEN is 69 focused tests (`62` cloud plus `7` prepared-manifest) with five
existing Pillow warnings. The final bounded real repair attempt issued one
request and reached `cloud.narrative_not_grounded` because returned claim IDs
were not locally resolvable; the provider response count was not persisted by
that pre-diagnostic call, so it is not fabricated. No repair result,
narration artifact, silent MP4, voice, or QC is proven. The next safe command
after publication is the same isolated repair boundary, at most one request,
then normal persistence/render only after a 115-125-word, 50-60-second,
fully grounded result is admitted. Do not weaken grounding or invent claims.

The checkpoint's scoped Ruff, compileall, diff-check, no-churn/secret review,
and exact-object fast-forward publication are complete. The related legacy
matrix remains a named baseline exception at 142 passed/13 prerequisite
failures and is not a green production gate.

# TARGETED REPAIR SCOPE HARDENING - 2026-08-21

The preceding prepared-manifest checkpoint is published as
`2df9ab4e756e501f9f30e5670239e77c1225c011` (parent `3330700dc7e4c310b19441d5c50099abbbae2b1d`); GitHub `main` matches it.
The current uncommitted scope is only the repair-scope reconciliation in
`app/services/cloud_multimodal.py`, its focused regression, and these docs.

The required RED was collection-clean and body-failing: one new test reported
that the published runner had no `_narration_repair_scope_reconciled` helper.
GREEN is 66 focused passes (`59` cloud-multimodal and `7` prepared-manifest),
with five existing Pillow deprecation warnings; Ruff, compileall, and
`git diff --check` pass. The fix permits provider prose/editorial-role drift
only when passage IDs, claim IDs, exact panel citations, claim type, ending,
observations, story spine, and causal scope remain unchanged. It then locally
restores candidate evidence/claims/roles before final validation. Any lineage
or new-claim drift still fails closed as `cloud.narrative_repair_scope_invalid`.

The one bounded normal-entrypoint attempt against the durable candidate was
cap-limited to one request and ended with that stable scope code; no repair
result cache was admitted. The durable candidate currently on disk is
`160` words / `64.35s` with hash
`c4662073d9aa1e51de1620c7d4b0edfe5a51ebf7fc3f7bdda6233789f93d7310`, not the
earlier 172-word/69.57-second report. The request counter was not persisted by
that failed job/log, so no exact provider-call count is claimed. Do not relabel
this candidate as final narration or reuse it across visual/story identities.

After this correction is published, rerun the same normal service boundary
with the 701-row visual cache and one bounded repair request. Accept only a
contract-valid 115-125 word / 50-60 second result with complete grounding and
display derivation; then continue narration artifact/QC and silent render.
No MP4, voice, or publication is proven.

# PREPARED MANIFEST + TARGETED REPAIR CHECKPOINT - 2026-08-21

Authoritative worktree: Oracle `/home/ubuntu/manhwashorts`, branch `main`.
Rollback parent for this checkpoint is `3330700dc7e4c310b19441d5c50099abbbae2b1d`.
The preceding checkpoint scope was limited to `app/services/cloud_multimodal.py`,
new `app/services/prepared_panel_manifest.py`, the two corresponding focused
test files, and this handoff documentation. `data`, `ms_env.sh`, databases,
WAL files, caches, logs, media, and provider credentials remain untracked and
must never be staged.

The warm-resume defect was measured before this slice: preparation decoded the
large panel object graph (about 529 MB on disk and about 784 MB peak RSS in the
stopped attempt) before cached visual rows could be reused. The new
`prepared-panel-manifest-v1` is payload-free and content-addressed. It records
ordered panel/source identities, immutable source checksums, integer bounds,
segmentation state, and optional feasible-ledger/crop hashes. A validated
manifest restores metadata-only panel markers; no marker can reach a provider.
Review-only pixel paths deliberately keep cold materialization. Persisted
`preparation_metrics` records mode, panel count, elapsed seconds, payload bytes,
peak RSS, and whether source decode was required. A stale fingerprint or
lineage mismatch falls back to safe cold preparation instead of accepting a
wrong cache.

The strict narration cache now treats a structurally grounded 172-word /
69.57-second result as typed `narration_repair_candidate-v1`, never as a final
cache entry. Final admission still requires the final cache contract,
115-125 words, 50-60 seconds, complete grounding/citations, model/prompt/
visual/story identities, and independently derived display text. The bounded
repair harness preserves retained passage/claim/evidence IDs and causal order,
removes only complete low-priority passages, and stores a typed repair result
atomically. Fake-provider coverage proves one repair request, zero repeated
normal-narration calls, cache-resume idempotency, and no ordinary-cache
admission for the invalid candidate.

Verification at this handoff: the focused manifest/cloud suite is 65 passed
with five existing Pillow deprecation warnings; Ruff, compileall, and
`git diff --check` are clean. The related matrix is not green: current and
clean-parent comparison both reproduce 142 passed and 13 failed pipeline nodes
at `PipelineError: run vision analysis before generating a draft`. This named
baseline exception is not a production-readiness claim. No real repair request
has been made in this checkpoint, and no story-map, narration, silent MP4,
voice, or QC artifact is proven.

After this source/test/docs checkpoint is published, resume through the normal
service boundary with the existing `/tmp/ms_env.sh` sourced without output,
reuse the 701-row visual cache, exercise at most one bounded real targeted
repair call, and persist only a contract-valid result. Do not restart visual
analysis or bypass grounding. The next acceptance gates are durable repair
success, narration artifact/QC, a real silent MP4, and only then the configured
voice stage.

# FOLLOW-UP CACHE MIGRATION PROOF - 2026-08-20

The first post-publication normal resume was stopped after exactly two
VISUAL_CHUNK_OK provider requests when the old descriptor hash did not match.
The durable job remained unchanged with 701 visual rows; no new stage result was
accepted. The mismatch is now explained and handled safely: current prepared
inputs have the same ordered 701 panel IDs, source-asset IDs/checksums, panel
bounds, and coverage hash as the persisted narration lineage, while the old
descriptor hash cannot be reconstructed from visual rows alone.

The migration now accepts this case only with persisted narration observations
whose ordered panel IDs, source assets, source indexes, and integer region bounds
match every current panel. The current deterministic rendered payload hashes are
then computed and persisted as the v2 per-panel identity proof. A tampered
lineage/crop remains rejected. A no-provider live diagnostic over the exact
project confirmed prepared=703, filtered=701, migrated=True,
proof=persisted_lineage_and_payload_derivation, identity_rows=701, and new
source hash fb61e64ef66bce8e9fa9d79bc5e00ec5fd6ab8c3d0d7057a84d70dc04a7fa5c5.

The normal script must include the repository import path:
PYTHONPATH=/home/ubuntu/manhwashorts PATH=/home/ubuntu/.local/bin:$PATH
.venv/bin/python scripts/run_cloud_multimodal_batch.py ...
This correction made no provider call; no narration, MP4, voice, or QC is
proven. The stopped-run log is /tmp/cache-identity-resume-20260820.log and
remains runtime-only.

# CURRENT CACHE-IDENTITY CHECKPOINT - 2026-08-20

Authority remains Oracle /home/ubuntu/manhwashorts, branch main, parent
27d86c44bb97fd03bf9f61d556bda195c244eac8. This green checkpoint fixes the
703-to-701 duplicate-visual-call defect without weakening evidence or model
identity gates.

The old visual source hash included preparation enumeration source_order and
other descriptor metadata. A full 703-region preparation therefore changed the
hash of an equivalent persisted 701-panel subset when two poison regions were
removed. The canonical visual identity now contains only ordered panel index
and panel ID, immutable source-asset checksum, exact normalized crop transform,
deterministic rendered provider-payload SHA-256 plus payload policy/mime, and
the pinned model/prompt identity at stage and chunk keys. Temporary paths,
database row order, timestamps, mutable review metadata, and serialization
ordering do not participate.

A legacy cache is accepted only when its ordered panel IDs, source-asset IDs
and checksums, monotonic cached source order, and recomputed legacy descriptor
hash (including current payload checksums) all match. It is migrated locally to
visual-cache-identity-v2 with ordered per-panel identity hashes and the new
whole-stage source hash. Any mismatch invalidates the visual cache and requires
a normal provider run; no rows are copied across model identities. New visual
checkpoints also carry the per-panel identity and exact chunk key, so a crop
change invalidates only its chunk while model/prompt changes invalidate all
affected stage keys.

TDD evidence for this checkpoint: the intended RED collected cleanly with
51 existing cloud tests passing and 3 body failures for the missing identity
helpers; GREEN is 54 passed in
tests/test_cloud_multimodal_mass_production.py. Scoped Ruff, compileall, and
semantic diff checks are green. This source/test/docs checkpoint itself made
zero provider calls. The interrupted pre-fix retry was not accepted as a
production result; its first duplicate visual request must not be counted as a
successful stage completion. No MP4, narration, voice, or QC is proven yet.

Safe resume after this checkpoint, using the normal checked-in service boundary:
~~~bash
cd /home/ubuntu/manhwashorts
set -a; source /tmp/ms_env.sh >/dev/null 2>&1; set +a
export MS_DATABASE_URL=sqlite:////data/data/p0-aws-acceptance/sample.db
export MS_STORAGE_DIR=/data/data/p0-aws-acceptance/storage
export MS_DATA_DIR=/data/data/p0-aws-acceptance
export MS_OUTPUT_DIR=/data/data/p0-aws-acceptance/output
export MS_TMP_DIR=/data/data/p0-aws-acceptance/tmp
export MS_TTS_PROVIDER=null
export MS_ENVIRONMENT=local
export MS_REQUIRE_RIGHTS_DECLARATION=false
PATH=/home/ubuntu/.local/bin:$PATH .venv/bin/python scripts/run_cloud_multimodal_batch.py   --project-id 22876a6014a842f48bfca58c10a592b5   --state-dir /data/data/p0-aws-acceptance/cloud-jobs   --segmentation-review-dir /data/data/p0-aws-acceptance/segmentation-review   --model grok-4.3 --max-attempts 3 --min-request-interval-s 0.3
~~~
The script resumes the durable job; the runner's request counter and stage
state must be recorded before any claim of visual/story/narration completion.
Runtime data, caches, DB/WAL, logs, media, data, and ms_env.sh remain
untracked and must never enter Git.

# FOLLOW-UP GREEN CHECKPOINT - 2026-08-20

Source/test fix checkpoint:
b66210204e2616903844cbf3dc414558a53035d4
(parent dfb8c26e6148bb8b3e098d25b1bf691e14f94cbd).

The duration-to-visual-repair boundary now retains a structurally valid
narration candidate when the strict 50-60 second/115-125 word contract fails.
That candidate remains unpersisted and is supplied only to the bounded
review-only repair stage. TDD was collection-clean with one intended body
failure (missing retention attribute), then focused GREEN was 1 passed and
the full cloud regression file is 49 passed with five existing Pillow
deprecation warnings. Ruff, compileall, and git diff --check are clean.

The latest real run before this correction completed in 16:14 with 3 provider
requests and peak RSS 8,381,328 KB, then ended NEEDS_REVIEW with
visual.narrative_repair_ungrounded. It produced no MP4 or review artifact.
The next run must reuse the 701-panel visual/story cache and exercise this
retained-candidate repair path; no visual stage restart or gate relaxation is
allowed. Voice/TTS is authorized only after the silent preview/QC gate; the configured
grok-voice-latest English voice must then be exercised through the normal service
with exact timing and audio QC. No voice run is proven yet. Publication,
credentials, runtime data, DB/WAL, caches, logs, and media remain blocked or
untracked.

The repair boundary accepts only the existing provider panel_ids transport
alias and canonicalizes it to local evidence_panel_ids; feasibility, claim,
chronology, and duration gates remain unchanged. RED was one intended body
failure; GREEN is 1 focused regression plus 50 cloud-stage tests.

The resume boundary now filters the live panel tuple to a persisted visual
subset before run_job, preventing a 703-to-701 source-hash mismatch and
duplicate visual calls. Malformed or empty cached rows leave the input
unchanged. RED was one intended body failure; GREEN is 1 focused regression
plus 51 cloud-stage tests.


# LIVE ORACLE TOPOLOGY CHECKPOINT - 2026-08-20

The authoritative checkout is /home/ubuntu/manhwashorts on Oracle, branch main.
The green source/test checkpoint is dfb8c26e6148bb8b3e098d25b1bf691e14f94cbd
(parent 078715a77251b097e563aff41f696a6005d75b7b). It changes only the
cloud multimodal runner and its mass-production tests: full visual coverage
remains cached at 701 processed rows, story mapping is 701/701, and narration
now selects grounded beats before one final prose reduce instead of
concatenating chunk prose. The focused cloud suite is 48 passed.

The last real resume before this slice ended NEEDS_REVIEW with
cloud.narrative_not_grounded after 3 narration requests, 975.73 seconds wall
time, and 8,397,748 KB peak RSS. No narration artifact, timeline, MP4, voice,
or QC is proven. Do not weaken grounding or use the older model-identity cache.
Runtime data, DB/WAL, data symlink, provider state, caches, logs, media, and
ms_env.sh remain untracked; never print, copy, or commit credentials. The
required handoff and cache/invalidation record is in
docs/ARCHITECTURE_MAP_REDUCE.md and docs/P0_129-133_AGENT_HANDOFF.md.


# CURRENT ORACLE EXECUTION CHECKPOINT - 2026-08-20

This block is authoritative for the current run and supersedes older workspace
history below. Execution is on Oracle at /home/ubuntu/manhwashorts.

- Repository: /home/ubuntu/manhwashorts; branch main.
- Current published HEAD: 7f7ffe697b5b9aa6c9a8a95fa4c046597a0622d8; parent
  d14ea5916976b29797dd9d23947aa3c3dac53994. GitHub main matches through the
  retained Windows exact-object transport clone; Oracle's tracking ref is
  still stale because Oracle HTTPS authentication is unavailable.
- The published source/test checkpoint adds the ordered_beats provider alias
  and a bounded 180-to-60 story-map coverage fallback. It never accepts a
  partial panel map and never invents panel references. Collection-clean RED
  covered the alias and incomplete-large-chunk cases; focused GREEN was
  2 passed and the full cloud regression file was 40 passed. Ruff,
  compileall, and git diff --check are clean.
- Protected runtime paths remain untracked: data, ms_env.sh, DB/WAL, caches,
  media, provider state, and temporary logs. ms_env.sh contains credentials;
  source it only in-process and never print, copy, log, fixture, or commit it.

Runtime checkpoint for project 22876a6014a842f48bfca58c10a592b5:
- The exact current visual cache
  /data/data/p0-aws-acceptance/cloud-stage-cache/5a60693742b5b2d390f60a686b3283bd.json
  has 701 panels and the visual cache key was a local hit; no visual provider
  calls were repeated. Two of 703 source panels remain the previously
  recorded skipped rows.
- Normal service story mapping reached STORY_MAPPED. Its persisted story
  map covers 701 of 701 panel IDs (60 beats, 53 claims); the bounded fallback
  preserved complete coverage.
- Narration is not complete and no MP4 exists. The narration boundary found
  175 of the 701 current visual observations with empty visible_facts.
  This is a real evidence-quality blocker, not a reason to weaken the analyzer
  contract or synthesize facts. The older complete cache has a different
  model identity and must not be mixed into the current grok-4.3 run.
- The job JSON
  /data/data/p0-aws-acceptance/cloud-jobs/22876a6014a842f48bfca58c10a592b5.json
  is currently STORY_MAPPED; its error field still contains the prior
  cloud.panel_coverage_incomplete retry code and must not be read as a
  successful narration/render claim. No timeline, silent render, voice, or QC
  result is proven.

Safe resume:
  cd /home/ubuntu/manhwashorts
  set -a; source /tmp/ms_env.sh >/dev/null 2>&1; set +a
  PYTHONPATH=/home/ubuntu/manhwashorts .venv/bin/python /tmp/run_phase1_resume.py

Before resuming narration, obtain a complete same-identity visual evidence
record for the 175 incomplete rows through an explicitly authorized repair or
a matching cache. Do not copy rows from another model identity, invent
visible facts, or bypass whole-panel grounding. After that, rerun the normal
bounded service; only a real persisted narration may proceed to silent
render. publish_allowed remains false; voice/TTS remains deferred.


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

## User-approved option: agent-vision observation pathway (review-only)

On 2026-08-15 the user approved a sanctioned option: when the executing agent
supports vision, the agent may perform panel observation directly (no provider
call), persisting geometry through a validated service boundary.

- Boundary: `app/services/agent_visual_observation.py`
  (`validate_agent_panel_observation`, `apply_agent_panel_observations`);
  entrypoint `scripts/review/apply_agent_visual_observation.py`.
  Contract `agent_visual_observation_v1`, evidence source
  `agent_visual_geometry_v1`, `mask_reason` prefixed `agent:<label>; `.
- Hard rules: review-only (`publish_allowed=false` + silent-review ack required),
  no supplied hash/contract/source/lineage (local canonical hash only), no
  `unknown` status, surgical update of `observation_json['visual_evidence']`
  only, ledger JSON under the ignored output dir. All framing gates consume the
  geometry unchanged; the pathway only supplies geometry.
- Verification: `tests/test_agent_visual_observation.py` 13 passed plus the
  69-test matrix (82 combined), ruff/compileall clean.

Status: boundary implemented and green; beat_1 agent observation EXECUTED on
2026-08-15. All 14 opening panels were visually inspected by the agent, honest
geometry was persisted through the boundary (agent label `claude-visual-beat1`,
evidence source `agent_visual_geometry_v1`, ledger at
`data/_beat1_agent_observation/agent-observation-ledger.json`), and a
deterministic feasibility sweep then measured **0/14 feasible crops** — balloon
overlap on orders 00/01/02/10, protected subject/face retention everywhere else.
Balloon corrections were recorded honestly (orders 04/05/07/08/09/11 corrected
to `known_empty`; order 11's claimed "balloon" is floating caption text). The
negative result is genuine: with honest geometry, the protected-retention gate
(subject/face ≥0.98 inside any crop) leaves no 9:16 window in any opening panel
— e.g. order 11 needs ≥397 px crop width to retain its left-half figure but a
672-px-tall 9:16 crop is ≤378 px wide. The remaining sanctioned option is
alternate opening-beat source art that is genuinely balloon/protected/blank-clean
(or a user/provider decision redefining opening evidence). Do not weaken gates
to force an MP4.

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

## CURRENT NARRATION-REPAIR CHECKPOINT - 2026-08-20

Published source/test checkpoint: 826856cc08550895ba8944e4b9b3fce6b0f62823.
The duplicate visual-call identity fix is green and GitHub main matches this
commit. The normal Oracle run reused the canonical 701-panel visual cache and
persisted VISUAL_ANALYZED plus the ordered story-map result without any
VISUAL_CHUNK_OK provider call.

A bounded narration-targeted-repair-v1 boundary is now implemented in
app/services/cloud_multimodal.py. When the selected final script misses
the strict 50-60 second / 115-125 word contract, the same pinned model receives
the validated candidate and repairs prose/timing only. Local reconciliation
requires the same passage IDs, claim IDs/text/qualification, evidence panel IDs,
observations, ending kind, and story spine; lineage changes fail closed as
cloud.narrative_repair_scope_invalid. Maximum repair attempts: 3. Visual and
story-map stages are not regenerated.

Verification on Oracle: the new RED was collection-clean with 2 intended body
failures; GREEN is 2 targeted tests, the complete
tests/test_cloud_multimodal_mass_production.py file (57 passed), Ruff,
compileall, and git diff --check. The resumed runtime then reached
STORY_MAPPED with canonical visual hash
fb61e64ef66bce8e9fa9d79bc5e00ec5fd6ab8c3d0d7057a84d70dc04a7fa5c5, but its
narration candidate ended NEEDS_REVIEW with
cloud.narrative_duration_out_of_range; no MP4 or voice/QC result is proven.

The failed job returned before saving the aggregate usage counter, so its
durable request_count remains 0 and the exact provider-call count for that
attempt is intentionally not claimed. Do not infer it from sockets/log size.
Resume only after checking for no active process, using the normal checked-in
entrypoint with PYTHONPATH=/home/ubuntu/manhwashorts and the existing
/data/data/p0-aws-acceptance state/cache. Do not repeat the 701 visual stage.\n\n## 2026-08-21 narration repair cache/prompt isolation

The targeted narration repair follow-up is now verified against parent d539c88. Repair requests use the explicit versioned prompt suffix `vision-first-story-analyzer-v3-targeted-repair-v1`, the `narration_repair` provider stage, and a stage-specific cache identity; ordinary narration keeps the `narration` cache identity. The ordinary narration helper no longer references the repair-only stage.

Failure persistence now records the runner request counter and estimated cost before a reviewable failure is written, so a failed repair cannot falsely appear to have made zero calls. This preserves the same pinned model, strict scope/grounding gates, three-attempt bound, and no visual-stage rerun.

Verification: focused identity/repair matrix 5 passed; complete `tests/test_cloud_multimodal_mass_production.py` 57 passed; Ruff and diff-check passed. No live retry, MP4, voice, or QC result is proven yet. Resume only through the checked-in batch entrypoint after checking for an active process; do not repeat the 701-panel visual stage.\n
## 2026-08-21 strict narration candidate/repair cache checkpoint

The 172-word, 69.57-second four-passage artifact is a typed
narration-repair-candidate-v1 only. It is never admitted as the final
narration-final-v1 cache result. Final admission requires 115-125 words,
50-60 seconds, prompt/model/visual/story identity, ordered grounding and
citations, and derived display words.

The bounded repair uses the same pinned model and prompt identity, preserves
retained passage/claim/evidence lineage and causal order, and may remove only
complete low-priority passages while retaining at least four passages. Repair
results use narration-repair-result-v1; cache resume is idempotent and makes
no provider call when the typed repair result is valid. Focused RED was
collection-clean with 5 collected, 4 passed, and 1 intended body failure.
GREEN is 5 focused tests, 58/58 cloud mass-production tests, Ruff,
compileall, and diff-check. The related 155-test matrix had 142 passes and
13 failures in the existing draft-pipeline fixture path; those failures are
outside this two-file source/test slice and are not claimed green.

The live job remains STORY_MAPPED with the canonical 701-panel visual cache;
the last bounded narration attempt was stopped after about 28m50s with four
sanitized cloud.narrative_not_grounded chunk failures. No narration, MP4,
voice, or QC result is proven by this checkpoint. Resume only through the
normal batch entrypoint after confirming no active process; do not repeat
cached visual calls.
## Current Oracle checkpoint: position-locked narration repair (2026-08-21)
The published base is `080744718f40cb3480a6a9d83896eabbe533c3c4`. The current
source/test checkpoint changes only the targeted narration repair boundary:
local code selects 8-12 grounded claim positions, assigns a 120-word budget,
and hashes the ordered registry as `slot_order_hash`. The pinned provider may
return only a positional `rewrites` array; it must not own slot, claim, beat,
evidence, or hash identifiers. Reconciliation and cache admission remain
local and fail closed on wrong wrappers/counts/types, budget or duration drift,
lineage drift, or reordered positions. One real repair request is allowed
after publication; no visual/story rerun or automatic retry.
Verified before publication: RED 6 collection-clean intended failures; GREEN
focused 138/138 (cloud 72, prepared manifest 7, adapter 23, synthesis 36),
Ruff, compileall, diff-check, no-churn, and key-shaped secret scan. Runtime
state is still STORY_MAPPED with 701 visual rows; no narration, MP4, voice, or
final QC is proven. Resume only with the sanitized runtime environment and
existing durable cache; never print `ms_env.sh`, commit data/media/DB/WAL,
credentials, or provider payloads.
## Published position-vector checkpoint

GitHub main and Oracle publish commit are `c663ccb72b4e7d29c86a14c793b83b957e5517e8`,
parent `080744718f40cb3480a6a9d83896eabbe533c3c4`. The focused position-vector
gates are green; the single real repair request is the next external action.
## Position-vector live attempt correction (2026-08-21)

The first post-publication position repair made exactly one real
`grok-4.3` request and failed closed with
`cloud.narrative_repair_position_budget_invalid`; no automatic retry or raw
provider payload retention occurred. Runtime-only sanitized metadata is at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`.
The current unpublished correction uses explicit bounded per-position word
ranges around a 120-word target and retains strict total/duration gates. Its
RED was one collection-clean body failure; GREEN is 139 focused tests plus
Ruff/compile/diff/no-churn/secret checks. Publish this correction before any
new real request; never repeat visual/story stages or expose provider text.
## Position-vector second live attempt and v2 budget correction (2026-08-21)

After the published `6e8df193d80ba42cbc3b6c5aa838c9154b1fd600` correction,
one newly authorized real `grok-4.3` request again failed closed with
`cloud.narrative_repair_position_budget_invalid`; request count was exactly one
and no automatic retry or provider prose was retained. The same sanitized
metadata report remains at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`1194ac83c3aa32ef933be9897f6207188c0f7bce1f04397b7b93c3c8f3096f61`.
The unpublished v2 correction widens each deterministic local position range
to a bounded minimum of 7 and target-plus-8 maximum, bumps the registry
identity, and keeps strict total/duration gates. Historical RED is one
collection-clean body failure on 6e8df19; GREEN is 140 focused tests plus
Ruff/compile/diff/no-churn/secret checks. Publish before any further real
request; never repeat visual/story calls.
## Position-vector response-shape instrumentation (2026-08-21)

After `1b2be08ae60a9a06ab8e5ec2e2972c22d9fb1e09` was published, one real
position-vector request again failed closed with
`cloud.narrative_repair_position_budget_invalid`; it used exactly one request
and no retry. That pre-instrumentation attempt retained no response-shape
metrics. The new boundary attaches only sanitized container/key, array count,
per-position counts, total/duration, expected ranges, and failed-predicate
metadata to the local error and durable review queue; provider text remains
discarded. The prompt now states exact 120 words as guidance, while admission
still accepts 115-125 words and 50-60 seconds. RED covered the prompt and
metrics assertions; GREEN is 142 focused tests plus static/security gates.
Publish this instrumentation before the next single real request.

## Position-vector trusted-subset correction (2026-08-21)

The first request after `f47262fd16fd75522fdbfa65e79d18dfb9f967ea` used one
request and no retry, then failed closed as
`cloud.narrative_repair_scope_invalid`. Its sanitized report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256 `d66bb529e2633785d7c93a8fdab6eaba4d445d5ae94d1e04f3f28194ff60a5b7`.
The response passed the positional budget boundary, so no budget-predicate
metrics were available; provider prose and raw payload remain discarded.

The RED regression proved that local compaction could retain a trusted subset
of a passage's claims without introducing lineage. The GREEN correction now
accepts only an ordered, duplicate-free subset of the candidate claim/evidence
arrays and preserves that subset during canonical reconstruction; new,
foreign, reordered, or empty lineage remains rejected. The focused matrix is
143/143 green with Ruff, compileall, diff-check, no-churn, and secret checks.
Publish this correction before one further bounded real request; visual/story
stages remain cached and no narration, MP4, voice, or final QC is proven.

## Position-vector aggregate budget correction (2026-08-21)

After the published `7f17e6ed6b38fd8d85e0cd9e6acd50f937278f14` scope
correction, one bounded real request used exactly one request and zero retries
and failed closed as `cloud.narrative_repair_position_budget_invalid`. The
sanitized report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256 `22b4fd1b8a4ecf29f458a010bbf9879e936629d04fd720cc8c14684f70db1621`.
Its metadata recorded 12 string rewrites with per-position counts
`[14,9,13,8,10,10,9,13,15,9,12,13]`, total 135 words, estimated duration
56.96 seconds, expected `7..18` ranges, and failed predicate
`aggregate_word_count`; no provider prose/raw payload was retained.

The RED regression required the sum of local position maxima to stay within
the final 125-word bound. GREEN deterministically distributes the five-word
slack above the 120-word guidance target across the earliest positions while
keeping strict 115-125-word and 50-60-second admission. The focused matrix is
144/144 green with Ruff, compileall, diff-check, no-churn, and secret checks.
Publish this correction before another real request; visual/story stages stay
cached and no narration, MP4, voice, or final QC is proven.

## Position-vector selection-count v2 correction (2026-08-21)

After the published `10eb14ef0a3bfe332cc8c7e3b3083b2216df6cb9` max-10
checkpoint, one bounded real request used 10 positions and returned 13 words
per position: total 130 words, duration 54.78 seconds, and failed closed on
`position_word_budget` because the later positions had max 12. The sanitized
report SHA is
`f6436f8a0cbcc4670593918b482c4f9756497386cb6834130e85ee4ab8c48590`.

The RED regression tightens the deterministic preselection ceiling to 9,
still inside the required 8-12 range. GREEN preserves at least four causal
passages, drops only the lowest-priority removable claims, and produces a
117-word target at the observed provider granularity while retaining strict
115-125-word/50-60-second admission. The focused matrix remains 145/145 with
static/security gates clean. Publish before another real request; no
narration, MP4, voice, or final QC is proven.

## Position-vector selection-count correction (2026-08-21)

After `bfb0ee137683f81caaf908cd47b8ea9216caa654`, the one bounded real
request returned 12 strings and failed closed after one request with total 140
words; its sanitized report SHA is
`abdca214cfeb384eef2a38a0a20bca33d6716aa751d0794ea0b91645bd486d4f`.
Counts were `[12,12,11,14,13,10,12,13,10,11,11,11]`; the exact failed
predicate was `position_word_budget` against the aggregate-feasible maxima.

RED proved that a candidate with 12 trusted positions could reach the provider
despite the observed over-verbosity. GREEN caps deterministic preselection at
10 positions (still within the required 8-12 range), removing only lowest
priority removable claims while retaining causal order and at least four
passages. The focused matrix is 145/145 green with static/security gates.
Publish before another real request; visual/story stages remain cached and no
narration, MP4, voice, or final QC is proven.

## Position-vector selection-count v3 correction (2026-08-21)

After the published `68f0e71298e8718e53b78b3d239671e8c204c0ec` max-9
checkpoint, one bounded real request used 9 positions and returned counts
`[15,15,15,14,15,13,13,13,13]`: total 126 words, duration 52.61 seconds,
and failed `cloud.narrative_repair_position_budget_invalid` after one request
and zero retries. The sanitized report SHA is
`ad198b21e470f7c530c71219f511a45d05a306699060eb9be8d97f478d916f14`.

RED proves max-9 can still exceed strict per-position maxima. GREEN caps the
trusted deterministic selection at the minimum 8 positions, still within the
required 8-12 range, preserving at least four causal passages and trusted
lineage. The focused matrix is 145/145 with Ruff, compileall, diff-check,
no-churn, and key-shaped secret scan clean. Publish before the next request;
no narration, MP4, voice, or final QC is proven.

## Position-vector aggregate admission correction (2026-08-21)

After the published max-8 checkpoint `ad4b62a7e7e6a4a4d9e70aefcc41aa54dac2a1c2`,
one bounded real request returned the valid positional shape of 8 strings with
counts `[17,16,16,15,16,13,13,13]`, total 119 words, and estimated duration
50.0 seconds. It failed closed only because one derived upper position budget
was exceeded; the sanitized report SHA is
`f8700f9e2f2486b8a85984a635a3333d102f9a97898624e92a5a6fefd3a9d16f` and
request/retry counts were 1/0.

The collection-clean RED regression proved that an in-range final response
could be rejected by the upper position guidance. GREEN keeps the minimum
position floor and strict aggregate 115-125-word/50-60-second gates, while
treating the derived upper position values as guidance once the whole response
is in range. Exact 120 remains guidance only. Focused verification is 146/146
with Ruff, compileall, diff-check, no-churn, and key-shaped secret scan clean.
Publish before another real request; no narration, MP4, voice, or final QC is
proven.

## Position-vector concise drafting correction (2026-08-21)

The first request after `cd458804e0e73344ac0cebc6c49f325e1b93ecd9` returned
8 strings with counts `[17,17,18,18,18,15,17,16]`, total 136 words, and
estimated duration 57.39 seconds. It failed closed after request/retry counts
1/0 because the final word bound was exceeded; sanitized report SHA:
`5654413fcb1a03698d0a93e34742addf137bc13b6913697255074c61b34f6b80`.

RED added a prompt regression. GREEN makes the provider drafting instruction
explicitly treat each `word_budget_max` as a hard drafting target and forbids
filling a position with extra words; local final 115-125-word/50-60-second
admission remains authoritative and exact 120 remains guidance. Focused
verification is 147/147 with Ruff, compileall, diff-check, no-churn, and
key-shaped secret scan clean. Publish before another request; no narration,
MP4, voice, or final QC is proven.

## Position-vector compact drafting correction (2026-08-21)

The first request after `e7cd76b34830fe9f9ea02eeb913a8eb28abbeb4f` returned
8 strings with counts `[17,17,16,16,17,15,15,14]`, total 127 words, and
estimated duration 53.48 seconds. It failed closed after request/retry counts
1/0 because the strict final word ceiling was exceeded; sanitized report SHA:
`c99db623cc4ad565083cfdd893c3803c802774db8347c503502eaa5093c2cbff`.

RED added a compact-vector prompt regression. GREEN asks the provider to aim
for 14-15 words per position in the fixed eight-position vector and never
exceed 15 unless a claim requires it, while local 115-125-word/50-60-second
gates remain strict. Exact 120 is guidance only. Focused verification is
148/148 with Ruff, compileall, diff-check, no-churn, and key-shaped secret scan
clean. Publish before another request; no narration, MP4, voice, or final QC is
proven.

## Position-vector safe target correction (2026-08-21)

The first request after `cd209c10ea6c1995adb09a3728c11be4b17b8626` returned
8 strings with counts `[17,16,15,16,17,15,15,15]`, total 126 words, and
estimated duration 53.04 seconds. It failed closed after request/retry counts
1/0 because the strict final word ceiling was exceeded; sanitized report SHA:
`8656b36af56854bfa3cde52530b5ea1d1cabbe34f5ecb11d1b3dee627eddc3bd`.

RED added a safe-target prompt regression. GREEN aims for 118 total words so
normal provider variation remains in the accepted range; exact 120 remains
guidance only, and local 115-125-word/50-60-second gates are unchanged.
Focused verification is 149/149 with Ruff, compileall, diff-check, no-churn,
and key-shaped secret scan clean. Publish before another request; no
narration, MP4, voice, or final QC is proven.

## Position-vector response-shape propagation correction (2026-08-21)

Parent checkpoint: `c39215d61211a80cf0f19729bcd0a026b1bb39cc`. The one bounded
real repair request after that checkpoint used request/retry counts 1/0 and
failed closed as `cloud.narrative_word_count_out_of_range`. Its sanitized
report is `/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`248525989776f6a52bb626f3439ef1ca36ecd0fd4cff13ece59ef5c946185ff2`.
The report had no response-shape metrics because the positional reconciler
accepted the response before a later gate failed; no provider prose was
retained or printed.

Collection-clean RED reproduced the missing durable metrics boundary. GREEN
now carries only sanitized metadata (container/keys, array length,
per-position word counts, total words, duration, slot/order identity, and
failed predicate) from successful positional reconciliation into the existing
failure report, then removes the private transport field before analyzer
validation. It does not relax grounding, causal, duration, identity, or
lineage gates and never persists provider prose. Focused verification is
151/151 with five existing warnings; Ruff, compileall, diff-check, no-churn,
and key-shaped secret scan are clean. Publish this checkpoint before at most
one new bounded repair request. No narration, MP4, voice, or final QC is
proven.
