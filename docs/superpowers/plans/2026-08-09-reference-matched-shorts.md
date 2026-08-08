# Reference-Matched Shorts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the selectable `reference_matched_shorts_v1` profile that understands every reconciled chapter panel, writes an evidence-linked English Cinematic Story Detective recap, and renders a stable 38-50 second Short with rapid semantic panel cuts and one-word captions.

**Architecture:** Keep the August 5 vision-first coverage/evidence chain as the only story source. Add one immutable profile contract and profile-aware adapters around narration, shot planning, captions, motion, QC, and rendering; legacy/default behavior remains unchanged when the profile is absent. Every task is a TDD slice reviewed and committed on the authoritative VPS before the next task starts.

**Tech Stack:** Python, FastAPI, SQLAlchemy/Alembic, Pillow/OpenCV already in the repository, existing OpenAI-compatible vision abstraction, FFmpeg/libass, pytest, Ruff, and compileall.

## Global Constraints

- Implement `docs/superpowers/specs/2026-08-09-reference-matched-shorts-design.md` and preserve `docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md`.
- Work and verification occur only in `/home/yusronrohmani/manhwashorts`; Windows paths are mechanical transport only. Every PowerShell SSH invocation ends in `2>&1`.
- Do not sample, randomize, choose representative panels, use pasted recap text, or silently fall back to text analysis. Coverage must equal `1.0`, unresolved area must equal `0`, and every canonical panel, observation, claim, and clause must reconcile.
- Spoken narration is English. `spoken_text` keeps punctuation for TTS; visible `display_text` is a separate uppercase, punctuation-free, exactly-one-word cue stream.
- Reference profile: 38-50 seconds; 28-36 shots; at least 85% holds of 0.9-1.5 seconds; at most 15% evidence-reasoned emphasis shots of 1.6-2.2 seconds; mean shot duration 1.05-1.65 seconds; at least 85% hard cuts; only semantic 0.12-0.18 second transitions.
- No shake, micro-shake, impact-shake, oscillation, orbit, whip, or random motion. Normal zoom is at most 1.06; impact emphasis is at most 1.08; one monotonic camera intent per shot.
- A canonical panel is used at most twice, never consecutively; a second use needs a different ROI, different narrative function, and a persisted reason.
- No intro, logo card, reference cover, top black sentence caption, or generic CTA. The ending is an evidence-backed cliffhanger or loop.
- Final technical profile is 1080x1920, 30 fps, H.264, yuv420p; audio targets -14 LUFS and true peak at most -1.5 dBTP. Do not add unlicensed music or SFX.
- Rights/source status remains authoritative. Similarity never grants publication or monetization; a rights failure keeps `publish_allowed=false`.
- Runtime media, databases, credentials, user data, manifests, and sidecars remain outside Git. Never reset, overwrite, or clean unrelated work. Do not push without user approval.

## Dependency Graph

    Task 3 commit 141d81b (complete)
      -> Task 0 lineage persistence (Task 3.1)
      -> remaining Aug 5 Plan 1 vision adapter/evidence tasks
      -> Task 1 immutable profile
      -> Task 2 complete reference observations
      -> Task 3 evidence-linked narrative clauses
      -> Task 4 semantic shot cadence
      -> Task 5 one-word captions
      -> Task 6 stable motion
      -> Task 7 voice-profile gate
      -> Task 8 profile QC and sidecars
      -> Task 9 isolated VPS render audit
      -> Task 10 rollout docs and stop before push

End-to-end success is impossible until vision capability, reconciled observations, human script approval, selected voice profile, caption QC, motion QC, render QC, and rights gates have authoritative evidence.

---

### Task 0: Persist upload lineage into SourceAsset (Task 3.1)

**Files:**
- Modify: `app/routers/projects.py:265-310`
- Add: `tests/test_source_lineage_persistence.py`

**Interfaces:**
- Consumes: `ingest.IngestedAsset.original_checksum`, `original_width`, `original_height`, `source_bounds`, `strip_order`, `region_order`, `trim_classification`, and `coverage_map_hash`.
- Produces: the same values in `SourceAsset`, with `source_bounds_json={"x", "y", "width", "height"}`.

