# Sharp Friend Narrative Identity v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. The named superpowers sub-skills are not installed in this environment; the active equivalent is Sol review plus Luna task execution. Execute one checked task at a time and stop at each review, commit, and push boundary.

**Goal:** Replace the rigid v2 narration shape with a versioned, evidence-grounded narrative identity that sounds like a sharp, friendly, perceptive friend while preserving complete-panel comprehension, qualified claims, explicit human approval, and the separate punctuation-free display subtitle representation.

**Architecture:** Full-panel vision evidence and ordered continuity feed a versioned narrative identity profile. The profile selects a v3 instruction resource and flexible passage validator. A deterministic naturalness screen reports measurable risks without rewriting prose. Pipeline persistence records the profile ID/version/hash beside the existing StoryAnalysis evidence and ScriptVersion metadata. Public approval remains the only transition to approved media work; this plan does not generate audio.

**Tech Stack:** Python 3.11, SQLAlchemy ORM, Pydantic, existing analyzer contract and OpenAI-compatible vision adapter, pytest, Ruff, compileall, FFmpeg only for existing non-audio integration checks, Alembic only if a focused persistence test proves JSON fields cannot hold the profile metadata.

## Global Constraints

The following requirements are copied from the approved design and apply to every task:

- "Full-panel reconciliation happens before prose; every narrative claim remains linked to panel evidence."
- "Narrative identity is a versioned runtime production profile, not an AGENTS.md instruction."
- "sharp_friend_v1 is conversational American English: a clever, friendly, perceptive friend under controlled tension."
- "Contractions, varied sentence lengths, conversational connectors, causal transitions, and selective evidence-grounded commentary are allowed."
- "Generic hype, fake intensity, generic calls to action, invented identity or motive, fixed intros, forced catchphrases, and copied speech-balloon dialogue are prohibited."
- "The initial total narration target remains 90-125 words, but rigid per-role word budgets are removed."
- "The output may contain four to six passages or an equivalent flexible structure; it is not forced into a checklist order."
- "The ending kind is cliffhanger, consequence, or open_question. A question mark is allowed only for open_question."
- "spoken_text retains punctuation for TTS prosody; display_text is derived independently as uppercase, punctuation-free, one lexical word per cue."
- "Human editorial approval is mandatory before voice generation, timeline generation, or rendering."
- "Voice generation and provider selection are explicitly deferred until the user chooses local or API execution; this plan does not create audio or select a voice."
- "Existing vision_evidence_v2 behavior remains compatible until an explicit versioned rollout selects sharp_friend_v1."
- "No rule/template fallback may generate a script when evidence or provider output is invalid."
- "publish_allowed remains false until source rights are verified."
- "All source, test, and render execution occurs on the VPS; Windows is transport-only for exact-history push."
- "No media, DB, credentials, user data, runtime output, or secrets enter Git."
- "Pushes are fast-forward only; no force push, tags, all branches, or unrelated remote writes."

Baseline and authority:

- Authoritative checkout: /home/yusronrohmani/manhwashorts through SSH alias google.
- Baseline: clean main at 7fe75cd3c7b19ade96bc39f3f00a84aa2b06865f; GitHub main must be verified at that SHA immediately before transport.
- Historical checkpoint: 635 passed in the full non-slow suite at f9221dd; it is historical evidence, not a fresh result for this docs-only planning commit.
- Every PowerShell SSH command in the implementation sequence ends with 2>&1.
- Existing v2, legacy text-analysis, explicit approval, spoken/display, rights, and provider capability gates remain in force unless a task explicitly selects the new profile.
- The required active voice choice is Sol as orchestrator/reviewer and Luna Max as executor; no model-choice prompt is needed.

## Current symbol map and verified persistence boundary

- app/prompts/vision_first_story_analyzer_v2.txt is the current committed analyzer instruction. It requires all-panel observation, fixed five roles, 90-125 total words, per-role ranges, and an open-question payoff.
- app/services/analyzer_contract.py defines PROMPT_VERSION, load_analyzer_instruction(), validate_analyzer_output(output, *, expected_panel_ids), exact observation/coverage/continuity/story-spine/claim gates, and the v2 five-role validator.
- app/services/vision_adapter.py defines VisionChapterSynthesisRequest, VisionObservationProvider.synthesize(), VisionRequestInvalid, VisionResponseInvalid, and the OpenAI-compatible structured JSON request. Synthesis currently accepts the committed v2 instruction identity.
- app/services/pipeline.py defines run_analysis(db, project_id, actor_id=""), generate_script(), build_timeline(), current_script(), and the persisted StoryAnalysis-to-ScriptVersion evidence gate. The public path must not call legacy text analysis or a template generator.
- app/services/editorial_qc.py defines build_report(..., profile=None) and the existing visual/audio/subtitle/rights checks. app/services/quality.py defines CheckResult and profile-aware quality functions.
- app/schemas.py defines AnalysisOut, AnalysisStatusOut, ScriptGenerateRequest, SectionIn, and ScriptApproveRequest. SectionIn already carries editorial_role, claim_ids, evidence_panel_ids, and evidence.
- app/routers/pipeline.py exposes POST /api/projects/{project_id}/analysis, POST /script, POST /script/approve, and GET /analysis/status.
- app/models.py stores StoryAnalysis coverage_manifest_json, continuity_ledger_json, evidence_graph_json, story_spine_json, reconciliation_json, instruction/provider fields, and ScriptVersion editorial metadata in JSON-capable fields. Project already has narration_style, template, target_duration, and voice_id. No narrative_identity column exists.
- The current persistence choice is to store a selected profile object under StoryAnalysis.reconciliation_json["narrative_identity"] and ScriptVersion.editorial_metadata["narrative_identity"], with canonical profile ID/version/SHA-256. A migration is not planned; Task 5 must stop and report if current JSON fields cannot preserve this immutable identity.
- There is no root AGENTS.md in this repository. An AGENTS.md in the adjacent OmniVoice project is not a runtime prompt source and is outside this plan.

