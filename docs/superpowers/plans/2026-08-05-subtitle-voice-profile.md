# Subtitle and Voice Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep punctuation-rich spoken_text for TTS prosody while producing punctuation-free display_text for every visible subtitle/export surface, and add a durable four-voice audition and selection lifecycle that blocks final rendering without an approved immutable voice profile.

**Architecture:** Consume the RECONCILED StoryAnalysis and evidence contracts from Plan 1. Add a word-timing-aware text transform in app/services/timeline.py, make render/export/API surfaces use display_text, persist one workspace-scoped VoiceProfile model through a focused migration, and use the existing app/services/tts.py HttpProvider and OmniVoice endpoint through a new audition service. Auditions compare voice characteristics only; they do not represent chapter coverage.

**Tech Stack:** Existing Python/SQLAlchemy/Alembic application, current subtitle/timeline/render/export/API modules, existing OmniVoice HTTP endpoint and HttpProvider, FFmpeg utilities already present, pytest, Ruff, and compileall. Do not add a TTS provider or edit OmniVoice service/user data.

## Global Constraints

- Implement docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md and the interfaces from Plan 1; do not redesign the story engine.
- spoken_text retains Unicode punctuation for TTS. display_text removes every Unicode punctuation code point, including apostrophes, quotation marks, em dashes, and hyphens, while retaining the same word sequence and timing token alignment.
- Every displayed, burned, SRT, and ASS string is derived from display_text and contains no Unicode punctuation. The TTS request receives spoken_text.
- When voice is implemented, burned subtitles must use karaoke highlighting: the word currently being spoken is highlighted yellow while the remaining visible words stay white. Highlight timing must follow measured word timings from the selected voice audio, not guessed cue fractions; it must preserve the same display-word/source-word mapping and never alter spoken_text.
- Four equal-length English audition samples use identical text, candidate settings, speed normalization, loudness normalization, and a persisted manifest. Candidate characters are fixed: calm documentarian, conversational analyst, cinematic storyteller, and sharp mystery narrator.
- Auditions are required when no approved profile exists, and again only after explicit user request or configuration invalidation. A selected profile is immutable and reusable for later chapters/renders.
- Final render is blocked without a selected approved profile. A profile selection cannot silently happen through a default candidate.
- Runtime audition audio and manifests may exist under runtime data but are never staged or committed. Do not alter OmniVoice service files, user data, credentials, or existing provider abstractions beyond calling them.
- Use TDD. Each task has explicit red and green commands and a focused commit. Preserve unrelated changes.

## Dependencies and Ownership

- Plan 1 must be reviewed and green first. Plan 2 consumes StoryAnalysis state, script passages, and evidence IDs but does not alter their meaning.
- Plan 2 owns app/services/timeline.py, app/services/tts.py call-site changes only, app/services/voice_auditions.py, app/services/voice_profiles.py, the VoiceProfile model addition after Plan 1's model commit, one VoiceProfile migration, related API schema/router/UI hooks, and subtitle/voice tests.
- Plan 3 owns motion files and cannot be changed by Plan 2.
- Plan 4 owns final cross-subsystem gate orchestration and rollout docs. It calls the profile gate exposed here.
- The sequential model ownership boundary is explicit: Plan 1 finishes its StoryAnalysis and PanelRegion migration first; Plan 2 then extends app/models.py with VoiceProfile in its own commit.

## Stable Interfaces