- [ ] **Step 1: Write the failing API persistence test**

    def test_upload_persists_every_ingest_lineage_field(auth_client, monkeypatch):
        from app.db import SessionLocal
        from app.models import SourceAsset
        from app.services.ingest import IngestedAsset

        result = IngestedAsset(
            type="image", original_filename="page_p01.png", mime_type="image/png",
            storage_key="tests/page_p01.png", size_bytes=4, checksum="derived",
            width=900, height=1200, original_checksum="original", original_width=900,
            original_height=5000, source_bounds=(0, 800, 900, 1200), strip_order=2,
            region_order=4, trim_classification="canonical_panel",
            coverage_map_hash="coverage-hash",
        )
        monkeypatch.setattr(
            "app.routers.projects.ingest.ingest_upload_parts", lambda *args: [result]
        )
        project_id = auth_client.post("/api/projects", json={"title": "Lineage"}).json()["id"]
        response = auth_client.post(
            f"/api/projects/{project_id}/assets/upload",
            files={"files": ("page.png", b"data", "image/png")},
            data={"rights_owner": "Tester", "license_type": "owned", "declared": "true"},
        )
        assert response.status_code == 201
        asset_id = response.json()[0]["id"]
        with SessionLocal() as db:
            asset = db.get(SourceAsset, asset_id)
            assert asset.original_checksum == "original"
            assert (asset.original_width, asset.original_height) == (900, 5000)
            assert asset.source_bounds_json == {"x": 0, "y": 800, "width": 900, "height": 1200}
            assert (asset.strip_order, asset.region_order) == (2, 4)
            assert asset.trim_classification == "canonical_panel"
            assert asset.coverage_map_hash == "coverage-hash"

- [ ] **Step 2: Run RED**

    cd /home/yusronrohmani/manhwashorts
    env PATH=/home/yusronrohmani/.local/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/pytest tests/test_source_lineage_persistence.py -q

  Expected: one body-level failure showing at least one SourceAsset lineage field retained its default.

- [ ] **Step 3: Copy the complete lineage in the upload loop**

    x, y, source_width, source_height = result.source_bounds
    asset = SourceAsset(
        original_checksum=result.original_checksum,
        original_width=result.original_width,
        original_height=result.original_height,
        source_bounds_json={
            "x": x, "y": y, "width": source_width, "height": source_height,
        },
        strip_order=result.strip_order,
        region_order=result.region_order,
        trim_classification=result.trim_classification,
        coverage_map_hash=result.coverage_map_hash,
    )

- [ ] **Step 4: Run GREEN and regressions**

    env PATH=/home/yusronrohmani/.local/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/pytest tests/test_source_lineage_persistence.py tests/test_segmentation.py tests/test_strips.py tests/test_api.py -q
    .venv/bin/ruff check app/routers/projects.py tests/test_source_lineage_persistence.py
    .venv/bin/python -m compileall -q app/routers/projects.py tests/test_source_lineage_persistence.py
    git diff --check

  Expected: lineage and existing upload/strip regressions pass.

- [ ] **Step 5: Commit the two-file allowlist**

    git add app/routers/projects.py tests/test_source_lineage_persistence.py
    git diff --cached --check
    git commit -m "fix: persist source coverage lineage on upload"

---

### Task 1: Add the immutable reference editorial profile

**Files:**
- Add: `app/services/reference_profile.py`
- Add: `tests/test_reference_profile.py`

**Interfaces:**
- Produces: `ReferenceProfileConfig`, `REFERENCE_MATCHED_SHORTS_V1`, `canonical_profile_json()`, `profile_hash()`, and `resolve_reference_profile()`.
- Legacy callers receive `None` and keep current behavior.

