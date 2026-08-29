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


def test_repair_payload_exposes_machine_readable_claim_chronology():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-late", beat="beat-2", order=30),
            _entry(module, panel_id="panel-early", beat="beat-1", order=10),
        ),
        model_identity_hash="model-hash",
    )
    payload = module.build_repair_payload(
        narration={
            "passages": [
                {
                    "passage_id": "p1",
                    "text": "Grounded narration remains visible.",
                    "claim_ids": ["claim-late"],
                    "evidence_panel_ids": ["panel-late"],
                }
            ],
            "estimated_duration_s": 3.0,
        },
        story_map={
            "beats": [
                {"beat_id": "beat-1", "panel_ids": ["panel-early"]},
                {"beat_id": "beat-2", "panel_ids": ["panel-late"]},
            ],
            "claims": [
                {"claim_id": "claim-late", "panel_ids": ["panel-late"]},
                {"claim_id": "claim-early", "panel_ids": ["panel-early"]},
            ],
        },
        ledger=ledger,
        section_to_beats={"hook": ("beat-1",), "setup": ("beat-2",)},
    )

    claims = {row["claim_id"]: row for row in payload["feasible_claims"]}
    assert claims["claim-early"]["evidence_source_orders"] == [10]
    assert claims["claim-early"]["min_source_order"] == 10
    assert claims["claim-late"]["max_source_order"] == 30
    assert claims["claim-early"]["evidence_panel_slot_capacity"] == {"panel-early": 1}
    assert claims["claim-early"]["visual_slot_capacity"] == 1
    assert claims["claim-early"]["unique_panel_count"] == 1
    capacity = payload["capacity_contract"]
    assert capacity["max_shot_duration_s"] == 4.0
    assert capacity["narration_words_per_second"] == 2.3
    assert capacity["max_lexical_words_per_visual_slot"] == 9
    assert capacity["claim_capacity_field"] == "visual_slot_capacity"
    assert capacity["panel_capacity_field"] == "evidence_panel_slot_capacity"
    chronology = payload["chronology_contract"]
    assert chronology["non_hook_rule"] == "nondecreasing_min_source_order"
    assert [row["claim_id"] for row in chronology["claims_by_source_order"]] == [
        "claim-early",
        "claim-late",
    ]
    assert payload["constraints"][
        "non_hook_claims_must_follow_chronology_contract"
    ] is True


def test_feasible_ledger_excludes_title_page_candidate_that_planner_rejects(monkeypatch):
    """The repair ledger and reference planner must share title-page policy."""
    module = _module()
    candidate = SimpleNamespace(
        panel_region_id="region-title",
        panel_id="panel-title",
        source_asset_id="asset-title",
        source_order=0,
        eligible_sections=("hook",),
        eligible_beats=("beat-1",),
        resolution_state="NATIVE",
        visual_strengths={},
        evidence_hash="e" * 64,
        source_checksum="s" * 64,
        panel_size=(1080, 1920),
        border_mask=SimpleNamespace(
            detector_version="color-agnostic-border-v1",
            mask_sha256="m" * 64,
        ),
        roi_alternatives=(
            SimpleNamespace(
                kind="primary",
                roi_label="panel_primary",
                crop_box=(0, 0, 1080, 1920),
            ),
        ),
        panel_candidate=SimpleNamespace(source_family="001__001"),
        visual_evidence=SimpleNamespace(),
    )
    monkeypatch.setattr(
        module.framing_analysis,
        "candidate_is_feasible",
        lambda *_args, **_kwargs: (True, _telemetry(module)),
    )

    ledger = module.build_feasible_visual_ledger(
        (candidate,),
        profile=SimpleNamespace(
            final_width=1080,
            final_height=1920,
            framing_blank_target_fraction=0.25,
        ),
        model_identity_hash="model-hash",
    )

    assert ledger.entries == ()


def test_non_title_first_panel_family_is_not_rejected_by_title_policy():
    module = _module()

    assert module.editorial_visual_planner.is_title_page_family(
        "009__001", source_order=26
    ) is False


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



def test_adjacent_duplicate_coalesce_respects_required_section_count():
    module = _module()
    source = [
        {
            "passage_id": f"p{index}",
            "text": f"Passage {index}.",
            "claim_ids": [f"claim-{index}"],
            "evidence_panel_ids": [panel_id],
        }
        for index, panel_id in enumerate(
            ("panel-1", "panel-2", "panel-shared", "panel-shared", "panel-5"),
            start=1,
        )
    ]

    passages, provenance = module.coalesce_adjacent_duplicate_panel_passages(
        source,
        minimum_passage_count=5,
    )

    assert len(passages) == 5
    assert [row["passage_id"] for row in passages] == ["p1", "p2", "p3", "p4", "p5"]
    assert provenance == ()

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




def test_same_beat_remap_preserves_existing_non_hook_chronology():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-hook", beat="beat-hook", order=42, blank=0.0),
            _entry(module, panel_id="panel-setup", beat="beat-setup", order=49, blank=0.0),
            _entry(module, panel_id="panel-current", beat="beat-middle", order=71, blank=0.12),
            _entry(module, panel_id="panel-too-late", beat="beat-middle", order=93, blank=0.01),
            _entry(module, panel_id="panel-twist", beat="beat-twist", order=81, blank=0.0),
            _entry(module, panel_id="panel-cta", beat="beat-cta", order=82, blank=0.0),
        ),
        model_identity_hash="model-hash",
    )
    value = {
        "claims": [
            {"claim_id": f"claim-{index}", "evidence_panel_ids": [panel_id]}
            for index, panel_id in enumerate(
                ("panel-hook", "panel-setup", "panel-current", "panel-twist", "panel-cta"),
                start=1,
            )
        ],
        "passages": [
            {
                "passage_id": f"p{index}",
                "claim_ids": [f"claim-{index}"],
                "evidence_panel_ids": [panel_id],
            }
            for index, panel_id in enumerate(
                ("panel-hook", "panel-setup", "panel-current", "panel-twist", "panel-cta"),
                start=1,
            )
        ],
    }

    repaired, provenance = module.remap_same_beat_panel_citations(
        value,
        ledger=ledger,
        section_to_beats={
            "hook": ("beat-hook",),
            "setup": ("beat-setup",),
            "body": ("beat-middle",),
            "twist": ("beat-twist",),
            "cta": ("beat-cta",),
        },
    )

    assert [row["evidence_panel_ids"] for row in repaired["passages"]] == [
        ["panel-hook"],
        ["panel-setup"],
        ["panel-current"],
        ["panel-twist"],
        ["panel-cta"],
    ]
    assert not any(item["to_panel_id"] == "panel-too-late" for item in provenance)
    module.validate_repaired_panel_references(
        repaired,
        ledger=ledger,
        allowed_claim_ids={f"claim-{index}" for index in range(1, 6)},
    )

def test_same_beat_remap_repairs_non_hook_chronology_inside_claim_lineage():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-hook", beat="beat-hook", order=90, blank=0.0),
            _entry(module, panel_id="panel-setup", beat="beat-setup", order=40, blank=0.0),
            _entry(module, panel_id="panel-body-stale", beat="beat-body", order=30, blank=0.01),
            _entry(module, panel_id="panel-body-ordered", beat="beat-body", order=55, blank=0.05),
            _entry(module, panel_id="panel-twist", beat="beat-twist", order=70, blank=0.0),
        ),
        model_identity_hash="model-hash",
    )
    value = {
        "claims": [
            {"claim_id": "claim-hook", "evidence_panel_ids": ["panel-hook"]},
            {"claim_id": "claim-setup", "evidence_panel_ids": ["panel-setup"]},
            {"claim_id": "claim-body", "evidence_panel_ids": ["panel-body-stale"]},
            {"claim_id": "claim-twist", "evidence_panel_ids": ["panel-twist"]},
        ],
        "passages": [
            {"passage_id": "p1", "claim_ids": ["claim-hook"], "evidence_panel_ids": ["panel-hook"]},
            {"passage_id": "p2", "claim_ids": ["claim-setup"], "evidence_panel_ids": ["panel-setup"]},
            {"passage_id": "p3", "claim_ids": ["claim-body"], "evidence_panel_ids": ["panel-body-stale"]},
            {"passage_id": "p4", "claim_ids": ["claim-twist"], "evidence_panel_ids": ["panel-twist"]},
        ],
    }

    repaired, provenance = module.remap_same_beat_panel_citations(
        value,
        ledger=ledger,
        section_to_beats={
            "hook": ("beat-hook",),
            "setup": ("beat-setup",),
            "body": ("beat-body",),
            "twist": ("beat-twist",),
        },
        allowed_claim_panel_ids={
            "claim-hook": {"panel-hook"},
            "claim-setup": {"panel-setup"},
            "claim-body": {"panel-body-stale", "panel-body-ordered"},
            "claim-twist": {"panel-twist"},
        },
    )

    assert [row["evidence_panel_ids"] for row in repaired["passages"]] == [
        ["panel-hook"],
        ["panel-setup"],
        ["panel-body-ordered"],
        ["panel-twist"],
    ]
    assert any(
        item["from_panel_id"] == "panel-body-stale"
        and item["to_panel_id"] == "panel-body-ordered"
        and item["reason"] == "claim-lineage chronology repair"
        for item in provenance
    )
    module.validate_repaired_panel_references(
        repaired,
        ledger=ledger,
        allowed_claim_ids={"claim-hook", "claim-setup", "claim-body", "claim-twist"},
        allowed_claim_panel_ids={
            "claim-hook": {"panel-hook"},
            "claim-setup": {"panel-setup"},
            "claim-body": {"panel-body-stale", "panel-body-ordered"},
            "claim-twist": {"panel-twist"},
        },
    )


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
    monkeypatch.setattr(
        module.editorial_visual_planner.visual_scoring,
        "require_reference_ready_visual_evidence",
        lambda evidence, **_kwargs: evidence,
    )
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


