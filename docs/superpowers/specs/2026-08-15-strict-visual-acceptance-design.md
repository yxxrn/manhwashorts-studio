# Strict Visual Acceptance Design

## Goal

Prevent technically valid but visibly broken Shorts previews. A render must fail before delivery when subtitles leave the horizontal safe area, the requested font is not actually available, or removable panel whitespace remains visible.

## Scope

This slice changes only sentence karaoke layout, panel reframing, measured QC, and the final preview acceptance gate. Narration, cloud analysis, voice generation, rights approval, and unrelated operator features remain unchanged.

## Subtitle contract

- Use the checked-in `BarberChop.otf` file for both Pillow measurements and FFmpeg/libass rendering. Resolve and verify its actual family name; do not rely on a guessed alias or silent font fallback.
- Every rendered karaoke state, including the active word at 1.08 scale, must fit inside a 120 px horizontal safe margin at 1080x1920.
- Display remains punctuation-free, uppercase, sentence-held karaoke with a yellow active word, at most two lines, and at least two words on a continuation line.
- Split long sentences into stable semantic chunks until their measured pixel bounds fit. Spoken narration and word timing remain immutable.
- Avoid absolute ASS positioning that bypasses style margins. If positioning is required, validate the final event bounding box against the same safe rectangle.
- Missing font, unmeasurable font, overflow, excessive lines, or timing overlap is blocking. No fallback font is accepted for production preview.

## Visual framing contract

- Measure edge-connected low-information/blank space on every selected crop using the existing color-agnostic detector.
- Target at most 3% edge blank fraction. Up to 5% is allowed only when the retained area intersects protected story art and the sidecar records the evidence and reason.
- Try tighter safe crops within the existing 1.5x review upscale ceiling before accepting a crop.
- If the current panel cannot meet the limit without balloon/protected/lineage violations, select another evidence-grounded panel for the same beat.
- A crop above 5%, or an unexplained crop above 3%, blocks preview delivery. Balloon, protected-art, chronology, and lineage gates are never weakened.

## Measured QC

The QC report must derive values from the exact render inputs and output frames. It must record font file/family/hash, maximum measured text width, safe width, maximum rendered lines, minimum horizontal clearance, per-shot blank fractions, and any protected-art exception. Constants describing the intended contract must not be reported as measured facts.

Before delivery, generate a contact sheet and inspect representative boundary/key frames. The preview is accepted only when technical probing, measured subtitle checks, framing checks, and visual inspection all pass. A playable MP4 alone is not success.

## Tests and checkpoints

1. RED tests reproduce the current font-name mismatch, active-word overflow, hard-coded QC, and accepted 16% blank crop.
2. GREEN subtitle tests prove exact-font measurement and safe bounds for every active word.
3. GREEN framing tests prove the 3% target, bounded 5% protected exception, tighter-crop retry, alternative-panel selection, and fail-closed behavior.
4. Render `final_test`, inspect the contact sheet and selected frames, and save source/tests/docs in atomic commits. Runtime media, databases, source panels, and credentials remain untracked.

## Acceptance

- 50-60 second 1080x1920 silent preview with no audio stream.
- No subtitle pixels outside the 120 px horizontal safe area; maximum two lines; exact BarberChop font; yellow active-word scale remains inside bounds.
- No removable gutters or distracting blank bands; measured edge blank is at most 3%, or at most 5% with a documented protected-art exception.
- QC contains measured evidence and blocks on any violation.
- Focused tests and relevant render verification pass before commit/push. Merge to `main` only after the replacement preview is visually accepted.