- [ ] **Step 1: Write RED tests for all immutable thresholds, full hash coverage, and explicit selection**

    def test_reference_profile_is_complete_and_hash_stable():
        from app.services.reference_profile import REFERENCE_MATCHED_SHORTS_V1, profile_hash
        profile = REFERENCE_MATCHED_SHORTS_V1
        assert (profile.duration_min_s, profile.duration_max_s) == (38.0, 50.0)
        assert (profile.shot_min, profile.shot_max) == (28, 36)
        assert profile.caption_words_per_cue == 1
        assert profile.final_pixel_format == "yuv420p"
        assert profile.audio_lufs_target == -14.0
        assert profile.audio_true_peak_max_db == -1.5
        assert profile_hash(profile) == profile_hash(profile)

- [ ] **Step 2: Run RED**

    .venv/bin/pytest tests/test_reference_profile.py -q

  Expected: import failure for `app.services.reference_profile`.

- [ ] **Step 3: Implement the complete frozen config and canonical hash**

    @dataclass(frozen=True)
    class ReferenceProfileConfig:
        profile_id: str; version: str
        duration_min_s: float; duration_max_s: float
        shot_min: int; shot_max: int
        hold_min_s: float; hold_max_s: float
        emphasis_min_s: float; emphasis_max_s: float
        hold_ratio_min: float; emphasis_ratio_max: float
        mean_shot_min_s: float; mean_shot_max_s: float
        hard_cut_ratio_min: float
        transition_min_s: float; transition_max_s: float
        normal_zoom_max: float; impact_zoom_max: float
        caption_words_per_cue: int; caption_uppercase: bool
        caption_unicode_punctuation_allowed: bool; caption_top_sentence_allowed: bool
        caption_safe_region: tuple[float, float, float, float]
        caption_anchor: tuple[float, float]; caption_outline_pixels: int
        caption_font_weight: str; caption_primary_color: str
        caption_outline_color: str; caption_shadow_color: str
        caption_shadow_alpha_max: float; caption_alignment: int
        max_canonical_panel_uses: int; consecutive_panel_reuse_allowed: bool
        final_width: int; final_height: int; final_fps: int
        final_codec: str; final_codec_profile: str; final_pixel_format: str
        audio_lufs_target: float; audio_true_peak_max_db: float
        unlicensed_music_sfx_allowed: bool

    def canonical_profile_json(profile: ReferenceProfileConfig) -> str:
        return json.dumps(asdict(profile), sort_keys=True, separators=(",", ":"))

    def profile_hash(profile: ReferenceProfileConfig) -> str:
        return hashlib.sha256(canonical_profile_json(profile).encode()).hexdigest()

- [ ] **Step 4: Run GREEN, Ruff, compileall, and commit**

    .venv/bin/pytest tests/test_reference_profile.py -q
    .venv/bin/ruff check app/services/reference_profile.py tests/test_reference_profile.py
    .venv/bin/python -m compileall -q app/services/reference_profile.py tests/test_reference_profile.py
    git add app/services/reference_profile.py tests/test_reference_profile.py
    git commit -m "feat: define reference matched editorial profile"

---

### Task 2: Observe every reconciled panel for reference storytelling

**Prerequisite:** Finish and review the remaining August 5 Plan 1 vision adapter, provider capability, persistence, and reconciliation tasks. A missing image-capable provider must stop with `vision_capability_missing`.

**Files:**
- Add: `app/services/reference_story.py`
- Add: `app/prompts/reference_matched_shorts_analyzer_v1.txt`
- Modify: `app/services/vision_adapter.py`
- Add: `tests/test_reference_story_observations.py`

**Interfaces:**
- Consumes: reconciled `CoverageMap`, ordered canonical `PanelRegion` rows, and persisted vision observations.
- Produces: frozen `ReferencePanelObservation` records and one ordered `ReferenceStoryLedger` whose `panel_ids` exactly match all canonical panel IDs.

- [ ] **Step 1: Write RED coverage and no-fallback tests**

    def test_reference_story_ledger_contains_every_canonical_panel(coverage_map, observations):
        ledger = build_reference_story_ledger(coverage_map, observations)
        expected = tuple(r.region_id for r in coverage_map.regions if r.region_class == "canonical_panel")
        assert ledger.panel_ids == expected
        assert ledger.coverage_map_hash == coverage_map.map_sha256

  Monkeypatch `random.choice`, `random.sample`, and the legacy text analyzer to raise; none may be called.

