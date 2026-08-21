# CURRENT ORACLE REPAIR HANDOFF - 2026-08-21

## Narration anti-copy repair checkpoint - 2026-08-21

Rollback parent for this contract/repair slice is
`a2d9e85eb5caa05abf792294b7265eed0300c67b`. Offline replay of the durable
candidate localized the strict rejection to passage `p2`, claims
`b1__sub0__claim2`, `b1__sub0__claim3`, and `b1__sub0__claim4`, with one
normalized four-word overlap from panel `region-a1ceb6aece5c808c9bee`.
The candidate otherwise had 701 observations, five passages, 118 words, and
51.3 seconds; no prose was copied into diagnostics.

RED was collection-clean: the new paraphrase and quoted/name-variant gates
passed, while the repair prompt/version and strict-call regressions failed
against the old repair v3 and `allow_dialogue_copy=True` call site. GREEN is
five focused regressions plus 269 affected cloud/analyzer/script/narrative
tests. Production narration and visual-repair validation no longer pass the
dialogue-copy bypass; targeted repair prompt/cache identities are v4/v5/v6
and require third-person paraphrase without preserving a four-word dialogue
sequence. Existing grounding, causal, 701-panel lineage, duration, and
identity gates remain strict.

No provider or TTS request was made in this slice. After this source/test/docs
checkpoint is published, the next command may spend exactly one bounded
`narration_repair` request using cached visual/story evidence only; do not
repeat visual/story calls. A valid result must persist through the exact
analysis boundary before local silent rendering is attempted. No narration,
MP4, TTS, or QC completion is claimed here.

## Anti-copy repair-trigger correction - 2026-08-21

The first post-publication invocation made zero provider requests because
`run_narration_repair_candidate` treated the 118-word candidate as
`cloud.narrative_repair_not_needed` even though its strict four-word dialogue
detector would reject it. A collection-clean RED regression reproduced that
state. The follow-up uses the shared analyzer detector through
`cloud.narrative_source_dialogue_copy` as a repair trigger; it does not relax
the final validator or alter source/story caches. The next allowed runtime
action remains exactly one cached narration-repair request with no
visual/story repetition.

## Trusted passage-evidence reconstruction - 2026-08-21

The next offline attempt reached `cloud.narrative_repair_slot_lineage_invalid`
before dispatch because the persisted p2 passage omitted one panel reference
that its trusted story claim required. Slot construction now validates the
candidate references as nonempty, known, unique, and related, then rebuilds
the ordered union solely from the trusted story claims. Foreign or unrelated
references still fail closed. No provider request was spent; the next action
remains one bounded cached narration repair after publication.

## DB persistence round-trip checkpoint - 2026-08-21

Rollback parent for this source/test/docs slice is
`f1f08bc2e9cd067b8703ba1d28298012cf27b74f`. The local persistence audit
proved that a valid 701-panel result builds 701 `PanelRegion` rows; the old
280-row records in the protected sample DB are stale history, not a hidden
production cap. The actual defect was two-part: persistence called
`generate_script` without binding the newly created `StoryAnalysis`, so
`latest_analysis()` could select a stale row, and persistence passed
`allow_dialogue_copy=True` while the reload path used the strict analyzer
contract. The fix binds `analysis_id=row.id`, rejects a foreign analysis
project, and keeps both persistence/reload validation boundaries strict.

Collection-clean RED reproduced stale selection as `narrative_profile_mismatch`.
GREEN evidence is the stale-row regression, a 701-row DB write/read test with
contiguous persisted `source_index` 0..700 and preserved sparse original
`source_order`, a foreign-analysis rejection test, and a post-flush rollback
test. The complete cloud file is 116 passed; the analyzer/script compatibility
matrix is 110 passed. Ruff, compileall, `git diff --check`, and no-churn checks
pass; five existing Pillow deprecation warnings remain.

The normal Oracle entrypoint was replayed with all cloud/narration/repair
budgets set to zero. It exited 0 with state `NEEDS_REVIEW`,
`cloud.narrative_not_grounded`, and request counts
`narration=0,narration_repair=0,other=0`. The strict sanitized predicate is
`script passage copies source dialogue`; no new StoryAnalysis or ScriptVersion
was committed, SQLite integrity is `ok`, and no narration, MP4, TTS, or QC is
claimed. The next boundary is an offline repair/replacement of that genuinely
blocked candidate, or a separately authorized fresh provider candidate; do
not weaken the strict gate or repeat visual/story work in this slice.

