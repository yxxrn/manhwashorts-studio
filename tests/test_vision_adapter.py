import base64
import copy
import importlib
import json
from pathlib import Path

import pytest

REQUIRED_OBSERVATION_KEYS = {
    "panel_id",
    "visible_facts",
    "dialogue_or_ocr",
    "inferences",
    "uncertainties",
    "entities",
    "state_changes",
    "causal_links",
    "evidence_refs",
}


def _vision_module():
    try:
        return importlib.import_module("app.services.vision_adapter")
    except Exception as exc:
        pytest.fail(
            "vision adapter import boundary is unavailable in the test body: "
            f"{exc}"
        )


def _panels():
    return (
        {
            "panel_id": "panel-a",
            "source_asset_id": "asset-a",
            "source_order": 17,
            "mime_type": "image/png",
            "payload": b"rights-safe-panel-a",
        },
        {
            "panel_id": "panel-b",
            "source_asset_id": "asset-b",
            "source_order": 23,
            "mime_type": "image/jpeg",
            "payload": b"rights-safe-panel-b",
        },
        {
            "panel_id": "panel-c",
            "source_asset_id": "asset-c",
            "source_order": 41,
            "mime_type": "image/webp",
            "payload": b"rights-safe-panel-c",
        },
    )


def _request(module):
    return module.VisionObservationRequest(
        analysis_run_id="run-vision-001",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="a" * 64,
        chunk_index=4,
        panels=_panels(),
    )


def _visual_request(module):
    scoring = importlib.import_module("app.services.visual_scoring")
    loader = getattr(scoring, "load_visual_evidence_instruction", None)
    assert callable(loader), "visual_instruction_loader_missing"
    version, digest, text = loader()
    assert version == "balloon-free-visual-evidence-v2"
    assert len(digest) == 64
    request = module.VisionObservationRequest(
        analysis_run_id="run-vision-001",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="a" * 64,
        chunk_index=4,
        panels=_panels(),
        visual_instruction_version=version,
        visual_instruction_sha256=digest,
    )
    return request, version, digest, text


def test_versioned_visual_semantic_repair_prompt_requires_grounded_facts():
    module = _vision_module()
    scoring = importlib.import_module("app.services.visual_scoring")
    loader = getattr(scoring, "load_visual_evidence_repair_instruction", None)
    assert callable(loader), "visual_semantic_repair_prompt_missing"
    version, digest, text = loader()
    request = module.VisionObservationRequest(
        analysis_run_id="run-vision-repair-001",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="a" * 64,
        chunk_index=0,
        panels=_panels()[:1],
        visual_instruction_version=version,
        visual_instruction_sha256=digest,
    )
    body = module._build_payload(request, request.panels, "mock-large")
    rendered = _body_text(body)

    assert version == "balloon-free-visual-evidence-repair-v2"
    assert len(digest) == 64
    assert text.strip() in rendered
    assert "visible_facts" in rendered
    assert "at least one" in rendered.lower()


def test_request_validator_accepts_only_known_visual_repair_identity():
    module = _vision_module()
    scoring = importlib.import_module("app.services.visual_scoring")
    version, digest, _text = scoring.load_visual_evidence_repair_instruction()
    request = module.VisionObservationRequest(
        analysis_run_id="run-vision-repair-002",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="a" * 64,
        chunk_index=0,
        panels=_panels()[:1],
        visual_instruction_version=version,
        visual_instruction_sha256=digest,
    )

    normalized = module._validate_request(request)

    assert normalized[0]["panel_id"] == "panel-a"


def _body_parts(body):
    parts = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            parts.extend(content)
    return parts


def _body_text(body):
    text_parts = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            text_parts.extend(
                part["text"]
                for part in content
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            )
    return "\n".join(text_parts)


