"""RED contract tests for production strip segmentation reconciliation."""

from __future__ import annotations

import hashlib
import importlib
import io
from dataclasses import replace

import pytest
from PIL import Image, ImageDraw


def _module():
    try:
        return importlib.import_module("app.services.strip_segmentation")
    except Exception as exc:
        pytest.fail(f"strip segmentation boundary import failed in test body: {exc}")


def _strip_bytes(*, gutters: bool = True, width: int = 400, height: int = 2200) -> bytes:
    image = Image.new("RGB", (width, height), (72, 91, 113))
    draw = ImageDraw.Draw(image)
    for y in range(0, height, 36):
        draw.rectangle((0, y, width - 1, y + 15), fill=(118, 64, 96))
    if gutters:
        for top in (710, 1450):
            draw.rectangle((0, top, width - 1, top + 42), fill=(132, 78, 171))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _input(module, source_asset_id: str, payload: bytes, *, order: int = 0):
    from app.services import segmentation

    with Image.open(io.BytesIO(payload)) as image:
        width, height = image.size
    return segmentation.SourceAssetInput(
        source_asset_id=source_asset_id,
        original_checksum=hashlib.sha256(payload).hexdigest(),
        original_width=width,
        original_height=height,
        source_bounds=(0, 0, width, height),
        strip_order=order,
        region_order=0,
        payload=payload,
        decoded_width=width,
        decoded_height=height,
    )


def _crop_bytes(payload: bytes, top: int, bottom: int) -> bytes:
    with Image.open(io.BytesIO(payload)) as image:
        crop = image.crop((0, top, image.width, bottom))
    output = io.BytesIO()
    crop.save(output, format="PNG")
    return output.getvalue()


def test_high_confidence_mid_colour_strip_is_reconciled_without_provider():
    module = _module()

    result = module.reconcile_strip(
        _strip_bytes(),
        source_asset_id="strip-a",
        original_checksum="a" * 64,
    )

    assert result.status == "RECONCILED"
    assert len(result.spans) >= 2
    assert result.spans[0][0] == 0
    assert all(left[1] == right[0] for left, right in zip(result.spans, result.spans[1:], strict=False))
    assert result.spans[-1][1] == 2200
    assert result.review_code == ""
    assert len(result.analysis_hash) == 64


def test_artwork_connected_strip_without_separator_becomes_needs_review():
    module = _module()

    result = module.reconcile_strip(
        _strip_bytes(gutters=False),
        source_asset_id="ambiguous",
        original_checksum="b" * 64,
    )

    assert result.status == "NEEDS_REVIEW"
    assert result.review_code == "segmentation.ambiguous_boundary"
    assert result.spans == ((0, 2200),)
    assert result.report["actionable_reason"]


def test_provider_boundary_outside_source_is_rejected_before_selection():
    module = _module()

    def assessor(request):
        return {
            "source_asset_id": request.source_asset_id,
            "source_checksum": request.source_checksum,
            "random_sampling": False,
            "boundaries": [{"y": request.height + 1, "accepted": True, "confidence": 0.99}],
        }

    with pytest.raises(module.StripSegmentationError, match="segmentation.provider_coordinate_invalid"):
        module.reconcile_strip(
            _strip_bytes(gutters=False),
            source_asset_id="provider-invalid",
            original_checksum="c" * 64,
            boundary_assessor=assessor,
        )