## POST-PUBLICATION REPAIR OUTCOME AND CLI OUTPUT HARDENING - 2026-08-21

After `87aed29e1600484dec07e8e1aadbdcfdeae7573e` was published, metadata-only
runtime classification found four legacy candidate records, all with
`identity_metadata_present=false`; no equivalent migration was admitted. The
first normal-run command made zero requests because the default DB did not
contain the project. A process-local `MS_DATABASE_URL` override selected the
protected sample DB without editing it.

The one authorized same-model run then used exactly two requests
(`narration=1`, `narration_repair=1`, `other=0`), with no visual/story repeat,
and ended `NEEDS_REVIEW` as `cloud.narrative_not_grounded`. Sanitized final
metrics are 118 words, 51.3 seconds, 5 passages, 8 claims, and 701 ordered
observations; display, duration-contract, and passage/claim/panel lineage
boolean checks all passed. The state/admission discrepancy is unresolved, so
no narration, MP4, TTS, or QC is claimed and no further provider request is
allowed from this checkpoint.

The runner was also found to print the entire job object, including stage
payloads, despite its redaction docstring. A body-level RED caught this; the
GREEN follow-up adds `_safe_job_summary` and prints only job ID, state, stable
error/review codes, and request counts. No provider data is serialized to CLI
stdout by the fixed boundary. The follow-up source/test/docs gates are GREEN
in the checkpoint committed with this handoff.

## METADATA-ONLY NARRATION IDENTITY RECONCILIATION - 2026-08-21

The scoped source/test checkpoint is based on published main
`5eaf91762f45ec4111d88e21ac458618bb86f42a`. It adds
`narration-repair-identity-v1` and compares only canonical metadata: the
ordered 701-panel IDs and visual-evidence identities, model/prompt identity,
story beats/claims/causal hashes, editorial selection, trusted slot order and
claim/evidence identities, and candidate dependencies. `prepared_order` is a
derived execution index and is ignored for equivalence; canonical panel rows
are normalized by panel ID, while `ordered_panel_ids` remains authoritative.
Any semantic panel, model, prompt, story, selection, slot, or candidate change
fails closed as `cloud.narrative_repair_identity_mismatch` with counts,
mismatch field, comparison hash, and reason only.

The durable legacy repair candidate has visual identity
`73c224732858ead17bdee4003cfc8824a7f1470e7e0a238f8baa5d80fd0b9579`; the
current persisted 701-panel story context has visual identity
`a9a43faf0a198b1bf3a995858fba39bea65cb27be3152b7019e2dba8a9b24b9f`.
The old candidate records do not contain the required canonical
`identity_metadata`, so they are rejected as `legacy_identity_metadata_missing`;
no migration is admitted and no hash is rewritten. This is an unsafe/unknown
legacy boundary, not proof of semantic equivalence. The new loader replaces
the active record with an equivalent migrated record before validating warm
reuse; changed identity remains rejected.

Narration budgets are independent: configured normal narration may use at most
one request and targeted repair at most one request. They can total two only
when a fresh candidate is genuinely required; other stages do not consume
either counter. The legacy `max_requests` global budget remains compatible for
older callers. No provider request was made during this checkpoint.

TDD/static evidence: the initial collection-clean RED had 13 intended
identity/budget failures; GREEN reached 13/13, then a warm-loader RED exposed
the discarded migration record and GREEN reached 14/14. The cloud file is
111 passed with five known Pillow deprecation warnings; the related
manifest/analyzer/script/vision matrix is 83 passed. Ruff, compileall,
`git diff --check`, and the key-shaped secret scan pass. The 13
`tests/test_pipeline.py` failures are reproduced with the identical first
failure (`PipelineError: run vision analysis before generating a draft`) on
the clean parent and current tree; they remain a named pre-vision fixture
exception, not a full-suite or production-render GREEN claim.

No narration, MP4, TTS, or QC artifact is proven. After publication, resume
the normal cached project entrypoint with the same configured model and
`--max-attempts 1 --max-narration-requests 1 --max-repair-requests 1`, without
repeating visual/story stages. A valid result must still pass grounding,
causal order, 115-125 words, 50-60 seconds, display, lineage, and cache gates;
only then may silent render and later voice run. Runtime data, DB/WAL, caches,
media, `data`, `ms_env.sh`, and credentials stay outside Git.

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