def test_ordered_multimodal_request_contains_each_panel_and_image_once(
    mock_provider_url,
):
    module = _vision_module()
    import mock_provider

    mock_provider.reset_vision_state()
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )

    observations = provider.observe(_request(module))
    requests = mock_provider.captured_vision_requests()
    assert len(requests) == 1
    body = requests[0]
    text = _body_text(body)
    panels = _panels()

    assert body["model"] == "mock-large"
    assert body["response_format"]["type"] in {"json_object", "json_schema"}
    assert "structured" in text.lower()
    assert "json" in text.lower()
    assert "RulesAnalyzer" not in json.dumps(body)
    assert "filename" not in text.lower()
    assert "run-vision-001" in text
    assert "vision-first-story-analyzer-v1" in text
    assert "a" * 64 in text
    assert "4" in text
    assert "structured JSON list" in text
    for key in REQUIRED_OBSERVATION_KEYS:
        assert key in text

    panel_positions = [text.index(panel["panel_id"]) for panel in panels]
    assert panel_positions == sorted(panel_positions)
    for panel in panels:
        assert panel["source_asset_id"] in text
        assert str(panel["source_order"]) in text

    image_urls = [
        part["image_url"]["url"]
        for part in _body_parts(body)
        if part.get("type") == "image_url"
    ]
    assert len(image_urls) == len(panels)
    assert len(set(image_urls)) == len(image_urls)
    decoded = [
        base64.b64decode(url.split(",", maxsplit=1)[1])
        for url in image_urls
    ]
    assert decoded == [panel["payload"] for panel in panels]
    assert [
        url.split(",", maxsplit=1)[0]
        for url in image_urls
    ] == [
        "data:image/png;base64",
        "data:image/jpeg;base64",
        "data:image/webp;base64",
    ]
    assert [observation["panel_id"] for observation in observations] == [
        panel["panel_id"] for panel in panels
    ]


def test_complete_mock_observations_have_all_required_keys(mock_provider_url):
    module = _vision_module()
    import mock_provider

    mock_provider.reset_vision_state()
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )

    observations = provider.observe(_request(module))

    assert isinstance(observations, list)
    assert len(observations) == 3
    for observation in observations:
        assert set(observation) >= REQUIRED_OBSERVATION_KEYS


def test_sse_chat_completion_is_assembled_for_observations(monkeypatch):
    module = _vision_module()
    import httpx
    import mock_provider

    content = json.dumps(mock_provider.default_vision_response())
    midpoint = len(content) // 2
    sse = "\n".join(
        (
            "data: "
            + json.dumps({"choices": [{"delta": {"content": content[:midpoint]}}]}),
            "data: "
            + json.dumps({"choices": [{"delta": {"content": content[midpoint:]}}]}),
            "data: [DONE]",
            "",
        )
    )
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=sse,
        request=httpx.Request("POST", "http://provider.test/v1/chat/completions"),
    )
    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: response)

    provider = module.OpenAICompatibleVisionProvider(
        base_url="http://provider.test/v1",
        model="mock-large",
        api_key="test-key",
    )

    observations = provider.observe(_request(module))

    assert [item["panel_id"] for item in observations] == [
        panel["panel_id"] for panel in _panels()
    ]


def test_invalid_image_http_error_is_classified_as_request_invalid(monkeypatch):
    module = _vision_module()
    import httpx

    response = httpx.Response(
        400,
        headers={"content-type": "application/json"},
        json={"code": "invalid_image", "error": "redacted"},
        request=httpx.Request("POST", "http://provider.test/v1/chat/completions"),
    )
    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: response)

    provider = module.OpenAICompatibleVisionProvider(
        base_url="http://provider.test/v1",
        model="mock-large",
        api_key="test-key",
    )

    with pytest.raises(module.VisionRequestInvalid):
        provider.observe(_request(module))


def test_observation_reuses_one_ephemeral_encoding_per_panel(monkeypatch):
    module = _vision_module()
    import mock_provider

    mock_provider.reset_vision_state()
    provider = module.OpenAICompatibleVisionProvider(
        base_url="http://provider.test/v1",
        model="mock-large",
        api_key="test-key",
    )
    original = module.base64.b64encode
    calls = []

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(module.base64, "b64encode", counted)
    response = mock_provider.default_vision_response()
    import httpx

    monkeypatch.setattr(
        module.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(response)}}]},
            request=httpx.Request("POST", "http://provider.test/v1/chat/completions"),
        ),
    )
    provider.observe(_request(module))
    provider.observe(_request(module))
    assert len(calls) == len(_panels())


def test_observation_accepts_a_whole_json_code_fence(monkeypatch):
    module = _vision_module()
    import httpx
    import mock_provider

    content = "```json\n" + json.dumps(mock_provider.default_vision_response()) + "\n```"
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "http://provider.test/v1/chat/completions"),
    )
    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: response)

    provider = module.OpenAICompatibleVisionProvider(
        base_url="http://provider.test/v1",
        model="mock-large",
        api_key="test-key",
    )

    observations = provider.observe(_request(module))

    assert [item["panel_id"] for item in observations] == [
        panel["panel_id"] for panel in _panels()
    ]


