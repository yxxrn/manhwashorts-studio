"""Body-level RED tests for payload-free prepared-panel warm resumes."""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest


def _modules():
    try:
        return (
            importlib.import_module("app.services.prepared_panel_manifest"),
            importlib.import_module("app.services.cloud_multimodal"),
        )
    except Exception as exc:
        pytest.fail(f"prepared-panel manifest imports failed in test body: {exc}")


def _panels(cloud):
    panels = []
    for index in range(2):
        panels.append(
            cloud.CloudPanelInput(
                panel_id=f"panel-{index}",
                source_asset_id=f"asset-{index}",
                source_order=index,
                mime_type="image/png",
                payload=f"encoded-panel-{index}".encode(),
                source_checksum=(f"{index + 1:064x}"),
                panel_bounds=(0, 0, 100, 120),
                source_dimensions=(100, 120),
                strip_region_id=f"region-{index}",
                coverage_map_version="vision-coverage-v2",
                coverage_map_hash="a" * 64,
                segmentation_version="vision-coverage-v2",
            )
        )
    return tuple(panels)


def _identity_hashes(cloud, panels):
    return tuple(
        cloud._hash(identity)
        for identity in cloud._visual_panel_identities(panels)
    )


def _source_assets():
    return tuple(
        {
            "source_asset_id": f"asset-{index}",
            "source_checksum": f"{index + 1:064x}",
            "original_dimensions": [100, 120],
            "strip_order": index,
            "region_order": 0,
            "source_family": "chapter-a",
        }
        for index in range(2)
    )


def test_prepared_manifest_round_trips_identity_without_image_bytes():
    manifest_module, cloud = _modules()
    panels = _panels(cloud)
    identity_hashes = _identity_hashes(cloud, panels)
    manifest = manifest_module.build_manifest(
        panels,
        {"status": "RECONCILED", "analysis_hash": "segmentation-hash"},
        panel_identity_hashes=identity_hashes,
        source_assets=_source_assets(),
    )

    validated = manifest_module.validate_manifest(manifest)
    restored = manifest_module.restore_cloud_panels(validated, cloud.CloudPanelInput)

    assert validated.manifest_hash == manifest["manifest_hash"]
    assert tuple(panel.panel_id for panel in restored) == ("panel-0", "panel-1")
    assert all(panel.metadata_only for panel in restored)
    assert all(panel.payload.startswith(manifest_module.PAYLOAD_MARKER_PREFIX) for panel in restored)
    assert tuple(
        cloud._hash(identity)
        for identity in cloud._visual_panel_identities(restored)
    ) == identity_hashes


def test_prepared_manifest_rejects_source_or_panel_identity_tampering():
    manifest_module, cloud = _modules()
    panels = _panels(cloud)
    manifest = manifest_module.build_manifest(
        panels,
        {"status": "RECONCILED", "analysis_hash": "segmentation-hash"},
        panel_identity_hashes=_identity_hashes(cloud, panels),
        source_assets=_source_assets(),
    )

    tampered = json.loads(json.dumps(manifest))
    tampered["panel_descriptors"][0]["source_checksum"] = "f" * 64
    with pytest.raises(manifest_module.PreparedPanelManifestError):
        manifest_module.validate_manifest(tampered)

    tampered_source = json.loads(json.dumps(manifest))
    tampered_source["source_assets"][0]["source_checksum"] = "e" * 64
    with pytest.raises(manifest_module.PreparedPanelManifestError):
        manifest_module.validate_manifest(tampered_source)


def test_metadata_only_panel_cannot_reach_provider_observe():
    manifest_module, cloud = _modules()
    panels = _panels(cloud)
    manifest = manifest_module.build_manifest(
        panels,
        {"status": "RECONCILED", "analysis_hash": "segmentation-hash"},
        panel_identity_hashes=_identity_hashes(cloud, panels),
        source_assets=_source_assets(),
    )
    restored = manifest_module.restore_cloud_panels(
        manifest_module.validate_manifest(manifest),
        cloud.CloudPanelInput,
    )

    class Provider:
        def __init__(self):
            self.model_id = "manifest-test"
            self.calls = 0

        def observe(self, request):
            self.calls += 1
            raise AssertionError("metadata-only panel reached provider")

    provider = Provider()
    runner = cloud.CloudStageRunner(
        provider=provider,
        model_identity=cloud.CloudModelIdentity(
            provider="openai_compatible",
            model="manifest-test",
            model_version="v1",
            endpoint="http://manifest.invalid/v1",
            prompt_versions={
                "visual": "balloon-free-visual-evidence-v1",
                "story_map": "cloud-causal-map-v2",
                "narration": "vision-first-story-analyzer-v3",
            },
        ),
        cache=None,
    )

    with pytest.raises(cloud.CloudStageError) as error:
        runner.run_visual_evidence(restored)
    assert error.value.code == "cloud.prepared_manifest_requires_materialization"
    assert provider.calls == 0


