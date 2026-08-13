from __future__ import annotations

import copy
import importlib
import json
from dataclasses import replace
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


def _valid_bundle(ledger):
    return {
        "provenance_kind": "codex_manual_vision_reference_v1",
        "production_evidence": False,
        "production_analysis": False,
        "publish_allowed": False,
        "rights_status": "internal review only",
        "panel_understanding": [],
        "chapter_map": {"beats": [], "causal_chain": [], "coverage": {}},
        "narrative_review": {
            "provenance_kind": "codex_manual_vision_reference_v1",
            "profile_id": "sharp_friend_v1",
            "passages": [],
            "ending_kind": "cliffhanger",
            "approval_state": "PENDING_EDITORIAL_REVIEW",
        },
        "narration_spoken": "Why can't Jin-Woo move?",
        "qc_report": {"blocking_findings": [], "warnings": []},
    }


def test_display_derivation_does_not_mutate_spoken_text():
    module = _module()
    spoken = "Why can't Jin-Woo move?"
    before = spoken[:]
    cues = module.derive_display_cues(spoken)

    assert [cue["display_text"] for cue in cues] == ["WHY", "CANT", "JINWOO", "MOVE"]
    assert spoken == before
    assert all(
        text.isalnum() and text == text.upper()
        for text in [cue["display_text"] for cue in cues]
    )


def test_bundle_rejects_production_provenance_and_media_payload(tmp_path, valid_manifest):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)
    bundle = _valid_bundle(ledger)
    bundle["provenance_kind"] = "vision_evidence_v2"
    with pytest.raises(module.ManualReviewError, match="review.provenance_invalid"):
        module.write_review_bundle(tmp_path / "bundle", bundle, ledger=ledger)

    bundle = _valid_bundle(ledger)
    bundle["panel_understanding"] = [{"image_path": "panel.png"}]
    with pytest.raises(module.ManualReviewError, match="review.media_payload_forbidden"):
        module.write_review_bundle(tmp_path / "media-bundle", bundle, ledger=ledger)


def test_bundle_round_trip_preserves_spoken_text_and_derived_cues(tmp_path, valid_manifest):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)
    bundle = _valid_bundle(ledger)
    output = tmp_path / "bundle"

    module.write_review_bundle(output, bundle, ledger=ledger)
    loaded = module.read_review_bundle(output, ledger=ledger)

    assert {path.name for path in output.iterdir()} == set(module.BUNDLE_FILES)
    assert loaded["narration_spoken"] == bundle["narration_spoken"]
    assert loaded["display_cues"] == [
        {
            "spoken_token_index": 0,
            "display_text": "WHY",
            "timing_status": "not_rendered",
        },
        {
            "spoken_token_index": 1,
            "display_text": "CANT",
            "timing_status": "not_rendered",
        },
        {
            "spoken_token_index": 2,
            "display_text": "JINWOO",
            "timing_status": "not_rendered",
        },
        {
            "spoken_token_index": 3,
            "display_text": "MOVE",
            "timing_status": "not_rendered",
        },
    ]


@pytest.mark.parametrize("filename", ["qc_report.json", "chapter_map.json"])
def test_bundle_rejects_missing_or_extra_files(tmp_path, valid_manifest, filename):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)
    output = tmp_path / "bundle"
    module.write_review_bundle(output, _valid_bundle(ledger), ledger=ledger)
    (output / filename).unlink()
    with pytest.raises(module.ManualReviewError, match="review.bundle_files_invalid"):
        module.read_review_bundle(output, ledger=ledger)


def test_bundle_rejects_display_hash_or_path_drift(tmp_path, valid_manifest):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)
    output = tmp_path / "bundle"
    module.write_review_bundle(output, _valid_bundle(ledger), ledger=ledger)

    display_path = output / "display_cues.json"
    display_path.write_text("[]", encoding="utf-8")
    with pytest.raises(module.ManualReviewError, match="review.display_derivation_invalid"):
        module.read_review_bundle(output, ledger=ledger)


def _valid_observations(ledger):
    observations = []
    for entry in ledger.entries:
        observations.append(
            {
                "source_order": entry.source_order,
                "source_asset_id": entry.source_asset_id,
                "panel_id": entry.panel_id,
                "visible_summary": (
                    "Title and front matter"
                    if entry.source_order == 0
                    else f"Visible action in panel {entry.source_order}."
                ),
                "visible_entities": [] if entry.source_order == 0 else ["visible subject"],
                "actions": [] if entry.source_order == 0 else ["moves"],
                "setting_or_continuity": "The ordered scene continues.",
                "dialogue_present": False,
                "dialogue_paraphrase": "",
                "dialogue_or_ocr": [],
                "uncertainties": [],
                "confidence": "high",
                "evidence_status": "manual_visual_review",
            }
        )
    return observations