## Position-vector live repair result and snapshot correction (2026-08-21)

After the published `e743ab219a17f426c07baca5745dab82fdd7648b` checkpoint,
the authorized isolated harness made exactly one real `grok-4.3` repair
request and zero retries. It failed closed as
`cloud.narrative_word_count_out_of_range`. The sanitized report SHA-256 is
`44c4a9712da510ee53b63fd4eac395e20505c51bc84f15ff4abda95c875897a4`.
The response shape was a dict with only `rewrites`, array length 8, word
counts `[18,16,16,17,15,14,14,14]`, total 124, and estimated duration 52.17
seconds. The trusted `slot_order_hash` was
`a0c1a311a8a9e10ee9ccfc97b1bbac791abf59ae501c5f9b3a6bc4a8ba8f8823`.

The aggregate bounds were in range, but the later gate still rejected the
candidate. The report exposed a second observability defect: the runner's
cached shape snapshot retained `failed_predicate=null` after the later error.
The follow-up RED/GREEN fix now updates that in-process snapshot with the
stable failure code/predicate; it does not change admission behavior and no
second provider call was made. No valid narration, MP4, voice, or final QC is
proven.

## Current positional-repair checkpoint

The published base is `7598bd58880f75ad0309eedf05e9d485703a1d9b`. The current
repair contract is versioned `narration-targeted-repair-v4` with result cache
`narration-repair-result-v4`, position registry `narration-repair-position-registry-v3`,
and prompt `vision-first-story-analyzer-v3-targeted-position-repair-v3`.
Per-position word allocations are guidance and diagnostics only. Hard admission
still requires exact positional shape/order, non-empty text, trusted lineage,
causal order, 115-125 words, 50-60 seconds, grounding, identity, and cache
contracts. A broad dominance guard rejects one position over
`max(24, ceil(total_words * 0.25))`; it admits the observed 124-word vector.

## Post-repair final-gate diagnostic checkpoint (2026-08-21)

Published source/test checkpoint: `6e389e1f343308ebd08864e414a8cb301bbbaf25`.
The one authorized real `grok-4.3` repair request after it used request/retry
counts 1/0 and failed closed as `cloud.narrative_duration_out_of_range`.
Sanitized report SHA-256:
`bce6fee0304ece68e6f730abc75f1c53dd4afe2d1c89fe2e7debc4b353d026b6`.
Its provider-shape metadata was eight strings with counts
`[18,17,16,16,16,13,13,13]`, total 122, pre-reconciliation estimate 51.3s,
and slot hash `cb0ce195a2e661f703e3330bf1373a20e7e3e7ac83c49314cb9d661d9d12db6e`.

The follow-up diagnostic boundary now records reconstructed word/duration,
passage/observation/display counts, scope status, and the exact local failed
predicate without retaining provider prose. No second provider request is
permitted from this checkpoint; narration, MP4, voice, and QC remain unproven.

## Canonical narration duration checkpoint (2026-08-21)

Parent: `36bfa661e6aaffd59759c23cbf7d1ff719baa678`. The final Sharp Friend
duration contract is `narration-duration-v1`: tokenize the reconstructed
spoken text with ASCII alphanumeric runs (`[A-Za-z0-9]+`), use the dramatic
profile's 2.3 words/second, and calculate
`max(0.6, round(word_count / 2.3, 2))` (zero words produce zero seconds).
Final v3 admission remains hard at 115-125 canonical words and 50-60 seconds.
Per-position repair allocations are prompt guidance/diagnostics, not final
predicates. Legacy v1/v2 `word_count` and `estimate_duration` behavior is
unchanged.

The prior sanitized repair report showed 122 provider words and a 51.3-second
pre-reconciliation estimate because it used the earlier whitespace metric; it
did not contain reconstructed result metrics. The exact RED vector reproduced
the later rejection: a literal `\\n\\n` separator in the batched repair path
added four `n` tokens, turning 122 into 126. The GREEN boundary now joins real
newlines and computes the same canonical metric for positional reconciliation,
`NarrationResult.qc_report`, cache admission/source identity, persisted v3
`ScriptVersion` metadata, and render planning. The corrected 122-word vector
is 53.04 seconds under this contract.

