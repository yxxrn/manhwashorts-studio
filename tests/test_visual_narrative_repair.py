"""RED-first tests for visual-aware narrative repair."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


def _module():
    try:
        return importlib.import_module("app.services.visual_narrative_repair")
    except Exception as exc:
        pytest.fail(f"visual narrative repair boundary import failed in test body: {exc}")


def _telemetry(module, crop_box=(0, 0, 1080, 1920)):
    return module.framing_analysis.FramingTelemetry(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        detector_version="color-agnostic-border-v1",
        mask_sha256="m" * 64,
        crop_box=crop_box,
        base_zoom=1.0,
        source_resolution_zoom_cap=1.5,
        protected_region_zoom_cap=1.5,
        edge_connected_blank_fraction=0.0,
        non_discardable_low_information_fraction=0.0,
        protected_retained_fraction=1.0,
        balloon_mask_intersection_ratio=0.0,
        subject_coverage=1.0,
        face_coverage=1.0,
        action_coverage=1.0,
        effect_coverage=1.0,
        continuity_context_coverage=1.0,
        mask_confidence=1.0,
        mask_source="vision",
    )


def _entry(module, *, panel_id="panel-safe", beat="beat-3", order=30, blank=0.0):
    return module.FeasibleVisualRecord(
        panel_region_id=f"region-{panel_id}",
        panel_id=panel_id,
        source_asset_id="asset-1",
        source_order=order,
        eligible_sections=(),
        eligible_beats=(beat,),
        resolution_state="UPSCALED",
        feasible_rois=(
            {
                "kind": "primary",
                "roi_label": "panel_primary",
                "crop_box": [0, 0, 1080, 1920],
                "telemetry": {
                    "balloon_mask_intersection_ratio": 0.0,
                    "protected_retained_fraction": 1.0,
                },
            },
        ),
        visual_strengths={
            "subject_coverage": 1.0,
            "action_coverage": 1.0,
            "edge_connected_blank_fraction": blank,
        },
        evidence_hash="e" * 64,
        detector_version="color-agnostic-border-v1",
        mask_sha256="m" * 64,
        panel_size=(1080, 1920),
    )


def test_zero_feasible_hook_is_explicitly_repairable_and_payload_is_panel_keyed():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(_entry(module),),
        model_identity_hash="model-hash",
    )

    missing = module.missing_visual_sections(
        ledger,
        {"hook": ("beat-1",), "setup": ("beat-3",)},
    )
    payload = module.build_repair_payload(
        narration={"passages": [{"passage_id": "p1", "text": "old"}]},
        story_map={"beats": [{"beat_id": "beat-3", "panel_ids": ["panel-safe"]}]},
        ledger=ledger,
        section_to_beats={"hook": ("beat-1",), "setup": ("beat-3",)},
    )

    assert missing == ("hook",)
    assert payload["repair_contract_version"] == module.REPAIR_CONTRACT_VERSION
    assert payload["missing_sections"] == ["hook"]
    assert payload["feasible_panel_ids"] == ["panel-safe"]
    assert "visual_evidence_by_asset" not in payload


def test_repaired_narrative_must_reference_only_feasible_panel_ids():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(_entry(module),),
        model_identity_hash="model-hash",
    )

    with pytest.raises(module.VisualNarrativeRepairError) as caught:
        module.validate_repaired_panel_references(
            {
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "evidence_panel_ids": ["panel-not-feasible"],
                    }
                ],
                "passages": [
                    {"passage_id": "p1", "claim_ids": ["claim-1"], "evidence_panel_ids": ["panel-not-feasible"]}
                ],
            },
            ledger=ledger,
            allowed_claim_ids={"claim-1"},
        )
    assert caught.value.code == "visual.narrative_repair_ungrounded"


def test_repaired_narrative_must_cover_each_missing_section_with_feasible_panel():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(_entry(module, panel_id="panel-safe", beat="beat-3"),),
        model_identity_hash="model-hash",
    )

    with pytest.raises(module.VisualNarrativeRepairError) as caught:
        module.validate_repaired_section_visual_coverage(
            [
                {
                    "passage_id": "p1",
                    "claim_ids": ["claim-1"],
                    "evidence_panel_ids": ["panel-originally-infeasible"],
                },
                {
                    "passage_id": "p2",
                    "claim_ids": ["claim-1"],
                    "evidence_panel_ids": ["panel-safe"],
                },
            ],
            ledger=ledger,
            section_to_beats={"hook": ("beat-1",), "setup": ("beat-3",)},
            missing_sections=("hook",),
        )

    assert caught.value.code == "visual.narrative_repair_ungrounded"


def test_adjacent_singleton_duplicate_panel_passages_are_coalesced_truthfully():
    module = _module()
    passages, provenance = module.coalesce_adjacent_duplicate_panel_passages(
        [
            {
                "passage_id": "p1",
                "text": "The turn lands.",
                "claim_ids": ["claim-1"],
                "evidence_panel_ids": ["panel-safe"],
            },
            {
                "passage_id": "p2",
                "text": "The consequence follows.",
                "claim_ids": ["claim-2"],
                "evidence_panel_ids": ["panel-safe"],
            },
        ]
    )

    assert len(passages) == 1
    assert passages[0]["text"] == "The turn lands. The consequence follows."
    assert passages[0]["claim_ids"] == ["claim-1", "claim-2"]
    assert passages[0]["evidence_panel_ids"] == ["panel-safe"]
    assert provenance[0]["contract_version"] == "visual_sequence_coalesce_v1"


def test_repaired_narrative_allows_later_dramatic_hook_but_keeps_following_beats_ordered():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-later", beat="beat-3", order=30),
            _entry(module, panel_id="panel-early", beat="beat-1", order=5),
        ),
        model_identity_hash="model-hash",
    )

    result = module.validate_repaired_panel_references(
        {
            "claims": [
                {"claim_id": "claim-1", "evidence_panel_ids": ["panel-later"]},
                {"claim_id": "claim-2", "evidence_panel_ids": ["panel-early"]},
            ],
            "passages": [
                {"passage_id": "p1", "editorial_role": "dramatic_opening", "claim_ids": ["claim-1"], "evidence_panel_ids": ["panel-later"]},
                {"passage_id": "p2", "editorial_role": "setup", "claim_ids": ["claim-2"], "evidence_panel_ids": ["panel-early"]},
            ],
        },
        ledger=ledger,
        allowed_claim_ids={"claim-1", "claim-2"},
    )

    assert result["passages"][0]["evidence_panel_ids"] == ["panel-later"]


def test_same_beat_remap_prefers_lower_blank_and_records_provenance():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-high-blank", beat="beat-3", order=30, blank=0.12),
            _entry(module, panel_id="panel-low-blank", beat="beat-3", order=31, blank=0.02),
            _entry(module, panel_id="panel-other-beat", beat="beat-4", order=32, blank=0.0),
        ),
        model_identity_hash="model-hash",
    )

    remapped, provenance = module.remap_same_beat_panel_citations(
        {
            "claims": [{"claim_id": "claim-1", "evidence_panel_ids": ["panel-high-blank"]}],
            "passages": [
                {
                    "passage_id": "p1",
                    "claim_ids": ["claim-1"],
                    "evidence_panel_ids": ["panel-high-blank"],
                }
            ],
        },
        ledger=ledger,
        section_to_beats={"hook": ("beat-3",)},
    )

    assert remapped["passages"][0]["evidence_panel_ids"] == ["panel-low-blank"]
    assert remapped["claims"][0]["evidence_panel_ids"] == ["panel-low-blank"]
    assert provenance[0]["contract_version"] == "visual_section_remap_v1"
    assert provenance[0]["from_panel_id"] == "panel-high-blank"
    assert provenance[0]["to_panel_id"] == "panel-low-blank"


def test_same_beat_remap_never_crosses_evidence_beats():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-current", beat="beat-3", order=30, blank=0.12),
            _entry(module, panel_id="panel-foreign", beat="beat-4", order=31, blank=0.0),
        ),
        model_identity_hash="model-hash",
    )

    remapped, provenance = module.remap_same_beat_panel_citations(
        {
            "claims": [{"claim_id": "claim-1", "evidence_panel_ids": ["panel-current"]}],
            "passages": [{"passage_id": "p1", "claim_ids": ["claim-1"], "evidence_panel_ids": ["panel-current"]}],
        },
        ledger=ledger,
        section_to_beats={"hook": ("beat-3",)},
    )

    assert remapped["passages"][0]["evidence_panel_ids"] == ["panel-current"]
    assert provenance == ()


def test_same_beat_remap_preserves_panel_capacity_across_sections():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-current", beat="beat-6", order=30, blank=0.20),
            _entry(module, panel_id="panel-low", beat="beat-6", order=31, blank=0.02),
            _entry(module, panel_id="panel-next", beat="beat-6", order=32, blank=0.04),
        ),
        model_identity_hash="model-hash",
    )

    remapped, _ = module.remap_same_beat_panel_citations(
        {
            "claims": [
                {"claim_id": "claim-1", "evidence_panel_ids": ["panel-current"]},
                {"claim_id": "claim-2", "evidence_panel_ids": ["panel-current"]},
            ],
            "passages": [
                {"passage_id": "p1", "claim_ids": ["claim-1"], "evidence_panel_ids": ["panel-current"]},
                {"passage_id": "p2", "claim_ids": ["claim-2"], "evidence_panel_ids": ["panel-current"]},
            ],
        },
        ledger=ledger,
        section_to_beats={"twist": ("beat-6",), "cta": ("beat-6",)},
    )

    assert [item["evidence_panel_ids"] for item in remapped["passages"]] == [
        ["panel-low"],
        ["panel-next"],
    ]


def test_same_beat_remap_uses_next_panel_when_best_panel_is_exhausted():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-best", beat="beat-6", order=30, blank=0.02),
            _entry(module, panel_id="panel-next", beat="beat-6", order=31, blank=0.04),
        ),
        model_identity_hash="model-hash",
    )

    remapped, _ = module.remap_same_beat_panel_citations(
        {
            "claims": [
                {"claim_id": "claim-1", "evidence_panel_ids": ["panel-best"]},
                {"claim_id": "claim-2", "evidence_panel_ids": ["panel-best"]},
            ],
            "passages": [
                {"passage_id": "p1", "claim_ids": ["claim-1"], "evidence_panel_ids": ["panel-best"]},
                {"passage_id": "p2", "claim_ids": ["claim-2"], "evidence_panel_ids": ["panel-best"]},
            ],
        },
        ledger=ledger,
        section_to_beats={"hook": ("beat-6",), "setup": ("beat-6",)},
    )

    assert [item["evidence_panel_ids"] for item in remapped["passages"]] == [
        ["panel-best"],
        ["panel-next"],
    ]


def test_claim_evidence_can_be_distributed_across_ordered_passages():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-first", beat="beat-1", order=5),
            _entry(module, panel_id="panel-second", beat="beat-2", order=10),
        ),
        model_identity_hash="model-hash",
    )

    result = module.validate_repaired_panel_references(
        {
            "claims": [
                {
                    "claim_id": "claim-1",
                    "evidence_panel_ids": ["panel-first", "panel-second"],
                }
            ],
            "passages": [
                {
                    "passage_id": "p1",
                    "editorial_role": "hook",
                    "claim_ids": ["claim-1"],
                    "evidence_panel_ids": ["panel-first"],
                },
                {
                    "passage_id": "p2",
                    "editorial_role": "setup",
                    "claim_ids": ["claim-1"],
                    "evidence_panel_ids": ["panel-second"],
                },
            ],
        },
        ledger=ledger,
        allowed_claim_ids={"claim-1"},
    )

    assert [item["evidence_panel_ids"] for item in result["passages"]] == [
        ["panel-first"],
        ["panel-second"],
    ]


def test_feasible_ledger_calls_framing_gate_and_excludes_rejected_panels(monkeypatch):
    module = _module()
    calls = []

    def fake_feasible(crop_box, evidence, mask, panel_size, target_size, **kwargs):
        calls.append((crop_box, panel_size, target_size, kwargs))
        telemetry = _telemetry(module, tuple(crop_box))
        return (crop_box[0] == 0), telemetry

    monkeypatch.setattr(module.framing_analysis, "candidate_is_feasible", fake_feasible)
    candidate = SimpleNamespace(
        panel_region_id="region-safe",
        panel_id="panel-safe",
        source_asset_id="asset-1",
        source_order=30,
        panel_size=(1080, 1920),
        border_mask=SimpleNamespace(
            mask_sha256="m" * 64,
            detector_version="color-agnostic-border-v1",
        ),
        visual_evidence=SimpleNamespace(),
        evidence_hash="e" * 64,
        source_upscale_manifest={"resolution_state": "UPSCALED"},
        eligible_sections=("setup",),
        eligible_beats=("beat-3",),
        roi_alternatives=(
            SimpleNamespace(kind="primary", roi_label="primary", crop_box=(0, 0, 1080, 1920), focus=(0.5, 0.5, 0.5, 0.5)),
            SimpleNamespace(kind="tighter_crop", roi_label="tight", crop_box=(1, 0, 1080, 1920), focus=(0.5, 0.5, 0.5, 0.5)),
        ),
    )

    ledger = module.build_feasible_visual_ledger(
        [candidate],
        profile=SimpleNamespace(final_width=1080, final_height=1920),
        model_identity_hash="model-hash",
    )

    assert len(ledger.entries) == 1
    assert [item["kind"] for item in ledger.entries[0].feasible_rois] == ["primary"]
    assert len(calls) == 2


def test_repair_attempts_are_bounded_and_cache_identity_includes_ledger_hash():
    module = _module()
    ledger = module.FeasibleVisualLedger(entries=(_entry(module),), model_identity_hash="model-hash")
    first = module.repair_cache_key(
        ledger=ledger,
        model_identity_hash="model-hash",
        prompt_sha256="p" * 64,
        narration_hash="n" * 64,
    )
    second = module.repair_cache_key(
        ledger=module.FeasibleVisualLedger(entries=(_entry(module, panel_id="other"),), model_identity_hash="model-hash"),
        model_identity_hash="model-hash",
        prompt_sha256="p" * 64,
        narration_hash="n" * 64,
    )
    assert first != second
    assert module.MAX_REPAIR_ATTEMPTS == 3


def test_repair_prompt_specifies_distributed_claim_evidence_binding():
    module = _module()
    prompt = " ".join(module.load_repair_prompt()[2].lower().split())

    assert "every passage citation must be a feasible panel" in prompt
    assert "distributed across passages" in prompt
    assert "each passage must include non-empty claim_ids" in prompt
    assert "all following passages must be chronological" in prompt
    assert "lowest edge_connected_blank_fraction first" in prompt
    assert "never claim that a nonzero blank fraction is zero" in prompt
    assert "same eligible beat" in prompt
    assert "lower-blank candidate" in prompt
    assert "enough feasible panel ids" in prompt
    assert "section capacity" in prompt
    assert "118-124 lexical words" in prompt
