# LATEST INTERRUPTION-SAFE HANDOFF - 2026-08-21

## Narration anti-copy repair checkpoint

Rollback parent: `a2d9e85eb5caa05abf792294b7265eed0300c67b`.

The offline durable-candidate replay proved the current blocker is local and
specific: passage `p2` (claims
`b1__sub0__claim2/b1__sub0__claim3/b1__sub0__claim4`) shares one normalized
four-word dialogue sequence with panel
`region-a1ceb6aece5c808c9bee`. The candidate still has 701 observations,
five passages, 118 words, and 51.3 seconds; no source prose is stored in the
diagnostic evidence.

RED covered paraphrase acceptance, quote/name-variant rejection, repair
prompt shape, and the production validator seam. GREEN is 5/5 focused and
269/269 affected matrix tests. The old preview relaxation was removed from
both narration and visual-review repair call sites; the repair prompt is
version v4, the repair contract is v5, and the result cache is v6. Strict
grounding, causal order, complete 701-panel lineage, duration, and identity
checks remain unchanged.

Next resume command, only after publishing this checkpoint, is the normal
cached targeted repair boundary with `max_repair_requests=1` and no visual or
story requests. If admitted, persist the exact analysis, then proceed to the
local silent render/QC boundary. Voice remains after silent QC; no provider or
TTS call, narration persistence, MP4, or QC completion is proven yet.

## Anti-copy repair-trigger correction

The first post-publication attempt made zero requests and returned
`cloud.narrative_repair_not_needed` because the repair trigger checked only
word/duration predicates. The strict validator still rejects the persisted
candidate's normalized four-word dialogue overlap. A RED regression now
requires the shared detector to emit
`cloud.narrative_source_dialogue_copy`; the GREEN correction keeps the final
validator, grounding, lineage, and duration gates unchanged. No provider call
has been made after this trigger correction. Resume with one bounded cached
repair request only after this checkpoint is published.

## Trusted passage-evidence reconstruction

The first retry after the anti-copy trigger correction made zero provider
requests and failed locally with `cloud.narrative_repair_slot_lineage_invalid`.
The candidate p2 passage had a nonempty but incomplete evidence list; the
trusted story claim refs were valid and supplied the missing panel. GREEN now
reconstructs the ordered union from those trusted refs and still rejects
foreign/unrelated/duplicate refs. The affected matrix is 271/271; publish
this checkpoint before the one allowed cached repair request.

## DB persistence round-trip checkpoint

Rollback parent: `f1f08bc2e9cd067b8703ba1d28298012cf27b74f`.

The persistence boundary is now exact-analysis keyed. The old `latest_analysis`
selection could choose a stale 280-panel row after the new row was flushed;
`persist_cloud_chapter` now passes `analysis_id=row.id` and rejects a row from
another project. Its preview-only `allow_dialogue_copy=True` exception was
also removed so the initial write and later reload use the same strict
analyzer contract. No quality, grounding, lineage, duration, visual, audio,
or QC gate was relaxed.

Offline TDD proof: the intended stale-row RED failed with
`narrative_profile_mismatch`; GREEN is 116/116 cloud tests, including
701-panel semantic write/read round-trip, preserved original sparse
`source_order`, contiguous persisted `source_index` 0..700, foreign-analysis
rejection, and rollback after a post-flush failure. The related analyzer/script
matrix is 110/110; Ruff, compileall, diff-check, and no-churn pass.

Real zero-budget replay (same normal entrypoint, no provider/TTS calls) ended
honestly at `NEEDS_REVIEW` with `cloud.narrative_not_grounded`; usage was
`request_count=0` and all per-stage counters were zero. The strict predicate
is `script passage copies source dialogue`, so the candidate is genuinely not
admissible. SQLite integrity is `ok`; the protected DB still has only its
pre-existing StoryAnalysis rows, with the newest two at 280 regions, because
the failed candidate transaction did not commit. No real 701-row persistence,
narration artifact, MP4, TTS, or QC is claimed. Next resume must repair or
replace the blocked candidate under the existing strict contract, then rerun
normal persistence with zero visual/story repetition.

## Post-publication repair outcome and output hardening

Published identity checkpoint: `87aed29e1600484dec07e8e1aadbdcfdeae7573e`.
The metadata-only scan found four legacy candidate records, all missing
`identity_metadata`; migration was not safe and no hash was rewritten. The
first normal command made zero requests because the default DB did not contain
the project. A process-local `MS_DATABASE_URL` override selected the protected
sample DB without modifying it.

The one authorized same-model attempt consumed exactly two requests
(`narration=1`, `narration_repair=1`, `other=0`) with no visual/story repeat,
then ended `NEEDS_REVIEW` as `cloud.narrative_not_grounded`. Sanitized result
metrics are 118 words, 51.3 seconds, 5 passages, 8 claims, and 701 ordered
observations. Display, duration-contract, and all local passage/claim/panel
lineage checks passed, but job admission still disagrees; no narration, MP4,
TTS, or QC is valid. No further provider call is allowed.

The runner printed full stage payloads to stdout, violating its stated safe
output boundary. A focused RED regression now has a GREEN fix: `_safe_job_summary`
prints only job ID, state, stable error/review codes, and request counts. This
follow-up source/test/docs change is the current publication checkpoint; do not
resume cloud work until the local admission discrepancy is diagnosed.

## Metadata-only narration identity checkpoint

Published rollback parent: `5eaf91762f45ec4111d88e21ac458618bb86f42a`.
The source/test slice adds canonical `narration-repair-identity-v1` metadata
for the ordered 701-panel visual lineage, model/prompt identity, story
beats/claims/causal chain, editorial selection, trusted slot order and
claim/evidence refs, and candidate dependencies. Only derived
`prepared_order` is representation-insensitive; trusted ordered panel IDs
remain exact. Semantic changes fail closed as
`cloud.narrative_repair_identity_mismatch` with sanitized counts, field,
comparison hash, and reason.

The old durable candidate visual identity is
`73c224732858ead17bdee4003cfc8824a7f1470e7e0a238f8baa5d80fd0b9579`; current
story context is
`a9a43faf0a198b1bf3a995858fba39bea65cb27be3152b7019e2dba8a9b24b9f`.
The legacy candidate has no `identity_metadata`, so runtime loading must
record `legacy_identity_metadata_missing` and reject it, not relabel or mix
the hashes. Exact-equivalent migration and idempotent migration records are
covered by tests; warm loader validation now uses the migrated record.

The request budget is split into one normal narration request and one
targeted-repair request, independently, with other stages excluded from both;
legacy global `max_requests` remains compatible. No provider request was made
in this checkpoint. RED was 13 intended collection-clean failures; GREEN is
14 focused identity/budget tests, 111 cloud tests, and 83 related manifest/
analyzer/script/vision tests. Ruff, compileall, diff-check, and secret scan
passed. The 13 pipeline failures reproduce identically on the clean parent and
current tree at the existing pre-vision fixture prerequisite and are not a
full-suite/render GREEN claim.

After publication, use the normal cached runner with
`--max-attempts 1 --max-narration-requests 1 --max-repair-requests 1`, same
model, no visual/story repeat. Do not issue a request until publication;
runtime media, DB/WAL, caches, `data`, `ms_env.sh`, and credentials remain
outside Git. Narration, MP4, TTS, and QC are still unproven.

## Fresh retry blocker

