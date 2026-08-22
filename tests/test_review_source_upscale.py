from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


def _upscale_module():
    return importlib.import_module("app.services.review_source_upscale")


def test_review_policy_resolves_original_source_by_checksum_not_segment_filename(tmp_path):
    module = _upscale_module()
    source = tmp_path / "chapter-010.webp"
    image = Image.new("RGB", (900, 2717), (12, 34, 56))
    image.save(source, format="WEBP")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    resolved = module.resolve_original_source_path(
        tmp_path,
        source_checksum=checksum,
        source_dimensions=(900, 2717),
    )

    assert resolved == source


def test_review_policy_prepares_900px_panel_and_transforms_bounds():
    module = _upscale_module()
    image = Image.new("RGB", (900, 1600), (30, 60, 90))
    policy = module.resolve_review_source_upscale_policy(
        "review_silent_source_upscale_v1"
    )

    prepared, manifest = module.prepare_review_panel(
        image,
        policy=policy,
        source_asset_id="asset-a",
        panel_region_id="region-a",
        source_asset_checksum="a" * 64,
    )

    assert prepared.size == (1080, 1920)
    assert manifest["policy_id"] == "review_silent_source_upscale_v1"
    assert manifest["original_dimensions"] == [900, 1600]
    assert manifest["prepared_dimensions"] == [1080, 1920]
    assert manifest["scale_factor"] == 1.2
    assert policy.max_scale == 1.5
    assert manifest["resolution_state"] == "UPSCALED"
    assert manifest["resample_filter"] == "LANCZOS"
    assert manifest["prepared_content_sha256"] == module.canonical_rgb_hash(prepared)
    assert module.transform_panel_bounds((10, 20, 890, 1580), manifest) == (
        12,
        24,
        1068,
        1896,
    )


def test_review_panel_crop_fallback_requires_exact_asset_bytes_and_localizes_bounds():
    module = _upscale_module()
    image = Image.new("RGB", (900, 1600), (30, 60, 90))
    payload = __import__("io").BytesIO()
    image.save(payload, format="PNG")
    raw = payload.getvalue()

    resolved, bounds = module.resolve_persisted_panel_crop(
        raw,
        asset_checksum=hashlib.sha256(raw).hexdigest(),
        panel_bounds=(0, 3200, 900, 4800),
    )

    assert resolved.size == (900, 1600)
    assert bounds == (0, 0, 900, 1600)


def test_review_panel_crop_fallback_rejects_tampered_bytes_or_geometry():
    module = _upscale_module()
    image = Image.new("RGB", (900, 1600), (30, 60, 90))
    payload = __import__("io").BytesIO()
    image.save(payload, format="PNG")
    raw = payload.getvalue()
    checksum = hashlib.sha256(raw).hexdigest()

    with pytest.raises(module.ReviewSourceUpscaleError, match="review.panel_crop_fallback_checksum_invalid"):
        module.resolve_persisted_panel_crop(
            raw + b"tampered",
            asset_checksum=checksum,
            panel_bounds=(0, 3200, 900, 4800),
        )
    with pytest.raises(module.ReviewSourceUpscaleError, match="review.panel_crop_fallback_geometry_invalid"):
        module.resolve_persisted_panel_crop(
            raw,
            asset_checksum=checksum,
            panel_bounds=(0, 3200, 899, 4800),
        )


def test_default_review_policy_uses_approved_mass_production_cap():
    module = _upscale_module()

    policy = module.resolve_review_source_upscale_policy(
        "review_silent_source_upscale_v1"
    )

    assert policy is not None
    assert policy.max_scale == 1.50
    assert policy.version == "1.3.0"


def test_review_policy_requires_explicit_silent_non_publish_boundary():
    module = _upscale_module()
    with pytest.raises(module.ReviewSourceUpscaleError, match="review.upscale_requires_silent_review"):
        module.validate_review_upscale_request(
            "review_silent_source_upscale_v1",
            silent_reference_review=False,
            publish_allowed=False,
        )
    with pytest.raises(module.ReviewSourceUpscaleError, match="review.upscale_publish_forbidden"):
        module.validate_review_upscale_request(
            "review_silent_source_upscale_v1",
            silent_reference_review=True,
            publish_allowed=True,
        )
    assert module.validate_review_upscale_request(
        "review_silent_source_upscale_v1",
        silent_reference_review=True,
        publish_allowed=False,
    ).policy_id == "review_silent_source_upscale_v1"


def test_review_policy_allows_over_cap_only_as_low_source_resolution_warning():
    module = _upscale_module()
    policy = module.resolve_review_source_upscale_policy(
        "review_silent_source_upscale_v1"
    )
    prepared, manifest = module.prepare_review_panel(
        Image.new("RGB", (320, 800)),
        policy=policy,
        source_asset_id="asset-a",
        panel_region_id="region-a",
        source_asset_checksum="a" * 64,
    )
    assert prepared.size == (1080, 2700)
    assert manifest["scale_factor"] == 3.375
    assert manifest["resolution_state"] == "LOW_SOURCE_RESOLUTION"
    assert manifest["non_native_warning"] == "review.low_source_resolution"
    module.validate_review_manifest(manifest, prepared)


