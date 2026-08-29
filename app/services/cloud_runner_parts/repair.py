"""Repair methods extracted from cloud_multimodal."""

# ruff: noqa: F821 -- runtime globals are refreshed from the compatibility facade.
from __future__ import annotations

from app.constants import (
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
)

from .runtime import runtime_bound

_RUNTIME_NAMES = (
    'CloudStageError',
    'CloudStageRunner',
    'EDITORIAL_SELECTION_VERSION',
    'Mapping',
    'NARRATION_MICRO_COMPACTION_VERSION',
    'NARRATION_REPAIR_CANDIDATE_VERSION',
    'NARRATION_REPAIR_EVIDENCE_CLOSURE_VERSION',
    'NARRATION_REPAIR_IDENTITY_MIGRATION_VERSION',
    'NARRATION_REPAIR_IDENTITY_VERSION',
    'NARRATION_REPAIR_INSTRUCTION',
    'NARRATION_REPAIR_MAX_ATTEMPTS',
    'NARRATION_REPAIR_PASSAGE_LINEAGE_VERSION',
    'NARRATION_REPAIR_POSITION_DOMINANCE_FLOOR',
    'NARRATION_REPAIR_POSITION_MAX_ATTEMPTS',
    'NARRATION_REPAIR_POSITION_MAX_COUNT',
    'NARRATION_REPAIR_POSITION_MAX_SHARE',
    'NARRATION_REPAIR_POSITION_MIN_COUNT',
    'NARRATION_REPAIR_POSITION_REGISTRY_VERSION',
    'NARRATION_REPAIR_RESULT_VERSION',
    'NARRATION_REPAIR_SLOT_REGISTRY_VERSION',
    'NARRATION_REPAIR_VERSION',
    'NarrationRepairPosition',
    'NarrationRepairSlot',
    'NarrationResult',
    'Sequence',
    'StoryMapResult',
    '_cache_key',
    '_hash',
    '_micro_compact_rewrites',
    '_narration_repair_contract_bounds',
    '_narration_repair_provider_prior_context',
    '_narration_result_is_usable',
    '_narration_retry_feedback',
    '_normalize_locked_story_budget',
    '_position_word_budget_bounds',
    '_stage_result_identity_is_compatible',
    'math',
    'persist_narration_repair_identity_migration',
    'reconcile_narration_repair_identity',
    'replace',
    'script',
)
_bound = runtime_bound(_RUNTIME_NAMES)