From published parent `813ec6e342584b38e4a5e379a25391406df5440e`, the isolated
repair harness reused the exact durable candidate and 701-panel visual/story
identities and made one request to `grok-4.3`. It failed closed with
`cloud.narrative_repair_slot_contract_invalid` again. No provider payload or
prose was retained, no result cache was admitted, and automatic retries are
stopped. The sanitized runtime report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-immutable-slot-schema-retry.json`.
No narration, MP4, voice, or QC artifact is available; a new response-schema
diagnosis is required before another separately authorized request.

## Latest slot-schema repair checkpoint

Published code correction is
`25f1d6598643b0217504520d3e28f58994b41688` (parent
`945770e75fc2483fc854fc0f7bf411993ee90f9a`). The provider prompt now gives
the exact slot row shape and local code still owns all claim/evidence lineage.
The one bounded real repair request issued one request and ended with the
sanitized code `cloud.narrative_repair_slot_contract_invalid`; raw response
content was not retained, and no second request was made. The new regression
plus focused matrix is 74 passed (67 cloud, 7 manifest) with five existing
Pillow warnings; static and secret gates are clean. No narration, MP4, voice,
or QC artifact exists. A future retry requires an explicitly bounded budget
after this published schema correction.

## Next atomic checkpoint: immutable repair slots

Published checkpoint is `170ae52f9e9a821d37a8ba025d44f09b0ad44187`, with
rollback parent `161e26807050bda6f3e764811e0a1f92e93ec6b2`. The focused
source/test work passed collection-clean RED and GREEN
verification. The repair registry is locally derived from the durable 160
word / 64.35 second candidate and compact trusted story identities; provider
responses cannot supply or rewrite claim/evidence IDs. A valid response must
cover every exact local slot in canonical order, with only revised prose and
explicit retained/dropped slots. The registry hash is part of the atomic
repair-result cache key and warm reuse must make zero provider calls.

The 73-test matrix is green (66 cloud plus 7 prepared-manifest), with five
existing Pillow warnings; Ruff, compileall, diff/no-churn, and secret scan are
clean. No post-correction real repair request has been made, and no narration,
MP4, voice, or QC artifact exists. Resume only after publication with the
same pinned model and matching 701-panel visual/story identities, at most one
repair request, and sanitized code/count diagnostics.

Oracle `/home/ubuntu/manhwashorts` is the authority. The published compact
narration-repair checkpoint is `170ae52f9e9a821d37a8ba025d44f09b0ad44187`,
parent `161e26807050bda6f3e764811e0a1f92e93ec6b2`.
Only `app/services/cloud_multimodal.py` and
`tests/test_cloud_multimodal_mass_production.py` are in-scope authored files;
the `data` symlink and `ms_env.sh` must remain untracked and secret-safe.

Durable preparation is now metadata-first through
`prepared-panel-manifest-v1`; the verified 701-panel visual cache is reusable
without provider calls. The invalid typed candidate is at
`/data/data/p0-aws-acceptance/cloud-stage-cache/2fc948cc2946867c605ea14b6210a234.json`
with hash
`c4662073d9aa1e51de1620c7d4b0edfe5a51ebf7fc3f7bdda6233789f93d7310`, 160
words, and 64.35 seconds. It remains a repair candidate only. Its exact
contract failures are duration and word-count out of range.

The repair harness now takes the candidate and compact selected visual/story
identities directly, checks exact panel and claim lineage, and makes no normal
narration call when the durable candidate is invalid. The collection-clean
RED had one intended body failure at `cloud.narrative_repair_identity_mismatch`;
focused GREEN is 69 passed (62 cloud, 7 manifest) with five existing Pillow
warnings. Safe provider errors are reduced to a stable code plus field/count.

The single bounded real repair request after this contract was present reached
`cloud.narrative_not_grounded`: returned claim IDs were not locally resolvable
against the compact story claim set. The request predated the safe diagnostic
patch, so the provider's returned count is intentionally unknown; current
local diagnostics would report `field=claim_ids;count=15`. No result cache,
narration artifact, MP4, voice, or QC exists. Do not repeat visual/story calls
or bypass the claim/evidence gate. The next resume command is the normal
project runtime with the correct database
`/data/data/p0-aws-acceptance/sample.db`, reusing the 701-row cache, and at
most one bounded repair request after the isolated fake-provider contract is
green.

Scoped Ruff/compileall/diff-check, no-churn and secret/allowlist scans passed
before publication. Keep the current related matrix exception explicit:
142 passed and 13 unchanged prerequisite failures at `pipeline.py:4362` on
both the parent and current comparison. No production readiness or preview
claim is valid until a grounded 115-125-word result is durably admitted and a
real silent render passes QC.

# TARGETED REPAIR SCOPE HARDENING - 2026-08-21

The prepared-manifest checkpoint is published as
`2df9ab4e756e501f9f30e5670239e77c1225c011` (parent `3330700dc7e4c310b19441d5c50099abbbae2b1d`); GitHub `main` matches. Current uncommitted scope is only the repair-scope reconciliation, its focused test, and this handoff.

RED was collection-clean with one intended body failure: the published runner
had no `_narration_repair_scope_reconciled` helper. GREEN is `66` focused
passes (`59` cloud and `7` manifest), with five existing Pillow warnings;
Ruff, compileall, and diff-check are clean. Provider prose may vary only when
the local candidate's passage IDs, claim IDs, exact panel evidence, claim type,
ending, observations, story spine, and causal scope are preserved. Local code
then restores the candidate evidence graph/roles. Any lineage drift remains a
fail-closed `cloud.narrative_repair_scope_invalid`.

The one normal resume was capped at one request and ended with that scope code;
no repair result was admitted. The durable candidate is `160` words / `64.35s`
with hash `c4662073d9aa1e51de1620c7d4b0edfe5a51ebf7fc3f7bdda6233789f93d7310`;
the earlier 172-word/69.57-second artifact is not present in the current
durable cache, and no relabeling is allowed. The failed job/log did not retain
an exact request count. Retry only after publishing this correction, using the
matching 701-panel cache and final 115-125 word / 50-60 second grounding gate.

# PREPARED MANIFEST + TARGETED REPAIR CHECKPOINT - 2026-08-21

Oracle authority remains `/home/ubuntu/manhwashorts` on `main`; rollback parent
is `3330700dc7e4c310b19441d5c50099abbbae2b1d`. The preceding checkpoint contained the
prepared-panel warm-resume manifest, strict typed narration repair boundary,
focused tests, and handoff docs only. Never stage `data`, `ms_env.sh`, DB/WAL,
provider caches, logs, media, or credentials.

The new `prepared-panel-manifest-v1` is metadata-only: ordered panel IDs,
source-asset checksums, integer crop bounds, segmentation identity, and
feasible-ledger/crop hashes are canonicalized and validated before reuse.
Metadata-only markers are blocked from provider observation. Review-only paths
still cold-materialize pixels. `preparation_metrics` makes cold/warm mode,
elapsed time, payload bytes, peak RSS, and source decoding auditable. Current
warm benchmark evidence is pending; the prior stopped preparation loaded about
529 MB of serialized panel data and reached about 784 MB peak RSS.

The existing 172-word/69.57-second result is preserved as typed
`narration-repair-candidate-v1` only. It cannot satisfy final cache admission;
the final result must independently pass 115-125 words, 50-60 seconds,
grounding/citations, model/prompt/visual/story identity, and display
derivation. Repair keeps retained passage/claim/evidence IDs and causal order,
removing only complete low-priority passages. The deterministic fake-provider
repair harness proves one repair request, no repeated normal-narration call,
atomic result caching, and idempotent resume.

Focused evidence is `65 passed` with five existing Pillow deprecation warnings;
Ruff, compileall, and diff-check are clean. Current and clean-parent related
matrices both reproduce `142 passed, 13 failed`, all at the unchanged
`run vision analysis before generating a draft` prerequisite in
`pipeline.py:4362`; this is a named non-regression exception, not a green
production gate. No real repair call, narration artifact, silent MP4, voice, or
QC is proven. Resume next through the normal service entrypoint, reuse the
701-row visual cache, make at most one bounded targeted repair request, and
record request/cache/timing evidence before continuing to render.

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


# CURRENT ORACLE EXECUTION CHECKPOINT - 2026-08-20

This block is authoritative for the current Oracle run and supersedes older
workspace histories below. Work directly in /home/ubuntu/manhwashorts on main;
the Windows checkout is transport-only.

- Source/test green checkpoint:
  dfb8c26e6148bb8b3e098d25b1bf691e14f94cbd
  (parent 078715a77251b097e563aff41f696a6005d75b7b).
- The checkpoint contains only app/services/cloud_multimodal.py and
  tests/test_cloud_multimodal_mass_production.py. Its focused topology
  coverage is green; documentation publication is the next checkpoint.
- Oracle's origin/main tracking ref is stale because Oracle HTTPS
  authentication is unavailable. Publish the exact commit object through the
  retained Windows transport clone only after docs and runtime gates are
  green; never force-push, push all refs, tags, credentials, runtime state,
  media, databases, caches, or logs.
- Protected runtime paths are untracked: data (the /data/data symlink),
  ms_env.sh, database/WAL files, provider state, caches, logs, and media.
  ms_env.sh contains credentials. Source /tmp/ms_env.sh only with output
  redirected and never print, copy, fixture, or commit its contents.

## Current production topology

- Visual map: 701 valid same-identity visual rows are cached under
  /data/data/p0-aws-acceptance/cloud-stage-cache; 526 original rows plus
  175 same-model repairs. The two poison source rows remain explicitly
  skipped. No later stage may call the visual provider for a valid cached row.
- Chapter story map: the last durable map covers 701/701 ordered panel IDs,
  with deterministic chunk merge and complete-coverage validation.
- Narration: the new code selects grounded editorial beats after full story
  mapping and sends one final selected-evidence reduce request. It rejects
  matching-hash but partial/stale narration caches and retains full-panel
  observations for downstream lineage. The source/test proof is 48 passed in
  tests/test_cloud_multimodal_mass_production.py.
- Runtime is not yet complete: the last pre-slice resume ended
  NEEDS_REVIEW with cloud.narrative_not_grounded, 3 narration requests,
  approximately 975.73 seconds wall time, and peak RSS 8,397,748 KB. It
  produced no narration artifact, timeline, MP4, voice output, or final QC.
  This is a measured blocker, not permission to weaken grounding.

## Resume and release gates

Resume the normal checked-in service after verifying no active process and the
job/cache state. Source /tmp/ms_env.sh with output redirected, set the
MS_DATABASE_URL, MS_STORAGE_DIR, MS_DATA_DIR, MS_OUTPUT_DIR, MS_TMP_DIR, and
MS_CLOUD_STAGE_CACHE_DIR variables to the /data/data/p0-aws-acceptance paths,
then run:

    .venv/bin/python -m pytest tests/test_cloud_multimodal_mass_production.py -q

The next production command is the normal project service resume documented in
docs/P0_129-133_AGENT_HANDOFF.md and docs/ARCHITECTURE_MAP_REDUCE.md; it must
reuse the visual cache, persist story/narration artifacts, and reach a real
silent MP4 before any claim of completion. Required acceptance remains:
complete evidence/grounding, 115-125 words and 50-60 seconds, 1080x1920
H.264 High/yuv420p video-only output, subtitle max two lines,
black/balloon/blank/lineage/QC gates green, and a second resume with no
unnecessary provider calls. Voice/TTS remains after the silent preview and
publication remains blocked.


# CURRENT ORACLE HANDOFF - 2026-08-20

- Authority: /home/ubuntu/manhwashorts on Oracle, branch main.
- Published checkpoint: 7f7ffe697b5b9aa6c9a8a95fa4c046597a0622d8,
  parent d14ea5916976b29797dd9d23947aa3c3dac53994; GitHub main matches.
- Source/test fix is published: provider ordered_beats is accepted only
  through local normalization, and a 180-panel story response with partial
  citations is split into deterministic 60-panel requests. Complete coverage
  remains mandatory. Focused fix tests: 2 passed; cloud regression: 40 passed.
- Runtime project/job: 22876a6014a842f48bfca58c10a592b5.
  Visual cache hit is 701 panels at
  /data/data/p0-aws-acceptance/cloud-stage-cache/5a60693742b5b2d390f60a686b3283bd.json;
  no cached visual calls were repeated. Story map is STORY_MAPPED with
  701/701 panel coverage, 60 beats, and 53 claims.
- Narration is not proven: 175 current visual observations have empty
  visible_facts, so the strict analyzer rejects the narration envelope.
  Do not use the older cache with model identity 1da735...; it is not the
  current configured identity. Do not synthesize facts or bypass coverage.
- No narration file, silent MP4, voice, or QC is available. The next safe
  action is a separately authorized same-identity visual repair for those
  incomplete rows, followed by the normal bounded narration resume.

Protected paths: data, ms_env.sh, DB/WAL, caches, logs, media, and
provider state remain runtime-only and must never enter Git. publish_allowed
is false and voice/TTS remains deferred.


# CURRENT ORACLE PHASE 1 CHECKPOINT - 2026-08-20

- Authority: /home/ubuntu/manhwashorts, branch main, published base
  91b005b150f75923c86f8b301d0d1f4fb5328dd1 (parent 27f0d95fd894aba8c6ee8fe34add32ef5f6ec7b9). The Oracle tracking ref is
  stale at b6f72cd; publish through the Windows exact-object transport.
- Protected untracked paths: data symlink, ms_env.sh, DB/WAL, provider
  state, caches, logs, and media. Never print or commit ms_env.sh.
- Phase 0 durable visual proof remains: the 701-panel cache/checkpoint copy
  under /data/data/p0-aws-acceptance/cloud-stage-cache is byte-identical,
  9 files, 7,855,981 bytes. No visual provider rerun is needed for the next
  stage unless cache identity invalidates.
- Phase 1 implementation is committed and published in the current checkpoint; its source/test paths are app/services/cloud_multimodal.py and tests/test_cloud_multimodal_mass_production.py.
- RED was collection-clean with three intended failures. GREEN is 38 cloud
  regression tests plus the expanded matrix at 239 passed and 1 existing
  fixture skip. The full non-slow run is 1068 collected, 1062 passed, 4
  skipped, 2 environment-invalid Linux failures in the Windows cmd.exe
  launcher tests.
- Implementation contract: story-map and narration use four workers and
  180-panel chunks, deterministic ordered merge, per-chunk cache keys,
  bounded retry, and resume without repeat provider calls. No provider raw
  payload, hash, credential, DB, or media is persisted by this slice.
- Next command is the normal story-map/narration service
  run against the durable visual cache. Then attempt the regular silent MP4
  path and inspect FFprobe/blackdetect/contact-sheet/QC. Voice/TTS remains
  deferred until the silent preview is proven.

# P0 Manhwa Shorts — Chapter 129–133 (703 panel)

Handoff lengkap untuk agent berikutnya. Status = **VISUAL STAGE SELESAI**, menunggu lanjut story map → narasi → render + voice.

---

## 1. Infrastruktur & Lokasi (HOST UTAMA = Oracle)

| Item | Nilai |
|---|---|
| Host | Oracle Cloud (`instance-20260816-2016`) |
| SSH alias | `oracle` (`~/.ssh/config` di orkestrator) |
| Repo | `/home/ubuntu/manhwashorts` (worktree modified, **BELUM commit**) |
| venv | `/home/ubuntu/manhwashorts/.venv` (Python 3.12) |
| FFmpeg | `/usr/local/bin/ffmpeg` (7.0.2 static) |
| Data besar | `/data/data` (disk 100GB) — `~/manhwashorts/data` → **symlink** ke `/data/data` |

**AWS tidak dipakai lagi** untuk proyek ini (sudah dipindah penuh ke Oracle).

### Env & kredensial
- File env utama: `/tmp/ms_env.sh` (juga disalin di root repo => `ms_env.sh`)
- Baca sebelum run: `source /tmp/ms_env.sh`
- Masuk **DB credential** (bukan env) untuk model vision: `grok-4.3` (id `bbc025efbef24e60aad3f6387f78d547`). Env `MS_LLM_MODEL` boleh beda; resolver ambil dari DB.

### Path penting (semua di `/data/data/p0-aws-acceptance/` via symlink)
- DB: `sample.db` (+ WAL 422MB — jangan commit, besar)
- Stage cache: `/tmp/ms-stage-cache/` (7 file; visual cache 703 dicek di sana)
- Cloud jobs: `cloud-jobs/22876a6014a842f48bfca58c10a592b5.json` (state `NEEDS_REVIEW` dari run visual terakhir yang crash — akan overwrite pada run sukses)
- Output: `output/22876a6014a842f48bfca58c10a592b5/`
- Segmentation review: `segmentation-review/`
- Reference panels (crop panel): `tmp/22876a6014a842f48bfca58c10a592b5/reference-review-panels/scene-*.png`

---

## 2. Status Pipeline (Saat Ini)

- Project: `22876a6014a842f48bfca58c10a592b5`, **703 panel** (113 halaman long-strip `900×16000`, 5 chapter 129-133, 646 asset)
- **Segmentation: ✅** (cache `segmentation` di stage_results)
- **Visual evidence: ✅ 701/703** (2 panel di-skip — lihat §5)
  - cache visual barusan ditulis ke `/tmp/ms-stage-cache/` (identity grok `aa2fc9cd...`)
- **Story map: ⏳ belum** (perlu dijalankan ulang — lihat §7)
- **Narasi: ⏳ belum**
- **Render + voice: ⏳ belum**

Dua panel skip (grok tolak konsisten, bukan 429):
- `region-5e2b11044fc68097804c`
- `region-ec97e3dd8c5b8941b68c`
→ logis: 701 panel yang dipakai, 2 hilang (±0.3%).

---

## 3. Porter / Model / Voice

- **Vision/LLM**: `grok-4.3` @ `http://43.156.164.238:8000/v1` (key di env/DB)
  - Context ±950K-1M token (terverifikasi 950K = 200 OK)
  - **Compute concurrency cap ~4-5 request besar bersamaan** (bukan rate-limit HTTP; semua 200, cuma antri lama)