def test_review_policy_scales_short_panel_for_the_native_resolution_floor():
    module = _upscale_module()
    policy = module.resolve_review_source_upscale_policy(
        "review_silent_source_upscale_v1"
    )
    prepared, manifest = module.prepare_review_panel(
        Image.new("RGB", (900, 500)),
        policy=policy,
        source_asset_id="asset-a",
        panel_region_id="region-a",
        source_asset_checksum="a" * 64,
    )

    assert prepared.height >= round(policy.target_height / 1.15)
    assert manifest["scale_factor"] > policy.max_scale
    assert manifest["resolution_state"] == "LOW_SOURCE_RESOLUTION"
    module.validate_review_manifest(manifest, prepared)


def test_review_only_low_resolution_warning_does_not_bypass_visual_hard_gates():
    from app.services import framing_analysis

    image = Image.new("RGB", (1000, 1000), (36, 48, 72))
    evidence = framing_analysis.PanelVisualEvidence(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id="panel-review-low-resolution",
        source_asset_id="asset-review-low-resolution",
        source_order=1,
        balloon_regions=(),
        protected_regions=(),
        balloon_mask_status="known_empty",
        mask_confidence=0.99,
        evidence_source="vision_geometry_v1",
        mask_reason="affirmative visual geometry result",
        evidence_hash="",
    )
    mask = framing_analysis.build_color_agnostic_border_mask(image, evidence)

    normal_ok, normal_telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 1000, 1000),
        evidence,
        mask,
        image.size,
        (1080, 1920),
    )
    review_ok, review_telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 1000, 1000),
        evidence,
        mask,
        image.size,
        (1080, 1920),
        allow_source_resolution_warning=True,
    )

    assert normal_ok is False
    assert normal_telemetry.rejection_code == "visual.source_resolution_insufficient"
    assert review_ok is True
    assert review_telemetry.fallback_reason == "review.low_source_resolution"
    assert review_telemetry.balloon_mask_intersection_ratio == 0.0


def test_review_low_resolution_warning_never_bypasses_balloon_or_protected_gates():
    from app.services import framing_analysis, visual_scoring

    image = Image.new("RGB", (1000, 1000), (36, 48, 72))
    balloon = visual_scoring.BalloonRegionEvidence(
        region_id="balloon-1",
        kind="speech_balloon",
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        normalized_polygon=(),
        confidence=0.99,
        evidence_source="vision_geometry_v1",
        mask_status="known_nonempty",
    )
    balloon_evidence = visual_scoring.PanelVisualEvidence(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id="panel-balloon",
        source_asset_id="asset-balloon",
        source_order=1,
        balloon_regions=(balloon,),
        protected_regions=(),
        balloon_mask_status="known_nonempty",
        mask_confidence=0.99,
        evidence_source="vision_geometry_v1",
        mask_reason="affirmative visual geometry result",
        evidence_hash="",
    )
    balloon_mask = framing_analysis.build_color_agnostic_border_mask(
        image, balloon_evidence
    )
    balloon_ok, balloon_telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 1000, 1000),
        balloon_evidence,
        balloon_mask,
        image.size,
        (1080, 1920),
        allow_source_resolution_warning=True,
    )

    protected = visual_scoring.ProtectedRegionEvidence(
        region_id="subject-1",
        kind="subject",
        normalized_bbox=(0.5, 0.0, 1.0, 1.0),
        normalized_polygon=(),
        confidence=0.99,
        evidence_source="vision_geometry_v1",
        required=True,
        minimum_coverage=0.98,
    )
    protected_evidence = visual_scoring.PanelVisualEvidence(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id="panel-protected",
        source_asset_id="asset-protected",
        source_order=1,
        balloon_regions=(),
        protected_regions=(protected,),
        balloon_mask_status="known_empty",
        mask_confidence=0.99,
        evidence_source="vision_geometry_v1",
        mask_reason="affirmative visual geometry result",
        evidence_hash="",
    )
    protected_mask = framing_analysis.build_color_agnostic_border_mask(
        image, protected_evidence
    )
    protected_ok, protected_telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 939, 1000),
        protected_evidence,
        protected_mask,
        image.size,
        (1080, 1920),
        allow_source_resolution_warning=True,
    )

    assert balloon_ok is False
    assert balloon_telemetry.rejection_code == "visual.balloon_mask_overlap"
    assert protected_ok is False
    assert protected_telemetry.rejection_code == "visual.protected_subject_coverage"