- [ ] **Step 2: Run RED**

    .venv/bin/pytest tests/test_reference_story_observations.py -q

  Expected: missing reference story contract/prompt behavior.

- [ ] **Step 3: Implement strict ordered records and prompt contract**

    @dataclass(frozen=True)
    class ReferencePanelObservation:
        observation_id: str
        panel_id: str
        source_asset_id: str
        source_order: int
        visible_facts: tuple[str, ...]
        actions: tuple[str, ...]
        reactions: tuple[str, ...]
        objects: tuple[str, ...]
        dialogue_context: tuple[str, ...]
        uncertainty: tuple[str, ...]

    @dataclass(frozen=True)
    class ReferenceStoryLedger:
        coverage_map_hash: str
        panel_ids: tuple[str, ...]
        observations: tuple[ReferencePanelObservation, ...]
        ordering_fingerprint: str

  The prompt requires one structured observation for every supplied panel ID and forbids recap text, omitted panels, invented dialogue, and unsupported motive.

- [ ] **Step 4: Run GREEN and parent reconciliation regressions**

    .venv/bin/pytest tests/test_reference_story_observations.py tests/test_vision_adapter.py tests/test_segmentation_reconciliation.py -q

- [ ] **Step 5: Commit the four-file allowlist**

    git add app/services/reference_story.py app/prompts/reference_matched_shorts_analyzer_v1.txt app/services/vision_adapter.py tests/test_reference_story_observations.py
    git commit -m "feat: observe every panel for reference storytelling"

---

### Task 3: Build evidence-linked Cinematic Story Detective clauses

**Files:**
- Add: `app/services/reference_narrative.py`
- Modify: `app/services/pipeline.py`
- Add: `tests/test_reference_narrative.py`

**Interfaces:**
- Consumes: `ReferenceStoryLedger`, reconciled claims, and human-approved script state.
- Produces: `ReferenceNarrativePlan` with ordered `ReferenceNarrativeClause` records.

- [ ] **Step 1: Write RED tests for causal structure, evidence, English output, immediate hook, and cliffhanger ending**

    @dataclass(frozen=True)
    class ReferenceNarrativeClause:
        clause_id: str
        spoken_text: str
        claim_ids: tuple[str, ...]
        evidence_panel_ids: tuple[str, ...]
        visual_role: Literal["action", "reaction", "object_detail", "dialogue_face", "reveal", "establishing"]

  Tests reject missing evidence, unknown panel IDs, repeated `then we see`, generic CTA endings, intro/logo passages, unsupported facts, and non-English narration.

- [ ] **Step 2: Run RED**

    .venv/bin/pytest tests/test_reference_narrative.py -q

- [ ] **Step 3: Validate provider output rather than fabricating a heuristic recap**

    def validate_reference_narrative(plan, ledger, reconciled_claim_ids):
        known_panels = set(ledger.panel_ids)
        known_claims = set(reconciled_claim_ids)
        errors = []
        for clause in plan.clauses:
            if not clause.evidence_panel_ids or not set(clause.evidence_panel_ids) <= known_panels:
                errors.append(f"clause.evidence_missing:{clause.clause_id}")
            if not set(clause.claim_ids) <= known_claims:
                errors.append(f"clause.claim_missing:{clause.clause_id}")
        if plan.has_intro_or_logo: errors.append("reference.intro_forbidden")
        if not plan.ends_with_cliffhanger_or_loop: errors.append("reference.ending_not_cliffhanger")
        return tuple(sorted(errors))

- [ ] **Step 4: Run GREEN and commit**

    .venv/bin/pytest tests/test_reference_narrative.py tests/test_script.py tests/test_pipeline.py -q
    git add app/services/reference_narrative.py app/services/pipeline.py tests/test_reference_narrative.py
    git commit -m "feat: plan evidence linked detective narration"

---

### Task 4: Select semantic panels and reference-matched shot cadence

**Files:**
- Modify: `app/services/editorial_visual_planner.py`
- Modify: `app/services/shot_director.py`
- Add: `tests/test_reference_shot_plan.py`