Implement these interfaces before wiring all callers. VoiceProfile is defined by Task 3; this import becomes valid after the Task 3 migration is applied.

    from dataclasses import dataclass
    from pathlib import Path
    from typing import Mapping, Sequence
    from sqlalchemy.orm import Session
    from app.models import VoiceProfile

    @dataclass(frozen=True)
    class TimedWord:
        text: str
        start: float
        end: float
        source_index: int

    @dataclass(frozen=True)
    class DisplayTextResult:
        spoken_text: str
        display_text: str
        words: tuple[TimedWord, ...]
        display_word_to_source: tuple[int, ...]

    def display_text_from_spoken(
        spoken_text: str,
        words: Sequence[TimedWord],
    ) -> DisplayTextResult:
        ...

    def validate_display_text(text: str) -> list[str]:
        ...

    VOICE_CANDIDATES: tuple[str, ...] = (
        "calm_documentarian",
        "conversational_analyst",
        "cinematic_storyteller",
        "sharp_mystery_narrator",
    )

    def auditions_required(
        *,
        selected_profile_exists: bool,
        explicit_request: bool,
        profile_invalidated: bool,
    ) -> bool:
        ...

    def build_audition_manifest(
        *,
        script_text: str,
        target_voice_settings: Mapping[str, object],
        candidate_ids: Sequence[str] = VOICE_CANDIDATES,
    ) -> dict[str, object]:
        ...

    def select_voice_profile(
        db: Session,
        *,
        workspace_id: str,
        audition_manifest_id: str,
        candidate_id: str,
        actor_id: str,
    ) -> VoiceProfile:
        ...

    def require_voice_profile(db: Session, workspace_id: str) -> VoiceProfile:
        ...

The profile gate returns a stable machine-readable reason such as voice_profile_missing or voice_profile_invalidated.

### Task 1: Define failing Unicode and timing tests

**Files:**
- Add tests/test_subtitle_unicode.py.
- Add tests/test_subtitle_timing_map.py.
- Extend existing subtitle/timeline fixtures only with small source-controlled data.

- [ ] Add examples for commas, curly quotes, contractions, em dashes, hyphens, Unicode ellipsis, and non-ASCII punctuation.
- [ ] Keep separate Unicode delimiter tests using Python escapes: em dash \u2014 and curly quotes \u201c and \u201d.
- [ ] Assert this transformation exactly:
  spoken: He said, "Well-known - don't panic."
  display: He said Wellknown dont panic
- [ ] Assert punctuation removal uses unicodedata.category(character).startswith("P") for all Unicode punctuation categories, not a small ASCII replacement list.
- [ ] Assert each display word maps to one source word index and keeps the original start/end timing. A deleted punctuation character must never shift a word boundary.
- [ ] Assert TTS payload tests retain the original punctuation while subtitle payload tests receive display_text.
- [ ] Add a karaoke timing regression fixture: the active spoken display word is yellow, inactive words are white, and each highlight transition matches measured word start/end times.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_subtitle_unicode.py tests/test_subtitle_timing_map.py -q

  Expected RED: tests collect and fail because the Unicode-safe transform and timing map are absent or current normalization leaves punctuation.
- [ ] Commit only the red tests and fixtures:

    git add tests/test_subtitle_unicode.py tests/test_subtitle_timing_map.py
    git diff --cached --check
    git commit -m "test: define punctuation-free subtitle contract"

### Task 2: Implement spoken/display text separation through timeline and export

**Files:**
- Modify app/services/timeline.py.
- Modify the subtitle assembly path in app/services/render.py.
- Modify the current SRT/ASS/export schema path identified by existing imports.
- Add or extend tests/test_timeline.py, tests/test_render.py, and tests/test_subtitle_exports.py.

Use a single transform at the timeline boundary:

    import unicodedata

    def _strip_unicode_punctuation(text: str) -> str:
        return "".join(
            character
            for character in text
            if not unicodedata.category(character).startswith("P")
        )

    def display_text_from_spoken(
        spoken_text: str,
        words: Sequence[TimedWord],
    ) -> DisplayTextResult:
        display_words: list[str] = []
        source_indexes: list[int] = []
        for word in words:
            cleaned = _strip_unicode_punctuation(word.text)
            if cleaned:
                display_words.append(cleaned)
                source_indexes.append(word.source_index)
        return DisplayTextResult(
            spoken_text=spoken_text,
            display_text=" ".join(display_words),
            words=tuple(words),
            display_word_to_source=tuple(source_indexes),
        )

- [ ] Run the red command from Task 1 and then the existing timeline/render tests:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_subtitle_unicode.py tests/test_subtitle_timing_map.py tests/test_timeline.py tests/test_render.py -q

  Expected RED: at least one current displayed/burned/exported string contains punctuation or loses a timing index.