def test_sse_chat_completion_is_assembled_for_json_stage(monkeypatch):
    module = _vision_module()
    import httpx

    content = json.dumps({"stage": "ok"})
    sse = "\n".join(
        (
            "data: "
            + json.dumps({"choices": [{"delta": {"content": content}}]}),
            "data: [DONE]",
            "",
        )
    )
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=sse,
        request=httpx.Request("POST", "http://provider.test/v1/chat/completions"),
    )
    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: response)

    provider = module.OpenAICompatibleVisionProvider(
        base_url="http://provider.test/v1",
        model="mock-large",
        api_key="test-key",
    )

    result = provider.complete_json(
        stage="test",
        prompt_version="prompt-v1",
        prompt_sha256="a" * 64,
        payload={"panel_ids": ["panel-a"]},
    )

    assert result == {"stage": "ok"}


def test_complete_json_with_images_sends_image_parts_not_json_payload_text(monkeypatch):
    module = _vision_module()
    import httpx

    captured = {}
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": '{"stage":"ok"}'}}]},
        request=httpx.Request("POST", "http://provider.test/v1/chat/completions"),
    )

    def fake_post(*args, **kwargs):
        captured["body"] = kwargs["json"]
        return response

    monkeypatch.setattr(module.httpx, "post", fake_post)
    provider = module.OpenAICompatibleVisionProvider(
        base_url="http://provider.test/v1",
        model="mock-large",
        api_key="test-key",
    )
    result = provider.complete_json_with_images(
        stage="strip_segmentation",
        prompt_version="strip-boundary-assessment-v1",
        prompt_sha256="a" * 64,
        payload={"source_asset_id": "asset-a"},
        images=(
            {
                "mime_type": "image/jpeg",
                "payload": b"jpeg-bytes",
                "tile_index": 0,
            },
        ),
    )
    assert result == {"stage": "ok"}
    body = captured["body"]
    parts = _body_parts(body)
    assert any(part.get("type") == "image_url" for part in parts)
    assert "payload_b64" not in json.dumps(body)
    assert "jpeg-bytes" not in json.dumps(body)


def test_complete_json_accepts_a_whole_json_code_fence(monkeypatch):
    module = _vision_module()
    import httpx

    content = "```json\n" + json.dumps({"stage": "ok"}) + "\n```"
    sse = "\n".join(
        (
            "data: "
            + json.dumps(
                {"choices": [{"delta": {"content": content}}]}
            ),
            "data: [DONE]",
            "",
        )
    )
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=sse,
        request=httpx.Request("POST", "http://provider.test/v1/chat/completions"),
    )
    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: response)
    provider = module.OpenAICompatibleVisionProvider(
        base_url="http://provider.test/v1",
        model="mock-large",
        api_key="test-key",
    )

    result = provider.complete_json(
        stage="story_map",
        prompt_version="story-map-v1",
        prompt_sha256="b" * 64,
        prompt_text="Return JSON.",
        payload={"panel_ids": ["panel-a"]},
    )

    assert result == {"stage": "ok"}


def _assert_invalid_response(module, mock_provider_url, content):
    import mock_provider

    request = _request(module)
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )
    codes = []
    try:
        for _ in range(2):
            mock_provider.set_vision_response_content(content)
            with pytest.raises(module.VisionCapabilityError) as caught:
                provider.observe(request)
            code = caught.value.code
            assert isinstance(code, str) and code
            codes.append(code)
    finally:
        mock_provider.reset_vision_state()
    assert codes[0] == codes[1]


def test_invalid_observations_fail_closed_without_inference(mock_provider_url):
    module = _vision_module()
    import mock_provider

    complete = mock_provider.default_vision_response()

    missing_panel_id = copy.deepcopy(complete)
    missing_panel_id[0].pop("panel_id")
    unknown_panel_id = copy.deepcopy(complete)
    unknown_panel_id[0]["panel_id"] = "panel-unknown"
    duplicate_panel_id = copy.deepcopy(complete)
    duplicate_panel_id[1]["panel_id"] = "panel-a"
    missing_required_key = copy.deepcopy(complete)
    missing_required_key[0].pop("entities")
    wrong_list_type = copy.deepcopy(complete)
    wrong_list_type[0]["visible_facts"] = "not-a-list"
    empty_evidence_refs = copy.deepcopy(complete)
    empty_evidence_refs[0]["evidence_refs"] = []
    foreign_evidence_refs = copy.deepcopy(complete)
    foreign_evidence_refs[0]["evidence_refs"] = ["panel-unknown"]

    invalid_responses = (
        json.dumps(missing_panel_id),
        json.dumps(unknown_panel_id),
        json.dumps(duplicate_panel_id),
        json.dumps(missing_required_key),
        json.dumps(wrong_list_type),
        json.dumps(empty_evidence_refs),
        json.dumps(foreign_evidence_refs),
        "{malformed-json",
        json.dumps({"observations": complete}),
    )
    for content in invalid_responses:
        _assert_invalid_response(module, mock_provider_url, content)