def test_feasible_ledger_forwards_conservative_full_panel_opt_in(monkeypatch):
    module = _module()
    calls = []

    def fake_feasible(crop_box, evidence, mask, panel_size, target_size, **kwargs):
        calls.append(kwargs)
        return (bool(kwargs.get("allow_conservative_full_panel")), _telemetry(module, tuple(crop_box)))

    monkeypatch.setattr(module.framing_analysis, "candidate_is_feasible", fake_feasible)
    monkeypatch.setattr(
        module.editorial_visual_planner.visual_scoring,
        "require_reference_ready_visual_evidence",
        lambda evidence, **_kwargs: evidence,
    )
    candidate = SimpleNamespace(
        panel_region_id="region-fallback",
        panel_id="panel-fallback",
        source_asset_id="asset-1",
        source_order=1,
        panel_size=(1080, 2164),
        border_mask=SimpleNamespace(
            mask_sha256="m" * 64,
            detector_version="color-agnostic-border-v1",
        ),
        visual_evidence=SimpleNamespace(),
        evidence_hash="e" * 64,
        source_upscale_manifest={"resolution_state": "UPSCALED"},
        eligible_sections=("hook",),
        eligible_beats=("beat-1",),
        roi_alternatives=(
            SimpleNamespace(
                kind="conservative_full_panel",
                roi_label="conservative_full_panel",
                crop_box=(0, 0, 1080, 2164),
                focus=(0.5, 0.5, 0.5, 0.5),
            ),
        ),
    )

    ledger = module.build_feasible_visual_ledger(
        [candidate],
        profile=SimpleNamespace(final_width=1080, final_height=1920),
        model_identity_hash="model-hash",
        allow_conservative_full_panel=True,
    )

    assert len(ledger.entries) == 1
    assert calls[0]["allow_conservative_full_panel"] is True


def test_feasible_ledger_uses_final_framing_telemetry_not_stale_roi_edge_estimate(monkeypatch):
    module = _module()
    evidence = SimpleNamespace()
    monkeypatch.setattr(
        module.editorial_visual_planner.visual_scoring,
        "require_reference_ready_visual_evidence",
        lambda value, **_kwargs: value,
    )
    monkeypatch.setattr(
        module.framing_analysis,
        "candidate_is_feasible",
        lambda *_args, **_kwargs: (True, _telemetry(module)),
    )
    candidate = SimpleNamespace(
        panel_region_id="region-edge-blank",
        panel_id="panel-edge-blank",
        source_asset_id="asset-1",
        source_order=30,
        panel_size=(1080, 1920),
        border_mask=SimpleNamespace(mask_sha256="m" * 64, detector_version="color-agnostic-border-v1"),
        visual_evidence=evidence,
        evidence_hash="e" * 64,
        source_upscale_manifest={"resolution_state": "UPSCALED"},
        eligible_sections=("hook",),
        eligible_beats=("beat-1",),
        roi_alternatives=(SimpleNamespace(
            kind="primary", roi_label="primary", crop_box=(0, 0, 1080, 1920),
            focus=(0.5, 0.5, 0.5, 0.5), edge_blank_fraction=1.0,
        ),),
    )
    ledger = module.build_feasible_visual_ledger(
        [candidate],
        profile=SimpleNamespace(final_width=1080, final_height=1920),
        model_identity_hash="model-hash",
    )
    assert len(ledger.entries) == 1
    assert ledger.entries[0].panel_id == "panel-edge-blank"
    assert ledger.entries[0].feasible_rois[0]["telemetry"]["edge_connected_blank_fraction"] == 0.0


def test_repair_scope_includes_stale_cta_citation_even_when_cta_beat_has_capacity():
    module = _module()
    entries = (
        _entry(module, panel_id="panel-hook", beat="beat-hook", order=10),
        _entry(module, panel_id="panel-setup", beat="beat-setup", order=20),
        _entry(module, panel_id="panel-conflict", beat="beat-conflict", order=30),
        _entry(module, panel_id="panel-twist", beat="beat-twist", order=40),
        _entry(module, panel_id="panel-cta-safe", beat="beat-cta", order=50),
    )
    ledger = module.FeasibleVisualLedger(entries=entries, model_identity_hash="model-hash")
    sections = {
        "hook": ("beat-hook",),
        "setup": ("beat-setup",),
        "conflict": ("beat-conflict",),
        "twist": ("beat-twist",),
        "cta": ("beat-cta",),
    }
    narration = {
        "ending_kind": "open_question",
        "evidence_graph": {
            "claims": [
                {"claim_id": "claim-hook", "evidence_panel_ids": ["panel-hook"]},
                {"claim_id": "claim-setup", "evidence_panel_ids": ["panel-setup"]},
                {"claim_id": "claim-conflict", "evidence_panel_ids": ["panel-conflict"]},
                {"claim_id": "claim-twist", "evidence_panel_ids": ["panel-twist"]},
                {"claim_id": "claim-stale-cta", "evidence_panel_ids": ["panel-sliver"]},
            ]
        },
        "passages": [
            {"passage_id": "p1", "claim_ids": ["claim-hook"], "evidence_panel_ids": ["panel-hook"]},
            {"passage_id": "p2", "claim_ids": ["claim-setup"], "evidence_panel_ids": ["panel-setup"]},
            {"passage_id": "p3", "claim_ids": ["claim-conflict"], "evidence_panel_ids": ["panel-conflict"]},
            {"passage_id": "p4", "claim_ids": ["claim-twist"], "evidence_panel_ids": ["panel-twist"]},
            {"passage_id": "p5", "claim_ids": ["claim-stale-cta"], "evidence_panel_ids": ["panel-sliver"]},
        ],
    }
    story_map = {
        "beats": [
            {"beat_id": beat, "panel_ids": [entry.panel_id]}
            for beat, entry in zip(
                ("beat-hook", "beat-setup", "beat-conflict", "beat-twist", "beat-cta"),
                entries,
                strict=True,
            )
        ],
        "claims": [
            {"claim_id": "claim-cta-safe", "text": "Safe ending fact.", "panel_ids": ["panel-cta-safe"]}
        ],
    }

    assert module.missing_visual_sections(ledger, sections) == ()
    assert module.repair_scope_sections(narration, ledger, sections) == ("cta",)
    payload = module.build_repair_payload(
        narration=narration,
        story_map=story_map,
        ledger=ledger,
        section_to_beats=sections,
    )
    assert payload["missing_sections"] == ["cta"]
    assert payload["feasible_claim_ids"] == ["claim-cta-safe"]




def test_repair_scope_includes_subtitle_overflow_with_feasible_visual_citations():
    module = _module()
    entries = (
        _entry(module, panel_id="panel-hook", beat="beat-hook", order=10),
        _entry(module, panel_id="panel-setup", beat="beat-setup", order=20),
        _entry(module, panel_id="panel-conflict", beat="beat-conflict", order=30),
        _entry(module, panel_id="panel-twist", beat="beat-twist", order=40),
        _entry(module, panel_id="panel-cta", beat="beat-cta", order=50),
    )
    ledger = module.FeasibleVisualLedger(entries=entries, model_identity_hash="model-hash")
    sections = {
        "hook": ("beat-hook",),
        "setup": ("beat-setup",),
        "conflict": ("beat-conflict",),
        "twist": ("beat-twist",),
        "cta": ("beat-cta",),
    }
    passage_rows = (
        ("hook", "claim-hook", "panel-hook", "A hero waits."),
        ("setup", "claim-setup", "panel-setup", "The trail continues."),
        ("conflict", "claim-conflict", "panel-conflict", "Trouble builds nearby."),
        ("twist", "claim-twist", "panel-twist", "A secret surfaces."),
        (
            "cta",
            "claim-cta",
            "panel-cta",
            "Plans form to resolve challenges independently.",
        ),
    )
    narration = {
        "estimated_duration_s": 12.0,
        "evidence_graph": {
            "claims": [
                {"claim_id": claim_id, "evidence_panel_ids": [panel_id]}
                for _section, claim_id, panel_id, _text in passage_rows
            ]
        },
        "passages": [
            {
                "passage_id": f"p{index}",
                "claim_ids": [claim_id],
                "evidence_panel_ids": [panel_id],
                "text": text,
            }
            for index, (_section, claim_id, panel_id, text) in enumerate(
                passage_rows, start=1
            )
        ],
    }

    assert module.missing_visual_sections(ledger, sections) == ()
    assert module.narration_sections_with_infeasible_citations(
        narration, ledger, sections
    ) == ()
    assert module.narration_sections_with_subtitle_overflow(
        narration, sections
    ) == ("cta",)
    assert module.repair_scope_sections(narration, ledger, sections) == ("cta",)