- [ ] Keep spoken_text on the cue and TTS payload. Derive display_text once and pass only display_text to build_ass, SRT, API subtitle responses, and any burned-text filter.
- [ ] Build the ASS karaoke surface from measured word timings: active word yellow, already-spoken and upcoming words white, with deterministic transitions and no punctuation reintroduced into display text.
- [ ] Preserve cue timing and source indexes while cleaning each word. Do not split or merge words as a side effect of punctuation removal.
- [ ] Make validate_display_text return the exact offending Unicode code point categories and cue identifier. The validator is blocking for display/export paths.
- [ ] Keep existing 47 word and two-line cue constraints. Run the projects current semantic-boundary logic after punctuation cleaning so display output does not create a new dangling token.
- [ ] Add tests for contractions and em dashes in TTS payloads, and for display, SRT, ASS, API, and burn-in strings containing no punctuation categories.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_subtitle_unicode.py tests/test_subtitle_timing_map.py tests/test_timeline.py tests/test_render.py tests/test_subtitle_exports.py -q

  Expected GREEN: all spoken/display, timing, cue, render, API, SRT, and ASS assertions pass.
- [ ] Commit:

    git add app/services/timeline.py app/services/render.py app/schemas.py app/routers/pipeline.py tests/test_timeline.py tests/test_render.py tests/test_subtitle_exports.py
    git diff --cached --check
    git commit -m "feat: separate spoken and display subtitle text"

### Task 3: Persist immutable workspace-scoped VoiceProfile

**Files:**
- After Plan 1's model commit, modify app/models.py to add VoiceProfile.
- Add alembic/versions/c8d2f1a4b7e9_add_voice_profiles.py.
- Add app/services/voice_profiles.py.
- Add tests/test_voice_profile_persistence.py and tests/test_voice_profile_migration.py.

Use the existing Project-to-Workspace relation to scope profiles at workspace level. Store:
workspace_id, id, candidate_id, profile_content_hash, provider_type, voice_id, settings_json, source_audition_manifest_id, selection_provenance_json, selected_by, selected_at, invalidated_at, and created_at.

The immutable content hash covers canonical candidate ID, provider type, voice ID, normalized settings, source audition manifest ID, and selection provenance. A selected row is never edited in place.

- [ ] Write migration tests first. Upgrade creates the table and indexes; round-trip persists a selected profile; downgrade removes only VoiceProfile objects.
- [ ] Write service tests for hash stability, candidate validation, workspace isolation, immutable settings/hash, selection provenance, and invalidation without mutation.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_voice_profile_persistence.py tests/test_voice_profile_migration.py -q

  Expected RED: collection succeeds and fails because VoiceProfile, the migration, and the service boundary do not exist.