def _remove_required_and_add_foreign(response):
    response[0].pop("entities")
    response[0]["foreign_key"] = []


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(
            _remove_required_and_add_foreign,
            id="missing-required-with-foreign-key",
        ),
    ),
)
def test_observation_missing_required_fields_still_fail_closed(mock_provider_url, mutate):
    module = _vision_module()
    import mock_provider

    response = copy.deepcopy(mock_provider.default_vision_response())
    mutate(response)
    _assert_invalid_response(module, mock_provider_url, json.dumps(response))


def test_visual_observation_projects_optional_provider_fields_to_trusted_contract(
    mock_provider_url,
):
    module = _vision_module()
    import mock_provider

    response = copy.deepcopy(mock_provider.default_visual_vision_response())
    response[0]["provider_note"] = {"untrusted": True}
    response[0]["visual_evidence"]["provider_geometry_note"] = "optional"
    response[0]["visual_evidence"]["protected_regions"][0]["provider_region_note"] = "optional"
    mock_provider.reset_vision_state()
    mock_provider.set_vision_response_content(json.dumps(response))
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )

    observations = provider.observe(_visual_request(module)[0])

    assert len(observations) == len(_panels())
    assert "provider_note" not in observations[0]
    assert "provider_geometry_note" not in observations[0]["visual_evidence"]
    assert "provider_region_note" not in observations[0]["visual_evidence"]["protected_regions"][0]


def test_response_order_is_deterministic_for_a_complete_request(mock_provider_url):
    module = _vision_module()
    import mock_provider

    mock_provider.reset_vision_state()
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )
    reversed_response = list(reversed(mock_provider.default_vision_response()))
    mock_provider.set_vision_response_content(json.dumps(reversed_response))

    first = provider.observe(_request(module))
    second = provider.observe(_request(module))

    assert first == second
    assert [item["panel_id"] for item in first] == [
        "panel-a",
        "panel-b",
        "panel-c",
    ]


def _assert_visual_invalid(module, mock_provider_url, content):
    import mock_provider

    request, _, _, _ = _visual_request(module)
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )
    try:
        mock_provider.set_vision_response_content(json.dumps(content))
        with pytest.raises(module.VisionCapabilityError) as caught:
            provider.observe(request)
        assert caught.value.code == "vision_response_invalid"
    finally:
        mock_provider.reset_vision_state()


def test_visual_request_uses_committed_prompt_and_ordered_provider_sidecars(
    mock_provider_url,
):
    module = _vision_module()
    import mock_provider

    request, version, digest, prompt = _visual_request(module)
    mock_provider.reset_vision_state()
    mock_provider.set_vision_response_content(
        json.dumps(mock_provider.default_visual_vision_response())
    )
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )
    observations = provider.observe(request)
    body = mock_provider.captured_vision_requests()[0]
    text = _body_text(body)

    assert [row["panel_id"] for row in observations] == [
        panel["panel_id"] for panel in _panels()
    ]
    assert version in text
    assert digest in text
    assert prompt.strip() in text
    assert "Return a JSON array" in text
    for key in REQUIRED_OBSERVATION_KEYS:
        assert key in text
    for key in (
        "normalized_bbox",
        "normalized_polygon",
        "region_id",
        "mask_status",
        "minimum_coverage",
    ):
        assert key in text
    assert "evidence_refs must include the panel_id" in text
    assert "evidence_hash" not in json.dumps(body)
    for panel, row in zip(_panels(), observations, strict=True):
        assert set(row) == REQUIRED_OBSERVATION_KEYS | {"visual_evidence"}
        sidecar = row["visual_evidence"]
        assert set(sidecar) == {
            "balloon_mask_status",
            "balloon_regions",
            "protected_regions",
            "mask_confidence",
            "evidence_source",
            "mask_reason",
            "panel_id",
            "source_asset_id",
            "source_order",
        }
        assert sidecar["panel_id"] == panel["panel_id"]
        assert sidecar["source_asset_id"] == panel["source_asset_id"]
        assert sidecar["source_order"] == panel["source_order"]
        assert "evidence_hash" not in sidecar


