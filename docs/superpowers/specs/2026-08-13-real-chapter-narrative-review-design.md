# Real Chapter Narrative Review: Codex Manual Vision and Sharp Friend v1

## 1. Purpose and user outcome

This design defines one isolated editorial reference run over the existing
23-panel chapter. Codex manual vision examines every ordered source panel and
produces the first human-reviewable `sharp_friend_v1` narrative for that
chapter. The result is a reference narrative and review bundle, not a
production analysis run and not a publishable deliverable.

The run must answer, in order:

1. What is visibly happening in every source panel?
2. What causal chain connects those panels?
3. What evidence-grounded English narration presents that chain as a clever,
   friendly, perceptive friend under controlled tension?
4. Which parts are certain, qualified, or unresolved?
5. Does the narration satisfy the completed Sharp Friend v1 contract while
   keeping spoken narration separate from display subtitles?

The run must not call a vision provider, TTS provider, audio service, render
service, database migration, or publication service. It does not create or
modify `StoryAnalysis`, `PanelRegion`, `ScriptVersion`, `AudioSegment`, or any
other production record.

## 2. Scope and source authority

### 2.1 Ordered source ledger

The authoritative input is the existing reviewed chapter ledger and its
source-file checksums. The ledger is chronological and immutable for this run.
It contains source orders `0..23`:

- source order `0` is the title/front-matter page. It must be inspected and
  retained in the completeness ledger, but it is explicitly excluded from
  story claims, narrative passages, story-beat coverage, and the 23-panel
  story count;
- source orders `1..23` are the complete story-panel scope. Every one must be
  examined exactly once in ascending order;
- no panel may be sampled, skipped, reordered, randomly selected, or replaced
  by a filename-based guess.

The source ledger records, for every order:

```json
{
  "source_order": 7,
  "source_asset_id": "<exact ledger value>",
  "panel_id": "<exact ledger value>",
  "review_path": "<relative ignored review path>",
  "source_storage_path": "<exact source path from the ledger>",
  "sha256": "<sha256 of the source bytes>",
  "width": 1080,
  "height": 1920,
  "rights_status": "internal review only",
  "included_in_story": true,
  "exclusion_reason": ""
}
```

The title record has `included_in_story: false` and the nonempty exclusion
reason `title_front_matter`. The exact IDs, paths, dimensions, and hashes are
read from the current ledger; this design never invents them. Before any
visual review begins, the run verifies that the ledger has exactly one record
for each order `0..23`, that each referenced file exists, and that its current
SHA-256 and dimensions match the ledger. A mismatch stops the run with a
sanitized provenance error.

### 2.2 Provenance boundary

The run is labeled exactly:

```text
provenance_kind = codex_manual_vision_reference_v1
production_evidence = false
production_analysis = false
publish_allowed = false
rights_status = internal review only
```

`codex_manual_vision_reference_v1` is a review-bundle provenance label. It is
not `vision_evidence_v2`, `editorial_full_panel_evidence_v1`, or any other
production evidence version. Manual visual observations must not be copied
into production `StoryAnalysis` or relabeled as provider output. A later
production run must independently satisfy its own provider, lineage,
coverage, approval, and rights gates.

The bundle records the manual review method, run identifier, input-ledger
hash, Sharp Friend profile/version hashes, and the Codex review session label
without storing credentials, authorization headers, hidden prompts, or raw
provider payloads. The session label is audit metadata only; it does not claim
that a production provider or production model generated the observations.

## 3. Reused contracts and non-negotiable constraints

The reference run consumes the contracts already implemented on `main`:

- `app/services/narrative_identity.py` supplies the explicit
  `sharp_friend_v1` profile through `get_narrative_identity()` and its
  verified prompt resource through `load_narrative_instruction()`;
- `app/services/analyzer_contract.py` defines the explicit v3 structure and
  shared evidence/coverage/continuity rules. The manual bundle may use its
  pure validation rules for an offline check, but it must not pretend to be a
  provider response or mutate a production row;
- `app/services/editorial_qc.py` provides the non-rewriting
  `screen_narrative_naturalness()` report;
- `app/services/quality.py` provides the safe conversion of narrative
  findings into blocking checks and warnings;
- the current spoken/display contract keeps punctuation-bearing narration
  separate from punctuation-free uppercase display words.

Defaults and compatibility are preserved:

- the default v2 analyzer and its five-role behavior remain unchanged;
- the manual run selects `sharp_friend_v1` explicitly and never silently
  falls back to v2;
- no manual artifact is treated as a production script or as a substitute for
  production visual evidence;
- no automatic prose repair, role reshuffling, claim invention, or display
  mutation is allowed.

## 4. Required review outputs

The run produces one isolated, content-addressed review bundle under the
existing ignored local review-data area. The bundle is not a repository
artifact. Its canonical files are:

### 4.1 `source_ledger.json`

The immutable `0..23` ledger described above, including source hashes,
dimensions, rights state, inclusion flags, and a canonical ledger SHA-256.

### 4.2 `panel_understanding.json`

Exactly 24 ordered records, one per source order. The title record contains
only front-matter handling and its exclusion reason. Each story record
contains:

```json
{
  "source_order": 12,
  "source_asset_id": "<ledger value>",
  "panel_id": "<ledger value>",
  "visible_summary": "A concise paraphrase of visible action and layout.",
  "visible_entities": ["only visibly supported labels"],
  "actions": ["only visibly supported actions"],
  "setting_or_continuity": "Visible location or continuity context.",
  "dialogue_present": true,
  "dialogue_paraphrase": "Paraphrase only; never a copied dialogue line.",
  "uncertainties": ["A bounded uncertainty if present."],
  "confidence": "high",
  "evidence_status": "manual_visual_review"
}
```

`visible_entities` may contain a name only when that identity is supported by
the reviewed panel/evidence record. Otherwise use a visual descriptor such as
`wounded warrior`, `white-haired fighter`, `beast warrior`, `mages`, or
`marked man`. The reviewer must not invent relationships, motives, locations,
or events that are not visible or causally supported by the ordered panel
record.

The bundle never stores copied speech-balloon text as a substitute for visual
understanding. If dialogue is relevant, the record stores a concise
paraphrase and a `dialogue_present` flag. Error messages contain only stable
codes and source orders; they never include source text, panel text, or
narrative output.

### 4.3 `chapter_map.json`

The causal reconciliation covers all story orders `1..23` and no title order.
It contains ordered beats and causal edges:

```json
{
  "beats": [
    {
      "beat_id": "beat-01",
      "panel_orders": [1, 2, 3],
      "visible_change": "What changes across these panels.",
      "stakes": "The supported immediate stakes.",
      "qualification": "What remains uncertain, if any.",
      "evidence_refs": [1, 2, 3]
    }
  ],
  "causal_chain": [
    {
      "from_beat": "beat-01",
      "to_beat": "beat-02",
      "relationship": "The supported causal transition.",
      "evidence_refs": [3, 4, 5]
    }
  ],
  "coverage": {
    "story_orders_required": [1, 2, 3],
    "story_orders_covered": [1, 2, 3]
  }
}
```

The actual beat labels are chapter-specific and must be grounded in the
review; the schema is fixed. Every beat and causal edge has at least one
story-panel reference. A causal interpretation that is not directly visible
must be marked as qualified and supported by the relevant sequence, never
presented as certain fact.

### 4.4 `narration_spoken.txt` and `narrative_review.json`

The spoken narration is English, punctuation-bearing, and preserved exactly
as reviewed. `narrative_review.json` is the authoritative structured wrapper:

```json
{
  "provenance_kind": "codex_manual_vision_reference_v1",
  "profile_id": "sharp_friend_v1",
  "profile_version": "1.0.0",
  "prompt_version": "vision-first-story-analyzer-v3",
  "prompt_sha256": "<verified current profile value>",
  "profile_contract_sha256": "<verified current profile value>",
  "source_ledger_sha256": "<canonical ledger hash>",
  "passages": [
    {
      "passage_id": "p-01",
      "editorial_role": "chapter-specific semantic label",
      "spoken_text": "Punctuation-bearing reviewed narration.",
      "claim_ids": ["claim-01"],
      "evidence_refs": [1, 2, 3],
      "qualification": "Required when the passage interprets rather than directly describes."
    }
  ],
  "ending_kind": "cliffhanger",
  "unresolved_question": "",
  "word_count": 0,
  "estimated_duration_s": 0.0,
  "approval_state": "PENDING_EDITORIAL_REVIEW"
}
```

There are exactly 4–6 passages. `editorial_role` is a nonempty
chapter-specific semantic label; the v2 hook/setup/escalation/payoff order is
not imposed. Every passage has at least one claim and one evidence reference.
Every claim has a unique ID, a concise claim type/text, a qualification when
interpretive, and panel references. The union of passage references covers
all material claims; no unsupported claim is permitted.