- [ ] Add the model and migration using the actual current Alembic predecessor. Add indexes for workspace_id, profile_content_hash, and invalidated_at. Enforce candidate_id membership in VOICE_CANDIDATES at the service boundary.
- [ ] Implement canonical JSON hashing:

    def profile_content_hash(
        *,
        candidate_id: str,
        provider_type: str,
        voice_id: str,
        settings: Mapping[str, object],
        audition_manifest_id: str,
        provenance: Mapping[str, object],
    ) -> str:
        payload = {
            "audition_manifest_id": audition_manifest_id,
            "candidate_id": candidate_id,
            "provenance": provenance,
            "provider_type": provider_type,
            "settings": settings,
            "voice_id": voice_id,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

- [ ] Implement select_voice_profile as an insert-only operation. Reject selection if the audition manifest is not complete, candidate is absent, or profile already differs from the requested immutable content.
- [ ] Implement explicit invalidation by setting invalidated_at on the selected record and requiring a new audition manifest. Never overwrite profile settings or its content hash.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_voice_profile_persistence.py tests/test_voice_profile_migration.py -q

  Expected GREEN: migration, hash, scope, immutability, provenance, selection, and invalidation tests pass.
- [ ] Commit:

    git add app/models.py alembic/versions/c8d2f1a4b7e9_add_voice_profiles.py app/services/voice_profiles.py tests/test_voice_profile_persistence.py tests/test_voice_profile_migration.py
    git diff --cached --check
    git commit -m "feat: persist immutable voice profiles"

### Task 4: Build the four-audition lifecycle through OmniVoice

**Files:**
- Add app/services/voice_auditions.py.
- Add tests/test_voice_auditions.py.
- Add tests/test_voice_audition_manifest.py.
- Modify only the existing audition/API router and schema files needed to expose the endpoints.
- Do not modify OmniVoice service or user-data files.

Use the current HttpProvider and its current HTTP endpoint. The audition service accepts one fixed English script, one target speed/loudness policy, and exactly these four candidates. It creates runtime files only under the applications existing runtime data root.

Required service boundary:

    @dataclass(frozen=True)
    class AuditionResult:
        manifest_id: str
        candidate_id: str
        audio_path: Path
        duration_seconds: float
        integrated_lufs: float
        true_peak_dbtp: float
        valid: bool
        blocking_reasons: tuple[str, ...]

    def build_audition_manifest(
        *,
        script_text: str,
        target_voice_settings: Mapping[str, object],
        candidate_ids: Sequence[str] = VOICE_CANDIDATES,
    ) -> dict[str, object]:
        ...

    def create_voice_auditions(
        db: Session,
        *,
        workspace_id: str,
        script_text: str,
        target_voice_settings: Mapping[str, object],
        actor_id: str,
    ) -> list[AuditionResult]:
        ...

- [ ] Write tests first with a fake HttpProvider that records text, voice ID, speed, and output path. Assert each candidate receives identical spoken script and target settings, differing only in candidate voice ID.
- [ ] Assert manifest equality for script SHA-256, candidate ordering, target settings, normalization policy, prompt version, and measured-duration policy.
- [ ] Assert output paths are under runtime data and no generated audio is inside a source-controlled path.
- [ ] Use target duration tolerance max(0.15, target_duration * 0.03) and pair spread tolerance max(0.10, target_duration * 0.02). If a candidate falls outside the measured-duration gate, mark it invalid; do not apply destructive time-stretch.
- [ ] Normalize loudness and measure true peak using the existing FFmpeg/audio helpers. Audition target policy records approximately -14 LUFS and true peak at most -1.5 dBTP, but a failed measurement blocks selection rather than being hidden.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_voice_auditions.py tests/test_voice_audition_manifest.py -q

  Expected RED: tests collect and fail because the audition service and manifest are absent.
- [ ] Implement the four fixed candidates, same text, normalized speed/loudness, duration measurement, manifest persistence, and failure cleanup. A provider failure records the candidate reason and cleans only the candidates temporary runtime file.
- [ ] Add endpoints:
  POST /api/workspaces/{workspace_id}/voice-auditions
  POST /api/workspaces/{workspace_id}/voice-profile/select
  The select request contains audition_manifest_id, candidate_id, actor_id, and optional editorial note; it must not accept arbitrary provider credentials or a free-form voice ID.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_voice_auditions.py tests/test_voice_audition_manifest.py tests/test_voice_api.py -q

  Expected GREEN: four candidates, identical text, manifest equality, normalization, duration gate, failure cleanup, and API tests pass.
- [ ] Commit:

    git add app/services/voice_auditions.py app/routers/pipeline.py app/schemas.py tests/test_voice_auditions.py tests/test_voice_audition_manifest.py tests/test_voice_api.py
    git diff --cached --check
    git commit -m "feat: add reusable voice audition lifecycle"

### Task 5: Enforce selection, reuse, invalidation, and final-render blocking

**Files:**
- Modify app/services/voice_profiles.py.
- Modify the final-render preflight boundary in app/services/render.py or the existing render gate module.
- Add tests/test_voice_profile_gate.py.
- Add tests/test_voice_profile_reuse.py.

Implement:

    def auditions_required(
        *,
        selected_profile_exists: bool,
        explicit_request: bool,
        profile_invalidated: bool,
    ) -> bool:
        if explicit_request or profile_invalidated:
            return True
        return not selected_profile_exists

    def require_voice_profile(db: Session, workspace_id: str) -> VoiceProfile:
        profile = latest_selected_profile(db, workspace_id)
        if profile is None or profile.invalidated_at is not None:
            raise VoiceProfileRequired("voice_profile_missing")
        return profile

- [ ] Add red tests for no profile, invalidated profile, selected profile reuse, explicit re-audition, and selection of a failed audition.
- [ ] Add a final-render preflight test that blocks with voice_profile_missing before audio/video work begins.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_voice_profile_gate.py tests/test_voice_profile_reuse.py -q

  Expected RED: current render preflight has no immutable profile gate and no invalidation distinction.
- [ ] Add the gate before final timeline/audio/render creation. Preview mode may remain fast, but it must expose that the final profile gate has not been satisfied.
- [ ] Ensure a selected profile is reused on later chapters without re-audition. Require a new four-sample manifest only on explicit user request or explicit configuration invalidation.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_voice_profile_gate.py tests/test_voice_profile_reuse.py tests/test_render.py -q

  Expected GREEN: missing, invalidated, reuse, explicit-request, and final-preflight tests pass.
- [ ] Commit:

    git add app/services/voice_profiles.py app/services/render.py tests/test_voice_profile_gate.py tests/test_voice_profile_reuse.py
    git diff --cached --check
    git commit -m "feat: block final render without selected voice"

### Task 6: Verify Plan 2 and stop for user voice choice

- [ ] Run the focused subtitle and voice suite:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_subtitle_unicode.py tests/test_subtitle_timing_map.py tests/test_timeline.py tests/test_render.py tests/test_subtitle_exports.py tests/test_voice_profile_persistence.py tests/test_voice_profile_migration.py tests/test_voice_auditions.py tests/test_voice_audition_manifest.py tests/test_voice_api.py tests/test_voice_profile_gate.py tests/test_voice_profile_reuse.py -q

  Expected GREEN: Unicode categories, punctuation routing, token timing, cue constraints, four-audition manifest, measurement gate, persistence, immutability, reuse, invalidation, and final-render blocking pass.
- [ ] Run static checks:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/ruff check app tests
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m compileall -q app tests
    git diff --check

  Expected GREEN: Ruff is clean, compileall exits zero, and diff-check prints no lines.
- [ ] Audit Git paths:

    git status --short --untracked-files=all
    git diff --name-only --cached

  Expected GREEN: only intended source/tests/docs are staged; runtime audio, databases, user data, credentials, and OmniVoice files are absent.
- [ ] Record the audition manifest, four runtime audio paths, measured durations, loudness, true peaks, candidate validity, and user-selection state. The samples compare voice characteristics only and do not represent chapter coverage.
- [ ] Record the selected voice's measured word timings as the provenance for karaoke transitions; if word timings are unavailable or invalid, block karaoke burn-in rather than guessing highlight timing.
- [ ] Stop and wait for explicit user selection. Do not begin a final chapter render or select a default candidate.
- [ ] Commit the independently reviewable Plan 2 slice:

    git add app/models.py alembic/versions/c8d2f1a4b7e9_add_voice_profiles.py app/services/timeline.py app/services/render.py app/services/voice_profiles.py app/services/voice_auditions.py app/routers/pipeline.py app/schemas.py tests/test_subtitle_unicode.py tests/test_subtitle_timing_map.py tests/test_timeline.py tests/test_render.py tests/test_subtitle_exports.py tests/test_voice_profile_persistence.py tests/test_voice_profile_migration.py tests/test_voice_auditions.py tests/test_voice_audition_manifest.py tests/test_voice_api.py tests/test_voice_profile_gate.py tests/test_voice_profile_reuse.py
    git diff --cached --check
    git commit -m "feat: complete subtitle and voice profile slice"

## Stop Point

The Plan 2 handoff reports the exact display-text punctuation audit, timing-map results, audition manifest ID, four candidate IDs and measured metrics, invalid candidates, runtime paths, selected profile state, and final-render gate state. Stop before a final render until the user explicitly selects a candidate.

## Execution Handoff

This is an executable plan. Use the required superpowers:subagent-driven-development workflow or run it inline with superpowers:executing-plans, preserving the wait-for-selection stop point.
