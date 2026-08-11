# Sharp Friend v1 Narrative Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans when either is installed. Those named superpowers skills are not installed in the current environment; the active equivalent is Sol review plus Luna task-by-task execution. Execute one checked task at a time and stop at the review gates. Steps use checkbox syntax.

**Goal:** Add an explicit, evidence-grounded sharp_friend_v1 narrative identity and v3 analyzer contract while leaving every default v2 loader, validator, snapshot, provider, pipeline, and media path unchanged.

**Architecture:** Task 1 fixes the v3 prompt resource and proves its direct UTF-8/LF/content contract without importing a profile loader or accepting a hash. Task 2 then introduces the frozen profile registry, reads that fixed resource, mechanically computes its prompt and canonical profile hashes, and verifies drift. Tasks 3-6 add explicit analyzer dispatch, flexible v3 validation, the two-chapter regression matrix, and one final release gate. No provider or persistence wiring is part of this slice.

**Tech Stack:** Python 3.11, frozen dataclasses, UTF-8 LF prompt resources, SHA-256, pathlib, pytest, Ruff, compileall, and the existing analyzer contract module.

## Global Constraints

The following requirements are copied from the approved design and apply to every implementation task:

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

Slice D owns exactly these implementation paths:

- app/services/narrative_identity.py — frozen profile registry, prompt resource loading, and canonical profile contract verification, introduced in Task 2.
- app/services/analyzer_contract.py — explicit keyword-only profile dispatch and the v3 branch; the default v2 branch remains byte/behavior compatible.
- app/prompts/vision_first_story_analyzer_v3.txt — the prompt resource, created and fixed in Task 1.
- tests/test_narrative_identity.py — direct resource tests in Task 1, then profile/dispatch/validator tests in Tasks 2-5.
- docs/STATUS.md — updated only after the complete green implementation gate.

There is no pipeline.py, vision_adapter.py, provider, ORM, schema, migration, voice, TTS, render, subtitle, API, or database change in this slice. There is no provider call. Slice E owns later synthesis, persistence, advisory naturalness QC, API/status, and human-review wiring.

The implementation is one atomic commit after all tasks and verification pass; task boundaries are reviewer-sized RED/GREEN checkpoints, not intermediate commits.

Baseline and authority:

- Source of truth: /home/yusronrohmani/manhwashorts through SSH alias google.
- Baseline: clean main at ad41f80d09e3d39755c3cd2725a1aa29abb2cac3, parent 21db23590b73e6d9683fd5b0eb5b7a1ec59cab77.
- Rollback point for this implementation: ad41f80d09e3d39755c3cd2725a1aa29abb2cac3.
- Every PowerShell SSH command in the execution sequence ends with 2>&1.
- The historical 635-test result at f9221dd546a24f6c18a7f891b2ded8e1c678c3f2 is historical evidence only; it is not a fresh result for this plan or implementation.

## Current Symbol Map and Boundaries

These locations were read on the baseline and are the interfaces the implementation must use:

- app/services/analyzer_contract.py:9 defines PROMPT_VERSION = "vision-first-story-analyzer-v2".
- app/services/analyzer_contract.py:10-12 defines PROMPT_PATH for vision_first_story_analyzer_v2.txt.
- app/services/analyzer_contract.py:14-22 defines the exact six _REQUIRED_OUTPUT_KEYS: observations, continuity_ledger, evidence_graph, coverage_manifest, narrative_outline, and script_passages.
- app/services/analyzer_contract.py:24-38 defines the exact v2 observation keys and six-field _STORY_SPINE_FIELDS.
- app/services/analyzer_contract.py:40-55 defines the v2 five passage keys, fixed role order, and per-role limits.
- app/services/analyzer_contract.py:87-93 defines AnalyzerContractError with code analyzer_contract_invalid.
- app/services/analyzer_contract.py:96-105 defines the current no-argument load_analyzer_instruction() resource loader. Its default body reads UTF-8, normalizes CRLF to LF, hashes normalized bytes, and returns (version, digest, normalized_text).
- app/services/analyzer_contract.py:160-204 validates ordered observations and exact lineage/evidence references.
- app/services/analyzer_contract.py:207-239 validates complete coverage and ratio/reconciliation fields.
- app/services/analyzer_contract.py:241-362 validates continuity entities, chunks, state changes, motives, causal links, and qualification-bearing claims.
- app/services/analyzer_contract.py:364-372 validates the current six-field story spine.
- app/services/analyzer_contract.py:392-451 validates the fixed v2 five-role passage shape, word ranges, CTA, opening/sentence dedupe, and claim/evidence coverage.
- app/services/analyzer_contract.py:453-463 validates the exact top-level output set and invokes the existing gates.
- app/services/analyzer_contract.py:465-476 exposes validate_analyzer_output(output, *, expected_panel_ids) and converts malformed failures to AnalyzerContractError.
- app/prompts/vision_first_story_analyzer_v2.txt is the committed v2 resource. Its exact snapshot remains untouched.
- tests/test_analyzer_contract.py has _contract_module, _load_instruction, _validate, _observation, and chapter fixture patterns that must remain valid without importing private helpers into the new test.
- tests/test_analyzer_contract_v2.py independently asserts the v2 version, prompt text, five roles, exact keys, v2 word ranges, CTA behavior, and default positive/negative validation. The new tests must not change it.
- app/services/reference_profile.py demonstrates the repository convention for frozen dataclasses, asdict, canonical JSON, and SHA-256; the narrative profile follows the same deterministic style without importing reference rendering.
- Python requires >=3.11; Ruff targets py311; pytest uses -q --strict-markers; slow tests are excluded from the release command.

## Architecture and Dependency Graph

    Task 1: app/prompts/vision_first_story_analyzer_v3.txt
      -> direct pathlib UTF-8/LF/content tests only
    Task 2: fixed prompt resource
      -> narrative_identity.load_narrative_instruction("sharp_friend_v1")
      -> verified prompt SHA and canonical profile SHA
    Task 3: explicit analyzer_contract dispatch
      -> load_analyzer_instruction(narrative_profile_id="sharp_friend_v1")
      -> validate_analyzer_output(..., narrative_profile_id="sharp_friend_v1")
    Task 4-5: shared evidence gates plus v3 structural screens
    Task 6: status, verification, and one atomic release commit

The profile module must not import analyzer_contract.py, so the dispatch can import it locally without a cycle. The validator reuses the existing private structural functions inside the same module; it does not duplicate observation, continuity, coverage, claim, or evidence parsing. The v3 branch adds only profile-specific checks after those shared gates.

## Produced Interfaces

### Task 1 prompt boundary

Task 1 produces only the fixed file app/prompts/vision_first_story_analyzer_v3.txt. It does not produce a Python loader, prompt SHA acceptance, profile object, or contract hash. The direct test opens the file with pathlib.Path, decodes UTF-8, rejects any CR byte, and checks normative content/order.

