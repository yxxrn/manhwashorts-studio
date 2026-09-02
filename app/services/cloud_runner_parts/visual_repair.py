"""Visual Repair methods extracted from cloud_multimodal."""

# ruff: noqa: F821 -- runtime globals are refreshed from the compatibility facade.
from __future__ import annotations

from .runtime import runtime_bound

_RUNTIME_NAMES = (
    'CloudStageError',
    'Mapping',
    'NarrationResult',
    '_canonicalize_visual_repair_ending',
    '_hash',
    '_micro_compact_rewrites',
    '_micro_expand_rewrites',
    '_narration_repair_contract_bounds',
    '_narration_result_is_usable',
    '_visual_narrative_repair_analyzer_metadata',
    '_visual_narrative_repair_error_metadata',
    '_visual_narrative_repair_failure_metadata',
    '_visual_narrative_repair_retry_feedback',
    'analyzer_contract',
    'asdict',
    'derive_display_words',
    'editorial_qc',
    'narrative_identity',
    'quality',
    'replace',
    'script',
    'sys',
    'visual_narrative_repair',
)
_bound = runtime_bound(_RUNTIME_NAMES)


class VisualNarrativeRepairMixin:
    @_bound
    def _run_visual_repair_text_only_duration_repair(
        self,
        *,
        visual_repair_prompt: tuple[str, str, str],
        source: Mapping[str, Any],
        observations: Sequence[Mapping[str, Any]],
        structural: Mapping[str, Any],
        story_map: StoryMapResult,
        visual: VisualStageResult,
        candidate: NarrationResult,
        capacity_plan: Mapping[str, Any],
        selected_story_context: Sequence[Mapping[str, Any]] = (),
        failure_codes: Sequence[str] = ("cloud.narrative_duration_out_of_range",),
    ) -> NarrationResult:
        """Repair only visual-repair prose after its evidence scope is locked."""

        rows = capacity_plan.get("rows") if isinstance(capacity_plan, Mapping) else None
        if not isinstance(rows, list) or len(rows) != len(candidate.passages):
            raise CloudStageError("visual.narrative_repair_ungrounded", reviewable=True)
        raw_passage_word_targets = {
            str(passage.get("passage_id", "")): int(row.get("target_lexical_words", 0))
            for passage, row in zip(candidate.passages, rows, strict=True)
            if isinstance(passage, Mapping) and isinstance(row, Mapping)
        }
        passage_word_budgets = {
            str(passage.get("passage_id", "")): int(row.get("max_lexical_words", 0))
            for passage, row in zip(candidate.passages, rows, strict=True)
            if isinstance(passage, Mapping) and isinstance(row, Mapping)
        }
        passage_word_targets = {
            passage_id: min(target, max(1, passage_word_budgets.get(passage_id, 0) - 2))
            for passage_id, target in raw_passage_word_targets.items()
        }
        duration_policy = source.get("duration_policy_contract")
        if isinstance(duration_policy, Mapping):
            bounds = _narration_repair_contract_bounds(duration_policy)
            target_floor = int(bounds["target_word_min"])
            deficit = max(0, target_floor - sum(passage_word_targets.values()))
            if deficit:
                for passage_id in raw_passage_word_targets:
                    ceiling = min(
                        raw_passage_word_targets[passage_id],
                        passage_word_budgets.get(passage_id, 0),
                    )
                    recoverable = max(0, ceiling - passage_word_targets[passage_id])
                    recovered = min(deficit, recoverable)
                    passage_word_targets[passage_id] += recovered
                    deficit -= recovered
                    if deficit == 0:
                        break
            if deficit:
                for passage_id in raw_passage_word_targets:
                    recoverable = max(
                        0,
                        passage_word_budgets.get(passage_id, 0)
                        - passage_word_targets[passage_id],
                    )
                    recovered = min(deficit, recoverable)
                    passage_word_targets[passage_id] += recovered
                    deficit -= recovered
                    if deficit == 0:
                        break
            if deficit:
                raise CloudStageError(
                    "visual.narrative_repair_ungrounded",
                    reviewable=True,
                    safe_metadata={
                        "failed_field": "capacity_safe_claim_plan",
                        "failed_predicate": "visual.repair_word_floor_unreachable",
                        "target_word_floor": target_floor,
                        "target_word_capacity": sum(passage_word_budgets.values()),
                    },
                )
        if (
            len(passage_word_budgets) != len(candidate.passages)
            or len(passage_word_targets) != len(candidate.passages)
            or any(value <= 0 for value in passage_word_budgets.values())
            or any(value <= 0 for value in passage_word_targets.values())
            or any(
                passage_word_targets[key] > passage_word_budgets[key]
                for key in passage_word_budgets
            )
        ):
            raise CloudStageError("visual.narrative_repair_ungrounded", reviewable=True)
        observation_by_id = {
            str(item.get("panel_id", "")): item
            for item in observations
            if isinstance(item, Mapping) and str(item.get("panel_id", "")).strip()
        }
        selected_evidence_context: list[dict[str, Any]] = []
        for passage_index, (passage, row) in enumerate(zip(candidate.passages, rows, strict=True)):
            if not isinstance(passage, Mapping) or not isinstance(row, Mapping):
                raise CloudStageError("visual.narrative_repair_ungrounded", reviewable=True)
            evidence_ids = tuple(str(value) for value in row.get("evidence_panel_ids", ()))
            if not evidence_ids:
                raise CloudStageError("visual.narrative_repair_ungrounded", reviewable=True)
            panel_context: list[dict[str, Any]] = []
            for panel_id in evidence_ids:
                observation = observation_by_id.get(panel_id)
                if not isinstance(observation, Mapping):
                    raise CloudStageError("visual.narrative_repair_ungrounded", reviewable=True)
                visible_facts = observation.get("visible_facts")
                uncertainties = observation.get("uncertainties")
                if (
                    not isinstance(visible_facts, list)
                    or not visible_facts
                    or not all(isinstance(value, str) and value.strip() for value in visible_facts)
                    or not isinstance(uncertainties, list)
                    or not all(isinstance(value, str) and value.strip() for value in uncertainties)
                ):
                    raise CloudStageError("visual.narrative_repair_ungrounded", reviewable=True)
                panel_context.append({
                    "panel_id": panel_id,
                    "visible_facts": [str(value).strip() for value in visible_facts],
                    "uncertainties": [str(value).strip() for value in uncertainties],
                })
            selected_evidence_context.append({
                "passage_index": passage_index,
                "passage_id": str(passage.get("passage_id", "")),
                "evidence_panel_ids": list(evidence_ids),
                "panels": panel_context,
            })
        repair_source = {
            **dict(source),
            "provider_context_mode": "locked_story_text_only",
            "selected_story_context": [dict(item) for item in selected_story_context],
            "selected_evidence_context": selected_evidence_context,
            "capacity_safe_claim_plan": {"rows": [dict(item) for item in rows]},
            "visual_repair_text_only_duration_repair": {
                "version": "visual-repair-text-only-duration-v1",
                "visual_repair_prompt_version": visual_repair_prompt[0],
                "visual_repair_prompt_sha256": visual_repair_prompt[1],
                "candidate_scope_hash": self._narration_scope_signature(candidate),
            },
        }
        repair_failure_codes = tuple(str(code) for code in failure_codes)
        repaired = self._run_targeted_narration_repair(
            self.prompts["narration"],
            repair_source,
            observations,
            structural,
            story_map,
            visual,
            candidate,
            repair_failure_codes,
            allow_passage_removal=False,
            passage_word_budgets=passage_word_budgets,
            passage_word_targets=passage_word_targets,
        )
        style_retry_count = 0
        try:
            visual_narrative_repair.validate_repaired_hook_quality(
                repaired.passages,
                repaired.evidence_graph.get("claims", ()),
                capacity_plan,
            )
        except visual_narrative_repair.VisualNarrativeRepairError as exc:
            if exc.code not in {
                "cloud.narrative_flat_recap",
                "cloud.narrative_hook_weak",
                "cloud.narrative_style_stiff",
            }:
                raise
            style_retry_count = 1
            retry_source = {
                **repair_source,
                "visual_repair_text_only_duration_repair": {
                    **dict(repair_source["visual_repair_text_only_duration_repair"]),
                    "candidate_scope_hash": self._narration_scope_signature(repaired),
                    "style_retry_code": exc.code,
                },
            }
            repaired = self._run_targeted_narration_repair(
                self.prompts["narration"],
                retry_source,
                observations,
                structural,
                story_map,
                visual,
                repaired,
                (exc.code,),
                allow_passage_removal=False,
                passage_word_budgets=passage_word_budgets,
                passage_word_targets=passage_word_targets,
            )
            visual_narrative_repair.validate_repaired_hook_quality(
                repaired.passages,
                repaired.evidence_graph.get("claims", ()),
                capacity_plan,
            )
        qc_report = dict(repaired.qc_report)
        qc_report["visual_repair_text_only_duration_repair_v1"] = {
            "version": "visual-repair-text-only-duration-v1",
            "scope": "text_only_locked_claim_evidence",
            "candidate_word_count": int(candidate.word_count),
            "candidate_duration_s": float(candidate.estimated_duration_s),
            "result_word_count": int(repaired.word_count),
            "result_duration_s": float(repaired.estimated_duration_s),
            "passage_removal_allowed": False,
            "failure_codes": [str(code) for code in failure_codes],
            "style_retry_count": style_retry_count,
            "provider_context_mode": "locked_story_text_only",
            "duration_policy_contract": repair_source.get("duration_policy_contract"),
        }
        return replace(
            repaired,
            qc_report=qc_report,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=visual_repair_prompt[0],
            prompt_sha256=visual_repair_prompt[1],
            visual_evidence_hash=visual.visual_evidence_hash,
        )

    @_bound
    def run_visual_narrative_repair(
        self,
        visual: VisualStageResult,
        story_map: StoryMapResult,
        narration: NarrationResult | None,
        ledger: visual_narrative_repair.FeasibleVisualLedger,
        section_to_beats: Mapping[str, Sequence[str]],
        *,
        panels: Sequence[CloudPanelInput] | None = None,
    ) -> NarrationResult:
        """Repair only missing visual sections using the same pinned model."""

        prompt = self.prompts["visual_narrative_repair"]
        observations, structural = self._narration_observations(visual, panels)
        feasible_ids = set(ledger.feasible_panel_ids)
        feasible_observations = [
            dict(item) for item in visual.panels if str(item.get("panel_id", "")) in feasible_ids
        ]
        repair_narration = (
            narration.as_dict()
            if narration is not None
            else {
                "spoken_text": "",
                "passages": [],
                "ending_kind": "",
                "initial_failure_code": "cloud.narrative_not_grounded",
            }
        )
        payload = visual_narrative_repair.build_repair_payload(
            narration=repair_narration,
            story_map=story_map.as_dict(),
            ledger=ledger,
            section_to_beats=section_to_beats,
            feasible_observations=feasible_observations,
        )
        capacity_plan = payload.get("capacity_safe_claim_plan")
        if not isinstance(capacity_plan, Mapping) or not bool(capacity_plan.get("feasible")):
            rebalance = payload.get("capacity_rebalance")
            rebalance = dict(rebalance) if isinstance(rebalance, Mapping) else {}
            raise CloudStageError(
                "visual.narrative_repair_ungrounded",
                reviewable=True,
                safe_metadata={
                    "failed_field": "capacity_safe_claim_plan",
                    "failed_predicate": "visual.repair_capacity_plan_infeasible",
                    "target_visual_slots": rebalance.get("target_visual_slots"),
                    "claim_backed_visual_slots": rebalance.get("claim_backed_visual_slots"),
                    "minimum_visual_slots_for_narration": rebalance.get(
                        "minimum_visual_slots_for_narration"
                    ),
                },
            )
        duration_policy_contract = _narration_repair_contract_bounds(
            payload.get("duration_policy_contract")
        )
        repair_word_min = int(duration_policy_contract["target_word_min"])
        repair_word_max = int(duration_policy_contract["target_word_max"])
        repair_duration_min = float(duration_policy_contract["target_duration_min_s"])
        repair_duration_max = float(duration_policy_contract["target_duration_max_s"])
        repair_is_adaptive = bool(duration_policy_contract["adaptive"])
        render_plan = visual_narrative_repair.FeasibleRenderPlan.from_ledger(ledger)
        capacity_plan_hash = _hash({
            "capacity_safe_claim_plan": capacity_plan,
            "capacity_rebalance": payload.get("capacity_rebalance"),
            "duration_policy_contract": duration_policy_contract,
        })
        source = {
            "visual_source_hash": visual.source_hash,
            "story_map_hash": story_map.story_map_hash,
            "narration_hash": _hash(repair_narration),
            "missing_sections": payload["missing_sections"],
            "ledger_hash": ledger.ledger_hash,
            "section_to_beats": payload["section_to_beats"],
            "render_plan_hash": render_plan.plan_hash,
            "capacity_plan_hash": capacity_plan_hash,
            "duration_policy_contract": duration_policy_contract,
        }
        key = visual_narrative_repair.repair_cache_key(
            ledger=ledger,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_sha256=prompt[1],
            narration_hash=str(source["narration_hash"]),
            capacity_plan_hash=capacity_plan_hash,
        )
        feasible_claim_rows = [
            dict(row)
            for row in payload.get("feasible_claims", ())
            if isinstance(row, Mapping) and str(row.get("claim_id", "")).strip()
        ]
        allowed_claim_panel_ids = {
            str(row["claim_id"]): {
                str(panel_id)
                for panel_id in row.get("evidence_panel_ids", ())
                if str(panel_id).strip()
            }
            for row in feasible_claim_rows
        }
        allowed_claim_ids = set(allowed_claim_panel_ids)

        def reconcile_repaired_references(
            raw_claims: object,
            raw_passages: object,
            *,
            enforce_word_budget: bool = True,
            validate_story_quality: bool = True,
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[dict[str, Any], ...]]:
            if not isinstance(raw_passages, list):
                raise visual_narrative_repair.VisualNarrativeRepairError(
                    "repair passages are malformed",
                    "visual.narrative_repair_ungrounded",
                )
            raw_passages = visual_narrative_repair.lock_capacity_plan_references(
                raw_passages,
                payload.get("capacity_safe_claim_plan", {}),
            )
            authoritative_claims = {
                str(claim["claim_id"]): {
                    key: claim[key]
                    for key in (
                        "claim_id",
                        "claim_type",
                        "text",
                        "qualification",
                        "evidence_panel_ids",
                    )
                    if key in claim
                }
                for claim in self._normalize_narration_claims(feasible_claim_rows)
                if str(claim.get("claim_id", "")).strip()
            }
            ordered_referenced_claim_ids: list[str] = []
            for passage in raw_passages:
                if not isinstance(passage, Mapping):
                    continue
                values = passage.get("claim_ids", ())
                if not isinstance(values, (list, tuple)):
                    continue
                for value in values:
                    claim_id = str(value).strip()
                    if claim_id and claim_id not in ordered_referenced_claim_ids:
                        ordered_referenced_claim_ids.append(claim_id)
            if any(claim_id not in authoritative_claims for claim_id in ordered_referenced_claim_ids):
                raise visual_narrative_repair.VisualNarrativeRepairError(
                    "repair claim is unsupported",
                    "visual.narrative_repair_ungrounded",
                )
            claims = visual_narrative_repair.narrow_claim_evidence_to_capacity_plan(
                [
                    dict(authoritative_claims[claim_id])
                    for claim_id in ordered_referenced_claim_ids
                ],
                raw_passages,
            )
            canonical_payload = visual_narrative_repair.canonicalize_repair_claim_ids(
                {"claims": claims, "passages": raw_passages},
                allowed_claim_ids=allowed_claim_ids,
            )
            capacity_plan = payload.get("capacity_safe_claim_plan", {})
            if visual_narrative_repair.repaired_references_match_capacity_plan(
                canonical_payload["passages"], capacity_plan
            ):
                repaired_payload, remaps = canonical_payload, ()
            else:
                repaired_payload, remaps = visual_narrative_repair.remap_same_beat_panel_citations(
                    canonical_payload,
                    ledger=ledger,
                    section_to_beats=section_to_beats,
                    allowed_claim_panel_ids=allowed_claim_panel_ids,
                )
            claims = self._normalize_narration_claims(repaired_payload["claims"])
            passages = [dict(item) for item in repaired_payload["passages"]]
            visual_narrative_repair.validate_repaired_panel_references(
                {"claims": claims, "passages": passages},
                ledger=ledger,
                allowed_claim_ids=allowed_claim_ids,
                allowed_claim_panel_ids=allowed_claim_panel_ids,
            )
            visual_narrative_repair.validate_repaired_capacity_safe_claim_plan(
                passages,
                payload.get("capacity_safe_claim_plan", {}),
                enforce_word_budget=enforce_word_budget,
            )
            if validate_story_quality:
                visual_narrative_repair.validate_repaired_hook_quality(
                    passages, claims, payload.get("capacity_safe_claim_plan", {})
                )
            return claims, passages, remaps

        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            try:
                cached_result = NarrationResult.from_dict(cached)
                if cached_result.visual_evidence_hash != visual.visual_evidence_hash or not _narration_result_is_usable(
                    cached_result,
                    visual,
                    require_duration=True,
                    duration_policy_contract=duration_policy_contract,
                ):
                    cached_result = None
                if cached_result is not None:
                    claims, passages, remaps = reconcile_repaired_references(
                        cached_result.evidence_graph.get("claims"),
                        list(cached_result.passages),
                    )
                    visual_narrative_repair.validate_repaired_section_visual_coverage(
                        passages,
                        ledger=ledger,
                        section_to_beats=section_to_beats,
                        missing_sections=visual_narrative_repair.missing_visual_sections(
                            ledger, section_to_beats
                        ),
                    )
                    visual_narrative_repair.validate_repaired_visual_capacity(
                        passages,
                        ledger,
                        total_duration_s=cached_result.estimated_duration_s,
                    )
                    if visual_narrative_repair.narration_sections_with_subtitle_overflow(
                        cached_result, section_to_beats
                    ):
                        raise ValueError("cached repair violates subtitle layout")
                    if not remaps:
                        return cached_result
                    evidence_graph = dict(cached_result.evidence_graph)
                    evidence_graph["claims"] = claims
                    qc_report = dict(cached_result.qc_report)
                    qc_report["visual_section_remap_v1"] = list(remaps)
                    return replace(
                        cached_result,
                        passages=tuple(passages),
                        evidence_graph=evidence_graph,
                        qc_report=qc_report,
                    )
            except (
                analyzer_contract.AnalyzerContractError,
                CloudStageError,
                KeyError,
                TypeError,
                ValueError,
                visual_narrative_repair.VisualNarrativeRepairError,
            ):
                # A cache entry is untrusted persisted state.  A stricter
                # lineage/visual ledger can invalidate an older entry; treat
                # that entry as a miss and use the bounded repair contract.
                pass

        retryable_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.narrative_not_grounded",
            "cloud.narrative_duration_out_of_range",
            "cloud.narrative_qc_blocked",
            "cloud.narrative_flat_recap",
            "cloud.narrative_hook_weak",
            "cloud.narrative_style_stiff",
            "visual.narrative_repair_ungrounded",
        }
        retry_feedback = ""
        for attempt in range(visual_narrative_repair.MAX_REPAIR_ATTEMPTS):
            try:
                request_payload = {
                    **payload,
                    "repair_attempt": attempt + 1,
                    "request_identity": source,
                }
                if retry_feedback:
                    request_payload["contract_retry_feedback"] = retry_feedback
                raw = self._call(
                    lambda request_payload=request_payload: self.provider.complete_json(
                        stage="visual_narrative_repair",
                        prompt_version=prompt[0],
                        prompt_sha256=prompt[1],
                        prompt_text=prompt[2],
                        payload=request_payload,
                    ),
                    request_stage="other",
                )
                if not isinstance(raw, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                provider_output = raw.get("analyzer_output", raw)
                if not isinstance(provider_output, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                raw_claims = provider_output.get("claims")
                if raw_claims is None:
                    raw_claims = provider_output.get("evidence_graph")
                claims = raw_claims
                passages = provider_output.get("passages")
                if passages is None:
                    passages = provider_output.get("script_passages")
                outline = provider_output.get("narrative_outline")
                if not isinstance(passages, list) or not isinstance(outline, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                claims, passages, remaps = reconcile_repaired_references(
                    claims,
                    passages,
                    enforce_word_budget=False,
                    validate_story_quality=False,
                )
                visual_narrative_repair.validate_repaired_section_visual_coverage(
                    passages,
                    ledger=ledger,
                    section_to_beats=section_to_beats,
                    missing_sections=visual_narrative_repair.missing_visual_sections(
                        ledger, section_to_beats
                    ),
                )
                canonical_outline, ending_canonicalization = _canonicalize_visual_repair_ending(
                    outline, passages
                )
                output = {
                    "observations": observations,
                    "continuity_ledger": structural["continuity_ledger"],
                    "coverage_manifest": structural["coverage_manifest"],
                    "evidence_graph": {"claims": claims},
                    "narrative_outline": canonical_outline,
                    "script_passages": [dict(item) for item in passages],
                }
                analyzer_contract.validate_analyzer_output(
                    output,
                    expected_panel_ids=tuple(str(item["panel_id"]) for item in observations),
                    narrative_profile_id="sharp_friend_v1",
                )
                claims_by_id = {str(claim["claim_id"]): claim for claim in claims}
                passage_rows = tuple(dict(item) for item in passages)
                visual_repair_micro_compaction: dict[str, Any] | None = None
                visual_repair_micro_expansion: dict[str, Any] | None = None
                visual_repair_text_only_duration_repair: dict[str, Any] | None = None
                visual_repair_text_only_narration_repair: dict[str, Any] | None = None
                style_failure_code: str | None = None
                try:
                    visual_narrative_repair.validate_repaired_hook_quality(
                        passage_rows, claims, payload.get("capacity_safe_claim_plan", {})
                    )
                except visual_narrative_repair.VisualNarrativeRepairError as style_exc:
                    if style_exc.code not in {
                        "cloud.narrative_flat_recap",
                        "cloud.narrative_hook_weak",
                        "cloud.narrative_style_stiff",
                    }:
                        raise
                    style_failure_code = style_exc.code
                if style_failure_code is not None:
                    draft_spoken_text = "\n\n".join(
                        str(item["text"]).strip() for item in passage_rows
                    )
                    draft_metrics = script.narration_duration_metrics(draft_spoken_text, "dramatic")
                    provisional = NarrationResult(
                        spoken_text=draft_spoken_text,
                        display_words=derive_display_words(draft_spoken_text),
                        passages=passage_rows,
                        ending_kind=str(canonical_outline["ending_kind"]),
                        word_count=int(draft_metrics["word_count"]),
                        estimated_duration_s=float(draft_metrics["estimated_duration_s"]),
                        qc_report={"style_repair_required": style_failure_code},
                        model_identity_hash=self.model_identity.identity_hash,
                        prompt_version=prompt[0],
                        prompt_sha256=prompt[1],
                        observations=tuple(observations),
                        continuity_ledger=dict(output["continuity_ledger"]),
                        evidence_graph={"claims": claims},
                        story_spine=dict(canonical_outline["story_spine"]),
                        visual_evidence_hash=visual.visual_evidence_hash,
                    )
                    text_repaired = self._run_visual_repair_text_only_duration_repair(
                        visual_repair_prompt=prompt,
                        source=source,
                        observations=observations,
                        structural=structural,
                        story_map=story_map,
                        visual=visual,
                        candidate=provisional,
                        capacity_plan=payload.get("capacity_safe_claim_plan", {}),
                        selected_story_context=payload.get("selected_story_context", ()),
                        failure_codes=(style_failure_code,),
                    )
                    visual_repair_text_only_duration_repair = dict(
                        text_repaired.qc_report.get("visual_repair_text_only_duration_repair_v1", {})
                    )
                    visual_repair_text_only_narration_repair = dict(
                        text_repaired.qc_report.get("narration_repair", {})
                    )
                    claims, passages, text_remaps = reconcile_repaired_references(
                        text_repaired.evidence_graph.get("claims"),
                        list(text_repaired.passages),
                    )
                    if text_remaps:
                        remaps = (*remaps, *text_remaps)
                    passage_rows = tuple(dict(item) for item in passages)
                    claims_by_id = {str(claim["claim_id"]): claim for claim in claims}
                    output["script_passages"] = [dict(item) for item in passage_rows]
                    output["evidence_graph"] = {"claims": claims}
                canonical_before = sum(
                    script.narration_word_count(str(item.get("text", "")))
                    for item in passage_rows
                )
                if not repair_is_adaptive and canonical_before > repair_word_max:
                    compacted, compaction = _micro_compact_rewrites(
                        tuple(str(item.get("text", "")).strip() for item in passage_rows),
                        total_words=canonical_before,
                    )
                    visual_repair_micro_compaction = dict(compaction)
                    if not compaction.get("failed_predicate"):
                        passage_rows = tuple(
                            dict(row, text=text)
                            for row, text in zip(passage_rows, compacted, strict=True)
                        )
                        passages = [dict(item) for item in passage_rows]
                        output["script_passages"] = [dict(item) for item in passage_rows]
                elif not repair_is_adaptive and canonical_before < repair_word_min:
                    expanded, expansion = _micro_expand_rewrites(
                        tuple(str(item.get("text", "")).strip() for item in passage_rows),
                        total_words=canonical_before,
                    )
                    visual_repair_micro_expansion = dict(expansion)
                    if not expansion.get("failed_predicate"):
                        expanded_rows = tuple(
                            dict(row, text=text)
                            for row, text in zip(passage_rows, expanded, strict=True)
                        )
                        try:
                            visual_narrative_repair.validate_repaired_capacity_safe_claim_plan(
                                expanded_rows,
                                payload.get("capacity_safe_claim_plan", {}),
                            )
                        except visual_narrative_repair.VisualNarrativeRepairError:
                            visual_repair_micro_expansion = {
                                **visual_repair_micro_expansion,
                                "applied": False,
                                "after_word_count": canonical_before,
                                "failed_predicate": "micro_expansion_capacity_budget",
                            }
                        else:
                            passage_rows = expanded_rows
                            passages = [dict(item) for item in passage_rows]
                            output["script_passages"] = [dict(item) for item in passage_rows]
                report = editorial_qc.screen_narrative_naturalness(
                    passage_rows,
                    claims_by_id,
                    narrative_identity.SHARP_FRIEND_V1,
                )
                checks = quality.check_narrative_naturalness(report)
                blocking_checks = [
                    check for check in checks if not check.passed and check.severity == "error"
                ]
                if blocking_checks:
                    raise CloudStageError(
                        "cloud.narrative_qc_blocked",
                        reviewable=True,
                        safe_metadata={
                            "failed_predicate": str(blocking_checks[0].code),
                            "anti_slop_markers": list(dict.fromkeys([
                                *list(getattr(report, "ai_slop_hits", ()) or ()),
                                *list(getattr(report, "reporter_prose_hits", ()) or ()),
                            ]))[:8],
                        },
                    )
                spoken_text = "\n\n".join(str(item["text"]).strip() for item in passage_rows)
                display_words = derive_display_words(spoken_text)
                duration_metrics = script.narration_duration_metrics(
                    spoken_text,
                    "dramatic",
                )
                canonical_word_count = int(duration_metrics["word_count"])
                duration = float(duration_metrics["estimated_duration_s"])
                if (
                    not repair_duration_min <= duration <= repair_duration_max
                    or not repair_word_min <= report.total_words <= repair_word_max
                    or not repair_word_min <= canonical_word_count <= repair_word_max
                ):
                    provisional_qc = {
                        "profile_id": "sharp_friend_v1",
                        "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
                        "total_words": int(report.total_words),
                        "estimated_duration_s": duration,
                        "duration_contract": duration_metrics,
                        "ending_kind": output["narrative_outline"]["ending_kind"],
                        "display_word_count": len(display_words),
                        "timing_source": "voice_required",
                        "warnings": list(report.warnings),
                        "signals": asdict(report),
                        "repair_contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                        "visual_section_remap_v1": list(remaps),
                        "visual_repair_ending_canonicalization_v1": ending_canonicalization,
                        "visual_repair_micro_compaction_v1": visual_repair_micro_compaction,
                        "visual_repair_micro_expansion_v1": visual_repair_micro_expansion,
                    }
                    provisional = NarrationResult(
                        spoken_text=spoken_text,
                        display_words=display_words,
                        passages=passage_rows,
                        ending_kind=str(output["narrative_outline"]["ending_kind"]),
                        word_count=canonical_word_count,
                        estimated_duration_s=duration,
                        qc_report=provisional_qc,
                        model_identity_hash=self.model_identity.identity_hash,
                        prompt_version=prompt[0],
                        prompt_sha256=prompt[1],
                        observations=tuple(observations),
                        continuity_ledger=dict(output["continuity_ledger"]),
                        evidence_graph=dict(output["evidence_graph"]),
                        story_spine=dict(output["narrative_outline"]["story_spine"]),
                        visual_evidence_hash=visual.visual_evidence_hash,
                    )
                    text_repaired = self._run_visual_repair_text_only_duration_repair(
                        visual_repair_prompt=prompt,
                        source=source,
                        observations=observations,
                        structural=structural,
                        story_map=story_map,
                        visual=visual,
                        candidate=provisional,
                        capacity_plan=payload.get("capacity_safe_claim_plan", {}),
                        selected_story_context=payload.get("selected_story_context", ()),
                    )
                    visual_repair_text_only_duration_repair = dict(
                        text_repaired.qc_report.get(
                            "visual_repair_text_only_duration_repair_v1", {}
                        )
                    )
                    visual_repair_text_only_narration_repair = dict(
                        text_repaired.qc_report.get("narration_repair", {})
                    )
                    claims, passages, text_remaps = reconcile_repaired_references(
                        text_repaired.evidence_graph.get("claims"),
                        list(text_repaired.passages),
                    )
                    if text_remaps:
                        remaps = (*remaps, *text_remaps)
                    visual_narrative_repair.validate_repaired_section_visual_coverage(
                        passages,
                        ledger=ledger,
                        section_to_beats=section_to_beats,
                        missing_sections=visual_narrative_repair.missing_visual_sections(
                            ledger, section_to_beats
                        ),
                    )
                    passage_rows = tuple(dict(item) for item in passages)
                    claims_by_id = {str(claim["claim_id"]): claim for claim in claims}
                    report = editorial_qc.screen_narrative_naturalness(
                        passage_rows,
                        claims_by_id,
                        narrative_identity.SHARP_FRIEND_V1,
                    )
                    checks = quality.check_narrative_naturalness(report)
                    blocking_checks = [
                        check for check in checks if not check.passed and check.severity == "error"
                    ]
                    if blocking_checks:
                        raise CloudStageError(
                            "cloud.narrative_qc_blocked",
                            reviewable=True,
                            safe_metadata={
                                "failed_predicate": str(blocking_checks[0].code),
                                "anti_slop_markers": list(dict.fromkeys([
                                    *list(getattr(report, "ai_slop_hits", ()) or ()),
                                    *list(getattr(report, "reporter_prose_hits", ()) or ()),
                                ]))[:8],
                            },
                        )
                    spoken_text = "\n\n".join(
                        str(item["text"]).strip() for item in passage_rows
                    )
                    display_words = derive_display_words(spoken_text)
                    duration_metrics = script.narration_duration_metrics(
                        spoken_text,
                        "dramatic",
                    )
                    canonical_word_count = int(duration_metrics["word_count"])
                    duration = float(duration_metrics["estimated_duration_s"])
                    output["script_passages"] = [dict(item) for item in passage_rows]
                    output["evidence_graph"] = {"claims": claims}
                    if (
                        not repair_duration_min <= duration <= repair_duration_max
                        or not repair_word_min <= report.total_words <= repair_word_max
                        or not repair_word_min <= canonical_word_count <= repair_word_max
                    ):
                        raise CloudStageError(
                            "cloud.narrative_duration_out_of_range",
                            reviewable=True,
                            safe_metadata={
                                "failed_field": "script_passages",
                                "failed_predicate": "narration_duration_contract",
                                "observed_word_count": canonical_word_count,
                                "reported_word_count": int(report.total_words),
                                "observed_duration_s": duration,
                                "target_word_count_min": repair_word_min,
                                "target_word_count_max": repair_word_max,
                                "target_duration_min_s": repair_duration_min,
                                "target_duration_max_s": repair_duration_max,
                                "duration_policy": duration_policy_contract["version"],
                                "text_only_repair_attempted": True,
                            },
                        )
                visual_narrative_repair.validate_repaired_visual_capacity(
                    passage_rows,
                    ledger,
                    total_duration_s=duration,
                )
                subtitle_overflow_sections = (
                    visual_narrative_repair.narration_sections_with_subtitle_overflow(
                        {
                            "passages": list(passage_rows),
                            "estimated_duration_s": duration,
                        },
                        section_to_beats,
                    )
                )
                if subtitle_overflow_sections:
                    raise CloudStageError(
                        "visual.narrative_repair_ungrounded",
                        reviewable=True,
                        safe_metadata={
                            "failed_field": "script_passages",
                            "failed_predicate": "visual.repair_subtitle_overflow",
                            "failed_section_count": len(subtitle_overflow_sections),
                        },
                    )
                result = NarrationResult(
                    spoken_text=spoken_text,
                    display_words=display_words,
                    passages=passage_rows,
                    ending_kind=str(output["narrative_outline"]["ending_kind"]),
                    word_count=report.total_words,
                    estimated_duration_s=duration,
                    qc_report={
                        "profile_id": "sharp_friend_v1",
                        "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
                        "total_words": report.total_words,
                        "estimated_duration_s": duration,
                        "duration_contract": duration_metrics,
                        "ending_kind": output["narrative_outline"]["ending_kind"],
                        "display_word_count": len(display_words),
                        "timing_source": "voice_required",
                        "warnings": list(report.warnings),
                        "signals": asdict(report),
                        "repair_contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                        "visual_section_remap_v1": list(remaps),
                        "visual_repair_ending_canonicalization_v1": ending_canonicalization,
                        "visual_repair_micro_compaction_v1": visual_repair_micro_compaction,
                        "visual_repair_micro_expansion_v1": visual_repair_micro_expansion,
                        "visual_repair_text_only_duration_repair_v1": (
                            visual_repair_text_only_duration_repair
                        ),
                        "narration_repair": visual_repair_text_only_narration_repair,
                        "duration_policy_contract": duration_policy_contract,
                        "feasible_ledger_hash": ledger.ledger_hash,
                        "feasible_render_plan_hash": render_plan.plan_hash,
                        "repaired_sections": list(
                            visual_narrative_repair.missing_visual_sections(
                                ledger, section_to_beats
                            )
                        ),
                    },
                    model_identity_hash=self.model_identity.identity_hash,
                    prompt_version=prompt[0],
                    prompt_sha256=prompt[1],
                    observations=tuple(observations),
                    continuity_ledger=dict(output["continuity_ledger"]),
                    evidence_graph=dict(output["evidence_graph"]),
                    story_spine=dict(output["narrative_outline"]["story_spine"]),
                    visual_evidence_hash=visual.visual_evidence_hash,
                )
                if self.cache is not None:
                    self.cache.put(key, result.as_dict())
                return result
            except analyzer_contract.AnalyzerContractError as aexc:
                print("ANALYZER_CONTRACT_FAIL:", repr(aexc), file=sys.stderr, flush=True)
                error = CloudStageError(
                    "cloud.narrative_not_grounded",
                    "analyzer contract rejected",
                    reviewable=True,
                    safe_metadata=_visual_narrative_repair_analyzer_metadata(
                        str(aexc),
                        output if isinstance(output, Mapping) else None,
                    ),
                )
            except visual_narrative_repair.VisualNarrativeRepairError as exc:
                error = CloudStageError(
                    exc.code,
                    reviewable=exc.reviewable,
                    safe_metadata=_visual_narrative_repair_error_metadata(
                        str(exc),
                        code=exc.code,
                    ),
                )
            except CloudStageError as exc:
                safe_metadata = dict(exc.safe_metadata)
                safe_metadata.setdefault(
                    "failed_predicate",
                    _visual_narrative_repair_error_metadata(
                        str(exc),
                        code=exc.code,
                    )["failed_predicate"],
                )
                error = CloudStageError(
                    exc.code,
                    str(exc),
                    reviewable=exc.reviewable,
                    safe_metadata=safe_metadata,
                )
            if (
                error.code not in retryable_codes
                or attempt + 1 >= visual_narrative_repair.MAX_REPAIR_ATTEMPTS
            ):
                safe_metadata = dict(getattr(error, "safe_metadata", {}) or {})
                safe_metadata.update(
                    _visual_narrative_repair_failure_metadata(
                        ledger=ledger,
                        section_to_beats=section_to_beats,
                        attempt_count=attempt + 1,
                        failure_code=error.code,
                    )
                )
                raise CloudStageError(
                    error.code,
                    reviewable=error.reviewable,
                    safe_metadata=safe_metadata,
                )
            observed_word_count = error.safe_metadata.get("observed_word_count")
            retry_feedback = _visual_narrative_repair_retry_feedback(
                error.code,
                failed_field=str(error.safe_metadata.get("failed_field", "")) or None,
                failed_predicate=str(error.safe_metadata.get("failed_predicate", "")) or None,
                observed_word_count=(
                    int(observed_word_count)
                    if isinstance(observed_word_count, int)
                    else None
                ),
            )
        raise CloudStageError("visual.narrative_repair_bounded", reviewable=True)
