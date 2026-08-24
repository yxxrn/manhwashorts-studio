# FOLLOW-UP GREEN CHECKPOINT - 2026-08-20

## Visual-repair predicate checkpoint - 2026-08-24

The latest warm normal-entrypoint attempt reused 60/60 visual and story
results, then exhausted three bounded visual-narrative repair attempts. It
failed closed as `visual.narrative_repair_ungrounded`; the durable ledger
metadata is 5 feasible panels, 10 ROIs, and 2 missing sections. This boundary
now classifies local repair predicates into sanitized metadata and provides
targeted retry guidance while retaining all strict grounding, lineage,
chronology, duration, and visual gates. The instrumentation patch must be
published before another provider request; no TTS/render/MP4 is claimed.
The latest exact predicate is `visual.repair_chronology`; only non-hook
citations are required to be nondecreasing by source order, so a later
evidence-backed hook remains valid at position zero.

Resume now reuses a persisted repaired narration when its feasible-panel and
missing-section validators pass, rather than issuing a duplicate visual-repair
request. The validators remain authoritative and invalid persisted state still
falls through to bounded repair.

Review-render failures now retain nested stable render, FFmpeg, encoder,
quality, audio, timeline, and media codes instead of collapsing to
`review.preview_failed`; this is diagnostic only and leaves all render/QC gates
unchanged.

## Frozen release-candidate gate - 2026-08-23

The corrected production wall-clock gate is `<=90` minutes from ingestion
through upload-ready MP4 and strict QC. After Video 1 is accepted, the
release-candidate commit and fixed production configuration are immutable for
the fresh `The Novel’s Extra` run. No code, gate/config threshold, manual
DB/artifact, or editorial changes are allowed during that proof. Persist start
and finish SHA/config fingerprints, exact command, per-stage/critical-path
timings, provider/cache behavior, and full QC. A failure rejects the release
candidate; fixes must be generic, regression-tested, newly published, and
followed by a fresh proof from zero state. Historical narration duration
contracts remain 50-60 seconds.

## Hard production wall-clock gate - 2026-08-23

The acceptance target is `<=90` minutes wall-clock from ingestion through an
upload-ready MP4 and all strict QC. A run over 60 minutes is not production
ready even when its quality checks pass. Instrument preparation, visual/story
map, narration/repair, timeline, TTS, render, and parallel QC with wall time,
critical-path time, request/retry/concurrency, cache hits, and peak resource
metrics. The invalidation DAG must persist and reuse prepared manifests,
feasibility/ROI/timeline inputs, and versioned provider results; direct
preflights precede expensive full runs. No gate may be weakened for speed.

## Cached visual-stage metadata reuse - 2026-08-23

The review resume boundary may load a prepared manifest without panel pixels.
`CloudStageRunner.run_visual_evidence` must therefore derive its canonical
ordered source/prompt cache key and query the durable visual cache before
enforcing materialization. A cache hit is safe because the cached result is
already keyed by panel identity, model, and prompt; a miss remains a strict
`cloud.prepared_manifest_requires_materialization` failure rather than a
provider call with incomplete input.

RED reproduced the warm-run failure; GREEN is 162/162 focused tests (137 cloud,
13 visual-repair, 12 prepared-manifest), Ruff, compileall, and diff-check. No
provider/TTS/render request was consumed. The existing 701-panel visual/story
cache and prepared manifest remain reusable; the next job resume is the first
runtime proof of this boundary.

## Warm prepared-manifest reuse - 2026-08-23

The review DAG now follows the same durable prepared-manifest boundary as
regular resume: restore first, cold-materialize only when the manifest is
missing or invalid, and persist the rebuilt manifest atomically. This avoids
the prior repeated 701-panel decode/OCR cost while keeping source checksum,
prepared order, payload hash, visual identity, and feasibility validation
unchanged. No cloud/TTS/render request was consumed by this fix.

Verification is 150/150 focused cloud/visual-repair tests, Ruff, compileall,
and diff-check. The next repair request is separately scoped by the v2/v3
visual-repair contract; visual/story caches remain reusable.

## Versioned visual-repair cache boundary - 2026-08-23

The review repair DAG now scopes provider-result reuse to an explicit repair
contract version. The prior run reused the 701-panel visual/story cache and
accepted 122-word narration, then failed at the current 36-panel/71-ROI
feasible ledger with two missing sections and no new requests. Contract v2,
prompt v3, and an explicit `contract_version` input to `repair_cache_key`
invalidate old section-closure responses without invalidating visual/story
evidence. The failure remains fail-closed; no visual or publish gate changed.

Verification: 149/149 focused cloud/visual-repair tests, Ruff, compileall,
and diff-check. No preview/QC artifact is claimed. The next resume must use
the newly versioned repair request and retain the 701-panel cache.

## Visual-repair analyzer diagnostics - 2026-08-23

The repair DAG now keeps strict analyzer failures actionable without retaining
provider content. A failed repair response is classified locally as
`analyzer_contract_invalid` plus a stable field and count, and the next
bounded attempt receives field-specific structural guidance. The three real
attempts after the prepared-payload fix reached a 36-panel/71-ROI feasible
ledger and failed closed as `cloud.narrative_not_grounded`; the valid
701-panel visual/story identities were not recomputed and no TTS call ran.

Focused RED/GREEN verification is 148/148 (135 cloud, 13 visual-repair), with
Ruff, compileall, and diff-check clean. The change affects observability and
retry precision only; all evidence, chronology, grounding, anti-copy,
feasibility, resolution, audio, and publish gates remain strict. No media/QC
completion is claimed.

## Persisted prepared-payload review boundary - 2026-08-22

The review DAG now preserves the prepared-panel materialization across the
persistence boundary. If `CloudBatchService._repair_review_narrative` has
non-empty manifest-restored `CloudPanelInput` rows, it calls the existing
panel-keyed prepared-payload builder; only callers with no prepared payloads
use `_load_reference_panel_fallback_candidates`. This avoids treating a
segmented `SourceAsset` crop as an original-strip coordinate space. The
offline probe recorded 701 prepared/visual rows, 588 rows reaching candidate
construction, and 113 geometry-invalid crop-fallback rows; it made no cloud
or TTS request.

The change is a source-materialization correction, not a gate relaxation:
the same visual evidence hash, source checksum/bounds, border-mask,
candidate-is-feasible, lineage, chronology, and publish checks run afterward.
The RED/GREEN regression and focused matrix are 147/147 passed (134 cloud,
13 visual-repair), with Ruff, compileall, and diff-check clean. The current
job still has no accepted MP4/audio/QC artifact; the next step is a cached
review rerun with no visual/story reanalysis.

## Strict narration anti-copy boundary - 2026-08-21

Rollback parent: `a2d9e85eb5caa05abf792294b7265eed0300c67b`.

The persisted 701-observation candidate was replayed offline through the
authoritative analyzer gate. Only passage `p2` failed: claims
`b1__sub0__claim2`, `b1__sub0__claim3`, and `b1__sub0__claim4` shared one
normalized four-word dialogue sequence from panel
`region-a1ceb6aece5c808c9bee`; all other sanitized metrics remained 5
passages, 118 words, 51.3 seconds, and complete lineage.

The repair instruction is now versioned with explicit third-person paraphrase,
quote/name-change anti-loophole guidance, and four-word sequence avoidance.
Both production narration validation call sites use the strict default; repair
contract/result versions advance to v5/v6 so old copied-dialogue results are
not reused. RED exposed the old prompt/bypass and GREEN is the 5-test focused
set plus 269 affected matrix tests. This changes no visual/story cache and
makes no provider call. The next bounded stage is one cached narration repair
request, followed only by local persistence and silent render if strict
admission succeeds.

### Repair compaction v2 — 2026-08-22

The repair reducer now records a sanitized response shape before any local
failure. The first post-closure response was 8 strings/128 words/55.65 seconds
and failed only because v1 had no safe contraction match. Compaction v2 is a
versioned local post-reconciliation transform with a narrow audited standard
contraction vocabulary. It can reduce 126–130 words only to the hard maximum
125, preserves slot order/meaning/lineage, and changes cache identity through
the policy version and result hash. It never relaxes duration, grounding,
anti-copy, visual, or publish gates.

GREEN proof: compaction 4/4, cloud 124/124, related 211/211, static and
no-churn checks pass; no second provider request occurred. Publish before the
next bounded repair call.

### Evidence-closure v2 for multi-section passages — 2026-08-22

The repair reducer has two distinct closure scopes. A position resolves its
own claim IDs to exact trusted panel evidence. A persisted passage resolves
the ordered union of all of its claim IDs to the canonical story beat/section
ancestry. The validator compares passage context with that union, never with
an arbitrary same-chapter set, and still requires each position's evidence to
equal its trusted claim refs. This fixes the real p3 cross-section failure
without weakening lineage.

Closure identity is now
`narration-repair-evidence-closure-v2`; registry v5, model/prompt/visual/
story identity, ordered panel IDs, and closure hash remain required. Offline
replay proves eight rows `ROW_OK` and `CLOSURE_OK` (hash prefix `e4636ae3`).
The focused closure set is 5/5, cloud is 123/123, related analyzer/story/
narrative is 211/211, segmentation/vision is 134/134, and static/no-churn
gates pass. No provider request or runtime artifact was touched. This
checkpoint is published as `bd6f7d791d033f36f62c725b724fdcad9fdc2b8b`; the
single authorized repair call is the next external action.