def test_repair_attempts_are_bounded_and_cache_identity_includes_ledger_and_capacity_plan_hash():
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
    third = module.repair_cache_key(
        ledger=ledger,
        model_identity_hash="model-hash",
        prompt_sha256="p" * 64,
        narration_hash="n" * 64,
        capacity_plan_hash="capacity-plan-b",
    )
    assert first != second
    assert first != third
    assert module.MAX_REPAIR_ATTEMPTS == 3


def test_repair_prompt_specifies_distributed_claim_evidence_binding():
    module = _module()
    prompt = " ".join(module.load_repair_prompt()[2].lower().split())

    assert "every passage citation must be a feasible panel" in prompt
    assert "distributed across passages" in prompt
    assert "each passage must include non-empty claim_ids" in prompt
    assert "nondecreasing subsequence" in prompt
    assert "min_source_order" in prompt
    assert "lowest edge_connected_blank_fraction first" in prompt
    assert "never claim that a nonzero blank fraction is zero" in prompt
    assert "same eligible beat" in prompt
    assert "lower-blank candidate" in prompt
    assert "enough feasible panel ids" in prompt
    assert "section capacity" in prompt
    assert "capacity_safe_claim_plan" in prompt
    assert "same number of passages" in prompt
    assert "displayed panels match the spoken content" in prompt
    assert "capacity_rebalance" in prompt
    assert "max_lexical_words" in prompt
    assert "mandatory" in prompt



def _coherence_claim(claim_id: str, panel_id: str, order: int):
    return {
        "claim_id": claim_id,
        "evidence_panel_ids": [panel_id],
        "evidence_panel_slot_capacity": {panel_id: 1},
        "min_source_order": order,
        "max_source_order": order,
    }



def test_selected_story_context_scopes_transition_semantics_without_broadening_evidence():
    module = _module()
    story_map = {
        "beats": [
            {"beat_id": "beat-a", "panel_ids": ["panel-a", "other-a"], "summary": "Setup context."},
            {"beat_id": "beat-b", "panel_ids": ["panel-b"], "summary": "Escalation context."},
            {"beat_id": "beat-c", "panel_ids": ["panel-c"], "summary": "Action context."},
            {"beat_id": "beat-d", "panel_ids": ["panel-d"], "summary": "Aftermath context."},
        ],
        "causal_chain": [
            {"from_beat": "beat-a", "to_beat": "beat-b", "reason": "the setup leads into escalation"},
            {"from_beat": "beat-b", "to_beat": "beat-c", "reason": "the escalation leads into action"},
        ],
    }
    plan = {"rows": [
        {"passage_index": 0, "section": "hook", "claim_ids": ["claim-c"], "evidence_panel_ids": ["panel-c"]},
        {"passage_index": 1, "section": "setup", "claim_ids": ["claim-a"], "evidence_panel_ids": ["panel-a"]},
        {"passage_index": 2, "section": "conflict", "claim_ids": ["claim-b"], "evidence_panel_ids": ["panel-b"]},
        {"passage_index": 3, "section": "ending", "claim_ids": ["claim-d"], "evidence_panel_ids": ["panel-d"]},
    ]}
    context = module._selected_story_context(story_map, plan)
    assert [row["evidence_panel_ids"] for row in context] == [["panel-c"], ["panel-a"], ["panel-b"], ["panel-d"]]
    assert context[0]["incoming_bridge"]["kind"] == "hook_teaser"
    assert context[1]["incoming_bridge"]["kind"] == "teaser_rewind"
    assert context[2]["incoming_bridge"] == {"kind": "causal", "causal_wording_allowed": True, "reason": "the setup leads into escalation"}
    assert context[3]["incoming_bridge"]["kind"] == "temporal_only"
    assert context[3]["incoming_bridge"]["causal_wording_allowed"] is False
    assert context[1]["beat_context"][0]["summary"] == "Setup context."
    assert context[1]["context_is_transition_only"] is True


def test_lock_capacity_plan_references_overrides_provider_grounding_metadata():
    module = _module()
    passages = [{
        "passage_id": "p0",
        "text": "Provider prose remains editable.",
        "claim_ids": ["wrong-claim"],
        "evidence_panel_ids": ["wrong-panel"],
        "panel_ids": ["alias-panel"],
    }]
    plan = {"rows": [{
        "claim_ids": ["claim-safe"],
        "evidence_panel_ids": ["panel-safe"],
    }]}
    locked = module.lock_capacity_plan_references(passages, plan)
    assert locked == [{
        "passage_id": "p0",
        "text": "Provider prose remains editable.",
        "claim_ids": ["claim-safe"],
        "evidence_panel_ids": ["panel-safe"],
    }]
    assert passages[0]["claim_ids"] == ["wrong-claim"]

def test_hook_quality_rejects_clinical_gender_nouns_and_missing_sword_object():
    module = _module()
    passages = [
        {"text": "Swords clash as two fighters meet above the ground.", "claim_ids": ["c1"]},
        {"text": "Before that the female raised her sword while the male swung his forward.", "claim_ids": ["c2"]},
        {"text": "Sparks flew when the blades met.", "claim_ids": ["c3"]},
        {"text": "Energy flashed as another strike followed.", "claim_ids": ["c4"]},
        {"text": "Their expressions changed when the clash ended.", "claim_ids": ["c5"]},
    ]
    claims = [
        {"claim_id": f"c{i}", "text": "combat claim", "evidence_panel_ids": [f"p{i}"]}
        for i in range(1, 6)
    ]
    with pytest.raises(module.VisualNarrativeRepairError) as caught:
        module.validate_repaired_hook_quality(passages, claims, {"rows": [{"hook_priority_score": 4}]})
    assert caught.value.code == "cloud.narrative_style_stiff"


def test_hook_quality_rejects_duel_freezes_in_place_metaphor():
    module = _module()
    passages = [
        {"text": "Swords meet as the duel freezes in place.", "claim_ids": ["c1"]},
        {"text": "Before that two fighters prepared their weapons.", "claim_ids": ["c2"]},
        {"text": "Sparks flew when the blades met.", "claim_ids": ["c3"]},
        {"text": "Energy flashed as another strike followed.", "claim_ids": ["c4"]},
        {"text": "Their expressions changed when the clash ended.", "claim_ids": ["c5"]},
    ]
    claims = [{"claim_id": f"c{i}", "text": "combat claim", "evidence_panel_ids": [f"p{i}"]} for i in range(1, 6)]
    with pytest.raises(module.VisualNarrativeRepairError) as caught:
        module.validate_repaired_hook_quality(passages, claims, {"rows": [{"hook_priority_score": 4}]})
    assert caught.value.code == "cloud.narrative_style_stiff"


def test_hook_quality_rejects_padded_counter_and_now_at_last_prose():
    module = _module()
    passages = [
        {"text": "A sword clash changes the duel as both fighters hold position.", "claim_ids": ["c1"]},
        {"text": "They trained together before stepping into position together now at last.", "claim_ids": ["c2"]},
        {"text": "Sparks erupted as their blades collided.", "claim_ids": ["c3"]},
        {"text": "Energy flashed from every counter swing that followed.", "claim_ids": ["c4"]},
        {"text": "Shock crossed their faces when the clash ended.", "claim_ids": ["c5"]},
    ]
    claims = [
        {"claim_id": f"c{i}", "text": "combat claim", "evidence_panel_ids": [f"p{i}"]}
        for i in range(1, 6)
    ]
    with pytest.raises(module.VisualNarrativeRepairError) as caught:
        module.validate_repaired_hook_quality(passages, claims, {"rows": [{"hook_priority_score": 4}]})
    assert caught.value.code == "cloud.narrative_style_stiff"


def test_hook_quality_counts_as_and_when_as_temporal_story_bridges():
    module = _module()
    passages = [
        {"text": "Sparks fly when blades meet high above the ground.", "claim_ids": ["c1"]},
        {"text": "Earlier they readied weapons before moving into position.", "claim_ids": ["c2"]},
        {"text": "Blades met as bright sparks opened the clash.", "claim_ids": ["c3"]},
        {"text": "Energy flared as reactions rippled through the fight.", "claim_ids": ["c4"]},
        {"text": "Expressions hardened when the blades locked again.", "claim_ids": ["c5"]},
    ]
    claims = [
        {"claim_id": "c1", "text": "sword clash combat"},
        {"claim_id": "c2", "text": "weapon preparation"},
        {"claim_id": "c3", "text": "sword clash"},
        {"claim_id": "c4", "text": "energy reactions"},
        {"claim_id": "c5", "text": "sword clash reactions"},
    ]
    report = module.validate_repaired_hook_quality(
        passages, claims, {"rows": [{"hook_priority_score": 4}]}
    )
    assert report["story_bridge_count"] >= 2


def test_coherence_window_prefers_deepest_scope_that_can_sustain_narration():
    module = _module()
    claims = []
    for index in range(7):
        claims.append(_coherence_claim(f"b0__sub0__unit{index}__claim1", f"b0a-{index}", index))
    for index in range(6):
        claims.append(_coherence_claim(f"b0__sub1__unit{index}__claim1", f"b0b-{index}", 20 + index))
    for index in range(13):
        claims.append(_coherence_claim(f"b1__sub2__unit{index}__claim1", f"b1-{index}", 100 + index * 10))

    selected, metadata = module._select_coherent_claim_window(
        claims, minimum_unique_panels=13
    )

    assert metadata["feasible"] is True
    assert metadata["selected_scope_prefix"] == "b1__sub2"
    assert metadata["selected_scope_depth"] == 2
    assert len({p for row in selected for p in row["evidence_panel_ids"]}) == 13



