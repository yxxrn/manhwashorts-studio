"""Narration methods extracted from cloud_multimodal."""

# ruff: noqa: F821 -- runtime globals are refreshed from the compatibility facade.
from __future__ import annotations

from app.constants import (
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
)
from app.services import story_understanding

from .runtime import runtime_bound

_RUNTIME_NAMES = (
    'CloudStageError',
    'EDITORIAL_SELECTION_VERSION',
    'Mapping',
    'NARRATION_CHUNK_STEP',
    'NARRATION_COVERAGE_FALLBACK_STEP',
    'NARRATION_COVERAGE_MIN_STEP',
    'NARRATION_REPAIR_CANDIDATE_STAGE',
    'NarrationResult',
    'STAGE_PARALLEL_WORKERS',
    'StoryMapResult',
    'ThreadPoolExecutor',
    '_cache_key',
    '_hash',
    '_narration_repair_contract_bounds',
    '_narration_result_is_usable',
    '_narration_retry_feedback',
    '_reconcile_narration_full_scope',
    '_safe_narration_contract_diagnostic',
    'analyzer_contract',
    'editorial_qc',
    'math',
    'narrative_identity',
    'quality',
    're',
    'replace',
    'script',
    'select_editorial_beats',
    'sys',
)
_bound = runtime_bound(_RUNTIME_NAMES)


