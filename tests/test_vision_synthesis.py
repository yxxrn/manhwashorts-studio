"""RED contract for chapter-wide vision synthesis before pipeline integration."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Mapping

import httpx
import pytest

PANEL_IDS = ("panel-a", "panel-b", "panel-c")


class _Response:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _vision_module():
    try:
        return importlib.import_module("app.services.vision_adapter")
    except Exception as exc:
        pytest.fail(f"vision adapter import boundary is unavailable: {exc}")


def _instruction_contract():
    contract = importlib.import_module("app.services.analyzer_contract")
    loader = getattr(contract, "load_analyzer_instruction", None)
    assert callable(loader), "committed analyzer instruction loader is missing"
    result = loader()
    assert isinstance(result, tuple) and len(result) == 3
    version, digest, text = result
    assert isinstance(version, str) and isinstance(digest, str)
    assert isinstance(text, str) and text
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    return version, digest, text


def _request_type(module):
    request_type = getattr(module, "VisionChapterSynthesisRequest", None)
    assert isinstance(
        request_type, type
    ), "VisionChapterSynthesisRequest is missing from vision_adapter"
    return request_type


def _provider(module, mock_provider_url):
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key="sk-synthesis-test-key",
    )
    synthesize = getattr(provider, "synthesize", None)
    assert callable(synthesize), "VisionObservationProvider.synthesize is missing"
    return provider


def _observations():
    return tuple(
        {
            "panel_id": panel_id,
            "source_asset_id": f"asset-{panel_id}",
            "strip_region_id": f"region-{panel_id}",
            "source_index": source_index,
            "region_bounds": {
                "x": 0,
                "y": source_index * 100,
                "width": 800,
                "height": 100,
            },
            "coverage_map_version": "coverage-v1",
            "coverage_map_hash": "coverage-hash-v1",
            "visible_facts": [f"visible fact for {panel_id}"],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
            "evidence_refs": [panel_id],
        }
        for source_index, panel_id in enumerate(PANEL_IDS)
    )


def _coverage_manifest():
    return {
        "total_panels": 3,
        "processed_panels": 3,
        "panel_ids": list(PANEL_IDS),
        "source_content_coverage_ratio": 1.0,
        "unresolved_material_area": 0,
        "material_unresolved_regions": [],
        "reconciliation_complete": True,
    }


def _chunks():
    return (
        {
            "chunk_id": "chunk-0",
            "panel_ids": ["panel-a", "panel-b"],
            "overlap_with_next": ["panel-b"],
        },
        {
            "chunk_id": "chunk-1",
            "panel_ids": ["panel-b", "panel-c"],
            "overlap_with_previous": ["panel-b"],
        },
    )


def _request(
    module,
    *,
    expected_panel_ids=None,
    ordered_observations=None,
    coverage_manifest=None,
    chunks=None,
    instruction_version=None,
    instruction_sha256=None,
    instruction_text=None,
):
    committed_version, committed_digest, committed_text = _instruction_contract()
    if instruction_version is None:
        instruction_version = committed_version
    if instruction_sha256 is None:
        instruction_sha256 = committed_digest
    if instruction_text is None:
        instruction_text = committed_text
    request_type = _request_type(module)
    return request_type(
        analysis_run_id="run-synthesis-001",
        instruction_version=instruction_version,
        instruction_sha256=instruction_sha256,
        instruction_text=instruction_text,
        expected_panel_ids=tuple(
            PANEL_IDS if expected_panel_ids is None else expected_panel_ids
        ),
        coverage_manifest=copy.deepcopy(
            _coverage_manifest()
            if coverage_manifest is None
            else coverage_manifest
        ),
        ordered_observations=tuple(
            copy.deepcopy(
                _observations()
                if ordered_observations is None
                else ordered_observations
            )
        ),
        chunks=tuple(copy.deepcopy(_chunks() if chunks is None else chunks)),
    )


def _valid_output():
    observations = list(copy.deepcopy(_observations()))
    return {
        "observations": observations,
        "continuity_ledger": {
            "chunks": list(_chunks()),
            "entities": [
                {
                    "entity_id": "entity-lead",
                    "canonical_name": "Lead",
                    "aliases": ["the investigator"],
                    "panel_ids": list(PANEL_IDS),
                }
            ],
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "claim-panel-sequence",
                    "claim_type": "fact",
                    "text": "The lead follows the visible trail across the chapter.",
                    "qualification": "The ordered panels show the trail changing location.",
                    "evidence_panel_ids": list(PANEL_IDS),
                }
            ]
        },
        "coverage_manifest": _coverage_manifest(),
        "narrative_outline": {
            "story_spine": {
                "who_wants_what": "The lead wants to find the missing courier.",
                "obstacle": "The trail moves beyond the guarded road.",
                "decision": "The lead follows the last visible clue.",
                "consequence": "The search reaches an unfamiliar tower.",
                "changed_stakes": "The clue may have led the lead into a trap.",
                "unresolved_question": "Who lit the tower lantern?",
            }
        },
        "script_passages": [
            {
                "passage_id": "passage-sequence-hook",
                "editorial_role": "hook",
                "text": "A visible clue appears before the lead can decide whether to follow it in the dark.",
                "claim_ids": ["claim-panel-sequence"],
                "evidence_panel_ids": list(PANEL_IDS),
            },
            {
                "passage_id": "passage-sequence-setup",
                "editorial_role": "setup",
                "text": "The lead studies the clue while the surrounding panels show a path toward an uncertain destination and leave the lead with one direction.",
                "claim_ids": ["claim-panel-sequence"],
                "evidence_panel_ids": list(PANEL_IDS),
            },
            {
                "passage_id": "passage-sequence-escalation",
                "editorial_role": "escalation",
                "text": "That movement raises the stakes because the clue points forward, yet the lead still cannot see who arranged it or what waits beyond the next panel, before the trail can disappear entirely.",
                "claim_ids": ["claim-panel-sequence"],
                "evidence_panel_ids": list(PANEL_IDS),
            },
            {
                "passage_id": "passage-sequence-insight",
                "editorial_role": "editorial_insight",
                "text": "The detail matters because a quiet image can change the lead's safest choice without warning while the clue remains visible.",
                "claim_ids": ["claim-panel-sequence"],
                "evidence_panel_ids": list(PANEL_IDS),
            },
            {
                "passage_id": "passage-sequence-payoff",
                "editorial_role": "payoff_open_loop",
                "text": "Who placed the clue there, and what will the next panel reveal?",
                "claim_ids": ["claim-panel-sequence"],
                "evidence_panel_ids": list(PANEL_IDS),
            },
        ],
    }


def _install_response(monkeypatch, module, payload, captured=None):
    content = json.dumps(payload, ensure_ascii=False)

    def fake_post(*args, **kwargs):
        if captured is not None:
            captured.append({"args": args, "kwargs": kwargs})
        return _Response(content)

    monkeypatch.setattr(module.httpx, "post", fake_post)


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _input_ledger(request):
    return {
        "expected_panel_ids": request.expected_panel_ids,
        "coverage_manifest": request.coverage_manifest,
        "ordered_observations": request.ordered_observations,
        "chunks": request.chunks,
    }


def _assert_preflight_rejected(module, provider, request, monkeypatch):
    def network_is_forbidden(*args, **kwargs):
        raise AssertionError("preflight must reject before network")

    monkeypatch.setattr(module.httpx, "post", network_is_forbidden)
    error_type = getattr(module, "VisionCapabilityError", None)
    assert isinstance(error_type, type)
    with pytest.raises(error_type):
        provider.synthesize(request)


def test_synthesis_sends_exact_prompt_complete_ordered_evidence_and_no_images(
    mock_provider_url, monkeypatch
):
    module = _vision_module()
    request = _request(module)
    provider = _provider(module, mock_provider_url)
    captured = []
    _install_response(monkeypatch, module, _valid_output(), captured)

    result = provider.synthesize(request)

    assert len(captured) == 1
    body = captured[0]["kwargs"]["json"]
    body_json = json.dumps(body, ensure_ascii=False, sort_keys=True)
    assert request.instruction_text in tuple(_walk_strings(body))
    assert request.instruction_sha256 in body_json
    assert body["response_format"]["type"] in {"json_object", "json_schema"}
    assert [
        body_json.index(panel_id) for panel_id in request.expected_panel_ids
    ] == sorted(body_json.index(panel_id) for panel_id in request.expected_panel_ids)
    for observation in request.ordered_observations:
        assert observation["panel_id"] in body_json
        assert observation["visible_facts"][0] in body_json
    for chunk in request.chunks:
        assert chunk["chunk_id"] in body_json
    assert "source_content_coverage_ratio" in body_json
    assert "data:image" not in body_json.lower()
    assert "base64" not in body_json.lower()

    ledger_json = json.dumps(_input_ledger(request), ensure_ascii=False).lower()
    assert "filename" not in ledger_json
    assert "list position" not in ledger_json
    assert "recap" not in ledger_json
    assert "story_spine" not in ledger_json
    assert "payload" not in ledger_json
    assert result == _valid_output()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("instruction_version", "vision-first-story-analyzer-other"),
        ("instruction_sha256", "b" * 64),
    ),
)
def test_synthesis_preflight_rejects_prompt_version_or_hash_mismatch(
    mock_provider_url, monkeypatch, field, value
):
    module = _vision_module()
    request = _request(module, **{field: value})
    provider = _provider(module, mock_provider_url)
    _assert_preflight_rejected(module, provider, request, monkeypatch)


def test_synthesis_preflight_rejects_self_matching_noncommitted_prompt(
    mock_provider_url, monkeypatch
):
    module = _vision_module()
    arbitrary_text = "This is not the committed analyzer instruction.\n"
    request = _request(
        module,
        instruction_text=arbitrary_text,
        instruction_sha256=hashlib.sha256(
            arbitrary_text.encode("utf-8")
        ).hexdigest(),
    )
    provider = _provider(module, mock_provider_url)
    _assert_preflight_rejected(module, provider, request, monkeypatch)


@pytest.mark.parametrize(
    "expected_panel_ids",
    (
        ("panel-a", "panel-b"),
        ("panel-a", "panel-a", "panel-c"),
        ("panel-a", "panel-foreign", "panel-c"),
        ("panel-c", "panel-b", "panel-a"),
    ),
    ids=("missing", "duplicate", "foreign", "reordered"),
)
def test_synthesis_preflight_rejects_invalid_expected_panel_order(
    mock_provider_url, monkeypatch, expected_panel_ids
):
    module = _vision_module()
    request = _request(module, expected_panel_ids=expected_panel_ids)
    provider = _provider(module, mock_provider_url)
    _assert_preflight_rejected(module, provider, request, monkeypatch)


def _observation_variant(kind):
    observations = list(_observations())
    if kind == "missing":
        return tuple(observations[:2])
    if kind == "duplicate":
        observations[1]["panel_id"] = "panel-a"
    elif kind == "foreign":
        observations[1]["panel_id"] = "panel-foreign"
    elif kind == "reordered":
        observations.reverse()
    elif kind == "missing-lineage":
        observations[1].pop("coverage_map_hash")
    elif kind == "bad-source-index":
        observations[1]["source_index"] = 0
    elif kind == "foreign-evidence":
        observations[1]["evidence_refs"] = ["panel-foreign"]
    elif kind == "missing-own-evidence":
        observations[1]["evidence_refs"] = ["panel-a"]
    return tuple(observations)


@pytest.mark.parametrize(
    "kind",
    (
        "missing",
        "duplicate",
        "foreign",
        "reordered",
        "missing-lineage",
        "bad-source-index",
        "foreign-evidence",
        "missing-own-evidence",
    ),
)
def test_synthesis_preflight_rejects_observation_inventory_errors(
    mock_provider_url, monkeypatch, kind
):
    module = _vision_module()
    request = _request(module, ordered_observations=_observation_variant(kind))
    provider = _provider(module, mock_provider_url)
    _assert_preflight_rejected(module, provider, request, monkeypatch)


@pytest.mark.parametrize("field", ("ratio", "unresolved", "reconciled"))
def test_synthesis_preflight_rejects_incomplete_coverage_manifest(
    mock_provider_url, monkeypatch, field
):
    module = _vision_module()
    coverage = _coverage_manifest()
    if field == "ratio":
        coverage["source_content_coverage_ratio"] = 0.99
    elif field == "unresolved":
        coverage["unresolved_material_area"] = 1
        coverage["material_unresolved_regions"] = ["region-gap"]
    else:
        coverage["reconciliation_complete"] = False
    request = _request(module, coverage_manifest=coverage)
    provider = _provider(module, mock_provider_url)
    _assert_preflight_rejected(module, provider, request, monkeypatch)


@pytest.mark.parametrize("kind", ("missing-panel", "no-adjacent-overlap"))
def test_synthesis_preflight_rejects_invalid_chunk_plan(
    mock_provider_url, monkeypatch, kind
):
    module = _vision_module()
    chunks = list(_chunks())
    if kind == "missing-panel":
        chunks[1]["panel_ids"] = ["panel-b"]
    else:
        chunks[1]["panel_ids"] = ["panel-c"]
    request = _request(module, chunks=tuple(chunks))
    provider = _provider(module, mock_provider_url)
    _assert_preflight_rejected(module, provider, request, monkeypatch)


@pytest.mark.parametrize(
    "kind", ("next-declaration-mismatch", "previous-declaration-mismatch")
)
def test_synthesis_preflight_rejects_declared_overlap_mismatch(
    mock_provider_url, monkeypatch, kind
):
    module = _vision_module()
    chunks = list(_chunks())
    if kind == "next-declaration-mismatch":
        chunks[0]["overlap_with_next"] = ["panel-a"]
    else:
        chunks[1]["overlap_with_previous"] = ["panel-c"]
    request = _request(module, chunks=tuple(chunks))
    provider = _provider(module, mock_provider_url)
    _assert_preflight_rejected(module, provider, request, monkeypatch)


def test_valid_synthesis_is_analyzer_validated_and_not_rewritten(
    mock_provider_url, monkeypatch
):
    module = _vision_module()
    request = _request(module)
    provider = _provider(module, mock_provider_url)
    output = _valid_output()
    before = copy.deepcopy(output)
    contract = importlib.import_module("app.services.analyzer_contract")
    real_validator = contract.validate_analyzer_output
    validation_calls = []

    def spy_validator(value, *, expected_panel_ids):
        validation_calls.append((copy.deepcopy(value), tuple(expected_panel_ids)))
        return real_validator(value, expected_panel_ids=expected_panel_ids)

    monkeypatch.setattr(contract, "validate_analyzer_output", spy_validator)
    _install_response(monkeypatch, module, output)

    result = provider.synthesize(request)

    assert result == output
    assert output == before
    assert validation_calls == [(output, PANEL_IDS)]


def _invalid_output(kind):
    output = _valid_output()
    if kind == "missing-top-level":
        del output["evidence_graph"]
    elif kind == "malformed-structure":
        output["coverage_manifest"] = "not-an-object"
    elif kind == "missing-story-spine":
        del output["narrative_outline"]["story_spine"]
    elif kind == "missing-claim-evidence":
        output["evidence_graph"]["claims"][0]["evidence_panel_ids"] = []
    elif kind == "generic-cta":
        output["script_passages"][0]["text"] += " Subscribe for more."
    elif kind == "non-object":
        return []
    return output


@pytest.mark.parametrize(
    "kind",
    (
        "missing-top-level",
        "malformed-structure",
        "missing-story-spine",
        "missing-claim-evidence",
        "generic-cta",
        "non-object",
    ),
)
def test_invalid_synthesis_response_fails_closed_without_template_fill(
    mock_provider_url, monkeypatch, kind
):
    module = _vision_module()
    request = _request(module)
    _, _, committed_text = _instruction_contract()
    provider = _provider(module, mock_provider_url)
    payload = _invalid_output(kind)
    before = copy.deepcopy(payload)
    _install_response(monkeypatch, module, payload)

    error_type = getattr(module, "VisionResponseInvalid", None)
    assert isinstance(error_type, type)
    with pytest.raises(error_type) as caught:
        provider.synthesize(request)

    assert caught.value.code == "vision_response_invalid"
    assert committed_text not in str(caught.value)
    assert "sk-synthesis-test-key" not in str(caught.value)
    assert payload == before


def test_synthesis_network_failure_uses_safe_error_boundary(
    mock_provider_url, monkeypatch
):
    module = _vision_module()
    request = _request(module)
    _, _, committed_text = _instruction_contract()
    provider = _provider(module, mock_provider_url)
    secret = "sk-synthesis-test-key"

    def fail_network(*args, **kwargs):
        raise httpx.ConnectError(f"provider rejected {secret}: {committed_text}")

    monkeypatch.setattr(module.httpx, "post", fail_network)
    error_type = getattr(module, "VisionProviderRequestFailed", None)
    assert isinstance(error_type, type)
    with pytest.raises(error_type) as caught:
        provider.synthesize(request)
    assert caught.value.code == "vision_provider_request_failed"
    assert secret not in str(caught.value)
    assert committed_text not in str(caught.value)


def test_synthesis_missing_configuration_blocks_before_network(monkeypatch):
    module = _vision_module()
    _, _, committed_text = _instruction_contract()
    provider = module.OpenAICompatibleVisionProvider(
        base_url="",
        model="",
        api_key="",
    )
    synthesize = getattr(provider, "synthesize", None)
    assert callable(synthesize), "VisionObservationProvider.synthesize is missing"

    def network_is_forbidden(*args, **kwargs):
        raise AssertionError("missing configuration must block before network")

    monkeypatch.setattr(module.httpx, "post", network_is_forbidden)
    request = _request(module)
    error_type = getattr(module, "VisionCapabilityError", None)
    assert isinstance(error_type, type)
    with pytest.raises(error_type) as caught:
        provider.synthesize(request)
    assert caught.value.code == "vision_capability_missing"
    assert committed_text not in str(caught.value)


def test_synthesis_capability_remains_network_free(mock_provider_url, monkeypatch):
    module = _vision_module()
    provider = _provider(module, mock_provider_url)

    def network_is_forbidden(*args, **kwargs):
        raise AssertionError("capability checks must not make a network request")

    monkeypatch.setattr(module.httpx, "post", network_is_forbidden)
    monkeypatch.setattr(module.httpx, "get", network_is_forbidden)
    report = provider.capability()
    assert report.available is True
    assert report.image_input is True
    assert report.structured_json is True