## Architecture and dependency graph

    ordered PanelRegion observations + coverage manifest
      -> VisionChapterSynthesisRequest with narrative identity
      -> vision provider structured output
      -> analyzer_contract v2 or sharp_friend_v1 validation
      -> naturalness screening and evidence/qualification checks
      -> StoryAnalysis narrative_identity metadata
      -> ScriptVersion editorial metadata and five-or-six passage sections
      -> explicit human editorial approval
      -> independent display_text derivation
      -> later voice generation after user choice

Task dependencies:

- Task 1 creates the immutable identity record consumed by all later tasks.
- Task 2 creates the v3 instruction and carries identity metadata through synthesis.
- Task 3 extends validation while preserving v2 when no identity is selected.
- Task 4 adds non-rewriting naturalness screening used by Task 5.
- Task 5 wires profile selection, persistence, status, and approval-safe pipeline behavior.
- Task 6 supplies human-readable fixture/review evidence and locks the final no-audio boundary.

Interfaces produced and consumed:

- Task 1 produces NarrativeIdentityProfile, SHARP_FRIEND_V1, canonical_narrative_profile_json(), narrative_profile_hash(), resolve_narrative_identity(), and load_narrative_identity_instruction().
- Task 2 consumes that profile and produces v3 prompt/version/hash fields in VisionChapterSynthesisRequest.
- Task 3 consumes identity plus analyzer output and produces a validated flexible script_passages structure with ending_kind.
- Task 4 consumes validated passages and claims and produces NarrativeNaturalnessReport plus stable screening findings.
- Task 5 consumes the profile/validator/report and persists safe identity metadata; API status exposes only identity scalars.
- Task 6 consumes the persisted script and approval service and produces review fixtures, not media.

## Task 1: Define the immutable sharp_friend_v1 runtime profile

**Files:**
- Create: app/services/narrative_identity.py.
- Create: tests/test_narrative_identity.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**

    @dataclass(frozen=True)
    class NarrativeIdentityProfile:
        profile_id: str
        version: str
        prompt_version: str
        prompt_path: str
        language: str
        style_label: str
        passage_min: int
        passage_max: int
        total_words_min: int
        total_words_max: int
        ending_kinds: tuple[str, ...]
        allows_contractions: bool
        requires_human_review: bool

    SHARP_FRIEND_V1: NarrativeIdentityProfile

    def canonical_narrative_profile_json(
        profile: NarrativeIdentityProfile,
    ) -> str: ...

    def narrative_profile_hash(profile: NarrativeIdentityProfile) -> str: ...

    def resolve_narrative_identity(
        profile_id: str | None,
    ) -> NarrativeIdentityProfile | None: ...

    def load_narrative_identity_instruction(
        profile: NarrativeIdentityProfile,
    ) -> tuple[str, str, str]: ...

**RED:**

- [ ] Add tests/test_narrative_identity.py with runtime imports through importlib so the absent module fails in the test body instead of collection.
- [ ] Assert the exact profile ID sharp_friend_v1, version 1.0.0, prompt version vision-first-story-analyzer-v3, language en-US, passage bounds 4 and 6, total word bounds 90 and 125, ending kinds cliffhanger/consequence/open_question, contractions allowed, and human review required.
- [ ] Assert frozen immutability by attempting to assign profile_id and expecting dataclasses.FrozenInstanceError.
- [ ] Assert canonical JSON equals sorted compact dataclasses.asdict output and contains each dataclass field once; assert SHA-256 is stable and changes when style_label, word bounds, ending kinds, or human-review settings change.
- [ ] Assert unknown, empty, legacy, and vision-first profile IDs resolve to None without environment mutation.
- [ ] Assert instruction loading normalizes CRLF to LF, returns the exact version, and hashes UTF-8 normalized content.
- [ ] Run the focused RED command from the VPS checkout:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_narrative_identity.py -q

  Expected result: collection succeeds and every failure is an absent-module/profile contract assertion, not an import collection or fixture setup error.
- [ ] Run Ruff on the test file and record that the RED is behavioral.

**Implementation:**

- [ ] Create app/services/narrative_identity.py with only stdlib imports, pathlib, dataclasses, hashlib, and json. Do not read credentials, environment variables, network, or database state.
- [ ] Define the frozen record and constant exactly as follows:

    SHARP_FRIEND_V1 = NarrativeIdentityProfile(
        profile_id="sharp_friend_v1",
        version="1.0.0",
        prompt_version="vision-first-story-analyzer-v3",
        prompt_path="app/prompts/vision_first_story_analyzer_v3.txt",
        language="en-US",
        style_label="clever friendly perceptive friend under controlled tension",
        passage_min=4,
        passage_max=6,
        total_words_min=90,
        total_words_max=125,
        ending_kinds=("cliffhanger", "consequence", "open_question"),
        allows_contractions=True,
        requires_human_review=True,
    )