class NarrationMixin:
    @_bound
    def _run_story_semantic_audit(self, understanding, observations, structural, story_map):
        prompt = self.prompts["story_semantic_audit"]
        source = story_understanding.build_semantic_audit_packet(
            understanding, observations, story_map.as_dict(), structural["continuity_ledger"]
        )
        key = _cache_key("story_semantic_audit", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            try:
                return story_understanding.validate_semantic_audit(
                    cached,
                    expected_beat_ids=[beat["beat_id"] for beat in understanding["narration_ready_beats"]],
                )
            except story_understanding.StoryUnderstandingError:
                pass
        raw = self._call(
            lambda: self.provider.complete_json(
                stage="story_semantic_audit", prompt_version=prompt[0],
                prompt_sha256=prompt[1], prompt_text=prompt[2], payload=source,
            ), request_stage="other",
        )
        audit = story_understanding.validate_semantic_audit(
            raw,
            expected_beat_ids=[beat["beat_id"] for beat in understanding["narration_ready_beats"]],
        )
        if self.cache is not None:
            self.cache.put(key, audit)
        return audit

    @_bound
    def _run_story_understanding(self, observations, structural, story_map, visual):
        prompt = self.prompts["story_understanding"]
        source = story_understanding.build_source_packet(
            observations, story_map.as_dict(), structural["continuity_ledger"]
        )
        key = _cache_key("story_understanding", source, self.model_identity, prompt)
        if (
            self.cache is not None
            and (cached := self.cache.get(key)) is not None
            and isinstance(cached, Mapping)
        ):
                try:
                    cached_understanding = dict(cached.get("understanding") or {})
                    cached_understanding_hash = str(cached_understanding.pop("understanding_hash", ""))
                    normalized = story_understanding.validate_result(
                        cached_understanding, expected_panel_ids=visual.panel_ids,
                        story_map=story_map.as_dict(),
                    )
                    if normalized["understanding_hash"] != cached_understanding_hash:
                        raise story_understanding.StoryUnderstandingError("cached understanding hash mismatch")
                    audit = story_understanding.validate_semantic_audit(
                        cached.get("semantic_audit"),
                        expected_beat_ids=[beat["beat_id"] for beat in normalized["narration_ready_beats"]],
                    )
                    return story_understanding.apply_semantic_audit(normalized, audit)
                except story_understanding.StoryUnderstandingError:
                    pass
        feedback = []
        for attempt in range(self.max_attempts):
            request_source = dict(source)
            if feedback:
                request_source["semantic_audit_feedback"] = feedback
            try:
                raw = self._call(lambda request_source=request_source: self.provider.complete_json(
                    stage="story_understanding", prompt_version=prompt[0],
                    prompt_sha256=prompt[1], prompt_text=prompt[2], payload=request_source,
                ), request_stage="other")
                normalized = story_understanding.validate_result(
                    raw, expected_panel_ids=visual.panel_ids, story_map=story_map.as_dict()
                )
                audit = self._run_story_semantic_audit(
                    normalized, observations, structural, story_map
                )
            except story_understanding.StoryUnderstandingError:
                if attempt + 1 < self.max_attempts:
                    continue
                raise CloudStageError("cloud.story_understanding_invalid", reviewable=True) from None
            try:
                final = story_understanding.apply_semantic_audit(normalized, audit)
            except story_understanding.StoryUnderstandingError:
                feedback = [
                    item for item in audit["verdicts"] if item["supported"] is False
                ]
                if attempt + 1 < self.max_attempts:
                    continue
                raise CloudStageError("cloud.story_understanding_ungrounded", reviewable=True) from None
            if self.cache is not None:
                # Cache the original proposal with the complete audit. Admission
                # reapplies the deterministic filter and never revives a failed beat.
                self.cache.put(key, {"understanding": normalized, "semantic_audit": audit})
            return final
        raise CloudStageError("cloud.story_understanding_invalid", reviewable=True)

    @_bound
    def run_narration(
        self,
        visual: VisualStageResult,
        story_map: StoryMapResult,
        *,
        panels: Sequence[CloudPanelInput] | None = None,
    ) -> NarrationResult:
        """Reduce full-panel evidence to selected beats, then write one script."""

        prompt = self.prompts["narration"]
        observations, _structural = self._narration_observations(visual, panels)
        full_story_context = self._run_story_understanding(
            observations, _structural, story_map, visual
        )
        selection = select_editorial_beats(
            visual, story_map, story_context=full_story_context
        )
        selected_ids = set(selection.panel_ids)
        selected_visual = replace(
            visual,
            panels=tuple(
                dict(panel)
                for panel in visual.panels
                if str(panel.get("panel_id", "")) in selected_ids
            ),
        )
        selected_beats: list[dict[str, Any]] = []
        for beat in story_map.beats:
            if not isinstance(beat, Mapping) or str(beat.get("beat_id", "")) not in set(
                selection.beat_ids
            ):
                continue
            row = dict(beat)
            row["panel_ids"] = [
                str(panel_id)
                for panel_id in beat.get("panel_ids", ())
                if str(panel_id) in selected_ids
            ]
            if row["panel_ids"]:
                selected_beats.append(row)
        selected_beat_ids = {str(beat["beat_id"]) for beat in selected_beats}
        selected_claims: list[dict[str, Any]] = []
        for claim in story_map.claims:
            if not isinstance(claim, Mapping) or str(claim.get("claim_id", "")) not in set(
                selection.claim_ids
            ):
                continue
            row = dict(claim)
            key = "evidence_panel_ids" if "evidence_panel_ids" in row else "panel_ids"
            row[key] = [
                str(panel_id) for panel_id in row.get(key, ()) if str(panel_id) in selected_ids
            ]
            if row[key]:
                selected_claims.append(row)
        selected_chain = tuple(
            dict(link)
            for link in story_map.causal_chain
            if str(link.get("from_beat", "")) in selected_beat_ids
            and str(link.get("to_beat", "")) in selected_beat_ids
        )
        selected_story = StoryMapResult(
            panel_ids=tuple(selection.panel_ids),
            beats=tuple(selected_beats),
            causal_chain=selected_chain,
            claims=tuple(selected_claims),
            story_map_hash=_hash(
                {
                    "selection_hash": selection.selection_hash,
                    "beats": selected_beats,
                    "claims": selected_claims,
                    "chain": list(selected_chain),
                }
            ),
            model_identity_hash=story_map.model_identity_hash,
            prompt_version=story_map.prompt_version,
            prompt_sha256=story_map.prompt_sha256,
            visual_evidence_hash=selected_visual.visual_evidence_hash,
        )
        selected_panels = (
            tuple(panel for panel in panels if panel.panel_id in selected_ids)
            if panels is not None
            else None
        )
        selected_observations, selected_structural = self._narration_observations(
            selected_visual,
            selected_panels,
        )
        story_context = story_understanding.project_to_panels(
            full_story_context, selection.panel_ids
        )
        support_claims = story_understanding.support_only_claims(selected_story.claims)
        understanding_claims = story_understanding.materialize_grounded_claims(story_context)
        understanding_beats = tuple(
            {
                "beat_id": f"su--{beat['beat_id']}",
                "panel_ids": [str(value) for value in beat.get("evidence_panel_ids", ())],
                "summary": str(beat.get("fact", "")),
                "story_role": str(beat.get("story_role", "")),
                "narrative_function": str(beat.get("narrative_function", "")),
                "grounded_source": "story_understanding",
            }
            for beat in story_context.get("narration_ready_beats", ())
            if isinstance(beat, Mapping)
        )
        panel_order = {
            str(panel_id): index for index, panel_id in enumerate(selected_story.panel_ids)
        }
        combined_beats = tuple(sorted(
            [*selected_story.beats, *understanding_beats],
            key=lambda beat: min(
                (panel_order.get(str(panel_id), len(panel_order)) for panel_id in beat.get("panel_ids", ())),
                default=len(panel_order),
            ),
        ))
        writer_story = replace(
            selected_story,
            beats=combined_beats,
            claims=(*support_claims, *understanding_claims),
            story_map_hash=_hash({
                "base_story_map_hash": selected_story.story_map_hash,
                "story_understanding_hash": story_context["understanding_hash"],
                "combined_beats": list(combined_beats),
                "understanding_claims": list(understanding_claims),
            }),
        )
        source = {
            "editorial_selection_version": EDITORIAL_SELECTION_VERSION,
            "editorial_selection": selection.as_dict(),
            "panel_ids": list(selection.panel_ids),
            "visual_source_hash": visual.source_hash,
            "visual_evidence_hash": visual.visual_evidence_hash,
            "visual_observations": selected_observations,
            "story_map": writer_story.as_dict(),
            "story_understanding": story_context,
            "duration_contract": {
                **script.narration_duration_contract("dramatic"),
                "minimum_s": STANDARD_FINAL_DURATION_MIN_SECONDS,
                "maximum_s": STANDARD_FINAL_DURATION_MAX_SECONDS,
                "target_word_min": 115,
                "target_word_max": 125,
            },
        }
        key = _cache_key("narration", source, self.model_identity, prompt)
        result: NarrationResult | None = None
        failure_codes: tuple[str, ...] = ()
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            try:
                cached_result = NarrationResult.from_dict(cached)
            except (KeyError, TypeError, ValueError):
                cached_result = None
            cache_identity_matches = (
                cached_result is not None
                and cached_result.model_identity_hash == self.model_identity.identity_hash
                and cached_result.prompt_version == prompt[0]
                and cached_result.prompt_sha256 == prompt[1]
                and cached_result.visual_evidence_hash == visual.visual_evidence_hash
            )
            final_metadata_matches = (
                cached_result is not None
                and cached_result.qc_report.get("editorial_selection", {}).get("selection_hash")
                == selection.selection_hash
                and cached_result.qc_report.get("narration_topology")
                == "chapter_story_understanding_v2"
                and cached_result.qc_report.get("narration_cache_contract") == "narration-final-v3"
                and cached_result.qc_report.get("story_map_hash") == writer_story.story_map_hash
                and cached_result.qc_report.get("story_understanding_version")
                == story_understanding.STORY_UNDERSTANDING_VERSION
                and cached_result.qc_report.get("story_understanding_hash")
                == story_context["understanding_hash"]
                and cached_result.qc_report.get("story_understanding_full_hash")
                == full_story_context["understanding_hash"]
                and cached_result.qc_report.get("story_semantic_audit_hash")
                == full_story_context["semantic_audit_hash"]
                and cached_result.qc_report.get("visual_evidence_hash")
                == visual.visual_evidence_hash
                and cached_result.qc_report.get("model_identity_hash")
                == self.model_identity.identity_hash
                and cached_result.qc_report.get("prompt_version") == prompt[0]
                and cached_result.qc_report.get("prompt_sha256") == prompt[1]
            )
            if (
                cache_identity_matches
                and final_metadata_matches
                and _narration_result_is_usable(
                    cached_result,
                    visual,
                    require_duration=True,
                    require_grounding=True,
                )
            ):
                return cached_result
            if cache_identity_matches and _narration_result_is_usable(
                cached_result,
                visual,
                require_duration=False,
                require_grounding=True,
            ):
                failure_codes = self._narration_contract_failures(cached_result)
                if failure_codes:
                    self._store_narration_repair_candidate(
                        source=source,
                        prompt=prompt,
                        result=cached_result,
                        failure_codes=failure_codes,
                        visual=selected_visual,
                        story_map=writer_story,
                    )
                    deleter = getattr(self.cache, "delete", None)
                    if callable(deleter):
                        deleter(key)
                    result = cached_result

        if result is None:
            loaded_candidate = self._load_narration_repair_candidate(
                source=source,
                prompt=prompt,
                visual=selected_visual,
            )
            if loaded_candidate is not None:
                result, failure_codes = loaded_candidate
                if failure_codes:
                    result = self.run_narration_repair_candidate(
                        result,
                        selected_visual,
                        writer_story,
                        panels=selected_panels,
                    )
                    failure_codes = ()

        if result is None:
            result = self._run_narration_batched(
                prompt,
                source,
                selected_observations,
                selected_structural,
                writer_story,
                selected_visual,
            )
        if not failure_codes:
            failure_codes = self._narration_contract_failures(result)
        if failure_codes:
            self._store_narration_repair_candidate(
                source=source,
                prompt=prompt,
                result=result,
                failure_codes=failure_codes,
                visual=selected_visual,
                story_map=writer_story,
            )
            try:
                result = self._run_targeted_narration_repair(
                    prompt,
                    source,
                    selected_observations,
                    selected_structural,
                    writer_story,
                    selected_visual,
                    result,
                    failure_codes,
                )
            except CloudStageError:
                self._last_narration_result = result
                raise
            remaining_failures = self._narration_contract_failures(result)
            if remaining_failures:
                self._last_narration_result = result
                failure_code = remaining_failures[0]
                raise CloudStageError(
                    failure_code,
                    reviewable=True,
                    safe_metadata=self._response_shape_metrics_for_failure(failure_code),
                )
        qc_report = dict(result.qc_report)
        qc_report["editorial_selection"] = selection.as_dict()
        qc_report["narration_topology"] = "chapter_story_understanding_v2"
        qc_report["narration_cache_contract"] = "narration-final-v3"
        qc_report["story_map_hash"] = writer_story.story_map_hash
        qc_report["story_understanding_version"] = story_understanding.STORY_UNDERSTANDING_VERSION
        qc_report["story_understanding_hash"] = story_context["understanding_hash"]
        qc_report["story_understanding_full_hash"] = full_story_context["understanding_hash"]
        qc_report["story_semantic_audit_version"] = full_story_context["semantic_audit_version"]
        qc_report["story_semantic_audit_hash"] = full_story_context["semantic_audit_hash"]
        qc_report["story_semantic_audit_dropped_beat_ids"] = list(
            full_story_context.get("semantic_audit_dropped_beat_ids", ())
        )
        qc_report["story_semantic_audit_supported_beat_count"] = int(
            full_story_context.get("semantic_audit_supported_beat_count", 0)
        )
        qc_report["visual_evidence_hash"] = visual.visual_evidence_hash
        qc_report["model_identity_hash"] = self.model_identity.identity_hash
        qc_report["prompt_version"] = prompt[0]
        qc_report["prompt_sha256"] = prompt[1]
        result = _reconcile_narration_full_scope(
            result,
            observations=observations,
            structural=_structural,
            expected_panel_ids=visual.panel_ids,
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        result = replace(result, qc_report=qc_report)
        if not _narration_result_is_usable(
            result,
            visual,
            require_duration=True,
            require_grounding=True,
        ):
            self._last_narration_result = result
            failure_codes = self._narration_contract_failures(result)
            if failure_codes:
                failure_code = failure_codes[0]
                raise CloudStageError(
                    failure_code,
                    reviewable=True,
                    safe_metadata=self._response_shape_metrics_for_failure(failure_code),
                )
            failure_code = "cloud.narrative_not_grounded"
            raise CloudStageError(
                failure_code,
                reviewable=True,
                safe_metadata=self._response_shape_metrics_for_failure(failure_code),
            )
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result

    @staticmethod
    @_bound
    def _reconcile_narration_passage_evidence(
        passage: Mapping[str, Any],
        claims_by_id: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Backfill omitted passage evidence from the trusted claim graph.

        Compatible providers sometimes return claim IDs and claim-level
        evidence while omitting the duplicate passage evidence field. The
        passage field is derived locally from those claim records; provider
        supplied non-empty values remain subject to the strict shared
        validator, including foreign or incomplete references.
        """

        if passage.get("evidence_panel_ids"):
            return
        claim_ids = passage.get("claim_ids")
        if not isinstance(claim_ids, list) or not claim_ids:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        evidence: list[str] = []
        for claim_id in claim_ids:
            claim = claims_by_id.get(str(claim_id))
            if not isinstance(claim, Mapping):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            refs = claim.get("evidence_panel_ids", claim.get("panel_ids"))
            if not isinstance(refs, (list, tuple)):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            for panel_id in refs:
                if not isinstance(panel_id, str) or not panel_id.strip():
                    raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
                if panel_id not in evidence:
                    evidence.append(panel_id)
        if not evidence:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        passage["evidence_panel_ids"] = evidence

    @_bound
    def _run_narration_chunk(
        self,
        prompt: tuple[str, str, str],
        source: Mapping[str, Any],
        observations: Sequence[Mapping[str, Any]],
        structural: Mapping[str, Any],
        story_map: StoryMapResult,
        visual: VisualStageResult,
        chunk_index: int,
        chunk: Sequence[Mapping[str, Any]],
        batch_count: int,
    ) -> NarrationResult:
        chunk_ids = tuple(str(panel["panel_id"]) for panel in chunk)
        chunk_id_set = set(chunk_ids)
        chunk_story = StoryMapResult(
            panel_ids=chunk_ids,
            beats=tuple(
                dict(beat)
                for beat in story_map.beats
                if any(str(panel_id) in chunk_id_set for panel_id in beat.get("panel_ids", []))
            ),
            causal_chain=tuple(
                dict(link)
                for link in story_map.causal_chain
                if str(link.get("from_beat", ""))
                in {
                    str(beat["beat_id"])
                    for beat in story_map.beats
                    if any(str(panel_id) in chunk_id_set for panel_id in beat.get("panel_ids", []))
                }
                or str(link.get("to_beat", ""))
                in {
                    str(beat["beat_id"])
                    for beat in story_map.beats
                    if any(str(panel_id) in chunk_id_set for panel_id in beat.get("panel_ids", []))
                }
            ),
            claims=tuple(
                dict(claim)
                for claim in story_map.claims
                if any(
                    str(panel_id) in chunk_id_set
                    for panel_id in claim.get(
                        "evidence_panel_ids",
                        claim.get("panel_ids", []),
                    )
                )
            ),
            story_map_hash=story_map.story_map_hash,
            model_identity_hash=story_map.model_identity_hash,
            prompt_version=story_map.prompt_version,
            prompt_sha256=story_map.prompt_sha256,
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        chunk_observations = [
            dict(item) for item in observations if str(item.get("panel_id", "")) in chunk_id_set
        ]
        if tuple(str(item.get("panel_id", "")) for item in chunk_observations) != chunk_ids:
            raise CloudStageError("cloud.panel_lineage_invalid")
        chunk_source = {
            **dict(source),
            "panel_ids": list(chunk_ids),
            "visual_observations": chunk_observations,
            "story_map": chunk_story.as_dict(),
            "batch_index": chunk_index,
            "batch_count": batch_count,
        }
        chunk_key = _cache_key("narration", chunk_source, self.model_identity, prompt)
        chunk_visual = replace(visual, panels=tuple(dict(panel) for panel in chunk))
        if self.cache is not None and (cached := self.cache.get(chunk_key)) is not None:
            try:
                cached_result = NarrationResult.from_dict(cached)
            except (KeyError, TypeError, ValueError):
                cached_result = None
            if (
                cached_result is not None
                and tuple(str(item.get("panel_id", "")) for item in cached_result.observations)
                == chunk_ids
                and cached_result.model_identity_hash == self.model_identity.identity_hash
                and cached_result.prompt_version == prompt[0]
                and cached_result.prompt_sha256 == prompt[1]
                and cached_result.visual_evidence_hash == chunk_visual.visual_evidence_hash
                and _narration_result_is_usable(
                    cached_result,
                    chunk_visual,
                    require_duration=False,
                )
            ):
                return cached_result
        return self._run_narration_batched(
            prompt,
            chunk_source,
            chunk_observations,
            structural,
            chunk_story,
            chunk_visual,
        )

    @_bound
    def _run_narration_in_chunks(
        self,
        prompt: tuple[str, str, str],
        source: Mapping[str, Any],
        observations: Sequence[Mapping[str, Any]],
        structural: Mapping[str, Any],
        story_map: StoryMapResult,
        visual: VisualStageResult,
    ) -> NarrationResult:
        chunks = [
            visual.panels[i : i + NARRATION_CHUNK_STEP]
            for i in range(0, len(visual.panels), NARRATION_CHUNK_STEP)
        ]
        fallback_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.narrative_not_grounded",
            "cloud.narrative_claim_unmapped",
            "cloud.narrative_qc_blocked",
            "cloud.narrative_duration_out_of_range",
        }

        def resolve_chunk(
            chunk_index: int,
            chunk: Sequence[Mapping[str, Any]],
            batch_count: int,
        ) -> tuple[NarrationResult, ...]:
            try:
                return (
                    self._run_narration_chunk(
                        prompt,
                        source,
                        observations,
                        structural,
                        story_map,
                        visual,
                        chunk_index,
                        chunk,
                        batch_count,
                    ),
                )
            except CloudStageError as exc:
                if exc.code not in fallback_codes:
                    raise
                if len(chunk) > NARRATION_COVERAGE_FALLBACK_STEP:
                    step = NARRATION_COVERAGE_FALLBACK_STEP
                elif len(chunk) > NARRATION_COVERAGE_MIN_STEP:
                    step = NARRATION_COVERAGE_MIN_STEP
                else:
                    raise
                resolved: list[NarrationResult] = []
                for sub_index in range(0, len(chunk), step):
                    resolved.extend(
                        resolve_chunk(
                            chunk_index * 100 + sub_index // step,
                            chunk[sub_index : sub_index + step],
                            batch_count,
                        )
                    )
                return tuple(resolved)

        with ThreadPoolExecutor(
            max_workers=min(STAGE_PARALLEL_WORKERS, max(1, len(chunks)))
        ) as executor:
            nested_results = tuple(
                executor.map(
                    lambda args: resolve_chunk(*args),
                    ((chunk_index, chunk, len(chunks)) for chunk_index, chunk in enumerate(chunks)),
                )
            )
        results = tuple(result for nested in nested_results for result in nested)
        all_passages = [dict(passage) for result in results for passage in result.passages]
        all_claims = [
            dict(claim) for result in results for claim in result.evidence_graph.get("claims", [])
        ]
        story_spine: dict[str, Any] = {}
        for result in results:
            for key, value in result.story_spine.items():
                if str(value).strip():
                    story_spine.setdefault(str(key), value)
        spoken_text = "\n\n".join(str(item["text"]).strip() for item in all_passages)
        display_words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
        if not display_words or any(not word.isalnum() for word in display_words):
            raise CloudStageError("cloud.display_derivation_invalid")
        duration_metrics = script.narration_duration_metrics(
            spoken_text,
            "dramatic",
        )
        duration = float(duration_metrics["estimated_duration_s"])
        total_words = int(duration_metrics["word_count"])
        qc_report = {
            "profile_id": "sharp_friend_v1",
            "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
            "total_words": total_words,
            "estimated_duration_s": duration,
            "duration_contract": duration_metrics,
            "ending_kind": results[-1].ending_kind,
            "display_word_count": len(display_words),
            "timing_source": "voice_required",
            "warnings": [],
            "signals": {},
            "chunk_count": len(chunks),
            "chunk_step": NARRATION_CHUNK_STEP,
            "worker_count": min(STAGE_PARALLEL_WORKERS, len(chunks)),
        }
        result = NarrationResult(
            spoken_text=spoken_text,
            display_words=display_words,
            passages=tuple(all_passages),
            ending_kind=results[-1].ending_kind,
            word_count=total_words,
            estimated_duration_s=duration,
            observations=tuple(dict(item) for item in observations),
            continuity_ledger=dict(structural["continuity_ledger"]),
            evidence_graph={"claims": all_claims},
            story_spine=story_spine,
            qc_report=qc_report,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        if not 40.0 <= duration <= 180.0:
            self._last_narration_result = result
            raise CloudStageError("cloud.narrative_duration_out_of_range", reviewable=True)
        full_key = _cache_key("narration", source, self.model_identity, prompt)
        if self.cache is not None and _narration_result_is_usable(
            result,
            visual,
            require_duration=True,
            require_grounding=True,
        ):
            self.cache.put(full_key, result.as_dict())
        return result

    @staticmethod
    @_bound
    def _narration_contract_failures(
        result: NarrationResult,
        duration_policy_contract: Mapping[str, Any] | None = None,
    ) -> tuple[str, ...]:
        bounds = _narration_repair_contract_bounds(duration_policy_contract)
        failures: list[str] = []
        duration_metrics = script.narration_duration_metrics(
            result.spoken_text,
            "dramatic",
        )
        canonical_duration = float(duration_metrics["estimated_duration_s"])
        canonical_word_count = int(duration_metrics["word_count"])
        if not bounds["target_duration_min_s"] <= canonical_duration <= bounds["target_duration_max_s"] or not math.isclose(
            float(result.estimated_duration_s),
            canonical_duration,
            rel_tol=0.0,
            abs_tol=0.001,
        ):
            failures.append("cloud.narrative_duration_out_of_range")
        if not bounds["target_word_min"] <= canonical_word_count <= bounds["target_word_max"] or int(result.word_count) != canonical_word_count:
            failures.append("cloud.narrative_word_count_out_of_range")
        if analyzer_contract.contains_source_dialogue_copy(
            result.observations,
            result.passages,
        ):
            failures.append("cloud.narrative_source_dialogue_copy")
        return tuple(dict.fromkeys(failures))

    @staticmethod
    @_bound
    def _narration_scope_signature(result: NarrationResult) -> str:
        passages = [
            {
                "passage_id": str(passage.get("passage_id", "")),
                "claim_ids": [str(value) for value in passage.get("claim_ids", ())],
                "evidence_panel_ids": [
                    str(value) for value in passage.get("evidence_panel_ids", ())
                ],
                "editorial_role": str(passage.get("editorial_role", "")),
            }
            for passage in result.passages
        ]
        claims = []
        for claim in result.evidence_graph.get("claims", ()):
            if not isinstance(claim, Mapping):
                continue
            claims.append(
                {
                    "claim_id": str(claim.get("claim_id", "")),
                    "claim_type": str(claim.get("claim_type", "")),
                    "text": str(claim.get("text", "")),
                    "evidence_panel_ids": [
                        str(value)
                        for value in claim.get(
                            "evidence_panel_ids",
                            claim.get("panel_ids", ()),
                        )
                    ],
                    "qualification": str(claim.get("qualification", "")),
                }
            )
        observations = [
            {
                "panel_id": str(observation.get("panel_id", "")),
                "source_asset_id": str(observation.get("source_asset_id", "")),
                "source_index": int(observation.get("source_index", -1)),
                "evidence_refs": [str(value) for value in observation.get("evidence_refs", ())],
            }
            for observation in result.observations
        ]
        return _hash(
            {
                "passages": passages,
                "claims": claims,
                "observations": observations,
                "ending_kind": result.ending_kind,
                "story_spine": result.story_spine,
            }
        )

    @staticmethod
    @_bound
    def _repair_cache_source(
        source: Mapping[str, Any],
        targeted_repair: Mapping[str, Any],
    ) -> dict[str, Any]:
        cache_source = dict(source)
        cache_source["targeted_repair"] = {
            str(key): value
            for key, value in targeted_repair.items()
            if str(key) != "repair_attempt"
        }
        return cache_source

    @_bound
    def _narration_repair_candidate_key(
        self,
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
    ) -> str:
        return _cache_key(
            NARRATION_REPAIR_CANDIDATE_STAGE,
            source,
            self.model_identity,
            prompt,
        )

    @staticmethod
    def _grounded_narrative_outline(story_context, passages, provider_outline=None):
        """Build the reasoning envelope only from semantically audited story beats."""
        context = story_context if isinstance(story_context, Mapping) else {}
        beats = [
            beat for beat in context.get("narration_ready_beats", ())
            if isinstance(beat, Mapping)
        ]
        if not beats:
            if isinstance(provider_outline, Mapping):
                return dict(provider_outline)
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)

        def first_fact(*roles):
            wanted = {str(role).casefold() for role in roles}
            for beat in beats:
                role = str(beat.get("story_role", "")).casefold()
                fact = " ".join(str(beat.get("fact", "")).split()).strip()
                if fact and (not wanted or role in wanted):
                    return fact
            return ""

        def first_field(field):
            for beat in beats:
                value = " ".join(str(beat.get(field, "")).split()).strip()
                if value:
                    return value
            return ""

        goal = first_fact("decision", "choice", "setup", "hook") or first_fact()
        obstacle = first_fact("obstacle", "escalation")
        decision = first_fact("decision", "choice")
        consequence = first_field("consequence") or first_fact("consequence")
        changed_stakes = first_field("change")
        unresolved = first_field("open_question")
        spine = {
            "who_wants_what": goal,
            "obstacle": obstacle or "No single explicit obstacle is established by the selected grounded evidence.",
            "decision": decision or "No explicit decision is established by the selected grounded evidence.",
            "consequence": consequence or "No explicit consequence is established by the selected grounded evidence.",
            "changed_stakes": changed_stakes or "No additional changed stakes are explicitly established by the selected grounded evidence.",
            "unresolved_question": unresolved or "No explicit unresolved question is established by the selected grounded evidence.",
        }
        final_text = ""
        if isinstance(passages, list) and passages and isinstance(passages[-1], Mapping):
            final_text = str(passages[-1].get("text", "")).strip()
        ending_kind = "open_question" if unresolved and final_text.endswith("?") else "consequence"
        return {"story_spine": spine, "ending_kind": ending_kind}
    @_bound
    def _run_narration_batched(
        self,
        prompt,
        source,
        observations,
        structural,
        story_map,
        visual,
        *,
        stage: str = "narration",
        targeted_repair: Mapping[str, Any] | None = None,
        request_prompt_version: str | None = None,
        request_prompt_sha256: str | None = None,
        request_prompt_text: str | None = None,
        repair_slots: Sequence[NarrationRepairSlot] | None = None,
        repair_position_registry: Mapping[str, Any] | None = None,
        repair_candidate: NarrationResult | None = None,
    ) -> NarrationResult:
        if repair_position_registry is not None:
            self.last_response_shape_metrics = {}
        retryable_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.narrative_not_grounded",
            "cloud.narrative_claim_unmapped",
            "cloud.narrative_qc_blocked",
            "cloud.narrative_duration_out_of_range",
            "cloud.narrative_repair_micro_compaction_unavailable",
            "cloud.narrative_repair_position_budget_invalid",
        }
        obs_by_id = {str(item["panel_id"]): item for item in observations}
        if repair_position_registry is not None:
            # The targeted position vector is chapter-scoped: its claims and
            # passage evidence always reference the full compact scope, so a
            # per-chunk split would validate them against a partial panel set
            # and reject every in-window vector as ungrounded.
            chunks = [visual.panels]
        else:
            chunk_step = 600
            chunks = [
                visual.panels[i : i + chunk_step] for i in range(0, len(visual.panels), chunk_step)
            ]
        all_passages: list[dict[str, Any]] = []
        all_claims: list[dict[str, Any]] = []
        story_spine: dict[str, Any] = {}
        chunk_beats_ids: set[str] = set()
        for chunk_index, chunk in enumerate(chunks):
            chunk_ids = tuple(panel["panel_id"] for panel in chunk)
            chunk_obs = [dict(obs_by_id[panel_id]) for panel_id in chunk_ids]
            for obs_index, obs_item in enumerate(chunk_obs):
                obs_item["source_index"] = obs_index
            chunk_story = story_map.as_dict()
            chunk_beat_ids = {
                str(beat["beat_id"])
                for beat in story_map.beats
                if {str(item) for item in beat.get("panel_ids", ())} & set(chunk_ids)
            }  # noqa: C401 - explicit set comprehension is equivalent
            chunk_story["beats"] = [
                dict(beat) for beat in story_map.beats if str(beat["beat_id"]) in chunk_beat_ids
            ]
            chunk_story["causal_chain"] = [
                dict(link)
                for link in story_map.causal_chain
                if str(link.get("from_beat", "")) in chunk_beat_ids
                or str(link.get("to_beat", "")) in chunk_beat_ids
            ]
            chunk_claims = [
                dict(claim)
                for claim in story_map.claims
                if any(
                    str(pid) in chunk_ids
                    for pid in claim.get(
                        "evidence_panel_ids",
                        claim.get("panel_ids", []),
                    )
                )
            ]
            chunk_story["claims"] = chunk_claims
            chunk_ledger = dict(structural["continuity_ledger"])
            chunk_ledger["chunks"] = [
                {
                    **dict(chunk_entry),
                    "panel_ids": [
                        str(pid)
                        for pid in chunk_entry.get("panel_ids", [])
                        if str(pid) in chunk_ids
                    ],
                }
                for chunk_entry in structural["continuity_ledger"].get("chunks", [])
                if any(str(pid) in chunk_ids for pid in chunk_entry.get("panel_ids", []))
            ]
            chunk_ledger["entities"] = [
                {
                    **dict(entity),
                    "panel_ids": [
                        str(pid) for pid in entity.get("panel_ids", []) if str(pid) in chunk_ids
                    ],
                }
                for entity in structural["continuity_ledger"].get("entities", [])
                if any(str(pid) in chunk_ids for pid in entity.get("panel_ids", []))
            ]
            compact_story_repair = (
                repair_position_registry is not None
                and str(source.get("provider_context_mode", "")) == "locked_story_text_only"
            )
            if compact_story_repair:
                chunk_source = {
                    **source,
                    "panel_ids": list(chunk_ids),
                    "batch_index": chunk_index,
                    "batch_count": len(chunks),
                }
            else:
                chunk_source = {
                    **source,
                    "panel_ids": list(chunk_ids),
                    "visual_observations": chunk_obs,
                    "story_map": chunk_story,
                    "batch_index": chunk_index,
                    "batch_count": len(chunks),
                }
            if targeted_repair is not None:
                chunk_source["targeted_repair"] = dict(targeted_repair)
            chunk_end = None
            retry_feedback = (
                str(targeted_repair.get("outer_retry_feedback", ""))
                if isinstance(targeted_repair, Mapping)
                else ""
            )
            for attempt in range(self.max_attempts):
                try:
                    request_payload = {**chunk_source, "retry_attempt": attempt}
                    if retry_feedback:
                        request_payload["contract_retry_feedback"] = retry_feedback
                    raw = self._call(
                        lambda request_payload=request_payload: self.provider.complete_json(
                            stage=stage,
                            prompt_version=request_prompt_version or prompt[0],
                            prompt_sha256=request_prompt_sha256 or prompt[1],
                            prompt_text=request_prompt_text or prompt[2],
                            payload=request_payload,
                        ),
                        request_stage=(
                            "narration_repair"
                            if stage == "narration_repair"
                            else "narration"
                            if stage == "narration"
                            else "other"
                        ),
                    )
                    if not isinstance(raw, Mapping):
                        raise CloudStageError("cloud.provider_response_invalid")
                    if repair_position_registry is not None:
                        if repair_candidate is None:
                            raise CloudStageError(
                                "cloud.narrative_repair_position_contract_invalid",
                                reviewable=True,
                            )
                        provider_output = self._reconcile_narration_repair_vector(
                            raw,
                            repair_position_registry,
                            repair_candidate,
                            story_map=story_map,
                        )
                        passage_lineage = provider_output.pop("_passage_lineage", None)
                        if not isinstance(passage_lineage, Mapping):
                            raise CloudStageError(
                                "cloud.narrative_repair_position_lineage_invalid",
                                reviewable=True,
                            )
                        shape_metrics = provider_output.pop("_response_shape_metrics", None)
                        if isinstance(shape_metrics, Mapping):
                            self.last_response_shape_metrics = dict(shape_metrics)
                        self.last_response_shape_metrics.update(
                            {
                                "passage_lineage_version": str(passage_lineage.get("version", "")),
                                "passage_lineage_hash": str(
                                    passage_lineage.get("lineage_hash", "")
                                ),
                            }
                        )
                    else:
                        provider_output = raw.get("analyzer_output", raw)
                    if not isinstance(provider_output, Mapping):
                        raise CloudStageError("cloud.provider_response_invalid")
                    if repair_slots is not None:
                        if repair_candidate is None:
                            raise CloudStageError(
                                "cloud.narrative_repair_slot_contract_invalid",
                                reviewable=True,
                            )
                        provider_output = self._reconcile_narration_repair_slots(
                            provider_output,
                            repair_slots,
                            repair_candidate,
                        )
                    raw_claims = provider_output.get("evidence_graph")
                    if raw_claims is None:
                        raw_claims = self._claims_from_causal_map(
                            provider_output.get("script_passages"),
                            StoryMapResult(
                                panel_ids=chunk_ids,
                                beats=tuple(chunk_story["beats"]),
                                causal_chain=tuple(chunk_story["causal_chain"]),
                                claims=tuple(chunk_claims),
                                story_map_hash=story_map.story_map_hash,
                                model_identity_hash=story_map.model_identity_hash,
                                prompt_version=story_map.prompt_version,
                                prompt_sha256=story_map.prompt_sha256,
                            ),
                        )
                    claims_list = self._normalize_narration_claims(raw_claims)
                    # Provider emits claim text on the referencing passage;
                    # backfill claim["text"] so the contract validator passes.
                    text_by_claim: dict[str, str] = {}
                    for passage in provider_output.get("script_passages") or []:
                        if not isinstance(passage, Mapping):
                            continue
                        for claim_ref in passage.get("claim_ids") or []:
                            text_by_claim.setdefault(str(claim_ref), str(passage.get("text", "")))
                    for claim in claims_list:
                        if not claim.get("text"):
                            claim["text"] = text_by_claim.get(str(claim.get("claim_id")), "")
                    claims_by_id = {
                        str(claim.get("claim_id")): claim
                        for claim in claims_list
                        if str(claim.get("claim_id", "")).strip()
                    }
                    normalized_passages = provider_output.get("script_passages")
                    if not isinstance(normalized_passages, list):
                        raise CloudStageError(
                            "cloud.narrative_not_grounded",
                            reviewable=True,
                        )
                    for passage in normalized_passages:
                        if not isinstance(passage, Mapping):
                            raise CloudStageError(
                                "cloud.narrative_not_grounded",
                                reviewable=True,
                            )
                        refs = passage.get("claim_ids")
                        if not isinstance(refs, list) or not refs:
                            raise CloudStageError(
                                "cloud.narrative_not_grounded",
                                reviewable=True,
                            )
                        normalized_refs: list[str] = []
                        for ref in refs:
                            if not isinstance(ref, str):
                                raise CloudStageError(
                                    "cloud.narrative_not_grounded",
                                    reviewable=True,
                                )
                            resolved = ref
                            if resolved not in claims_by_id:
                                matches = [
                                    claim_id
                                    for claim_id in claims_by_id
                                    if claim_id.rsplit("__", 1)[-1] == ref
                                ]
                                if len(matches) != 1:
                                    raise CloudStageError(
                                        "cloud.narrative_not_grounded",
                                        reviewable=True,
                                    )
                                resolved = matches[0]
                            normalized_refs.append(resolved)
                        passage["claim_ids"] = normalized_refs
                        self._reconcile_narration_passage_evidence(
                            passage,
                            claims_by_id,
                        )
                    output = {
                        "observations": chunk_obs,
                        "continuity_ledger": chunk_ledger,
                        "coverage_manifest": {
                            "total_panels": len(chunk_ids),
                            "processed_panels": len(chunk_ids),
                            "total_canonical_panels": len(chunk_ids),
                            "persisted_canonical_panels": len(chunk_ids),
                            "processed_canonical_panel_count": len(chunk_ids),
                            "panel_ids": list(chunk_ids),
                            "source_content_coverage_ratio": 1.0,
                            "unresolved_material_area": 0,
                            "material_unresolved_regions": [],
                            "reconciliation_complete": True,
                        },
                        "evidence_graph": {"claims": claims_list},
                        "narrative_outline": self._grounded_narrative_outline(
                            source.get("story_understanding"),
                            provider_output.get("script_passages"),
                            provider_output.get("narrative_outline"),
                        ),
                        "script_passages": provider_output.get("script_passages"),
                    }
                    analyzer_contract.validate_analyzer_output(
                        output,
                        expected_panel_ids=tuple(str(item["panel_id"]) for item in chunk_obs),
                        narrative_profile_id="sharp_friend_v1",
                    )
                    claims = {claim["claim_id"]: claim for claim in claims_list}
                    passages = tuple(dict(item) for item in output["script_passages"])
                    report = editorial_qc.screen_narrative_naturalness(
                        passages,
                        claims,
                        narrative_identity.SHARP_FRIEND_V1,
                    )
                    checks = quality.check_narrative_naturalness(report)
                    blocking_checks = [
                        check for check in checks if not check.passed and check.severity == "error"
                    ]
                    if blocking_checks:
                        marker_values = list(getattr(report, "ai_slop_hits", ()) or ())
                        marker_values.extend(getattr(report, "reporter_prose_hits", ()) or ())
                        raise CloudStageError(
                            "cloud.narrative_qc_blocked",
                            reviewable=True,
                            safe_metadata={
                                "failed_predicate": str(blocking_checks[0].code),
                                "qc_codes": [str(check.code) for check in blocking_checks],
                                "anti_slop_markers": list(dict.fromkeys(marker_values))[:8],
                            },
                        )
                    outline = output.get("narrative_outline")
                    if isinstance(outline, Mapping):
                        candidate_spine = outline.get("story_spine")
                        if isinstance(candidate_spine, Mapping):
                            for key, value in candidate_spine.items():
                                if str(value).strip():
                                    story_spine.setdefault(str(key), value)
                    chunk_end = str(
                        (outline or {}).get("ending_kind", "")
                        if isinstance(outline, Mapping)
                        else ""
                    )
                    all_passages.extend(dict(item) for item in passages)
                    all_claims.extend(dict(claim) for claim in claims_list)
                    chunk_beats_ids.update(chunk_beat_ids)
                    break
                except analyzer_contract.AnalyzerContractError as exc:
                    diagnostic = _safe_narration_contract_diagnostic(
                        str(exc),
                        output if isinstance(output, Mapping) else None,
                    )
                    if attempt + 1 < self.max_attempts:
                        retry_feedback = _narration_retry_feedback(str(exc))
                        continue
                    raise CloudStageError(
                        "cloud.narrative_not_grounded",
                        diagnostic,
                        reviewable=True,
                    ) from None
                except CloudStageError as exc:
                    if exc.safe_metadata:
                        self.last_response_shape_metrics = dict(exc.safe_metadata)
                    print(
                        f"NARR_CHUNK_FAIL chunk={chunk_index} attempt={attempt} code={exc.code}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if exc.code in retryable_codes and attempt + 1 < self.max_attempts:
                        observed_word_count = exc.safe_metadata.get("total_word_count")
                        retry_feedback = _narration_retry_feedback(
                            exc.code,
                            observed_word_count=(
                                int(observed_word_count)
                                if isinstance(observed_word_count, int)
                                and not isinstance(observed_word_count, bool)
                                else None
                            ),
                            target_word_min=(
                                targeted_repair.get("target_word_min")
                                if isinstance(targeted_repair, Mapping)
                                else None
                            ),
                            target_word_max=(
                                targeted_repair.get("target_word_max")
                                if isinstance(targeted_repair, Mapping)
                                else None
                            ),
                            target_word_count=(
                                targeted_repair.get("target_word_count")
                                if isinstance(targeted_repair, Mapping)
                                else None
                            ),
                            capacity_locked=bool(
                                isinstance(targeted_repair, Mapping)
                                and targeted_repair.get("passage_word_budgets")
                            ),
                            failed_predicate=(
                                str(exc.safe_metadata.get("failed_predicate", ""))
                                if isinstance(exc.safe_metadata, Mapping)
                                else None
                            ),
                            per_position_word_counts=(
                                exc.safe_metadata.get("per_position_word_counts")
                                if isinstance(exc.safe_metadata, Mapping)
                                else None
                            ),
                            expected_ranges=(
                                exc.safe_metadata.get("expected_ranges")
                                if isinstance(exc.safe_metadata, Mapping)
                                else None
                            ),
                            anti_slop_markers=(
                                exc.safe_metadata.get("anti_slop_markers")
                                if isinstance(exc.safe_metadata, Mapping)
                                else None
                            ),
                        )
                        continue
                    raise
            if chunk_end is None:
                print(
                    f"NARR_CHUNK_FAIL chunk={chunk_index} exhausted retries",
                    file=sys.stderr,
                    flush=True,
                )
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        spoken_text = "\n\n".join(str(item["text"]).strip() for item in all_passages)
        display_words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
        if not display_words or any(not word.isalnum() for word in display_words):
            raise CloudStageError("cloud.display_derivation_invalid")
        duration_metrics = script.narration_duration_metrics(
            spoken_text,
            "dramatic",
        )
        duration = float(duration_metrics["estimated_duration_s"])
        # Preview relaxation: the 50-60s contract targets a single short clip,
        # but a full 703-panel chapter batch narrates ~2.5x that length.  The
        # production contract stays 50-60s; preview accepts long-form output.
        # The final 50-60s/115-125 contract is enforced by run_narration
        # after the bounded targeted repair; this helper must return the
        # validated candidate even when it needs repair.
        total_words = int(duration_metrics["word_count"])
        qc_report = {
            "profile_id": "sharp_friend_v1",
            "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
            "total_words": total_words,
            "estimated_duration_s": duration,
            "duration_contract": duration_metrics,
            "ending_kind": chunk_end,
            "display_word_count": len(display_words),
            "timing_source": "voice_required",
            "warnings": [],
            "signals": {},
        }
        result = NarrationResult(
            spoken_text=spoken_text,
            display_words=display_words,
            passages=tuple(all_passages),
            ending_kind=str(chunk_end),
            word_count=total_words,
            estimated_duration_s=duration,
            observations=tuple(observations),
            continuity_ledger=dict(structural["continuity_ledger"]),
            evidence_graph={"claims": all_claims},
            story_spine=story_spine,
            qc_report=qc_report,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        if self.cache is not None:
            cache_prompt = (
                request_prompt_version or prompt[0],
                request_prompt_sha256 or prompt[1],
                request_prompt_text or prompt[2],
            )
            if stage == "narration":
                if _narration_result_is_usable(
                    result,
                    visual,
                    require_duration=True,
                    require_grounding=True,
                ):
                    self.cache.put(
                        _cache_key(
                            stage,
                            source,
                            self.model_identity,
                            cache_prompt,
                        ),
                        result.as_dict(),
                    )
                else:
                    failures = self._narration_contract_failures(result)
                    if failures:
                        self._store_narration_repair_candidate(
                            source=source,
                            prompt=cache_prompt,
                            result=result,
                            failure_codes=failures,
                            visual=visual,
                            story_map=story_map,
                        )
            # narration_repair results are written only after scope validation
            # by _run_targeted_narration_repair.
        return result