## Trusted passage-evidence reconstruction

The repair path previously required every trusted story-claim reference to
already appear in the persisted candidate passage. That made a valid local
lineage repair impossible when a stale candidate carried an incomplete
passage evidence list, producing `cloud.narrative_repair_slot_lineage_invalid`
with zero provider calls. Slot construction now validates the candidate list
and deterministically rebuilds the ordered evidence union from trusted local
claim refs; unrelated or foreign refs remain blocking. This is lineage
reconciliation, not evidence invention or gate relaxation.

## Anti-copy repair-trigger correction

The persisted 118-word candidate was valid for duration but not for strict
dialogue-copy admission. Before this correction, the targeted repair boundary
returned `cloud.narrative_repair_not_needed` without spending a request because
its trigger enumerated only duration and word-count failures. The trigger now
calls the shared analyzer dialogue detector and emits
`cloud.narrative_source_dialogue_copy`; this is a repair selector, not a
quality relaxation. The final analyzer/persistence gate remains the authority.

## Persistence round-trip invariant - 2026-08-21

Rollback parent: `f1f08bc2e9cd067b8703ba1d28298012cf27b74f`.

`persist_cloud_chapter` is a single exact-analysis persistence boundary:
after flushing a new `StoryAnalysis`, it calls `generate_script(...,
analysis_id=row.id)`. The loader uses that row when supplied and rejects a
foreign project. Legacy callers without `analysis_id` retain the existing
latest-row behavior. The earlier write/reload mismatch also came from a
preview-only `allow_dialogue_copy=True` persistence call; both write and
reload now use the strict analyzer contract, so copied dialogue remains a
blocking quality failure.

The database model preserves immutable source lineage (`source_order`) while
the persisted observation ledger records the contiguous execution order
(`source_index`). A fake-provider 701-panel round trip proves 701/701 rows,
stable panel IDs, sparse original source orders, and a full coverage manifest
after a new-session reload. A foreign-analysis test and a post-flush rollback
test cover fail-closed identity and transaction behavior. The cloud suite is
116 passed and the analyzer/script matrix is 110 passed; Ruff, compileall,
diff-check, and no-churn pass.

The protected real sample DB was not manually edited. A zero-budget normal
entrypoint replay made no requests and ended `NEEDS_REVIEW` with
`cloud.narrative_not_grounded` because the current candidate fails the strict
`script passage copies source dialogue` predicate. SQLite integrity is `ok`,
and no narration, MP4, TTS, or QC stage is admitted. This checkpoint therefore
proves the persistence contract offline, not real 701-panel admission.

## Post-publication identity outcome and CLI redaction boundary - 2026-08-21

After identity checkpoint `87aed29e1600484dec07e8e1aadbdcfdeae7573e`, the
metadata-only runtime scan found four legacy narration-repair candidates with
no canonical `identity_metadata`; migration was correctly not admitted. The
first normal command used the default empty DB and made zero requests; a
process-local `MS_DATABASE_URL` override selected the protected sample DB.

The single authorized same-model run consumed two independent requests
(`narration=1`, `narration_repair=1`, `other=0`) without visual/story repeat.
It ended `NEEDS_REVIEW` with `cloud.narrative_not_grounded`. Safe metrics show
118 words, 51.3 seconds, 5 passages, 8 claims, and 701 ordered observations;
display, duration-contract, and passage/claim/panel lineage checks all pass.
The remaining admission/state discrepancy is unresolved; no downstream
narration, silent render, voice, or QC stage may proceed and no new provider
request is allowed.

The normal CLI was also printing its complete job object, including stage
payloads, despite its safe-output contract. A body-level RED regression led to
the narrow `_safe_job_summary` boundary, which emits only job ID, state, stable
error/review codes, and request counts. This follow-up is the current
source/test/docs publication checkpoint.

## Narration repair identity and independent request budgets - 2026-08-21

The metadata-only reconciliation boundary is versioned
`narration-repair-identity-v1`. Its canonical dependency shape contains the
exact ordered processed-panel IDs and visual-evidence identities, model and
prompt hashes, story beat/claim/causal hashes, selection, trusted slot order
and claim/evidence refs, and candidate dependencies. Derived `prepared_order`
is ignored for equivalence; panel rows are normalized by panel ID, while the
ordered ID list is never reordered or relabeled. A semantic mismatch fails
closed as `cloud.narrative_repair_identity_mismatch` and persists only safe
counts, mismatch field, comparison hash, and reason.

The prior durable candidate is visual identity
`73c224732858ead17bdee4003cfc8824a7f1470e7e0a238f8baa5d80fd0b9579`; the
current 701-panel story context is
`a9a43faf0a198b1bf3a995858fba39bea65cb27be3152b7019e2dba8a9b24b9f`.
Because the legacy candidate record lacks `identity_metadata`, the migration
decision is fail-closed `legacy_identity_metadata_missing`; no semantic
equivalence is asserted and no hash is rewritten. Exact-equivalent metadata
migration, semantic-change rejection, idempotent migration records, and warm
loader reuse are tested.

Normal narration and targeted repair have separate caps of one request each;
the combined maximum is two only when a fresh candidate is required. Visual,
story-map, and other stages use a separate counter. Legacy callers using the
single global `max_requests` cap remain compatible. This prevents a normal
narration request from consuming the only repair budget. No provider call was
made while implementing or verifying this boundary.

Verification is 14 focused identity/budget passes, 111 cloud-multimodal passes
(five known Pillow deprecation warnings), and 83 related manifest/analyzer/
script/vision passes, with Ruff, compileall, diff-check, and secret scan
passing. The separate pipeline suite has the same 13 pre-vision fixture
failures on clean parent/current and remains an explicit non-regression
exception. No narration, MP4, TTS, or QC artifact is admitted by this slice.

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


# Oracle map-reduce production workflow

Updated for source/test checkpoint dfb8c26e6148bb8b3e098d25b1bf691e14f94cbd
on 2026-08-20. This is an interruption-safe architecture and performance
record for project 22876a6014a842f48bfca58c10a592b5; it does not claim that a
silent MP4 exists.

## Stage topology

1. Visual map uses the existing bounded provider workers and content-addressed
   per-stage cache. The cache identity includes source hash, panel IDs and
   order, model identity, prompt version/SHA, and the visual contract. A valid
   row is never called again. Current durable result: 701 processed rows out
   of 703 source rows; two poison rows remain explicitly skipped.
2. Chapter story map runs deterministic ordered chunks (default 180 panels,
   with strict 60/30 coverage fallback) through the bounded worker pool. Each
   chunk has its own cache key. The reduce step prefixes chunk IDs and merges
   beats, claims, and causal links by chunk order, then validates the complete
   701-panel ordered coverage. It never synthesizes a missing panel reference.
3. Editorial selection runs only after the complete story map. It scores
   grounded beats using claim/state-change/causal evidence, distributes the
   target across ordered buckets, keeps stable ties by source order and beat
   ID, and selects panel IDs from each beat's cited evidence. The selection
   hash contains the visual hash, story-map hash, version, beat IDs, claim IDs,
   panel IDs, and scores.
4. Final narration reduce receives only the selected evidence envelope and
   makes one final prose request. It does not concatenate chunk-level prose.
   The response is locally validated for grounding, coverage, continuity,
   Sharp Friend structure, naturalness, and the 115-125 word/50-60 second
   production contract. Full reconciled observations are restored on the
   result for downstream lineage.
5. Targeted repair may retry only the failed stage/section with sanitized
   contract feedback. It must not invalidate a valid visual cache or reuse a
   narration result whose observation count, passage count, spoken/display
   content, duration, or selection hash is incomplete.
6. Timeline/render/QC can start only after narration is durable. Base visual
   scene work may be cached by source/evidence/framing identity; subtitle,
   timing, and audio layers invalidate only their dependent artifacts. QC
   remains independently fail-closed for evidence, blank/balloon/protected
   framing, subtitles, motion, audio, black frames, and artifact integrity.

## Cache and invalidation DAG

source bytes + ordered segmentation
  -> visual request identity + visual cache
      -> story-map chunk identities + ordered reduce
          -> editorial selection hash
              -> final narration identity
                  -> provisional timing / timeline
                      -> base scene render
                          -> subtitles / audio / final QC

Changing source bytes, panel bounds, model identity, prompt hash, or visual
contract invalidates visual and every descendant. Changing only story-map
prompt/config invalidates story map, selection, narration, and descendants.
Changing selection invalidates narration and descendants. Changing narration
or timing invalidates subtitle/timeline/audio layers but not valid visual or
story caches. A valid same-identity visual row is never resent.

## Measured checkpoint

The last pre-topology resume measured 3 narration requests, 975.73 seconds
wall time, and peak RSS 8,397,748 KB before ending in
NEEDS_REVIEW/cloud.narrative_not_grounded; no FFmpeg process or MP4 was
created. The focused topology gate is 48 passed with five existing Pillow
deprecation warnings. These numbers are baselines, not acceptance claims:
the cold target is <=60 minutes, hard ceiling <=75 minutes, warm resume <=15
minutes, and narration-only rerun <=10 minutes.