- [ ] Implement canonical serialization with dataclasses.asdict(profile), json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False), and UTF-8 SHA-256; do not add a second larger config dictionary.
- [ ] Resolve only the exact profile ID and return None for all other IDs. Load the profile prompt with Path(__file__).resolve().parents[1] / "prompts" / "vision_first_story_analyzer_v3.txt", normalize CRLF to LF, retain one trailing LF, and return (profile.prompt_version, digest, normalized_text).
- [ ] Add tests that compare the canonical profile field set to dataclasses.fields(NarrativeIdentityProfile), ensuring future fields cannot silently exist outside the hashed object.

**GREEN and checkpoint:**

- [ ] Run the focused test and expect all profile tests to pass.
- [ ] Run .venv/bin/ruff check app/services/narrative_identity.py tests/test_narrative_identity.py and .venv/bin/python -m compileall -q app/services/narrative_identity.py tests/test_narrative_identity.py.
- [ ] Run git diff --check and inspect the two source/test paths for secrets.
- [ ] Update docs/STATUS.md with the profile ID/hash contract and CHANGELOG.md with the task result.
- [ ] Stage only the four listed paths, commit feat: define sharp friend narrative identity, and record the full SHA.
- [ ] Create an exact-history bundle from the VPS commit, import it into the clean Windows transport clone, fast-forward only, and push main:main only after verifying the GitHub base was unchanged. Verify HTTPS ls-remote equals the new SHA before starting Task 2. Rollback is this commit.

## Task 2: Add the v3 instruction and carry identity through synthesis

**Files:**
- Modify: app/services/narrative_identity.py.
- Create: app/prompts/vision_first_story_analyzer_v3.txt.
- Modify: app/services/vision_adapter.py.
- Modify: tests/test_vision_synthesis.py.
- Modify: CHANGELOG.md.

**Interfaces:**

    @dataclass(frozen=True)
    class VisionChapterSynthesisRequest:
        analysis_run_id: str
        instruction_version: str
        instruction_sha256: str
        instruction_text: str
        expected_panel_ids: tuple[str, ...]
        coverage_manifest: Mapping[str, Any]
        ordered_observations: tuple[Mapping[str, Any], ...]
        chunks: tuple[Mapping[str, Any], ...]
        narrative_profile_id: str | None = None
        narrative_profile_version: str | None = None
        narrative_profile_sha256: str | None = None

- [ ] Extend the existing request by appending only the three defaulted identity fields shown above, so current dataclass construction remains valid.
- [ ] Add a preflight helper with this exact contract:

    def validate_narrative_identity(
        request: VisionChapterSynthesisRequest,
    ) -> NarrativeIdentityProfile | None:
        ...

  It returns None for v2 requests with all identity fields absent. If any identity field is present, it resolves the profile and compares the profile ID, version, and canonical profile SHA-256; a self-consistent but uncommitted prompt or profile is rejected with VisionRequestInvalid.
- [ ] Include the identity object in the compact synthesis request metadata. Never include image bytes, base64 URLs, filenames, voice IDs, TTS model names, or provider secrets in this request.

**RED:**

- [ ] Extend tests/test_vision_synthesis.py with a valid sharp_friend request using the committed v3 profile and a self-consistent alternate prompt/hash that must fail before network; assert the request contains profile identity but no audio/provider fields.
- [ ] Assert the existing v2 request still uses the v2 prompt and no identity object.
- [ ] Run:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_synthesis.py -q

  Expected RED is a missing profile-field/preflight/payload assertion, while the established v2 synthesis tests remain green.
- [ ] Ruff the modified test and record the focused RED before production changes.

**Implementation:**

- [ ] Create app/prompts/vision_first_story_analyzer_v3.txt as LF UTF-8. Its ordered normative instructions must say: observe every ordered panel first; reconcile coverage, continuity, entities, aliases, motives, state changes, causal links, and evidence before prose; write as a clever friendly perceptive friend under controlled tension; permit contractions and varied human sentence lengths; use conversational connectors, causal transitions, and selective evidence-grounded commentary; qualify interpretations; prohibit generic hype, fake intensity, CTA, fixed intro, forced catchphrase, invented identities/motives, and copied speech-balloon dialogue; allow four to six passages; use ending_kind cliffhanger/consequence/open_question with question mark only for open_question; preserve spoken punctuation and derive display text separately; require human editorial approval; do not generate voice in this stage.
- [ ] Add the exact prompt version string vision-first-story-analyzer-v3 and compute its digest mechanically through load_narrative_identity_instruction; do not hand-write a snapshot until the normalized prompt exists.
- [ ] Extend OpenAICompatibleVisionProvider.synthesize so metadata includes:

    "narrative_identity": {
        "profile_id": "sharp_friend_v1",
        "version": "1.0.0",
        "sha256": "<computed profile hash>"
    }

  The value is computed from SHARP_FRIEND_V1 and is never accepted from untrusted text alone. Keep temperature 0, structured JSON, and all existing safe error handling.
- [ ] Keep voice generation entirely outside this request and adapter boundary.

**GREEN and checkpoint:**

- [ ] Run Task 2 synthesis tests, Task 1 identity tests, Task 4 adapter/capability tests, and the analyzer contract tests with the VPS FFmpeg PATH.
- [ ] Run .venv/bin/ruff check app/services/narrative_identity.py app/services/vision_adapter.py tests/test_vision_synthesis.py and .venv/bin/python -m compileall -q app/services/narrative_identity.py app/services/vision_adapter.py.
- [ ] Run git diff --check and inspect the prompt for secrets and unsupported provider instructions.
- [ ] Update CHANGELOG.md with the v3 prompt/profile transport identity.
- [ ] Stage only the five listed paths, commit feat: add sharp friend analyzer prompt, and push through the exact-history Windows transport after fast-forward verification. Record the GitHub SHA and rollback commit.