**Interfaces:**
- Consumes: approved clauses, candidate panel/ROI metadata, and `ReferenceProfileConfig`.
- Produces: shot records with `clause_id`, `panel_id`, `visual_role`, `reuse_reason`, `emphasis_reason`, transition metadata, and profile hash.

- [ ] **Step 1: Write RED tests for 38-50 seconds, 28-36 shots, hold/emphasis ratios, mean duration, hard cuts, chronology, role matching, and reuse rules**

    report = measure_reference_shots(shots, REFERENCE_MATCHED_SHORTS_V1)
    assert 28 <= report.shot_count <= 36
    assert report.hold_ratio >= 0.85
    assert report.emphasis_ratio <= 0.15
    assert 1.05 <= report.mean_shot_seconds <= 1.65
    assert report.hard_cut_ratio >= 0.85
    assert report.consecutive_panel_reuse_count == 0

- [ ] **Step 2: Run RED**

    .venv/bin/pytest tests/test_reference_shot_plan.py -q

- [ ] **Step 3: Add an explicit profile path without changing legacy defaults**

    def reference_slot_durations(total_s, emphasis_indexes, profile):
        count = max(profile.shot_min, min(profile.shot_max, round(total_s / 1.35)))
        emphasis = tuple(sorted(set(i for i in emphasis_indexes if 0 <= i < count)))
        emphasis = emphasis[: math.floor(count * profile.emphasis_ratio_max)]
        emphasis_duration = 1.8
        hold_duration = (
            total_s - emphasis_duration * len(emphasis)
        ) / (count - len(emphasis))
        if not profile.hold_min_s <= hold_duration <= profile.hold_max_s:
            raise ReferenceProfileError("reference.shot_distribution")
        return tuple(
            emphasis_duration if index in emphasis else hold_duration
            for index in range(count)
        )

    def validate_second_panel_use(previous, current):
        if previous.panel_id != current.panel_id:
            return
        if previous.order_index + 1 == current.order_index:
            raise ReferenceProfileError("reference.panel_reuse_consecutive")
        if previous.visual_role == current.visual_role:
            raise ReferenceProfileError("reference.panel_reuse_same_role")
        if roi_iou(previous.roi, current.roi) >= 0.60 or not current.reuse_reason:
            raise ReferenceProfileError("reference.panel_reuse_not_distinct")

  `plan_reference_shots()` calls these helpers, preserves candidate source order, maps each clause role to the highest-scoring evidence panel/ROI, and raises rather than silently using a panel for the third time or inventing an asset.

- [ ] **Step 4: Run GREEN plus legacy planner regressions**

    .venv/bin/pytest tests/test_reference_shot_plan.py tests/test_shot_director.py tests/test_editorial_visual_planner.py -q

- [ ] **Step 5: Commit**

    git add app/services/editorial_visual_planner.py app/services/shot_director.py tests/test_reference_shot_plan.py
    git commit -m "feat: direct reference paced semantic shots"

---

### Task 5: Render one-word punctuation-free captions

**Files:**
- Modify: `app/services/timeline.py`
- Modify: `app/services/render.py`
- Modify: `app/services/editorial_qc.py`
- Add: `tests/test_reference_captions.py`
- Add: `tests/test_reference_ass.py`

**Interfaces:**
- Produces: one `ReferenceCaptionToken` for every spoken lexical token and profile-specific ASS styling; legacy 4-7 word grouping remains unchanged outside the profile.

- [ ] **Step 1: Write RED tests**

  Assert every cue contains one uppercase lexical token, no Unicode category beginning with `P`, preserves source word timing/order, stays within media bounds and safe region, uses bold white/black outline styling, and emits no top sentence event.

- [ ] **Step 2: Run RED**

    .venv/bin/pytest tests/test_reference_captions.py tests/test_reference_ass.py -q