def test_cached_manifest_restores_precomputed_identity_without_image_bytes():
    manifest_module, cloud = _modules()
    panels = _panels(cloud)
    descriptors = []
    for index, panel in enumerate(panels):
        descriptor = dict(panel.descriptor())
        descriptor.pop("identity_payload_checksum", None)
        descriptor["identity_descriptor_hash"] = f"{index + 11:064x}"
        descriptors.append(descriptor)
    manifest = manifest_module.build_manifest_from_descriptors(
        descriptors,
        {"status": "RECONCILED", "analysis_hash": "segmentation-hash"},
        panel_identity_hashes=tuple(item["identity_descriptor_hash"] for item in descriptors),
        source_identity_hash="b" * 64,
        source_assets=_source_assets(),
    )

    restored = manifest_module.restore_cloud_panels(
        manifest_module.validate_manifest(manifest),
        cloud.CloudPanelInput,
    )

    assert all(panel.metadata_only for panel in restored)
    assert tuple(panel.identity_descriptor_hash for panel in restored) == tuple(
        item["identity_descriptor_hash"] for item in descriptors
    )
    assert all(panel.source_identity_hash == "b" * 64 for panel in restored)
    assert tuple(
        cloud._visual_panel_identity_hashes(restored)
    ) == tuple(item["identity_descriptor_hash"] for item in descriptors)
    assert cloud._visual_source_hash(restored) == "b" * 64


def test_cached_manifest_rejects_identity_count_before_descriptor_indexing():
    manifest_module, cloud = _modules()
    descriptors = [dict(panel.descriptor()) for panel in _panels(cloud)]

    with pytest.raises(
        manifest_module.PreparedPanelManifestError,
        match="panel identity count does not match descriptors",
    ):
        manifest_module.build_manifest_from_descriptors(
            descriptors,
            {"status": "RECONCILED", "analysis_hash": "segmentation-hash"},
            panel_identity_hashes=(),
            source_identity_hash="b" * 64,
            source_assets=_source_assets(),
        )


def test_cached_manifest_rejects_changed_current_source_fingerprint():
    manifest_module, cloud = _modules()
    panels = _panels(cloud)
    manifest = manifest_module.build_manifest(
        panels,
        {"status": "RECONCILED", "analysis_hash": "segmentation-hash"},
        panel_identity_hashes=_identity_hashes(cloud, panels),
        source_identity_hash="c" * 64,
        source_assets=_source_assets(),
    )
    changed_assets = list(_source_assets())
    changed_assets[0] = dict(changed_assets[0])
    changed_assets[0]["source_checksum"] = "f" * 64

    validated = manifest_module.validate_manifest(manifest)
    with pytest.raises(manifest_module.PreparedPanelManifestError):
        manifest_module.require_source_assets_match(validated, changed_assets)


def test_cached_visual_stage_builds_manifest_without_source_byte_reads(monkeypatch):
    manifest_module, cloud = _modules()
    pipeline = importlib.import_module("app.services.pipeline")
    assets = [
        SimpleNamespace(
            id=f"asset-{index}",
            original_checksum=f"{index + 1:064x}",
            checksum=f"{index + 1:064x}",
            original_width=100,
            original_height=120,
            width=100,
            height=120,
            strip_order=index,
            region_order=0,
            source_family="chapter-a",
        )
        for index in range(2)
    ]
    monkeypatch.setattr(pipeline, "project_assets", lambda _db, _project_id: assets)
    monkeypatch.setattr(pipeline, "image_assets", lambda value: value)
    panels = _panels(cloud)
    visual_rows = tuple(
        {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "source_checksum": panel.source_checksum,
            "observation": {},
            "visual_evidence": {},
            "evidence_hash": "e" * 64,
        }
        for panel in panels
    )
    visual = cloud.VisualStageResult(
        panels=visual_rows,
        source_hash="d" * 64,
        model_identity_hash="m" * 64,
        prompt_version="visual-v1",
        prompt_sha256="p" * 64,
        panel_identity_hashes=("a" * 64, "b" * 64),
    )
    segmentation_state = {
        "status": "RECONCILED",
        "version": "vision-coverage-v2",
        "reports": [
            {
                "source_asset_id": f"asset-{index}",
                "source_checksum": f"{index + 1:064x}",
                "source_dimensions": [100, 120],
                "spans": [[0, 120]],
                "analysis_hash": f"{index + 11:064x}",
            }
            for index in range(2)
        ],
    }

    manifest = cloud._build_cached_prepared_manifest(
        object(),
        "project-a",
        visual.as_dict(),
        segmentation_state,
    )
    restored, restored_segmentation = cloud._restore_project_prepared_manifest(
        object(),
        "project-a",
        manifest,
    )

    assert tuple(panel.panel_id for panel in restored) == tuple(panel.panel_id for panel in panels)
    assert all(panel.metadata_only for panel in restored)
    assert all(len(panel.payload) < 100 for panel in restored)
    assert restored_segmentation["status"] == "RECONCILED"