def test_visual_sidecar_accepts_optional_null_polygon_for_bbox_regions(
    mock_provider_url,
):
    module = _vision_module()
    import mock_provider

    request, _, _, _ = _visual_request(module)
    response = copy.deepcopy(mock_provider.default_visual_vision_response())
    for row in response:
        for region in row["visual_evidence"]["protected_regions"]:
            region["normalized_polygon"] = None
    mock_provider.reset_vision_state()
    mock_provider.set_vision_response_content(json.dumps(response))
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )

    observations = provider.observe(request)

    assert len(observations) == len(_panels())


def test_visual_sidecar_normalizes_provider_mask_required_alias(
    mock_provider_url,
):
    module = _vision_module()
    import mock_provider

    request, _, _, _ = _visual_request(module)
    response = copy.deepcopy(mock_provider.default_visual_vision_response())
    for row in response:
        for region in row["visual_evidence"]["balloon_regions"]:
            region["mask_status"] = "mask_required"
    mock_provider.reset_vision_state()
    mock_provider.set_vision_response_content(json.dumps(response))
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )

    observations = provider.observe(request)

    assert len(observations) == len(_panels())
    for observation in observations:
        sidecar = observation["visual_evidence"]
        regions = sidecar["balloon_regions"]
        if sidecar["balloon_mask_status"] == "known_nonempty":
            assert regions
            assert all(region["mask_status"] == "known_nonempty" for region in regions)


def test_visual_sidecar_normalizes_provider_balloon_semantic_aliases(
    mock_provider_url,
):
    module = _vision_module()
    import mock_provider

    request, _, _, _ = _visual_request(module)
    response = copy.deepcopy(mock_provider.default_visual_vision_response())
    aliases = ("tail", "speech")
    for row in response:
        regions = row["visual_evidence"]["balloon_regions"]
        for index, region in enumerate(regions):
            region["kind"] = aliases[index % len(aliases)]
            region["mask_status"] = "covered"
    mock_provider.reset_vision_state()
    mock_provider.set_vision_response_content(json.dumps(response))
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=mock_provider.GOOD_KEY,
    )

    observations = provider.observe(request)

    assert len(observations) == len(_panels())
    for observation in observations:
        sidecar = observation["visual_evidence"]
        if sidecar["balloon_mask_status"] == "known_nonempty":
            assert sidecar["balloon_regions"]
            assert all(
                region["kind"] == "speech_balloon"
                and region["mask_status"] == "known_nonempty"
                for region in sidecar["balloon_regions"]
            )


def test_visual_prompt_snapshot_is_normalized_and_local_hash_owned():
    scoring = importlib.import_module("app.services.visual_scoring")
    loader = getattr(scoring, "load_visual_evidence_instruction", None)
    assert callable(loader), "visual_instruction_loader_missing"
    version, digest, text = loader()
    snapshot = Path(__file__).parent / "fixtures" / "visual_evidence_prompt_snapshot.sha256"
    assert snapshot.exists()
    assert version == "balloon-free-visual-evidence-v2"
    assert snapshot.read_text(encoding="utf-8").strip() == digest
    assert text.endswith("\n")
    assert "evidence_hash" not in text
    assert "Never label OCR-only geometry as known_nonempty or known_empty" in text
    assert "every balloon region MUST use kind speech_balloon" in text
    assert "unknown is only for unavailable or insufficient visual geometry" in text
    assert "Geometry must be tight and visibly grounded" in text
    assert "protected_regions MUST describe the editorially important character geometry" in text
    assert "Do not mark only a hand, arm, foot, weapon tip" in text