def test_provider_hash_and_lineage_are_rejected_as_untrusted():
    module = _module()

    def provider_hash(request):
        return {
            "source_asset_id": request.source_asset_id,
            "source_checksum": request.source_checksum,
            "random_sampling": False,
            "analysis_hash": "a" * 64,
            "boundaries": [],
        }

    with pytest.raises(module.StripSegmentationError, match="segmentation.provider_hash_forbidden"):
        module.reconcile_strip(
            _strip_bytes(gutters=False),
            source_asset_id="provider-hash",
            original_checksum="1" * 64,
            boundary_assessor=provider_hash,
        )

    def nested_provider_hash(request):
        return {
            "source_asset_id": request.source_asset_id,
            "source_checksum": request.source_checksum,
            "random_sampling": False,
            "boundaries": [
                {
                    "y": request.candidates[0].position,
                    "accepted": True,
                    "confidence": 0.9,
                    "reason": "separator",
                    "protected_regions": [{"mask_sha256": "b" * 64}],
                }
            ],
        }

    with pytest.raises(module.StripSegmentationError, match="segmentation.provider_hash_forbidden"):
        module.reconcile_strip(
            _strip_bytes(),
            source_asset_id="provider-nested-hash",
            original_checksum="1" * 64,
            boundary_assessor=nested_provider_hash,
        )

    def foreign_lineage(request):
        return {
            "source_asset_id": "foreign",
            "source_checksum": request.source_checksum,
            "random_sampling": False,
            "boundaries": [],
        }

    with pytest.raises(module.StripSegmentationError, match="segmentation.provider_lineage_invalid"):
        module.reconcile_strip(
            _strip_bytes(gutters=False),
            source_asset_id="provider-lineage",
            original_checksum="2" * 64,
            boundary_assessor=foreign_lineage,
        )


def test_provider_protected_region_rejects_a_boundary_crossing_face():
    module = _module()

    def assessor(request):
        candidate = request.candidates[0]
        return {
            "source_asset_id": request.source_asset_id,
            "source_checksum": request.source_checksum,
            "random_sampling": False,
            "boundaries": [
                {
                    "y": candidate.position,
                    "accepted": True,
                    "confidence": 0.99,
                    "reason": "boundary is visually clear",
                    "protected_regions": [
                        {
                            "region_id": "face-1",
                            "kind": "face",
                            "bounds": [0, candidate.position - 10, request.width, candidate.position + 10],
                            "confidence": 0.98,
                            "evidence_source": "vision_geometry_v1",
                        }
                    ],
                }
            ],
        }

    result = module.reconcile_strip(
        _strip_bytes(),
        source_asset_id="protected",
        original_checksum="d" * 64,
        boundary_assessor=assessor,
    )

    assert result.status == "NEEDS_REVIEW"
    assert result.review_code == "segmentation.protected_boundary"
    assert result.rejected_cuts


def test_provider_request_contains_overlapping_tiles_and_candidate_boundaries():
    module = _module()
    seen = {}

    def assessor(request):
        seen["request"] = request
        return {
            "source_asset_id": request.source_asset_id,
            "source_checksum": request.source_checksum,
            "random_sampling": False,
            "boundaries": [
                {
                    "y": candidate.position,
                    "accepted": True,
                    "confidence": 0.96,
                    "reason": "the supplied overlapping tiles show a separator",
                    "protected_regions": [],
                }
                for candidate in request.candidates
            ],
        }

    result = module.reconcile_strip(
        _strip_bytes(),
        source_asset_id="provider-tiles",
        original_checksum="4" * 64,
        boundary_assessor=assessor,
    )

    request = seen["request"]
    assert result.status == "RECONCILED"
    assert request.candidates
    assert len(request.tiles) >= 2
    assert request.tiles[0]["overlap_below"] > 0
    assert request.tiles[1]["overlap_above"] > 0
    assert all(tile["payload_b64"] for tile in request.tiles)


def test_reconciliation_is_deterministic_for_multiple_source_files():
    module = _module()
    first = _input(module, "file-a", _strip_bytes(width=180, height=400), order=0)
    second = _input(module, "file-b", _strip_bytes(width=180, height=400), order=1)

    result = module.reconcile_sources((second, first))

    assert result.status == "RECONCILED"
    assert [item.source_asset_id for item in result.ordered_inputs] == ["file-a", "file-b"]
    assert len(result.analysis_hash) == 64
    assert result.analysis_hash == module.reconcile_sources((first, second)).analysis_hash


