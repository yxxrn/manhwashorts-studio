from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest
from PIL import Image


def _module():
    return importlib.import_module("app.services.manual_narrative_review")


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def valid_manifest(tmp_path: Path) -> tuple[Path, Path]:
    review_root = tmp_path / "review"
    ordered_root = review_root / "ordered"
    ordered_root.mkdir(parents=True)
    assets: list[dict[str, object]] = []
    for source_order in range(24):
        width = 64 + source_order
        height = 96 + source_order
        filename = f"{source_order:03d}-panel-{source_order:02d}.png"
        image_path = ordered_root / filename
        Image.new("RGB", (width, height), (source_order, 30, 90)).save(image_path)
        assets.append(
            {
                "display_index": source_order,
                "asset_id": f"asset-{source_order:02d}",
                "panel_id": f"panel-{source_order:02d}",
                "source_order": source_order,
                "filename": filename,
                "storage_key": f"images/{filename}",
                "storage_path": f"legacy/storage/{filename}",
                "review_path": f"ordered/{filename}",
                "type": "image",
                "mime_type": "image/png",
                "checksum": "",
                "width": width,
                "height": height,
                "rights": {
                    "status": "declared",
                    "permission_reference": "Internal review only",
                },
            }
        )
        import hashlib

        assets[-1]["checksum"] = hashlib.sha256(image_path.read_bytes()).hexdigest()

    manifest = {
        "bundle_version": "manual-review-source-ledger-v1",
        "project_id": "fixture-project",
        "source_storage_root": "legacy/storage",
        "asset_count": 24,
        "source_order_coverage": list(range(24)),
        "assets": assets,
    }
    manifest_path = _write_json(review_root / "manifest.json", manifest)
    return manifest_path, review_root


def _mutated_manifest(
    manifest_path: Path, mutation: str, tmp_path: Path
) -> tuple[Path, Path]:
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = value["assets"]
    if mutation == "duplicate":
        assets.append(copy.deepcopy(assets[-1]))
    elif mutation == "missing":
        assets.pop()
    elif mutation == "out_of_order":
        assets[0], assets[1] = assets[1], assets[0]
    elif mutation == "unknown_path":
        assets[3]["review_path"] = "../outside.png"
    elif mutation == "checksum":
        assets[4]["checksum"] = "0" * 64
    elif mutation == "dimension":
        assets[5]["width"] += 1
    else:
        raise AssertionError(f"unknown mutation {mutation}")
    broken_path = _write_json(tmp_path / f"{mutation}.json", value)
    return broken_path, manifest_path.parent


def test_valid_ledger_requires_title_and_all_story_orders(valid_manifest):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)

    assert [entry.source_order for entry in ledger.entries] == list(range(24))
    assert ledger.entries[0].included_in_story is False
    assert ledger.entries[0].exclusion_reason == "title_front_matter"
    assert [entry.source_order for entry in ledger.entries[1:]] == list(range(1, 24))
    assert ledger.provenance_kind == "codex_manual_vision_reference_v1"
    assert ledger.production_evidence is False
    assert ledger.production_analysis is False
    assert ledger.publish_allowed is False


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "missing", "out_of_order", "unknown_path", "checksum", "dimension"],
)
def test_ledger_mutations_fail_closed(mutation, valid_manifest, tmp_path):
    module = _module()
    manifest_path, review_root = valid_manifest
    broken_path, _ = _mutated_manifest(manifest_path, mutation, tmp_path)

    with pytest.raises(module.ManualReviewError, match=r"review\."):
        module.load_source_ledger(broken_path, base_dir=review_root)


def test_ledger_hash_is_deterministic_and_excludes_derived_hash(valid_manifest):
    module = _module()
    manifest_path, review_root = valid_manifest
    first = module.load_source_ledger(manifest_path, base_dir=review_root)
    second = module.load_source_ledger(manifest_path, base_dir=review_root)

    assert module.canonical_ledger_json(first) == module.canonical_ledger_json(second)
    assert first.ledger_sha256 == second.ledger_sha256
    assert "ledger_sha256" not in module.canonical_ledger_json(first)