- **TTS / Voice**: `grok-voice-latest` @ `.../v1/tts`, protocol `grok`, language `en`, **voice `the-explainer-american`**, resp `mp3`
  - Test berhasil (±1.97s audio)

---

## 4. Konfigurasi Terakhir (di `app/services/cloud_multimodal.py`)

Preview-only relaxations (DISETUJUI user utk batch 703, **prod tetap ketat**):

| Parameter | Nilai | Alasan |
|---|---|---|
| `VISUAL_REQUEST_MAX_PANELS` | **8** | sweet spot; 4=0.54s/panel; 16+ output > max_tokens => invalid |
| `VISUAL_PARALLEL_WORKERS` | **8** | ~4-5 efektif (compute cap); 16 memicu antrian |
| `VISION_REQUEST_TIMEOUT` | **600.0** | krusial! 30s = timeout palsu pada request 22-70s |
| thumbnail `_visual_provider_payload` | **384×576** | +30% throughput vs 512×768; kualitas OCR tetap ok |
| `max_tokens` di 3 request body | **65536** | tanpa ini response besar ke-truncate => invalid |
| `MAX_ESTIMATED_BYTES` | 3_500_000 | utk chunk besar (perp dimuat dari local cache) |
| Binary reduction | aktif | chunk gagal => dipecah sampai 1 panel; hanya panel beracun yang skip |
| Checkpoint per-chunk | aktif | `/tmp/visual_checkpoints.jsonl` — resume instan kalau run di-kill |
| Subset build | aktif | panel skip tidak bikin KeyError; hasil = subset chrome order |

Durasi narasi preview: **40-180s** (`chunk_step=600`, `allow_dialogue_copy=True`, `cadence_adapted=True`).

RENCANA story/narasi (belum implement): paralel **4 worker**, `chunk_step=180` (≈1 bab/chunk, kualitas aman).

---

## 5. Temuan Lengkap (dari awal → sekarang) — PENYEBAB & FIX

### A. Batching & format provider (narasi/story)
1. **Cache model mismatch**: semua cache lama identity `1da7354e` (gemini-3.7), runner grok identity `aa2fc9cd` → cache selalu miss → re-run penuh. Fix: buat cache ulang dengan runner grok.
2. **Format key berubah-ubah tiap run** (LLM non-deterministik): `inference_text`, `type`/`statement`, `factual`→`fact`, `entities` key baru → kontrak parse diperluas + **fallback generik** (unknown key → scan string value langsung di `Mapping`).
3. **`expected_panel_ids` ≠ visual.panel_ids** (karena 3 balloon-unknown dulu di-skip) → validate pakai `expected_panel_ids` dari observations yang benar diproses.
4. **`source_index` tidak deterministik** → reindex kontigu per-chunk.
5. **Coverage manifest** harus ikut jumlah obs yang diproses (tidak harus full 703 awal).
6. **balloon-unknown jangan di-skip di `_narration_observations`** — obs-nya lengkap; skip bikin count mismatch (277≠280 → kontrak "exactly once").

