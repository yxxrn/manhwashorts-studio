import base64
import copy
import importlib
import json

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


def _add_unexpected_key(response):
    response[0]["unexpected_key"] = []


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(
            _remove_required_and_add_foreign,
            id="missing-required-with-foreign-key",
        ),
        pytest.param(_add_unexpected_key, id="otherwise-valid-with-unexpected-key"),
    ),
)
def test_observation_keys_must_match_exact_contract(mock_provider_url, mutate):
    module = _vision_module()
    import mock_provider

    response = copy.deepcopy(mock_provider.default_vision_response())
    mutate(response)
    _assert_invalid_response(module, mock_provider_url, json.dumps(response))


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
