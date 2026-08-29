import dataclasses
import importlib
import json
import logging

import pytest


def _vision_module():
    try:
        return importlib.import_module("app.services.vision_adapter")
    except Exception as exc:
        pytest.fail(
            "vision capability import boundary is unavailable in the test body: "
            f"{exc}"
        )


def _request(module):
    return module.VisionObservationRequest(
        analysis_run_id="run-capability-001",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="b" * 64,
        chunk_index=0,
        panels=(
            {
                "panel_id": "panel-a",
                "source_asset_id": "asset-a",
                "source_order": 1,
                "mime_type": "image/png",
                "payload": b"capability-panel-a",
            },
        ),
    )


def test_capability_report_schema_and_explicit_text_only_report():
    module = _vision_module()
    report_fields = tuple(
        field.name
        for field in dataclasses.fields(module.VisionCapabilityReport)
    )
    assert report_fields == (
        "provider_type",
        "provider_name",
        "model",
        "image_input",
        "structured_json",
        "available",
        "blocking_reason",
    )

    report = module.VisionCapabilityReport(
        provider_type="rules",
        provider_name="rules",
        model=None,
        image_input=False,
        structured_json=False,
        available=False,
        blocking_reason="vision_capability_missing",
    )
    assert report.image_input is False
    assert report.structured_json is False
    assert report.available is False
    assert report.blocking_reason == "vision_capability_missing"


def test_missing_configuration_blocks_before_any_network_call(
    mock_provider_url, monkeypatch
):
    module = _vision_module()
    import httpx
    import mock_provider

    def explode(*args, **kwargs):
        raise AssertionError("network call must not occur")

    monkeypatch.setattr(httpx.Client, "request", explode)
    monkeypatch.setattr(httpx.Client, "post", explode)
    monkeypatch.setattr(httpx, "post", explode)

    configurations = (
        {
            "base_url": "",
            "model": "mock-large",
            "api_key": mock_provider.GOOD_KEY,
        },
        {
            "base_url": mock_provider_url,
            "model": "",
            "api_key": mock_provider.GOOD_KEY,
        },
        {
            "base_url": mock_provider_url,
            "model": "mock-large",
            "api_key": "",
        },
    )
    for configuration in configurations:
        provider = module.OpenAICompatibleVisionProvider(**configuration)
        report = provider.capability()
        assert report.image_input is False or report.available is False
        assert report.available is False
        assert report.blocking_reason
        with pytest.raises(module.VisionCapabilityError) as caught:
            provider.observe(_request(module))
        assert caught.value.code == "vision_capability_missing"


def test_capability_and_errors_never_expose_api_key(mock_provider_url, caplog):
    module = _vision_module()
    import mock_provider

    secret = "sk-vision-sentinel-never-log"
    mock_provider.reset_vision_state()
    provider = module.OpenAICompatibleVisionProvider(
        base_url=mock_provider_url,
        model="mock-large",
        api_key=secret,
    )

    report = provider.capability()
    assert secret not in repr(report)
    with caplog.at_level(logging.DEBUG), pytest.raises(
        module.VisionCapabilityError
    ) as caught:
        provider.observe(_request(module))

    assert secret not in str(caught.value)
    assert secret not in caplog.text
    assert secret not in json.dumps(mock_provider.captured_vision_requests())