### B. Kontrak gate
7. **`script passage copies source dialogue`** (collision 4-gram dgn dialogue) → relax `allow_dialogue_copy=True` (preview; prod strict).
8. **Durasi narasi** di luar jangkauan (batch 703 wajar panjang) → preview 40-180s (prod 50-60s).

### C. Kecepatan / konfigurasi (AWALNYA 9 JAM → sekarang ±15-25 min visual)
9. **1 panel/request = 703 request** (~jam). Root: `VISUAL_REQUEST_MAX_PANELS=1`.
10. **Unlimited rate limit di dashboard tetap lambat** → ternyata bukan HTTP rate-limit (semua 200, no 429), tapi **compute-concurrency cap** model grok: ~4-5 request besar bersamaan, sisanya antri (TTFB panjang).
11. **`VISION_REQUEST_TIMEOUT=30` → 600**: penyebab "error invalid" beruntun — request 22-70s kena timeout palsu di concurren tinggi.
12. **Thumbnail 512×768 → 384×576**: +30%.
13. **Worker 8 vs 16**: 16 → antrian lama; 8 efektif.

### D. Resume / anti-gagal
14. **Checkpoint per-chunk** (`/tmp/visual_checkpoints.jsonl`): tiap chunk sukses langsung ditulis; re-run = seed + hanya sisa. Menyelesaikan masalah "27 menit kerja hilang saat kill".
15. **Subset build pada skip**: sebelumnya `reconciled=[reconciled_by_id[...] for ordered]` → KeyError begitu ada panel skip. Fix: filter `if pid in reconciled_by_id`, pertahankan order.

### E. Infra Oracle
16. Disk baru 100GB di `/dev/sdb` → `/data` (fstab). Data pindah symlink. Boot disk lega.
17. venv rebuild, ffmpeg static install.
18. Env & run scripts disalin dari AWS (md5 identik).

---

## 6. Skrip run/debug di `/tmp` (Oracle)

- `/tmp/run_tb2.py` — runner FULL pipeline (service.run_project), model grok, max_attempts=3
- `/tmp/run_visual_only.py` — jalankan VISUAL saja; pakai `pickle cache` panels (prepare 2-5min → 0s). **checkpoint pulih**.
- `/tmp/run_visual_50.py` — visual subset 50 utk debug
- `/tmp/timing_test.py` — ukur 1 request observe asli (durasi/rows)
- `/tmp/conc_test.py` — uji concurrency (4 vs 5 request 8-panel paralel)
- `/tmp/grok_bench.py` — benchmark batch size / worker (echo payload)
- `/tmp/panels_cache.pkl` (**529MB**) — panels hasil prepare (pickle); prepare jadi instan
- `/tmp/ms_env.sh` — env runtime

Cara run visual-only:
```bash
cd ~/manhwashorts && source /tmp/ms_env.sh
nohup .venv/bin/python -u /tmp/run_visual_only.py > /tmp/run_visual.log 2>&1 &
# pantau:
grep -c VISUAL_CHUNK_OK /tmp/run_visual.log
grep VISUAL_DONE /tmp/run_visual.log
```

---

## 7. NEXT STEPS (urutan yang disarankan)

1. **Story map (703)** — gunakan runner grok; sudah ada cache `story_map`/`narration` lama identity gemini → miss → jangan keliru. Rancangan: **paralel 4 worker, chunk_step=180** (≈1 bab). Est ±10-15 min.
2. **Narasi (703)** — chunk_step sama, tergantung story map. Est ±15-20 min.
3. **Render silent MP4 703 panel** (motion rules sudah ada di repo; referensi v8 08pbie di output lama).
4. **Voice (TTS grok-voice-latest)** — protocol `grok`, voice `the-explainer-american`.
5. **QC + konfirmasi render** sebelum klaim selesai (hard gate; jangan klaim tanpa MP4+QC).

Catatan batas waktu per run dari user: **stop + debug kembali** kalau melewati jatah (~22-30 min utk visual; pipeline penuh ~70 min).

---

## 8. Env (disalin ke repo root)

`ms_env.sh` — lihat file di root repo. JANGAN commit (berisi API key) kecuali repo bersifat private & user minta.
## Oracle interruption-safe checkpoint 2026-08-20

- Authority is /home/ubuntu/manhwashorts on Oracle, branch main.
- The checkpoint commit is 00b82b069a8ac3bf6910c1b2903e0847f66129e1.
  GitHub main is verified at the same SHA through the isolated Windows exact-
  history transport. Oracle's origin/main tracking ref is stale at b6f72cd
  because VPS HTTPS authentication is unavailable. The tracked worktree intentionally remains dirty; no reset, checkout,
  force push, or unrelated cleanup is authorized.
- Phase 0 focused verification is green: 169 passed with 35 existing Pillow
  deprecation warnings. The two checkpoint/cache regressions and the two
  narration/persistence regressions are green (2 passed). No provider call was
  needed for this verification.
- FileStageCache and scoped visual checkpoint persistence now use atomic local
  JSON writes and an instance-scoped checkpoint ledger. Existing runtime cache
  content was copied without deletion from /tmp/ms-stage-cache and
  /tmp/visual_checkpoints.jsonl to
  /data/data/p0-aws-acceptance/cloud-stage-cache. The copy is byte-identical:
  8 JSON stage files plus one checkpoint ledger, 9 files and 7,855,981 bytes.
  The visual cache entry records 701 panels; the source checkpoint ledger has
  736 lines. Runtime files remain outside Git.
- The source/tests/docs diff still includes prior preserved P0 work across the
  cloud, analyzer, visual, segmentation, render, operator-adjacent, and test
  paths. It must be reviewed and staged by allowlist; data, database/WAL,
  media/output, provider state, and ms_env.sh must not be staged.
- Story map, narration, timeline/render, silent MP4, voice/TTS, and final QC
  are not complete. The next bounded implementation is four-worker,
  approximately 180-panel story-map/narration chunking with deterministic
  ordered merge, durable per-chunk cache/checkpoint, bounded retries, and
  resume proof. Voice remains after a verified silent preview.
- Resume from a fresh shell with:
  cd /home/ubuntu/manhwashorts
  source /tmp/ms_env.sh
  .venv/bin/python -m pytest tests/test_cloud_multimodal_mass_production.py
  tests/test_analyzer_contract.py tests/test_vision_adapter.py
  tests/test_vision_pipeline.py tests/test_story_evidence.py
  tests/test_strip_segmentation.py tests/test_strips.py -ra
  Do not print the environment file or its values.

## Cache identity correction checkpoint - 2026-08-20

The current source/test parent is
27d86c44bb97fd03bf9f61d556bda195c244eac8. Before resuming the target job,
the duplicate visual-call defect was corrected in the cloud runner. Equivalent
full-preparation 703-to-701 inputs now have the same per-panel and whole-stage
identity because transient enumeration order and mutable metadata are excluded.
Identity includes ordered panel ID/index, immutable source checksum, normalized
crop transform, rendered provider-payload hash plus fixed payload policy, and
model/prompt identity.

A persisted legacy visual result is accepted only after exact ordered panel ID,
source-asset ID/checksum, monotonic persisted order, and recomputed legacy
descriptor/payload hash checks. It is then locally migrated to v2 with canonical
per-panel hashes; otherwise the visual stage is invalidated safely. The
source change/test checkpoint made zero provider calls. RED was 3 intended body
failures after 51 existing passes; GREEN is 54 cloud-stage tests plus scoped
Ruff/compileall/diff checks. No new narration, MP4, voice, or QC result exists.

Resume only through the normal checked-in entrypoint after verifying no active
process:
~~~bash
cd /home/ubuntu/manhwashorts
set -a; source /tmp/ms_env.sh >/dev/null 2>&1; set +a
export MS_DATABASE_URL=sqlite:////data/data/p0-aws-acceptance/sample.db
export MS_STORAGE_DIR=/data/data/p0-aws-acceptance/storage
export MS_DATA_DIR=/data/data/p0-aws-acceptance
export MS_OUTPUT_DIR=/data/data/p0-aws-acceptance/output
export MS_TTS_PROVIDER=null
PATH=/home/ubuntu/.local/bin:$PATH .venv/bin/python scripts/run_cloud_multimodal_batch.py   --project-id 22876a6014a842f48bfca58c10a592b5   --state-dir /data/data/p0-aws-acceptance/cloud-jobs   --segmentation-review-dir /data/data/p0-aws-acceptance/segmentation-review   --model grok-4.3 --max-attempts 3 --min-request-interval-s 0.3
~~~
Do not print ms_env.sh; keep data, DB/WAL, caches, logs, media, and secrets
untracked. The normal command must report the runner request count and durable
state before any subsequent story-map/narration/render/voice claim.

## Follow-up live cache migration proof - 2026-08-20