### Task 2 app.services.narrative_identity

~~~python
from __future__ import annotations

from dataclasses import dataclass


class NarrativeIdentityError(ValueError):
    code = "narrative_identity_invalid"


@dataclass(frozen=True)
class NarrativeIdentityProfile:
    profile_id: str
    profile_version: str
    language: str
    identity: str
    target_word_min: int = 90
    target_word_max: int = 125
    passage_min: int = 4
    passage_max: int = 6
    allowed_ending_kinds: tuple[str, ...] = (
        "cliffhanger",
        "consequence",
        "open_question",
    )
    prompt_version: str = "vision-first-story-analyzer-v3"
    prompt_filename: str = "vision_first_story_analyzer_v3.txt"
    contract_sha256: str = ""


SHARP_FRIEND_V1: NarrativeIdentityProfile


def get_narrative_identity(profile_id: str) -> NarrativeIdentityProfile:
    """Return a verified profile or raise a safe NarrativeIdentityError."""


def load_narrative_instruction(
    profile_id: str,
) -> tuple[str, str, str]:
    """Return (prompt_version, prompt_sha256, normalized_lf_text)."""


def canonical_profile_contract_json(
    profile: NarrativeIdentityProfile,
    prompt_sha256: str,
) -> str:
    """Serialize the profile contract with contract_sha256 blanked."""
~~~

Task 2 creates this interface only after Task 1 has fixed the prompt. get_narrative_identity() accepts only sharp_friend_v1; unknown or empty IDs raise NarrativeIdentityError("unknown narrative identity") without a path, prompt body, secret, or raw exception. load_narrative_instruction() reads app/prompts/{profile.prompt_filename}, normalizes CRLF and lone CR to LF, computes the prompt SHA-256, verifies the embedded version line, computes the canonical profile contract with contract_sha256 blank, and compares its SHA-256 to the committed profile.contract_sha256. Any mismatch raises NarrativeIdentityError("narrative identity resource is invalid").

The contract hash intentionally excludes itself:

~~~python
def canonical_profile_contract_json(
    profile: NarrativeIdentityProfile,
    prompt_sha256: str,
) -> str:
    payload = {
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "language": profile.language,
        "identity": profile.identity,
        "target_word_min": profile.target_word_min,
        "target_word_max": profile.target_word_max,
        "passage_min": profile.passage_min,
        "passage_max": profile.passage_max,
        "allowed_ending_kinds": list(profile.allowed_ending_kinds),
        "prompt_version": profile.prompt_version,
        "prompt_filename": profile.prompt_filename,
        "prompt_sha256": prompt_sha256,
        "contract_sha256": "",
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
~~~

### Task 3 app.services.analyzer_contract

The public signatures become:

~~~python
def load_analyzer_instruction(
    *,
    narrative_profile_id: str | None = None,
) -> tuple[str, str, str]:
    """Load v2 by default or an explicitly selected verified profile."""


def validate_analyzer_output(
    output: Mapping[str, Any],
    *,
    expected_panel_ids: Sequence[str],
    narrative_profile_id: str | None = None,
) -> None:
    """Validate v2 by default or an explicitly selected profile contract."""
~~~

None is the exact v2 path. narrative_profile_id="sharp_friend_v1" dispatches to the v3 resource and validator. Any other non-None ID is converted to AnalyzerContractError("unknown narrative profile") with code analyzer_contract_invalid; there is no fallback to v2. The public argument is keyword-only so current positional/default callers do not change.

The v3 branch keeps _REQUIRED_OUTPUT_KEYS and _REQUIRED_OBSERVATION_KEYS exactly unchanged. It calls _validate_observations, _validate_coverage_manifest, _validate_continuity, and _validate_claims before profile-specific checks. It never mutates the output and never returns source dialogue text in an error or finding.

## Task 1: Fix the v3 prompt resource

**Files:**

- Create: app/prompts/vision_first_story_analyzer_v3.txt
- Test: tests/test_narrative_identity.py

**Consumes:** The existing repository prompt-resource convention and the exact v3 directives in the approved design. It does not consume narrative_identity.py or analyzer_contract.py.

**Produces:** A UTF-8 LF prompt resource with exact normative content. No profile object, prompt hash, contract hash, or loader acceptance is claimed at this task boundary.

- [ ] **Step 1: Write collection-safe direct resource tests before the resource exists.**

Use pathlib.Path from the test module and do not import app.services.narrative_identity or call load_analyzer_instruction. The test must fail in its body when the resource is absent:

~~~python
from pathlib import Path

import pytest


PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "prompts"
    / "vision_first_story_analyzer_v3.txt"
)


def _read_v3_prompt_directly() -> str:
    if not PROMPT_PATH.exists():
        pytest.fail(f"missing v3 prompt resource: {PROMPT_PATH.name}")
    raw = PROMPT_PATH.read_bytes()
    assert b"\r" not in raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(f"v3 prompt is not UTF-8: {exc}")


def test_v3_prompt_resource_is_lf_utf8_and_normative():
    prompt = _read_v3_prompt_directly()
    lowered = prompt.lower()
    required = (
        "contract id: vision_first_editorial_story_engine.analyzer",
        "version: vision-first-story-analyzer-v3",
        "narrative profile: sharp_friend_v1",
        "observe every ordered panel",
        "reconcile all panel, observation, chunk, coverage, continuity, synthesis, and claim gates before prose",
        "story_spine",
        "ending_kind",
        "wants",
        "obstacle",
        "decision",
        "consequence",
        "changed stakes",
        "unresolved direction",
        "contractions",
        "varied sentence lengths",
        "causal connectors",
        "selective evidence-grounded commentary",
        "four to six",
        "cliffhanger",
        "open_question",
        "spoken text",
        "do not generate display_text",
        "no fixed intro",
        "no channel cta",
        "copied speech-balloon dialogue",
        "do not invent an identity, motive, relationship, event, or causal link",
    )
    for fragment in required:
        assert fragment in lowered, fragment
    assert lowered.index("observe every ordered panel") < lowered.index("four to six")
    assert lowered.index("four to six") < lowered.index("ending_kind")
~~~

- [ ] **Step 2: Run the Task 1 RED command.**

Run on VPS:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py -q
~~~

Expected result: collection succeeds because the test imports only pathlib/pytest, and the single direct resource test fails because app/prompts/vision_first_story_analyzer_v3.txt is absent. An import or collection error is not an acceptable RED result.

- [ ] **Step 3: Create the exact v3 resource.**

The resource must use LF line endings and contain this complete normative content, with no generated display cues:

~~~text
VISION-FIRST STORY ANALYZER
Contract ID: vision_first_editorial_story_engine.analyzer
Version: vision-first-story-analyzer-v3
Narrative profile: sharp_friend_v1

This is a normative analyzer instruction contract. The output is evidence for a complete reconciled chapter, not a free-form recap. Follow every MUST rule and fail closed when a required structure or evidence link cannot be supplied.

1. MUST observe every ordered panel in the complete reconciled chapter before writing any prose. Never sample representative panels, omit source regions, or use a text-only or template fallback. Preserve panel_id, source_asset_id, strip_region_id, source_index, region_bounds, coverage_map_version, coverage_map_hash, and evidence references. Reconcile all panel, observation, chunk, coverage, continuity, synthesis, and claim gates before prose.

2. MUST track entities, aliases, motives, state changes, and causal links across sequential overlapping chunks. Carry the continuity ledger through every chunk and reconcile it after the final chunk. Record uncertainty and qualify interpretations rather than turning guesses into facts.

3. Build narrative_outline with exactly story_spine and ending_kind. story_spine has exactly who_wants_what, obstacle, decision, consequence, changed_stakes, and unresolved_question, which records the unresolved direction. Keep those as reasoning fields; do not force them into a checklist-shaped passage order.

4. Write in conversational American English as a clever, friendly, perceptive friend under controlled tension. Use contractions, varied sentence lengths, causal connectors, selective evidence-grounded commentary, and perceptive consequences or hidden clues only when the claims support them. Keep every interpretation qualified.

5. Return four to six script_passages. Each passage has exactly passage_id, editorial_role, text, claim_ids, and evidence_panel_ids. Passage IDs are unique. editorial_role is a nonempty semantic label; it is not a fixed vocabulary, order, or quota. Each passage has nonempty claim_ids, and its evidence_panel_ids cover every panel cited by those claims.

6. Set ending_kind to exactly cliffhanger, consequence, or open_question. An open_question final passage must end with ? and its unresolved question must be nonempty and evidence-grounded. A cliffhanger or consequence ending must not be forced to end with ?. Do not manufacture a question to satisfy the format.

7. The initial narration target is 90-125 whitespace-counted words overall, without rigid per-role budgets. A different grounded count is not by itself a contract failure. Spoken text keeps its punctuation and casing for later TTS. Do not generate display_text, subtitle timing, audio, or voice choices.

8. Do not write a panel inventory, repeated then/after-that chronology, fixed intro, title, cover line, forced catchphrase, channel CTA, generic hype, fake intensity, or certainty inflation. Do not copy speech-balloon dialogue. Do not invent an identity, motive, relationship, event, or causal link. Mark unsupported intent as qualified interpretation or leave it out.

9. Every factual or interpretive claim must have a claim ID and supporting evidence_panel_ids. Every claim must exist in evidence_graph and every interpretation must retain a nonempty qualification. Missing, foreign, unsupported, or uncovered evidence is a provider failure.

10. The human editor remains responsible for approval. This contract screens evidence and structure; it never repairs, rewrites, template-fills, or silently migrates a v2 output.
~~~

- [ ] **Step 4: Run the Task 1 GREEN command.**

Run the same focused command:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py -q
~~~

Expected result: the direct pathlib test passes. It proves only that the committed resource is valid UTF-8/LF and contains the normative v3 directives. It must not calculate or accept a prompt/profile hash and must not import the future profile loader. Stop this task here; Task 2 owns the hash boundary.

## Task 2: Create and verify the frozen narrative identity profile

**Files:**

- Create: app/services/narrative_identity.py
- Modify: tests/test_narrative_identity.py

**Consumes:** The now-fixed app/prompts/vision_first_story_analyzer_v3.txt from Task 1. The resource is the source for both prompt SHA and canonical profile SHA; no prompt text is duplicated in Python.

**Produces:** NarrativeIdentityProfile, NarrativeIdentityError, SHARP_FRIEND_V1, get_narrative_identity(), load_narrative_instruction(), and canonical_profile_contract_json().

- [ ] **Step 1: Add profile/hash tests after the Task 1 resource is green.**

Append imports and tests to the existing test file only after the direct resource test passes. Keep new-module imports inside test bodies so this task's missing implementation is a collection-clean body failure:

~~~python
import hashlib
import importlib
from dataclasses import replace


def _identity_module():
    try:
        return importlib.import_module("app.services.narrative_identity")
    except Exception as exc:
        pytest.fail(f"narrative identity import failed in the test body: {exc}")


def _identity_error(module):
    error_type = getattr(module, "NarrativeIdentityError", None)
    assert isinstance(error_type, type)
    assert issubclass(error_type, Exception)
    return error_type


def test_profile_is_frozen_and_has_exact_sharp_friend_identity_fields():
    module = _identity_module()
    profile = getattr(module, "SHARP_FRIEND_V1", None)
    assert profile.profile_id == "sharp_friend_v1"
    assert profile.profile_version == "1.0.0"
    assert profile.language == "en-US"
    assert profile.identity == "a clever, friendly, perceptive friend under controlled tension"
    assert (profile.target_word_min, profile.target_word_max) == (90, 125)
    assert (profile.passage_min, profile.passage_max) == (4, 6)
    assert profile.allowed_ending_kinds == (
        "cliffhanger",
        "consequence",
        "open_question",
    )
    assert profile.prompt_version == "vision-first-story-analyzer-v3"
    assert profile.prompt_filename == "vision_first_story_analyzer_v3.txt"
    assert len(profile.contract_sha256) == 64
    with pytest.raises((AttributeError, TypeError)):
        profile.profile_id = "other"


def test_unknown_profile_fails_without_leaking_resource_details():
    module = _identity_module()
    error_type = _identity_error(module)
    with pytest.raises(error_type, match="unknown narrative identity") as caught:
        module.get_narrative_identity("not_a_real_profile")
    assert "vision_first_story_analyzer_v3" not in str(caught.value)
    assert "/" not in str(caught.value)


def test_loader_returns_lf_prompt_and_matches_profile_contract():
    module = _identity_module()
    version, digest, text = module.load_narrative_instruction("sharp_friend_v1")
    assert version == "vision-first-story-analyzer-v3"
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert "\r" not in text
    assert "observe every ordered panel" in text.lower()
    assert module.get_narrative_identity("sharp_friend_v1").contract_sha256


def test_profile_loader_rejects_profile_hash_tampering(monkeypatch):
    module = _identity_module()
    original = module.SHARP_FRIEND_V1
    registry = dict(module._PROFILE_REGISTRY)
    registry["sharp_friend_v1"] = replace(original, contract_sha256="0" * 64)
    monkeypatch.setattr(module, "_PROFILE_REGISTRY", registry)
    with pytest.raises(module.NarrativeIdentityError):
        module.load_narrative_instruction("sharp_friend_v1")