## Safe resume

After checking the job JSON, cache identity, and that no duplicate process is
active, source /tmp/ms_env.sh with output redirected and set the runtime
variables to the /data/data/p0-aws-acceptance paths. Run the focused cloud
suite, then use the normal checked-in project service resume. Do not use an
agent-authored database edit or replay wrapper. Persist a stage result before
advancing; on failure record the stable code and resume from the last valid
identity-matching cache. Never copy provider payloads, keys, source images,
DB/WAL files, caches, logs, or media into Git.

## Release gates

A source/test/docs checkpoint is green only when focused and relevant tests,
Ruff, compileall, diff-check, allowlist, and secret-scope scans pass. The
production run is green only with real narration, a real video-only MP4 and
FFprobe/blackdetect/contact-sheet/QC evidence. Voice/TTS may follow the
verified silent preview; publish_allowed remains false until rights and
editorial approval gates pass.

## Positional repair observability correction

The positional rewrite reducer now emits a private, in-process shape record
only after the response has passed local positional reconciliation. The
record contains non-prose response metadata: container type and top-level
keys, array length, per-position word counts, total words, duration estimate,
trusted slot/order identity, and the next local predicate/code when a later
gate rejects the candidate. `_run_narration_batched` copies that record into
the existing sanitized failure report and removes the private field before
the normal analyzer contract sees it. This keeps later-gate diagnosis durable
without storing provider prose or changing admission gates.

The current parent attempt failed once with
`cloud.narrative_word_count_out_of_range`; its report had empty shape metrics,
which is the defect this checkpoint closes. The focused regression is
collection-clean and 151/151 green. A new provider request is prohibited
until this source/test/docs checkpoint is published; visual/story caches are
not invalidated.

## Live positional repair result and snapshot correction

The one authorized real repair request after `e743ab2` used `grok-4.3`,
request/retry counts 1/0, and failed closed as
`cloud.narrative_word_count_out_of_range`. The sanitized report recorded a
dict containing only `rewrites`, array length 8, counts
`[18,16,16,17,15,14,14,14]`, total 124, duration 52.17 seconds, and
slot-order hash
`a0c1a311a8a9e10ee9ccfc97b1bbac791abf59ae501c5f9b3a6bc4a8ba8f8823`.
The aggregate bounds passed, but the later gate rejected the candidate. The
runner snapshot originally kept `failed_predicate=null`; the follow-up fix
updates the same non-prose snapshot with the stable failure code/predicate so
future durable reports retain the later-gate taxonomy. No second provider
call is permitted by this checkpoint, and visual/story caches are untouched.

## Positional repair admission v4

The position registry still supplies deterministic per-position budgets for
provider drafting and sanitized diagnostics, but those ranges are not final
admission predicates. The reducer admits the observed 124-word/52.17-second
distribution `[18,16,16,17,15,14,14,14]` and retains the aggregate word,
duration, lineage, grounding, causal, identity, and display gates. It rejects
only a broad pathological single-position share above
`max(24, ceil(total_words * 0.25))` in addition to the hard vector/type checks.
The repair prompt is `vision-first-story-analyzer-v3-targeted-position-repair-v3`;
cache/result identities are versioned so old results cannot be reused under the
changed contract.

## Post-repair final-gate diagnostic checkpoint

The single authorized request after `6e389e1f343308ebd08864e414a8cb301bbbaf25`
used `grok-4.3`, request/retry counts 1/0, and failed closed as
`cloud.narrative_duration_out_of_range`. Its sanitized report SHA-256 is
`bce6fee0304ece68e6f730abc75f1c53dd4afe2d1c89fe2e7debc4b353d026b6`.
The response shape was a dict with only `rewrites`, eight strings, counts
`[18,17,16,16,16,13,13,13]`, total 122, estimate 51.3s, and slot-order hash
`cb0ce195a2e661f703e3330bf1373a20e7e3e7ac83c49314cb9d661d9d12db6e`.

The repair reducer now adds post-reconciliation metrics to its private
sanitized snapshot: reconstructed word count, spoken-token count, duration,
passage/observation/display counts, visual-panel count, scope status, and a
stable failed-predicate list. `_response_shape_metrics_for_failure` preserves
the first reconstructed predicate while retaining the public stable error
code. No provider prose is stored, and this follow-up does not relax the
final vector, grounding, causal, identity, or duration gates. Focused tests:
153 passed with five existing Pillow warnings; no narration, MP4, voice, or
QC artifact is proven and no second provider request is authorized here.

## Canonical narration-duration contract and resume boundary

The v3 narration DAG now has one local timing identity, `narration-duration-v1`:
the final reconstructed spoken text is tokenized as ASCII alphanumeric runs,
dramatic pacing is 2.3 words/second, and non-empty duration is
`max(0.6, round(words / 2.3, 2))`. The 115-125 word and 50-60 second bounds
are final admission gates. Position budgets remain provider guidance and
diagnostics. The contract identity is included in narration source/cache
inputs; the computed metrics are persisted in `NarrationResult.qc_report`, in
v3 `ScriptVersion.editorial_metadata`, and consumed by render planning.

The prior report's 51.3 seconds was a pre-reconciliation whitespace estimate
for the observed 122-word vector. The RED end-to-end fixture exposed the
actual boundary defect: the batched reducer used literal `\\n\\n`, adding four
`n` tokens between passages and causing a 126-word later-gate result. The
reducer now joins actual newlines and every downstream gate recomputes the
same canonical metric; 122 words therefore produce 53.04 seconds.

The current working tree's focused matrix is 278 passed with five existing
Pillow warnings. The seven legacy pipeline fixture failures reproduce on clean
parent. Full Oracle non-slow is not a host-readiness claim: current is 1104
passed/26 failed/10 skipped versus clean parent 1119/16/4. Failures are
runtime-state/FFmpeg/encoder/probe/API/TTS/Windows-launcher dependent and the
production host must satisfy those capabilities before silent or voiced QC.

Published-resume command (one repair request, no visual/story repeat):

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

If the repair fails, keep only sanitized shape/predicate metrics and stop
provider calls. If it succeeds, advance through narration persistence, silent
render/QC, TTS/alignment, voiced QC, and warm-resume accounting.

### Repair observability checkpoint — 2026-08-21