The initial target is 90–125 whitespace-counted spoken words. That range is a
review signal, not an automatic rejection by the v3 structural contract. The
bundle records a deterministic estimate using the fixed review convention
`estimated_duration_s = round(word_count * 60 / 150, 3)` and labels it as an
estimate because no TTS timings exist. It does not claim an audio duration or
render duration.

The ending contract is exact:

- `cliffhanger` may end with a statement and must include a supported
  unresolved direction;
- `consequence` ends on the supported consequence and must not be forced into
  a question;
- `open_question` must end in `?` and include a nonempty,
  evidence-backed `unresolved_question` in the final passage;
- a question mark on `cliffhanger` or `consequence` is an ending mismatch;
- the reviewer never manufactures a question merely to satisfy the shape.

The spoken text must not contain copied dialogue, generic hype, a call to
action, invented identity/motive/relationship/fact, certainty inflation, or a
fixed channel catchphrase. Contractions, varied sentence lengths, causal
connectors, selective commentary, and a non-question ending are allowed when
they serve the evidence.

### 4.5 `display_cues.json`

Display text is derived after the spoken text is fixed. It is a separate
representation and never replaces or edits `narration_spoken.txt`.

Each nonempty spoken token produces one display cue record:

```json
{
  "spoken_token_index": 0,
  "display_text": "WHY",
  "timing_status": "not_rendered"
}
```

The derivation uses the existing deterministic display normalizer: retain
Unicode letters and digits, remove punctuation and symbols, collapse
whitespace, and uppercase the result. Apostrophes, hyphens, quotation marks,
question marks, and other punctuation do not appear in display text. A token
that normalizes to empty is skipped safely. Every emitted `display_text` is
exactly one uppercase Unicode-alphanumeric word. No absolute timing is
invented because this review does not create audio or video.

### 4.6 `qc_report.json`

The QC report is deterministic for a fixed bundle and contains no raw source
or secret values. It records:

- input ledger integrity and exact `0..23` order coverage;
- story coverage `1..23` exactly once, with title order `0` excluded from
  narrative claims;
- panel/evidence lineage and claim-reference coverage;
- causal-chain completeness and qualification coverage;
- passage count, word count, and duration estimate;
- ending-kind and punctuation result;
- copied-dialogue, CTA, generic-hype, unsupported-identity, and
  unsupported-causality findings;
- sentence-length percentiles/variance, repeated phrase/sentence ratio,
  opening n-gram diversity, connector diversity, causal-transition presence,
  contraction presence as an allowed signal rather than a quota;
- spoken/display separation and one-word display validity;
- `blocking_findings`, `warnings`, `review_state`, and a canonical QC hash.

Blocking findings are missing/foreign/duplicate panel lineage, incomplete
story coverage, missing evidence for a material claim, copied dialogue,
unsupported identity or fact, CTA/hype violations, unqualified
interpretation, malformed ending, malformed display cue, or source checksum
drift. Word count outside 90–125, rhythm/template risk, and low connector
diversity are warnings unless an existing Sharp Friend validator explicitly
classifies them as blockers. QC screens and reports; it never rewrites the
narrative.

## 5. Architecture and data flow

This is an offline review boundary around existing contracts, not a new
production runtime path:

```text
immutable source ledger (0..23)
        |
        v
ordered manual visual inspection of every panel
        |
        v
panel_understanding.json + chapter_map.json
        |
        v
Sharp Friend v1 evidence-linked passage draft
        |
        v
analyzer/naturalness/display contract checks
        |
        v
narration_spoken.txt + display_cues.json + qc_report.json
        |
        v
PENDING_EDITORIAL_REVIEW -> APPROVED_REFERENCE_ONLY or REJECTED
```

The boundaries are deliberately narrow:

1. **Ledger boundary:** verifies source identity, chronological order,
   dimensions, checksums, rights state, and title exclusion.
2. **Observation boundary:** creates paraphrased per-panel visual records and
   never exposes source text in failure output.
3. **Reconciliation boundary:** turns the ordered records into beats, claims,
   continuity, and qualified causal edges.
4. **Narrative boundary:** renders 4–6 Sharp Friend passages from the reviewed
   evidence, preserving punctuation and spoken wording.