def test_review_planner_prefers_native_candidates_before_low_resolution_fallback():
    from app.services import editorial_visual_planner

    low = SimpleNamespace(
        panel_id="low-panel",
        panel_region_id="low-region",
        source_order=1,
        source_upscale_manifest={
            "policy_id": "review_silent_source_upscale_v1",
            "resolution_state": "LOW_SOURCE_RESOLUTION",
            "non_native_warning": "review.low_source_resolution",
        },
    )
    native = SimpleNamespace(
        panel_id="native-panel",
        panel_region_id="native-region",
        source_order=2,
        source_upscale_manifest={
            "policy_id": "review_silent_source_upscale_v1",
            "resolution_state": "UPSCALED",
            "non_native_warning": "review.source_upscale_non_native",
        },
    )

    ordered = editorial_visual_planner._prioritize_resolution_candidates(
        (low, native)
    )

    assert [candidate.panel_id for candidate in ordered] == [
        "native-panel",
        "low-panel",
    ]


def test_review_policy_rejects_unknown_resolution_override_without_fallback():
    module = _upscale_module()
    policy = module.ReviewSourceUpscalePolicy(
        max_scale=1.5,
        allow_low_source_resolution_warning=False,
    )
    with pytest.raises(module.ReviewSourceUpscaleError, match="review.upscale_limit_exceeded"):
        module.prepare_review_panel(
            Image.new("RGB", (600, 1000)),
            policy=policy,
            source_asset_id="asset-a",
            panel_region_id="region-a",
            source_asset_checksum="a" * 64,
        )


def test_review_policy_rejects_tampered_manifest_hash_and_content():
    module = _upscale_module()
    image = Image.new("RGB", (900, 1600), (30, 60, 90))
    policy = module.resolve_review_source_upscale_policy(
        "review_silent_source_upscale_v1"
    )
    prepared, manifest = module.prepare_review_panel(
        image,
        policy=policy,
        source_asset_id="asset-a",
        panel_region_id="region-a",
        source_asset_checksum="a" * 64,
    )
    module.validate_review_manifest(manifest, prepared)

    tampered_hash = dict(manifest, prepared_content_sha256="0" * 64)
    with pytest.raises(module.ReviewSourceUpscaleError, match="review.upscale_manifest_invalid"):
        module.validate_review_manifest(tampered_hash, prepared)

    tampered_manifest_hash = dict(manifest, manifest_sha256="0" * 64)
    with pytest.raises(module.ReviewSourceUpscaleError, match="review.upscale_manifest_invalid"):
        module.validate_review_manifest(tampered_manifest_hash, prepared)


def test_unknown_policy_does_not_silently_fall_back():
    module = _upscale_module()
    with pytest.raises(module.ReviewSourceUpscaleError, match="review.upscale_policy_unknown"):
        module.resolve_review_source_upscale_policy("review_silent_source_upscale_v9")


def test_pipeline_does_not_accept_review_upscale_on_default_or_final_path():
    from app.services import pipeline

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        pipeline,
        "get_project",
        lambda *_args: SimpleNamespace(template="reference_matched_shorts_v2"),
    )
    try:
        with pytest.raises(pipeline.PipelineError, match="review.upscale_requires_silent_review"):
            pipeline.build_render_request(
                None,
                SimpleNamespace(project_id="project"),
                review_source_upscale_policy="review_silent_source_upscale_v1",
            )
    finally:
        monkeypatch.undo()


def test_pipeline_rejects_upscale_without_reference_profile(monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(
        pipeline,
        "get_project",
        lambda *_args: SimpleNamespace(template="legacy"),
    )
    monkeypatch.setattr(
        pipeline.reference_profile,
        "resolve_reference_profile",
        lambda *_args: None,
    )
    with pytest.raises(pipeline.PipelineError, match="review.upscale_requires_reference_profile"):
        pipeline.build_render_request(
            None,
            SimpleNamespace(project_id="project"),
            silent_reference_review=True,
            review_source_upscale_policy="review_silent_source_upscale_v1",
        )


def test_pipeline_review_upscale_requires_reference_silent_request(monkeypatch):
    from app.services import pipeline

    project = SimpleNamespace(template="reference_matched_shorts_v2")
    job = SimpleNamespace(project_id="project")
    monkeypatch.setattr(pipeline, "get_project", lambda *_args: project)
    monkeypatch.setattr(
        pipeline,
        "_build_silent_reference_request",
        lambda *args, **kwargs: (args, kwargs),
    )
    result = pipeline.build_render_request(
        None,
        job,
        silent_reference_review=True,
        review_source_upscale_policy="review_silent_source_upscale_v1",
        output_override=Path("review.mp4"),
    )

    assert result[1]["review_source_upscale_policy"].policy_id == "review_silent_source_upscale_v1"
    assert result[1]["output_override"] == Path("review.mp4")