## Task 3: Evolve analyzer validation for flexible, evidence-grounded passages

**Files:**
- Modify: app/services/analyzer_contract.py.
- Modify: app/prompts/vision_first_story_analyzer_v3.txt.
- Create: tests/test_analyzer_contract_v3.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**

    def validate_analyzer_output(
        output: Mapping[str, Any],
        *,
        expected_panel_ids: Sequence[str],
        identity: NarrativeIdentityProfile | None = None,
    ) -> None:
        ...

    def validate_narrative_ending(
        passages: Sequence[Mapping[str, Any]],
        ending_kind: str,
    ) -> None:
        ...

- [ ] Keep identity=None as the exact v2 behavior: five ordered roles, existing role word bounds, question-ending payoff, and all current observation/coverage/continuity/story-spine/claim gates.
- [ ] When identity.profile_id == "sharp_friend_v1", require exactly four, five, or six passage objects; require each passage to have exactly passage_id, editorial_role, text, claim_ids, and evidence_panel_ids; require unique nonempty passage IDs and role labels, but do not require a fixed role order or fixed role names.
- [ ] Require total whitespace-separated word count between identity.total_words_min and identity.total_words_max. Do not add per-role minimum or maximum counts.
- [ ] Require narrative_outline.ending_kind to be one of cliffhanger, consequence, open_question. Require a final question mark only for open_question; reject a final question mark for cliffhanger or consequence. Do not rewrite text to repair punctuation.
- [ ] Preserve exact ordered expected_panel_ids, full coverage reconciliation, all observation lineage keys, continuity ledger, six story-spine concepts, unique claim IDs, claim evidence, and passage claim/evidence linkage. A flexible identity must not relax evidence.
- [ ] Reject generic hype, engagement CTA, copied dialogue, invented identity/motive, unqualified inference, foreign/empty evidence IDs, missing top-level keys, unexpected top-level keys, duplicate passage keys, and repeated normalized openings/sentences across passages. These remain fail-closed AnalyzerContractError results.
- [ ] Add a profile identity block to the validator’s audit result only if the existing contract exposes one; otherwise keep the validator return value None and use its exception boundary. Do not persist prose in the validator.

**RED:**

- [ ] Add a positive four-passage fixture and a positive six-passage fixture with distinct source entities, contractions, conversational connectors, and a grounded consequence ending without a question mark.
- [ ] Add a positive open_question fixture with a final question mark and a negative consequence fixture with a final question mark.
- [ ] Add negative cases for three and seven passages, per-role budget assumptions, duplicate/unknown role labels, unexpected passage keys, repeated openings, CTA/hype, copied balloon dialogue, unsupported claims, unqualified interpretations, and missing evidence.
- [ ] Add a regression that the v2 fixture remains valid with identity=None and that no voice provider or display-text derivation runs during validation.
- [ ] Run:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_analyzer_contract_v3.py tests/test_analyzer_contract.py -q

  Expected RED: v3 identity import/argument and flexible passage failures only; existing v1/v2 tests must not become collection failures.
- [ ] Run Ruff on the new test and record RED before production edits.

**Implementation:**

- [ ] Import NarrativeIdentityProfile in a type-check-safe way from app.services.narrative_identity and add identity=None without changing callers that use keyword expected_panel_ids.
- [ ] Factor the existing fixed-role validator into an explicit v2 branch and add a separate _validate_sharp_friend_passages function. Do not let v3 branch through a fixed five-role list.
- [ ] Use a complete implementation equivalent to:

    def _validate_sharp_friend_passages(
        passages: Sequence[Mapping[str, Any]],
        outline: Mapping[str, Any],
        claims: Mapping[str, Mapping[str, Any]],
        identity: NarrativeIdentityProfile,
    ) -> None:
        if not identity.passage_min <= len(passages) <= identity.passage_max:
            raise AnalyzerContractError("narrative.passage_count")
        if any(set(item) != _PASSAGE_KEYS for item in passages):
            raise AnalyzerContractError("narrative.passage_shape")
        words = sum(len(str(item["text"]).split()) for item in passages)
        if not identity.total_words_min <= words <= identity.total_words_max:
            raise AnalyzerContractError("narrative.word_count")
        ending_kind = outline.get("ending_kind")
        if ending_kind not in identity.ending_kinds:
            raise AnalyzerContractError("narrative.ending_kind")
        final_text = str(passages[-1]["text"]).rstrip()
        if (ending_kind == "open_question") != final_text.endswith("?"):
            raise AnalyzerContractError("narrative.question_ending")
        _validate_passage_evidence(passages, claims)
        _reject_cross_passage_template_reuse(passages)
        _reject_unsupported_narrative_language(passages, claims)

- [ ] Keep sentence and role checks deterministic and screening-only; do not ask the validator to rewrite or shorten provider text.
- [ ] Update the v3 prompt once so the implementation and prompt agree on flexible passage count and ending semantics. Recompute any committed prompt digest mechanically.

**GREEN and checkpoint:**