The first post-fix normal resume was safely terminated after exactly two new
visual chunk requests. The job JSON remained unchanged at 701 cached visual
rows; no new visual stage was accepted. Exact no-provider diagnostic:
prepared=703, filtered=701, ordered IDs/assets/checksums/bounds/coverage matched
persisted narration lineage, migrated=True,
proof=persisted_lineage_and_payload_derivation, identity_rows=701, canonical
source hash fb61e64ef66bce8e9fa9d79bc5e00ec5fd6ab8c3d0d7057a84d70dc04a7fa5c5.
The log /tmp/cache-identity-resume-20260820.log is runtime-only.

Use PYTHONPATH=/home/ubuntu/manhwashorts when invoking the normal checked-in
batch script. The live diagnostic made zero provider calls. Continue only after
checking no active process, then record the next runner request count and durable
state. The silent preview, voice, and final QC remain unproven.

## Targeted narration repair checkpoint - 2026-08-20

Published parent/checkpoint: 826856cc08550895ba8944e4b9b3fce6b0f62823.

The canonical visual identity migration is now followed by a local
narration-targeted-repair-v1 boundary. The normal story-map reduce completed
from the migrated 701-panel visual result. If the selected final narration is
outside 50-60 seconds or 115-125 words, the repair stage sends the existing
validated candidate and target contract to the same pinned model. It cannot
change passage/claim/evidence lineage, observations, ending kind, or story spine;
a scope change is cloud.narrative_repair_scope_invalid; three attempts are
the hard bound. No visual or story-map call is repeated.

RED: 2 collection-clean intended failures. GREEN: 2 new repair tests,
57 cloud mass-production tests, Ruff, compileall, diff-check. Runtime:
visual cache migration proof remained canonical, story map reached
STORY_MAPPED, narration ended NEEDS_REVIEW with
cloud.narrative_duration_out_of_range. The failure happened before the job
usage aggregate was saved; durable request_count=0 is therefore not the real
provider count and must not be reported as one. No MP4/voice/QC exists.

Resume command (do not print the environment file):
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
  --model grok-4.3 --max-attempts 3 --min-request-interval-s 0.3
~~~\n\n## 2026-08-21 narration repair cache/prompt isolation

Source checkpoint: parent d539c88. The repair boundary now uses a distinct prompt version/hash and `narration_repair` stage/cache identity, so a targeted repair cannot overwrite or be mistaken for the ordinary narration result. The ordinary chunk helper uses the ordinary `narration` identity. `_record_failure` persists the live request counter and estimated cost before writing NEEDS_REVIEW/FAILED.

Verification is 5 focused cache/repair tests, 57/57 cloud mass-production tests, Ruff, and diff-check. No provider retry or MP4 is claimed in this checkpoint. Resume with the existing command below only after confirming no active process; reuse the migrated visual cache and do not rerun cached visual panels.\n
## Typed narration candidate and repair cache handoff - 2026-08-21

The current code keeps the observed 172-word/69.57-second four-passage
narration as narration-repair-candidate-v1, never as an accepted final
narration result. Final admission requires 115-125 words, 50-60 seconds,
grounding/citations, ordered visual coverage, prompt/model/visual/story
identity, and independent display derivation. A bounded same-model repair
preserves retained claim/evidence lineage and causal order; only complete
low-priority passages may be removed, with at least four retained.
narration-repair-result-v1 is idempotent: a valid repair cache resumes with
zero additional provider calls.

Verification is green for the focused 5-test matrix and the complete
cloud-multimodal file (58 passed), Ruff, compileall, and diff-check. The
related 155-test matrix is not green: 142 passed and 13 current
tests/test_pipeline.py draft-fixture failures remain outside this slice.
The runtime job is still STORY_MAPPED with 701 visual rows and no proven
narration, MP4, voice, or QC. The last bounded attempt stopped around 28m50s
after four sanitized cloud.narrative_not_grounded chunk failures; do not
infer a provider count from the unsaved aggregate usage field.
## Position-locked rewrite-vector handoff - 2026-08-21
The previous immutable-ID repair was not retried. The current repair boundary
now preselects 8-12 trusted claim positions locally, in causal order, with a
deterministic 120-word budget and `slot_order_hash` covering candidate/story/
model/prompt identity plus lineage. The provider receives ordered context but
returns only a positional `rewrites` array. Local reconciliation owns passage,
claim, evidence, and display reconstruction; identifier wrappers, count/type
drift, budget/duration drift, reordered positions, and unknown lineage fail
closed. The position path is single-attempt and its result cache rejects
position-registry or slot-order drift.
RED: 6 collected, collection-clean, 6 intended body failures. GREEN: 138/138
focused cloud/manifest/adapter/synthesis tests; Ruff, compileall,
diff-check, no-churn, and secret scan clean. No real request has been made.
After the source/test/docs checkpoint is published, run exactly one bounded
repair request against the durable 160-word/64.35-second candidate. Do not
repeat visual/story stages or issue an automatic retry. Continue to silent
MP4, voice, and warm-resume only if the strict repaired result is admitted.
Until then the durable job is STORY_MAPPED with 701 visual rows and no proven
narration or media artifact.
## Published position-vector checkpoint

Commit `c663ccb72b4e7d29c86a14c793b83b957e5517e8` is the published fast-forward
from `080744718f40cb3480a6a9d83896eabbe533c3c4`. The position repair contract
is GREEN and no real repair request has been made yet.
## Position-vector live attempt and correction - 2026-08-21