def test_coherence_window_rejects_interleaved_common_prefix_stitching():
    module = _module()
    orders = [210, 230, 252, 253, 257, 283, 295, 298, 322, 327, 341, 346, 347, 368, 378]
    claims = [
        _coherence_claim(
            f"b1__sub{index % 3}__unit{index}__claim1",
            f"panel-{order}",
            order,
        )
        for index, order in enumerate(orders)
    ]

    selected, metadata = module._select_coherent_claim_window(
        claims, minimum_unique_panels=13
    )

    assert selected == []
    assert metadata["feasible"] is False
    assert metadata["reason"] == "no_single_story_scope_has_required_visual_capacity"



def test_coherence_window_accepts_connected_adjacent_combat_subbranches():
    module = _module()
    claims = [
        _coherence_claim(f"b1__sub2__sub1__claim{index}", f"combat-a-{order}", order)
        for index, order in enumerate((337, 341, 344, 349), start=1)
    ] + [
        _coherence_claim(f"b1__sub2__sub2__claim{index}", f"combat-b-{order}", order)
        for index, order in enumerate((355, 361, 368, 378), start=1)
    ]

    selected, metadata = module._select_coherent_claim_window(
        claims, minimum_unique_panels=8
    )

    assert metadata["feasible"] is True
    assert metadata["rule"] == "story_coherence_window_v2"
    assert metadata["selected_scope_prefix"] == "b1__sub2"
    assert metadata["selected_connected_scope_chain"] == [
        "b1__sub2__sub1",
        "b1__sub2__sub2",
    ]
    assert metadata["selected_source_order_min"] == 337
    assert metadata["selected_source_order_max"] == 378
    assert metadata["selected_unique_panel_count"] == 8
    assert len(selected) == 8



def test_coherence_window_prefers_eight_panel_combat_arc_over_shorter_distractor():
    module = _module()
    claims = [
        _coherence_claim("b1__sub2__sub0__claim4", "distractor-327", 327),
        *[
            _coherence_claim(
                f"b1__sub2__sub1__claim{index}",
                f"combat-a-{order}",
                order,
            )
            for index, order in enumerate((337, 341, 343, 344, 346), start=1)
        ],
        *[
            _coherence_claim(
                f"b1__sub2__sub2__claim{index}",
                f"combat-b-{order}",
                order,
            )
            for index, order in enumerate((347, 349, 361), start=1)
        ],
    ]
    for row in claims:
        if row["claim_id"].startswith(("b1__sub2__sub1", "b1__sub2__sub2")):
            row["text"] = "Sword combat clash with weapon effects and reactions."
        else:
            row["text"] = "A distressed expression appears."

    selected, metadata = module._select_coherent_claim_window(
        claims,
        minimum_unique_panels=7,
        preferred_unique_panels=8,
    )

    assert metadata["feasible"] is True
    assert metadata["selected_preferred_capacity_met"] is True
    assert metadata["selected_scope_prefix"] == "b1__sub2"
    assert metadata["selected_connected_scope_chain"] == [
        "b1__sub2__sub1",
        "b1__sub2__sub2",
    ]
    assert metadata["selected_source_order_min"] == 337
    assert metadata["selected_source_order_max"] == 361
    assert metadata["selected_unique_panel_count"] == 8
    assert all(
        not row["claim_id"].startswith("b1__sub2__sub0") for row in selected
    )


def test_coherence_window_reselects_when_preferred_window_is_not_section_safe():
    module = _module()
    attractive = [
        _coherence_claim(
            f"b1__sub0__claim{index}",
            f"unsafe-{index}",
            10 + index,
        )
        for index in range(1, 6)
    ]
    safe = [
        _coherence_claim(
            f"b1__sub1__claim{index}",
            f"safe-{index}",
            30 + index,
        )
        for index in range(1, 6)
    ]
    for row in attractive:
        row["text"] = "Sword attack clash danger threat reveals hidden trap."
    for row in safe:
        row["text"] = "Characters continue the same grounded scene."

    selected_without_guard, metadata_without_guard = module._select_coherent_claim_window(
        [*attractive, *safe],
        minimum_unique_panels=5,
        preferred_unique_panels=5,
    )
    selected, metadata = module._select_coherent_claim_window(
        [*attractive, *safe],
        minimum_unique_panels=5,
        preferred_unique_panels=5,
        window_is_feasible=lambda rows: all(
            "__sub0__" not in str(row.get("claim_id", "")) for row in rows
        ),
    )

    assert metadata_without_guard["selected_scope_prefix"] == "b1__sub0"
    assert all("__sub0__" in row["claim_id"] for row in selected_without_guard)
    assert metadata["feasible"] is True
    assert metadata["section_capacity_aware"] is True
    assert metadata["selected_scope_prefix"] == "b1__sub1"
    assert all("__sub1__" in row["claim_id"] for row in selected)


def test_coherence_window_rejects_cross_root_capacity_stitching():
    module = _module()
    claims = [
        *[
            _coherence_claim(f"b0__sub0__unit{i}__claim1", f"b0-{i}", i)
            for i in range(7)
        ],
        *[
            _coherence_claim(f"b1__sub0__unit{i}__claim1", f"b1-{i}", 100 + i)
            for i in range(6)
        ],
    ]

    selected, metadata = module._select_coherent_claim_window(
        claims, minimum_unique_panels=13
    )

    assert selected == []
    assert metadata["feasible"] is False
    assert metadata["reason"] == "no_single_story_scope_has_required_visual_capacity"


def test_visual_capacity_rebalances_sixteen_raw_roi_slots_to_thirteen_unique_panels():
    module = _module()
    requirements = [
        {"passage_index": 0, "section": "hook", "required_visual_slots": 2},
        {"passage_index": 1, "section": "setup", "required_visual_slots": 4},
        {"passage_index": 2, "section": "conflict", "required_visual_slots": 4},
        {"passage_index": 3, "section": "twist", "required_visual_slots": 2},
        {"passage_index": 4, "section": "cta", "required_visual_slots": 4},
    ]
    claims = [
        {
            "claim_id": f"claim-{index}",
            "evidence_panel_slot_capacity": {f"panel-{index}": capacity},
        }
        for index, capacity in enumerate((2, 1, 1, 1, 2, 1, 1, 2, 1, 1, 1, 1, 1))
    ]

    rows, metadata = module._rebalance_visual_capacity_requirements(
        requirements,
        claims,
        max_words_per_visual_slot=9,
    )

    assert [row["required_visual_slots"] for row in rows] == [2, 3, 3, 2, 3]
    assert metadata["claim_backed_visual_slots"] == 16
    assert metadata["claim_backed_unique_panels"] == 13
    assert metadata["usable_nonrepeating_visual_slots"] == 13
    assert metadata["minimum_visual_slots_for_narration"] == 13
    assert metadata["target_visual_slots"] == 13
    assert metadata["target_word_count_min"] == 115
    assert metadata["target_word_count_max"] == 117
    assert metadata["target_word_count_goal"] == 115
    assert metadata["rebalanced"] is True
    assert metadata["feasible"] is True


def test_visual_capacity_uses_grounded_headroom_when_available():
    module = _module()
    requirements = [
        {"passage_index": index, "section": f"section-{index}", "required_visual_slots": 1}
        for index in range(5)
    ]
    claims = [
        {
            "claim_id": f"claim-{index}",
            "evidence_panel_slot_capacity": {f"panel-{index}": 1},
        }
        for index in range(27)
    ]
    rows, metadata = module._rebalance_visual_capacity_requirements(
        requirements, claims, max_words_per_visual_slot=9
    )
    assert metadata["rule"] == "visual_capacity_rebalance_v3"
    assert metadata["minimum_visual_slots_for_narration"] == 13
    assert metadata["preferred_visual_slots_for_narration"] == 15
    assert metadata["preferred_words_per_visual_slot"] == 8
    assert metadata["target_visual_slots"] == 15
    assert metadata["target_word_count_goal"] == 120
    assert [row["required_visual_slots"] for row in rows] == [2, 3, 4, 3, 3]
    assert metadata["hook_visual_slot_cap"] == 2