- [ ] Run the v3 and legacy analyzer tests, then Task 7A synthesis, Task 7B pipeline/story evidence, Task 6 resolver, and Task 4 adapter tests with PATH=/home/yusronrohmani/.local/bin:$PATH.
- [ ] Run .venv/bin/ruff check app/services/analyzer_contract.py tests/test_analyzer_contract_v3.py and .venv/bin/python -m compileall -q app/services/analyzer_contract.py tests/test_analyzer_contract_v3.py.
- [ ] Run git diff --check, inspect prompt hash/version, and prove no provider/audio call appears in the validator tests.
- [ ] Update STATUS and CHANGELOG with the v3 validator result, compatibility state, focused counts, commit SHA, and rollback SHA.
- [ ] Stage only the five listed paths, commit feat: support flexible sharp friend passages, and push main fast-forward through the Windows transport. Verify GitHub ls-remote equals the commit before Task 4.

## Task 4: Add measurable naturalness screening without rewriting prose

**Files:**
- Modify: app/services/editorial_qc.py.
- Modify: app/services/quality.py.
- Create: tests/test_narrative_qc.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**

    @dataclass(frozen=True)
    class NarrativeNaturalnessReport:
        total_words: int
        sentence_length_p10: float
        sentence_length_p50: float
        sentence_length_p90: float
        sentence_length_variance: float
        repeated_normalized_sentence_ratio: float
        repeated_opening_ngram_ratio: float
        connector_diversity_count: int
        causal_transition_coverage: float
        contraction_count: int
        generic_hype_hits: tuple[str, ...]
        cta_hits: tuple[str, ...]
        claim_evidence_coverage_ratio: float
        qualified_interpretation_coverage_ratio: float
        warnings: tuple[str, ...]

    def screen_narrative_naturalness(
        passages: Sequence[Mapping[str, object]],
        claims: Mapping[str, Mapping[str, object]],
        profile: NarrativeIdentityProfile,
    ) -> NarrativeNaturalnessReport: ...

    def check_narrative_naturalness(
        report: NarrativeNaturalnessReport,
    ) -> list[CheckResult]: ...

- [ ] Use the existing CheckResult fields code, severity, message, passed, and detail. Do not create a second QC result type.
- [ ] Make the report immutable and serializable with only numeric, tuple, and safe string fields. Do not include full passage text, raw provider output, prompt text, credentials, storage paths, or panel image bytes.
- [ ] Define stable blocking codes narrative.evidence_missing, narrative.interpretation_unqualified, narrative.unsupported_claim, narrative.balloon_dialogue_copied, narrative.cta, narrative.generic_hype, narrative.ending_invalid, and narrative.display_derivation_invalid.
- [ ] Define stable warning codes narrative.template_risk and narrative.rhythm_warning. Warnings are visible audit findings but do not rewrite or auto-reject otherwise grounded prose.
- [ ] Do not impose a contraction quota, fixed sentence count, fixed opening, fixed connector order, or per-role word budget.

**RED:**

- [ ] Add accepted fixtures with contractions, a short sentence followed by a longer causal sentence, varied connectors, a consequence ending without a question mark, and complete qualified evidence.
- [ ] Add rejected fixtures for copied dialogue, generic hype, engagement CTA, unsupported claim, unqualified interpretation, missing evidence, invalid ending punctuation, and display punctuation violations.
- [ ] Add a warning fixture with intentionally repetitive openings and low rhythm variance; assert the report is screening data and does not alter passage text.
- [ ] Assert narrative uses such as "moves like lightning", "she comments on the clue", and "I like this story's hidden clue" are not CTA findings, while "like this video", "subscribe", and "follow for more" are findings.
- [ ] Assert sentence p10/p50/p90, variance, repeated ratio, opening n-gram ratio, connector diversity, causal coverage, contraction count, and evidence ratios are deterministic.
- [ ] Run:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_narrative_qc.py -q

  Expected RED: missing naturalness report/function and stable finding assertions, with collection/setup clean.
- [ ] Run Ruff on the new test and record the behavioral RED.

**Implementation:**

- [ ] Add sentence tokenization that splits on terminal .?! while retaining only lengths and normalized hashes. For each passage, compute sentence lengths, first-three-lexical-word opening n-grams, and normalized nonempty sentence hashes.
- [ ] Compute percentile values using a deterministic nearest-rank function:

    def _nearest_rank(values: Sequence[int], fraction: float) -> float:
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(round(
            fraction * (len(ordered) - 1)
        ))))
        return float(ordered[index])

- [ ] Compute repeated sentence/opening ratios across passages only; a repeated sentence twice within one passage is not a cross-passage template finding.
- [ ] Count connector diversity from a fixed lower-case set such as because, but, so, while, although, however, instead, meanwhile, yet, and therefore. Count contractions by apostrophe-bearing lexical tokens but do not require any minimum.
- [ ] Mark evidence coverage only when every claim ID is present, each claim has nonempty evidence_panel_ids, and each interpretation is qualified by the claim’s qualification field or an explicit uncertainty marker. Mark copied balloon dialogue only when a passage exactly equals or directly repeats a dialogue_or_ocr evidence string.
- [ ] Return warning findings for repeated openings, repeated sentences, low sentence-length variance, or low connector diversity. Return blocking findings only for unsupported/uncertain evidence, prohibited CTA/hype, copied dialogue, invalid ending, or invalid display derivation.
- [ ] Add quality.check_narrative_naturalness(report) that converts report fields to existing CheckResult values; retain check_narration_language, check_subtitles, rights, motion, and legacy profile checks unchanged.
- [ ] Let editorial_qc.build_report(profile=...) include only safe naturalness counts/findings when the selected profile is sharp_friend_v1. profile=None must produce the existing legacy report shape and thresholds.