5. **Display boundary:** derives punctuation-free uppercase one-word cues from
   the frozen spoken text.
6. **QC/review boundary:** reports deterministic findings and requires a
   human decision. It does not persist or approve production records.

The existing production flow remains separate:

```text
real provider observations
  -> production visual evidence and PanelRegion lineage
  -> StoryAnalysis
  -> ScriptVersion
  -> explicit SCRIPT_APPROVED
  -> later voice/profile/timeline/render gates
```

The manual bundle may be used by an editor as reference material, but it
cannot satisfy any of those production gates by itself.

## 6. Approaches considered

### 6.1 Recommended: Codex manual vision over the complete ledger

Codex examines every ordered source image in the existing review workflow and
records a human-auditable visual ledger before drafting prose. This gives the
reviewer semantic depth, preserves chronology, makes omissions detectable,
and is honest about the result being a reference artifact. It is slower and
requires human review, but it does not require production credentials or
pretend that a manual observation was provider evidence.

### 6.2 Runtime multimodal provider first

This would send the complete chapter through the production vision adapter and
persist a current `StoryAnalysis`. It is the correct eventual production
path, but it is deferred here because provider credentials/capability and
current rights readiness are not established. Using it now would change the
production evidence boundary and exceed this review-only objective.

### 6.3 Metadata-only or filename-driven narration

This is rejected. Asset names, source order, existing crop coordinates, and
manual motion metadata do not contain enough reliable semantic information to
produce a complete causal narrative. Metadata may verify lineage and order;
it may not substitute for examining the panels.

## 7. Review and approval lifecycle

The bundle is immutable by revision. A revision has a canonical input-ledger
hash, narrative hash, display hash, QC hash, and a new revision ID. Editing
spoken text or panel understanding creates a new revision and retains the
previous bundle for audit; it does not mutate an approved revision in place.

States are:

- `DRAFT`: panel ledger or narrative is still being assembled;
- `QC_BLOCKED`: deterministic blocking findings exist;
- `PENDING_EDITORIAL_REVIEW`: QC is clear and a human has not yet decided;
- `APPROVED_REFERENCE_ONLY`: a human explicitly accepted this local review
  revision for reference discussion, never for production execution;
- `REJECTED`: a human rejected it with nonempty reasons;
- `REVISED`: a new revision superseded this one, with a link to the prior ID.

`APPROVED_REFERENCE_ONLY` must include a nonempty reviewer label,
`reviewed_at`, the exact revision hash, and an explicit statement that the
approval is not `SCRIPT_APPROVED`, not provider evidence, and not publication
approval. A rejected or edited revision cannot retain approval. No state in
this lifecycle writes to the application database or unlocks voice, timeline,
render, or publication.

## 8. Testing and acceptance criteria

The reference run is accepted only when all of these checks pass:

### 8.1 Ledger and provenance

- The input ledger contains exactly source orders `0..23` once each.
- All 24 source hashes and dimensions verify before review.
- Story coverage is exactly orders `1..23`; order `0` is explicitly excluded
  as title/front matter.
- The canonical ledger hash is recorded, and a second validation with the same
  files produces the same hash.
- No random/sample selection, filename matching, or unrecorded panel is used.
- Provenance is exactly `codex_manual_vision_reference_v1`, with
  `production_evidence=false` and `publish_allowed=false`.

### 8.2 Visual understanding and causal reconciliation

- There are 24 panel-understanding records in chronological order.
- Every story beat, causal edge, material claim, and interpretation has exact
  story-panel references.
- Unknowns are qualified; no identity, relationship, motive, event, or
  causality is invented.
- No error or failure record contains copied speech-balloon text, raw source
  text, or full narration text.

### 8.3 Sharp Friend narration

- There are 4–6 passages with nonfixed semantic roles.
- Spoken text is English, punctuation-bearing, and unchanged after QC.
- Every passage has nonempty claim and evidence references.
- Claims are covered by the ordered visual ledger and interpretations are
  qualified.
- Copied dialogue, CTA, generic hype, fixed catchphrase, and unsupported
  certainty are rejected.
- The ending-kind contract is satisfied without manufacturing a question.
- 90–125 words is reported as the target range; outside it is a visible
  warning, not an unannounced rewrite or false production pass.

### 8.4 Display separation

- Every emitted cue is one uppercase Unicode-alphanumeric word with no
  punctuation or whitespace.