def test_claim_bundle_aware_rebalance_fits_atomic_combat_claims_without_repeats():
    module = _module()
    claims = [
        {
            "claim_id": "combat-setup",
            "text": "Characters prepare weapons before combat.",
            "min_source_order": 337,
            "max_source_order": 343,
            "evidence_panel_slot_capacity": {"a1": 1, "a2": 1, "a3": 1},
        },
        {
            "claim_id": "combat-hook",
            "text": "Sword clash attack with weapon effects.",
            "min_source_order": 344,
            "max_source_order": 346,
            "evidence_panel_slot_capacity": {"b1": 1, "b2": 1},
        },
        {
            "claim_id": "combat-turn-1",
            "text": "Sword combat continues.",
            "min_source_order": 347,
            "max_source_order": 347,
            "evidence_panel_slot_capacity": {"c1": 1},
        },
        {
            "claim_id": "combat-turn-2",
            "text": "Energy effects follow the clash.",
            "min_source_order": 349,
            "max_source_order": 349,
            "evidence_panel_slot_capacity": {"d1": 1},
        },
        {
            "claim_id": "combat-payoff",
            "text": "A sword clash and reaction close the exchange.",
            "min_source_order": 361,
            "max_source_order": 361,
            "evidence_panel_slot_capacity": {"e1": 1},
        },
    ]
    requirements = [
        {
            "passage_index": index,
            "section": section,
            "required_visual_slots": required,
        }
        for index, (section, required) in enumerate(
            zip(("hook", "setup", "conflict", "twist", "cta"), (2, 2, 2, 1, 1), strict=True)
        )
    ]

    rows, plan, metadata = module._claim_bundle_aware_capacity_plan(
        claims, requirements
    )

    assert metadata["applied"] is True
    assert metadata["original_allocation"] == [2, 2, 2, 1, 1]
    assert metadata["selected_allocation"] == [2, 3, 1, 1, 1]
    assert [row["required_visual_slots"] for row in rows] == [2, 3, 1, 1, 1]
    assert plan["feasible"] is True
    assert [row["claim_ids"] for row in plan["rows"]] == [
        ["combat-hook"],
        ["combat-setup"],
        ["combat-turn-1"],
        ["combat-turn-2"],
        ["combat-payoff"],
    ]
    assert len(
        {
            panel_id
            for row in plan["rows"]
            for panel_id in row["evidence_panel_ids"]
        }
    ) == 8


def test_capacity_safe_claim_plan_backtracks_when_early_overshoot_strands_late_passage():
    module = _module()
    claims = [
        {
            "claim_id": "claim-greedy-trap",
            "min_source_order": 10,
            "max_source_order": 10,
            "evidence_panel_slot_capacity": {"panel-h1": 1, "panel-cta-shared": 1},
        },
        {
            "claim_id": "claim-hook-exact",
            "min_source_order": 11,
            "max_source_order": 11,
            "evidence_panel_slot_capacity": {"panel-h1": 1, "panel-h2": 1},
        },
        {
            "claim_id": "claim-setup",
            "min_source_order": 20,
            "max_source_order": 20,
            "evidence_panel_slot_capacity": {"panel-s1": 1, "panel-s2": 1, "panel-s3": 1},
        },
        {
            "claim_id": "claim-conflict",
            "min_source_order": 30,
            "max_source_order": 30,
            "evidence_panel_slot_capacity": {"panel-c1": 1, "panel-c2": 1, "panel-c3": 1},
        },
        {
            "claim_id": "claim-twist",
            "min_source_order": 40,
            "max_source_order": 40,
            "evidence_panel_slot_capacity": {"panel-t1": 1, "panel-t2": 1},
        },
        {
            "claim_id": "claim-cta",
            "min_source_order": 50,
            "max_source_order": 50,
            "evidence_panel_slot_capacity": {"panel-cta-shared": 1, "panel-z1": 1, "panel-z2": 1},
        },
    ]
    requirements = [
        {"passage_index": 0, "section": "hook", "required_visual_slots": 2},
        {"passage_index": 1, "section": "setup", "required_visual_slots": 3},
        {"passage_index": 2, "section": "conflict", "required_visual_slots": 3},
        {"passage_index": 3, "section": "twist", "required_visual_slots": 2},
        {"passage_index": 4, "section": "cta", "required_visual_slots": 3},
    ]

    plan = module._capacity_safe_claim_plan(claims, requirements)

    assert plan["feasible"] is True
    assert plan["rule"] == "ordered_unique_panel_capacity_search_v4"
    assert [row["available_visual_slots"] for row in plan["rows"]] == [2, 3, 3, 2, 3]
    assert plan["rows"][0]["claim_ids"] == ["claim-hook-exact"]
    assert plan["rows"][-1]["claim_ids"] == ["claim-cta"]
    assert all(row["claim_ids"] and row["evidence_panel_ids"] for row in plan["rows"])


def test_capacity_word_budgets_sum_to_grounded_target_without_exceeding_slots():
    module = _module()
    plan = {
        "feasible": True,
        "rows": [
            {"passage_index": 0, "available_visual_slots": 2},
            {"passage_index": 1, "available_visual_slots": 3},
            {"passage_index": 2, "available_visual_slots": 3},
            {"passage_index": 3, "available_visual_slots": 2},
            {"passage_index": 4, "available_visual_slots": 3},
        ],
    }

    decorated = module._attach_capacity_word_budgets(
        plan, max_words_per_visual_slot=9, target_word_count=115
    )

    assert decorated["feasible"] is True
    assert decorated["word_budget_feasible"] is True
    assert sum(row["target_lexical_words"] for row in decorated["rows"]) == 115
    assert [row["max_lexical_words"] for row in decorated["rows"]] == [18, 27, 27, 18, 27]
    assert all(
        row["target_lexical_words"] <= row["max_lexical_words"]
        for row in decorated["rows"]
    )


def test_missing_capacity_plan_references_are_recovered_but_nonempty_substitutions_remain():
    module = _module()
    plan = {
        "rows": [
            {"claim_ids": ["claim-a"], "evidence_panel_ids": ["panel-a"]},
            {"claim_ids": ["claim-b"], "evidence_panel_ids": ["panel-b"]},
        ]
    }
    passages = [
        {"passage_id": "p1", "text": "Grounded prose.", "claim_ids": [], "evidence_panel_ids": []},
        {
            "passage_id": "p2",
            "text": "Provider substitution stays visible to the strict validator.",
            "claim_ids": ["claim-other"],
            "evidence_panel_ids": ["panel-other"],
        },
    ]

    recovered = module.recover_missing_capacity_plan_references(passages, plan)

    assert recovered[0]["claim_ids"] == ["claim-a"]
    assert recovered[0]["evidence_panel_ids"] == ["panel-a"]
    assert recovered[1]["claim_ids"] == ["claim-other"]
    assert recovered[1]["evidence_panel_ids"] == ["panel-other"]


def test_missing_capacity_plan_reference_recovery_preserves_nonempty_panel_alias():
    module = _module()
    plan = {"rows": [{"claim_ids": ["claim-a"], "evidence_panel_ids": ["panel-a"]}]}
    passages = [
        {
            "passage_id": "p1",
            "text": "Grounded prose.",
            "claim_ids": None,
            "panel_ids": ["panel-provider"],
        }
    ]

    recovered = module.recover_missing_capacity_plan_references(passages, plan)

    assert recovered[0]["claim_ids"] == ["claim-a"]
    assert "evidence_panel_ids" not in recovered[0]
    assert recovered[0]["panel_ids"] == ["panel-provider"]


def test_capacity_safe_plan_is_mandatory_for_repaired_passage_evidence_and_words():
    module = _module()
    plan = {
        "feasible": True,
        "rows": [
            {
                "claim_ids": ["claim-a"],
                "evidence_panel_ids": ["panel-a"],
                "max_lexical_words": 9,
            }
        ],
    }
    valid = [
        {
            "text": "Grounded words stay with this panel.",
            "claim_ids": ["claim-a"],
            "evidence_panel_ids": ["panel-a"],
        }
    ]
    module.validate_repaired_capacity_safe_claim_plan(valid, plan)

    with pytest.raises(module.VisualNarrativeRepairError, match="diverges from capacity plan"):
        module.validate_repaired_capacity_safe_claim_plan(
            [dict(valid[0], evidence_panel_ids=["panel-b"])], plan
        )
    with pytest.raises(module.VisualNarrativeRepairError, match="exceeds capacity word budget"):
        module.validate_repaired_capacity_safe_claim_plan(
            [dict(valid[0], text="one two three four five six seven eight nine ten")],
            plan,
        )


def test_capacity_safe_claim_plan_groups_unique_panels_in_source_order():
    module = _module()
    claims = [
        {"claim_id": "claim-a", "min_source_order": 10, "max_source_order": 10,
         "evidence_panel_slot_capacity": {"panel-a1": 1, "panel-a2": 1}},
        {"claim_id": "claim-a-duplicate", "min_source_order": 11, "max_source_order": 11,
         "evidence_panel_slot_capacity": {"panel-a1": 1, "panel-a2": 1}},
        {"claim_id": "claim-b", "min_source_order": 20, "max_source_order": 20,
         "evidence_panel_slot_capacity": {"panel-b": 1}},
        {"claim_id": "claim-c", "min_source_order": 30, "max_source_order": 30,
         "evidence_panel_slot_capacity": {"panel-c": 1}},
        {"claim_id": "claim-d", "min_source_order": 40, "max_source_order": 40,
         "evidence_panel_slot_capacity": {"panel-d1": 1, "panel-d2": 1}},
    ]
    requirements = [
        {"passage_index": 0, "section": "hook", "required_visual_slots": 2},
        {"passage_index": 1, "section": "setup", "required_visual_slots": 2},
        {"passage_index": 2, "section": "twist", "required_visual_slots": 2},
    ]

    plan = module._capacity_safe_claim_plan(claims, requirements)

    assert plan["feasible"] is True
    assert plan["preserve_passage_count"] is True
    assert [row["available_visual_slots"] for row in plan["rows"]] == [2, 2, 2]
    assert len({panel for row in plan["rows"] for panel in row["evidence_panel_ids"]}) == 6
    assert all(row["unique_panel_shortfall"] == 0 for row in plan["rows"])