**GREEN and checkpoint:**

- [ ] Run the focused naturalness tests, analyzer v3 tests, v2 analyzer tests, and Task 7A/7B evidence tests.
- [ ] Run .venv/bin/ruff check app/services/editorial_qc.py app/services/quality.py tests/test_narrative_qc.py and .venv/bin/python -m compileall -q app/services/editorial_qc.py app/services/quality.py.
- [ ] Run git diff --check, inspect that QC detail has no prose payload or secret, and verify profile=None tests are unchanged.
- [ ] Update STATUS and CHANGELOG with warning-versus-blocker semantics and focused counts.
- [ ] Stage only the five listed paths, commit feat: screen sharp friend naturalness, and push exact main fast-forward through the Windows transport. Verify GitHub SHA and retain the commit as the rollback boundary.

## Task 5: Persist narrative identity and expose safe status through the vision pipeline

**Files:**
- Modify: app/services/pipeline.py.
- Modify: app/routers/pipeline.py.
- Modify: app/schemas.py.
- Create: tests/test_narrative_pipeline.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**

    def run_analysis(
        db: Session,
        project_id: str,
        actor_id: str = "",
        *,
        narrative_profile_id: str | None = None,
    ) -> StoryAnalysis: ...

    def generate_script(
        db: Session,
        project_id: str,
        actor_id: str = "",
        *,
        narrative_profile_id: str | None = None,
    ) -> ScriptVersion: ...

    class ScriptGenerateRequest(BaseModel):
        keep_locked: bool = False
        hook_count: int = 3
        seed: int | None = None
        narrative_profile_id: str | None = None

- [ ] If the current request model uses a different existing field order, append narrative_profile_id with a default rather than changing positional behavior.
- [ ] Keep POST /api/projects/{project_id}/analysis without a body valid for v2 compatibility; if a body is accepted, only the exact profile ID sharp_friend_v1 is selectable.
- [ ] Add safe optional fields to AnalysisOut and AnalysisStatusOut only: narrative_profile_id, narrative_profile_version, narrative_profile_sha256, and narrative_screening_warning_codes. Never return prompt text, observations, complete claims, passages, raw provider output, file paths, keys, or image data.
- [ ] Persist the identity under both analysis and script boundaries:

    reconciliation_json["narrative_identity"] = {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "sha256": narrative_profile_hash(profile),
    }

    editorial_metadata["narrative_identity"] = {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "sha256": narrative_profile_hash(profile),
        "human_review_required": True,
        "editorial_review_confirmed": False,
    }

- [ ] Load the profile from the latest reconciled StoryAnalysis, reject a generate_script override that differs from the persisted profile, and reject a missing/corrupt identity hash with a stable PipelineError. Do not derive a profile from prose or client-supplied hash alone.
- [ ] Call validate_analyzer_output(..., identity=profile) and check_narrative_naturalness after provider synthesis and before ScriptVersion creation. Do not auto-repair output, call RulesScriptGenerator, call run_legacy_text_analysis, or call TTS.
- [ ] Preserve exact provider passage text and punctuation in the stored spoken_text/source fields. Keep display_text derivation in the existing timeline path, never in the analyzer or naturalness screen.
- [ ] Keep generate_script limited to materializing an unapproved SCRIPT_DRAFT. Existing approve_script with editorial_review_confirmed=True and nonempty actor remains the only approval transition.
- [ ] Ensure GET /api/projects/{project_id}/analysis/status reports the safe profile fields and current warning/blocking codes while excluding observations/full claims/full passages and secrets.

**RED:**

- [ ] Add a valid sharp_friend analysis fixture with complete coverage, continuity, claims, flexible four-passage output, ending_kind, and stored identity metadata; assert the profile is absent from current status or the generate path rejects the new identity.
- [ ] Add mismatch tests for profile ID/version/hash, stale analysis identity, missing coverage, missing human review metadata, and invalid flexible passages. Assert no ScriptVersion is created.
- [ ] Add a v2 fixture with identity omitted and assert the existing five-role path remains valid.
- [ ] Add a safety test monkeypatching resolve_analyzer, RulesScriptGenerator, generate_voiceover, and build_timeline to raise; generate_script with sharp_friend must not call any of them.
- [ ] Add status API assertions for allowed scalar/count/code fields and forbidden observations, full claims/passages, prompt text, secrets, and file paths.
- [ ] Add explicit approval tests: generate creates SCRIPT_DRAFT with confirmed false; approve without editorial_review_confirmed or actor fails; approve with both and valid evidence succeeds.
- [ ] Run:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_narrative_pipeline.py tests/test_script_evidence_gate.py tests/test_vision_status_api.py -q

  Expected RED: profile request fields, persistence, validator identity argument, and status exposure are absent while existing evidence gates remain collection-clean.
- [ ] Ruff the new test and capture RED before production edits.

**Implementation:**

- [ ] Resolve an explicitly requested profile before provider synthesis; if absent, keep the existing v2 workflow and metadata. Do not silently select sharp_friend_v1 from Project.template or narration_style in this task unless a later approved product slice changes that selector.
- [ ] Add a private helper with the exact safe boundary:

    def _narrative_identity_from_analysis(
        analysis: StoryAnalysis,
    ) -> NarrativeIdentityProfile | None:
        ...

  It reads only reconciliation_json, resolves the known profile, recomputes its canonical hash, and raises PipelineError("narrative_identity_invalid") on mismatch.