- [ ] **Step 3: Implement the profile cue transform**

    def reference_caption_tokens(spoken_text, timed_words):
        output = []
        for index, word in enumerate(timed_words):
            cleaned = "".join(c for c in word.text if not unicodedata.category(c).startswith("P"))
            if cleaned:
                output.append(ReferenceCaptionToken(
                    cue_id=f"word-{index}", spoken_token=word.text,
                    display_token=cleaned.upper(), start_s=word.start, end_s=word.end,
                    removed_punctuation=tuple(c for c in word.text if unicodedata.category(c).startswith("P")),
                ))
        return tuple(output)

  Profile-aware QC requires a one-word ratio of exactly `1.0`; it must not call the legacy `single_word_caption_ratio_ge_15pct` rejection.

- [ ] **Step 4: Run GREEN and legacy subtitle/render regressions**

    .venv/bin/pytest tests/test_reference_captions.py tests/test_reference_ass.py tests/test_timeline.py tests/test_render.py tests/test_editorial_qc.py -q

- [ ] **Step 5: Commit**

    git add app/services/timeline.py app/services/render.py app/services/editorial_qc.py tests/test_reference_captions.py tests/test_reference_ass.py
    git commit -m "feat: add reference one word captions"

---

### Task 6: Enforce stable monotonic motion and profile duration QC

**Files:**
- Modify: `app/services/motion_director.py`
- Modify: `app/services/shot_director.py`
- Modify: `app/services/render.py`
- Add or complete: `app/services/motion_qc.py`
- Modify: `app/services/quality.py`
- Modify: `app/services/editorial_qc.py`
- Add: `tests/test_reference_motion.py`
- Add: `tests/test_reference_duration_qc.py`

**Interfaces:**
- Produces: deterministic frame telemetry and profile-aware duration/motion QC; legacy `<60s` and 2.3-3.3 second rules remain active only for legacy/default profiles.

- [ ] **Step 1: Write RED tests for forbidden modes, zoom ceilings, reversal-free curves, even quantization, 38-50 second dispatch, and legacy isolation**

- [ ] **Step 2: Run RED**

    .venv/bin/pytest tests/test_reference_motion.py tests/test_reference_duration_qc.py -q

- [ ] **Step 3: Implement explicit profile dispatch**

    if profile_id == REFERENCE_MATCHED_SHORTS_V1.profile_id:
        duration_ok = profile.duration_min_s <= duration <= profile.duration_max_s
        average_ok = profile.mean_shot_min_s <= average_shot <= profile.mean_shot_max_s
    else:
        duration_ok = duration >= 60.0
        average_ok = 2.3 <= average_shot <= 3.3

  Reference curves use only hold, push, pull, pan, reveal, and impact_emphasis; each curve is monotonic with deterministic easing and a recorded focus target. Remove forbidden modes only from the reference option set; profile validation rejects them before FFmpeg.

- [ ] **Step 4: Run GREEN, motion/render regressions, Ruff, and compileall**

    .venv/bin/pytest tests/test_reference_motion.py tests/test_reference_duration_qc.py tests/test_motion_director.py tests/test_render.py tests/test_quality.py -q
    .venv/bin/ruff check app/services/motion_director.py app/services/shot_director.py app/services/render.py app/services/motion_qc.py app/services/quality.py app/services/editorial_qc.py tests/test_reference_motion.py tests/test_reference_duration_qc.py
    .venv/bin/python -m compileall -q app tests

- [ ] **Step 5: Commit**

    git add app/services/motion_director.py app/services/shot_director.py app/services/render.py app/services/motion_qc.py app/services/quality.py app/services/editorial_qc.py tests/test_reference_motion.py tests/test_reference_duration_qc.py
    git commit -m "feat: stabilize reference profile motion"

---

### Task 7: Reuse the four-audition immutable voice gate

**Prerequisite:** Complete the August 5 subtitle/voice Plan 2 voice-audition and immutable VoiceProfile tasks.

**Files:**
- Modify: `app/services/pipeline.py`
- Add: `tests/test_reference_voice_gate.py`

**Interfaces:**
- Consumes: `require_voice_profile(db, workspace_id)` and the persisted four-candidate audition manifest.
- Produces: a profile render block reason of `voice_profile_missing` or the approved immutable `voice_profile_hash`.