@pytest.mark.parametrize(
    "variant",
    (
        "missing",
        "foreign",
        "malformed",
        "known_empty_unproven",
        "known_empty_ocr_only",
        "duplicate",
        "ocr_only",
        "provider_hash",
    ),
)
def test_visual_sidecars_fail_closed_without_provider_hash_trust(
    mock_provider_url, variant
):
    module = _vision_module()
    import mock_provider

    response = copy.deepcopy(mock_provider.default_visual_vision_response())
    sidecar = response[0]["visual_evidence"]
    if variant == "missing":
        response[0].pop("visual_evidence")
    elif variant == "foreign":
        sidecar["panel_id"] = "panel-foreign"
    elif variant == "malformed":
        sidecar["balloon_regions"][0]["normalized_bbox"] = [0.1, 0.2]
    elif variant == "known_empty_unproven":
        sidecar = response[1]["visual_evidence"]
        sidecar.update(mask_confidence=0.0, evidence_source="", mask_reason="")
    elif variant == "known_empty_ocr_only":
        sidecar = response[1]["visual_evidence"]
        sidecar["evidence_source"] = "ocr_text_only"
    elif variant == "duplicate":
        sidecar["balloon_regions"].append(copy.deepcopy(sidecar["balloon_regions"][0]))
    elif variant == "ocr_only":
        sidecar["evidence_source"] = "ocr_text_only"
        sidecar["balloon_regions"][0]["evidence_source"] = "ocr_text_only"
    else:
        sidecar["evidence_hash"] = "provider-supplied"
    _assert_visual_invalid(module, mock_provider_url, response)


def test_analysis_windows_are_complete_views_of_one_canonical_panel():
    module = _vision_module()
    panel = {
        "panel_id": "panel-tall",
        "source_asset_id": "asset-tall",
        "source_order": 1,
        "mime_type": "image/jpeg",
        "payload": b"overview",
        "analysis_window_version": module.ANALYSIS_WINDOW_CONTRACT_VERSION,
        "analysis_window_source_size": [900, 3000],
        "analysis_windows": (
            {"window_index": 0, "y0": 0, "y1": 1200, "overlap_above": 0, "overlap_below": 200, "mime_type": "image/jpeg", "payload": b"w0"},
            {"window_index": 1, "y0": 1000, "y1": 2200, "overlap_above": 200, "overlap_below": 200, "mime_type": "image/jpeg", "payload": b"w1"},
            {"window_index": 2, "y0": 2000, "y1": 3000, "overlap_above": 200, "overlap_below": 0, "mime_type": "image/jpeg", "payload": b"w2"},
        ),
    }
    request = module.VisionObservationRequest(
        analysis_run_id="run-tall",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="a" * 64,
        chunk_index=0,
        panels=(panel,),
    )
    normalized = module._validate_request(request)
    assert len(normalized) == 1
    assert len(normalized[0]["analysis_windows"]) == 3
    body = module._build_payload(request, normalized, "grok-4.3")
    image_parts = [part for part in _body_parts(body) if part.get("type") == "image_url"]
    assert len(image_parts) == 4  # one overview + three detail windows
    rendered = _body_text(body)
    assert "SAME panel" in rendered
    assert "full canonical panel" in rendered
    assert "panel-tall" in rendered


def test_analysis_windows_reject_source_coverage_gap():
    module = _vision_module()
    panel = {
        "panel_id": "panel-gap",
        "source_asset_id": "asset-gap",
        "source_order": 1,
        "mime_type": "image/jpeg",
        "payload": b"overview",
        "analysis_window_version": module.ANALYSIS_WINDOW_CONTRACT_VERSION,
        "analysis_window_source_size": [900, 3000],
        "analysis_windows": (
            {"window_index": 0, "y0": 0, "y1": 1200, "overlap_above": 0, "overlap_below": 0, "mime_type": "image/jpeg", "payload": b"w0"},
            {"window_index": 1, "y0": 1300, "y1": 3000, "overlap_above": 0, "overlap_below": 0, "mime_type": "image/jpeg", "payload": b"w1"},
        ),
    }
    request = module.VisionObservationRequest(
        analysis_run_id="run-gap",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="a" * 64,
        chunk_index=0,
        panels=(panel,),
    )
    with pytest.raises(module.VisionRequestInvalid):
        module._validate_request(request)


def test_provider_bbox_xywh_alias_is_normalized_only_when_xyxy_is_impossible():
    module = _vision_module()
    assert module._normalize_provider_bbox(
        [0.18, 0.68, 0.64, 0.28]
    ) == pytest.approx([0.18, 0.68, 0.82, 0.96])
    # Already-valid xyxy remains untouched even though it could also resemble xywh.
    original = [0.08, 0.08, 0.78, 0.58]
    assert module._normalize_provider_bbox(original) == original
    # An alias that would leave the unit frame is not repaired and must fail later.
    invalid = [0.8, 0.8, 0.4, 0.4]
    assert module._normalize_provider_bbox(invalid) == invalid