- [ ] Store the profile ID/version/hash in both JSON boundaries only after all evidence and analyzer gates pass. If SQLAlchemy JSON serialization cannot preserve the tuple values, serialize only the canonical scalar strings and keep the source dataclass immutable.
- [ ] Pass narrative_profile_id/version/sha256 into VisionChapterSynthesisRequest and require the adapter’s committed v3 identity preflight.
- [ ] Extend generate_script’s reconstructed analyzer output to carry narrative_outline["ending_kind"] and the flexible passage list. Preserve the existing section-role/evidence shape required by approval and SectionIn.
- [ ] Add status summary code that maps only stable booleans, counts, hashes, profile scalars, and short codes. Do not include report detail containing narrative text.
- [ ] Keep legacy v2 and explicit legacy text analysis behavior unchanged when no sharp_friend profile is selected, but do not let legacy behavior serve as a fallback for a selected sharp_friend run.

**GREEN and checkpoint:**

- [ ] Run the focused narrative pipeline/status tests, analyzer v3/v2 tests, vision synthesis/adapter/capability tests, Task 7B pipeline/story evidence tests, resolver/BYOK tests, and API tests with PATH=/home/yusronrohmani/.local/bin:$PATH.
- [ ] Run .venv/bin/ruff check app/services/pipeline.py app/routers/pipeline.py app/schemas.py tests/test_narrative_pipeline.py and .venv/bin/python -m compileall -q app/services/pipeline.py app/routers/pipeline.py app/schemas.py.
- [ ] Run migration/schema boundary tests; add no migration unless the focused JSON round-trip test fails and Sol approves a schema slice.

## Task 6: Review natural narration with explicit human approval and no audio

**Files:**
- Create: tests/test_narrative_review.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**RED:**

- [ ] Create two synthetic rights-safe chapter fixtures with different entities, causal links, panel evidence, and spoken passages. One accepted fixture uses contractions, varied sentence lengths, a causal consequence ending, and no copied dialogue; the other accepted fixture uses an evidence-grounded open question.
- [ ] Add rejected fixtures for copied speech-balloon text, generic hype, CTA, invented identity/motive, unqualified interpretation, missing claim evidence, repeated template openings, and invalid ending punctuation.
- [ ] Assert the review uses complete ordered panel evidence and does not sample representative panels or call text fallback.
- [ ] Assert generated section text is byte-preserved from provider passages and display_text is derived later as separate uppercase punctuation-free one-word cues.
- [ ] Assert explicit approval call uses:

    approve_script(
        db,
        project_id,
        actor_id="editor-1",
        editorial_review_confirmed=True,
    )

  A missing actor or false confirmation remains a PipelineError and leaves SCRIPT_DRAFT unapproved.
- [ ] Monkeypatch generate_voiceover, build_timeline, and render to raise; the review and approval tests must not call them. The plan ends before voice or audio work.
- [ ] Run:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_narrative_review.py tests/test_narrative_pipeline.py -q

  Expected RED is the missing profile review fixture or absent flexible approval assertions, not media/provider setup.

**Implementation and GREEN:**

- [ ] Use only the existing analyzer, script materialization, approval, and status boundaries. Do not add a second approval service and do not manually mutate a ScriptVersion or StoryAnalysis row in a fixture.
- [ ] Add no root AGENTS.md. If a future coding-agent handoff is needed, it must be a separate reviewed documentation task and must explicitly say it does not control runtime prompt selection; this task does not create it.
- [ ] Run all v3/v2 analyzer, naturalness, pipeline, API/status, vision synthesis, resolver, and BYOK regressions.
- [ ] Run .venv/bin/ruff check tests/test_narrative_review.py and .venv/bin/python -m compileall -q tests/test_narrative_review.py.
- [ ] Run git diff --check and inspect the test fixtures for synthetic rights-safe data only.
- [ ] Update STATUS with accepted/rejected examples, explicit human approval, and the voice-generation deferral.
- [ ] Stage only the three listed paths, commit test: review sharp friend evidence narration, and push the exact commit through Windows transport. Record the rollback SHA and stop for Sol’s review before any voice/provider work.

## Acceptance matrix

| Approved design requirement | Plan task and assertion |
| --- | --- |
| Versioned sharp_friend_v1 profile and immutable canonical hash | Task 1 frozen dataclass, hash mutation tests |
| Full-panel evidence before prose and claim qualification | Tasks 2, 3, and 5 synthesis/validator gates |
| v3 conversational prompt and no hype/CTA/fixed intro/balloon copying | Task 2 prompt snapshot and Tasks 3/4/6 rejection fixtures |
| Flexible four-to-six passages without per-role budgets | Task 3 positive four/six and count/budget negatives |
| Ending kind and question-mark semantics | Task 3 ending tests and Task 4 ending QC |
| Human-readable naturalness signals without rewriting | Task 4 report metrics, warning/blocker tests |
| Spoken punctuation versus display derivation | Tasks 5 and 6 byte-preservation/display tests |
| Explicit human approval and no pre-approval audio/timeline | Tasks 5 and 6 approval/mocked-call tests |
| v2 compatibility and no legacy fallback for selected v3 | Tasks 3 and 5 v2/selected-profile isolation |
| Safe API/status and no secrets/full prose | Task 5 response allowlist tests |
| No AGENTS runtime coupling | Current symbol map and Task 6 no-file assertion |
| Voice generation deferred; publish rights gate unchanged | Global constraints and Task 6 stop boundary |
| Docs, commits, push, rollback, and no generated artifacts | Every task checkpoint and final handoff |