One real repair request ran after the published `4a82e09` checkpoint against
the durable 160-word/64.35-second candidate and failed closed with
`cloud.narrative_repair_position_budget_invalid`; request count was exactly 1
and no retry occurred. Only sanitized metadata was persisted at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`.
The correction now gives each position an explicit bounded word range while
keeping total 115-125 words and 50-60 seconds strict. RED: 1 intended body
failure. GREEN: 139/139 focused cloud/manifest/adapter/synthesis tests, Ruff,
compileall, diff-check, no-churn, and secret scan. Publish this correction
before one new bounded real retry; do not repeat visual/story calls.
## Position-vector second live attempt and v2 correction - 2026-08-21

The published `6e8df193d80ba42cbc3b6c5aa838c9154b1fd600` correction was used
for exactly one newly authorized real repair request. It failed closed with
`cloud.narrative_repair_position_budget_invalid`; request count was 1 and no
retry or provider prose was retained. The sanitized report is still
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`1194ac83c3aa32ef933be9897f6207188c0f7bce1f04397b7b93c3c8f3096f61`.

The next correction is registry v2: each trusted position has the explicit
bounded range `max(7, target-8)` through `target+8`; aggregate 115-125 words
and 50-60 seconds remain fail-closed. Historical RED is one collection-clean
body failure against the old boundary; GREEN is 140/140 focused tests plus
Ruff/compileall/diff/no-churn/secret checks. Publish this correction before
one new real request. The project remains STORY_MAPPED with 701 visual rows;
no narration, MP4, voice, or final QC is proven.
## Position-vector response-shape instrumentation - 2026-08-21

After `1b2be08ae60a9a06ab8e5ec2e2972c22d9fb1e09`, one real request again failed
closed with `cloud.narrative_repair_position_budget_invalid` after exactly one
request and no retry. The old report has no response-shape metrics; its SHA is
`1194ac83c3aa32ef933be9897f6207188c0f7bce1f04397b7b93c3c8f3096f61`.

The unpublished correction records sanitized container/keys, array length and
types, per-position counts, total/duration, expected ranges, and the exact
failed predicate before raising, then persists them in the review queue and
the bounded harness report. Exact 120 words is guidance only; 115-125 words
and 50-60 seconds remain the final gates, with a 118-word regression green.
RED: one collection-clean prompt failure plus one collection-clean metrics
failure. GREEN: 142/142 focused tests, Ruff, compileall, diff-check,
no-churn, and secret scan. Publish before one new real request; never rerun
visual/story stages. No narration, MP4, voice, or final QC is proven.

## Position-vector scope correction - 2026-08-21

The first real request after `f47262fd16fd75522fdbfa65e79d18dfb9f967ea`
failed closed with `cloud.narrative_repair_scope_invalid` after exactly one
request and zero retries. The sanitized report is at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`d66bb529e2633785d7c93a8fdab6eaba4d445d5ae94d1e04f3f28194ff60a5b7`.
It failed after positional budget reconciliation, so its response-shape
metrics are empty; no provider prose/raw payload was retained.

RED was one collection-clean body failure for trusted claim compaction. GREEN
is 143/143 focused tests with Ruff, compileall, diff-check, no-churn, and
secret scan clean. The correction permits only an ordered trusted subset of
candidate claim/evidence references and preserves that subset in the canonical
result. This correction must be published before the next single bounded real
request. Resume command remains the isolated repair harness after publication;
do not rerun visual/story stages.

## Position-vector aggregate budget correction - 2026-08-21

After `7f17e6ed6b38fd8d85e0cd9e6acd50f937278f14`, the one bounded real
position-vector request failed closed with
`cloud.narrative_repair_position_budget_invalid`; request count was 1 and
retry count 0. The sanitized report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`22b4fd1b8a4ecf29f458a010bbf9879e936629d04fd720cc8c14684f70db1621`.
Its non-prose shape metrics were array length 12, string items, counts
`[14,9,13,8,10,10,9,13,15,9,12,13]`, total 135, duration 56.96 seconds,
expected `7..18` ranges, and `aggregate_word_count`.

RED proved the registry's per-position maxima could describe an impossible
aggregate. GREEN is 144/144 focused tests with Ruff, compileall, diff-check,
no-churn, and secret scan clean. The correction caps the sum of position
maxima at 125 while retaining final 115-125-word/50-60-second gates and
exact-120 as guidance only. Publish before the next one-request repair;
visual/story caches remain reusable and no final media is proven.

## Position-vector selection-count correction - 2026-08-21

After `bfb0ee137683f81caaf908cd47b8ea9216caa654`, the one bounded real repair
request failed closed with `cloud.narrative_repair_position_budget_invalid`
after exactly one request and zero retries. The sanitized report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`abdca214cfeb384eef2a38a0a20bca33d6716aa751d0794ea0b91645bd486d4f`.
Its non-prose metrics were array length 12, counts
`[12,12,11,14,13,10,12,13,10,11,11,11]`, total 140, duration 58.7 seconds,
expected maxima 11/10, and predicate `position_word_budget`.

RED proved the provider could receive too many trusted positions despite the
aggregate cap. GREEN is 145/145 focused tests with Ruff, compileall,
diff-check, no-churn, and secret scan clean. The correction selects at most 10
positions from the required 8-12 range, dropping only deterministic
lowest-priority removable claims while preserving causal order and at least
four passages. Publish before the next one-request repair; do not repeat
visual/story stages.

## Position-vector selection-count v2 correction - 2026-08-21

After `10eb14ef0a3bfe332cc8c7e3b3083b2216df6cb9`, the one bounded real repair
request failed closed with `cloud.narrative_repair_position_budget_invalid`
after exactly one request and zero retries. The sanitized report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`f6436f8a0cbcc4670593918b482c4f9756497386cb6834130e85ee4ab8c48590`.
Its non-prose metrics were array length 10, all counts 13, total 130 words,
duration 54.78 seconds, expected maxima 13/12, and predicate
`position_word_budget`.

RED proved the provider's stable 13-word position granularity could exceed the
10-position contract. GREEN selects at most 9 trusted positions, which remains
within the required 8-12 range, preserves at least four causal passages, and
drops only deterministic lowest-priority removable claims. The focused matrix
is 145/145 with Ruff, compileall, diff-check, no-churn, and secret scan clean.
Publish before the next one-request repair; do not repeat visual/story stages.

## Position-vector selection-count v3 correction - 2026-08-21

The published max-9 checkpoint `68f0e71298e8718e53b78b3d239671e8c204c0ec`
was tested with one bounded real repair request. It failed closed as
`cloud.narrative_repair_position_budget_invalid` after request count 1 and
retry count 0. The sanitized report remains at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
and has SHA-256
`ad198b21e470f7c530c71219f511a45d05a306699060eb9be8d97f478d916f14`.
Non-prose metrics were array length 9, per-position word counts
`[15,15,15,14,15,13,13,13,13]`, total 126, estimated duration 52.61 seconds,
expected maxima 15/14/13 by position, and failed predicate
`position_word_budget`.

The RED test demonstrates that max-9 can still exceed the strict local
position budget. The GREEN correction selects at most 8 trusted positions,
still within the required 8-12 range, retaining causal order, at least four
passages, and exact local evidence lineage. Focused verification is 145/145
with Ruff, compileall, diff-check, no-churn, and key-shaped secret scan clean.
Publish this checkpoint before one new bounded repair request; do not repeat
the 701-panel visual or story stages. No narration, MP4, voice, or final QC is
proven at this checkpoint.

## Position-vector aggregate admission correction - 2026-08-21

After the published max-8 checkpoint `ad4b62a7e7e6a4a4d9e70aefcc41aa54dac2a1c2`,
one bounded real repair request returned 8 strings with per-position counts
`[17,16,16,15,16,13,13,13]`, total 119 words, and estimated duration 50.0
seconds. It failed closed as `cloud.narrative_repair_position_budget_invalid`
only on the derived upper position guidance, after request count 1 and retry
count 0. The sanitized report remains at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`f8700f9e2f2486b8a85984a635a3333d102f9a97898624e92a5a6fefd3a9d16f`.

The RED regression proves that an in-range final response could be rejected by
the upper position predicate. GREEN retains the minimum position floor and
strict aggregate 115-125-word/50-60-second admission, while treating upper
per-position values as guidance once the complete response is in range. Exact
120 is guidance only. Focused verification is 146/146 with Ruff, compileall,
diff-check, no-churn, and key-shaped secret scan clean. Publish this fix before
another one-request repair; no narration, MP4, voice, or final QC is proven.

## Position-vector concise drafting correction - 2026-08-21

The first request after `cd458804e0e73344ac0cebc6c49f325e1b93ecd9` returned
8 strings with per-position counts `[17,17,18,18,18,15,17,16]`, total 136
words, and estimated duration 57.39 seconds. It failed closed after request
count 1 and retry count 0 because the final word bound was exceeded. The
sanitized report remains at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`5654413fcb1a03698d0a93e34742addf137bc13b6913697255074c61b34f6b80`.

The RED regression checks the concise drafting instruction. GREEN tells the
provider to treat each `word_budget_max` as a hard drafting target and not fill
position budgets with extra words. Local aggregate 115-125-word/50-60-second
gates remain strict and exact 120 is guidance only. Focused verification is
147/147 with Ruff, compileall, diff-check, no-churn, and key-shaped secret scan
clean. Publish before another one-request repair; no narration, MP4, voice, or
final QC is proven.

## Resume checkpoint: position guidance correction

Resume from published parent `7598bd58880f75ad0309eedf05e9d485703a1d9b` after
the GREEN source/test/docs publication. Do not rerun visual or story stages.
Run the focused cloud/manifest/adapter/synthesis matrix, then issue exactly one
bounded positional repair request with the current prompt/cache identities.
Per-position allocations are guidance only; retain hard vector, trusted-lineage,
grounding, causal, aggregate 115-125-word/50-60-second, identity, and display
gates. The only additional balance guard is the broad
`max(24, ceil(total_words * 0.25))` dominance limit. If the request fails,
persist sanitized shape/predicate metadata and stop provider calls; do not
retry unchanged and do not claim narration, MP4, voice, or QC.

## Position-vector live repair result and snapshot correction - 2026-08-21

After published checkpoint `e743ab219a17f426c07baca5745dab82fdd7648b`, the
authorized isolated harness made exactly one real `grok-4.3` repair request
and zero retries. It failed closed as
`cloud.narrative_word_count_out_of_range`; sanitized report SHA-256:
`44c4a9712da510ee53b63fd4eac395e20505c51bc84f15ff4abda95c875897a4`.
Non-prose response metrics were array length 8, counts
`[18,16,16,17,15,14,14,14]`, total 124, duration 52.17 seconds, and
slot-order hash
`a0c1a311a8a9e10ee9ccfc97b1bbac791abf59ae501c5f9b3a6bc4a8ba8f8823`.
The aggregate bounds passed, but a later gate rejected the candidate and the
runner snapshot still had `failed_predicate=null`. The follow-up RED/GREEN
fix updates that snapshot with stable failure code/predicate. No second
provider call was made; no narration, MP4, voice, or final QC is proven.

## Position-vector response-shape propagation correction - 2026-08-21

Parent checkpoint: `c39215d61211a80cf0f19729bcd0a026b1bb39cc`. The single
bounded real repair request after it used request/retry counts 1/0 and failed
closed as `cloud.narrative_word_count_out_of_range`. The sanitized report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
(`248525989776f6a52bb626f3439ef1ca36ecd0fd4cff13ece59ef5c946185ff2`).
Its response-shape metrics were empty because a later gate failed after
positional reconciliation; no provider prose was retained.

RED reproduced the missing propagation. GREEN now carries sanitized shape
metrics (array length, per-position counts, total/duration, slot/order
identity, and failed predicate) through the durable report and strips the
private field before analyzer validation. Focused verification is 151/151
with five existing warnings; Ruff, compileall, diff-check, no-churn, and
key-shaped secret scan are clean. Publish before one new bounded repair call;
visual/story stages remain cached. No narration, MP4, voice, or final QC is
proven.

## Position-vector safe target correction - 2026-08-21

The first request after `cd209c10ea6c1995adb09a3728c11be4b17b8626` returned
8 strings with counts `[17,16,15,16,17,15,15,15]`, total 126 words, and
estimated duration 53.04 seconds. It failed closed after request count 1 and
retry count 0 because the strict final word ceiling was exceeded. The
sanitized report remains at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`8656b36af56854bfa3cde52530b5ea1d1cabbe34f5ecb11d1b3dee627eddc3bd`.

The RED regression checks the safe-target prompt. GREEN aims for 118 total
words so normal provider variation remains in range; exact 120 is guidance
only and local aggregate 115-125-word/50-60-second gates remain strict.
Focused verification is 149/149 with Ruff, compileall, diff-check, no-churn,
and key-shaped secret scan clean. Publish before another one-request repair;
no narration, MP4, voice, or final QC is proven.

## Position-vector compact drafting correction - 2026-08-21

The first request after `e7cd76b34830fe9f9ea02eeb913a8eb28abbeb4f` returned
8 strings with counts `[17,17,16,16,17,15,15,14]`, total 127 words, and
estimated duration 53.48 seconds. It failed closed after request count 1 and
retry count 0 because the strict final word ceiling was exceeded. The
sanitized report remains at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`c99db623cc4ad565083cfdd893c3803c802774db8347c503502eaa5093c2cbff`.

The RED regression checks the compact-vector prompt. GREEN asks for 14-15
words per position in the fixed eight-position vector and never more than 15
unless a claim must be preserved. Local aggregate 115-125-word/50-60-second
gates remain strict and exact 120 is guidance only. Focused verification is
148/148 with Ruff, compileall, diff-check, no-churn, and key-shaped secret scan
clean. Publish before another one-request repair; no narration, MP4, voice, or
final QC is proven.

## Current resume checkpoint: post-repair final-gate diagnostics - 2026-08-21

Current published parent is
`6e389e1f343308ebd08864e414a8cb301bbbaf25`. Do not rerun visual or story
stages. The one authorized real `grok-4.3` positional repair request after
that parent used request/retry counts 1/0 and failed closed as
`cloud.narrative_duration_out_of_range`. Sanitized report:
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
with SHA-256
`bce6fee0304ece68e6f730abc75f1c53dd4afe2d1c89fe2e7debc4b353d026b6`.

Safe response metadata: eight rewrite strings; counts
`[18,17,16,16,16,13,13,13]`; total 122; pre-reconciliation estimate 51.3s;
slot-order hash
`cb0ce195a2e661f703e3330bf1373a20e7e3e7ac83c49314cb9d661d9d12db6e`.
The vector aggregate was in range, but the durable report lacked the final
reconstructed result shape needed to classify the later gate. The published
follow-up source/test boundary now records those reconstructed metrics and
failed predicates without provider prose. Focused verification is 153 passed
with five existing Pillow warnings; Ruff, compileall, diff-check, no-churn,
and key-shaped secret scan are clean.

No further provider call is authorized in this checkpoint. Preserve the
durable report, DB, caches, and `/tmp/ms_env.sh`; do not print or copy secrets.
There is still no valid narration, silent MP4, voice output, or QC artifact.

## Canonical narration duration resume checkpoint - 2026-08-21

Resume parent: `36bfa661e6aaffd59759c23cbf7d1ff719baa678`. The final v3
duration contract is `narration-duration-v1`: ASCII alphanumeric tokenization,
dramatic pacing at 2.3 words/second, and
`max(0.6, round(word_count / 2.3, 2))` for non-empty text. The hard final
gate is 115-125 canonical words and 50-60 canonical seconds. Per-position
budgets are guidance only. Legacy v1/v2 timing helpers remain unchanged.

The persisted real-request report at
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-budget.json`
contained only the pre-reconciliation 51.3-second estimate for the 122-word
vector. The exact RED fixture showed why the later gate diverged: a literal
`\\n\\n` passage separator added four `n` tokens, producing a local 126-word
result. The GREEN code uses real newlines and one canonical calculator across
repair reconciliation, result QC, cache identity/admission, v3 persistence,
and render planning. The observed 122-word vector is 53.04 seconds under the
authoritative rule.

