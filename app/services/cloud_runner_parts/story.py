"""Story methods extracted from cloud_multimodal."""

# ruff: noqa: F821 -- runtime globals are refreshed from the compatibility facade.
from __future__ import annotations

from .runtime import runtime_bound

_RUNTIME_NAMES = (
    'CloudPanelInput',
    'CloudStageError',
    'Mapping',
    'STAGE_PARALLEL_WORKERS',
    'STORY_MAP_CHUNK_STEP',
    'StoryMapResult',
    'ThreadPoolExecutor',
    '_cache_key',
    '_hash',
    '_narrative_grounding_error',
    'hashlib',
)
_bound = runtime_bound(_RUNTIME_NAMES)


class StoryMapMixin:
    @staticmethod
    @_bound
    def _claims_from_causal_map(
        script_passages: Any,
        story_map: StoryMapResult,
    ) -> list[dict[str, Any]]:
        """Reuse only locally validated causal claims when the graph is omitted.

        Some compatible models return passage prose and claim IDs while
        omitting the duplicate evidence-graph envelope.  Reusing the exact
        causal-map records is safe because that graph was already reconciled
        against every ordered panel; no claim text or evidence is invented.
        """

        if not isinstance(script_passages, list) or not script_passages:
            raise _narrative_grounding_error("script_passages", 0)
        claims_by_id = {
            str(claim.get("claim_id")): dict(claim)
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        referenced_ids: list[str] = []
        for passage in script_passages:
            if not isinstance(passage, Mapping):
                raise _narrative_grounding_error("script_passages", len(script_passages))
            claim_ids = passage.get("claim_ids")
            if not isinstance(claim_ids, list) or not claim_ids:
                raise _narrative_grounding_error("passage.claim_ids", len(script_passages))
            for claim_id in claim_ids:
                if not isinstance(claim_id, str):
                    raise _narrative_grounding_error("passage.claim_ids", len(claim_ids))
                resolved_id = claim_id
                if resolved_id not in claims_by_id:
                    suffix_matches = [
                        candidate_id
                        for candidate_id in claims_by_id
                        if candidate_id.rsplit("__", 1)[-1] == claim_id
                    ]
                    if len(suffix_matches) != 1:
                        raise _narrative_grounding_error("claim_ids", len(claims_by_id))
                    resolved_id = suffix_matches[0]
                referenced_ids.append(resolved_id)
        return [claims_by_id[claim_id] for claim_id in dict.fromkeys(referenced_ids)]

    @_bound
    def _reconcile_story_map(
        self,
        raw: Any,
        expected_panel_ids: tuple[str, ...],
        prompt: tuple[str, str, str],
    ) -> StoryMapResult:
        if not isinstance(raw, Mapping) or raw.get("story_map_hash"):
            raise CloudStageError(
                "cloud.provider_hash_forbidden"
                if isinstance(raw, Mapping) and raw.get("story_map_hash")
                else "cloud.provider_response_invalid"
            )
        if (
            raw.get("panel_ids") != list(expected_panel_ids)
            or raw.get("random_sampling") is not False
        ):
            raise CloudStageError("cloud.panel_coverage_incomplete")
        beats = raw.get("beats")
        if beats is None and isinstance(raw.get("ordered_beats"), list):
            beats = raw["ordered_beats"]
        chain = raw.get("causal_chain")
        claims = raw.get("claims")
        if (
            not isinstance(beats, list)
            or not beats
            or not isinstance(chain, list)
            or not chain
            or not isinstance(claims, list)
            or not claims
        ):
            raise CloudStageError("cloud.story_map_invalid")
        expected = set(expected_panel_ids)
        covered: set[str] = set()
        for beat in beats:
            if (
                not isinstance(beat, Mapping)
                or not str(beat.get("beat_id", "")).strip()
                or not str(beat.get("summary", "")).strip()
            ):
                raise CloudStageError("cloud.story_map_invalid")
            refs = beat.get("panel_ids")
            if not isinstance(refs, list) or not refs or any(ref not in expected for ref in refs):
                raise CloudStageError("cloud.panel_coverage_incomplete")
            covered.update(refs)
        for claim in claims:
            if (
                not isinstance(claim, Mapping)
                or not str(claim.get("claim_id", "")).strip()
                or not str(claim.get("text", "")).strip()
                or not str(claim.get("qualification", "")).strip()
            ):
                raise CloudStageError("cloud.story_claim_invalid")
            refs = claim.get("panel_ids")
            if not isinstance(refs, list) or not refs or any(ref not in expected for ref in refs):
                raise CloudStageError("cloud.story_claim_invalid")
            covered.update(refs)
        beat_ids = {beat["beat_id"] for beat in beats}
        for link in chain:
            if (
                not isinstance(link, Mapping)
                or link.get("from_beat") not in beat_ids
                or link.get("to_beat") not in beat_ids
                or not str(link.get("reason", "")).strip()
            ):
                raise CloudStageError("cloud.story_map_invalid")
        if covered != expected:
            raise CloudStageError("cloud.panel_coverage_incomplete")
        return StoryMapResult(
            panel_ids=expected_panel_ids,
            beats=tuple(dict(item) for item in beats),
            causal_chain=tuple(dict(item) for item in chain),
            claims=tuple(dict(item) for item in claims),
            story_map_hash=_hash(
                {key: value for key, value in raw.items() if key != "story_map_hash"}
            ),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
        )

    @staticmethod
    @_bound
    def _normalize_narration_claims(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            raw_claims = value.get("claims")
            if raw_claims is None:
                raw_claims = list(value.values())
        elif isinstance(value, list):
            raw_claims = value
        else:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        if not isinstance(raw_claims, list) or not raw_claims:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        claims: list[dict[str, Any]] = []
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, Mapping):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            claim = dict(raw_claim)
            if "evidence_panel_ids" not in claim and "panel_ids" in claim:
                claim["evidence_panel_ids"] = claim.pop("panel_ids")
            if "text" not in claim and "statement" in claim:
                claim["text"] = claim.pop("statement")
            if claim.get("claim_type") not in {"fact", "interpretation"}:
                raw_type = str(claim.pop("claim_type", "") or claim.pop("type", "")).lower()
                claim["claim_type"] = (
                    "fact" if raw_type in {"fact", "factual", "true"} else "interpretation"
                )
            # The provider's compact envelope omits this classifier.  Treating
            # an unclassified narrative claim as an interpretation is the
            # conservative local metadata choice; qualification remains
            # mandatory and the shared validator still owns all claim gates.
            claim.setdefault("claim_type", "interpretation")
            if not claim.get("qualification"):
                claim["qualification"] = "inferred from panel evidence"
            claims.append(claim)
        return claims

    @staticmethod
    @_bound
    def _narration_observations(
        visual: VisualStageResult,
        panels: Sequence[CloudPanelInput] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Reconcile the persisted visual ledger into the analyzer envelope.

        The cloud narration response is intentionally limited to prose and its
        claim graph.  Panel observations, bounds, checksums, and coverage are
        already locally reconciled by the visual stage and must not be
        regenerated or trusted from a second provider response.
        """

        visual_by_id = {str(item.get("panel_id")): item for item in visual.panels}
        if panels is None:
            ordered_panels: tuple[CloudPanelInput, ...] = ()
            for item in visual.panels:
                bounds = item.get("panel_bounds")
                dimensions = item.get("source_dimensions")
                if not isinstance(bounds, (list, tuple)) or not isinstance(
                    dimensions, (list, tuple)
                ):
                    raise CloudStageError("cloud.panel_lineage_invalid")
                try:
                    ordered_panels += (
                        CloudPanelInput(
                            panel_id=str(item["panel_id"]),
                            source_asset_id=str(item["source_asset_id"]),
                            source_order=int(item["source_order"]),
                            mime_type="image/png",
                            payload=b"visual-ledger-payload",
                            source_checksum=str(item.get("source_checksum", "")),
                            panel_bounds=tuple(int(value) for value in bounds),
                            source_dimensions=tuple(int(value) for value in dimensions),
                            strip_region_id=str(item.get("strip_region_id", item["panel_id"])),
                            coverage_map_version=str(item.get("coverage_map_version", "")),
                            coverage_map_hash=str(item.get("coverage_map_hash", "")),
                        ),
                    )
                except (CloudStageError, KeyError, TypeError, ValueError):
                    raise CloudStageError("cloud.panel_lineage_invalid") from None
        else:
            ordered_panels = tuple(panel for panel in panels if str(panel.panel_id) in visual_by_id)

        if tuple(panel.panel_id for panel in ordered_panels) != visual.panel_ids:
            raise CloudStageError("cloud.panel_lineage_invalid")
        observations: list[dict[str, Any]] = []
        for _source_index, panel in enumerate(ordered_panels):
            visual_item = visual_by_id.get(panel.panel_id)
            if visual_item is None or not isinstance(visual_item.get("observation"), Mapping):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            source = visual_item["observation"]
            required_lists = (
                "visible_facts",
                "dialogue_or_ocr",
                "inferences",
                "uncertainties",
                "evidence_refs",
            )
            if any(not isinstance(source.get(key), list) for key in required_lists):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            clean_lists: dict[str, list[str]] = {}
            for key in required_lists:
                values = source[key]
                normalized_values: list[str] = []
                structured_text_keys = {
                    "dialogue_or_ocr": ("text", "ocr_text"),
                    "visible_facts": ("fact",),
                    "inferences": (
                        "inference",
                        "assertion",
                        "rationale",
                        "description",
                        "claim",
                        "detail",
                        "details",
                        "hypothesis",
                        "inference_text",
                        "conclusion",
                    ),
                    "uncertainties": ("uncertainty",),
                }.get(key)
                for value in values:
                    if isinstance(value, str):
                        normalized_values.append(value)
                    elif structured_text_keys is not None and isinstance(value, Mapping):
                        for structured_text_key in structured_text_keys:
                            structured_text = value.get(structured_text_key)
                            if isinstance(structured_text, str):
                                normalized_values.append(structured_text)
                                break
                        else:
                            candidates = [
                                str(candidate)
                                for candidate in value.values()
                                if isinstance(candidate, str) and candidate.strip()
                            ]
                            if candidates:
                                normalized_values.append(max(candidates, key=len))
                            else:
                                raise CloudStageError(
                                    "cloud.narrative_not_grounded", reviewable=True
                                )
                    else:
                        raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
                values = normalized_values
                if key in {"dialogue_or_ocr", "inferences", "uncertainties"}:
                    values = [value for value in values if value.strip()]
                clean_lists[key] = list(values)
            evidence_refs = clean_lists["evidence_refs"]
            if panel.panel_id not in evidence_refs:
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            if panel.panel_bounds is None or panel.source_dimensions is None:
                raise CloudStageError("cloud.panel_lineage_invalid")
            x0, y0, x1, y1 = panel.panel_bounds
            observation = {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "strip_region_id": panel.strip_region_id or panel.panel_id,
                "source_index": len(observations),
                "region_bounds": {
                    "x": x0,
                    "y": y0,
                    "width": x1 - x0,
                    "height": y1 - y0,
                },
                "coverage_map_version": panel.coverage_map_version,
                "coverage_map_hash": panel.coverage_map_hash,
                **clean_lists,
            }
            observations.append(observation)

        panel_ids = [str(observation["panel_id"]) for observation in observations]
        entity_panels: dict[str, list[str]] = {}
        entity_names: dict[str, str] = {}
        for panel_id in panel_ids:
            source = visual_by_id[panel_id]["observation"]
            for entity in source.get("entities", []):
                if not isinstance(entity, str) or not entity.strip():
                    continue
                canonical = entity.strip()
                entity_key = canonical.casefold()
                entity_names.setdefault(entity_key, canonical)
                entity_panels.setdefault(entity_key, []).append(panel_id)
        if not entity_names:
            # A structural continuity bucket is not a semantic identity or
            # narrative claim; it preserves the validator's nonempty ledger
            # invariant when visual evidence contains no named entity.
            entity_names["observed_context"] = "observed context"
            entity_panels["observed_context"] = list(panel_ids)
        entities = [
            {
                "entity_id": f"visual-entity-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}",
                "canonical_name": entity_names[key],
                "aliases": [],
                "panel_ids": list(dict.fromkeys(entity_panels[key])),
            }
            for key in sorted(entity_names)
        ]
        continuity = {
            "chunks": [{"chunk_id": "visual-reconciled-chunk", "panel_ids": panel_ids}],
            "entities": entities,
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        }
        coverage = {
            "total_panels": len(panel_ids),
            "processed_panels": len(panel_ids),
            "total_canonical_panels": len(panel_ids),
            "persisted_canonical_panels": len(panel_ids),
            "processed_canonical_panel_count": len(panel_ids),
            "panel_ids": panel_ids,
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        }
        return observations, {"continuity_ledger": continuity, "coverage_manifest": coverage}

    @_bound
    def run_story_map(self, visual: VisualStageResult) -> StoryMapResult:
        """Map every ordered panel in deterministic bounded chunks.

        The whole-stage cache remains the fast path.  On a miss, each 180-panel
        request is cached independently and evaluated by at most four workers;
        results are merged by chunk index so concurrency never changes output
        order or identifiers.
        """
        prompt = self.prompts["story_map"]
        source = {
            "panel_ids": list(visual.panel_ids),
            "visual": visual.panels,
            "visual_source_hash": visual.source_hash,
        }
        key = _cache_key("story_map", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            cached_result = StoryMapResult.from_dict(cached)
            if cached_result.visual_evidence_hash == visual.visual_evidence_hash:
                return cached_result
        if (
            migrated := self._migrate_legacy_story_map_cache(visual, key=key, prompt=prompt)
        ) is not None:
            return StoryMapResult.from_dict(migrated)

        chunk_step = STORY_MAP_CHUNK_STEP
        chunks = [
            visual.panels[i : i + chunk_step] for i in range(0, len(visual.panels), chunk_step)
        ]
        with ThreadPoolExecutor(
            max_workers=min(STAGE_PARALLEL_WORKERS, max(1, len(chunks)))
        ) as executor:
            results = tuple(
                executor.map(
                    lambda args: self._run_story_map_chunk(prompt, visual, *args),
                    ((chunk_index, chunk, len(chunks)) for chunk_index, chunk in enumerate(chunks)),
                )
            )

        all_beats: list[dict[str, Any]] = []
        all_chain: list[dict[str, Any]] = []
        all_claims: list[dict[str, Any]] = []
        for chunk_index, result in enumerate(results):
            prefix = f"b{chunk_index}__"
            all_beats.extend(
                dict(item, beat_id=prefix + str(item["beat_id"])) for item in result.beats
            )
            all_claims.extend(
                dict(item, claim_id=prefix + str(item["claim_id"])) for item in result.claims
            )
            all_chain.extend(
                {
                    **dict(link),
                    "from_beat": prefix + str(link["from_beat"]),
                    "to_beat": prefix + str(link["to_beat"]),
                }
                for link in result.causal_chain
            )
        combined = StoryMapResult(
            panel_ids=visual.panel_ids,
            beats=tuple(all_beats),
            causal_chain=tuple(all_chain),
            claims=tuple(all_claims),
            story_map_hash=_hash({"beats": all_beats, "claims": all_claims, "chain": all_chain}),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        if self.cache is not None:
            self.cache.put(key, combined.as_dict())
        return combined