def test_feasible_render_plan_is_deterministic_and_shared_by_repair_payload():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-b", order=20, blank=0.2),
            _entry(module, panel_id="panel-a", order=10, blank=0.0),
        ),
        model_identity_hash="model-hash",
    )
    first = module.FeasibleRenderPlan.from_ledger(ledger)
    second = module.FeasibleRenderPlan.from_ledger(module.FeasibleVisualLedger.from_dict(ledger.as_dict()))

    assert first.plan_hash == second.plan_hash
    assert first.panel_ids == ("panel-a", "panel-b")
    assert first.as_dict()["contract_version"] == "feasible-render-plan-v1"
    payload = module.build_repair_payload(
        narration={"passages": []},
        story_map={"beats": []},
        ledger=ledger,
        section_to_beats={},
    )
    assert payload["feasible_render_plan"]["plan_hash"] == first.plan_hash


def test_feasible_render_plan_fails_closed_on_stale_lineage():
    module = _module()
    ledger = module.FeasibleVisualLedger(entries=(_entry(module),), model_identity_hash="model-hash")
    plan = module.FeasibleRenderPlan.from_ledger(ledger)
    with pytest.raises(module.VisualNarrativeRepairError):
        plan.validate_current_panel(
            "panel-safe",
            source_asset_id="wrong-asset",
            source_checksum="source",
            evidence_hash="e" * 64,
        )



def test_repair_claim_id_transport_drift_canonicalizes_to_unique_trusted_id():
    module = _module()
    value = {
        "claims": [
            {
                "claim_id": "b0__sub2__sub0__claim1",
                "evidence_panel_ids": ["panel-safe"],
            }
        ],
        "passages": [
            {
                "passage_id": "p1",
                "claim_ids": ["b0__sub2__sub0__claim1"],
                "evidence_panel_ids": ["panel-safe"],
            }
        ],
    }

    repaired = module.canonicalize_repair_claim_ids(
        value,
        allowed_claim_ids={"b0__sub2__sub0__claim_1"},
    )

    assert repaired["claims"][0]["claim_id"] == "b0__sub2__sub0__claim_1"
    assert repaired["passages"][0]["claim_ids"] == ["b0__sub2__sub0__claim_1"]


def test_repair_claim_id_canonicalization_rejects_untrusted_claim():
    module = _module()
    value = {
        "claims": [
            {
                "claim_id": "b0__sub2__sub0__claim9",
                "evidence_panel_ids": ["panel-safe"],
            }
        ],
        "passages": [
            {
                "passage_id": "p1",
                "claim_ids": ["b0__sub2__sub0__claim9"],
                "evidence_panel_ids": ["panel-safe"],
            }
        ],
    }

    with pytest.raises(module.VisualNarrativeRepairError) as caught:
        module.canonicalize_repair_claim_ids(
            value,
            allowed_claim_ids={"b0__sub2__sub0__claim_1"},
        )

    assert caught.value.code == "visual.narrative_repair_ungrounded"


def test_repair_payload_exposes_only_claims_with_original_feasible_evidence():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(_entry(module, panel_id="panel-safe", beat="beat-1", order=10),),
        model_identity_hash="model-hash",
    )
    payload = module.build_repair_payload(
        narration={"passages": []},
        story_map={
            "beats": [{"beat_id": "beat-1", "panel_ids": ["panel-safe", "panel-unsafe"]}],
            "claims": [
                {
                    "claim_id": "claim-safe",
                    "text": "Visible fact.",
                    "qualification": "Directly visible.",
                    "panel_ids": ["panel-safe", "panel-unsafe"],
                },
                {
                    "claim_id": "claim-unavailable",
                    "text": "Unavailable fact.",
                    "qualification": "Only unsafe evidence.",
                    "panel_ids": ["panel-unsafe"],
                },
            ],
        },
        ledger=ledger,
        section_to_beats={"hook": ("beat-1",)},
    )

    assert payload["feasible_claim_ids"] == ["claim-safe"]
    assert payload["feasible_claims"] == [
        {
            "claim_id": "claim-safe",
            "text": "Visible fact.",
            "qualification": "Directly visible.",
            "evidence_panel_ids": ["panel-safe"],
            "evidence_source_orders": [10],
            "min_source_order": 10,
            "max_source_order": 10,
            "evidence_panel_slot_capacity": {"panel-safe": 1},
            "visual_slot_capacity": 1,
            "unique_panel_count": 1,
        }
    ]


def test_feasible_claim_capacity_caps_multi_roi_panel_at_two_shots():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            module.FeasibleVisualRecord(
                panel_region_id="region-many",
                panel_id="panel-many",
                source_asset_id="asset-many",
                source_order=10,
                eligible_sections=("hook",),
                eligible_beats=("beat-1",),
                resolution_state="NATIVE",
                feasible_rois=(
                    {"kind": "primary", "roi_label": "a", "crop_box": [0, 0, 100, 100], "telemetry": {}},
                    {"kind": "secondary", "roi_label": "b", "crop_box": [10, 0, 100, 100], "telemetry": {}},
                    {"kind": "detail", "roi_label": "c", "crop_box": [20, 0, 100, 100], "telemetry": {}},
                ),
                visual_strengths={"edge_connected_blank_fraction": 0.0},
                evidence_hash="e" * 64,
                detector_version="detector-v1",
                mask_sha256="m" * 64,
                panel_size=(100, 100),
            ),
        ),
        model_identity_hash="model-hash",
    )
    rows = module.feasible_story_claims(
        {"claims": [{"claim_id": "claim-many", "panel_ids": ["panel-many"]}]},
        ledger,
    )
    assert rows[0]["evidence_panel_slot_capacity"] == {"panel-many": 2}
    assert rows[0]["visual_slot_capacity"] == 2


def test_repaired_claim_cannot_rebind_to_feasible_panel_outside_story_lineage():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-original", beat="beat-1", order=10),
            _entry(module, panel_id="panel-other", beat="beat-1", order=11),
        ),
        model_identity_hash="model-hash",
    )

    with pytest.raises(module.VisualNarrativeRepairError) as caught:
        module.validate_repaired_panel_references(
            {
                "claims": [
                    {"claim_id": "claim-1", "evidence_panel_ids": ["panel-other"]}
                ],
                "passages": [
                    {
                        "passage_id": "p1",
                        "claim_ids": ["claim-1"],
                        "evidence_panel_ids": ["panel-other"],
                    }
                ],
            },
            ledger=ledger,
            allowed_claim_ids={"claim-1"},
            allowed_claim_panel_ids={"claim-1": {"panel-original"}},
        )

    assert caught.value.code == "visual.narrative_repair_ungrounded"


def test_same_beat_remap_keeps_claim_evidence_inside_story_lineage():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-current", beat="beat-1", order=10, blank=0.20),
            _entry(module, panel_id="panel-lowest", beat="beat-1", order=11, blank=0.01),
            _entry(module, panel_id="panel-allowed", beat="beat-1", order=12, blank=0.05),
        ),
        model_identity_hash="model-hash",
    )

    repaired, provenance = module.remap_same_beat_panel_citations(
        {
            "claims": [
                {"claim_id": "claim-1", "evidence_panel_ids": ["panel-current"]}
            ],
            "passages": [
                {
                    "passage_id": "p1",
                    "claim_ids": ["claim-1"],
                    "evidence_panel_ids": ["panel-current"],
                }
            ],
        },
        ledger=ledger,
        section_to_beats={"hook": ("beat-1",)},
        allowed_claim_panel_ids={
            "claim-1": {"panel-current", "panel-allowed"}
        },
    )

    assert repaired["passages"][0]["evidence_panel_ids"] == ["panel-allowed"]
    assert provenance[0]["to_panel_id"] == "panel-allowed"


def test_feasible_ledger_rejects_source_sliver_before_upscale_framing(monkeypatch):
    module = _module()
    calls = []

    def fake_feasible(*_args, **_kwargs):
        calls.append(True)
        return True, _telemetry(module)

    monkeypatch.setattr(module.framing_analysis, "candidate_is_feasible", fake_feasible)
    monkeypatch.setattr(
        module.editorial_visual_planner.visual_scoring,
        "require_reference_ready_visual_evidence",
        lambda value, **_kwargs: value,
    )
    candidate = SimpleNamespace(
        panel_region_id="region-sliver",
        panel_id="panel-sliver",
        source_asset_id="asset-1",
        source_order=414,
        panel_size=(1666, 1670),
        border_mask=SimpleNamespace(
            mask_sha256="m" * 64,
            detector_version="color-agnostic-border-v1",
        ),
        visual_evidence=SimpleNamespace(),
        evidence_hash="e" * 64,
        source_checksum="s" * 64,
        source_upscale_manifest={
            "resolution_state": "UPSCALED",
            "source_panel_bounds": [0, 0, 900, 284],
        },
        eligible_sections=("cta",),
        eligible_beats=("beat-final",),
        roi_alternatives=(
            SimpleNamespace(
                kind="primary",
                roi_label="primary",
                crop_box=(0, 0, 1666, 1670),
                focus=(0.5, 0.5, 0.5, 0.5),
                edge_blank_fraction=0.0,
            ),
        ),
        panel_candidate=SimpleNamespace(source_family=""),
    )

    ledger = module.build_feasible_visual_ledger(
        [candidate],
        profile=SimpleNamespace(final_width=1080, final_height=1920),
        model_identity_hash="model-hash",
    )

    assert ledger.entries == ()
    assert calls == []