class NarrationRepairMixin:
    @staticmethod
    @_bound
    def _compact_narration_repair_context(
        candidate: NarrationResult,
        visual: VisualStageResult,
        story_map: StoryMapResult,
    ) -> tuple[VisualStageResult, StoryMapResult]:
        """Derive the exact selected context for a durable repair candidate."""

        candidate_panel_ids = tuple(
            str(item.get("panel_id", ""))
            for item in candidate.observations
            if isinstance(item, Mapping)
        )
        if (
            not candidate_panel_ids
            or len(candidate_panel_ids) != len(set(candidate_panel_ids))
            or any(panel_id not in visual.panel_ids for panel_id in candidate_panel_ids)
        ):
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        if story_map.visual_evidence_hash not in {
            "",
            visual.visual_evidence_hash,
        }:
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        if candidate_panel_ids == visual.panel_ids:
            compact_visual = visual
        else:
            visual_by_id = {str(item.get("panel_id", "")): item for item in visual.panels}
            compact_visual = replace(
                visual,
                panels=tuple(visual_by_id[panel_id] for panel_id in candidate_panel_ids),
            )
        selected_panel_ids = set(candidate_panel_ids)
        compact_beats: list[dict[str, Any]] = []
        for beat in story_map.beats:
            panel_ids = [
                str(panel_id)
                for panel_id in beat.get("panel_ids", ())
                if str(panel_id) in selected_panel_ids
            ]
            if panel_ids:
                row = dict(beat)
                row["panel_ids"] = panel_ids
                compact_beats.append(row)
        compact_beat_ids = {str(beat.get("beat_id", "")) for beat in compact_beats}
        compact_chain = tuple(
            dict(link)
            for link in story_map.causal_chain
            if str(link.get("from_beat", "")) in compact_beat_ids
            and str(link.get("to_beat", "")) in compact_beat_ids
        )
        compact_claims: list[dict[str, Any]] = []
        for claim in story_map.claims:
            key = "evidence_panel_ids" if "evidence_panel_ids" in claim else "panel_ids"
            refs = [
                str(panel_id)
                for panel_id in claim.get(key, ())
                if str(panel_id) in selected_panel_ids
            ]
            if refs:
                row = dict(claim)
                row[key] = refs
                compact_claims.append(row)
        compact_story = StoryMapResult(
            panel_ids=candidate_panel_ids,
            beats=tuple(compact_beats),
            causal_chain=compact_chain,
            claims=tuple(compact_claims),
            story_map_hash=_hash(
                {
                    "panel_ids": list(candidate_panel_ids),
                    "beats": compact_beats,
                    "claims": compact_claims,
                    "chain": list(compact_chain),
                }
            ),
            model_identity_hash=story_map.model_identity_hash,
            prompt_version=story_map.prompt_version,
            prompt_sha256=story_map.prompt_sha256,
            visual_evidence_hash=compact_visual.visual_evidence_hash,
        )
        if candidate.visual_evidence_hash != compact_visual.visual_evidence_hash:
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        return compact_visual, compact_story

    @staticmethod
    @_bound
    def _narration_repair_lineage_identity(candidate: NarrationResult) -> str:
        """Hash repair context while excluding replaceable citation surfaces."""

        passages = []
        for passage in candidate.passages:
            if not isinstance(passage, Mapping):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            passages.append(
                {
                    "passage_id": str(passage.get("passage_id", "")),
                    "editorial_role": str(passage.get("editorial_role", "")),
                    "text": str(passage.get("text", "")),
                    "claim_ids": [str(value) for value in passage.get("claim_ids", ())],
                }
            )
        claims = []
        raw_claims = candidate.evidence_graph.get("claims", ())
        if not isinstance(raw_claims, (list, tuple)):
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        for claim in raw_claims:
            if not isinstance(claim, Mapping):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            claims.append(
                {
                    "claim_id": str(claim.get("claim_id", "")),
                    "claim_type": str(claim.get("claim_type", "")),
                    "text": str(claim.get("text", "")),
                    "qualification": str(claim.get("qualification", "")),
                }
            )
        return _hash(
            {
                "version": NARRATION_REPAIR_EVIDENCE_CLOSURE_VERSION,
                "spoken_text": candidate.spoken_text,
                "ending_kind": candidate.ending_kind,
                "story_spine": dict(candidate.story_spine),
                "passages": passages,
                "claims": claims,
                "visual_evidence_hash": candidate.visual_evidence_hash,
                "model_identity_hash": candidate.model_identity_hash,
                "prompt_version": candidate.prompt_version,
                "prompt_sha256": candidate.prompt_sha256,
            }
        )

    @staticmethod
    @_bound
    def _story_evidence_panel_closure(
        story_map: StoryMapResult,
        claim_refs: Sequence[str],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Resolve exact claim refs to beat sections and permitted panels."""

        story_panel_ids = {str(panel_id) for panel_id in story_map.panel_ids}
        sections: set[str] = set()
        for panel_id in claim_refs:
            if not panel_id or panel_id not in story_panel_ids:
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            matching_sections = {
                str(beat.get("beat_id", "")).split("__", 1)[0]
                for beat in story_map.beats
                if isinstance(beat, Mapping)
                and panel_id in {str(value) for value in beat.get("panel_ids", ())}
                and str(beat.get("beat_id", "")).strip()
            }
            if not matching_sections:
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            sections.update(matching_sections)
        if not sections:
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        permitted = tuple(
            panel_id
            for panel_id in story_map.panel_ids
            if any(
                section in sections
                for section in {
                    str(beat.get("beat_id", "")).split("__", 1)[0]
                    for beat in story_map.beats
                    if isinstance(beat, Mapping)
                    and str(panel_id) in {str(value) for value in beat.get("panel_ids", ())}
                    and str(beat.get("beat_id", "")).strip()
                }
            )
        )
        if not permitted:
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        return tuple(sorted(sections)), tuple(str(value) for value in permitted)

    @staticmethod
    @_bound
    def _story_passage_evidence_closure(
        story_map: StoryMapResult,
        passage_claim_ids: Sequence[str],
        story_claims: Mapping[str, Mapping[str, Any]],
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Resolve every claim in one trusted passage to its section closure."""

        claim_ids = tuple(str(value).strip() for value in passage_claim_ids)
        if (
            not claim_ids
            or any(not value for value in claim_ids)
            or len(set(claim_ids)) != len(claim_ids)
        ):
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        trusted_refs: list[str] = []
        for claim_id in claim_ids:
            claim = story_claims.get(claim_id)
            if claim is None:
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            raw_refs = claim.get(
                "evidence_panel_ids",
                claim.get("panel_ids", ()),
            )
            if not isinstance(raw_refs, (list, tuple)):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            refs = tuple(str(value).strip() for value in raw_refs)
            if not refs or any(not value for value in refs) or len(set(refs)) != len(refs):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            for panel_id in refs:
                if panel_id not in trusted_refs:
                    trusted_refs.append(panel_id)
        sections, permitted = CloudStageRunner._story_evidence_panel_closure(
            story_map,
            trusted_refs,
        )
        return tuple(trusted_refs), sections, permitted

    @staticmethod
    @_bound
    def _build_narration_repair_slots(
        candidate: NarrationResult,
        story_map: StoryMapResult,
        *,
        preserve_candidate_evidence: bool = False,
    ) -> tuple[NarrationRepairSlot, ...]:
        """Create stable local slot identities from already grounded records."""

        candidate_passages = tuple(candidate.passages)
        if len(candidate_passages) < 4:
            raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
        candidate_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        story_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        story_panel_ids = {str(panel_id) for panel_id in story_map.panel_ids}
        removable_passage_ids = set(CloudStageRunner._removable_narration_passage_ids(candidate))
        slots: list[NarrationRepairSlot] = []
        seen_passage_ids: set[str] = set()
        for passage_index, passage in enumerate(candidate_passages):
            if not isinstance(passage, Mapping):
                raise CloudStageError(
                    "cloud.narrative_repair_slot_lineage_invalid", reviewable=True
                )
            passage_id = str(passage.get("passage_id", "")).strip()
            claim_ids_raw = passage.get("claim_ids")
            evidence_panel_ids_raw = passage.get("evidence_panel_ids")
            if (
                not passage_id
                or passage_id in seen_passage_ids
                or not isinstance(claim_ids_raw, list)
                or not isinstance(evidence_panel_ids_raw, list)
                or not claim_ids_raw
                or not evidence_panel_ids_raw
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_slot_lineage_invalid", reviewable=True
                )
            seen_passage_ids.add(passage_id)
            claim_ids = tuple(str(value) for value in claim_ids_raw)
            candidate_evidence_panel_ids = tuple(str(value) for value in evidence_panel_ids_raw)
            if (
                any(not value.strip() for value in claim_ids)
                or any(
                    not value.strip() or value not in story_panel_ids
                    for value in candidate_evidence_panel_ids
                )
                or len(set(claim_ids)) != len(claim_ids)
                or len(set(candidate_evidence_panel_ids)) != len(candidate_evidence_panel_ids)
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_slot_lineage_invalid", reviewable=True
                )
            trusted_evidence_panel_ids: list[str] = []
            for claim_id in claim_ids:
                candidate_claim = candidate_claims.get(claim_id)
                story_claim = story_claims.get(claim_id)
                if candidate_claim is None or story_claim is None:
                    raise CloudStageError(
                        "cloud.narrative_repair_slot_lineage_invalid", reviewable=True
                    )
                claim_refs = tuple(
                    str(value)
                    for value in story_claim.get(
                        "evidence_panel_ids",
                        story_claim.get("panel_ids", ()),
                    )
                )
                if (
                    not claim_refs
                    or any(
                        not value.strip() or value not in story_panel_ids for value in claim_refs
                    )
                    or len(set(claim_refs)) != len(claim_refs)
                    or not set(claim_refs) & set(candidate_evidence_panel_ids)
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_slot_lineage_invalid", reviewable=True
                    )
                for panel_id in claim_refs:
                    if panel_id not in trusted_evidence_panel_ids:
                        trusted_evidence_panel_ids.append(panel_id)
            _, permitted_panel_ids = CloudStageRunner._story_evidence_panel_closure(
                story_map,
                trusted_evidence_panel_ids,
            )
            if not set(candidate_evidence_panel_ids).issubset(set(permitted_panel_ids)):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            evidence_panel_ids = (
                candidate_evidence_panel_ids
                if preserve_candidate_evidence
                else tuple(trusted_evidence_panel_ids)
            )
            matching_beats = [
                (beat_index, beat)
                for beat_index, beat in enumerate(story_map.beats)
                if isinstance(beat, Mapping)
                and {str(value) for value in beat.get("panel_ids", ())} & set(evidence_panel_ids)
            ]
            if not matching_beats:
                raise CloudStageError(
                    "cloud.narrative_repair_slot_lineage_invalid", reviewable=True
                )
            causal_position, beat = matching_beats[0]
            beat_id = str(beat.get("beat_id", "")).strip()
            if not beat_id:
                raise CloudStageError(
                    "cloud.narrative_repair_slot_lineage_invalid", reviewable=True
                )
            priority = (
                len(claim_ids) * 1000
                + len(evidence_panel_ids) * 10
                + len(candidate_passages)
                - passage_index
            )
            identity_payload = {
                "version": NARRATION_REPAIR_SLOT_REGISTRY_VERSION,
                "candidate_visual_evidence_hash": candidate.visual_evidence_hash,
                "story_map_hash": story_map.story_map_hash,
                "passage_id": passage_id,
                "claim_ids": list(claim_ids),
                "evidence_panel_ids": list(evidence_panel_ids),
                "beat_id": beat_id,
                "causal_position": causal_position,
            }
            slots.append(
                NarrationRepairSlot(
                    slot_id=f"narration_slot_v1_{_hash(identity_payload)}",
                    passage_id=passage_id,
                    claim_ids=claim_ids,
                    evidence_panel_ids=evidence_panel_ids,
                    beat_id=beat_id,
                    causal_position=causal_position,
                    priority=priority,
                    removable=passage_id in removable_passage_ids,
                )
            )
        return tuple(slots)

    @staticmethod
    @_bound
    def _narration_repair_slot_registry(
        slots: Sequence[NarrationRepairSlot],
    ) -> dict[str, Any]:
        slot_rows = [slot.as_dict() for slot in slots]
        registry_identity = {
            "version": NARRATION_REPAIR_SLOT_REGISTRY_VERSION,
            "slots": slot_rows,
        }
        return {
            **registry_identity,
            "slot_ids": [slot.slot_id for slot in slots],
            "removable_slot_ids": [slot.slot_id for slot in slots if slot.removable],
            "registry_hash": _hash(registry_identity),
        }

    @staticmethod
    @_bound
    def _narration_repair_evidence_closure(
        positions: Sequence[NarrationRepairPosition | Mapping[str, Any]],
        candidate: NarrationResult,
        story_map: StoryMapResult,
        *,
        allow_claim_evidence_subset: bool = False,
    ) -> dict[str, Any]:
        """Build the exact panel/section closure for selected claim positions."""

        candidate_passages = {
            str(passage.get("passage_id", "")): passage
            for passage in candidate.passages
            if isinstance(passage, Mapping)
        }
        story_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in story_map.claims
            if isinstance(claim, Mapping)
        }
        rows: list[dict[str, Any]] = []
        permitted_panel_ids: list[str] = []
        for value in positions:
            row = value.as_dict() if isinstance(value, NarrationRepairPosition) else dict(value)
            passage_id = str(row.get("passage_id", "")).strip()
            claim_ids = tuple(str(item) for item in row.get("claim_ids", ()))
            evidence_panel_ids = tuple(str(item) for item in row.get("evidence_panel_ids", ()))
            passage = candidate_passages.get(passage_id)
            passage_claim_ids = (
                tuple(str(item) for item in passage.get("claim_ids", ()))
                if passage is not None
                else ()
            )
            context_panel_ids = (
                tuple(str(item) for item in passage.get("evidence_panel_ids", ()))
                if passage is not None
                else ()
            )
            if (
                passage is None
                or not passage_id
                or not claim_ids
                or len(set(claim_ids)) != len(claim_ids)
                or not passage_claim_ids
                or len(set(passage_claim_ids)) != len(passage_claim_ids)
                or not set(claim_ids).issubset(set(passage_claim_ids))
                or not evidence_panel_ids
                or len(set(evidence_panel_ids)) != len(evidence_panel_ids)
                or not context_panel_ids
                or len(set(context_panel_ids)) != len(context_panel_ids)
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            _, passage_sections, passage_permitted = (
                CloudStageRunner._story_passage_evidence_closure(
                    story_map,
                    passage_claim_ids,
                    story_claims,
                )
            )
            trusted_claim_refs: list[str] = []
            for claim_id in claim_ids:
                claim_refs, _, _ = CloudStageRunner._story_passage_evidence_closure(
                    story_map,
                    (claim_id,),
                    story_claims,
                )
                selected_claim_refs = (
                    tuple(ref for ref in claim_refs if ref in set(evidence_panel_ids))
                    if allow_claim_evidence_subset
                    else claim_refs
                )
                if (
                    not selected_claim_refs
                    or (
                        not allow_claim_evidence_subset
                        and not set(claim_refs).issubset(set(evidence_panel_ids))
                    )
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
                for panel_id in selected_claim_refs:
                    if panel_id not in trusted_claim_refs:
                        trusted_claim_refs.append(panel_id)
            if (
                not set(context_panel_ids).issubset(set(passage_permitted))
                or not set(evidence_panel_ids).issubset(set(passage_permitted))
                or set(evidence_panel_ids) != set(trusted_claim_refs)
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            row = {
                "position": row.get("position"),
                "slot_id": str(row.get("slot_id", "")),
                "passage_id": passage_id,
                "claim_ids": list(claim_ids),
                "evidence_panel_ids": list(evidence_panel_ids),
                "requested_context_panel_ids": list(context_panel_ids),
                "beat_id": str(row.get("beat_id", "")),
                "causal_position": row.get("causal_position"),
                "section_keys": list(passage_sections),
                "permitted_panel_ids": list(passage_permitted),
            }
            rows.append(row)
            for panel_id in passage_permitted:
                if panel_id not in permitted_panel_ids:
                    permitted_panel_ids.append(panel_id)
        if not rows:
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        identity = {
            "version": NARRATION_REPAIR_EVIDENCE_CLOSURE_VERSION,
            "evidence_scope_mode": (
                "candidate_passage_locked"
                if allow_claim_evidence_subset
                else "full_claim_closure"
            ),
            "lineage_candidate_hash": CloudStageRunner._narration_repair_lineage_identity(
                candidate
            ),
            "candidate_visual_evidence_hash": candidate.visual_evidence_hash,
            "candidate_model_identity_hash": candidate.model_identity_hash,
            "candidate_prompt_version": candidate.prompt_version,
            "candidate_prompt_sha256": candidate.prompt_sha256,
            "story_map_hash": story_map.story_map_hash,
            "story_model_identity_hash": story_map.model_identity_hash,
            "story_prompt_version": story_map.prompt_version,
            "story_prompt_sha256": story_map.prompt_sha256,
            "story_visual_evidence_hash": story_map.visual_evidence_hash,
            "story_panel_ids": [str(value) for value in story_map.panel_ids],
            "positions": rows,
            "permitted_panel_ids": permitted_panel_ids,
        }
        return {
            **identity,
            "closure_hash": _hash(identity),
        }

    @staticmethod
    @_bound
    def _validate_narration_repair_evidence_closure(
        registry: Mapping[str, Any],
        candidate: NarrationResult,
        story_map: StoryMapResult | None = None,
    ) -> Mapping[str, Any]:
        closure = registry.get("evidence_closure")
        if not isinstance(closure, Mapping):
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        closure_hash = str(closure.get("closure_hash", ""))
        identity = {str(key): value for key, value in closure.items() if key != "closure_hash"}
        if (
            closure.get("version") != NARRATION_REPAIR_EVIDENCE_CLOSURE_VERSION
            or not closure_hash
            or _hash(identity) != closure_hash
            or closure.get("lineage_candidate_hash")
            != CloudStageRunner._narration_repair_lineage_identity(candidate)
            or closure.get("candidate_visual_evidence_hash") != candidate.visual_evidence_hash
            or closure.get("candidate_model_identity_hash") != candidate.model_identity_hash
            or closure.get("candidate_prompt_version") != candidate.prompt_version
            or closure.get("candidate_prompt_sha256") != candidate.prompt_sha256
            or registry.get("evidence_closure_hash") != closure_hash
        ):
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        evidence_scope_mode = str(closure.get("evidence_scope_mode", "full_claim_closure"))
        if evidence_scope_mode not in {"full_claim_closure", "candidate_passage_locked"}:
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        allow_claim_evidence_subset = evidence_scope_mode == "candidate_passage_locked"
        if story_map is not None:
            if (
                closure.get("story_map_hash") != story_map.story_map_hash
                or closure.get("story_model_identity_hash") != story_map.model_identity_hash
                or closure.get("story_prompt_version") != story_map.prompt_version
                or closure.get("story_prompt_sha256") != story_map.prompt_sha256
                or closure.get("story_visual_evidence_hash") != story_map.visual_evidence_hash
                or closure.get("story_panel_ids") != [str(value) for value in story_map.panel_ids]
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            story_claims = {
                str(claim.get("claim_id", "")): claim
                for claim in story_map.claims
                if isinstance(claim, Mapping)
            }
            candidate_passages = {
                str(passage.get("passage_id", "")): passage
                for passage in candidate.passages
                if isinstance(passage, Mapping)
            }
            for row in closure.get("positions", ()):
                if not isinstance(row, Mapping):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
                passage = candidate_passages.get(str(row.get("passage_id", "")).strip())
                raw_passage_claim_ids = passage.get("claim_ids") if passage is not None else None
                raw_claim_ids = row.get("claim_ids")
                raw_evidence_panel_ids = row.get("evidence_panel_ids")
                raw_context_panel_ids = row.get("requested_context_panel_ids")
                if not all(
                    isinstance(value, (list, tuple))
                    for value in (
                        raw_passage_claim_ids,
                        raw_claim_ids,
                        raw_evidence_panel_ids,
                        raw_context_panel_ids,
                    )
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
                passage_claim_ids = tuple(str(item).strip() for item in raw_passage_claim_ids)
                claim_ids = tuple(str(item).strip() for item in raw_claim_ids)
                evidence_panel_ids = tuple(str(item).strip() for item in raw_evidence_panel_ids)
                context_panel_ids = tuple(str(item).strip() for item in raw_context_panel_ids)
                if (
                    not passage_claim_ids
                    or not claim_ids
                    or not evidence_panel_ids
                    or not context_panel_ids
                    or any(
                        not value
                        for value in (
                            *passage_claim_ids,
                            *claim_ids,
                            *evidence_panel_ids,
                            *context_panel_ids,
                        )
                    )
                    or len(set(passage_claim_ids)) != len(passage_claim_ids)
                    or len(set(claim_ids)) != len(claim_ids)
                    or len(set(evidence_panel_ids)) != len(evidence_panel_ids)
                    or not set(claim_ids).issubset(set(passage_claim_ids))
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
                _, passage_sections, passage_permitted = (
                    CloudStageRunner._story_passage_evidence_closure(
                        story_map,
                        passage_claim_ids,
                        story_claims,
                    )
                )
                trusted_refs = []
                for claim_id in claim_ids:
                    claim_refs, _, _ = CloudStageRunner._story_passage_evidence_closure(
                        story_map,
                        (claim_id,),
                        story_claims,
                    )
                    selected_claim_refs = (
                        tuple(ref for ref in claim_refs if ref in set(evidence_panel_ids))
                        if allow_claim_evidence_subset
                        else claim_refs
                    )
                    if (
                        not selected_claim_refs
                        or (
                            not allow_claim_evidence_subset
                            and not set(claim_refs).issubset(set(evidence_panel_ids))
                        )
                    ):
                        raise CloudStageError(
                            "cloud.narrative_repair_evidence_closure_invalid",
                            reviewable=True,
                        )
                    trusted_refs.extend(
                        item for item in selected_claim_refs if item not in trusted_refs
                    )
                if (
                    not set(context_panel_ids).issubset(set(passage_permitted))
                    or not set(evidence_panel_ids).issubset(set(passage_permitted))
                    or set(trusted_refs) != set(evidence_panel_ids)
                    or list(passage_sections) != list(row.get("section_keys", ()))
                    or list(passage_permitted) != list(row.get("permitted_panel_ids", ()))
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
        return closure

    @_bound
    def _narration_repair_position_registry(
        self,
        positions: Sequence[NarrationRepairPosition | Mapping[str, Any]],
        candidate: NarrationResult,
        story_map: StoryMapResult,
        *,
        prompt: tuple[str, str, str] | None = None,
        allow_claim_evidence_subset: bool = False,
        allow_hook_teaser: bool = False,
        duration_policy_contract: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Canonicalize the ordered local rewrite registry and its cache identity."""

        bounds = _narration_repair_contract_bounds(duration_policy_contract)
        canonical_positions: list[NarrationRepairPosition] = []
        for position_index, value in enumerate(positions):
            try:
                position = (
                    value
                    if isinstance(value, NarrationRepairPosition)
                    else NarrationRepairPosition(
                        position=int(value["position"]),
                        slot_id=str(value["slot_id"]),
                        passage_id=str(value["passage_id"]),
                        claim_ids=tuple(str(item) for item in value["claim_ids"]),
                        evidence_panel_ids=tuple(str(item) for item in value["evidence_panel_ids"]),
                        beat_id=str(value["beat_id"]),
                        causal_position=int(value["causal_position"]),
                        priority=int(value["priority"]),
                        removable=bool(value["removable"]),
                        word_budget=int(value["word_budget"]),
                        word_budget_min=int(
                            value.get(
                                "word_budget_min",
                                _position_word_budget_bounds(int(value["word_budget"]))[0],
                            )
                        ),
                        word_budget_max=int(
                            value.get(
                                "word_budget_max",
                                _position_word_budget_bounds(int(value["word_budget"]))[1],
                            )
                        ),
                    )
                )
                position = replace(position, position=position_index)
            except (KeyError, TypeError, ValueError):
                raise CloudStageError(
                    "cloud.narrative_repair_position_selection_invalid",
                    reviewable=True,
                ) from None
            canonical_positions.append(position)
        if (
            not NARRATION_REPAIR_POSITION_MIN_COUNT
            <= len(canonical_positions)
            <= NARRATION_REPAIR_POSITION_MAX_COUNT
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        causal_positions = [item.causal_position for item in canonical_positions]
        chronology_positions = causal_positions[1:] if allow_hook_teaser else causal_positions
        if chronology_positions != sorted(chronology_positions):
            raise CloudStageError(
                "cloud.narrative_repair_position_order_invalid",
                reviewable=True,
            )
        if len({item.slot_id for item in canonical_positions}) != len(canonical_positions):
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        rows = [item.as_dict() for item in canonical_positions]
        selected_claim_ids = {
            claim_id for item in canonical_positions for claim_id in item.claim_ids
        }
        story_claim_ids = {
            str(claim.get("claim_id", ""))
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        candidate_claim_ids = {
            str(claim.get("claim_id", ""))
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        available_claim_ids = story_claim_ids & candidate_claim_ids
        minimum_selected_claims = min(
            NARRATION_REPAIR_POSITION_MAX_COUNT,
            len(available_claim_ids),
        )
        if not minimum_selected_claims <= len(selected_claim_ids) <= 12:
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        target_word_count = sum(item.word_budget for item in canonical_positions)
        if not bounds["target_word_min"] <= target_word_count <= bounds["target_word_max"]:
            raise CloudStageError(
                "cloud.narrative_repair_position_budget_invalid",
                reviewable=True,
            )
        selected_passage_ids = {item.passage_id for item in canonical_positions}
        if len(selected_passage_ids) < 4:
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        prompt_identity = prompt or self.prompts["narration"]
        evidence_closure = self._narration_repair_evidence_closure(
            canonical_positions,
            candidate,
            story_map,
            allow_claim_evidence_subset=allow_claim_evidence_subset,
        )
        identity = {
            "version": NARRATION_REPAIR_POSITION_REGISTRY_VERSION,
            "candidate_hash": _hash(candidate.as_dict()),
            "visual_evidence_hash": candidate.visual_evidence_hash,
            "story_map_hash": story_map.story_map_hash,
            "model_identity_hash": self.model_identity.identity_hash,
            "prompt_version": prompt_identity[0],
            "prompt_sha256": prompt_identity[1],
            "allow_hook_teaser": bool(allow_hook_teaser),
            "duration_policy_contract": bounds,
            "positions": rows,
            "evidence_closure_hash": evidence_closure["closure_hash"],
        }
        return {
            **identity,
            "positions": rows,
            "evidence_closure": evidence_closure,
            "target_word_count": target_word_count,
            "target_duration_s": script.estimate_narration_duration(
                " ".join(["word"] * target_word_count),
                "dramatic",
            ),
            "slot_order_hash": _hash(identity),
        }

    @_bound
    def _build_narration_repair_position_registry(
        self,
        candidate: NarrationResult,
        story_map: StoryMapResult,
        *,
        prompt: tuple[str, str, str] | None = None,
        passage_word_budgets: Mapping[str, int] | None = None,
        passage_word_targets: Mapping[str, int] | None = None,
        duration_policy_contract: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Select 4-8 trusted claim positions before any provider request."""

        separate_passage_targets = passage_word_targets is not None
        if passage_word_targets is None:
            passage_word_targets = passage_word_budgets
        slots = self._build_narration_repair_slots(
            candidate,
            story_map,
            preserve_candidate_evidence=bool(passage_word_budgets),
        )
        candidate_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        story_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        all_positions: list[NarrationRepairPosition] = []
        for slot in slots:
            for claim_index, claim_id in enumerate(slot.claim_ids):
                candidate_claim = candidate_claims.get(claim_id)
                story_claim = story_claims.get(claim_id)
                if candidate_claim is None or story_claim is None:
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
                claim_refs = tuple(
                    str(value)
                    for value in story_claim.get(
                        "evidence_panel_ids",
                        story_claim.get("panel_ids", ()),
                    )
                )
                if passage_word_budgets:
                    claim_refs = tuple(
                        ref for ref in claim_refs if ref in set(slot.evidence_panel_ids)
                    )
                if not claim_refs or not set(claim_refs).issubset(set(slot.evidence_panel_ids)):
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
                identity = {
                    "version": NARRATION_REPAIR_POSITION_REGISTRY_VERSION,
                    "candidate_hash": _hash(candidate.as_dict()),
                    "story_map_hash": story_map.story_map_hash,
                    "slot_id": slot.slot_id,
                    "claim_id": claim_id,
                    "causal_position": slot.causal_position,
                }
                all_positions.append(
                    NarrationRepairPosition(
                        position=len(all_positions),
                        slot_id=f"narration_position_v1_{_hash(identity)}",
                        passage_id=slot.passage_id,
                        claim_ids=(claim_id,),
                        evidence_panel_ids=claim_refs,
                        beat_id=slot.beat_id,
                        causal_position=slot.causal_position,
                        priority=slot.priority - claim_index,
                        removable=slot.removable or len(slot.claim_ids) > 1,
                        word_budget=1,
                    )
                )
        if len(all_positions) < NARRATION_REPAIR_POSITION_MIN_COUNT:
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        if passage_word_budgets:
            budget_keys = {str(key) for key in passage_word_budgets}
            target_keys = {str(key) for key in (passage_word_targets or {})}
            passage_keys = {slot.passage_id for slot in slots}
            if budget_keys != passage_keys or target_keys != passage_keys or any(
                int((passage_word_targets or {})[key]) <= 0
                or int(passage_word_budgets[key]) < int((passage_word_targets or {})[key])
                for key in passage_keys
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_budget_invalid", reviewable=True
                )
            selected = []
            for slot in slots:
                identity = {
                    "version": NARRATION_REPAIR_POSITION_REGISTRY_VERSION,
                    "mode": "capacity_passage_locked",
                    "candidate_hash": _hash(candidate.as_dict()),
                    "story_map_hash": story_map.story_map_hash,
                    "passage_id": slot.passage_id,
                    "claim_ids": list(slot.claim_ids),
                    "evidence_panel_ids": list(slot.evidence_panel_ids),
                }
                selected.append(
                    NarrationRepairPosition(
                        position=len(selected),
                        slot_id=f"narration_position_v1_passage_{_hash(identity)}",
                        passage_id=slot.passage_id,
                        claim_ids=slot.claim_ids,
                        evidence_panel_ids=slot.evidence_panel_ids,
                        beat_id=slot.beat_id,
                        causal_position=slot.causal_position,
                        priority=slot.priority,
                        removable=False,
                        word_budget=1,
                    )
                )
        else:
            selected = list(all_positions)
            while len(selected) > NARRATION_REPAIR_POSITION_MAX_COUNT:
                counts: dict[str, int] = {}
                for item in selected:
                    counts[item.passage_id] = counts.get(item.passage_id, 0) + 1
                removable = [
                    item for item in selected if item.removable and counts[item.passage_id] > 1
                ]
                if not removable or len(selected) - 1 < 8:
                    raise CloudStageError(
                        "cloud.narrative_repair_position_selection_invalid",
                        reviewable=True,
                    )
                selected.remove(
                    min(
                        removable,
                        key=lambda item: (
                            item.priority,
                            item.causal_position,
                            item.passage_id,
                            item.claim_ids,
                        ),
                    )
                )
        if passage_word_budgets:
            selected_counts: dict[str, int] = {}
            for item in selected:
                selected_counts[item.passage_id] = selected_counts.get(item.passage_id, 0) + 1
            missing = set(selected_counts) - set(passage_word_budgets)
            if missing:
                raise CloudStageError("cloud.narrative_repair_position_budget_invalid", reviewable=True)
            per_position_budget = []
            per_position_max = []
            seen_by_passage: dict[str, int] = {}
            for item in selected:
                target_total = int((passage_word_targets or passage_word_budgets)[item.passage_id])
                max_total = int(passage_word_budgets[item.passage_id])
                count = selected_counts[item.passage_id]
                target_base, target_remainder = divmod(target_total, count)
                max_base, max_remainder = divmod(max_total, count)
                seen = seen_by_passage.get(item.passage_id, 0)
                per_position_budget.append(
                    target_base + (1 if seen < target_remainder else 0)
                )
                per_position_max.append(max_base + (1 if seen < max_remainder else 0))
                seen_by_passage[item.passage_id] = seen + 1
            target_word_count = sum(
                int((passage_word_targets or passage_word_budgets)[key])
                for key in selected_counts
            )
        else:
            target_word_count = 120
            base_budget, remainder = divmod(target_word_count, len(selected))
            per_position_budget = [base_budget + (1 if index < remainder else 0) for index in range(len(selected))]
            per_position_max = [
                per_position_budget[index] + (1 if index < 125 - target_word_count else 0)
                for index in range(len(selected))
            ]
        budgeted = [
            replace(
                item,
                position=index,
                word_budget=per_position_budget[index],
                word_budget_min=(
                    _position_word_budget_bounds(per_position_budget[index])[0]
                    if separate_passage_targets
                    else per_position_budget[index]
                    if passage_word_budgets
                    else _position_word_budget_bounds(per_position_budget[index])[0]
                ),
                word_budget_max=_position_word_budget_bounds(
                    per_position_budget[index],
                    max_word_budget=per_position_max[index],
                )[1],
            )
            for index, item in enumerate(selected)
        ]
        registry = self._narration_repair_position_registry(
            budgeted,
            candidate,
            story_map,
            prompt=prompt,
            allow_claim_evidence_subset=bool(passage_word_budgets),
            allow_hook_teaser=bool(passage_word_budgets),
            duration_policy_contract=duration_policy_contract,
        )
        registry["passage_word_budgets"] = (
            {str(key): int(value) for key, value in passage_word_budgets.items()}
            if passage_word_budgets
            else {}
        )
        registry["passage_word_targets"] = (
            {str(key): int(value) for key, value in (passage_word_targets or {}).items()}
            if passage_word_budgets
            else {}
        )
        candidate_passages = {
            str(passage.get("passage_id", "")): passage
            for passage in candidate.passages
            if isinstance(passage, Mapping)
        }
        provider_positions = []
        for item in budgeted:
            passage = candidate_passages[item.passage_id]
            provider_positions.append(
                {
                    "position": item.position,
                    "word_budget": item.word_budget,
                    "word_budget_min": item.word_budget_min,
                    "word_budget_max": item.word_budget_max,
                    "passage_word_budget_max": (
                        int(passage_word_budgets[item.passage_id]) if passage_word_budgets else None
                    ),
                    "passage_text": str(passage.get("text", "")),
                    "claim_context": [
                        {
                            "text": str(candidate_claims[claim_id].get("text", "")),
                            "qualification": str(
                                candidate_claims[claim_id].get("qualification", "")
                            ),
                        }
                        for claim_id in item.claim_ids
                    ],
                    "evidence_panel_ids": list(item.evidence_panel_ids),
                    "beat_context": item.beat_id,
                }
            )
        registry["provider_positions"] = provider_positions
        passage_lineage = self._reconstruct_narration_repair_passage_lineage(
            candidate,
            registry,
        )
        registry["passage_lineage_version"] = passage_lineage["version"]
        registry["passage_lineage_hash"] = passage_lineage["lineage_hash"]
        registry["slot_order_hash"] = _hash(
            {
                "position_registry_hash": registry["slot_order_hash"],
                "passage_lineage_version": passage_lineage["version"],
                "passage_lineage_hash": passage_lineage["lineage_hash"],
                "passage_word_budgets": registry["passage_word_budgets"],
                "passage_word_targets": registry["passage_word_targets"],
            }
        )
        return registry

    @staticmethod
    @_bound
    def _reconstruct_narration_repair_passage_lineage(
        candidate: NarrationResult,
        registry: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Rebuild passage claim/evidence refs from trusted local positions.

        The positional provider contract owns rewrite text only.  Candidate
        passage references may be incomplete after an earlier repair, so the
        persisted position registry is the authority for the retained claim
        and evidence union.  This boundary never accepts provider-supplied
        identifiers or infers new evidence.
        """

        raw_positions = registry.get("positions")
        closure = registry.get("evidence_closure")
        evidence_scope_mode = (
            str(closure.get("evidence_scope_mode", "full_claim_closure"))
            if isinstance(closure, Mapping)
            else "full_claim_closure"
        )
        allow_claim_evidence_subset = evidence_scope_mode == "candidate_passage_locked"
        if evidence_scope_mode not in {"full_claim_closure", "candidate_passage_locked"}:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid", reviewable=True
            )
        if not isinstance(raw_positions, list) or not raw_positions:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        candidate_passages = tuple(candidate.passages)
        if len(candidate_passages) < 4 or any(
            not isinstance(passage, Mapping) for passage in candidate_passages
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        candidate_by_id = {
            str(passage.get("passage_id", "")): passage for passage in candidate_passages
        }
        if len(candidate_by_id) != len(candidate_passages) or any(
            not passage_id.strip() for passage_id in candidate_by_id
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        raw_candidate_claims = candidate.evidence_graph.get("claims", ())
        if not isinstance(raw_candidate_claims, (list, tuple)):
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        candidate_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in raw_candidate_claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        if (
            len(candidate_claims) != len(raw_candidate_claims)
            or any(not isinstance(claim, Mapping) for claim in raw_candidate_claims)
            or any(not str(claim.get("claim_id", "")).strip() for claim in raw_candidate_claims)
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        observation_ids = {
            str(observation.get("panel_id", ""))
            for observation in candidate.observations
            if isinstance(observation, Mapping) and str(observation.get("panel_id", "")).strip()
        }
        if not observation_ids or not candidate_claims:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )

        groups: dict[str, dict[str, list[Any]]] = {}
        seen_position_ids: set[str] = set()
        allow_hook_teaser = bool(registry.get("allow_hook_teaser", False))
        previous_causal_position = -1
        for index, value in enumerate(raw_positions):
            if not isinstance(value, Mapping):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            position = value.get("position")
            slot_id = str(value.get("slot_id", "")).strip()
            passage_id = str(value.get("passage_id", "")).strip()
            claim_values = value.get("claim_ids")
            evidence_values = value.get("evidence_panel_ids")
            causal_position = value.get("causal_position")
            order_invalid = (
                not (allow_hook_teaser and index == 0)
                and isinstance(causal_position, int)
                and not isinstance(causal_position, bool)
                and causal_position < previous_causal_position
            )
            if (
                isinstance(position, bool)
                or not isinstance(position, int)
                or position != index
                or not slot_id
                or slot_id in seen_position_ids
                or passage_id not in candidate_by_id
                or not isinstance(claim_values, (list, tuple))
                or not isinstance(evidence_values, (list, tuple))
                or not claim_values
                or not evidence_values
                or isinstance(causal_position, bool)
                or not isinstance(causal_position, int)
                or order_invalid
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            claim_ids = tuple(str(value) for value in claim_values)
            evidence_panel_ids = tuple(str(value) for value in evidence_values)
            original_claim_values = candidate_by_id[passage_id].get("claim_ids")
            original_evidence_values = candidate_by_id[passage_id].get("evidence_panel_ids")
            if (
                not isinstance(original_claim_values, (list, tuple))
                or not isinstance(original_evidence_values, (list, tuple))
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            original_claim_ids = tuple(str(value) for value in original_claim_values)
            original_evidence_panel_ids = tuple(str(value) for value in original_evidence_values)
            if (
                any(not value.strip() for value in claim_ids)
                or len(set(claim_ids)) != len(claim_ids)
                or any(not value.strip() for value in original_claim_ids)
                or len(set(original_claim_ids)) != len(original_claim_ids)
                or any(claim_id not in candidate_claims for claim_id in claim_ids)
                or any(claim_id not in original_claim_ids for claim_id in claim_ids)
                or any(not value.strip() for value in evidence_panel_ids)
                or len(set(evidence_panel_ids)) != len(evidence_panel_ids)
                or any(panel_id not in observation_ids for panel_id in evidence_panel_ids)
                or (
                    allow_claim_evidence_subset
                    and not set(evidence_panel_ids).issubset(set(original_evidence_panel_ids))
                )
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            for claim_id in claim_ids:
                claim = candidate_claims[claim_id]
                claim_refs = claim.get(
                    "evidence_panel_ids",
                    claim.get("panel_ids", ()),
                )
                if not isinstance(claim_refs, (list, tuple)) or not claim_refs:
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
                claim_refs = tuple(str(value) for value in claim_refs)
                selected_claim_refs = (
                    tuple(ref for ref in claim_refs if ref in set(evidence_panel_ids))
                    if allow_claim_evidence_subset
                    else claim_refs
                )
                if (
                    any(not value.strip() for value in claim_refs)
                    or len(set(claim_refs)) != len(claim_refs)
                    or not selected_claim_refs
                    or (
                        not allow_claim_evidence_subset
                        and not set(claim_refs).issubset(set(evidence_panel_ids))
                    )
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
            seen_position_ids.add(slot_id)
            previous_causal_position = (
                -1 if allow_hook_teaser and index == 0 else causal_position
            )
            group = groups.setdefault(
                passage_id,
                {
                    "position_ids": [],
                    "claim_ids": [],
                    "evidence_panel_ids": [],
                    "causal_positions": [],
                },
            )
            group["position_ids"].append(slot_id)
            group["causal_positions"].append(causal_position)
            for claim_id in claim_ids:
                if claim_id not in group["claim_ids"]:
                    group["claim_ids"].append(claim_id)
            for panel_id in evidence_panel_ids:
                if panel_id not in group["evidence_panel_ids"]:
                    group["evidence_panel_ids"].append(panel_id)

        passage_rows: list[dict[str, Any]] = []
        for passage in candidate_passages:
            passage_id = str(passage.get("passage_id", ""))
            group = groups.get(passage_id)
            if group is None:
                continue
            selected_claim_ids = [
                claim_id
                for claim_id in passage.get("claim_ids", ())
                if str(claim_id) in group["claim_ids"]
            ]
            evidence_panel_ids = list(group["evidence_panel_ids"])
            if not selected_claim_ids or not evidence_panel_ids:
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            for claim_id in selected_claim_ids:
                claim = candidate_claims[claim_id]
                claim_refs = claim.get(
                    "evidence_panel_ids",
                    claim.get("panel_ids", ()),
                )
                claim_ref_set = (
                    {str(value) for value in claim_refs}
                    if isinstance(claim_refs, (list, tuple))
                    else set()
                )
                if (
                    not claim_ref_set
                    or (
                        allow_claim_evidence_subset
                        and not (claim_ref_set & set(evidence_panel_ids))
                    )
                    or (
                        not allow_claim_evidence_subset
                        and not claim_ref_set.issubset(set(evidence_panel_ids))
                    )
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
            passage_rows.append(
                {
                    "passage_id": passage_id,
                    "claim_ids": selected_claim_ids,
                    "evidence_panel_ids": evidence_panel_ids,
                    "position_ids": list(group["position_ids"]),
                    "causal_positions": list(group["causal_positions"]),
                }
            )
        if len(passage_rows) < 4:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        expected_version = str(registry.get("passage_lineage_version", ""))
        if expected_version and expected_version != NARRATION_REPAIR_PASSAGE_LINEAGE_VERSION:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        identity = {
            "version": NARRATION_REPAIR_PASSAGE_LINEAGE_VERSION,
            "candidate_visual_evidence_hash": candidate.visual_evidence_hash,
            "passages": passage_rows,
        }
        lineage_hash = _hash(identity)
        expected_hash = str(registry.get("passage_lineage_hash", ""))
        if expected_hash and expected_hash != lineage_hash:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        return {
            "version": NARRATION_REPAIR_PASSAGE_LINEAGE_VERSION,
            "passages": passage_rows,
            "lineage_hash": lineage_hash,
        }

    @staticmethod
    @_bound
    def _reconcile_narration_repair_vector(
        raw: Mapping[str, Any],
        registry: Mapping[str, Any],
        candidate: NarrationResult,
        *,
        story_map: StoryMapResult | None = None,
    ) -> dict[str, Any]:
        """Map provider rewrite index N to trusted local position N."""

        evidence_closure = CloudStageRunner._validate_narration_repair_evidence_closure(
            registry,
            candidate,
            story_map,
        )
        raw_positions = registry.get("positions")
        rewrites = raw.get("rewrites") if isinstance(raw, Mapping) else None
        budget_normalization: dict[str, Any] | None = None
        bounds = _narration_repair_contract_bounds(registry.get("duration_policy_contract"))
        word_min = int(bounds["target_word_min"])
        word_max = int(bounds["target_word_max"])
        duration_min = float(bounds["target_duration_min_s"])
        duration_max = float(bounds["target_duration_max_s"])

        def response_items(value: Any) -> list[Any] | None:
            if isinstance(value, Sequence) and not isinstance(
                value,
                (str, bytes, bytearray, Mapping),
            ):
                return list(value)
            return None

        def response_shape_metrics(
            failed_predicate: str | None,
            word_counts: Sequence[int | None],
            total_words: int | None,
            duration: float | None,
            micro_compaction: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            expected_ranges = []
            position_items = raw_positions if isinstance(raw_positions, list) else []
            for item in position_items:
                if isinstance(item, Mapping):
                    expected_ranges.append(
                        {
                            "position": item.get("position"),
                            "target": item.get("word_budget"),
                            "min": item.get("word_budget_min"),
                            "max": item.get("word_budget_max"),
                        }
                    )
                else:
                    expected_ranges.append(
                        {"position": None, "target": None, "min": None, "max": None}
                    )
            rewrite_items = response_items(rewrites)
            metrics = {
                "container_type": type(raw).__name__,
                "top_level_keys": (
                    sorted(str(key) for key in raw) if isinstance(raw, Mapping) else []
                ),
                "array_key": "rewrites",
                "array_count": len(rewrite_items) if rewrite_items is not None else None,
                "array_item_types": (
                    [type(item).__name__ for item in rewrite_items]
                    if rewrite_items is not None
                    else []
                ),
                "per_position_word_counts": list(word_counts),
                "total_word_count": total_words,
                "estimated_duration_s": duration,
                "slot_order_hash": str(registry.get("slot_order_hash", "")),
                "expected_ranges": expected_ranges,
                "accepted_word_bounds": {"min": word_min, "max": word_max},
                "accepted_duration_bounds_s": {"min": duration_min, "max": duration_max},
                "failed_predicate": failed_predicate,
            }
            if micro_compaction is not None:
                metrics["micro_compaction"] = dict(micro_compaction)
            if budget_normalization is not None:
                metrics["locked_story_budget_normalization"] = dict(budget_normalization)
            return metrics

        def current_response_shape(failed_predicate: str) -> dict[str, Any]:
            rewrite_items = response_items(rewrites)
            if rewrite_items is None:
                return response_shape_metrics(failed_predicate, [], None, None)
            word_counts = [
                script.narration_word_count(text) if isinstance(text, str) else None
                for text in rewrite_items
            ]
            total_words = sum(count for count in word_counts if count is not None)
            all_strings = bool(rewrite_items) and all(count is not None for count in word_counts)
            duration = (
                script.estimate_narration_duration(" ".join(rewrite_items), "dramatic")
                if all_strings
                else None
            )
            return response_shape_metrics(
                failed_predicate,
                word_counts,
                total_words,
                duration,
            )

        if not isinstance(raw, Mapping) or set(raw) != {"rewrites"}:
            raise CloudStageError(
                "cloud.narrative_repair_position_contract_invalid",
                reviewable=True,
                safe_metadata=current_response_shape("response_top_level_shape"),
            )
        if not isinstance(raw_positions, list) or not isinstance(rewrites, list):
            raise CloudStageError(
                "cloud.narrative_repair_position_contract_invalid",
                reviewable=True,
                safe_metadata=current_response_shape("response_array_type"),
            )
        if len(rewrites) != len(raw_positions):
            raise CloudStageError(
                "cloud.narrative_repair_position_contract_invalid",
                reviewable=True,
                safe_metadata=current_response_shape("rewrite_count"),
            )

        word_counts = [
            script.narration_word_count(text) if isinstance(text, str) else None
            for text in rewrites
        ]
        total_words = sum(count for count in word_counts if count is not None)
        all_strings = all(count is not None for count in word_counts)
        duration = (
            script.estimate_narration_duration(" ".join(rewrites), "dramatic")
            if all_strings
            else None
        )
        passage_lineage = CloudStageRunner._reconstruct_narration_repair_passage_lineage(
            candidate,
            registry,
        )
        positions: list[NarrationRepairPosition] = []
        closure_rows = {
            int(row["position"]): row
            for row in evidence_closure.get("positions", ())
            if isinstance(row, Mapping) and isinstance(row.get("position"), int)
        }
        for index, value in enumerate(raw_positions):
            try:
                position = NarrationRepairPosition(
                    position=index,
                    slot_id=str(value["slot_id"]),
                    passage_id=str(value["passage_id"]),
                    claim_ids=tuple(str(item) for item in value["claim_ids"]),
                    evidence_panel_ids=tuple(str(item) for item in value["evidence_panel_ids"]),
                    beat_id=str(value["beat_id"]),
                    causal_position=int(value["causal_position"]),
                    priority=int(value["priority"]),
                    removable=bool(value["removable"]),
                    word_budget=int(value["word_budget"]),
                    word_budget_min=int(
                        value.get(
                            "word_budget_min",
                            _position_word_budget_bounds(int(value["word_budget"]))[0],
                        )
                    ),
                    word_budget_max=int(
                        value.get(
                            "word_budget_max",
                            _position_word_budget_bounds(int(value["word_budget"]))[1],
                        )
                    ),
                )
            except (KeyError, TypeError, ValueError):
                raise CloudStageError(
                    "cloud.narrative_repair_position_contract_invalid",
                    reviewable=True,
                    safe_metadata=current_response_shape("position_descriptor"),
                ) from None
            closure_row = closure_rows.get(index)
            if (
                closure_row is None
                or str(closure_row.get("slot_id", "")) != position.slot_id
                or str(closure_row.get("passage_id", "")) != position.passage_id
                or tuple(str(item) for item in closure_row.get("claim_ids", ()))
                != position.claim_ids
                or tuple(str(item) for item in closure_row.get("evidence_panel_ids", ()))
                != position.evidence_panel_ids
                or str(closure_row.get("beat_id", "")) != position.beat_id
                or int(closure_row.get("causal_position", -1)) != position.causal_position
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            positions.append(position)
            text = rewrites[index]
            if not isinstance(text, str) or not text.strip():
                raise CloudStageError(
                    "cloud.narrative_repair_position_contract_invalid",
                    reviewable=True,
                    safe_metadata=current_response_shape("rewrite_text"),
                )
            trusted_ids = (
                position.slot_id,
                position.passage_id,
                position.beat_id,
                *position.claim_ids,
                *position.evidence_panel_ids,
            )
            if any(identifier and identifier in text for identifier in trusted_ids):
                raise CloudStageError(
                    "cloud.narrative_repair_position_contract_invalid",
                    reviewable=True,
                    safe_metadata=current_response_shape("trusted_identifier_echo"),
                )

        if all_strings:
            normalized_rewrites, budget_normalization = _normalize_locked_story_budget(
                tuple(str(text) for text in rewrites),
                tuple(value for value in raw_positions if isinstance(value, Mapping)),
                registry,
            )
            if not budget_normalization.get("failed_predicate"):
                rewrites = list(normalized_rewrites)
                word_counts = [script.narration_word_count(text) for text in rewrites]
                total_words = sum(word_counts)
                duration = script.estimate_narration_duration(" ".join(rewrites), "dramatic")

        micro_compaction: dict[str, Any] | None = None
        if all_strings:
            rewrites, micro_compaction = _micro_compact_rewrites(
                tuple(rewrites),
                total_words=total_words,
            )
            if micro_compaction.get("failed_predicate"):
                raise CloudStageError(
                    "cloud.narrative_repair_micro_compaction_unavailable",
                    reviewable=True,
                    safe_metadata=response_shape_metrics(
                        str(micro_compaction["failed_predicate"]),
                        [script.narration_word_count(text) for text in rewrites],
                        int(micro_compaction["after_word_count"]),
                        script.estimate_narration_duration(" ".join(rewrites), "dramatic"),
                        micro_compaction,
                    ),
                )
            word_counts = [script.narration_word_count(text) for text in rewrites]
            total_words = sum(word_counts)
            duration = script.estimate_narration_duration(
                " ".join(rewrites),
                "dramatic",
            )
            dominance_limit = max(
                NARRATION_REPAIR_POSITION_DOMINANCE_FLOOR,
                math.ceil(total_words * NARRATION_REPAIR_POSITION_MAX_SHARE),
            )
            for word_count in word_counts:
                if word_count > dominance_limit:
                    raise CloudStageError(
                        "cloud.narrative_repair_position_budget_invalid",
                        reviewable=True,
                        safe_metadata=response_shape_metrics(
                            "position_word_dominance",
                            word_counts,
                            total_words,
                            duration,
                            micro_compaction,
                        ),
                    )
        if not word_min <= total_words <= word_max or duration is None or not duration_min <= duration <= duration_max:
            failed_predicate = (
                "aggregate_word_count" if not word_min <= total_words <= word_max else "aggregate_duration"
            )
            raise CloudStageError(
                "cloud.narrative_repair_position_budget_invalid",
                reviewable=True,
                safe_metadata=response_shape_metrics(
                    failed_predicate,
                    word_counts,
                    total_words if all_strings else None,
                    duration,
                    micro_compaction,
                ),
            )
        grouped_text: dict[str, list[str]] = {}
        for position, text in zip(positions, rewrites, strict=True):
            grouped_text.setdefault(position.passage_id, []).append(text.strip())
        explicit_passage_word_budgets = registry.get("passage_word_budgets")
        if explicit_passage_word_budgets:
            if not isinstance(explicit_passage_word_budgets, Mapping):
                raise CloudStageError(
                    "cloud.narrative_repair_position_budget_invalid",
                    reviewable=True,
                )
            passage_word_counts = {
                passage_id: script.narration_word_count(" ".join(parts))
                for passage_id, parts in grouped_text.items()
            }
            passage_word_budgets = {
                str(passage_id): int(budget)
                for passage_id, budget in explicit_passage_word_budgets.items()
            }
            if set(passage_word_counts) != set(passage_word_budgets) or any(
                budget <= 0 for budget in passage_word_budgets.values()
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_budget_invalid",
                    reviewable=True,
                )
            over_budget = {
                passage_id: passage_word_counts[passage_id] - budget
                for passage_id, budget in passage_word_budgets.items()
                if passage_word_counts[passage_id] > budget
            }
            if over_budget:
                raise CloudStageError(
                    "cloud.narrative_repair_position_budget_invalid",
                    reviewable=True,
                    safe_metadata={
                        **response_shape_metrics(
                            "passage_word_budget", word_counts, total_words, duration, micro_compaction
                        ),
                        "over_budget_passage_count": len(over_budget),
                        "max_passage_word_overage": max(over_budget.values()),
                    },
                )
        lineage_by_passage = {str(row["passage_id"]): row for row in passage_lineage["passages"]}
        passages: list[dict[str, Any]] = []
        for original in candidate.passages:
            passage_id = str(original.get("passage_id", ""))
            if passage_id not in grouped_text or passage_id not in lineage_by_passage:
                continue
            lineage = lineage_by_passage[passage_id]
            passage = dict(original)
            passage["text"] = " ".join(grouped_text[passage_id])
            passage["claim_ids"] = list(lineage["claim_ids"])
            passage["evidence_panel_ids"] = list(lineage["evidence_panel_ids"])
            if not passage["claim_ids"] or not passage["evidence_panel_ids"]:
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            passages.append(passage)
        claims = [
            dict(claim)
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping)
            and str(claim.get("claim_id", ""))
            in {claim_id for row in passage_lineage["passages"] for claim_id in row["claim_ids"]}
        ]
        if len(passages) < 4 or not claims:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        return {
            "narrative_outline": {
                "story_spine": dict(candidate.story_spine),
                "ending_kind": candidate.ending_kind,
            },
            "script_passages": passages,
            "evidence_graph": {"claims": claims},
            "_passage_lineage": passage_lineage,
            "_response_shape_metrics": response_shape_metrics(
                None,
                word_counts,
                total_words if all_strings else None,
                duration,
                micro_compaction,
            ),
        }

    @staticmethod
    @_bound
    def _reconcile_narration_repair_slots(
        raw: Mapping[str, Any],
        slots: Sequence[NarrationRepairSlot],
        candidate: NarrationResult,
    ) -> dict[str, Any]:
        """Replace provider slot references with trusted local lineage."""

        provider_output = raw.get("analyzer_output", raw) if isinstance(raw, Mapping) else raw
        if not isinstance(provider_output, Mapping) or set(provider_output) != {"repair_slots"}:
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        envelope = provider_output.get("repair_slots")
        if not isinstance(envelope, Mapping) or set(envelope) != {
            "retained_slot_ids",
            "dropped_slot_ids",
            "slots",
        }:
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        slot_by_id = {slot.slot_id: slot for slot in slots}
        ordered_ids = [slot.slot_id for slot in slots]
        retained = envelope["retained_slot_ids"]
        dropped = envelope["dropped_slot_ids"]
        if not isinstance(retained, list) or not isinstance(dropped, list):
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        for values in (retained, dropped):
            if any(not isinstance(value, str) for value in values):
                raise CloudStageError(
                    "cloud.narrative_repair_slot_contract_invalid", reviewable=True
                )
            if any(value not in slot_by_id for value in values):
                raise CloudStageError("cloud.narrative_repair_slot_unknown", reviewable=True)
            if len(values) != len(set(values)):
                raise CloudStageError("cloud.narrative_repair_slot_duplicate", reviewable=True)
        if set(retained) & set(dropped):
            raise CloudStageError("cloud.narrative_repair_slot_duplicate", reviewable=True)
        if set(retained) | set(dropped) != set(ordered_ids):
            raise CloudStageError("cloud.narrative_repair_slot_missing", reviewable=True)
        canonical_retained = [slot_id for slot_id in ordered_ids if slot_id in set(retained)]
        canonical_dropped = [slot_id for slot_id in ordered_ids if slot_id in set(dropped)]
        if retained != canonical_retained or dropped != canonical_dropped:
            raise CloudStageError("cloud.narrative_repair_slot_order_invalid", reviewable=True)
        if len(retained) < 4:
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        if any(not slot_by_id[slot_id].removable for slot_id in dropped):
            raise CloudStageError("cloud.narrative_repair_slot_drop_forbidden", reviewable=True)
        rows = envelope["slots"]
        if not isinstance(rows, list):
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        row_ids: list[str] = []
        text_by_id: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, Mapping) or "slot_id" not in row:
                raise CloudStageError(
                    "cloud.narrative_repair_slot_contract_invalid", reviewable=True
                )
            slot_id = row.get("slot_id")
            if not isinstance(slot_id, str):
                raise CloudStageError(
                    "cloud.narrative_repair_slot_contract_invalid", reviewable=True
                )
            if slot_id not in slot_by_id:
                raise CloudStageError("cloud.narrative_repair_slot_unknown", reviewable=True)
            if slot_id in row_ids:
                raise CloudStageError("cloud.narrative_repair_slot_duplicate", reviewable=True)
            if (
                set(row) != {"slot_id", "text"}
                or not isinstance(row.get("text"), str)
                or not row["text"].strip()
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_slot_contract_invalid", reviewable=True
                )
            row_ids.append(slot_id)
            text_by_id[slot_id] = row["text"].strip()
        if row_ids != canonical_retained:
            raise CloudStageError("cloud.narrative_repair_slot_missing", reviewable=True)
        candidate_by_passage_id = {
            str(passage.get("passage_id", "")): passage
            for passage in candidate.passages
            if isinstance(passage, Mapping)
        }
        candidate_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        passages: list[dict[str, Any]] = []
        retained_claim_ids: set[str] = set()
        for slot_id in canonical_retained:
            slot = slot_by_id[slot_id]
            original = candidate_by_passage_id.get(slot.passage_id)
            if original is None or any(
                claim_id not in candidate_claims for claim_id in slot.claim_ids
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_slot_lineage_invalid", reviewable=True
                )
            passage = dict(original)
            passage["text"] = text_by_id[slot_id]
            passage["claim_ids"] = list(slot.claim_ids)
            passage["evidence_panel_ids"] = list(slot.evidence_panel_ids)
            passages.append(passage)
            retained_claim_ids.update(slot.claim_ids)
        claims = CloudStageRunner._normalize_narration_claims(
            [
                dict(claim)
                for claim in candidate.evidence_graph.get("claims", ())
                if isinstance(claim, Mapping)
                and str(claim.get("claim_id", "")) in retained_claim_ids
            ]
        )
        return {
            "narrative_outline": {
                "story_spine": dict(candidate.story_spine),
                "ending_kind": candidate.ending_kind,
            },
            "script_passages": passages,
            "evidence_graph": {"claims": claims},
        }

    @_bound
    def _narration_repair_identity_metadata(
        self,
        *,
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
        candidate: NarrationResult,
        visual: VisualStageResult | None = None,
        story_map: StoryMapResult | None = None,
    ) -> dict[str, Any]:
        """Build the metadata-only dependency identity for a repair candidate."""

        story_value = story_map.as_dict() if story_map is not None else source.get("story_map", {})
        if not isinstance(story_value, Mapping):
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        if story_map is None:
            try:
                story_map = StoryMapResult.from_dict(story_value)
            except (KeyError, TypeError, ValueError):
                raise CloudStageError(
                    "cloud.narrative_repair_identity_mismatch",
                    reviewable=True,
                ) from None
        panel_ids = list(
            visual.panel_ids
            if visual is not None
            else tuple(str(value) for value in source.get("panel_ids", story_map.panel_ids))
        )
        panel_rows = list(visual.panels) if visual is not None else []
        panel_by_id = {
            str(row.get("panel_id", "")): row for row in panel_rows if isinstance(row, Mapping)
        }
        panel_identity_hashes = []
        canonical_panel_rows = []
        for panel_id in panel_ids:
            row = panel_by_id.get(panel_id, {})
            visual_row = row.get("visual_evidence", {})
            evidence_hash = str(
                row.get("evidence_hash")
                or (visual_row.get("evidence_hash") if isinstance(visual_row, Mapping) else "")
                or _hash(
                    {
                        "panel_id": panel_id,
                        "visual_evidence_hash": visual.visual_evidence_hash if visual else "",
                    }
                )
            )
            panel_identity_hashes.append(evidence_hash)
            canonical_panel_rows.append(
                {
                    "panel_id": panel_id,
                    "source_order": row.get("source_order"),
                    "source_asset_id": row.get("source_asset_id"),
                    "source_checksum": row.get("source_checksum"),
                    "panel_bounds": row.get("panel_bounds"),
                    "evidence_hash": evidence_hash,
                }
            )
        selection = source.get("editorial_selection", {})
        if not isinstance(selection, Mapping):
            selection = {}
        selection_summary = {
            "beat_ids": [str(value) for value in selection.get("beat_ids", ())],
            "panel_ids": [str(value) for value in selection.get("panel_ids", panel_ids)],
            "claim_ids": [str(value) for value in selection.get("claim_ids", ())],
            "selection_hash": str(selection.get("selection_hash", _hash(selection))),
        }
        try:
            position_registry = self._build_narration_repair_position_registry(
                candidate,
                story_map,
                prompt=prompt,
            )
            position_rows = list(position_registry["positions"])
            slot_summary = {
                "slot_ids": [str(row["slot_id"]) for row in position_rows],
                "claim_ids": [
                    str(claim_id) for row in position_rows for claim_id in row.get("claim_ids", ())
                ],
                "evidence_panel_ids": [
                    str(panel_id)
                    for row in position_rows
                    for panel_id in row.get("evidence_panel_ids", ())
                ],
                "slot_order_hash": str(position_registry["slot_order_hash"]),
            }
        except CloudStageError:
            slot_summary = {
                "slot_ids": [],
                "claim_ids": [],
                "evidence_panel_ids": [],
                "slot_order_hash": "unavailable",
            }
        return {
            "policy_version": NARRATION_REPAIR_IDENTITY_VERSION,
            "panel_lineage": {
                "ordered_panel_ids": panel_ids,
                "panel_identity_hashes": panel_identity_hashes,
                "visual_evidence_hash": visual.visual_evidence_hash
                if visual
                else str(source.get("visual_evidence_hash", "")),
                "panels": canonical_panel_rows,
            },
            "model": {"identity_hash": self.model_identity.identity_hash},
            "prompt": {"version": prompt[0], "sha256": prompt[1]},
            "story": {
                "panel_ids": [str(value) for value in story_map.panel_ids],
                "beats_hash": _hash(story_map.beats),
                "claims_hash": _hash(story_map.claims),
                "causal_chain_hash": _hash(story_map.causal_chain),
                "story_map_hash": story_map.story_map_hash,
                "beat_count": len(story_map.beats),
                "claim_count": len(story_map.claims),
                "causal_link_count": len(story_map.causal_chain),
            },
            "selection": selection_summary,
            "slot_registry": slot_summary,
            "candidate": {
                "candidate_hash": _hash(candidate.as_dict()),
                "visual_evidence_hash": candidate.visual_evidence_hash,
                "model_identity_hash": candidate.model_identity_hash,
                "prompt_version": candidate.prompt_version,
                "prompt_sha256": candidate.prompt_sha256,
                "story_map_hash": story_map.story_map_hash,
            },
        }

    @_bound
    def _persist_narration_repair_identity_rejection(
        self,
        *,
        old_identity_hash: str,
        new_identity_hash: str,
        metadata: Mapping[str, Any],
        reason: str,
        model_identity_hash: str,
        prompt: tuple[str, str, str],
    ) -> None:
        if self.cache is None:
            return
        record = {
            "cache_type": NARRATION_REPAIR_IDENTITY_MIGRATION_VERSION,
            "status": "rejected",
            "policy_version": NARRATION_REPAIR_IDENTITY_VERSION,
            "old_identity_hash": old_identity_hash,
            "new_identity_hash": new_identity_hash,
            "canonical_comparison_hash": str(metadata.get("canonical_comparison_hash", "")),
            "counts": dict(metadata.get("counts", {})),
            "mismatch_field": str(metadata.get("mismatch_field", "identity")),
            "reason": str(reason),
            "model_identity_hash": model_identity_hash,
            "prompt_version": prompt[0],
            "prompt_sha256": prompt[1],
        }
        key = "narration-repair-identity-rejection:" + _hash(
            {
                "old_identity_hash": old_identity_hash,
                "new_identity_hash": new_identity_hash,
                "mismatch_field": record["mismatch_field"],
                "model_identity_hash": model_identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
            }
        )
        if not isinstance(self.cache.get(key), Mapping):
            self.cache.put(key, record)

    @_bound
    def _store_narration_repair_candidate(
        self,
        *,
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
        result: NarrationResult,
        failure_codes: Sequence[str],
        visual: VisualStageResult | None = None,
        story_map: StoryMapResult | None = None,
    ) -> None:
        if self.cache is None:
            return
        payload = result.as_dict()
        try:
            identity_metadata = self._narration_repair_identity_metadata(
                source=source,
                prompt=prompt,
                candidate=result,
                visual=visual,
                story_map=story_map,
            )
        except CloudStageError:
            identity_metadata = None
        self.cache.put(
            self._narration_repair_candidate_key(source, prompt),
            {
                "cache_type": NARRATION_REPAIR_CANDIDATE_VERSION,
                "candidate": payload,
                "candidate_hash": _hash(payload),
                "source_identity_hash": _hash(source),
                "model_identity_hash": self.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
                "visual_evidence_hash": result.visual_evidence_hash,
                "failure_codes": list(dict.fromkeys(str(code) for code in failure_codes)),
                "identity_metadata": identity_metadata,
            },
        )

    @_bound
    def _migrate_narration_repair_candidate_record(
        self,
        *,
        record: Mapping[str, Any],
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
        visual: VisualStageResult,
        candidate: NarrationResult,
    ) -> Mapping[str, Any] | None:
        stored_identity = record.get("identity_metadata")
        current_identity: dict[str, Any]
        try:
            current_identity = self._narration_repair_identity_metadata(
                source=source,
                prompt=prompt,
                candidate=candidate,
                visual=visual,
            )
        except CloudStageError as exc:
            self._persist_narration_repair_identity_rejection(
                old_identity_hash=str(record.get("source_identity_hash", "legacy")),
                new_identity_hash=_hash(source),
                metadata=exc.safe_metadata,
                reason="current_identity_invalid",
                model_identity_hash=self.model_identity.identity_hash,
                prompt=prompt,
            )
            return None
        if not isinstance(stored_identity, Mapping):
            self._persist_narration_repair_identity_rejection(
                old_identity_hash=str(record.get("source_identity_hash", "legacy")),
                new_identity_hash=_hash(source),
                metadata={
                    "mismatch_field": "identity_metadata",
                    "counts": {
                        "new_panel_count": len(visual.panels),
                    },
                },
                reason="legacy_identity_metadata_missing",
                model_identity_hash=self.model_identity.identity_hash,
                prompt=prompt,
            )
            return None
        try:
            migration = reconcile_narration_repair_identity(
                stored_identity,
                current_identity,
                old_identity_hash=str(record.get("source_identity_hash", "")),
                new_identity_hash=_hash(source),
                reason="candidate_identity_reconciliation",
            )
        except CloudStageError as exc:
            self._persist_narration_repair_identity_rejection(
                old_identity_hash=str(record.get("source_identity_hash", "")),
                new_identity_hash=_hash(source),
                metadata=exc.safe_metadata,
                reason="semantic_identity_mismatch",
                model_identity_hash=self.model_identity.identity_hash,
                prompt=prompt,
            )
            return None
        persist_narration_repair_identity_migration(
            self.cache,
            stored_identity,
            current_identity,
            old_identity_hash=str(record.get("source_identity_hash", "")),
            new_identity_hash=_hash(source),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            reason="candidate_identity_reconciliation",
        )
        migrated = dict(record)
        migrated["identity_metadata"] = current_identity
        migrated["identity_migration"] = migration
        migrated["source_identity_hash"] = _hash(source)
        self.cache.put(self._narration_repair_candidate_key(source, prompt), migrated)
        return migrated

    @_bound
    def _load_narration_repair_candidate(
        self,
        *,
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
        visual: VisualStageResult,
    ) -> tuple[NarrationResult, tuple[str, ...]] | None:
        if self.cache is None:
            return None
        record = self.cache.get(self._narration_repair_candidate_key(source, prompt))
        if (
            not isinstance(record, Mapping)
            or record.get("cache_type") != NARRATION_REPAIR_CANDIDATE_VERSION
        ):
            record = None
            iterator = getattr(self.cache, "iter_records", None)
            if callable(iterator):
                for candidate_record in iterator(cache_type=NARRATION_REPAIR_CANDIDATE_VERSION):
                    if (
                        not isinstance(candidate_record, Mapping)
                        or candidate_record.get("model_identity_hash")
                        != self.model_identity.identity_hash
                        or candidate_record.get("prompt_version") != prompt[0]
                        or candidate_record.get("prompt_sha256") != prompt[1]
                    ):
                        continue
                    payload = candidate_record.get("candidate")
                    if not isinstance(payload, Mapping):
                        continue
                    try:
                        candidate = NarrationResult.from_dict(payload)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if candidate_record.get("candidate_hash") != _hash(candidate.as_dict()):
                        continue
                    migrated = self._migrate_narration_repair_candidate_record(
                        record=candidate_record,
                        source=source,
                        prompt=prompt,
                        visual=visual,
                        candidate=candidate,
                    )
                    if migrated is not None:
                        record = migrated
                        break
            if record is None:
                return None
        payload = record.get("candidate")
        if not isinstance(payload, Mapping):
            return None
        try:
            candidate = NarrationResult.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return None
        migrated_record = self._migrate_narration_repair_candidate_record(
            record=record,
            source=source,
            prompt=prompt,
            visual=visual,
            candidate=candidate,
        )
        if migrated_record is None:
            return None
        record = migrated_record
        if (
            record.get("candidate_hash") != _hash(candidate.as_dict())
            or record.get("source_identity_hash") != _hash(source)
            or record.get("model_identity_hash") != self.model_identity.identity_hash
            or record.get("prompt_version") != prompt[0]
            or record.get("prompt_sha256") != prompt[1]
            or candidate.visual_evidence_hash != visual.visual_evidence_hash
            or not _narration_result_is_usable(
                candidate,
                visual,
                require_duration=False,
                require_grounding=True,
            )
        ):
            return None
        failures = tuple(str(code) for code in record.get("failure_codes", ()))
        expected = self._narration_contract_failures(candidate)
        if not failures or tuple(dict.fromkeys(failures)) != expected:
            return None
        return candidate, expected

    @_bound
    def _narration_repair_result_key(
        self,
        *,
        source: Mapping[str, Any],
        targeted_repair: Mapping[str, Any],
        prompt: tuple[str, str, str],
    ) -> str:
        return _cache_key(
            NARRATION_REPAIR_VERSION,
            self._repair_cache_source(source, targeted_repair),
            self.model_identity,
            prompt,
        )

    @_bound
    def _store_narration_repair_result(
        self,
        *,
        source: Mapping[str, Any],
        targeted_repair: Mapping[str, Any],
        prompt: tuple[str, str, str],
        result: NarrationResult,
    ) -> None:
        if self.cache is None:
            return
        payload = result.as_dict()
        repair_report = result.qc_report.get("narration_repair", {})
        micro_compaction = repair_report.get("micro_compaction", {})
        self.cache.put(
            self._narration_repair_result_key(
                source=source,
                targeted_repair=targeted_repair,
                prompt=prompt,
            ),
            {
                "cache_type": NARRATION_REPAIR_RESULT_VERSION,
                "result": payload,
                "result_hash": _hash(payload),
                "source_identity_hash": _hash(source),
                "model_identity_hash": self.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
                "repair_attempt": int(targeted_repair.get("repair_attempt", 1)),
                "candidate_hash": str(targeted_repair.get("candidate_hash", "")),
                "position_registry_version": str(
                    targeted_repair.get("position_registry_version", "")
                ),
                "slot_order_hash": str(targeted_repair.get("slot_order_hash", "")),
                "passage_lineage_version": str(targeted_repair.get("passage_lineage_version", "")),
                "passage_lineage_hash": str(targeted_repair.get("passage_lineage_hash", "")),
                "micro_compaction_version": str(micro_compaction.get("version", "")),
                "micro_compaction_result_hash": str(micro_compaction.get("result_hash", "")),
            },
        )

    @_bound
    def _load_narration_repair_result(
        self,
        *,
        source: Mapping[str, Any],
        targeted_repair: Mapping[str, Any],
        prompt: tuple[str, str, str],
        candidate: NarrationResult,
        visual: VisualStageResult,
        removable_passage_ids: Sequence[str],
    ) -> NarrationResult | None:
        if self.cache is None:
            return None
        record = self.cache.get(
            self._narration_repair_result_key(
                source=source,
                targeted_repair=targeted_repair,
                prompt=prompt,
            )
        )
        if (
            not isinstance(record, Mapping)
            or record.get("cache_type") != NARRATION_REPAIR_RESULT_VERSION
        ):
            return None
        payload = record.get("result")
        if not isinstance(payload, Mapping):
            return None
        try:
            result = NarrationResult.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return None
        repair_report = result.qc_report.get("narration_repair", {})
        micro_compaction = repair_report.get("micro_compaction", {})
        if (
            record.get("result_hash") != _hash(result.as_dict())
            or record.get("source_identity_hash") != _hash(source)
            or record.get("model_identity_hash") != self.model_identity.identity_hash
            or record.get("prompt_version") != prompt[0]
            or record.get("prompt_sha256") != prompt[1]
            or record.get("candidate_hash") != str(targeted_repair.get("candidate_hash", ""))
            or record.get("position_registry_version")
            != str(targeted_repair.get("position_registry_version", ""))
            or record.get("slot_order_hash") != str(targeted_repair.get("slot_order_hash", ""))
            or record.get("passage_lineage_version")
            != str(targeted_repair.get("passage_lineage_version", ""))
            or record.get("passage_lineage_hash")
            != str(targeted_repair.get("passage_lineage_hash", ""))
            or record.get("micro_compaction_version") != NARRATION_MICRO_COMPACTION_VERSION
            or micro_compaction.get("version") != NARRATION_MICRO_COMPACTION_VERSION
            or record.get("micro_compaction_result_hash") != micro_compaction.get("result_hash")
            or not _narration_result_is_usable(
                result,
                visual,
                require_duration=True,
                require_grounding=True,
            )
        ):
            return None
        result = self._narration_repair_scope_reconciled(
            candidate,
            result,
            removable_passage_ids,
            trusted_lineage={
                str(row["passage_id"]): row
                for row in self._reconstruct_narration_repair_passage_lineage(
                    candidate,
                    targeted_repair.get("position_registry"),
                )["passages"]
            },
        )
        if result is None:
            return None
        report = dict(result.qc_report)
        report["narration_repair"] = {
            "contract_version": NARRATION_REPAIR_VERSION,
            "micro_compaction": dict(micro_compaction),
            "scope": "position_locked_rewrite_vector",
            "candidate_hash": str(targeted_repair.get("candidate_hash", "")),
            "position_registry_version": str(targeted_repair.get("position_registry_version", "")),
            "slot_order_hash": str(targeted_repair.get("slot_order_hash", "")),
            "passage_lineage_version": str(targeted_repair.get("passage_lineage_version", "")),
            "passage_lineage_hash": str(targeted_repair.get("passage_lineage_hash", "")),
            "failure_codes": list(targeted_repair.get("failure_codes", ())),
            "attempts": int(record.get("repair_attempt", 1)),
            "provider_stage": "narration_repair",
            "cache_reused": True,
        }
        return replace(result, qc_report=report)

    @staticmethod
    @_bound
    def _narration_passage_ids(result: NarrationResult) -> tuple[str, ...]:
        return tuple(str(passage.get("passage_id", "")) for passage in result.passages)

    @staticmethod
    @_bound
    def _removable_narration_passage_ids(
        candidate: NarrationResult,
    ) -> tuple[str, ...]:
        if len(candidate.passages) <= 4:
            return ()
        rows = [
            passage
            for passage in candidate.passages[:-1]
            if str(passage.get("passage_id", "")).strip()
        ]
        rows.sort(
            key=lambda passage: (
                len(passage.get("claim_ids", ())),
                len(passage.get("evidence_panel_ids", ())),
                str(passage.get("passage_id", "")),
            )
        )
        return tuple(
            str(passage["passage_id"]) for passage in rows[: max(0, len(candidate.passages) - 4)]
        )

    @staticmethod
    @_bound
    def _narration_repair_scope_compatible(
        candidate: NarrationResult,
        repaired: NarrationResult,
        removable_passage_ids: Sequence[str],
    ) -> bool:
        return (
            CloudStageRunner._narration_repair_scope_reconciled(
                candidate,
                repaired,
                removable_passage_ids,
            )
            is not None
        )

    @staticmethod
    @_bound
    def _narration_repair_scope_reconciled(
        candidate: NarrationResult,
        repaired: NarrationResult,
        removable_passage_ids: Sequence[str],
        trusted_lineage: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> NarrationResult | None:
        if (
            candidate.ending_kind != repaired.ending_kind
            or candidate.story_spine != repaired.story_spine
            or tuple(candidate.observations) != tuple(repaired.observations)
            or len(repaired.passages) < 4
        ):
            return None
        candidate_passages = {str(item.get("passage_id", "")): item for item in candidate.passages}
        repaired_passages = {str(item.get("passage_id", "")): item for item in repaired.passages}
        if len(candidate_passages) != len(candidate.passages) or len(repaired_passages) != len(
            repaired.passages
        ):
            return None
        removed_passage_ids = set(candidate_passages) - set(repaired_passages)
        if not removed_passage_ids.issubset(set(removable_passage_ids)) or set(
            repaired_passages
        ) - set(candidate_passages):
            return None
        for passage_id in set(repaired_passages) & set(candidate_passages):
            before = candidate_passages[passage_id]
            after = repaired_passages[passage_id]
            if trusted_lineage is None:
                for key in ("claim_ids", "evidence_panel_ids"):
                    before_values = before.get(key)
                    after_values = after.get(key)
                    if not isinstance(before_values, (list, tuple)) or not isinstance(
                        after_values, (list, tuple)
                    ):
                        return None
                    before_values = tuple(str(value) for value in before_values)
                    after_values = tuple(str(value) for value in after_values)
                    if (
                        not after_values
                        or len(set(after_values)) != len(after_values)
                        or any(value not in before_values for value in after_values)
                        or tuple(value for value in before_values if value in set(after_values))
                        != after_values
                    ):
                        return None
                continue
            # The trusted claim-evidence closure, not the candidate's stale
            # passage evidence, is the position repair's evidence reference:
            # the registry rebuilds each passage's evidence union from the
            # trusted story map, so a repaired vector carries that closure
            # even when the durable candidate cited fewer or different
            # panels. Claims must still match the candidate exactly.
            reference_row = trusted_lineage.get(passage_id)
            if not isinstance(reference_row, Mapping):
                return None
            for key in ("claim_ids", "evidence_panel_ids"):
                reference_values = reference_row.get(key)
                after_values = after.get(key)
                if not isinstance(reference_values, (list, tuple)) or not isinstance(
                    after_values, (list, tuple)
                ):
                    return None
                reference_tuple = tuple(str(value) for value in reference_values)
                after_values = tuple(str(value) for value in after_values)
                if (
                    not after_values
                    or len(set(after_values)) != len(after_values)
                    or set(after_values) != set(reference_tuple)
                    or tuple(value for value in reference_tuple if value in set(after_values))
                    != after_values
                ):
                    return None
            candidate_claims = before.get("claim_ids")
            repaired_claims = after.get("claim_ids")
            if not isinstance(candidate_claims, (list, tuple)) or not isinstance(
                repaired_claims,
                (list, tuple),
            ):
                return None
            if not {str(value) for value in repaired_claims} <= {
                str(value) for value in candidate_claims
            }:
                return None
        candidate_claims = {
            str(item.get("claim_id", "")): item
            for item in candidate.evidence_graph.get("claims", ())
            if isinstance(item, Mapping)
        }
        repaired_claims = {
            str(item.get("claim_id", "")): item
            for item in repaired.evidence_graph.get("claims", ())
            if isinstance(item, Mapping)
        }
        retained_claim_ids = {
            str(claim_id)
            for passage in repaired.passages
            for claim_id in passage.get("claim_ids", ())
        }
        if (
            not retained_claim_ids
            or set(repaired_claims) != retained_claim_ids
            or set(repaired_claims) - set(candidate_claims)
        ):
            return None
        for claim_id in retained_claim_ids:
            before = candidate_claims[claim_id]
            after = repaired_claims[claim_id]
            before_refs = tuple(
                str(value)
                for value in before.get("evidence_panel_ids", before.get("panel_ids", ()))
            )
            after_refs = tuple(
                str(value) for value in after.get("evidence_panel_ids", after.get("panel_ids", ()))
            )
            if before.get("claim_type") != after.get("claim_type") or before_refs != after_refs:
                return None
        canonical_passages = []
        for passage in repaired.passages:
            passage_id = str(passage.get("passage_id", ""))
            canonical = dict(passage)
            original = candidate_passages[passage_id]
            canonical["editorial_role"] = original.get("editorial_role", "")
            canonical["claim_ids"] = list(passage.get("claim_ids", ()))
            canonical["evidence_panel_ids"] = list(passage.get("evidence_panel_ids", ()))
            canonical_passages.append(canonical)
        canonical_claims = [
            dict(claim)
            for claim_id, claim in candidate_claims.items()
            if claim_id in retained_claim_ids
        ]
        return replace(
            repaired,
            passages=tuple(canonical_passages),
            ending_kind=candidate.ending_kind,
            observations=tuple(dict(item) for item in candidate.observations),
            continuity_ledger=dict(candidate.continuity_ledger),
            evidence_graph={"claims": canonical_claims},
            story_spine=dict(candidate.story_spine),
        )

    @_bound
    def run_narration_repair_candidate(
        self,
        candidate: NarrationResult,
        visual: VisualStageResult,
        story_map: StoryMapResult,
        *,
        panels: Sequence[CloudPanelInput] | None = None,
    ) -> NarrationResult:
        """Run only the bounded compaction repair from compact durable stages.

        This boundary deliberately accepts metadata-only visual rows and does
        not call normal narration generation.  The candidate remains outside
        the final narration cache until the strict final admission checks in
        ``run_narration`` pass.
        """

        prompt = self.prompts["narration"]
        if (
            not _stage_result_identity_is_compatible(
                candidate.model_identity_hash,
                self.model_identity,
                stage="narration",
            )
            or candidate.prompt_version != prompt[0]
            or candidate.prompt_sha256 != prompt[1]
        ):
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        if candidate.model_identity_hash != self.model_identity.identity_hash:
            candidate = replace(
                candidate,
                model_identity_hash=self.model_identity.identity_hash,
            )
        compact_visual, compact_story_map = self._compact_narration_repair_context(
            candidate,
            visual,
            story_map,
        )
        failure_codes = self._narration_contract_failures(candidate)
        if not failure_codes:
            raise CloudStageError("cloud.narrative_repair_not_needed")
        compact_panels = None
        if panels is not None:
            panels_by_id = {str(panel.panel_id): panel for panel in panels}
            try:
                compact_panels = tuple(
                    panels_by_id[panel_id] for panel_id in compact_visual.panel_ids
                )
            except KeyError:
                raise CloudStageError(
                    "cloud.narrative_repair_identity_mismatch",
                    reviewable=True,
                ) from None
        if compact_panels is None:
            observations = [dict(item) for item in candidate.observations]
            visual_rows = {str(item.get("panel_id", "")): item for item in compact_visual.panels}
            for observation in observations:
                panel_id = str(observation.get("panel_id", ""))
                visual_item = visual_rows.get(panel_id)
                if (
                    visual_item is None
                    or str(observation.get("source_asset_id", ""))
                    != str(visual_item.get("source_asset_id", ""))
                    or panel_id
                    not in {str(value) for value in observation.get("evidence_refs", ())}
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_identity_mismatch",
                        reviewable=True,
                    )
            structural = {
                "continuity_ledger": dict(candidate.continuity_ledger),
                "coverage_manifest": {
                    "total_panels": len(observations),
                    "processed_panels": len(observations),
                    "total_canonical_panels": len(observations),
                    "persisted_canonical_panels": len(observations),
                    "processed_canonical_panel_count": len(observations),
                    "panel_ids": list(compact_visual.panel_ids),
                    "source_content_coverage_ratio": 1.0,
                    "unresolved_material_area": 0,
                    "material_unresolved_regions": [],
                    "reconciliation_complete": True,
                },
            }
        else:
            observations, structural = self._narration_observations(
                compact_visual,
                compact_panels,
            )
        source = {
            "editorial_selection_version": EDITORIAL_SELECTION_VERSION,
            "panel_ids": list(compact_visual.panel_ids),
            "visual_source_hash": compact_visual.source_hash,
            "visual_evidence_hash": compact_visual.visual_evidence_hash,
            "visual_observations": observations,
            "story_map": compact_story_map.as_dict(),
            "duration_contract": {
                **script.narration_duration_contract("dramatic"),
                "minimum_s": STANDARD_FINAL_DURATION_MIN_SECONDS,
                "maximum_s": STANDARD_FINAL_DURATION_MAX_SECONDS,
                "target_word_min": 115,
                "target_word_max": 125,
            },
        }
        self._store_narration_repair_candidate(
            source=source,
            prompt=prompt,
            result=candidate,
            failure_codes=failure_codes,
            visual=compact_visual,
            story_map=compact_story_map,
        )
        return self._run_targeted_narration_repair(
            prompt,
            source,
            observations,
            structural,
            compact_story_map,
            compact_visual,
            candidate,
            failure_codes,
        )

    @_bound
    def _run_targeted_narration_repair(
        self,
        prompt: tuple[str, str, str],
        source: Mapping[str, Any],
        observations: Sequence[Mapping[str, Any]],
        structural: Mapping[str, Any],
        story_map: StoryMapResult,
        visual: VisualStageResult,
        candidate: NarrationResult,
        failure_codes: Sequence[str],
        *,
        allow_passage_removal: bool = True,
        passage_word_budgets: Mapping[str, int] | None = None,
        passage_word_targets: Mapping[str, int] | None = None,
    ) -> NarrationResult:
        """Repair prose or complete low-priority passages without changing evidence scope."""

        candidate_hash = _hash(candidate.as_dict())
        duration_policy_contract = _narration_repair_contract_bounds(
            source.get("duration_policy_contract")
        )
        position_registry = self._build_narration_repair_position_registry(
            candidate,
            story_map,
            passage_word_budgets=passage_word_budgets,
            passage_word_targets=passage_word_targets,
            duration_policy_contract=duration_policy_contract,
        )
        if str(source.get("provider_context_mode", "")) == "locked_story_text_only":
            position_registry["provider_context_mode"] = "locked_story_text_only"
            contexts = source.get("selected_story_context", ())
            position_registry["selected_story_context"] = [
                dict(item) for item in contexts if isinstance(item, Mapping)
            ]
            evidence_contexts = source.get("selected_evidence_context", ())
            position_registry["selected_evidence_context"] = [
                dict(item) for item in evidence_contexts if isinstance(item, Mapping)
            ]
            position_registry["slot_order_hash"] = _hash({
                "base_slot_order_hash": position_registry["slot_order_hash"],
                "provider_context_mode": position_registry["provider_context_mode"],
                "selected_story_context": position_registry["selected_story_context"],
                "selected_evidence_context": position_registry["selected_evidence_context"],
            })
        removable_passage_ids = (
            self._removable_narration_passage_ids(candidate)
            if allow_passage_removal
            else ()
        )
        provider_prior_narration = _narration_repair_provider_prior_context(
            candidate,
            locked_story_text_only=(
                str(source.get("provider_context_mode", "")) == "locked_story_text_only"
            ),
        )
        repair_context = {
            "contract_version": NARRATION_REPAIR_VERSION,
            "micro_compaction_version": NARRATION_MICRO_COMPACTION_VERSION,
            "failure_codes": list(dict.fromkeys(str(code) for code in failure_codes)),
            "candidate_hash": candidate_hash,
            "position_registry_version": position_registry["version"],
            "slot_order_hash": position_registry["slot_order_hash"],
            "passage_lineage_version": position_registry["passage_lineage_version"],
            "passage_lineage_hash": position_registry["passage_lineage_hash"],
            "position_registry": position_registry,
            "position_context": position_registry["provider_positions"],
            "removable_passage_ids": list(removable_passage_ids),
            "immutable_scope": [
                "passage_id",
                "claim_ids",
                "evidence_panel_ids",
                "evidence_graph",
                "observations",
                "ending_kind",
                "story_spine",
            ],
            "target_word_min": duration_policy_contract["target_word_min"],
            "target_word_max": duration_policy_contract["target_word_max"],
            "target_word_count": position_registry["target_word_count"],
            "target_duration_min_s": duration_policy_contract["target_duration_min_s"],
            "target_duration_max_s": duration_policy_contract["target_duration_max_s"],
            "duration_policy_contract": duration_policy_contract,
            "passage_word_budgets": (
                {str(key): int(value) for key, value in passage_word_budgets.items()}
                if passage_word_budgets
                else {}
            ),
            "passage_word_targets": (
                {str(key): int(value) for key, value in (passage_word_targets or {}).items()}
                if passage_word_budgets
                else {}
            ),
            "prior_narration": provider_prior_narration,
        }
        repair_prompt_version = "vision-first-story-analyzer-v3-targeted-position-repair-v21"
        capacity_locked_instruction = (
            "\n\nCAPACITY-LOCKED WORD BUDGET MODE: passage_word_budget_max is a hard ceiling. "
            "Do not move words from one passage into another. The supplied positions for each passage "
            "must remain exactly one complete retained passage; do not merge, split, omit, or cross-mix "
            "passages. Treat each supplied position word_budget as a drafting target, not a quota; natural "
            "concise wording may fall below it. The position targets sum to target_word_count, while the "
            "aggregate target_word_min/target_word_max and every word_budget_max remain the actual acceptance "
            "bounds. The complete vector MUST contain at least target_word_min words. If concise wording falls "
            "below that minimum, add only grounded temporal ordering, action progression, or supported contrast "
            "already present in claim_context; never add filler merely to hit target_word_count. Any position with "
            "word_budget_max <= 9 must be one short sentence with at most that many lexical words; never combine "
            "redundant phrases such as 'again and again' with 'in quick succession'. Count every rewrite separately "
            "before returning, then recount each passage and the complete vector."
            if passage_word_budgets
            else ""
        )
        locked_story_instruction = (
            "\n\nLOCKED STORY TEXT-ONLY MODE: visual observations and the full panel inventory are "
            "intentionally omitted from the provider payload. Rewrite only spoken story prose. "
            "The old spoken_text and passage text are intentionally omitted because they failed review; do not "
            "reconstruct or imitate prior wording. selected_evidence_context contains only visible_facts and "
            "uncertainties from the exact evidence panels assigned to each retained passage. Use those visible facts to add concrete "
            "grounded detail, and obey every uncertainty as a limit: never invent intent, relationship, "
            "outcome, training history, tension, standoff mechanics, suspension/balance mechanics, or a "
            "causal link that is not explicitly supported. Do not write suspended, perfect balance, tension, "
            "standoff, or trained side by side unless those facts literally appear in the matching visible_facts. "
            "Use natural spoken nouns such as woman, man, or fighter; never use 'the female' or 'the male' as noun phrases, "
            "and never omit the object after a possessive (for example, do not write 'swung his forward'). A weapon explicitly "
            "described as sheathed must stay sheathed in prose; do not call it drawn, ready, raised, or already in use. "
            "Never move a visible fact across passages. "
            "Use selected_story_context to understand teaser rewind and temporal/causal bridge "
            "permissions, but do not add facts from beat summaries unless the locked claim context or "
            "matching selected_evidence_context already supports them. Avoid visual-inventory language such as appears, stands, sits, "
            "shows, close-up, panel, sequence, view, or visible. At least two distinct retained rewrites must "
            "contain a truthful grounded bridge such as as, when, before, after, while, but, yet, or so; do not "
            "return five disconnected observations. Do not change any IDs."
            if str(source.get("provider_context_mode", "")) == "locked_story_text_only"
            else ""
        )
        repair_prompt_text = (
            f"{prompt[2]}\n\n{NARRATION_REPAIR_INSTRUCTION}{capacity_locked_instruction}{locked_story_instruction}"
        )
        repair_prompt = (
            repair_prompt_version,
            _hash(repair_prompt_text),
            repair_prompt_text,
        )
        cached_repair = self._load_narration_repair_result(
            source=source,
            targeted_repair=repair_context,
            prompt=repair_prompt,
            candidate=candidate,
            visual=visual,
            removable_passage_ids=removable_passage_ids,
        )
        if cached_repair is not None:
            return cached_repair

        last_error = CloudStageError(
            "cloud.narrative_duration_out_of_range",
            reviewable=True,
        )
        attempt_limit = (
            NARRATION_REPAIR_POSITION_MAX_ATTEMPTS
            if position_registry["version"] == NARRATION_REPAIR_POSITION_REGISTRY_VERSION
            else NARRATION_REPAIR_MAX_ATTEMPTS
        )
        carried_retry_feedback = ""
        for attempt in range(attempt_limit):
            context = {
                **repair_context,
                "repair_attempt": attempt + 1,
            }
            if carried_retry_feedback:
                context["outer_retry_feedback"] = carried_retry_feedback
            try:
                repaired = self._run_narration_batched(
                    prompt,
                    source,
                    observations,
                    structural,
                    story_map,
                    visual,
                    stage="narration_repair",
                    targeted_repair=context,
                    request_prompt_version=repair_prompt_version,
                    request_prompt_sha256=repair_prompt[1],
                    request_prompt_text=repair_prompt_text,
                    repair_position_registry=position_registry,
                    repair_candidate=candidate,
                )
                reconciled = self._narration_repair_scope_reconciled(
                    candidate,
                    repaired,
                    removable_passage_ids,
                    trusted_lineage={
                        str(row["passage_id"]): row
                        for row in self._reconstruct_narration_repair_passage_lineage(
                            candidate,
                            position_registry,
                        )["passages"]
                    },
                )
                if reconciled is None:
                    self.last_response_shape_metrics.update(
                        {
                            "reconciled_scope_ok": False,
                            "reconciled_failed_predicates": ["scope_compatibility"],
                            "reconciled_failed_predicate": "scope_compatibility",
                        }
                    )
                    raise CloudStageError(
                        "cloud.narrative_repair_scope_invalid",
                        reviewable=True,
                        safe_metadata=self._response_shape_metrics_for_failure(
                            "cloud.narrative_repair_scope_invalid"
                        ),
                    )
                repaired = reconciled
                self.last_response_shape_metrics.update(
                    self._narration_repair_result_shape_metrics(
                        repaired,
                        visual,
                        scope_ok=True,
                    )
                )
                if not _narration_result_is_usable(
                    repaired,
                    visual,
                    require_duration=True,
                    require_grounding=True,
                    duration_policy_contract=duration_policy_contract,
                ):
                    failures = self._narration_contract_failures(
                        repaired, duration_policy_contract
                    )
                    failure_code = failures[0] if failures else "cloud.narrative_not_grounded"
                    last_error = CloudStageError(
                        failure_code,
                        reviewable=True,
                        safe_metadata=self._response_shape_metrics_for_failure(failure_code),
                    )
                    continue
                report = dict(repaired.qc_report)
                micro_compaction = self.last_response_shape_metrics.get(
                    "micro_compaction",
                    {
                        "version": NARRATION_MICRO_COMPACTION_VERSION,
                        "applied": False,
                        "operation_count": 0,
                        "operation_types": [],
                        "result_hash": _hash({"rewrites": []}),
                    },
                )
                report["narration_repair"] = {
                    "contract_version": NARRATION_REPAIR_VERSION,
                    "micro_compaction": dict(micro_compaction),
                    "scope": "position_locked_rewrite_vector",
                    "candidate_hash": candidate_hash,
                    "position_registry_version": position_registry["version"],
                    "slot_order_hash": position_registry["slot_order_hash"],
                    "passage_lineage_version": position_registry["passage_lineage_version"],
                    "passage_lineage_hash": position_registry["passage_lineage_hash"],
                    "failure_codes": list(repair_context["failure_codes"]),
                    "removable_passage_ids": list(removable_passage_ids),
                    "removed_passage_ids": [
                        passage_id
                        for passage_id in self._narration_passage_ids(candidate)
                        if passage_id not in self._narration_passage_ids(repaired)
                    ],
                    "attempts": attempt + 1,
                    "provider_stage": "narration_repair",
                    "duration_policy_contract": duration_policy_contract,
                    "cache_reused": False,
                }
                repaired = replace(repaired, qc_report=report)
                self._store_narration_repair_result(
                    source=source,
                    targeted_repair=context,
                    prompt=repair_prompt,
                    result=repaired,
                )
                return repaired
            except CloudStageError as exc:
                if exc.code == "cloud.request_budget_exceeded":
                    if attempt == 0:
                        last_error = exc
                    break
                last_error = exc
                metadata = exc.safe_metadata if isinstance(exc.safe_metadata, Mapping) else {}
                observed = metadata.get("total_word_count")
                carried_retry_feedback = _narration_retry_feedback(
                    exc.code,
                    observed_word_count=(
                        int(observed)
                        if isinstance(observed, int) and not isinstance(observed, bool)
                        else None
                    ),
                    target_word_min=int(repair_context["target_word_min"]),
                    target_word_max=int(repair_context["target_word_max"]),
                    target_word_count=int(repair_context["target_word_count"]),
                    capacity_locked=bool(repair_context.get("passage_word_budgets")),
                    failed_predicate=str(metadata.get("failed_predicate", "")),
                    per_position_word_counts=metadata.get("per_position_word_counts"),
                    expected_ranges=metadata.get("expected_ranges"),
                )
                if attempt + 1 >= NARRATION_REPAIR_MAX_ATTEMPTS:
                    break
        raise last_error