The first real positional-vector attempt after `99b042ed` is durably
classified without provider prose: request count 1, retry count 0,
8-string response, per-position counts `[15,17,17,17,16,16,15,14]`, total
127, canonical duration estimate 55.22s, and failed predicate
`aggregate_word_count`. The report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-canonical-99b042e.json`.
Because 127 exceeds the hard 115-125 admission range, the rejection is
correct and no stage cache or quality gate is weakened; no automatic retry
is permitted.

## Narrow repair compaction boundary - 2026-08-21

The positional repair reducer now has a local `narration-micro-compaction-v1`
step between provider-vector shape validation and final admission. It accepts
only 126-130 canonical words, walks rewrite positions and the fixed audited
contraction table deterministically, and stops at 125. It never drops content
words or evidence slots. `script.narration_word_count` is the canonical
tokenizer for this path and treats apostrophe contractions as one word; the
legacy helpers remain unchanged. The reducer recomputes duration, display,
grounding, causal, lineage, dominance, and cache checks after transformation.

Failure taxonomy is explicit: `micro_compaction_window` for totals outside
the narrow window and `micro_compaction_no_safe_operation` when no approved
operation remains, both surfaced as
`cloud.narrative_repair_micro_compaction_unavailable` with non-prose metrics.
Repair cache version is `narration-repair-result-v5`; its key contains the
policy version and its value records the transformed rewrite hash, operation
types/count, and pre/post totals. Valid in-range vectors bypass compaction
unchanged. RED/GREEN proof is 4 intended failures + 1 duration-pass RED, then
5 focused passes plus the full cloud multimodal file and scoped static gates.
No real repair request has been made after this code checkpoint.

Full non-slow Oracle verification collected 1130 tests and measured 1124
passed, 2 failed, and 4 skipped. The two failures are the existing Windows
`cmd.exe` operator-launcher dispatch tests on Linux and occur before the
changed code runs. The related 117-test cloud/narrative matrix passed. This
checkpoint is source/test green but not a host-level production-render claim.

## Prepared execution order versus source lineage

The durable prepared-panel manifest now uses `prepared-panel-manifest-v2` for
the warm metadata boundary. Every descriptor carries both coordinates:

- `source_order`: immutable original segmentation order, retained for source
  asset/crop/evidence lineage and audit. It may contain intentional gaps when
  poison panels are excluded from a review subset.
- `prepared_order`: derived contiguous execution index assigned after the
  verified ordered subset is selected. It is always `0..len(subset)-1`.

The rebuild path validates source asset checksum, source dimensions, canonical
panel bounds, visual identity/payload hash, unique panel ID, strictly
increasing original source order, and contiguous prepared order before writing
the metadata manifest. It never decodes panel bytes or calls the provider.
Legacy v1 manifests are validated against their original hash first, then
rewritten in memory as v2 with derived indices; invalid legacy hashes fail
closed. The marker payload and manifest hash are versioned, while the visual
model/cache identity remains based on the trusted source/crop/rendered-payload
identity rather than temporary paths or subset relabeling.

The exact regression uses 703 synthetic source regions with two filtered rows
and 701 visual rows. It proves preserved source orders, `prepared_order`
0..700, metadata-only cached rebuild, stable restoration, legacy migration,
and rejection of duplicate/reordered/crop/payload identity tampering. Focused
manifest/cloud/narrative verification is 128 passed; no provider request was
made by this fix. The subsequent cached batch runner used its one authorized
request and failed at the local narration boundary; visual/story stages remain
cache hits and all downstream grounding, duration, render, and voice gates
remain authoritative.

## 2026-08-21 prepared-subset and repair outcome

The published v2 manifest checkpoint is `bcfb971`. One cached normal run
recorded `request_count=1`, zero retries, and no visual/story repeat. It failed
closed at the local passage-evidence admission predicate:
`cloud.narrative_not_grounded`, `field=passage_evidence`, `count=5`. No admitted
narration result or downstream media stage exists. Treat this as a contract
boundary defect, not permission to weaken grounding or issue an automatic retry.

## Passage-lineage reconciliation boundary — 2026-08-21

The prior 701-panel cached run used one authorized narration-repair request and
zero retries, then failed locally as
`cloud.narrative_not_grounded` (`field=passage_evidence`, `count=5`). This was
a reconciliation defect: the positional repair provider owns rewrite text, not
passage evidence identifiers. No prose, narration result, MP4, voice, or QC
artifact was retained.

The corrected reducer derives `narration-repair-passage-lineage-v1` from the
trusted local position registry. It reconstructs claim IDs and evidence panel
IDs for every retained passage, preserves causal order, and unions only the
trusted refs of positions merged into that passage. The canonical lineage hash
is included in the registry/slot identity, repair cache key, persisted repair
record, and sanitized QC metadata. Empty, foreign, duplicate, unknown,
reordered, malformed, stale-version, and changed-hash inputs fail closed as
`cloud.narrative_repair_position_lineage_invalid`.

TDD/static proof before another provider call: 4 intended collection-clean RED
failures became 5 focused passes; the full cloud file is 97/97 and the related
analyzer/script/manifest matrix is 121/121. Ruff, compileall, diff-check, and
no-churn stats pass. The separate pipeline integration file still has the same
13 pre-vision fixture failures as its clean-parent comparison; it is a named
environment/fixture exception and not a production readiness claim.

The next authorized runtime operation, only after this checkpoint is published,
is one bounded same-model repair request with zero retries and no visual/story
repetition. A valid result must still pass canonical grounding, causal order,
115-125 words, 50-60 seconds, display derivation, cache identity, and lineage
gates before persistence and render. A failed request stores only sanitized
shape/count/metrics and predicate data; no automatic retry is allowed.

## Post-publication repair-resume outcome — 2026-08-21

The documented legacy runner was invoked once after `8097f0b`. The 701-panel
visual cache was reused, but the compatible story/candidate cache was absent;
the normal stage consumed the one-request budget and ended before targeted repair
as `cloud.request_budget_exceeded`. Durable usage is `request_count=1`; no
repair-attempt record or downstream artifact exists, and no further provider call
has been issued.

The durable repair candidate carries visual identity
`73c224732858ead17bdee4003cfc8824a7f1470e7e0a238f8baa5d80fd0b9579`, while the
current persisted story context carries a different visual identity. The
identity checks correctly prevent mixing them. The next bounded slice is local
metadata-only reconciliation of an exact matching visual/story/candidate cache
or a fail-closed stale-cache report; no hash rewrite, visual/story re-call, or
provider retry is allowed before that fix is published.

## Local narration admission/state discrepancy invariant — 2026-08-21

Rollback parent: `78759e92dedf0c0ba9b6c6f49408c25dd4d7c68a`.

The repair path has two legitimate scopes: a selected-panel repair scope and
the complete ordered chapter scope. The defect mixed them by retaining the
selected 40-panel `continuity_ledger` while replacing observations with all
701 panels. The old lightweight admission checked observation and claim
lineage but not continuity coverage; persistence then rejected the same object
through the shared analyzer validator as `cloud.narrative_not_grounded`.

The canonical boundary now calls the existing analyzer continuity predicate at
grounded admission. `_reconcile_narration_full_scope` requires exact ordered
panel IDs and a valid locally derived full structural ledger before final
assembly; it copies no provider-owned evidence or identifiers. Intermediate
chunk/selected repair validation remains scoped to its own visual input, while
the final result and persistence boundary use the complete scope.

Offline evidence is metadata-only: the persisted candidate is 118 words,
51.3 seconds, 5 passages, 8 claims, and 701 observations, but its continuity
chunk covers 40/701. Rebuilding from the durable 701 descriptor manifest gives
one ordered 701-panel continuity chunk and passes the shared analyzer validator
and final admission. No image bytes, DB/WAL, cache, provider, or secret state
was touched.

TDD/static evidence is 113 cloud tests and 83 related tests passed; Ruff,
compileall, diff-check, no-churn, and changed-diff secret scan pass. Non-slow
is 1,154 collected with 1,148 passed, 2 failed, and 4 skipped; both failures
are the Oracle-Linux host's inability to execute Windows `cmd.exe` launcher
tests. No narration, MP4, TTS, or QC readiness is claimed until the published
code resumes the existing job through normal persistence and downstream gates.
## Cached narration state admission invariant — 2026-08-21

Rollback parent: `5cff1984f48a6711e47fadad94557bb42cdb08fb`.
Publication commit: `392298a5b837462c9f3440a3e02328f316e3990c`.

The map/reduce runtime has two legitimate scopes: provider repair may operate
on a selected panel scope, while final narration admission requires the
complete ordered visual scope. A cached repair result once crossed those
boundaries with all observations but a selected continuity ledger. The
published continuity predicate rejects that object; this follow-up prevents
the resume state machine from interpreting the local rejection as permission
to call narration again.

`CloudBatchService._reconcile_cached_narration` rebuilds full observations and
the continuity ledger from the current visual/panel registry through the same
`_reconcile_narration_full_scope` helper used by final assembly. Only after
that local reconciliation do model/prompt/visual identity, canonical duration,
grounding, and shared analyzer gates run. A valid cached object resumes to
`READY_TO_RENDER` with zero cloud calls; invalid lineage is recorded as a
fail-closed error. No provider-owned prose, claim/evidence IDs, hash rewrite,
or quality-gate relaxation is possible at this boundary.

The new regression proves the full state transition using a provider-dispatch
sentinel and persists the 701-panel continuity ledger. Runtime caches and the
job remain unchanged until the normal provider-free persistence step is run.

## Persistence boundary checkpoint — 2026-08-21

The zero-budget normal entrypoint run after publication made no provider calls
and passed local narration scope reconciliation (`701/701` continuity). It
failed at the downstream DB round-trip in
`pipeline.generate_script` / `_validated_persisted_vision_output` with
`cloud.persistence_failed` (`PipelineError: persisted vision evidence is
invalid`). The transaction rolled back and SQLite integrity remained `ok`.

The durable job/manifest has 701 panels, while read-only inspection of the
current project's two pre-existing `StoryAnalysis` rows found 280 panel
regions in each. This establishes a persistence-context mismatch but does not
yet identify whether row selection, serialization, or another DB round-trip
predicate is responsible. The repair must keep the canonical 701-panel
lineage and all existing analyzer/visual gates; no migration, manual DB edit,
provider retry, or fallback is permitted. Re-run only after an offline RED /
GREEN persistence regression proves the exact boundary, then use the same
zero-budget normal entrypoint before any cloud or TTS stage.

## Repair evidence-closure boundary — 2026-08-21

Rollback parent: `bbd2211343715f781be821930b218d63ea713175`.

The targeted repair reducer now has a strict closure identity separate from
the text rewrite vector. Local code takes the exact retained passage and claim
IDs from the persisted candidate, resolves each claim to canonical story-map
evidence, and derives the allowed panel set from the matching beat/section
ancestry. It records ordered section keys, permitted panels, candidate/story
visual and model/prompt identity, ordered story panel IDs, and a closure hash
under `narration-repair-evidence-closure-v1`. Requested context outside that
closure, unresolved or duplicate IDs, unrelated same-chapter sections, missing
beat ancestry, stale story identity, or changed hashes fail closed as
`cloud.narrative_repair_evidence_closure_invalid`.

The provider has no evidence-ID field in this contract: it returns only the
ordered rewrite strings. Reconciliation validates the exact local closure and
copies trusted claim/evidence lineage; it never accepts provider IDs or
silently widens the panel set. The focused suite includes a positive exact-p2
ancestry case plus unrelated-panel, missing-ancestry, and stale-story negative
cases: focused closure 5/5, cloud file 122/122, and related matrix 275/275.
This change is offline-only; no visual/story/provider request or runtime
artifact was touched. After publication, permit one bounded repair
request with zero retries, then admit/persist only after all existing
grounding, causal, duration, display, identity, and cache gates pass.

### Position-level evidence closure correction - 2026-08-21

The closure replay exposed a local representation mismatch: an expanded
position had been assigned its passage-wide evidence union, but the closure
predicate is claim-specific. Registry identity is now
`narration-repair-position-registry-v5`; each position carries only the exact
canonical story-map evidence refs for its own claim, and passage lineage is
reconstructed by an ordered union of those trusted rows. No panel or section
set is widened. The p2 regression proves distinct claim/panel rows, while
unrelated same-chapter, missing-ancestry, duplicate/mixed, foreign, and stale
story identities remain rejected with the stable closure code.

Offline proof is focused 5/5, cloud 122/122, related 275/275, with no provider
request or runtime artifact change. The next authorized action after publication
is exactly one same-model zero-retry repair request; visual/story caches remain
untouched and downstream persistence/render/voice gates stay closed until full
admission succeeds.

### Repair budget terminal outcome — 2026-08-22

After compaction v2 was published at `a40e51b79808bc8520cf422bce0f0af838f8fe7e`,
one bounded same-model request was consumed. Its sanitized response shape was
8 strings, 112 words, and 48.7 seconds; local admission failed at the hard
`aggregate_word_count` predicate. No provider prose was retained, no retry was
issued, and no local expansion or threshold relaxation is safe. Downstream
script persistence, silent/voiced render, TTS, and QC remain closed.


## Micro-compaction v3 repair checkpoint — 2026-08-22

After published f4fa6e77bde41758e5b02e62dfb241aa5bbf0cf6, one newly authorized bounded same-model repair request used exactly one request, zero retries, and no visual/story calls. Sanitized response metadata was eight strings, 126 words, and 54.78 seconds; local admission failed closed as cloud.narrative_repair_micro_compaction_unavailable with predicate micro_compaction_no_safe_operation. Provider prose was not retained.

The RED regression reproduced the 126-word narrow overshoot. GREEN adds five audited conversational auxiliary contractions, bumps the policy identity to narration-micro-compaction-v3, and retains the hard 115-125-word and 50-60-second gates. The new 126-to-125 reconciliation regression passes. The full cloud/related matrix is 336/336; the complete compaction-focused selection is 6/6; Ruff, compileall, diff-check, no-churn, allowlist, and secret-shape scans are clean. No padding, truncation, lineage change, or gate relaxation was introduced.

The v3 source/test/docs checkpoint is committed and published as `95965721b253468258483aeda9b64eb998153565` from parent `f4fa6e77bde41758e5b02e62dfb241aa5bbf0cf6`. Visual/story caches remain reusable; narration persistence, silent/voiced render, TTS, and QC remain closed until a repair result passes strict admission.


## 2026-08-22 - positional repair response-shape diagnostics checkpoint

- Published baseline: `2cd528ebc35748fbc72582f5a12acc9c6aea0868`, parent `b0053be96359aeb3f7a1b9a325fb0b8e3450b4c1`.
- The single same-scope repair attempt after the prior publication used exactly 1 provider request, 0 retries, and no visual/story repeat. It failed closed locally as `cloud.narrative_repair_position_contract_invalid`; no provider prose was retained.
- Root cause was a local observability gap: positional response-shape metrics were constructed only after top-level/array/count validation, so an early contract rejection persisted no array length, per-position counts, total, duration estimate, expected ranges, or failed predicate. This did not relax any quality, grounding, lineage, duration, or identity gate.
- TDD evidence: the new early-rejection regression was collection-clean RED before the fix; GREEN is the focused positional matrix (24 passed) and the related cloud/analyzer/script/manifest/narrative matrix (337 collected, 337 passed), plus Ruff, compileall, diff-check, and no line-ending churn.
- The corrected boundary persists only sanitized metadata for every early positional contract failure: container/key shape, rewrite count/types, per-position word counts, total words, duration estimate, expected ranges, slot-order identity, hard accepted ranges, and the exact failed predicate. It never stores provider text.
- Hard admission remains unchanged: trusted positional order/lineage, grounding, causal coherence, display derivation, 115-125 words, and 50-60 seconds remain fail-closed. No narration, MP4, TTS, or final QC artifact is claimed yet.
- Next resume point: inspect only the sanitized report from `2cd528e`, then issue a bounded same-model repair call as authorized. If strict admission succeeds, persist the narration through the normal entrypoint, render/QC silent video first, then proceed to configured TTS/alignment and voiced QC. Keep runtime data, caches, media, database files, and `ms_env.sh` untracked.


## 2026-08-22 - bounded repair shape report

- From published checkpoint `a3fa443151d4b84864dcab56186adb141e0e602a`, the next authorized same-scope repair used exactly 1 provider request and 0 retries, with no visual/story repeat.
- Sanitized response metrics: positional container `dict`, 8 rewrite items, all 8 items strings, total 140 words, estimated duration 60.87 seconds, and one top-level key. The stored metadata contains no provider prose.
- Local admission correctly failed closed with `cloud.narrative_repair_micro_compaction_unavailable`, predicate `micro_compaction_window`. The conservative local compactor accepts only 126-130 word candidates; it must not truncate or weaken the hard 115-125 word and 50-60 second gates. No source/test change is justified by this result.
- No narration, MP4, TTS, or final QC artifact is claimed. The next retry remains bounded and must be admitted only after positional shape, trusted lineage, grounding, causal order, display derivation, total words, and duration all pass.


## 2026-08-22 - bounded repair attempt with over-window candidate

- From published checkpoint `6e54fa2982e2754e4a4cd997f47922d8c675a313`, the next authorized same-scope repair used exactly 1 provider request and 0 retries, with no visual/story repeat.
- Sanitized response metrics: 8 rewrite strings, 135 total words, estimated duration 58.7 seconds, all items typed as strings, and no retained provider prose.
- Local admission correctly failed closed with `cloud.narrative_repair_micro_compaction_unavailable`, predicate `micro_compaction_window`. Duration is inside 50-60 seconds, but 135 words is outside both the conservative 126-130 compaction window and the hard 115-125 final bound. No truncation, padding, or gate relaxation is allowed.
- The next repair request remains the smallest path to a strict-valid candidate; visual/story caches remain reusable and no narration or media artifact is claimed yet.

## 2026-08-22 - Stage 1 safe synchronization and v6 prompt reconciliation

- Reconciliation baseline: latest published main is `2ae54ad03a66f257c3d690de5d881c52da7b412c` (fix: tighten positional repair word target; parent `de40f6d6a1885294d22c0bf70fd5802f06ccce5a`). Oracle HEAD and live GitHub main were verified identical at that SHA before this documentation checkpoint. No provider, TTS, render, or media work was performed in Stage 1.
- Prompt contract v6 is the published repair boundary: `vision-first-story-analyzer-v3-targeted-position-repair-v6` in `app/services/cloud_multimodal.py`. The versioned instruction now requires the provider to recount the complete vector and revise until it totals 115-125 words, never return a vector above 125 words, and treats the local micro-compactor strictly as a 126-130-word safety net. `tests/test_cloud_multimodal_mass_production.py` pins the v6 identity and the three new instruction clauses. No admission, lineage, grounding, duration, or identity gate changed.
- Provider calls for the three same-scope positional repair requests recorded on 2026-08-22: 3 total, retries: 0, with zero visual/story repetition (contract-shape diagnostics from `2cd528e`; 140-word/60.87-second rejection from `a3fa443`; 135-word/58.7-second rejection from `6e54fa2`).
- Latest durable rejected candidate: 8 rewrite strings, 135 total words, 58.7 seconds estimated duration; local admission failed closed with `cloud.narrative_repair_micro_compaction_unavailable`, predicate `micro_compaction_window`. No truncation, padding, or gate relaxation was applied.
- The visual/story cache for the 701 production panels remains reusable; the next repair retry needs no visual or story provider call.
- No accepted narration, TTS, MP4, or QC artifact exists at this checkpoint.
- Stage 1 local synchronization of the Windows worktree `B:\Project\manhwashorts-studio`: local `main` fast-forwarded `b6f72cd` -> `2ae54ad` with `git merge --ff-only origin/main` after proving `b6f72cd` is a strict ancestor and that no incoming tracked path collides with untracked input. Recoverable archive ref `archive/stage1-20260822-pre-sync` = `b6f72cd`; branch `codex/final-production-silent-acceptance` remains pushed at `b6f72cd`. `final_test/` and `The Novel’s Extra/` remain untracked and unstaged. No reset, clean, forced checkout, or force-push was used.
- Validation on Oracle for this checkpoint: focused positional/repair/micro-compaction selection 64 passed with 62 deselected; related cloud/analyzer/script/manifest/narrative matrix 308 passed with 7 pre-existing Pillow deprecation warnings; Ruff, compileall, `git diff --check`, line-ending/no-churn, and secret-shape scans are clean.
- Next boundary (Stage 2, not started): one bounded same-model v6 repair retry from the published SHA using only the cached 701-panel visual/story evidence, with strict admission before any persistence, render, TTS, or QC step.

## 2026-08-22 - Stage 2 strict-valid narration accepted and persisted

- Source SHA at start: `247f2686442596768a98b03354f9dd98f857f323`. Four RED->GREEN fixes were published before acceptance, each with focused regressions and green matrices:
  - `d69fba4ec8512bbafffbf2ba98e22d79cd97c3d9` - `run_job` now repairs a structurally usable but contract-failing cached narration through the public `run_narration_repair_candidate` boundary before admission, so persistence can never receive a contract-failing narration. RED: `test_run_job_repairs_structurally_usable_dialogue_copy_narration`.
  - `ad094b95aaa1434ecf9c22e1ffdf675b8ee8dc99` - repair prompt v7 caps every rewrite at 15 words.
  - `f2b52ea46912747130c372ec42076b3ce4f0e54f` - repair prompt v8 centers drafting at exactly 14-15 words per position, aiming for 15.
  - `e41698976a66be5cc6c891d5939596e454d39587` - the targeted position repair validates its full-scope vector in one request; the previous per-chunk validation rejected every in-window vector as `field=claim_evidence` because claims referenced panels outside the chunk. RED: `test_targeted_position_repair_validates_full_scope_in_one_request`.
  - `1ded65168fad19eadb5fd9a3a516380c5604cabc` - scope reconciliation compares repaired evidence against the trusted claim-evidence closure instead of the durable candidate's stale passage evidence. RED: `test_position_repair_admits_trusted_evidence_closure_scope`.
- Candidate pool: a bounded driver (`data/_stage2_narration_pool/pool_driver.py`, ignored) drove the canonical `run_narration_repair_candidate` boundary with unique request IDs and sanitized JSONL ledgers. Rounds 1-6 spent 98 narration_repair requests over 48 attempts and failed closed on honest predicates (word window 92-145, grounding, scope). Round 7 attempt 1 was accepted on the first request: 122 words, 53.04 seconds, 5 passages, 701 observations, micro-compaction not required, no provider prose retained. Diagnostic probes added 3 further requests. Zero vision/story provider calls were made; the 701-panel visual/story cache was reused throughout.
- Persistence through the normal entrypoint (`scripts/run_cloud_multimodal_batch.py --project-id 22876a6014a842f48bfca58c10a592b5 --state-dir data/p0-aws-acceptance/cloud-jobs`) reused the canonical repair-result cache with 0 provider calls and persisted: StoryAnalysis `db03ed6687dc48e4be83ed238e618a88` (canonical_panel_count 701, processed_panel_count 701, model_identity_hash `aa2fc9cdf79e7bf625bd028dd9164f0b652ed9e980aae77978f3f9b8121b418b`, narrative identity sharp_friend_v1 v1.0.0 sha256 `134b544c9e2f74ca0b8c64ff55a27c831e76f77a08f26fc2a463112cb0678b3e`), 701 PanelRegion rows with 701 distinct panel_ids, and ScriptVersion `2df45d26729f4945bc3fbc35f886aa3f` v3 (122 words, 53.04s, 5 sections, 25 citations, generator vision_evidence_v3). Audit `script.generate` at 2026-08-22T06:47:36Z. Job state READY_TO_RENDER with approval_required and voice_timing_required intact.
- Post-persistence proofs on the stored narration: `_narration_contract_failures` is empty, `contains_source_dialogue_copy` is false, display derivation is exact (123 punctuation-free uppercase words match the voice script), and repair lineage is recorded (slot_order_hash `ad8b62627f7595b1cc5e7ae5c01204f2adfb04c3386811b7cfb3733e9c8cfc1d`, passage_lineage_hash `91f5189105bd0e02ec3ec8b1290a3e6cc836542c37ac1395d071d0e165cad5c4`).
- No TTS, subtitles, audio, or video was generated. The next stage is voice timing/TTS after silent-render QC, through the existing approval gates.

## 2026-08-22 - Resumed production audit and review-cap correction at `a0fec40ce2b7e262b12f3824f5c05330bbd932b1`

- Reconciliation audit verified Oracle `main`, live GitHub `main`, and the Windows transport clone at `a0fec40ce2b7e262b12f3824f5c05330bbd932b1`; Oracle has no tracked dirty files. Protected runtime `data` and `ms_env.sh` remain untracked and were not read into output. The local Windows repository remains separate and untouched: `app/services/pipeline.py` is modified, while `final_test/` (40 files) and `The Novel’s Extra/` remain protected untracked inputs.
- Stage 2 is verified only through normal persistence: StoryAnalysis `db03ed6687dc48e4be83ed238e618a88`, ScriptVersion `2df45d26729f4945bc3fbc35f886aa3f`, 701/701 panel lineage, 122 words, 53.04 seconds, five passages, strict grounding and anti-dialogue-copy checks passing, and SQLite integrity `ok`. No provider/TTS call was made during this resumed audit.
- Stage 3 is not complete: no current MP4, valid audio bytes, subtitle bundle, FFprobe, blackdetect, contact sheet, or QC PASS exists. The database has five timing rows for the latest script, but their referenced audio files are absent; these rows are incomplete/stale evidence and must not be treated as successful TTS. The current render job is only `review-preview-queued`; job state remains `READY_TO_RENDER`.
- The review-only upscale contract had drifted to `max_scale=2.50`/version `1.2.0`. A RED regression reproduced the mismatch; the scoped GREEN correction sets `review_silent_source_upscale_v1` to deterministic Lanczos cap `1.50`/version `1.3.0`, with the existing low-resolution warning and hard balloon/protected/lineage gates unchanged. Focused upscale tests: 15 passed; affected profile/visual/integration matrix: 99 passed; Ruff, compileall, and `git diff --check`: clean.
- Oracle’s current storage contains only 19 source files matching current original checksums; the protected local `final_test/` source set must be copied into an ignored Oracle runtime location before a truthful render can start. No source images, media, database, cache, provider payload, or credential is eligible for Git.
- Next safe action: publish this source/test/docs checkpoint, securely copy only the 40-file `final_test/` input into protected Oracle runtime, run the review-only silent path through the normal service boundary, inspect actual video/QC, then proceed to configured TTS only after silent QC passes. Do not repeat the valid 701-panel visual/story stages.

## 2026-08-22 - Review-only persisted panel-crop fallback checkpoint

- The review-only source resolver now has an explicit `persisted_panel_crop_v1` materialization path for panels whose immutable full original bytes are unavailable but whose stored asset bytes are an exact persisted crop. It is not a source-lineage rewrite and is never enabled for normal/final/publish rendering.
- Admission requires SHA-256 equality with the stored `SourceAsset.checksum`, decodable RGB pixels, and decoded dimensions exactly equal to the persisted region width and height. The fallback localizes coordinates to `(0, 0, width, height)` while preserving original `source_asset_checksum`, `source_order`, region identity, and evidence lineage. The manifest records the materialization kind, original/persisted dimensions, prepared checksum, policy/version, and deterministic Lanczos transform.
- Candidate construction and render-time materialization use the same typed manifest and revalidate bytes, bounds, evidence, telemetry, and prepared checksum; malformed/tampered bytes or geometry fail closed. Review-only upscaling remains opt-in, `publish_allowed=false`, capped at 1.50x with the existing low-resolution warning, balloon/protected/blank/lineage gates unchanged.
- RED/GREEN evidence: two exact-crop checksum/geometry regressions were RED before the resolver, then the focused upscale/materialization matrix passed 31 tests; Ruff, compileall, and `git diff --check` passed. Parent checkpoint: `6b582568c097a533c4d8f0d02617fef696b656b1`.
- Stage 3 is still unproven: no MP4, audio, subtitle, FFprobe, blackdetect, contact sheet, or QC PASS is claimed. The next action is a normal review-only service run using protected input/runtime storage, with no visual/story reanalysis.

## 2026-08-22 - Review-repair crop fallback forwarding checkpoint

- Runtime diagnosis after the published `persisted_panel_crop_v1` resolver found a narrow forwarding omission: `_repair_review_narrative` validated the explicit review upscale policy but did not pass `allow_persisted_panel_crop_fallback`, so the fallback loader re-raised `review.upscale_source_missing` before candidate construction. This was a local state/argument defect; no provider or TTS request was consumed.
- TDD evidence: the body-level regression was collection-clean and RED with `KeyError: allow_persisted_panel_crop_fallback`; adding the policy-derived forwarding flag made the focused cloud/review/lineage/reference matrix GREEN: 212 passed (130 cloud, 17 upscale, 14 panel-lineage, 6 reference-profile, 45 reference-profile integration). Ruff, compileall, and `git diff --check` are required before publication.
- The fix is review-only and policy-gated (`policy is not None`); normal/final/publish paths retain the default false behavior. The next action after publication is one normal service resume using existing visual/story/narration state, with no visual/story reanalysis; preserve exact lineage and stop on the next strict blocker.

## 2026-08-22 - Legacy review-manifest normalization checkpoint

- The first real review resume reached the regular render boundary with zero provider requests and failed closed on `review.upscale_manifest_invalid`: existing accepted timeline entries encoded `source_materialization: null`. This is legacy metadata for the original-source path, not permission to reinterpret a crop or bypass lineage.
- RED/GREEN evidence: `test_legacy_null_or_missing_materialization_defaults_to_original` failed on the published validator, then passed after a narrow normalizer/validator update. The focused review/cloud/lineage/reference matrix is 213 passed; Ruff, compileall, `git diff --check`, and secret-shape scan are clean.
- Only missing/null materialization is normalized to `original_source_v1`; explicit unknown values remain rejected. The render retry must use the persisted timeline and exact DB/storage overrides, with no visual/story/provider repeat; the previous render attempt consumed zero provider/TTS requests and left the job in `NEEDS_REVIEW` with `review.preview_failed`.

## 2026-08-22 - Conservative balloon geometry admission correction

- Framing admission now consumes the union of all persisted balloon geometry representations (normalized bbox plus polygon-derived envelope). This closes a fail-open representation mismatch where the old bbox-only calculation returned zero overlap for a crop intersecting the polygon. It preserves canonical evidence/hash identity and leaves `candidate_is_feasible`'s zero-overlap requirement unchanged.
- The exact regression is source order 225, region `a436184a3ee14d04a055dacb0a005daf`; the focused framing/review/upscale matrix is 75 passed and 1 existing skip. A pre-fix silent artifact is rejected runtime evidence and must be regenerated before the TTS DAG edge.

## 2026-08-22 - Review sidecar serialization boundary

- The post-encode review DAG now treats sidecar serialization as a typed boundary: `_reference_review_sidecar` passes values through `_reference_json_safe` before `json.dumps(..., allow_nan=False)`. This covers dataclasses, mappings, paths, tuples, and finite scalars while rejecting unknown/non-finite values as `visual.panel_lineage_unavailable`.
- The RED regression used an in-memory dataclass telemetry/manifest value; the focused GREEN silent-review/upscale matrix is `52 passed`. The fix is source/test-only and does not change the persisted panel/mask/evidence contract or include full mask grids in the compact sidecar.
- The next DAG edge is a cached normal review rerun. Acceptance still requires sidecar, FFprobe, blackdetect, strict QC, contact-sheet/frame inspection, and `REVIEW_PREVIEW_READY` before TTS.

## 2026-08-22 - Review-only provisional duration contract `4613214`

- The review DAG now has one local provisional-duration boundary: `_silent_review_media_duration(scenes)` sums the exact rounded `SceneInput.duration` values used by `render.render_video` and `join_scene_clips`. It is intentionally separate from authoritative voice timing and cannot alter default/voiced render behavior.
- The prior replay built 41 scenes but used absolute `scene.end_time` for subtitle groups; accumulated sub-millisecond drift triggered `subtitle.timing_out_of_bounds`. The 30-scene `1.0004s` regression covers this boundary. Review failure serialization also retains `subtitle.*` codes for resumable diagnosis.
- Published source checkpoint: parent `c1acd37`, child `46132146979ca66021b5674acc6ea954bd0c462b`; affected offline matrix `197 passed, 20 warnings`, Ruff/compileall/diff-check/secret scan clean. No provider/TTS request or accepted MP4 resulted from this slice.

## 2026-08-22 - Profile-aware blank-space admission `ff2484b`

- `framing_analysis.candidate_is_feasible` now accepts the resolved profile's `blank_target_fraction` and returns stable `visual.blank_infeasible` telemetry when the edge-connected blank fraction exceeds it. The parameter is optional only for legacy direct callers; every profile-aware production caller supplies it.
- Propagation covers editorial planner ROI phases, visual feasible-ledger construction, normal reference preparation, exact persisted-ROI silent preparation, and `write_review_preview_bundle`. Review-only aggressive crop may relax protected coverage/resolution as explicitly designed, never blank-space admission.
- The observed 34/41 sidecar shots above `0.03` exposed the previous mismatch; final bundle QC had been stricter than candidate admission. The regression and affected offline matrix are green; no provider/TTS or accepted MP4 resulted from the correction.

## 2026-08-22 - Visual-repair diagnostic/observability boundary

- The feasible-ledger reduce is authoritative after framing: 701 cached panels reduced to 277 eligible candidates, 1,734 ROI evaluations, 71 feasible ROIs, and 36 panel identities. The strict reduction preserves blank, balloon, protected, resolution, and lineage rejection codes.
- Visual narrative repair remains a bounded provider stage over that ledger. Its final safe error now carries only contract version, attempt count, failure code, feasible panel/ROI counts, missing-section count, and ledger hash, allowing the job state to resume without provider prose or payload retention.
- The latest run used 3 visual-repair requests and no TTS; no accepted media artifact exists. Cached visual/story stages are immutable inputs to the next repair attempt, and no cache invalidation is permitted unless their identities change.

## 2026-08-22 - Visual-repair cache validation boundary

- `CloudStageRunner.run_visual_narrative_repair` now treats a cache hit as untrusted persisted state: deserialization, visual-evidence identity, feasible-ledger lineage, and section-coverage validation occur inside a narrow typed boundary. Invalid entries become cache misses and enter the existing bounded repair loop; valid matching entries remain reusable.
- This closes the local state discrepancy observed after the cached replay: `visual.narrative_repair_ungrounded` had been emitted before the repair loop because cache validation was outside it. No grounding, duration, visual, lineage, or provider-response gate was relaxed.
- Regression and focused matrix are green (`146 passed`); the next request may reuse cached visual/story inputs but must not repeat those stages.
## 2026-08-23 - Panel-admission funnel contract (unpublished)

The visual DAG now has an explicit local admission boundary before provider dispatch:

`raw_input_images -> ingest_outputs -> candidate_regions -> canonical_regions -> admitted_vision_panels -> visual evidence`.

`admit_panel_inputs()` owns the deterministic decision ledger. Each transition stores input/output count, monotonic elapsed seconds, and a stable reason code. Every candidate decision stores source asset ID/checksum, original bounds, source order, candidate panel IDs, detector/contract version, and reproducible metrics. The ledger also stores the complete candidate coverage manifest, reduction percentages, reason-code set, and `ledger_hash`.

Admission invariants:

- `verified_gutter`/transition regions and explicit ingest decisions proving no story evidence may be rejected locally; unresolved material, protected/dialogue-bearing content, and ambiguous blank/title/cover classifications become `NEEDS_REVIEW`, never silent loss.
- Exact duplicate identity requires source checksum, payload checksum, dimensions, and bounds. Near-duplicate identity requires the same source checksum/dimensions and at least 98% smaller-region overlap. Adjacent true panels have no qualifying overlap and remain separate.
- An over-segment merge is admitted only when the caller supplies an auditable merged payload, all parts share source lineage, bounds are contiguous with no gap/overlap, and protected-region retention is explicitly verified. Otherwise the boundary remains review-blocked.
- `panel_sink` is called only with the resulting admitted panels. The segmentation state persists the funnel ledger; downstream provider code cannot observe rejected/ambiguous rows as vision inputs.

The strict stream session separately requires every submitted panel to reach accepted or terminal missing state, persists sanitized failure counts/IDs, and rolls adaptive workers back at the first unstable wave. This is a correctness boundary, not a gate relaxation.

Current limitation: `prepare_project_panels` still completes the existing reconciliation/coverage map before its final funnel pass, so normal cold-path first-dispatch overlap is not yet proven by this slice. The next subset must use a fresh namespace and emit the funnel table, especially the prepared=703 versus filtered=701 reason ledger, before any story/narration/TTS/render stage.

## 2026-08-23 - Incremental panel admission dispatch

The panel materialization loop now maintains a prefix admission ledger and invokes the single stream sink immediately for each panel that is locally admitted. The global ledger still runs after all canonical regions are materialized and is the authoritative count/coverage/reason record. This removes the local “encode all panels before first sink” barrier and preserves single-writer/backpressure behavior, but it does not yet make `reconcile_sources` or `build_complete_coverage_map` source-level streaming; that remaining limitation is explicit.


## 2026-08-23 - Stream cache identity and filtered-subset resume

The stream cache has two distinct identities: the immutable per-panel identity used for safe reuse, and the derived batch-position key used only for current-run chunk bookkeeping. Resume validates stream contract version, model/prompt scope, ordered panel membership, source asset/checksum, payload identity, and canonical evidence identity before accepting a seeded row. It no longer requires the previous batch-position key to be equal, so equivalent filtered-subset or batch reshaping can reuse valid rows without provider calls. No model/prompt/lineage/version scope is broadened, and terminal coverage remains fail-closed.

The regression inserts an earlier panel on resume and proves the previously cached panel is reused while only the new panel calls the provider. This is a local cache correctness boundary; it does not claim that upstream source reconciliation is fully streaming or that the measured subset is a cold 40/40 proof.

## 2026-08-23 - Warm subset checkpoint

The v4 cache-only run restored the accepted v3 rows after sorting checkpoint completion records by immutable source order. The admission ledger was raw=37, ingest=35, candidate=37, canonical=37, admitted=37, with no rejection, dedupe, merge, or review rows; stream terminal accounting was 37/37, one writer, zero provider requests, zero retries, and 14.703s elapsed. A forbidden-observe guard made cache misses fail before any external call. This validates warm reuse and deterministic ordering, not cold provider completeness or downstream production readiness.

## 2026-08-23 - Source-level stream callback and admission audit

The source preparation boundary now exposes an optional `on_reconciled`
callback. After a source group returns `RECONCILED`, streaming preparation
materializes that group's canonical regions from one local coverage map, runs
the local `panel-admission-v1` funnel, and sends only admitted payloads to the
existing bounded queue/single writer while later groups continue. The default
non-stream path retains reconciliation-first ordering and legacy error codes.
This is an overlap correction, not a quality-gate relaxation: the final global
admission and complete-coverage checks still run before `prepare_project_panels`
returns, and a later failure aborts the provisional stream.

The funnel is the canonical pre-vision chain:
`raw_input_images -> ingest_outputs -> candidate_regions -> canonical_regions -> admitted_vision_panels`.
It records per-transition counts, elapsed time, reason code, source checksum,
bounds, candidate IDs, detector/version, metrics, coverage manifest, and ledger
hash. Verified gutters, explicit no-story blank/title/cover, and auditable
exact/near duplicates can be rejected; protected/dialogue-bearing, unresolved,
and ambiguous regions remain `NEEDS_REVIEW`. Provider output is not used for
basic local filtering, and no story-panel bytes are silently dropped.

Source/test GREEN evidence is 170 cloud tests plus 47 strip/segmentation tests;
Ruff, compileall, and diff-check pass. The unchanged 13 pipeline fixture
failures remain a named baseline exception. A clean v7 normal-entrypoint probe
was stopped after 4m13s without a first visual dispatch or visual checkpoint;
the preserved namespace contains segmentation-review reports only and no
downstream artifact.

The read-only v6 database explains only the shape of the prepared/filter
observation: current analysis `54fc779ba2334d55a46f815fa56ccd6c` has 701
distinct canonical rows over source-order domain 0..702 with gaps 303 and 306,
while two older 280-row analyses are stale. This is not yet a semantic funnel
ledger for the two gaps. A fresh 40-80 panel run must produce the complete
funnel table and reason codes before any story/narration/TTS/render stage; v6
and v7 runtime data remain read-only.

## 2026-08-23 - Callback subset blocker and geometry diagnosis

The fresh callback subset reached the intended overlap boundary but did not
reach terminal visual coverage. The 40-region callback run emitted two visual
chunks before a later source group failed `segmentation.ambiguous_boundary`.
The separate local-only 41-region run emitted one visual chunk and measured
first visual dispatch at 9.876s, before preparation returned at 15.745s, but
also failed the strict segmentation boundary. These are control-flow
diagnostics, not accepted visual subsets.

The failure was isolated offline using only a persisted sanitized assessment
and source lineage. For source family `129__010`, provider accepted positions
were 2426, 3866, and 5229; the local candidate/ideal contract required
1600, 3200, 4800, 6400, 8000, 9600, 11200, 12800, and 14400 within its
configured proximity and valid-partition rules. The replay returned
`NEEDS_REVIEW/segmentation.ambiguous_boundary`, so the current result is a
correct fail-closed geometry decision, not a stream or panel-admission bug.
Do not loosen proximity, fabricate a cut, or use a review override in the
production proof. The next implementation slice must add a focused regression
for the exact boundary contract before any new real subset request.

## 2026-08-23 - Versioned nonuniform boundary selection

The source boundary had a local false rejection: the old greedy reducer
required a cut near each equal-height ideal before checking whether the
provider-confirmed candidates formed a safe complete partition. The v2
reducer now searches only trusted accepted candidates, ranks target-count and
geometry deviation deterministically, and enforces hard minimum/maximum span,
coverage, uniqueness, and required-cut constraints. The maximum is two target
frame-heights; it is not a quality relaxation. A sparse candidate set that
leaves an oversized terminal span still returns
`segmentation.ambiguous_boundary`. The versioned segmentation identity forces
old derived reconciliation metadata to be invalidated rather than mixed.

The RED fixture is a nonuniform 900x3565 source with a safe cut at y=731,
outside the old ideal-radius window; it is GREEN with the v2 selector. A
negative 8000px sparse-candidate fixture remains blocked. The pre-fix short
subset is not proof: 41 target regions, 38 assets, 19 provisional submissions,
37 requests, first dispatch 14.653s, preparation 227.594s, and no final funnel
because a later source group remained ambiguous. Publish the source/test fix,
then run a fresh namespace and require a complete funnel plus terminal N/N.
## 2026-08-23 — Blocked admission ledger and v4 diagnosis

The streaming preparation boundary now emits a deterministic local admission
ledger even when source reconciliation fails after provisional callbacks.
`panel_admission_failure_ledger(...)` reuses the candidate-region coverage
manifest, records all funnel counts/transitions/reason codes and the ledger
hash, sets `status=BLOCKED`, and forces terminal admitted count to zero.
`prepare_project_panels` attaches it as sanitized error metadata. This is an
observability and fail-closed accounting correction; provider validation,
protected-region, lineage, and terminal N/N gates are unchanged.

The v4 diagnostic run at
`/data/data/p0-aws-acceptance/video1-stream-source-callback-short-v4` selected
40 canonical regions from 37 assets/23 groups, submitted 33 provisional
panels, made 27 requests, dispatched first vision at 30.393s, spent 170.760s
in preparation, and ended after 594.381s with
`segmentation.ambiguous_boundary`. Its pre-fix summary had no admission
ledger, so no funnel counts are inferred from it. Persisted reports identify
zero-confidence geometry-only candidates (`130__005`, `130__019`) and
artwork-connected cuts (`131__017`, `131__019`). The next fresh subset must
produce a complete funnel table before story/narration or media stages.

The v5 run also exercised the non-exception path: `reconcile_sources` returned
`NEEDS_REVIEW` with `segmentation.ambiguous_boundary` after 36 provisional
submissions, but the pre-correction status branch discarded the funnel. The
branch now attaches the same `BLOCKED` ledger as the exception path. V5 used
41 canonical regions / 37 assets / 24 groups, 13 requests, first dispatch
32.515s, preparation 170.591s, total 581.553s, and remains rejected
diagnostic evidence; its empty old funnel is not semantically reconstructed.

## 2026-08-24 — visual admission/recovery invariant

The visual stage now has a panel-local recovery boundary: a valid row is
persisted immediately, malformed or unknown rows are retried by panel ID, and
whole-chunk reduction remains reserved for transport-level failure. A source
with no safe cut is a reconciled full-height scene, preserving source lineage
and requiring viewport/pan downstream. Unknown balloon geometry may become
`conservative_full_panel_v1` only after bounded targeted retry; the typed
record is still `unknown`, and the renderer/planner require an explicit review
flag plus the exact full-source ROI. All ordinary default and publication
paths remain fail-closed. The current phase5b runtime has no encrypted provider
profile, so its resume consumed zero provider requests and produced no media.

## 2026-08-24 — boundary response repair invariant

The local operator path has now verified the configured encrypted BYOK profile
with one explicit capability probe. A review resume persisted 64 checkpoint
rows (51 unique panel IDs) before a source-boundary response failed closed as
`segmentation.provider_coordinate_invalid`; the sanitized job review queue
also recorded three provider-request failures and one protected-boundary
entry. `_assess_with_retry` now repairs only provider response shape errors
with one field-level sanitized hint (`boundaries[].y`, geometry collection, or
required boundary list), without accepting out-of-candidate coordinates or
changing lineage/protected/coverage gates. The operator no longer exposes the
credential key hint in setup, connection, or status output. No story/narration,
TTS, render, or QC artifact is claimed yet.

The next real resume exposed `cloud.panel_lineage_invalid` after 108
checkpoint rows / 61 unique IDs. The prepared-panel preflight was 60/60 with
unique `source_order` and `prepared_order`; source-group completion order was
simply different from canonical prepared order. Stream termination now checks
exact submitted ID-set equality (while preserving duplicate/missing/foreign
rejection) and delegates deterministic ordering to the existing merge step.
This is a generic overlap-control fix, not a gate relaxation.

## 2026-08-24 — bounded narration repair vectors

The narration repair boundary now distinguishes grounded context size from
the usual eight-position drafting shape. The prior builder/canonicalizer
required eight positions even when editorial selection produced a valid
five-passage, three-claim context; this caused a local
`cloud.narrative_repair_position_selection_invalid` before provider repair.
The canonical rule is now 4--8 trusted positions. If the story exposes fewer
than eight trusted claims, the registry must retain every available claim; if
more are available, the existing low-priority selection still caps the vector
at eight. Provider output remains text-only and positional; local code owns
all passage/claim/evidence lineage.

The changed repair prompt is versioned
`vision-first-story-analyzer-v3-targeted-position-repair-v9`; smaller vectors
receive total-budget guidance without inventing positions or treating the
eight-position per-slot target as a hard cap. Final shape, lineage, evidence
closure, causal order, anti-dialogue-copy, 115--125 word, and 50--60 second
validators remain fail-closed. The current phase5b visual/story stages are
reused; this fix consumed no provider/TTS/render request and does not claim an
artifact.

## 2026-08-24 — stable review-render error propagation

Review rendering has a nested failure boundary: `build_render_request()` or
the renderer can raise a local exception whose stable code is present in its
message even when the exception type has no `.code` field. Previously the
wrapper persisted `review.preview_failed`, preventing diagnosis and causing a
repeat of the same expensive resume. The canonical boundary now extracts only
known stable code namespaces (`render`, `ffmpeg`, `subtitle`, `visual`, and
the other existing review-stage namespaces) and uses the generic code only
when no stable code exists. This is diagnostic preservation, not a gate
relaxation. The focused cloud/review tests passed 201/201; no provider/TTS or
media work was performed. The next durable resume must use the preserved code
to fix the smallest render/QC boundary.