## Cross-plan design-section coverage

| Approved spec section | Visual plan | Narrative plan |
| --- | --- | --- |
| Current evidence and baseline | Global constraints, symbol map | Global constraints, symbol map |
| COLOR_AGNOSTIC_BALLOON_FREE_V1 contract | Tasks 1-4 | Task 5 consumes visual sidecars without changing them |
| Balloon/subject/action/effect evidence and provenance | Task 1 | Tasks 3 and 5 preserve claim/evidence lineage |
| Color-agnostic blank detection and feasibility telemetry | Tasks 2-3 | Task 6 reviews the resulting visual evidence only |
| Deterministic panel/beat fallback and stable motion | Task 4 | Task 5 preserves the timeline and approval boundary |
| sharp_friend_v1 identity and prompt | Not applicable | Tasks 1-2 |
| Flexible narration, ending kinds, and evidence validator | Not applicable | Task 3 |
| Naturalness screening and human-readable QC | Not applicable | Task 4 and Task 6 |
| Pipeline/API/status persistence | Task 5 consumes approved visual evidence | Task 5 |
| Spoken/display separation | Task 5 preserves render inputs | Tasks 5-6 |
| Voice deferral and rights gate | Task 5 no-audio review | Global constraints, Task 6 |
| Verification, docs, commits, transport, rollback | Every Plan 1 task | Every Plan 2 task |

## Verification matrix and handoff

- Focused red-green commands are recorded in each task. The full VPS gate after every green task is:

      env PATH=/home/yusronrohmani/.local/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/python -m pytest -q -m "not slow"

  Use the repository’s current environment; do not install packages or alter provider configuration.
- The static gate after every green task is:

      .venv/bin/ruff check <changed Python paths and tests>
      .venv/bin/python -m compileall -q app
      git diff --check

- Relevant FFmpeg checks are limited to existing subtitle/render/profile tests and isolated review probes; no audio is generated and no voice provider is called. Any real-panel media remains outside Git.
- Provider-mock tests must verify exact v3 prompt version/hash/profile metadata, complete ordered panel evidence, no base64 in synthesis, safe error boundaries, and no TTS coupling.
- Database tests must prove JSON round-trip of profile ID/version/hash, current analysis linkage, immutable hash, explicit approval, and v2 compatibility. No runtime DB or sample DB is committed.
- API tests must prove status whitelisting, selected profile mismatch blocking, script draft state, approval actor/confirmation, and absence of full prose/raw provider data.
- Secret scope review scans only changed diffs and reports filenames/counts, never values.

## Commit, push, rollback, and scope policy

- [ ] Before each task, verify VPS branch, HEAD, and empty status with a PowerShell SSH command ending 2>&1. Stop if another worker’s changes are present.
- [ ] Keep each task’s source/test ownership allowlist exact; stage with git add -- path1 path2 and verify git diff --cached --name-only equals the task list.
- [ ] Commit only after focused tests, relevant regressions, Ruff, compileall, and diff-check are green. Never include data/, media/, DB/WAL, output, temp, credentials, .env, or user data.
- [ ] Export the exact VPS commit as a bundle, import it into an isolated Windows clone at the recorded GitHub SHA, verify the commit graph, then push main:main with a fast-forward-only update. Never push from the VPS because its SSH authentication is unavailable.
- [ ] Verify HTTPS ls-remote exactly equals the pushed SHA, then verify VPS worktree HEAD/status remains unchanged and clean. Keep the isolated clone; remove only the exact ephemeral bundle after verifying its path.
- [ ] Rollback is the last reviewed commit of the task. A rollback requires Sol approval and an explicit revert commit; do not use broad reset or checkout.
- [ ] Update docs/STATUS.md and CHANGELOG.md after every green task with exact tests, commit SHA, GitHub SHA, next task, rollback SHA, and the explicit no-audio boundary.
- [ ] At the end of Task 6, stop before voice auditions, TTS, audio rendering, or provider selection. The next task requires a new approval.

## Self-review checklist

- [ ] Verify every approved design section maps to the acceptance matrix and a task.
- [ ] Search all four docs for unresolved planning markers, vague implementation phrases, and contradictory fixed-role/flexible-role instructions.
- [ ] Check that every named function, dataclass field, JSON key, route, and import matches the current targeted reads or is explicitly created by its task.
- [ ] Confirm identity=None preserves v2; selected sharp_friend_v1 never falls back to legacy/template generation.
- [ ] Confirm no task generates voice/audio or changes rights/publish behavior.
- [ ] Run git diff --check, Markdown heading/link sanity, exact four-file allowlist, and secret-scope scan before the planning commit.
- [ ] Record the exact plan line counts, task counts, baseline SHA, resulting commit SHA, GitHub SHA, and clean VPS status in the final handoff.


- [ ] Run git diff --check and a safe response-field audit proving status never serializes full claims/passages or prompt text.
- [ ] Update STATUS and CHANGELOG with the profile selection, persistence, status, approval, and v2 compatibility evidence.
- [ ] Stage only the six listed paths, commit feat: persist sharp friend narrative profile, and push exact-history fast-forward through the Windows transport. Verify GitHub ls-remote before Task 6.
