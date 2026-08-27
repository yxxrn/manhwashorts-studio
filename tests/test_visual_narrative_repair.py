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


def test_feasible_ledger_rejects_roi_planner_would_reject_for_edge_blank(monkeypatch):
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
    assert ledger.entries == ()


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
        "estimated_duration_s": 53.48,
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
        }
    ]


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
