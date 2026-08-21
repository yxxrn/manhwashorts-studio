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
