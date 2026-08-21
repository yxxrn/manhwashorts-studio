# FOLLOW-UP GREEN CHECKPOINT - 2026-08-20

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