Verification is 278 focused passed with five existing Pillow warnings. The
seven legacy pipeline fixture failures reproduce identically on clean parent
`36bfa661e6aaffd59759c23cbf7d1ff719baa678`. Full Oracle non-slow is
environment-limited: current is 1104 passed, 26 failed, 10 skipped; clean
parent is 1119 passed, 16 failed, 4 skipped. FFmpeg/encoder/probe, render
fixture, API/TTS, and Windows-launcher failures must be resolved on the actual
production host before claiming a final artifact. No provider call is made
until this checkpoint is published.

After publication, source `/tmp/ms_env.sh` silently and run exactly one
bounded repair request with the cached visual/story stages:

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

Do not rerun visual/story, print credentials, retain provider prose, or retry
automatically. On repair success continue to silent render/QC, configured TTS,
final voiced QC, and warm resume; on failure retain sanitized metrics only.

### Latest repair checkpoint

Published source is `99b042ed`. The one bounded real positional repair call
used the exact durable candidate and made one provider request with zero
retries. It failed closed at `aggregate_word_count` with sanitized shape
metrics: 8 strings, counts `[15,17,17,17,16,16,15,14]`, total 127, estimate
55.22s. The durable report is
`/data/data/p0-aws-acceptance/cloud-jobs/repair-attempts/20260821-position-vector-canonical-99b042e.json`.
The hard final range remains 115-125 words and 50-60s; do not relax it or
issue another provider call without a newly published repair change.

## Micro-compaction release handoff - 2026-08-21

Source checkpoint before this slice: `9960076ce4d7dba93de968e0dc7b1581d92cfe8b`.
The new local policy is `narration-micro-compaction-v1`: only a 126-130 word
repair result may be transformed, using deterministic audited contractions;
the canonical narration tokenizer counts each contraction as one spoken word.
The transformed vector is remeasured for 115-125 words and 50-60 seconds and
then revalidated for display derivation, grounding, causal order, lineage,
dominance, and cache identity. No safe operation or a total outside the window
fails closed with sanitized metrics; no content words are deleted and no
provider retry is introduced.

RED: 4 intended failures, 1 duration-gate pass. GREEN: 5 focused tests, the
full cloud multimodal regression file, scoped Ruff, compileall, and diff-check
passed. The policy/result version and transformed-vector hash are persisted in
repair cache metadata. Visual/story caches were not touched and no provider
request was made. Narration admission, MP4, TTS, and QC remain unproven.

Fresh-agent next command after the GREEN commit is the existing one-request
resume command in the preceding canonical-duration section, with the same
database/cache/model identity and no visual/story repeat. Do not print
`/tmp/ms_env.sh`; leave data, DB/WAL, cache, media, logs, and credentials
outside Git.

The full non-slow verification after this change collected 1130 tests: 1124
passed, 2 failed, and 4 skipped. Both failures are the Windows `cmd.exe`
operator-launcher tests, which cannot execute on Oracle's Linux host; they are
not failures in the changed narration path. The 117-test cloud/narrative
matrix passed, and no provider request was made. Windows launcher verification
remains required before a production-readiness claim.

## 2026-08-21 resume-safe 703-to-701 manifest fix

The published parent is `d5d26e2a7a2383834d33bd37904bb8af4053b8b8`. The one
bounded resume attempt stopped before provider dispatch because the metadata
manifest rejected gapped source orders as non-contiguous; its sanitized job
code was `cloud.narrative_repair_scope_invalid`. This was the filtered-subset
boundary, not a provider response or narration-quality result.

The repair introduces prepared-panel manifest v2. `source_order` remains the
immutable segmentation/visual lineage coordinate; `prepared_order` is a
separate deterministic execution index for the exact processed subset. The
metadata-only cached rebuild validates 703 source regions against 701 visual
rows, retains the two source-order gaps, derives `prepared_order` 0..700, and
does not decode or resend any panel. Legacy v1 manifests migrate only after
their existing hash and lineage checks pass. Duplicate IDs, reordered source
orders, duplicate execution indices, changed crop/dimensions/checksums, and
payload identity mismatches remain fail-closed.

Verification is 128 focused tests passed (11 manifest, 93 cloud, 14 narrative
pipeline, 10 narrative QC), plus Ruff, compileall, diff-check, and secret scan.
The previously published normal runner was executed once with the same
project/model/cache identity and no visual/story repeat; its failure is
recorded below. Do not issue another provider call from this checkpoint.
Runtime DB, caches, media, `data`, and `/tmp/ms_env.sh` remain outside Git.

## 2026-08-21 repair result after manifest publication

Published manifest fix: `bcfb97119492df9dcf4a57aa22f5458b5f07dbb8`. The cached
runner executed once with one request and zero retries. The 701-panel prepared
manifest rebuilt with immutable gapped `source_order` and contiguous
`prepared_order` 0..700; visual/story caches were reused.

The job ended `NEEDS_REVIEW` at the local narration boundary with sanitized
`cloud.narrative_not_grounded`, `field=passage_evidence`, `count=5`. No provider
prose was persisted or reported. No narration, silent MP4, TTS, voiced MP4, or
QC exists. The single authorized repair request is consumed: do not retry it.
Fix/test this exact boundary and publish a new GREEN checkpoint before asking
for another bounded call.

## Passage-evidence reconciliation checkpoint — 2026-08-21

Rollback parent is published main `3cc0283923d4ebc1ce2904338f4ec96e5f2d0495`.
The current dirty scope is the passage-lineage source/test change plus this
handoff documentation; `data`, `ms_env.sh`, DB/WAL, caches, logs, media, and
credentials remain protected runtime state.

The previous normal resume used one request and zero retries, reused all valid
701-panel visual/story caches, and ended before narration admission with
`cloud.narrative_not_grounded`, sanitized `field=passage_evidence`, `count=5`.
No provider prose was stored and no narration, silent MP4, TTS, voiced MP4, or
QC exists.

The local positional repair reducer now builds passage evidence solely from the
trusted position registry. For each retained passage it copies valid local
claim IDs and deterministically ordered evidence panel IDs; merged positions
use only the union of their contributing trusted refs. It rejects missing or
empty trusted refs, foreign/unknown/duplicate claims or panels, changed order,
malformed containers, stale version, and lineage-hash drift as
`cloud.narrative_repair_position_lineage_invalid`. Identity version:
`narration-repair-passage-lineage-v1`.

Proof before another external call: RED 4 intended collection-clean failures;
GREEN focused 5/5, cloud file 97/97, related analyzer/script/manifest 121/121,
Ruff/compileall/diff-check clean. The known 13 `tests/test_pipeline.py`
pre-vision fixture failures remain reproduced against the clean parent and are
explicitly not a full-suite green or production-render claim.