- Cue order maps back to spoken-token order.
- Punctuation removal never changes `narration_spoken.txt`.
- No subtitle timing, ASS/SRT file, audio stream, video, karaoke highlight,
  or render claim is produced by this spec.

### 8.5 Deterministic QC and human review

- The same immutable input bundle yields the same structural and naturalness
  QC report and canonical report hash.
- Warnings and blockers are distinct and human-readable.
- No automatic prose repair occurs.
- A clear QC report reaches `PENDING_EDITORIAL_REVIEW`; only an explicit human
  decision may move it to `APPROVED_REFERENCE_ONLY` or `REJECTED`.
- Existing Sharp Friend/v2 compatibility tests remain green; the manual
  artifact does not alter default v2 behavior.

The repository's existing narrative compatibility checks remain the baseline
before review handoff:

```text
tests/test_narrative_identity.py
tests/test_narrative_pipeline.py
tests/test_narrative_qc.py
tests/test_narrative_review.py
tests/test_vision_synthesis.py
```

The complete non-slow suite, Ruff, compileall, diff checks, and secret-scope
review remain required for any future code-bearing implementation. This
spec-only checkpoint does not execute a provider or render a chapter.

## 9. Artifact, privacy, and rights policy

Review artifacts stay in the existing local ignored data/review area. The
bundle may contain sanitized JSON, Markdown, the spoken narration text, and
the display-word list needed for editorial review. It must not be committed
or pushed unless a later explicit task authorizes a small sanitized fixture.

Never commit or transport as source-controlled content:

- source images, contact sheets, MP4/WAV/MP3 files, or rendered media;
- databases, SQLite WAL/SHM files, credentials, API keys, `.env` files, or
  authorization headers;
- raw provider payloads, hidden prompt material, copied balloon dialogue, or
  full source text in error logs;
- runtime manifests that contain secret material or absolute machine paths.

The chapter remains `internal review only`; `publish_allowed` stays `false`.
The manual narrative is not a rights determination and does not authorize
public upload.

## 10. Explicitly deferred and forbidden work

This design does not authorize:

- production multimodal provider calls or provider credential configuration;
- converting manual observations into `vision_evidence_v2`,
  `editorial_full_panel_evidence_v1`, `StoryAnalysis`, or `PanelRegion` rows;
- voice selection, neural TTS, espeak, synthetic audio, audio mixing, or
  audition generation;
- subtitle timing, ASS/SRT export, karaoke, video rendering, FFmpeg, or media
  generation;
- ORM/schema/migration/database changes;
- UI, API, background jobs, publication, upload, scheduling, or channel
  credentials;
- source-image mutation, global cropping decisions, or creative panel
  selection beyond the exact chronological ledger;
- writing an implementation plan before the user reviews this spec.

The next decision after spec review is whether to authorize a separate
implementation slice for this offline review bundle. Even if authorized,
voice and production evidence remain separate later gates.

## 11. Reproducibility and handoff

The spec checkpoint is reproducible from a clean `main` baseline. The spec
branch and `main` publication must contain only this Markdown file. A later
review-run handoff records:

- source ledger path and SHA-256;
- exact source order coverage and title exclusion;
- review bundle path and canonical bundle hashes;
- Sharp Friend prompt/profile hashes;
- QC counts, warnings, blockers, and approval state;
- explicit `production_evidence=false`, `publish_allowed=false`, and rights
  state;
- the Git commit that defines the review contract.

The publication workflow is non-destructive: verify a clean branch, commit the
spec, push the short-lived `codex/real-chapter-narrative-review-spec` branch,
fetch `origin/main`, integrate with a fast-forward when possible or a normal
non-destructive merge only if `main` advanced, push `main:main` without force,
tags, or `--all`, and leave the worktree on clean `main` tracking
`origin/main`. The spec branch remains available for review.

## 12. Self-review result

- No placeholder marker, unresolved decision, or incomplete requirement remains
  in this design.
- Manual reference provenance is explicitly separated from production visual
  evidence and cannot satisfy production gates.
- Source order `0` is inspected for completeness but excluded from the
  23-panel story scope; orders `1..23` are mandatory and exact.
- Spoken narration remains punctuation-bearing; display words are derived
  independently and no audio/video timing is invented.
- QC can warn about target word count/rhythm without silently rewriting prose;
  evidence, copied dialogue, unsupported claims, and malformed endings block.
- Artifact, rights, provider, credential, database, media, voice, and
  publication boundaries are explicit.