def test_repaired_passage_rejects_feasible_panel_outside_its_claim_lineage():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(
            _entry(module, panel_id="panel-a", beat="beat-a", order=10),
            _entry(module, panel_id="panel-b", beat="beat-b", order=20),
        ),
        model_identity_hash="model-hash",
    )

    with pytest.raises(module.VisualNarrativeRepairError) as exc:
        module.validate_repaired_panel_references(
            {
                "claims": [
                    {"claim_id": "claim-a", "evidence_panel_ids": ["panel-a"]}
                ],
                "passages": [
                    {
                        "passage_id": "p1",
                        "text": "Grounded sentence.",
                        "claim_ids": ["claim-a"],
                        "evidence_panel_ids": ["panel-a", "panel-b"],
                    }
                ],
            },
            ledger=ledger,
            allowed_claim_ids={"claim-a"},
            allowed_claim_panel_ids={"claim-a": {"panel-a"}},
        )

    assert exc.value.code == "visual.narrative_repair_ungrounded"
    assert "outside its claim lineage" in str(exc.value)

def test_repair_scope_includes_visual_capacity_shortfall():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(_entry(module, panel_id="panel-a", beat="beat-a", order=10),),
        model_identity_hash="model-hash",
    )
    narration = {
        "estimated_duration_s": 9.0,
        "passages": [
            {
                "passage_id": "p1",
                "text": "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty.",
                "claim_ids": ["claim-a"],
                "evidence_panel_ids": ["panel-a"],
            }
        ],
        "evidence_graph": {
            "claims": [
                {"claim_id": "claim-a", "evidence_panel_ids": ["panel-a"]}
            ]
        },
    }

    assert module.narration_sections_with_visual_capacity_shortfall(
        narration, ledger, {"hook": ("beat-a",)}
    ) == ("hook",)
    assert module.repair_scope_sections(
        narration, ledger, {"hook": ("beat-a",)}
    ) == ("hook",)


def test_repaired_visual_capacity_accepts_two_panels_with_three_roi_slots():
    module = _module()
    first = _entry(module, panel_id="panel-a", beat="beat-a", order=10)
    second = _entry(module, panel_id="panel-b", beat="beat-a", order=11)
    first = module.FeasibleVisualRecord(
        **{
            **first.__dict__,
            "feasible_rois": first.feasible_rois + (dict(first.feasible_rois[0], roi_label="alternate"),),
        }
    )
    ledger = module.FeasibleVisualLedger(
        entries=(first, second), model_identity_hash="model-hash"
    )
    passages = [
        {
            "passage_id": "p1",
            "text": "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty.",
            "claim_ids": ["claim-a", "claim-b"],
            "evidence_panel_ids": ["panel-a", "panel-b"],
        }
    ]

    module.validate_repaired_visual_capacity(passages, ledger)


def test_repaired_visual_capacity_rejects_single_long_panel():
    module = _module()
    ledger = module.FeasibleVisualLedger(
        entries=(_entry(module, panel_id="panel-a", beat="beat-a", order=10),),
        model_identity_hash="model-hash",
    )
    passages = [
        {
            "passage_id": "p1",
            "text": "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty.",
            "claim_ids": ["claim-a"],
            "evidence_panel_ids": ["panel-a"],
        }
    ]

    with pytest.raises(module.VisualNarrativeRepairError) as exc:
        module.validate_repaired_visual_capacity(passages, ledger)

    assert exc.value.code == "visual.narrative_repair_ungrounded"


def test_hook_quality_rejects_flat_panel_description():
    module = _module()
    passages = [{"text": "We see a man standing by the door.", "claim_ids": ["c1"]}]
    claims = [{"claim_id": "c1", "text": "He discovers a hidden door."}]
    plan = {"rows": [{"hook_priority_score": 1}]}
    with pytest.raises(module.VisualNarrativeRepairError) as exc:
        module.validate_repaired_hook_quality(passages, claims, plan)
    assert exc.value.code == "cloud.narrative_flat_recap"



def test_hook_quality_rejects_majority_visual_description_prose():
    module = _module()
    passages = [
        {"text": "A close-up shows a worried face.", "claim_ids": ["c1"]},
        {"text": "The girl appears beside a white cat.", "claim_ids": ["c2"]},
        {"text": "The sequence depicts two figures with swords.", "claim_ids": ["c3"]},
        {"text": "A blue mark changes the situation.", "claim_ids": ["c4"]},
        {"text": "That choice leaves the group exposed.", "claim_ids": ["c5"]},
    ]
    claims = [
        {"claim_id": f"c{index}", "text": "Grounded fact."}
        for index in range(1, 6)
    ]
    plan = {"rows": [{"hook_priority_score": 0}]}
    with pytest.raises(module.VisualNarrativeRepairError) as exc:
        module.validate_repaired_hook_quality(passages, claims, plan)
    assert exc.value.code == "cloud.narrative_flat_recap"


def test_hook_quality_rejects_disconnected_observation_sequence():
    module = _module()
    passages = [
        {"text": "The hidden trap changes the room.", "claim_ids": ["c1"]},
        {"text": "She reaches the desk.", "claim_ids": ["c2"]},
        {"text": "Weapons wait nearby.", "claim_ids": ["c3"]},
        {"text": "The sword fight starts.", "claim_ids": ["c4"]},
        {"text": "The group leaves the building.", "claim_ids": ["c5"]},
    ]
    claims = [{"claim_id": f"c{i}", "text": "Grounded fact."} for i in range(1, 6)]
    plan = {"rows": [{"hook_priority_score": 0}]}
    with pytest.raises(module.VisualNarrativeRepairError) as exc:
        module.validate_repaired_hook_quality(passages, claims, plan)
    assert exc.value.code == "cloud.narrative_flat_recap"


def test_hook_quality_rejects_ignoring_grounded_curiosity_claim():
    module = _module()
    passages = [{"text": "He walks toward the room.", "claim_ids": ["plain"]}]
    claims = [
        {"claim_id": "plain", "text": "He walks toward the room."},
        {"claim_id": "turn", "text": "He discovers a hidden trap."},
    ]
    plan = {"rows": [{"hook_priority_score": 2}]}
    with pytest.raises(module.VisualNarrativeRepairError) as exc:
        module.validate_repaired_hook_quality(passages, claims, plan)
    assert exc.value.code == "cloud.narrative_hook_weak"


def test_hook_quality_accepts_grounded_curiosity_opening():
    module = _module()
    passages = [{"text": "The hidden trap changes what this room means.", "claim_ids": ["turn"]}]
    claims = [{"claim_id": "turn", "text": "He discovers a hidden trap."}]
    plan = {"rows": [{"hook_priority_score": 2}]}
    result = module.validate_repaired_hook_quality(passages, claims, plan)
    assert result["status"] == "pass"
    assert result["hook_claim_score"] > 0


def test_capacity_plan_uses_strongest_grounded_hook_then_resets_chronology():
    module = _module()
    claims = [
        {"claim_id": "c10", "text": "He enters the hall.", "min_source_order": 10,
         "max_source_order": 10, "evidence_panel_slot_capacity": {"p10": 1}},
        {"claim_id": "c20", "text": "He meets the guard.", "min_source_order": 20,
         "max_source_order": 20, "evidence_panel_slot_capacity": {"p20": 1}},
        {"claim_id": "c30", "text": "He finds the key.", "min_source_order": 30,
         "max_source_order": 30, "evidence_panel_slot_capacity": {"p30": 1}},
        {"claim_id": "c40", "text": "The hidden door reveals an unexpected threat.",
         "qualification": "visible danger beyond the door", "min_source_order": 40,
         "max_source_order": 40, "evidence_panel_slot_capacity": {"p40": 1}},
        {"claim_id": "c50", "text": "He reacts to the threat.", "min_source_order": 50,
         "max_source_order": 50, "evidence_panel_slot_capacity": {"p50": 1}},
        {"claim_id": "c60", "text": "He escapes the room.", "min_source_order": 60,
         "max_source_order": 60, "evidence_panel_slot_capacity": {"p60": 1}},
    ]
    requirements = [
        {"passage_index": i, "section": name, "required_visual_slots": 1}
        for i, name in enumerate(("hook", "setup", "conflict", "twist", "cta"))
    ]
    plan = module._capacity_safe_claim_plan(claims, requirements)
    assert plan["feasible"] is True
    assert plan["rows"][0]["claim_ids"] == ["c40"]
    later_orders = [row["claim_min_source_orders"][0] for row in plan["rows"][1:]]
    assert later_orders == sorted(later_orders)
    assert later_orders[0] < 40


def test_capacity_plan_reference_match_is_exact_and_ordered():
    module = _module()
    plan = {
        "rows": [
            {
                "claim_ids": ["claim-a", "claim-b"],
                "evidence_panel_ids": ["panel-a", "panel-b"],
            }
        ]
    }
    passages = [
        {
            "claim_ids": ["claim-a", "claim-b"],
            "evidence_panel_ids": ["panel-a", "panel-b"],
        }
    ]
    assert module.repaired_references_match_capacity_plan(passages, plan) is True
    assert module.repaired_references_match_capacity_plan(
        [dict(passages[0], evidence_panel_ids=["panel-b", "panel-a"])], plan
    ) is False
    assert module.repaired_references_match_capacity_plan(
        [dict(passages[0], claim_ids=["claim-b", "claim-a"])], plan
    ) is False