Focused contract/integration verification is 278 passed with five existing
Pillow warnings. Clean-parent comparison reproduced all seven legacy
`tests/test_pipeline.py` fixture failures (vision-analysis prerequisite), so
they are not attributed to this slice. Full Oracle non-slow remains an
environment report, not a release claim: current working tree 1104 passed,
26 failed, 10 skipped; clean parent 1119 passed, 16 failed, 4 skipped. The
delta is runtime-state/FFmpeg/API/Windows-launcher dependent; production
acceptance still requires an FFmpeg host with the required encoder/probe
support and a real silent render.

After this source/test/docs checkpoint is published, resume once with the
existing cached visual/story state and exactly one bounded repair request:

~~~bash
cd /home/ubuntu/manhwashorts
set -a; source /tmp/ms_env.sh >/dev/null 2>&1; set +a
export PYTHONPATH=/home/ubuntu/manhwashorts
export MS_DATABASE_URL=sqlite:////data/data/p0-aws-acceptance/sample.db
export MS_STORAGE_DIR=/data/data/p0-aws-acceptance/storage
export MS_DATA_DIR=/data/data/p0-aws-acceptance
export MS_TMP_DIR=/data/data/p0-aws-acceptance/tmp
export MS_TTS_PROVIDER=null
export MS_ENVIRONMENT=local
export MS_REQUIRE_RIGHTS_DECLARATION=false
PATH=/home/ubuntu/.local/bin:$PATH .venv/bin/python scripts/run_cloud_multimodal_batch.py \
  --project-id 22876a6014a842f48bfca58c10a592b5 \
  --state-dir /data/data/p0-aws-acceptance/cloud-jobs \
  --segmentation-review-dir /data/data/p0-aws-acceptance/segmentation-review \
  --model grok-4.3 --max-attempts 1 --min-request-interval-s 0.3
~~~

Do not rerun visual/story stages, print `/tmp/ms_env.sh`, store provider
prose, or issue an automatic retry. If repair succeeds, continue to silent
render/QC, then the configured voice stage and warm-resume proof; otherwise
persist only sanitized metrics and stop provider calls.

## 2026-08-21 bounded repair result