def test_profile_loader_rejects_prompt_filename_tampering(monkeypatch):
    module = _identity_module()
    original = module.SHARP_FRIEND_V1
    registry = dict(module._PROFILE_REGISTRY)
    registry["sharp_friend_v1"] = replace(
        original,
        prompt_filename="vision_first_story_analyzer_v1.txt",
    )
    monkeypatch.setattr(module, "_PROFILE_REGISTRY", registry)
    with pytest.raises(module.NarrativeIdentityError):
        module.load_narrative_instruction("sharp_friend_v1")
~~~

Use dataclasses.replace as shown; each tamper test copies the private `_PROFILE_REGISTRY`, replaces only its `sharp_friend_v1` entry, and monkeypatches that copied registry. This proves the loader uses registry lookup rather than bypassing it through the exported constant. The tests do not edit the prompt file and assert a safe NarrativeIdentityError without returning path/content.

- [ ] **Step 2: Run the Task 2 RED command.**

Run:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py -q
~~~

Expected result: the direct Task 1 prompt test passes, while the profile/hash tests fail in their bodies because app.services.narrative_identity is absent. Collection and setup remain clean. Do not accept a failure caused by the prompt resource or a copied test fixture.

- [ ] **Step 3: Implement the frozen profile and safe loader.**

Use dataclass(frozen=True), Path(__file__).resolve().parents[1] / "prompts", read_text(encoding="utf-8"), LF normalization, and SHA-256. Define the exact fields and defaults from the Produced Interfaces block. Use a private registry containing only {"sharp_friend_v1": SHARP_FRIEND_V1}. Error messages must not include filesystem paths, prompt content, raw exceptions, keys, or provider data.

The loader verification sequence is:

1. Look up the exact profile ID.
2. Read the fixed Task 1 prompt and normalize CRLF and lone CR to LF.
3. Require the normalized prompt to contain Version: vision-first-story-analyzer-v3.
4. Compute prompt_sha256 from normalized UTF-8 bytes.
5. Serialize every profile field plus prompt_sha256 with contract_sha256 set to an empty string.
6. SHA-256 that canonical JSON using sorted keys, compact separators, and ensure_ascii=False.
7. Compare the computed canonical digest with the committed real literal in the resolved registry profile's `contract_sha256` (the `_PROFILE_REGISTRY["sharp_friend_v1"]` entry), never a detached constant that the loader bypasses.
8. Return the profile/resource only when all checks pass.

The canonical serializer is:

~~~python
def canonical_profile_contract_json(
    profile: NarrativeIdentityProfile,
    prompt_sha256: str,
) -> str:
    payload = {
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "language": profile.language,
        "identity": profile.identity,
        "target_word_min": profile.target_word_min,
        "target_word_max": profile.target_word_max,
        "passage_min": profile.passage_min,
        "passage_max": profile.passage_max,
        "allowed_ending_kinds": list(profile.allowed_ending_kinds),
        "prompt_version": profile.prompt_version,
        "prompt_filename": profile.prompt_filename,
        "prompt_sha256": prompt_sha256,
        "contract_sha256": "",
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
~~~

- [ ] **Step 4: Mechanically compute and commit the real profile literal.**

After Task 1's prompt is fixed, run this exact VPS command:

~~~bash
.venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path

prompt = Path("app/prompts/vision_first_story_analyzer_v3.txt").read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
payload = {
    "profile_id": "sharp_friend_v1",
    "profile_version": "1.0.0",
    "language": "en-US",
    "identity": "a clever, friendly, perceptive friend under controlled tension",
    "target_word_min": 90,
    "target_word_max": 125,
    "passage_min": 4,
    "passage_max": 6,
    "allowed_ending_kinds": ["cliffhanger", "consequence", "open_question"],
    "prompt_version": "vision-first-story-analyzer-v3",
    "prompt_filename": "vision_first_story_analyzer_v3.txt",
    "prompt_sha256": prompt_sha256,
    "contract_sha256": "",
}
canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
print("prompt_sha256=" + prompt_sha256)
print("contract_sha256=" + hashlib.sha256(canonical.encode("utf-8")).hexdigest())
PY
~~~

Put the exact lowercase 64-character second output into the frozen profile literal. Then call load_narrative_instruction("sharp_friend_v1") and verify it returns the same prompt SHA and the committed contract SHA. Never derive a new accepted contract SHA at runtime from a changed prompt; a changed prompt or profile field must fail closed.

- [ ] **Step 5: Run the Task 2 GREEN command.**

Run:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py -q
~~~

Expected result: the direct prompt test and all profile, lookup, LF loader, prompt version, prompt SHA, canonical profile verification, and tamper tests pass. This is the first task allowed to claim a committed contract hash.

## Task 3: Add explicit analyzer profile selection without changing v2 defaults

**Files:**

- Modify: app/services/analyzer_contract.py:96-105,364-476
- Modify: tests/test_narrative_identity.py

**Consumes:** The fixed prompt from Task 1 and verified NarrativeIdentityProfile/loader from Task 2.

**Produces:** Keyword-only narrative_profile_id selection on the loader and validator. All calls that omit it execute the current v2 code path.

- [ ] **Step 1: Add default-compatibility and dispatch RED tests.**

Add tests that prove explicit unknown profile failure and default v2 identity:

~~~python
def test_analyzer_loader_defaults_to_unchanged_v2_and_selects_v3_only_explicitly():
    module = importlib.import_module("app.services.analyzer_contract")
    default_version, default_digest, default_text = module.load_analyzer_instruction()
    assert default_version == "vision-first-story-analyzer-v2"
    assert "Version: vision-first-story-analyzer-v2" in default_text
    selected_version, selected_digest, selected_text = module.load_analyzer_instruction(
        narrative_profile_id="sharp_friend_v1"
    )
    assert selected_version == "vision-first-story-analyzer-v3"
    assert selected_digest != default_digest
    assert selected_text != default_text


def test_analyzer_unknown_explicit_profile_fails_closed_without_v2_fallback():
    module = importlib.import_module("app.services.analyzer_contract")
    with pytest.raises(module.AnalyzerContractError) as caught:
        module.load_analyzer_instruction(narrative_profile_id="missing_profile")
    assert caught.value.code == "analyzer_contract_invalid"
    assert "vision-first-story-analyzer-v2" not in str(caught.value)
    assert "missing_profile" not in str(caught.value)
~~~

- [ ] **Step 2: Run the dispatch RED test.**

Run:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py::test_analyzer_loader_defaults_to_unchanged_v2_and_selects_v3_only_explicitly -q
~~~

Expected result: collection succeeds and the body fails because the current loader has no keyword-only profile dispatch.

- [ ] **Step 3: Implement the loader dispatch.**

Keep the current v2 body intact in a private helper so its byte/hash behavior does not drift:

~~~python
def _load_v2_instruction() -> tuple[str, str, str]:
    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return PROMPT_VERSION, digest, normalized
    except (OSError, UnicodeError):
        raise AnalyzerContractError("analyzer instruction cannot be loaded") from None


def load_analyzer_instruction(
    *,
    narrative_profile_id: str | None = None,
) -> tuple[str, str, str]:
    if narrative_profile_id is None:
        return _load_v2_instruction()
    try:
        return narrative_identity.load_narrative_instruction(narrative_profile_id)
    except narrative_identity.NarrativeIdentityError:
        raise AnalyzerContractError("unknown narrative profile") from None
~~~

Import narrative_identity inside the explicit branch or as a module import that cannot create a cycle. Do not change PROMPT_VERSION, PROMPT_PATH, the v2 fixture, or the no-argument result.

- [ ] **Step 4: Implement validator profile dispatch.**

Change only the signature and final branch point:

~~~python
def validate_analyzer_output(
    output: Mapping[str, Any],
    *,
    expected_panel_ids: Sequence[str],
    narrative_profile_id: str | None = None,
) -> None:
    try:
        expected = _expected_panel_ids(expected_panel_ids)
        _validate_output(
            output,
            expected,
            narrative_profile_id=narrative_profile_id,
        )
    except AnalyzerContractError:
        raise
    except Exception:
        raise AnalyzerContractError("malformed analyzer output") from None
~~~

Extend _validate_output with a defaulted keyword-only profile parameter. When it is None, call the current _validate_narrative_outline and _validate_script_passages unchanged. When it equals sharp_friend_v1, call the v3 functions from Task 4. Any other non-None value raises AnalyzerContractError("unknown narrative profile") before output inspection. This preserves v1/v2 snapshots and prevents accidental profile fallback.

- [ ] **Step 5: Run dispatch GREEN and unchanged v2 snapshots.**

Run:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py tests/test_analyzer_contract.py \
  tests/test_analyzer_contract_v2.py -q
~~~

Expected result: new default/explicit selection tests and every current analyzer test pass. A changed v2 digest, v2 prompt path, or v2 positive/negative behavior is a stop condition.

## Task 4: Implement the flexible v3 structured validator

**Files:**

- Modify: app/services/analyzer_contract.py (profile-specific helpers adjacent to current narrative helpers)
- Modify: tests/test_narrative_identity.py

**Consumes:** Shared expected panel IDs and existing validators; NarrativeIdentityProfile bounds and ending kinds from Task 2.

**Produces:** v3 validation for flexible passages and evidence-grounded endings without changing v2 validation.

- [ ] **Step 1: Add positive v3 fixture helpers and body RED tests.**

Define local fixture builders in the new test file rather than importing private helpers from v1/v2 tests. The builder must create all six top-level keys, three ordered observations, two overlapping continuity chunks, at least two claims with nonempty qualifications, the six-field spine, and a configurable passage list. Use Mapping/Any types and a local importlib boundary:

~~~python
from typing import Any
import importlib
import pytest


def _v3_chapter(
    *,
    chapter_prefix: str,
    passages: list[dict[str, object]],
    ending_kind: str,
    dialogue: list[str] | None = None,
) -> dict[str, Any]:
    panel_ids = tuple(f"{chapter_prefix}-panel-{index}" for index in range(3))
    dialogue_values = dialogue or []
    observations = []
    for source_index, panel_id in enumerate(panel_ids):
        observations.append(
            {
                "panel_id": panel_id,
                "source_asset_id": f"{chapter_prefix}-asset",
                "strip_region_id": f"{chapter_prefix}-region-{source_index}",
                "source_index": source_index,
                "region_bounds": {"x": 0, "y": source_index * 100, "width": 800, "height": 100},
                "coverage_map_version": "coverage-v3-test",
                "coverage_map_hash": f"{chapter_prefix}-coverage",
                "visible_facts": [f"{chapter_prefix} fact {source_index}"],
                "dialogue_or_ocr": dialogue_values if source_index == 0 else [],
                "inferences": [f"{chapter_prefix} inference {source_index}"],
                "uncertainties": [],
                "evidence_refs": [panel_id],
            }
        )
    claims = [
        {
            "claim_id": f"{chapter_prefix}-claim-fact",
            "claim_type": "fact",
            "text": f"{chapter_prefix} fact claim is visible.",
            "qualification": "The panel visibly supports this fact.",
            "evidence_panel_ids": [panel_ids[0]],
        },
        {
            "claim_id": f"{chapter_prefix}-claim-interpretation",
            "claim_type": "interpretation",
            "text": f"{chapter_prefix} decision may change the route.",
            "qualification": "The sequence suggests this consequence but does not prove intent.",
            "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
        },
    ]
    return {
        "observations": observations,
        "continuity_ledger": {
            "chunks": [
                {"chunk_id": f"{chapter_prefix}-chunk-0", "panel_ids": list(panel_ids[:2])},
                {"chunk_id": f"{chapter_prefix}-chunk-1", "panel_ids": list(panel_ids[1:])},
            ],
            "entities": [
                {
                    "entity_id": f"{chapter_prefix}-entity",
                    "canonical_name": f"{chapter_prefix} witness",
                    "aliases": [],
                    "panel_ids": list(panel_ids),
                }
            ],
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {"claims": claims},
        "coverage_manifest": {
            "total_panels": 3,
            "processed_panels": 3,
            "panel_ids": list(panel_ids),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        },
        "narrative_outline": {
            "story_spine": {
                "who_wants_what": f"{chapter_prefix} wants an answer.",
                "obstacle": f"{chapter_prefix} faces a locked route.",
                "decision": f"{chapter_prefix} chooses a risky opening.",
                "consequence": f"{chapter_prefix} changes the immediate balance.",
                "changed_stakes": f"{chapter_prefix} may lose the next chance.",
                "unresolved_question": f"What will {chapter_prefix} do next?",
            },
            "ending_kind": ending_kind,
        },
        "script_passages": passages,
    }


def _validate_v3(chapter: dict[str, Any]) -> None:
    module = importlib.import_module("app.services.analyzer_contract")
    expected = tuple(item["panel_id"] for item in chapter["observations"])
    module.validate_analyzer_output(
        chapter,
        expected_panel_ids=expected,
        narrative_profile_id="sharp_friend_v1",
    )
~~~

Define _passages(prefix: str, count: int, ending_kind: str, total_words: int | None = None) -> list[dict[str, object]] with four and six distinct semantic labels and distinct first three words. When total_words is supplied, allocate the requested number across contiguous passage text while keeping each passage grounded and nonempty. Positive fixtures include contractions and varied sentence lengths. Keep normal positives within 90-125 words, then add one valid case below or above that range to prove the target is advisory in Slice D.

Add tests for four and six passages, consequence/cliffhanger/open_question endings, contractions, varied labels/order, and advisory word counts:

~~~python
@pytest.mark.parametrize("count", (4, 6))
def test_v3_accepts_four_or_six_grounded_passages(count):
    chapter = _v3_chapter(
        chapter_prefix=f"chapter-{count}",
        passages=_passages(f"chapter-{count}", count, "consequence"),
        ending_kind="consequence",
    )
    _validate_v3(chapter)


def test_v3_accepts_grounded_open_question_and_non_question_consequence():
    consequence = _v3_chapter(
        chapter_prefix="consequence",
        passages=_passages("consequence", 4, "consequence"),
        ending_kind="consequence",
    )
    _validate_v3(consequence)
    assert not consequence["script_passages"][-1]["text"].rstrip().endswith("?")

    question = _v3_chapter(
        chapter_prefix="question",
        passages=_passages("question", 6, "open_question"),
        ending_kind="open_question",
    )
    _validate_v3(question)
    assert question["script_passages"][-1]["text"].rstrip().endswith("?")


def test_v3_target_word_range_is_advisory_not_a_hard_failure():
    chapter = _v3_chapter(
        chapter_prefix="short-grounded",
        passages=_passages("short-grounded", 4, "cliffhanger", total_words=72),
        ending_kind="cliffhanger",
    )
    _validate_v3(chapter)
~~~

- [ ] **Step 2: Run the v3 shape RED tests.**

Run:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py -q
~~~

Expected result: collection succeeds; explicit v3 tests fail because the current validator accepts no profile argument and only accepts the v2 five-role shape.

- [ ] **Step 3: Add the exact v3 outline and passage validators.**

The v3 outline validator requires exactly story_spine and ending_kind, reuses the six current spine fields, and accepts only the profile's three ending kinds:

~~~python
_V3_GENERIC_HYPE = (
    "epic battle",
    "unstoppable attack",
    "insane power",
)


def _validate_narrative_outline_v3(
    value: Any,
    profile: NarrativeIdentityProfile,
) -> None:
    outline = _mapping(value, "narrative_outline")
    if set(outline) != {"story_spine", "ending_kind"}:
        _fail("v3 narrative_outline keys do not match the contract")
    spine = _mapping(outline["story_spine"], "story_spine")
    if set(spine) != set(_STORY_SPINE_FIELDS):
        _fail("story_spine must contain all six reasoning fields")
    for field in _STORY_SPINE_FIELDS:
        _nonempty_string(spine.get(field), f"story_spine.{field}")
    if outline["ending_kind"] not in profile.allowed_ending_kinds:
        _fail("ending_kind is not supported by the narrative profile")


def _ngrams(words: list[str], size: int) -> set[tuple[str, ...]]:
    return {
        tuple(words[index : index + size])
        for index in range(max(0, len(words) - size + 1))
    }


def _source_dialogue_ngrams(observations: Any) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for observation_value in observations:
        observation = _mapping(observation_value, "observation")
        for line in _string_list(observation["dialogue_or_ocr"], "dialogue_or_ocr"):
            result.update(_ngrams(_normalized_lexical_words(line), 4))
    return result
~~~

The complete v3 passage validator must require a list length between profile.passage_min and profile.passage_max, the existing exact five passage keys, unique passage IDs, nonempty semantic editorial roles, nonempty text, nonempty claim IDs, known claims, and evidence covering every referenced claim. It rejects existing CTA patterns and only epic battle, unstoppable attack, and insane power; broad words such as hero or action remain allowed. It compares normalized lexical four-word windows from dialogue_or_ocr strings to passage windows and never includes source text in an error. It does not enforce total word count or per-role ranges.

Use this complete implementation shape, passing the exact outline into the ending check:

~~~python
def _validate_script_passages_v3(
    value: Any,
    expected: tuple[str, ...],
    claim_evidence: dict[str, set[str]],
    observations: Any,
    outline: Mapping[str, Any],
    profile: NarrativeIdentityProfile,
) -> None:
    if not isinstance(value, list) or not profile.passage_min <= len(value) <= profile.passage_max:
        _fail("script_passages must contain four to six passages")
    passage_ids: set[str] = set()
    source_dialogue = _source_dialogue_ngrams(observations)
    for passage_value in value:
        passage = _mapping(passage_value, "script passage")
        if set(passage) != _SCRIPT_PASSAGE_KEYS:
            _fail("script passage keys do not match the v3 contract")
        passage_id = _nonempty_string(passage["passage_id"], "passage_id")
        if passage_id in passage_ids:
            _fail("passage IDs must be unique")
        passage_ids.add(passage_id)
        _nonempty_string(passage["editorial_role"], "editorial_role")
        text = _nonempty_string(passage["text"], "script passage text")
        if _contains_channel_cta(text):
            _fail("generic channel CTA language is not allowed")
        normalized_text = " ".join(_normalized_lexical_words(text))
        if any(marker in normalized_text for marker in _V3_GENERIC_HYPE):
            _fail("generic hype language is not allowed")
        if _ngrams(_normalized_lexical_words(text), 4) & source_dialogue:
            _fail("script passage copies source dialogue")
        claim_ids = _string_list(
            passage["claim_ids"], "passage claim_ids", allow_empty=False
        )
        if not set(claim_ids) <= set(claim_evidence):
            _fail("script passage references an unknown claim")
        evidence = set(
            _panel_refs(passage["evidence_panel_ids"], expected, "passage evidence")
        )
        required = set().union(
            *(claim_evidence[claim_id] for claim_id in claim_ids)
        )
        if not required <= evidence:
            _fail("script passage evidence does not cover its claims")
    final_text = _nonempty_string(
        value[-1]["text"], "final script passage text"
    ).rstrip()
    _validate_v3_ending(outline, final_text, profile)


def _validate_v3_ending(
    outline: Mapping[str, Any],
    final_text: str,
    profile: NarrativeIdentityProfile,
) -> None:
    ending_kind = outline["ending_kind"]
    unresolved = _nonempty_string(
        outline["story_spine"]["unresolved_question"],
        "story_spine.unresolved_question",
    )
    if ending_kind == "open_question":
        if not final_text.endswith("?") or not unresolved:
            _fail("open_question ending must be evidence-grounded and end with ?")
    elif ending_kind in {"cliffhanger", "consequence"} and final_text.endswith("?"):
        _fail("non-question ending kind must not end with ?")
~~~

The implementation must pass narrative_outline into _validate_v3_ending, must not enforce the 90-125 word target, and must not enforce per-role word ranges. editorial_role is only _nonempty_string; its vocabulary and order are free. The existing claim validator still requires every interpretation to have a nonempty qualification.

- [ ] **Step 4: Run the v3 positive and hard-gate GREEN tests.**

Run:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py -q
~~~

Expected result: four/six passage structures, consequence/cliffhanger/open-question endings, contractions, varied labels, and advisory word counts pass. The v2 default tests are run again in Task 5.

## Task 5: Complete the unrelated-chapter negative matrix and v2 regression proof

**Files:**

- Modify: tests/test_narrative_identity.py

**Consumes:** The fixed Task 1 prompt, verified Task 2 profile, and both explicit analyzer dispatches from Task 3.

**Produces:** A focused fixture suite proving evidence grounding and anti-template boundaries without importing private helpers from other modules.

- [ ] **Step 1: Add two distinct chapter fixtures.**

Use two local builders with different panel IDs, visible facts, entities, claims, causal consequences, and dialogue strings. Chapter A may describe a locked dock and a compass; Chapter B must describe a tower alarm and a missing map. Both contain the same structural keys but different prose and evidence IDs:

~~~python
def test_v3_changes_with_evidence_and_does_not_require_a_fixed_opening():
    first = _v3_chapter(
        chapter_prefix="dock",
        passages=_passages("dock", 4, "consequence"),
        ending_kind="consequence",
    )
    second = _v3_chapter(
        chapter_prefix="tower",
        passages=_passages("tower", 6, "cliffhanger"),
        ending_kind="cliffhanger",
    )
    _validate_v3(first)
    _validate_v3(second)
    assert first["observations"] != second["observations"]
    assert first["script_passages"][0]["text"] != second["script_passages"][0]["text"]
~~~

The differing labels and openings prove v3 does not require the v2 hook/setup/escalation/payoff vocabulary or a fixed opening sentence. Do not add a hard naturalness quota here; Slice E owns advisory metrics.

- [ ] **Step 2: Add the complete v3 rejection matrix.**

Parameterize mutations over a valid four-passage chapter and assert AnalyzerContractError with code analyzer_contract_invalid:

~~~python
@pytest.mark.parametrize(
    "mutation",
    (
        "missing_claim",
        "foreign_panel",
        "unsupported_claim",
        "unqualified_interpretation",
        "copied_dialogue",
        "cta",
        "generic_hype",
        "ending_mismatch",
        "unknown_ending_kind",
    ),
)
def test_v3_rejects_ungrounded_or_forbidden_narration(mutation):
    module = importlib.import_module("app.services.analyzer_contract")
    chapter = _v3_chapter(
        chapter_prefix="reject",
        passages=_passages("reject", 4, "consequence"),
        ending_kind="consequence",
        dialogue=["the marked gate opens for the patient red-eyed stranger"],
    )
    passage = chapter["script_passages"][0]
    if mutation == "missing_claim":
        passage["claim_ids"] = []
    elif mutation == "foreign_panel":
        passage["evidence_panel_ids"] = ["foreign-panel"]
    elif mutation == "unsupported_claim":
        passage["claim_ids"] = ["claim-not-in-graph"]
    elif mutation == "unqualified_interpretation":
        chapter["evidence_graph"]["claims"][1]["qualification"] = ""
    elif mutation == "copied_dialogue":
        passage["text"] = "The marked gate opens for the patient red-eyed stranger."
    elif mutation == "cta":
        passage["text"] = "The gate shifts, so subscribe for more."
    elif mutation == "generic_hype":
        passage["text"] = "The epic battle changes the route."
    elif mutation == "ending_mismatch":
        chapter["script_passages"][-1]["text"] += "?"
    else:
        chapter["narrative_outline"]["ending_kind"] = "teaser"
    with pytest.raises(module.AnalyzerContractError) as caught:
        _validate_v3(chapter)
    assert caught.value.code == "analyzer_contract_invalid"
    assert "marked gate opens" not in str(caught.value)
~~~

The concrete fixture text for each mutation must keep other gates valid enough to reach the intended check. For copied_dialogue, use four consecutive lexical words from the dialogue string; for generic_hype, use exactly one versioned marker such as epic battle; for CTA, use an engagement request. Add separate tests that delete a required observation, set source_content_coverage_ratio below 1.0, or remove a continuity panel; those fail through the existing shared gates.

- [ ] **Step 3: Prove v2 defaults and snapshots remain exact.**

The test file must call the default loader and default validator on a copied v2 fixture and assert the v2 version/hash behavior. Do not modify v1/v2 tests or the snapshot file. Run:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py tests/test_analyzer_contract.py \
  tests/test_analyzer_contract_v2.py -q
~~~

Expected result: all v3 tests and every pre-existing v1/v2 test pass. If a default call chooses v3, stop and fix dispatch rather than changing a fixture.

## Task 6: Release verification, status handoff, and one atomic publication commit

**Files:**

- Modify after green only: docs/STATUS.md
- Stage exactly: app/services/narrative_identity.py, app/services/analyzer_contract.py, app/prompts/vision_first_story_analyzer_v3.txt, tests/test_narrative_identity.py, docs/STATUS.md

**Consumes:** Tasks 1-5 and exact current parent ad41f80d09e3d39755c3cd2725a1aa29abb2cac3.

**Produces:** One reviewed local commit and a fast-forward-only GitHub main update. No runtime artifacts.

- [ ] **Step 1: Update STATUS only after the implementation matrix is green.**

Add a concise top entry recording:

- sharp_friend_v1, profile version, prompt version, prompt SHA-256, and canonical profile contract SHA-256;
- the exact Task 1 RED/GREEN direct prompt counts and Task 2 RED/GREEN profile counts;
- focused analyzer/v2 totals and exact full non-slow collected/passed/failed/skipped/duration totals;
- that default v2 loader/validator and vision_prompt_snapshot.sha256 remain unchanged;
- that v3 accepts four-to-six passages, uses advisory 90-125 words, supports consequence/cliffhanger/open_question, and rejects CTA/hype/copied dialogue/unsupported evidence;
- that no provider, pipeline, DB, voice, audio, render, or API wiring was changed;
- rollback parent ad41f80d09e3d39755c3cd2725a1aa29abb2cac3 and next owner/task: Slice E naturalness/QC/synthesis wiring.

Do not claim runtime provider behavior or human approval from this contract-only slice.

- [ ] **Step 2: Run the exact focused and release verification commands.**

Run the focused matrix:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
  tests/test_narrative_identity.py tests/test_analyzer_contract.py \
  tests/test_analyzer_contract_v2.py -q
~~~

Run Ruff on only changed Python/test files:

~~~bash
.venv/bin/ruff check app/services/narrative_identity.py \
  app/services/analyzer_contract.py tests/test_narrative_identity.py
~~~

Run compile and whitespace checks:

~~~bash
.venv/bin/python -m compileall -q app
git diff --check
git diff --numstat
git diff --ignore-space-at-eol --stat
git diff --stat
~~~

Run the exact full non-slow suite with the VPS FFmpeg path:

~~~bash
PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest -q -m "not slow" --disable-warnings
~~~

Record collected, passed, failed, skipped, duration, and exit code in STATUS. The suite must exit 0. No generated media, database, WAL, prompt copy, or temporary patch may appear in git status.

- [ ] **Step 3: Perform the final allowlist and secret review.**

Run:

~~~bash
git status --short
git diff --name-only
git diff --cached --name-only
git diff --cached --check
git diff --cached --numstat
git diff --cached --ignore-space-at-eol --stat
git diff --cached | grep -E -i 'sk-[A-Za-z0-9]{20,}|api[_-]?key|authorization:|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|ghp_[A-Za-z0-9]+' || true
~~~

The staged names must be exactly the five paths listed above. The high-confidence scan must print no secret-shaped value; do not print credential contents while diagnosing a match.

- [ ] **Step 4: Create the single implementation commit.**

After Sol's final audit of the staged diff, commit once on VPS:

~~~bash
git add app/services/narrative_identity.py \
  app/services/analyzer_contract.py \
  app/prompts/vision_first_story_analyzer_v3.txt \
  tests/test_narrative_identity.py \
  docs/STATUS.md
git commit -m "feat: define sharp friend narrative identity"
~~~

Verify the new commit has parent ad41f80d09e3d39755c3cd2725a1aa29abb2cac3, contains exactly the five implementation paths, and leaves the VPS worktree clean. Do not create an intermediate task commit.

- [ ] **Step 5: Publish the exact commit through Windows transport.**

Use the isolated Windows directory C:\Users\yxxrn\Documents\AutoManhwa\transport-slice-d-sharp-friend-20260811. Do not edit source in that clone. Create a mechanical VPS bundle, transfer it with SFTP, and verify its SHA-256 before import:

~~~bash
git bundle create /tmp/task-slice-d-sharp-friend.bundle --all
sha256sum /tmp/task-slice-d-sharp-friend.bundle
git bundle verify /tmp/task-slice-d-sharp-friend.bundle
~~~

Clone/fetch the HTTPS repository into that named transport clone, verify refs/remotes/origin/main is the VPS commit parent, fetch the bundle into refs/remotes/transport/slice-d, checkout main from refs/remotes/origin/main, merge --ff-only refs/remotes/transport/slice-d, and push only main:main. Before push, git ls-remote must equal the VPS commit parent. After push, git ls-remote refs/heads/main must equal the new full commit SHA, transport ahead/behind must both be zero, and both VPS and transport worktrees must be clean. Remove only the exact temporary bundle/batch files after parity; keep the named transport clone.

- [ ] **Step 6: Record the publication handoff.**

Report the full commit SHA, parent, changed paths, profile/prompt/contract hashes, exact RED/GREEN/full counts, staged allowlist, secret/diff checks, GitHub SHA parity, clean status, rollback point, and next Slice E boundary. State explicitly that no provider call, TTS/audio, render, DB, migration, or runtime script wiring occurred.

## Interfaces Deferred to Slice E

Slice D must not implement these interfaces, but its names and selected profile identity must leave a stable handoff:

- Provider request construction will later carry narrative_profile_id="sharp_friend_v1" and exact prompt version/hash; Slice D does not modify VisionObservationRequest or VisionChapterSynthesisRequest.
- Pipeline persistence will later store profile ID/version/prompt hash beside StoryAnalysis and ScriptVersion; Slice D does not modify ORM or JSON persistence.
- Slice E will add advisory naturalness metrics, warning/blocker classification, synthesis wiring, API/status exposure, and explicit human-review integration. It must call the v3 validator explicitly and must not route v3 output through the v2 fixed-role validator.
- Spoken text remains punctuation-bearing; existing display-text derivation remains outside this slice and must not be duplicated here.

## Acceptance Matrix

| Boundary | Required proof | Owner in this plan |
|---|---|---|
| Prompt resource | UTF-8 LF, v3 version, full-panel and sharp-friend directives, direct Path test | Task 1 |
| Profile identity | Frozen sharp_friend_v1, exact fields, safe lookup, real canonical hash | Task 2 |
| Prompt/hash boundary | Prompt SHA and profile SHA derive from the fixed Task 1 bytes; tamper fails closed | Task 2 |
| Dispatch | No profile means exact v2; only explicit sharp_friend_v1 selects v3; unknown never falls back | Task 3 |
| Structural output | Six top-level keys, ordered observations, coverage, continuity, claims, qualifications unchanged | Tasks 3-4 |
| Flexible prose | Four or six positive passages, semantic roles, contractions, varied lengths, no per-role budgets | Task 4 |
| Ending | Consequence/cliffhanger may end without ?; open_question requires ? and unresolved direction | Task 4 |
| Hard screens | Unsupported/foreign refs, unqualified interpretation, copied dialogue, CTA, generic hype, ending mismatch reject | Task 5 |
| Advisory target | Outside 90-125 does not fail Slice D by word count alone | Tasks 4-5 |
| Compatibility | v2 analyzer tests and prompt snapshot remain green without edits | Task 5 |
| Scope | No provider, pipeline, DB, voice, audio, render, API, or migration change | All tasks |
| Release | Exact five-path stage, full non-slow green, clean worktrees, fast-forward main-only publication | Task 6 |

## Self-Review Before Implementation Handoff

- Task 1 cannot claim a loader or hash: it creates only the v3 prompt and direct pathlib content/LF tests.
- Task 2 starts only after the fixed Task 1 resource is green, computes the real prompt SHA and canonical profile SHA mechanically, and reaches the frozen profile/loader GREEN gate.
- The default public loader and validator signatures retain v2 behavior; profile selection is keyword-only and explicit.
- The v3 shape keeps the six existing top-level keys and five existing passage keys while changing only passage count, semantic roles, ending metadata, and profile-specific hard screens.
- Shared observation, coverage, continuity, claim, qualification, and evidence validators run before v3 prose checks.
- Unknown profile IDs and prompt/profile hash drift fail closed without paths, prompt bodies, raw provider output, or secrets in errors.
- The prompt says all ordered panels precede prose, preserves punctuation-bearing spoken text, and explicitly forbids generated display text.
- The 90-125 target is advisory in Slice D; no rigid per-role budget or forced question is reintroduced.
- The copied-dialogue check uses normalized local four-word windows and never returns source text.
- v2 fixtures, tests, and snapshot remain outside the change set and must stay green.
- The plan contains no runtime provider call, pipeline wiring, ORM migration, voice selection, audio generation, or render work.
- Implementation docs are updated only after green verification, and the single commit is not created until Sol reviews the exact staged allowlist.