- [ ] **Step 1: Write RED tests that final rendering stops before TTS/FFmpeg without a selected profile and reuses an approved profile without regenerating auditions**

- [ ] **Step 2: Run RED**

    .venv/bin/pytest tests/test_reference_voice_gate.py -q

- [ ] **Step 3: Gate the reference pipeline before media execution**

    voice_profile = require_voice_profile(db, project.workspace_id)
    render_metadata["voice_profile_hash"] = voice_profile.profile_content_hash

  Audition manifests must state `purpose="timbre_comparison"` and `chapter_coverage=false`.

- [ ] **Step 4: Run GREEN with voice regressions and commit**

    .venv/bin/pytest tests/test_reference_voice_gate.py tests/test_voice_auditions.py tests/test_voice_profile_persistence.py -q
    git add app/services/pipeline.py tests/test_reference_voice_gate.py
    git commit -m "feat: require selected voice for reference renders"

---

### Task 8: Add profile QC and auditable sidecars

**Files:**
- Add: `app/services/reference_qc.py`
- Modify: `app/services/editorial_qc.py`
- Modify: `app/services/quality.py`
- Add: `tests/test_reference_qc.py`

**Interfaces:**
- Produces: `ReferenceProfileQC` and a JSON-safe metrics payload containing profile/hash, coverage, claims, duration, shots, captions, reuse, motion, codec, audio, rights, and publish decision.

- [ ] **Step 1: Write RED tests for every acceptance metric and stable failure code**

  Required failures include `coverage.incomplete`, `reference.duration`, `reference.shot_count`, `reference.hold_ratio`, `reference.hard_cut_ratio`, `reference.panel_reuse`, `reference.caption_word_count`, `reference.caption_punctuation`, `reference.caption_top_forbidden`, `reference.motion_forbidden`, `reference.zoom_limit`, `reference.intro_forbidden`, `reference.cta_forbidden`, `reference.ending_not_cliffhanger`, `reference.codec`, `reference.audio_loudness`, and `source_gate_failed`.

- [ ] **Step 2: Run RED**

    .venv/bin/pytest tests/test_reference_qc.py -q

- [ ] **Step 3: Implement additive profile QC**

    @dataclass(frozen=True)
    class ReferenceProfileQC:
        profile_id: str
        profile_hash: str
        passed: bool
        failures: tuple[str, ...]
        metrics: Mapping[str, int | float | str | bool]
        rights_publish_allowed: bool

    def evaluate_reference_profile(inputs, profile):
        failures = []
        if not profile.duration_min_s <= inputs.duration_s <= profile.duration_max_s:
            failures.append("reference.duration")
        if not profile.shot_min <= len(inputs.shots) <= profile.shot_max:
            failures.append("reference.shot_count")
        if inputs.hold_ratio < profile.hold_ratio_min:
            failures.append("reference.hold_ratio")
        if inputs.hard_cut_ratio < profile.hard_cut_ratio_min:
            failures.append("reference.hard_cut_ratio")
        if inputs.caption_word_failures:
            failures.append("reference.caption_word_count")
        if inputs.caption_punctuation_failures:
            failures.append("reference.caption_punctuation")
        if inputs.forbidden_motion_modes:
            failures.append("reference.motion_forbidden")
        if inputs.has_intro_or_logo:
            failures.append("reference.intro_forbidden")
        if inputs.has_generic_cta:
            failures.append("reference.cta_forbidden")
        if not inputs.ends_with_cliffhanger_or_loop:
            failures.append("reference.ending_not_cliffhanger")
        if not inputs.rights_publish_allowed:
            failures.append("source_gate_failed")
        failures = tuple(sorted(set(failures)))
        return ReferenceProfileQC(
            profile_id=profile.profile_id,
            profile_hash=profile_hash(profile),
            passed=not failures,
            failures=failures,
            metrics=inputs.metrics,
            rights_publish_allowed=not failures and inputs.rights_publish_allowed,
        )