After publication, issue at most one new repair request to the same configured
model, with zero retries and no visual/story repeat. If it passes all strict
grounding/lineage/duration/display gates, persist narration and proceed to
silent render/QC, then configured voice/TTS and warm-resume proof. If it fails,
persist only sanitized metrics/predicate and stop provider calls.

## Post-publication repair-resume outcome — 2026-08-21

Checkpoint `8097f0b8da60a32834d5e39d445df1393637457b` is published and clean
apart from protected runtime paths. Its documented normal runner command was
executed once with a one-request budget. The visual cache was reused, but the
compatible story/candidate cache was not found; the job ended before targeted
repair as `cloud.request_budget_exceeded` with sanitized `request_count=1`.
No repair JSON, provider prose, narration, silent MP4, TTS, voiced MP4, or QC
exists. No further request has been made.

The durable candidate's visual identity is
`73c224732858ead17bdee4003cfc8824a7f1470e7e0a238f8baa5d80fd0b9579`; the
current 701-panel story context has a different identity. Do not force a hash,
reuse a different story map, rerun visual/story, or send another provider
request. The next implementation slice is a metadata-only exact-cache
reconciliation with focused identity tests, then a newly authorized bounded
repair attempt.

## 2026-08-21 local narration admission/state discrepancy fix

Rollback parent: `78759e92dedf0c0ba9b6c6f49408c25dd4d7c68a`.

The sanitized persisted candidate reports 118 words, 51.3 seconds, 5
passages, 8 claims, and 701 observations with matching visual identity and
complete observation/passage/claim evidence checks. The exact local defect was
scope mixing: targeted repair kept a continuity ledger for 40 selected panels,
then final assembly widened observations to 701 panels. The lightweight
admission missed continuity coverage; persistence correctly rejected the mixed
object as `cloud.narrative_not_grounded`.

The fix uses the existing analyzer continuity predicate at grounded admission
and validates/copies the locally derived full structural ledger through
`_reconcile_narration_full_scope` before final admission. It preserves all
hard grounding, lineage, causal, duration, visual, audio, and QC gates.

Offline replay used only the existing 701 prepared-panel descriptors (no image
decode, DB write, cache write, or provider call). The old object is 40/701 for
continuity and fails strict admission; metadata-only reconstruction is 701/701
and passes the shared analyzer validator and final admission. The new RED
regression reproduced the old false-positive admission; GREEN is 113 cloud
tests plus 83 related tests. Non-slow is 1,154 collected: 1,148 passed, 2
failed, 4 skipped; the two failures are Oracle-Linux `cmd.exe` launcher tests.

Current job state remains `NEEDS_REVIEW` until the published code is resumed
through the normal local reconciliation/persistence transaction. Do not repeat
visual/story stages, issue a provider request, edit runtime JSON/DB manually,
or claim narration/render/voice/QC readiness. After publication, resume with
the existing cached job and record the state transition before any downstream
stage.
## Cached narration state-boundary continuation — 2026-08-21

Rollback parent: `5cff1984f48a6711e47fadad94557bb42cdb08fb`.
Publication commit: `392298a5b837462c9f3440a3e02328f316e3990c`.

The published continuity fix correctly rejects a 40-panel repair ledger mixed
with 701 observations. The resume path had to honor that invariant too: its
old cache-miss branch could dispatch a new narration request after a local
cached candidate failed strict admission. `CloudBatchService` now performs a
metadata-only `_reconcile_cached_narration` step from the exact current visual
and ordered panel registry before cached admission. It reconstructs only
locally owned observations/continuity and calls no provider. Any unreconcilable
cached record is fail-closed.

RED reproduced the missing state helper; GREEN resumes the persisted mixed
candidate through `run_job` with a provider-call sentinel and proves
`READY_TO_RENDER`, full continuity persistence, and zero narration dispatch.
The runtime job remains untouched and still needs the normal persistence
transaction. Resume only after publication with the existing cached visual,
story, and narration identities; do not repeat visual/story calls, edit DB or
job JSON manually, or issue a cloud request in this boundary. No MP4, voice,
or QC artifact is proven.

## Post-publication zero-budget persistence attempt — 2026-08-21

Published code: `392298a5b837462c9f3440a3e02328f316e3990c`.

The normal checked-in batch runner was executed with all narration/repair
request budgets set to zero. It did not call the provider and did not repeat
visual/story stages. Cached narration admission passed after local full-scope
reconciliation (118 words, 51.3 seconds, 5 passages, 8 claims, 701
observations, continuity `701/701`). The next persistence boundary failed
closed at `pipeline.generate_script` / `_validated_persisted_vision_output`:
`PipelineError: persisted vision evidence is invalid`. The job JSON now records
`FAILED` / `cloud.persistence_failed`; the DB transaction rolled back and
SQLite integrity is `ok`.

Read-only current-project DB facts are two older `StoryAnalysis` rows with 280
panel regions each, versus the cached 701-panel prepared manifest. The exact
DB round-trip/selection/serialization predicate still needs isolation; do not
guess a migration or rerun cloud work. The next resume command is the same
zero-budget `scripts/run_cloud_multimodal_batch.py` invocation after a local
test/fix of the 701-panel persistence boundary. Stop before provider/TTS and
do not claim narration persistence, MP4, or QC.

## Strict repair evidence closure — 2026-08-21

Rollback parent: `bbd2211343715f781be821930b218d63ea713175`.

The targeted repair now has an exact local evidence-closure boundary. It starts
with the persisted candidate's retained passage and claim IDs, resolves each
claim to canonical story-map evidence, then resolves those panel IDs to their
beat/section ancestry. Only the resulting ordered permitted panel set may
appear as candidate context. Closure identity includes candidate/story
visual/model/prompt hashes and ordered story panel IDs. No broad same-chapter
fallback, hash rewrite, or provider-owned lineage is accepted.

The provider response remains positional rewrite text only. Local code copies
claim/evidence lineage from the trusted registry and rejects foreign,
unresolved, unrelated-section, duplicate/mixed, missing-ancestry, stale-story,
changed-hash, and closure-metadata drift as
`cloud.narrative_repair_evidence_closure_invalid`. The positive exact p2
ancestry case and negative closure cases are tested: focused closure 5/5,
cloud file 122/122, and related matrix 275/275. Closure RED was recorded
before production edits; no provider request or runtime-state/DB/media/secret
change was made. Publish the source/test/docs checkpoint before the single
same-model zero-retry repair request.

### Position-level evidence closure correction - 2026-08-21

The published closure replay was still blocked locally because each expanded
claim position inherited its passage-wide evidence union. That contradicted the
validator's exact-claim rule and produced
`cloud.narrative_repair_evidence_closure_invalid` before any provider call.
Registry v5 now stores only the validated claim refs on each position; the
passage result reconstructs the trusted union from those positions. This is a
stricter provenance repair, not a broader same-chapter fallback. The exact p2
positive case proves separate panel refs, and unrelated, unresolved, duplicate,
mixed-section, missing-ancestry, and stale-identity cases remain rejected.

The offline gates are 5/5 focused closure, 122/122 cloud, and 275/275 related;
no provider request or runtime-state edit occurred. After publication, make
only the one authorized same-model repair request, zero retries, no visual/story
repeat, and continue only if the complete grounding/lineage/causal/
word-duration/display/cache contract admits the result.

### Multi-section passage closure correction — 2026-08-22

The Oracle worktree is based on
`24971e742653aeae48a2b15757adccf44a5dedb9`. The cached repair replay exposed
a strict local defect: p3 contains claims whose canonical ancestry spans two
story sections. v1 checked passage context against each one-claim closure,
so positions 4 and 5 failed closed before any provider request. The v2 fix
uses `_story_passage_evidence_closure` to union only the exact passage's
trusted claim ancestry; position rows retain exact claim-level evidence and
all foreign or unrelated panels remain rejected.

The new regression plus existing closure cases are GREEN (5/5); the cloud
file is 123/123; the related analyzer/story/narrative matrix is 211/211; the
segmentation/vision matrix is 134/134; Ruff, compileall, diff-check, and
no-churn checks pass. The persisted offline tracer reports all eight rows
`ROW_OK`, `CLOSURE_OK`, registry v5, closure hash prefix `e4636ae3`, and zero
provider requests. This source/test/docs checkpoint is published as
`bd6f7d791d033f36f62c725b724fdcad9fdc2b8b`; protected `data`, `ms_env.sh`,
DB/WAL, caches, logs, and media remain outside Git.

Resume only after publication with the one authorized same-model repair
request, zero retries and no visual/story repeat. Do not rerun cached visual
or story stages and do not continue to narration/render unless the complete
strict admission contract passes.

### Micro-compaction v2 correction — 2026-08-22

The single authorized cached repair call used one request and zero retries but
returned a structurally valid 128-word vector. Local admission correctly
failed at `micro_compaction_no_safe_operation` with an estimated 55.65 seconds;
only sanitized shape metrics were retained and no provider prose was stored.

The local RED regression showed the policy vocabulary was still v1. GREEN adds
only standard meaning-preserving future/modal and negative contractions,
bumps the identity to `narration-micro-compaction-v2`, and stops at 125 words.
The hard 115–125 word, 50–60 second, grounding, anti-copy, lineage, identity,
display, and persistence gates are unchanged. Compaction tests are 4/4, cloud
is 124/124, related analyzer/story/narrative is 211/211, and static/no-churn
gates pass. No second provider request has been made.

Publish this source/test/docs correction before another single same-model
repair request; visual/story caches remain reusable and must not be rerun.
