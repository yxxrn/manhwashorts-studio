from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

RUNNER = Path(__file__).parents[1] / "scripts" / "review" / "run_real_chapter_narrative_review.py"


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def valid_manifest(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source"
    ordered = root / "ordered"
    ordered.mkdir(parents=True)
    assets = []
    import hashlib

    for source_order in range(24):
        filename = f"{source_order:03d}-panel.png"
        image_path = ordered / filename
        Image.new("RGB", (80, 120), (source_order, 40, 100)).save(image_path)
        assets.append(
            {
                "asset_id": f"asset-{source_order:02d}",
                "panel_id": f"panel-{source_order:02d}",
                "source_order": source_order,
                "review_path": f"ordered/{filename}",
                "storage_path": f"legacy/{filename}",
                "checksum": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "width": 80,
                "height": 120,
                "rights": {"permission_reference": "Internal review only"},
            }
        )
    manifest = {
        "asset_count": 24,
        "source_order_coverage": list(range(24)),
        "assets": assets,
    }
    return _write_json(root / "manifest.json", manifest), root


def _run_cli(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *(str(arg) for arg in args)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_prepare_verifies_all_24_files_without_creating_media(tmp_path, valid_manifest):
    manifest_path, _ = valid_manifest
    output = tmp_path / "review"
    result = _run_cli("prepare", "--manifest", manifest_path, "--output", output)

    assert result.returncode == 0
    source_ledger = json.loads((output / "source_ledger.json").read_text(encoding="utf-8"))
    assert [entry["source_order"] for entry in source_ledger["entries"]] == list(range(24))
    assert json.loads((output / "panel_observations_template.json").read_text(encoding="utf-8"))
    assert not list(output.glob("*.mp4"))
    assert not list(output.glob("*.wav"))


def test_finalize_rejects_missing_order_and_production_provenance(tmp_path, valid_manifest):
    manifest_path, _ = valid_manifest
    output = tmp_path / "review"
    prepared = _run_cli("prepare", "--manifest", manifest_path, "--output", output)
    assert prepared.returncode == 0
    observations = _write_json(tmp_path / "observations.json", [])
    chapter_map = _write_json(tmp_path / "chapter-map.json", {})
    narrative = _write_json(tmp_path / "narrative.json", {"provenance_kind": "vision_evidence_v2"})

    result = _run_cli(
        "finalize",
        "--bundle",
        output,
        "--observations",
        observations,
        "--chapter-map",
        chapter_map,
        "--narrative",
        narrative,
    )

    assert result.returncode != 0
    assert "review.panel_coverage_invalid" in result.stderr

