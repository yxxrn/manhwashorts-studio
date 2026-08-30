from app.services import visual_narrative_repair as vnr


def _entry(panel_id: str, beat_id: str, order: int) -> vnr.FeasibleVisualRecord:
    return vnr.FeasibleVisualRecord(
        panel_region_id=f"region-{panel_id}", panel_id=panel_id,
        source_asset_id=f"asset-{order}", source_order=order,
        eligible_sections=vnr.REPAIR_EDITORIAL_SECTIONS, eligible_beats=(beat_id,),
        resolution_state="NATIVE",
        feasible_rois=({
            "kind": "primary", "roi_label": "safe",
            "crop_box": [0, 0, 1080, 1920],
            "editorial_safe_sections": list(vnr.REPAIR_EDITORIAL_SECTIONS),
            "editorial_safe_beats": [beat_id],
            "telemetry": {
                "balloon_mask_intersection_ratio": 0.0,
                "protected_retained_fraction": 1.0,
                "editorial_crop_quality": {},
            },
        },),
        visual_strengths={}, evidence_hash=f"{order:064x}"[-64:],
        detector_version="test-detector", mask_sha256=f"{order + 1000:064x}"[-64:],
        panel_size=(1080, 1920), source_asset_checksum=f"{order + 2000:064x}"[-64:],
    )


def test_repair_payload_prefers_standard_duration_before_deeper_adaptive_scope():
    scopes = (["b1__sub0"] * 3) + (["b1__sub1"] * 2) + (["b1__sub2"] * 8)
    entries = []
    beats = []
    claims = []
    for index, scope in enumerate(scopes, start=1):
        panel_id = f"panel-{index:02d}"
        beat_id = f"beat-{index:02d}"
        entries.append(_entry(panel_id, beat_id, index * 10))
        beats.append({"beat_id": beat_id, "panel_ids": [panel_id]})
        claims.append({
            "claim_id": f"{scope}__claim{index}",
            "text": f"Grounded story fact {index}.",
            "panel_ids": [panel_id],
        })
    ledger = vnr.FeasibleVisualLedger(entries=tuple(entries), model_identity_hash="model-hash")
    all_beats = tuple(row["beat_id"] for row in beats)
    payload = vnr.build_repair_payload(
        narration={"passages": []},
        story_map={"beats": beats, "claims": claims},
        ledger=ledger,
        section_to_beats=dict.fromkeys(vnr.REPAIR_EDITORIAL_SECTIONS, all_beats),
    )
    contract = payload["duration_policy_contract"]
    window = payload["coherence_window"]
    assert contract["version"] == vnr.REPAIR_DURATION_POLICY_STANDARD
    assert contract["adaptive"] is False
    assert contract["selected_unique_panel_count"] == 13
    assert contract["target_word_min"] == vnr.REPAIR_TARGET_WORD_MIN
    assert window["selected_scope_prefix"] == "b1"
    assert window["selected_connected_scope_chain"] == ["b1__sub0", "b1__sub1", "b1__sub2"]
    assert len(payload["feasible_claims"]) == 13