- [ ] **Step 4: Run GREEN and commit**

    .venv/bin/pytest tests/test_reference_qc.py tests/test_editorial_qc.py tests/test_quality.py -q
    git add app/services/reference_qc.py app/services/editorial_qc.py app/services/quality.py tests/test_reference_qc.py
    git commit -m "feat: audit reference profile metrics"

---

### Task 9: Render and audit an isolated VPS sample

**Files:**
- Add: `scripts/audit_reference_render.py`
- Add: `tests/test_reference_artifact_audit.py`
- Do not add generated artifacts to Git.

**Interfaces:**
- The audit consumes runtime paths and returns a nonzero exit code on missing/mismatched sidecars or media metrics.

- [ ] **Step 1: Write RED artifact tests**

  Require coverage manifest, observation ledger, evidence graph, approved script/clauses, voice manifest/profile hash, shot list, subtitle/token list, motion telemetry, QC JSON, contact sheet, source-rights report, and MP4 when render gates allow it.

- [ ] **Step 2: Run RED, implement the audit, and run GREEN**

    .venv/bin/pytest tests/test_reference_artifact_audit.py -q

- [ ] **Step 3: Execute one new isolated render on VPS**

  Use a new runtime output directory and never mutate the prior render. If vision capability, human approval, voice selection, or another prerequisite is absent, stop at that exact gate and do not fabricate downstream artifacts.

- [ ] **Step 4: Verify media and sidecars**

    ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,r_frame_rate,pix_fmt -of json /absolute/runtime/path/output.mp4
    .venv/bin/python scripts/audit_reference_render.py /absolute/runtime/path

  Expected: 1080x1920, 30 fps, H.264, yuv420p, duration 38-50 seconds, complete sidecars, audio near -14 LUFS/true peak at most -1.5 dBTP, and `publish_allowed=false` whenever the source-rights report is blocked.

- [ ] **Step 5: Commit only the source audit and test**

    git add scripts/audit_reference_render.py tests/test_reference_artifact_audit.py
    git commit -m "test: audit reference render artifacts"

---

### Task 10: Update rollout documentation and stop before push

**Files:**
- Modify: `docs/STATUS.md`
- Modify: `docs/P0_EDITORIAL.md`
- Modify: `docs/RELEASE_RUNBOOK.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Record commits, gate results, artifact paths, rights state, rollback points, and known limitations without claiming monetization eligibility**

- [ ] **Step 2: Run the focused suites from Tasks 0-9, then the full non-slow suite, relevant slow FFmpeg tests, Ruff, compileall, and Git audits**

    env PATH=/home/yusronrohmani/.local/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/pytest -m "not slow" -q
    env PATH=/home/yusronrohmani/.local/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/pytest -m slow -q
    .venv/bin/ruff check app tests scripts
    .venv/bin/python -m compileall -q app tests scripts
    git diff --check
    git status --short --untracked-files=all

- [ ] **Step 3: Commit documentation only**

    git add docs/STATUS.md docs/P0_EDITORIAL.md docs/RELEASE_RUNBOOK.md CHANGELOG.md
    git diff --cached --check
    git commit -m "docs: record reference profile rollout"

- [ ] **Step 4: Stop before push**

  Report all commit SHAs, test counts, exact runtime artifact paths, QC metrics, rights/publish status, and clean/dirty status. Do not push until the user approves the reviewed render.

## Self-Review Checklist

- [ ] Every locked design requirement maps to a task and a measurable assertion.
- [ ] No `TBD`, `TODO`, “similar to,” silent fallback, or unresolved placeholder remains in executable instructions or committed code.
- [ ] `ReferenceProfileConfig` and canonical hash contain the same complete field set.
- [ ] Prompt path is consistently `app/prompts/reference_matched_shorts_analyzer_v1.txt`.
- [ ] Profile dispatch supersedes `<60s`, `duration_outside_60_90s`, 2.3-3.3 second average, and single-word-caption rejection only for `reference_matched_shorts_v1`.
- [ ] Legacy/default behavior stays covered by regression tests.
- [ ] The plan does not claim success before provider, evidence, human approval, voice, motion, render, and rights evidence exists.
- [ ] Every stage uses exact allowlists and stops before push.