The published canonical-duration checkpoint is `99b042ed`. The single
real positional repair request used the durable 160-word/64.35-second
candidate with `grok-4.3`, made exactly one request and zero retries, and
failed closed at `cloud.narrative_repair_position_budget_invalid`.
The sanitized report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-canonical-99b042e.json`:
8 strings, counts `[15,17,17,17,16,16,15,14]`, total 127, estimated 55.22s,
predicate `aggregate_word_count`. The 115-125 word bound is hard, so this
is a provider-output rejection, not a reason to weaken the contract. No
automatic retry or visual/story call is authorized until a new bounded
repair decision is published.

## Micro-compaction checkpoint - 2026-08-21

Parent: `9960076ce4d7dba93de968e0dc7b1581d92cfe8b`. The published repair
boundary now uses `narration-micro-compaction-v1` only for a narrow 126-130
word overshoot. It applies a deterministic, ordered list of audited English
contractions (including `it is` to `it's` and `does not` to `doesn't`), then
recomputes the canonical `narration-duration-v1` word count, duration, display
derivation, grounding, lineage, dominance, and final gates. It stops at 125,
never accepts below 115, rejects a missing safe operation as
`cloud.narrative_repair_micro_compaction_unavailable`, and rejects totals over
the window without provider retry. Valid 115-125 results are unchanged.

The repair result/cache contract is v5. The targeted-repair identity includes
the compaction policy version; the result and sanitized response metrics carry
the operation count/types, pre/post counts, and a hash of the transformed
rewrite vector. No provider prose is stored. RED was 4 intended failures and 1
existing hard-duration pass; GREEN was 5 focused tests, the full cloud
multimodal file passed, and scoped Ruff/compileall/diff checks passed. Visual,
story, narration admission, silent MP4, voice, and final QC remain unproven.

The complete Oracle non-slow run collected 1130 tests: 1124 passed, 2 failed,
and 4 skipped. Both failures are the existing `tests/test_operator_launcher.py`
Windows `cmd.exe` dispatch checks on this Linux host; they do not execute the
changed narration path. Treat this as an environment exception, not a
release-green claim, and rerun those launcher checks on Windows before
production acceptance.

## 2026-08-21 prepared subset manifest checkpoint

The prepared-panel manifest is now `prepared-panel-manifest-v2`. It preserves
trusted original `source_order`, source asset/checksum, crop bounds, dimensions,
and payload identity for audit/cache lineage, while adding the derived
contiguous `prepared_order` execution index. The 703-to-701 processed subset
therefore remains ordered and cache-valid when two poison panels are absent;
source lineage is never renumbered. Legacy v1 manifests migrate metadata-only
after hash, order, asset, and crop validation. No image decode, visual provider
call, or cache reanalysis is part of this migration.

The focused manifest/cloud/narrative matrix is 128 passed (11 manifest, 93
cloud, 14 narrative-pipeline, 10 narrative-QC). Ruff, compileall, diff-check,
and key-shaped secret scan passed. The previous normal-run blocker was
`PreparedPanelManifestError: prepared panel order is not contiguous`, with
durable taxonomy `cloud.narrative_repair_scope_invalid`; the fix separates
execution order from source lineage and rejects duplicate/reordered IDs,
changed payload/crop identity, and invalid legacy hashes. The planned normal
resume was executed once; its outcome is recorded below. Do not rerun
visual/story or print `/tmp/ms_env.sh`.

The one-request resume completed with `request_count=1` and zero retries, but
ended `NEEDS_REVIEW` at the local narration boundary with sanitized
`cloud.narrative_not_grounded` (`field=passage_evidence;count=5`). No narration,
MP4, TTS, or QC is proven. Do not issue another provider request until this
passage-evidence reconciliation boundary has a focused GREEN fix and a new
authorized checkpoint.

## 2026-08-21 passage-evidence reconciliation checkpoint

The next source/test checkpoint is based on published parent
`3cc0283923d4ebc1ce2904338f4ec96e5f2d0495`. The prior cached run consumed one
authorized `grok-4.3` request, made zero retries, reused the 701-panel
visual/story state, and stopped locally as `cloud.narrative_not_grounded`
with sanitized `field=passage_evidence;count=5`. No provider prose, narration,
MP4, voice, or QC artifact is proven.

Repair output owns rewrite text only. The local position registry now rebuilds
each retained passage's claim IDs and evidence panel IDs, including ordered
union lineage for merged positions. It rejects empty/unknown/duplicate claims,
foreign evidence, causal reordering, changed lineage hashes, malformed
containers, and stale passage-lineage versions with
`cloud.narrative_repair_position_lineage_invalid`. The versioned identity is
`narration-repair-passage-lineage-v1`; its hash is included in the position
registry, repair cache identity, persisted repair result, and sanitized QC.

TDD evidence before any new provider call: intended RED was 4 collection-clean
body failures; focused GREEN was 5/5; the full cloud file was 97/97 and the
related analyzer/script/manifest matrix was 121/121. Ruff, compileall, and
`git diff --check` passed. `tests/test_pipeline.py` still has 13 known
pre-vision fixture failures reproduced at this checkpoint and on the clean
parent; this is not a full-suite or production-render GREEN claim.

After this checkpoint is published, resume the normal project command once with
the same model and cached visual/story identities, at most one repair request
and zero automatic retries. If it passes, continue to narration persistence,
silent render/QC, then voice/TTS and warm-resume proof. If it fails, retain only
sanitized field/count/metrics and stop provider calls. Never repeat visual/story
calls, print `/tmp/ms_env.sh`, or stage runtime data, media, DB/WAL, caches, or
credentials.

## Post-publication bounded repair attempt — 2026-08-21

After `8097f0b8da60a32834d5e39d445df1393637457b` was published, the documented
legacy runner command was exercised once with a one-request budget. It reused
the 701-panel visual cache but missed the compatible story/candidate cache and
ended before targeted repair as `cloud.request_budget_exceeded`, with sanitized
job usage `request_count=1`. No repair-attempt record, provider prose,
narration, MP4, voice, or QC artifact was admitted. No further provider call
has been made.

The durable 160-word repair candidate has visual identity hash
`73c224732858ead17bdee4003cfc8824a7f1470e7e0a238f8baa5d80fd0b9579`, while the
current persisted 701-panel story context resolves to a different visual
identity. No matching visual/story cache was found. The strict repair boundary
therefore blocks rather than mixing identities or rewriting hashes. Do not
repeat visual/story calls, issue another provider request, or manually edit
runtime state until an exact local cache-reconciliation fix is reviewed.

## 2026-08-21 local narration admission/state discrepancy fix

The verified implementation parent is `78759e92dedf0c0ba9b6c6f49408c25dd4d7c68a`.
The scoped change is `app/services/cloud_multimodal.py` plus its focused
regression and the four handoff documents. Protected `data`, `ms_env.sh`,
DB/WAL, caches, logs, media, and credentials remain outside Git.

Root cause: targeted repair produced a continuity ledger for its selected
40-panel scope. Final assembly replaced only `NarrationResult.observations`
with the complete 701-panel observation ledger, leaving the selected 40-panel
continuity ledger attached. The lightweight admission predicate did not check
continuity coverage, so it returned usable; the shared persistence validator
then correctly rejected the mixed object as `cloud.narrative_not_grounded`.

The fix calls the existing analyzer continuity predicate at grounded admission,
validates panel order and full continuity in `_reconcile_narration_full_scope`,
and uses that helper before final narration admission. It copies only the
locally derived full structural ledger; it does not alter provider prose,
claims, evidence, hashes, duration, or quality gates.

Offline replay of the persisted job remains provider-free: the old object is
118 words/51.3 seconds/5 passages/8 claims/701 observations but has a 40-panel
continuity chunk and fails strict admission. Rebuilding from the existing
701-descriptor prepared manifest gives 701/701 continuity and passes the shared
analyzer validator and final admission. No DB or cache was modified.

Verification: the new RED regression failed on the old false-positive
admission; GREEN is 113/113 cloud tests and 83/83 related manifest/narrative/
script/vision tests, with Ruff, compileall, diff-check, no-churn, and changed
diff secret scan clean. Oracle non-slow is 1,154 collected: 1,148 passed,
2 failed, 4 skipped. The two failures are Windows `cmd.exe` launcher tests
that cannot run on Oracle Linux and are unrelated to this slice.

Resume only after the GREEN checkpoint is published: rerun the normal cached
service boundary with no visual/story repeat and no provider call required by
the local reconciliation path. Keep the runtime job's current `NEEDS_REVIEW`
state until the repaired result is persisted through the normal transaction;
do not claim narration, MP4, TTS, or QC readiness from this checkpoint.

## 2026-08-21 cached narration state-boundary continuation fix

Rollback parent for this follow-up is `5cff1984f48a6711e47fadad94557bb42cdb08fb`.
Publication commit is `392298a5b837462c9f3440a3e02328f316e3990c`.
The published continuity fix made local admission strict, but a resume of an
existing cached job could still treat a failed cached candidate as permission
to call `run_narration` again. That would turn a local state discrepancy into
an unnecessary cloud request.

`CloudBatchService._reconcile_cached_narration` now rebuilds only the ordered
observation and continuity fields from the current reconciled visual/panel
registry through `_reconcile_narration_full_scope`, before cached metadata and
grounding admission. A reconciled candidate can reach `READY_TO_RENDER`
without provider work; an unreconcilable cached object is recorded as a
fail-closed stage error. Provider prose, claim/evidence lineage, hashes,
duration, and all quality gates remain authoritative and unchanged.

The state-boundary regression stores a mixed selected-scope candidate, resumes
it through `CloudBatchService.run_job`, and installs a provider-dispatch
sentinel; it proves the job becomes `READY_TO_RENDER` and persists the full
continuity ledger without a narration call. The next runtime operation is the
provider-free normal reconciliation/persistence boundary using the existing
job and visual/story state. Do not repeat visual/story stages, issue a new
provider request, or edit runtime JSON/DB manually. No narration, MP4, TTS, or
QC readiness is claimed until that transaction and downstream gates pass.

## 2026-08-21 provider-free persistence boundary outcome

The published source/test checkpoint is `392298a5b837462c9f3440a3e02328f316e3990c`.
The normal checked-in batch entrypoint was then run with narration and repair
budgets set to zero. It made zero provider requests and locally reconciled the
cached candidate to one ordered 701-panel continuity ledger, but failed closed
at `pipeline.generate_script` / `_validated_persisted_vision_output` with
`PipelineError: persisted vision evidence is invalid` (`cloud.persistence_failed`).
The SQLite transaction rolled back and integrity remains `ok`; the current
project still has two existing `StoryAnalysis` rows with 280 panel regions
each, while the cached job manifest contains 701 panels. This is a DB
round-trip/selection or serialization mismatch still to isolate, not evidence
that the 118-word candidate is ungrounded. The job JSON is now `FAILED` from
this zero-budget attempt and its narration continuity is 701/701.

Do not edit the DB/job JSON manually, repeat visual/story work, or issue a
provider/TTS request. The next exact step is to test and fix the normal
701-panel persistence round-trip locally, rerun the zero-budget entrypoint,
and only then evaluate downstream rendering gates.

## 2026-08-21 strict repair evidence-closure checkpoint

Rollback parent: `bbd2211343715f781be821930b218d63ea713175`.

The rejected broad same-chapter fallback was not reintroduced. The targeted
repair registry computes `narration-repair-evidence-closure-v1` only from the
persisted candidate's exact passage and claim IDs, canonical story-map claim
evidence, and the beat/section ancestry for those claims. Its identity also
binds candidate visual/model/prompt hashes and story-map hash, model, prompt,
visual hash, and ordered panel IDs. Every candidate context panel must be in
that exact permitted section closure; foreign, unresolved, duplicate,
mixed-section, stale-story, changed-hash, or missing-ancestry data fails
closed as `cloud.narrative_repair_evidence_closure_invalid`.

The provider remains a positional text rewriter and returns no identifiers.
Local reconciliation copies trusted claim/evidence lineage from the closure;
it never accepts provider-owned panel or section IDs and never broadens to all
same-chapter panels. Positive exact-p2 ancestry and negative unrelated-panel,
missing-ancestry, and stale-story-identity regressions are collection clean.
The focused closure matrix is 5/5, the full cloud file is 122/122, and the
related analyzer/script/narrative matrix is 275/275. No provider request,
runtime-state edit, DB write, media, or secret was used for this checkpoint.

After publication, make exactly one bounded repair request to the already
configured same model, with zero retries and no visual/story repeat. Do not
proceed to persistence, silent render, or voice until the repaired result
passes the full closure, grounding, causal, duration, display, and cache
gates.

## Exact claim-position closure correction - 2026-08-21

The offline replay of the persisted candidate still failed before the provider:
the position registry attached each claim position to its passage-wide evidence
union, while the strict closure validator requires the exact evidence refs of
that position's trusted claim. This was a local admission contradiction, not a
reason to widen the permitted panel set. The correction publishes
`narration-repair-position-registry-v5` and binds each position to its validated
claim refs; passage reconstruction continues to union only those trusted
position refs. The candidate/story identity and
`cloud.narrative_repair_evidence_closure_invalid` fail-closed boundary are
unchanged.

The p2 regression now proves the two positions carry exactly their separate
trusted panels, while unrelated same-chapter, missing-ancestry, duplicate or
stale-identity cases remain rejected. Focused closure is 5/5, the cloud file is
122/122, and the related matrix is 275/275; no provider request or runtime
state was used. After this checkpoint is published, only the single previously
authorized same-model repair request may run, with zero retries and no
visual/story repeat.

## Strict multi-section repair closure v2 — 2026-08-22

Oracle is reachable through the real OpenSSH binary
`C:\Windows\System32\OpenSSH\ssh.exe`; the bare sandbox `ssh` wrapper is not a
network diagnostic. The current published base is
`24971e742653aeae48a2b15757adccf44a5dedb9`. The closure checkpoint is
published as `bd6f7d791d033f36f62c725b724fdcad9fdc2b8b` with that parent; the
tracked Oracle worktree is clean. `data` and `ms_env.sh` remain protected
untracked runtime paths.

The v1 closure defect was local and strict: passage p3 contains claims from
two canonical story sections, but each position was validating the full
passage context against one claim's ancestry. Positions 4 and 5 therefore
failed before network use. Closure v2 adds
`_story_passage_evidence_closure`, which unions the trusted ancestry of every
claim in that exact persisted passage. Each position still carries only its
own claim evidence; this is not a same-chapter fallback or a lineage
relaxation. The cache/schema identity is bumped to
`narration-repair-evidence-closure-v2`.

Offline proof after the fix: all eight persisted positions are `ROW_OK`, the
closure is `CLOSURE_OK` with hash prefix `e4636ae3`, and registry v5 remains
valid. The prior replay was `cloud.narrative_repair_evidence_closure_invalid`
with zero requests. Focused closure is 5/5, the cloud file is 123/123, the
related analyzer/story/narrative matrix is 211/211, the segmentation/vision
matrix is 134/134, Ruff/compileall/diff-check/no-churn pass, and no provider
or TTS request was made in this checkpoint.

The source/test/docs checkpoint is now committed and published. Use the
existing cached repair harness exactly once with the same configured model,
zero retries, and no visual/story
repeat. Persist only a fully admitted result; preserve all protected runtime
paths and never print `/tmp/ms_env.sh`.