def test_partial_source_family_is_reconstructed_before_boundary_review():
    module = _module()
    full = _strip_bytes(height=2200)
    full_checksum = hashlib.sha256(full).hexdigest()
    first_payload = _crop_bytes(full, 0, 1100)
    second_payload = _crop_bytes(full, 1100, 2200)
    first = replace(
        _input(module, "piece-a", first_payload, order=0),
        original_checksum=full_checksum,
        original_width=400,
        original_height=2200,
        source_bounds=(0, 0, 400, 1100),
        source_family="page-a",
    )
    second = replace(
        _input(module, "piece-b", second_payload, order=1),
        original_checksum=full_checksum,
        original_width=400,
        original_height=2200,
        source_bounds=(0, 1100, 400, 2200),
        source_family="page-a",
    )

    result = module.reconcile_sources((second, first))

    assert result.status == "RECONCILED"
    assert len(result.reports) == 1
    assert result.reports[0].source_asset_id == "source-family:page-a"
    assert result.reports[0].height == 2200


def test_partial_artwork_connected_source_family_needs_review_before_visual_stage():
    module = _module()
    full = _strip_bytes(gutters=False, height=2200)
    full_checksum = hashlib.sha256(full).hexdigest()
    pieces = []
    for index, (top, bottom) in enumerate(((0, 1100), (1100, 2200))):
        pieces.append(
            replace(
                _input(module, f"piece-{index}", _crop_bytes(full, top, bottom), order=index),
                original_checksum=full_checksum,
                original_width=400,
                original_height=2200,
                source_bounds=(0, top, 400, bottom),
                source_family="page-ambiguous",
            )
        )

    result = module.reconcile_sources(tuple(reversed(pieces)))

    assert result.status == "NEEDS_REVIEW"
    assert result.reports[0].review_code == "segmentation.ambiguous_boundary"
    assert result.reports[0].spans == ((0, 2200),)


def test_manual_override_is_explicitly_audited_and_not_provider_evidence():
    module = _module()
    result = module.reconcile_strip(
        _strip_bytes(gutters=False),
        source_asset_id="override",
        original_checksum="e" * 64,
    )

    overridden = module.apply_manual_override(
        result,
        cuts=(),
        actor_id="editor-1",
        reason="confirmed as one tall canonical scene",
    )

    assert overridden.status == "RECONCILED"
    assert overridden.override["actor_id"] == "editor-1"
    assert overridden.override["reason"]
    assert overridden.override["provenance"] == "manual_override"
    assert overridden.provider_assessment is None


def test_source_gap_and_overlap_are_blocked_before_any_visual_stage():
    module = _module()
    payload = _strip_bytes(width=120, height=120)
    first = _input(module, "piece-a", payload, order=0)
    second = replace(first, source_asset_id="piece-b", source_bounds=(0, 90, 120, 120), source_family="page-a")
    first = replace(first, source_bounds=(0, 0, 120, 80), source_family="page-a")

    with pytest.raises(module.StripSegmentationError, match="segmentation.coverage_incomplete"):
        module.reconcile_sources((first, second))

    overlapping = replace(second, source_bounds=(0, 60, 120, 120))
    with pytest.raises(module.StripSegmentationError, match="segmentation.coverage_overlap"):
        module.reconcile_sources((first, overlapping))

    duplicate = replace(second, source_asset_id="piece-a", source_bounds=(0, 80, 120, 120))
    with pytest.raises(module.StripSegmentationError, match="segmentation.lineage_duplicate"):
        module.reconcile_sources((first, duplicate))


def test_review_artifact_contains_sanitized_json_and_thumbnail(tmp_path):
    module = _module()
    payload = _strip_bytes(gutters=False)
    result = module.reconcile_strip(
        payload,
        source_asset_id="review/source-1",
        original_checksum="3" * 64,
    )

    report_path, thumbnail_path = module.write_review_artifact(result, payload, tmp_path)

    assert report_path.is_file()
    assert thumbnail_path.is_file()
    report = report_path.read_text(encoding="utf-8")
    assert "review/source-1" in report
    assert "payload_b64" not in report


def test_pixel_budget_fails_closed_before_long_strip_analysis():
    module = _module()
    image = Image.new("RGB", (4096, 4096), (20, 40, 60))
    output = io.BytesIO()
    image.save(output, format="PNG")

    with pytest.raises(module.StripSegmentationError, match="segmentation.pixel_budget_exceeded"):
        module.reconcile_strip(
            output.getvalue(),
            source_asset_id="too-large",
            original_checksum="f" * 64,
        )