def test_hook_quality_accepts_truthful_temporal_story_bridges():
    module = _module()
    passages = [
        {"text": "A hidden risk comes into focus without an easy answer.", "claim_ids": ["c0"]},
        {"text": "Earlier, the group settles into a quieter routine.", "claim_ids": ["c1"]},
        {"text": "Later, the mood shifts as surprise interrupts that calm.", "claim_ids": ["c2"]},
        {"text": "Pressure builds around a choice nobody can ignore.", "claim_ids": ["c3"]},
        {"text": "The conflict returns and leaves the next step uncertain.", "claim_ids": ["c4"]},
    ]
    claims = [{"claim_id": f"c{i}", "text": "grounded event"} for i in range(5)]
    audit = module.validate_repaired_hook_quality(passages, claims, {"rows": [{"hook_priority_score": 0}]})
    assert audit["story_bridge_count"] >= 2
    assert audit["flat_recap_detected"] is False


def test_hook_quality_rejects_stiff_bureaucratic_spoken_prose():
    module = _module()
    passages = [
        {"text": "Blades collide as the duel turns decisive.", "claim_ids": ["c0"]},
        {"text": "They prepared during the course of earlier drills before the confrontation phase.", "claim_ids": ["c1"]},
        {"text": "Sparks fly when the blades meet.", "claim_ids": ["c2"]},
        {"text": "Energy flares as the exchange continues.", "claim_ids": ["c3"]},
    ]
    claims = [
        {"claim_id": f"c{index}", "text": "combat clash", "evidence_panel_ids": [f"p{index}"]}
        for index in range(4)
    ]
    plan = {"rows": [{"hook_priority_score": 4}]}
    with pytest.raises(module.VisualNarrativeRepairError) as caught:
        module.validate_repaired_hook_quality(passages, claims, plan)
    assert caught.value.code == "cloud.narrative_style_stiff"


def test_hook_quality_accepts_direct_conversational_spoken_prose():
    module = _module()
    passages = [
        {"text": "Blades collide as the duel turns decisive.", "claim_ids": ["c0"]},
        {"text": "Earlier they readied their weapons before moving in.", "claim_ids": ["c1"]},
        {"text": "Sparks fly when the blades meet.", "claim_ids": ["c2"]},
        {"text": "Energy flares as the exchange continues.", "claim_ids": ["c3"]},
    ]
    claims = [
        {"claim_id": f"c{index}", "text": "combat clash", "evidence_panel_ids": [f"p{index}"]}
        for index in range(4)
    ]
    plan = {"rows": [{"hook_priority_score": 4}]}
    result = module.validate_repaired_hook_quality(passages, claims, plan)
    assert result["stiff_spoken_passage_count"] == 0


def test_feasible_ledger_prunes_extreme_crop_without_editorial_context(monkeypatch):
    from dataclasses import replace

    module = _module()
    monkeypatch.setattr(
        module.editorial_visual_planner.visual_scoring,
        "require_reference_ready_visual_evidence",
        lambda value, **_kwargs: value,
    )

    def fake_feasible(crop_box, *_args, **_kwargs):
        zoom = 3.18 if tuple(crop_box) == (370, 807, 710, 1411) else 1.8
        return True, replace(_telemetry(module, tuple(crop_box)), base_zoom=zoom)

    monkeypatch.setattr(module.framing_analysis, "candidate_is_feasible", fake_feasible)
    candidate = SimpleNamespace(
        panel_region_id="region-editorial",
        panel_id="panel-editorial",
        source_asset_id="asset-1",
        source_order=30,
        panel_size=(1080, 2521),
        border_mask=SimpleNamespace(
            mask_sha256="m" * 64,
            detector_version="color-agnostic-border-v1",
        ),
        visual_evidence=SimpleNamespace(protected_regions=()),
        panel_candidate=SimpleNamespace(
            source_family="",
            features=module.editorial_visual_planner.visual_scoring.VisualFeatures(
                action_pose=0.8,
                dramatic_composition=0.8,
            ),
        ),
        evidence_hash="e" * 64,
        source_upscale_manifest={"resolution_state": "UPSCALED"},
        eligible_sections=("hook",),
        eligible_beats=("action",),
        roi_alternatives=(
            SimpleNamespace(
                kind="aggressive_crop",
                roi_label="extreme",
                crop_box=(370, 807, 710, 1411),
                focus=(0.5, 0.44, 0.5, 0.44),
            ),
            SimpleNamespace(
                kind="tighter_crop",
                roi_label="readable",
                crop_box=(150, 400, 930, 1787),
                focus=(0.5, 0.44, 0.5, 0.44),
            ),
        ),
    )
    ledger = module.build_feasible_visual_ledger(
        [candidate],
        profile=SimpleNamespace(
            final_width=1080,
            final_height=1920,
            framing_blank_target_fraction=0.03,
        ),
        model_identity_hash="model-hash",
        allow_source_resolution_warning=True,
    )
    assert len(ledger.entries) == 1
    assert [roi["roi_label"] for roi in ledger.entries[0].feasible_rois] == ["readable"]
    assert ledger.entries[0].feasible_rois[0]["telemetry"]["editorial_crop_quality"][
        "unjustified_detail_crop"
    ] is False


def test_feasible_ledger_separates_exact_beat_lineage_from_editorial_roles(monkeypatch):
    module = _module()

    monkeypatch.setattr(
        module.framing_analysis,
        "candidate_is_feasible",
        lambda *_args, **_kwargs: (True, _telemetry(module)),
    )
    monkeypatch.setattr(
        module.editorial_visual_planner.visual_scoring,
        "require_reference_ready_visual_evidence",
        lambda value, **_kwargs: value,
    )
    candidate = SimpleNamespace(
        panel_region_id="region-beat-a",
        panel_id="panel-beat-a",
        source_asset_id="asset-a",
        source_order=10,
        panel_size=(1080, 1920),
        border_mask=SimpleNamespace(
            mask_sha256="m" * 64,
            detector_version="color-agnostic-border-v1",
        ),
        visual_evidence=SimpleNamespace(protected_regions=()),
        panel_candidate=SimpleNamespace(source_family="", features=None),
        evidence_hash="e" * 64,
        source_checksum="s" * 64,
        source_upscale_manifest={"resolution_state": "UPSCALED"},
        eligible_sections=("beat-a",),
        eligible_beats=("beat-a",),
        roi_alternatives=(
            SimpleNamespace(
                kind="primary",
                roi_label="primary",
                crop_box=(0, 0, 1080, 1920),
                focus=(0.5, 0.5, 0.5, 0.5),
            ),
        ),
    )

    ledger = module.build_feasible_visual_ledger(
        [candidate],
        profile=SimpleNamespace(
            final_width=1080,
            final_height=1920,
            framing_blank_target_fraction=0.03,
        ),
        model_identity_hash="model-hash",
        editorial_sections=("hook", "setup", "conflict", "twist", "cta"),
    )

    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert entry.eligible_beats == ("beat-a",)
    assert entry.eligible_sections == ("conflict", "cta", "hook", "setup", "twist")
    assert all(roi["editorial_safe_beats"] == ["beat-a"] for roi in entry.feasible_rois)


def test_capacity_plan_rejects_panels_unsafe_for_target_editorial_section():
    module = _module()
    claims = [
        {
            "claim_id": "claim-conflict",
            "min_source_order": 10,
            "max_source_order": 10,
            "evidence_panel_slot_capacity": {"panel-conflict": 1},
            "evidence_panel_slot_capacity_by_section": {
                "conflict": {"panel-conflict": 1}
            },
        },
        {
            "claim_id": "claim-setup",
            "min_source_order": 20,
            "max_source_order": 20,
            "evidence_panel_slot_capacity": {"panel-setup": 1},
            "evidence_panel_slot_capacity_by_section": {
                "setup": {"panel-setup": 1}
            },
        },
        {
            "claim_id": "claim-conflict-late",
            "min_source_order": 30,
            "max_source_order": 30,
            "evidence_panel_slot_capacity": {"panel-conflict-late": 1},
            "evidence_panel_slot_capacity_by_section": {
                "conflict": {"panel-conflict-late": 1}
            },
        },
    ]
    requirements = [
        {"passage_index": 0, "section": "setup", "required_visual_slots": 1},
        {"passage_index": 1, "section": "conflict", "required_visual_slots": 1},
    ]

    plan = module._capacity_safe_claim_plan(claims, requirements)

    assert plan["feasible"] is True
    assert plan["section_capacity_aware"] is True
    assert plan["rows"][0]["evidence_panel_ids"] == ["panel-setup"]
    assert plan["rows"][1]["evidence_panel_ids"] in (["panel-conflict"], ["panel-conflict-late"])
    assert "panel-conflict" not in plan["rows"][0]["evidence_panel_ids"]


def test_capacity_plan_fails_closed_when_only_wrong_role_panels_exist():
    module = _module()
    claims = [
        {
            "claim_id": "claim-conflict",
            "min_source_order": 10,
            "max_source_order": 10,
            "evidence_panel_slot_capacity": {"panel-conflict": 1},
            "evidence_panel_slot_capacity_by_section": {
                "conflict": {"panel-conflict": 1}
            },
        }
    ]

    plan = module._capacity_safe_claim_plan(
        claims,
        [{"passage_index": 0, "section": "setup", "required_visual_slots": 1}],
    )

    assert plan["feasible"] is False
    assert plan["rows"][0]["evidence_panel_ids"] == []