def _valid_chapter_map():
    beats = [
        {
            "beat_id": "beat-01",
            "panel_orders": list(range(1, 9)),
            "visible_change": "The standoff develops.",
            "stakes": "The visible conflict becomes harder to avoid.",
            "qualification": "The interpretation is limited to the ordered visual sequence.",
            "evidence_refs": list(range(1, 9)),
        },
        {
            "beat_id": "beat-02",
            "panel_orders": list(range(9, 17)),
            "visible_change": "The confrontation changes direction.",
            "stakes": "The immediate danger increases.",
            "qualification": "The transition is inferred from consecutive visible actions.",
            "evidence_refs": list(range(9, 17)),
        },
        {
            "beat_id": "beat-03",
            "panel_orders": list(range(17, 24)),
            "visible_change": "The aftermath leaves an unresolved direction.",
            "stakes": "The next consequence remains important.",
            "qualification": "The ending direction is not fully shown in the panels.",
            "evidence_refs": list(range(17, 24)),
        },
    ]
    return {
        "beats": beats,
        "causal_chain": [
            {
                "from_beat": "beat-01",
                "to_beat": "beat-02",
                "relationship": "The first standoff leads into the visible confrontation.",
                "evidence_refs": [8, 9],
            },
            {
                "from_beat": "beat-02",
                "to_beat": "beat-03",
                "relationship": "The confrontation leaves the later situation unresolved.",
                "evidence_refs": [16, 17],
            },
        ],
        "coverage": {
            "story_orders_required": list(range(1, 24)),
            "story_orders_covered": list(range(1, 24)),
        },
        "story_spine": {
            "who_wants_what": "The visible group seeks a way through the conflict.",
            "obstacle": "The opposing situation blocks that intent.",
            "decision": "A visible decision changes the immediate exchange.",
            "consequence": "The sequence leaves a changed immediate situation.",
            "changed_stakes": "The next move carries greater pressure.",
            "unresolved_question": "What follows from the changed situation remains unresolved.",
        },
    }


def test_observations_require_all_orders_and_exact_lineage(valid_manifest):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)
    observations = _valid_observations(ledger)
    observations[-1]["source_order"] = 22

    with pytest.raises(module.ManualReviewError, match="review.panel_coverage_invalid"):
        module.validate_panel_observations(ledger, observations)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing_evidence", "review.evidence_missing"),
        ("foreign_panel", "review.evidence_foreign"),
        ("copied_dialogue", "narrative.balloon_dialogue_copied"),
        ("unqualified", "narrative.interpretation_unqualified"),
        ("cta", "narrative.cta"),
        ("hype", "narrative.generic_hype"),
        ("question_consequence", "narrative.ending_invalid"),
    ],
)
def test_manual_narrative_rejects_known_bad_contract(valid_manifest, mutation, code):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)
    observations = _valid_observations(ledger)
    typed_observations = module.validate_panel_observations(ledger, observations)
    if mutation == "copied_dialogue":
        typed_observations = tuple(
            replace(typed_observations[1], dialogue_or_ocr=("red guard opens gate",))
            if index == 1
            else item
            for index, item in enumerate(typed_observations)
        )
    review = _valid_review_from_typed(module, typed_observations, mutation)

    with pytest.raises(module.ManualReviewError, match=code):
        module.validate_manual_narrative(review, ledger=ledger)


def _valid_review_from_typed(module, typed_observations, mutation=None, passage_count=4, ending_kind="consequence"):
    claims = tuple(
        {
            "claim_id": f"claim-{index + 1}",
            "claim_type": "fact",
            "text": f"The ordered visuals support claim {index + 1}.",
            "qualification": "This claim is limited to the reviewed panels.",
            "evidence_panel_ids": [f"panel-{index + 1:02d}"],
        }
        for index in range(passage_count)
    )
    passages = [
        {
            "passage_id": f"passage-{index + 1}",
            "editorial_role": f"chapter beat {index + 1}",
            "text": f"The sequence makes claim {index + 1} visible, and the pressure keeps moving.",
            "claim_ids": [f"claim-{index + 1}"],
            "evidence_panel_ids": [f"panel-{index + 1:02d}"],
            "qualification": "The passage stays within the reviewed evidence.",
        }
        for index in range(passage_count)
    ]
    if mutation == "missing_evidence":
        passages[0]["claim_ids"] = []
    elif mutation == "foreign_panel":
        passages[0]["evidence_panel_ids"] = ["panel-foreign"]
    elif mutation == "copied_dialogue":
        passages[0]["text"] = "The red guard opens gate."
    elif mutation == "unqualified":
        claims = tuple(
            {**claim, "claim_type": "interpretation", "qualification": ""}
            if index == 0
            else claim
            for index, claim in enumerate(claims)
        )
    elif mutation == "cta":
        passages[0]["text"] = "Subscribe for more chapter reviews."
    elif mutation == "hype":
        passages[0]["text"] = "This is an epic battle with insane power."
    elif mutation == "question_consequence":
        passages[-1]["text"] = "What happens next?"
    elif mutation is not None:
        raise AssertionError(mutation)
    chapter_map = _valid_chapter_map()
    return module.ManualNarrativeReview(
        panel_observations=tuple(typed_observations),
        chapter_map=chapter_map,
        passages=tuple(passages),
        ending_kind=ending_kind,
        unresolved_question=chapter_map["story_spine"]["unresolved_question"],
        spoken_text=" ".join(item["text"] for item in passages),
        claims=claims,
    )


@pytest.mark.parametrize("passage_count", [4, 6])
@pytest.mark.parametrize("ending_kind", ["consequence", "cliffhanger"])
def test_manual_narrative_allows_flexible_passages_and_non_question_endings(
    valid_manifest, passage_count, ending_kind
):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)
    observations = module.validate_panel_observations(ledger, _valid_observations(ledger))
    review = _valid_review_from_typed(
        module, observations, passage_count=passage_count, ending_kind=ending_kind
    )

    module.validate_manual_narrative(review, ledger=ledger)


def test_manual_narrative_allows_evidence_grounded_open_question(valid_manifest):
    module = _module()
    manifest_path, review_root = valid_manifest
    ledger = module.load_source_ledger(manifest_path, base_dir=review_root)
    observations = module.validate_panel_observations(ledger, _valid_observations(ledger))
    review = _valid_review_from_typed(module, observations, ending_kind="open_question")
    review = replace(
        review,
        passages=(*review.passages[:-1], {**review.passages[-1], "text": "What follows now?"}),
        spoken_text=" ".join(
            [*(_passage["text"] for _passage in review.passages[:-1]), "What follows now?"]
        ),
    )

    module.validate_manual_narrative(review, ledger=ledger)
