# Final Production Silent Acceptance Design

## Objective

Generate one review-only silent production preview from the ordered chapter in
`B:/Project/manhwashorts-studio/final_test`. The run must use the existing
operator/BYOK boundary, one pinned multimodal model, all reconciled material
panels, the Sharp Friend narrative contract, the regular render path, and the
sentence-chunked word-karaoke subtitle contract. It is an editorial artifact,
not a publishable or voiced deliverable.

The current checkout is re-baselined at the verified implementation parent
available when this task starts. The untracked `final_test/` input and all
runtime output remain outside Git.

## Non-negotiable boundaries

- The provider is called only through the existing encrypted BYOK and
  `OpenAICompatibleVisionProvider`/`CloudStageRunner` boundaries.
- The endpoint, API key, provider bodies, source pixels, and raw exceptions are
  never printed, persisted in plaintext, passed as process arguments, or
  committed. Provider hashes are never trusted; local canonical hashes win.
- The requested model must be present in the provider's returned model list and
  must pass the real image-plus-structured-JSON capability probe. A model name
  alone is not evidence of vision support.
- Visual evidence, causal map, and narration are separate stages using the same
  pinned model identity and versioned prompts. Every ordered material panel is
  examined; no sampling or random selection is allowed.
- `publish_allowed=false`, rights remain blocked, and the final voice/TTS/audio
  path remains disabled. The silent review may use clearly labelled provisional
  display timing, but it must never be persisted as authoritative voice timing.
- Existing `profile=None`, v2 analyzer, legacy subtitle, and regular render
  behavior remain compatible unless the explicit silent-review profile is
  selected.

## Inputs and immutable lineage

The operator resolves `final_test/` as one chapter. It validates supported
image types, deterministic filename order, byte checksums, dimensions, and
source-family grouping before any provider request. The completed strip
segmentation boundary may split long files only when local/provider boundary
reconciliation is valid; an ambiguous boundary becomes a review blocker.

The run records a small ignored manifest containing source path names, file
checksums, dimensions, ordered panel/region IDs, integer bounds, segmentation
version, coverage-map hash, provider/model identity hash, prompt versions, and
stage request counts. It does not copy source images into the bundle.

## Data flow

```text
final_test folder
  -> deterministic ingest/strip reconciliation
  -> exact PanelRegion ledger and local asset hashes
  -> visual evidence for every ordered material region
  -> local evidence/hash/balloon/protected-region reconciliation
  -> full ordered causal map and claims
  -> Sharp Friend narration + display derivation + QC
  -> exact panel candidates and Task 5/6 framing feasibility
  -> stable scenes and sentence-chunked karaoke
  -> regular render with silent_reference_review=True
  -> isolated MP4 + compact audit/QC bundle
```

The provider returns geometry and claims only. `visual_scoring` validates
lineage and computes evidence hashes. Unknown balloon geometry is a hard
reference blocker; known nonempty balloon overlap is rejected; protected
subject/action retention is checked locally. Narrative claims must reference
the reconciled evidence graph and all panel coverage gates must pass.

## Local operator context

The local CLI must not require a browser login. On the first local run, an
explicit `ensure_local_operator_context(db)` service creates one deterministic
active local operator and one `My Workspace` owned by that operator when none
exists. It records an audit event with origin `local_operator_cli`, uses no
privilege escalation, and commits only after both rows and the audit event are
valid. Existing active users/workspaces are preserved; reruns are idempotent.
Cancellation or failed validation leaves pre-existing context and credentials
unchanged. The provider setup resumes after context creation instead of
restarting endpoint/key entry.

## Provider acceptance

The operator enters the configured endpoint and hidden key through the existing
setup flow. The CLI normalizes the display alias to the canonical LLM provider
kind, calls encrypted BYOK verification/model discovery, and retains the
selected model only if it is returned by the endpoint. The preferred model is
`ag/gemini-3.6-flash-high`; if absent or capability-invalid, the run stops with
a sanitized blocker rather than guessing another model.

The explicit capability probe uses the existing deterministic 48x48 generated
PNG and one `CloudStageRunner.run_visual_evidence` request with strict nested
visual evidence. The report records only HTTP/model outcome category, selected
model, request count, and sanitized error code. It never records raw provider
JSON or a credential hint beyond the existing safe BYOK key hint.

## Silent preview contract

The preview uses the current regular render request and renderer with:

- `profile=REFERENCE_MATCHED_SHORTS_V1`;
- `silent_reference_review=True`;
- no `audio_path`, `music_path`, TTS, voice, SFX, or audio stream;
- 1080x1920, 60 FPS, H.264 High, yuv420p;
- target duration 50--60 seconds, preferably 50--55;
- chronological material panels and exact persisted panel lineage;
- sentence-level display chunks that remain visible while word-level cues
  highlight the active word yellow and scale it by 1.08;
- uppercase, punctuation-free display text, Barber Chop font, safe 120px
  margins, readable 77px-equivalent sizing, and a hard maximum of two lines;
- stable varied low-amplitude motion, no balloon intersection, no distracting
  edge-connected blank space when feasible, and no silent legacy fallback when
  reference evidence is invalid.

Silent timing is derived from approved narration word/passage duration only for
this review artifact. It is labelled `timing_source=provisional_review_pacing`
and is rejected by the final voiced-render gate until real audio word timing is
available.

## Required artifacts

Write to a new ignored directory under `data/` with a run ID, never replacing
an earlier preview. Required files are the playable MP4, punctuated spoken
narration, independently derived display cues, source/panel ledger, visual
evidence and causal map JSON, narration/QC JSON, shot/edit plan, compact render
manifest, ffprobe JSON, blackdetect evidence, contact sheet, and representative
subtitle/crop audit frames. `publish_allowed=false` and
`approval_state=PENDING_EDITORIAL_REVIEW` remain in every relevant report.

## Failure and review policy

Any missing context, unsupported model, failed capability probe, incomplete
coverage, stale checksum, unknown balloon mask, protected-region failure,
ambiguous segmentation, unsupported narrative claim, invalid subtitle timing,
overflow, black frame, audio stream, or unsafe crop fails closed with a stable
code and a concise ignored review report. One failed chapter does not mutate or
invalidate other job state. Resume uses source/model/prompt hashes and never
reuses stale stage output.

## Future manual voice handoff (specification only)

After silent acceptance, a separate implementation slice exports an editable
`voice_script.txt` with punctuation-preserving narration and optional
configurable tags such as `[pause]`, `[short pause]`, `[breath]`, `[whisper]`,
and `[tense]`. A clean spoken file and punctuation-free display surface remain
separate; tags never enter subtitles. The CLI then enters `WAITING_FOR_VOICE`,
prints the exact export path and external-web instructions, and accepts a user
supplied WAV/MP3/M4A. A later validator checks codec/sample rate/duration,
reconciles actual word timing by provider timestamps or local forced alignment,
normalizes audio, resynchronizes karaoke/shot pacing, and preserves the pinned
voice-profile identity and manual provenance. Mismatches create an auditable
review blocker; replacement audio is a new immutable attempt. No part of this
voice flow is implemented in the silent acceptance slice.

## Verification and release

Software tests must pass before any real provider or media run. The real run
must prove the selected model's structured visual response, complete panel
coverage, deterministic QC, exact subtitle constraints, video technical
properties, zero audio streams, no black frames, and a clean isolated artifact
path. Docs and operator handoff record exact commands, hashes, counts, rollback
commit, provider request count, and remaining blockers. Only source/tests/docs
may be committed; no source images, runtime DB, credentials, or media are
tracked. Main is fast-forwarded only after the real MP4 and audit gates pass.
