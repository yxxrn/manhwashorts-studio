"""Batch orchestration methods extracted from cloud_multimodal."""

# ruff: noqa: F821 -- runtime globals come from the compatibility facade.
from __future__ import annotations

from .runtime import runtime_bound

_RUNTIME_NAMES = (
    'ChapterJobRecord',
    'ChapterResult',
    'ChapterState',
    'CloudStageError',
    'Mapping',
    'NarrationResult',
    'Path',
    'StoryMapResult',
    'ThreadPoolExecutor',
    'VisualStageResult',
    '_build_cached_prepared_manifest',
    '_build_ephemeral_review_candidates',
    '_build_project_prepared_manifest',
    '_durable_visual_repair_covers_missing_sections',
    '_find_cached_visual_subset',
    '_hash',
    '_materialize_metadata_only_panels',
    '_merge_stream_visual_rows',
    '_migrate_visual_cache_identity',
    '_narration_is_current_visual_repair_checkpoint',
    '_narration_is_legacy_visual_repair_checkpoint',
    '_narration_result_is_usable',
    '_narration_stage_prompt_is_compatible',
    '_panels_for_cached_visual_stage',
    '_peak_rss_kb',
    '_reconcile_narration_full_scope',
    '_restore_project_prepared_manifest',
    '_review_failure_code',
    '_review_resume_visual_story_is_current',
    '_review_section_panel_ids',
    '_seed_visual_subset_cache',
    '_segmentation_checkpoint_identity',
    '_stage_result_identity_is_compatible',
    '_subsample_panels',
    '_validate_job_id',
    '_visual_cache_requires_subset_restore',
    '_visual_cached_row_is_reusable',
    '_visual_panel_ids_requiring_materialization',
    'persist_cloud_chapter',
    'prepare_project_panels',
    'prepared_panel_manifest',
    'replace',
    'sys',
    'time',
    'visual_narrative_repair',
)
_bound = runtime_bound(_RUNTIME_NAMES)


class CloudBatchMixin:
    @_bound
    def _reconcile_cached_narration(
        self,
        narration: NarrationResult,
        visual: VisualStageResult,
        panels: Sequence[CloudPanelInput],
    ) -> NarrationResult:
        """Repair local full-scope fields before cached-state admission.

        A cached narration owns prose and trusted claim lineage; the visual
        stage owns the ordered observation and continuity ledger.  Rebuild
        only those local fields from the current reconciled panel registry so
        a selected-scope repair result cannot be admitted as a full chapter.
        No provider call is valid at this boundary.
        """

        observations, structural = self.runner._narration_observations(
            visual,
            panels,
        )
        return _reconcile_narration_full_scope(
            narration,
            observations=observations,
            structural=structural,
            expected_panel_ids=visual.panel_ids,
            visual_evidence_hash=visual.visual_evidence_hash,
        )

    @_bound
    def run_job(
        self,
        job_id: str,
        panels: Sequence[CloudPanelInput],
        *,
        precomputed_visual: VisualStageResult | None = None,
    ) -> ChapterJobRecord:
        _validate_job_id(job_id)
        record = self.store.load(job_id) or ChapterJobRecord(job_id=job_id)
        record.model_identity_hash = self.runner.model_identity.identity_hash
        try:
            ordered = self.runner._ordered_panels(tuple(panels))
            if precomputed_visual is not None:
                merged_rows = _merge_stream_visual_rows(
                    ({"rows": list(precomputed_visual.panels)},),
                    ordered,
                    rejected_panel_ids=tuple(
                        str(item.get("panel_id", ""))
                        for item in precomputed_visual.rejected_panels
                        if isinstance(item, Mapping)
                    ),
                )
                merged_ids = tuple(str(row["panel_id"]) for row in merged_rows)
                if merged_ids != precomputed_visual.panel_ids:
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                visual = replace(precomputed_visual, panels=merged_rows)
                record.stage_results["visual"] = visual.as_dict()
                self.store.save(record)
            else:
                cached_visual = record.stage_results.get("visual")
                migrated_visual = _migrate_visual_cache_identity(
                    cached_visual,
                    ordered,
                    model_identity=self.runner.model_identity,
                    prompt=self.runner.prompts["visual"],
                    persisted_lineage=record.stage_results.get("narration"),
                )
                if migrated_visual is None:
                    raise KeyError("stale_visual_cache")
                visual = VisualStageResult.from_dict(migrated_visual)
                ordered_ids = tuple(panel.panel_id for panel in ordered)
                rejected_ids = {
                    str(item.get("panel_id", ""))
                    for item in visual.rejected_panels
                    if isinstance(item, Mapping)
                }
                partial_cached_visual = bool(rejected_ids) and (
                    set(visual.panel_ids) | rejected_ids == set(ordered_ids)
                    and not set(visual.panel_ids).intersection(rejected_ids)
                )
                if partial_cached_visual:
                    merged_rows = _merge_stream_visual_rows(
                        ({"rows": list(visual.panels)},),
                        ordered,
                        rejected_panel_ids=tuple(rejected_ids),
                    )
                    visual = replace(visual, panels=merged_rows)
                elif visual.panel_ids != ordered_ids or any(
                    not _visual_cached_row_is_reusable(row, panel)
                    for row, panel in zip(visual.panels, ordered, strict=False)
                ):
                    visual = self.runner.run_visual_evidence(panels)
                if migrated_visual != cached_visual:
                    record.stage_results["visual"] = visual.as_dict()
                    self.store.save(record)
                if "visual" not in record.stage_results:
                    raise KeyError("visual_missing")
        except (KeyError, TypeError, ValueError):
            record.stage_results.pop("visual", None)
            record.stage_results.pop("story_map", None)
            record.stage_results.pop("narration", None)
            try:
                visual = self.runner.run_visual_evidence(panels)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        # Preview-only: reconcile downstream panels to the visual subset so
        # skipped provider-failing panels do not break narration grounding.
        visual_ids = set(visual.panel_ids)
        if len(visual_ids) != len(panels):
            total_before = len(panels)
            panels = tuple(item for item in panels if item.panel_id in visual_ids)
            print(
                f"RUN_JOB_PANELS_FILTER kept={len(panels)} dropped={total_before - len(panels)} of {total_before}",
                file=sys.stderr,
                flush=True,
            )
        try:
            record.stage_results["visual"] = visual.as_dict()
            record.state = ChapterState.VISUAL_ANALYZED
            self.store.save(record)
            story_map = StoryMapResult.from_dict(record.stage_results["story_map"])
            current_story_prompt = self.runner.prompts["story_map"]
            if (
                not _stage_result_identity_is_compatible(
                    story_map.model_identity_hash,
                    self.runner.model_identity,
                    stage="story_map",
                )
                or story_map.prompt_version != current_story_prompt[0]
                or story_map.prompt_sha256 != current_story_prompt[1]
                or story_map.visual_evidence_hash != visual.visual_evidence_hash
            ):
                raise KeyError("stale_story_cache")
        except (KeyError, TypeError, ValueError):
            record.stage_results.pop("story_map", None)
            record.stage_results.pop("narration", None)
            try:
                story_map = self.runner.run_story_map(visual)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        try:
            record.stage_results["story_map"] = story_map.as_dict()
            record.state = ChapterState.STORY_MAPPED
            self.store.save(record)
            narration = NarrationResult.from_dict(record.stage_results["narration"])
            narration = self._reconcile_cached_narration(narration, visual, panels)
            if (
                not _narration_is_current_visual_repair_checkpoint(
                    record, narration, self.runner
                )
                and not _narration_is_legacy_visual_repair_checkpoint(
                    record, narration, self.runner
                )
                and self.runner._narration_contract_failures(narration)
            ):
                # A structurally usable base narration can still violate the strict
                # narration contract (word window, duration, dialogue copy).
                # Repair it through the targeted boundary before admission so
                # persistence never receives a contract-failing narration.
                narration = self.runner.run_narration_repair_candidate(
                    narration,
                    visual,
                    story_map,
                    panels=panels,
                )
            if (
                not _stage_result_identity_is_compatible(
                    narration.model_identity_hash,
                    self.runner.model_identity,
                    stage="narration",
                )
                or not _narration_stage_prompt_is_compatible(record, narration, self.runner)
                or narration.visual_evidence_hash != visual.visual_evidence_hash
                or not _narration_result_is_usable(
                    narration,
                    visual,
                    require_duration=True,
                    require_grounding=True,
                )
            ):
                raise KeyError("stale_narration_cache")
        except CloudStageError as exc:
            return self._record_failure(record, exc)
        except (KeyError, TypeError, ValueError):
            try:
                narration = self.runner.run_narration(visual, story_map, panels=panels)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        try:
            record.stage_results["narration"] = narration.as_dict()
            record.state = ChapterState.SCRIPTED
            record.stage_results["usage"] = {
                "request_count": self.runner.request_count,
                "request_counts": dict(self.runner.request_counts),
                "estimated_cost_usd": round(self.runner.estimated_cost_usd, 8),
            }
            self.store.save(record)
            record.state = ChapterState.READY_TO_RENDER
            self.store.save(record)
            return record
        except CloudStageError as exc:
            return self._record_failure(record, exc)

    @_bound
    def _merge_latest_durable_progress(self, record: ChapterJobRecord) -> ChapterJobRecord:
        """Merge checkpoints written through a separately loaded durable record."""
        load_latest = getattr(self.store, "load", None)
        latest = load_latest(record.job_id) if callable(load_latest) else None
        if latest is not None and latest is not record:
            record.stage_results = {**record.stage_results, **latest.stage_results}
            if len(latest.review_queue) > len(record.review_queue):
                record.review_queue = list(latest.review_queue)
        return record

    @_bound
    def _record_failure(self, record: ChapterJobRecord, exc: CloudStageError) -> ChapterJobRecord:
        record = self._merge_latest_durable_progress(record)
        record.stage_results["usage"] = {
            "request_count": self.runner.request_count,
            "request_counts": dict(self.runner.request_counts),
            "estimated_cost_usd": round(self.runner.estimated_cost_usd, 8),
        }
        safe_metadata = dict(exc.safe_metadata)
        status_code = safe_metadata.get("status_code")
        transient_provider = bool(
            safe_metadata.get("retryable")
            or safe_metadata.get("timeout")
            or status_code == 429
            or isinstance(status_code, int)
            and status_code >= 500
        )
        waiting_for_provider = transient_provider and bool(safe_metadata.get("durable_progress"))
        record.state = (
            ChapterState.WAITING_PROVIDER
            if waiting_for_provider
            else ChapterState.NEEDS_REVIEW
            if exc.reviewable
            else ChapterState.FAILED
        )
        record.error_code = exc.code
        record.error_message = str(exc)
        if waiting_for_provider:
            safe_metadata.setdefault(
                "resume", "retry failed source units from the durable segmentation checkpoint"
            )
        if exc.reviewable or waiting_for_provider:
            review_entry = {"code": exc.code, "reason": str(exc)}
            metrics_for_failure = getattr(self.runner, "_response_shape_metrics_for_failure", None)
            if (
                not safe_metadata
                and callable(metrics_for_failure)
                and (
                    exc.code.startswith("cloud.narrative_")
                    or exc.code == "cloud.request_budget_exceeded"
                )
            ):
                safe_metadata = metrics_for_failure(exc.code)
            if "array_key" in safe_metadata:
                safe_metadata.setdefault("failed_code", exc.code)
                safe_metadata.setdefault("failed_predicate", exc.code)
            if safe_metadata:
                review_entry["safe_metadata"] = safe_metadata
            record.review_queue.append(review_entry)
        self.store.save(record)
        return record

    @_bound
    def _repair_review_narrative(
        self,
        db: Any,
        project_id: str,
        script_row: Any | None,
        panels: Sequence[CloudPanelInput],
        result: ChapterResult | None,
        *,
        visual: VisualStageResult | None = None,
        story_map: StoryMapResult | None = None,
        review_source_upscale_policy: Any,
        review_source_root: Path,
    ) -> tuple[ChapterResult, visual_narrative_repair.FeasibleVisualLedger, tuple[str, ...]]:
        """Build the local feasible ledger and repair only missing sections."""

        from app.models import Project
        from app.services import pipeline, reference_profile, review_source_upscale

        project = db.get(Project, project_id)
        if project is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        profile = reference_profile.resolve_reference_profile(project.template)
        if profile is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        try:
            if isinstance(
                review_source_upscale_policy, review_source_upscale.ReviewSourceUpscalePolicy
            ):
                policy = review_source_upscale_policy
            else:
                policy = review_source_upscale.validate_review_upscale_request(
                    review_source_upscale_policy,
                    silent_reference_review=True,
                    publish_allowed=False,
                )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise CloudStageError(exc.code, reviewable=True) from None
        if policy is None:
            raise CloudStageError("review.upscale_policy_required", reviewable=True)

        current_visual = result.visual if result is not None else visual
        current_story_map = result.story_map if result is not None else story_map
        current_narration = result.narration if result is not None else None
        if current_visual is None or current_story_map is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)

        images = pipeline.image_assets(pipeline.project_assets(db, project_id))
        # The durable prepared manifest is metadata-only (no panel image
        # bytes are retained), so after persistence the DB crop loader is the
        # only source of real payload bytes; the ephemeral builder only works
        # with the in-memory prepared panels that still carry payloads before
        # the manifest round-trip.
        _carries_payloads = any(
            bool(getattr(panel, "payload", b"") or b"")
            and not bool(getattr(panel, "metadata_only", False))
            for panel in panels
        )
        if _carries_payloads:
            candidates, section_to_beats = _build_ephemeral_review_candidates(
                panels,
                current_visual,
                current_story_map,
                profile=profile,
                review_source_upscale_policy=policy,
            )
        else:
            section_names = tuple(
                str(section.get("section", ""))
                for section in (getattr(script_row, "sections", ()) or ())
                if isinstance(section, Mapping) and str(section.get("section", "")).strip()
            )
            section_to_beats = visual_narrative_repair.default_section_to_beats(
                section_names,
                current_story_map.beats,
            )
            analysis = pipeline.latest_analysis(db, project_id)
            eligible_panel_ids = {
                str(region.panel_id)
                for region in (getattr(analysis, "panel_regions", ()) or ())
                if int(region.source_order) > 0
            }
            beat_by_id = {
                str(beat["beat_id"]): beat
                for beat in current_story_map.beats
                if isinstance(beat, Mapping) and str(beat.get("beat_id", "")).strip()
            }
            claim_panel_ids = {
                str(panel_id)
                for claim in (getattr(current_story_map, "claims", ()) or ())
                if isinstance(claim, Mapping)
                for panel_id in (claim.get("panel_ids") or ())
                if str(panel_id).strip()
            }
            beat_evidence_panel_ids: dict[str, tuple[str, ...]] = {}
            for beat_id, beat in beat_by_id.items():
                panel_ids: list[str] = []
                for panel_id in beat.get("panel_ids", ()):
                    panel_id = str(panel_id)
                    if (
                        panel_id in eligible_panel_ids
                        and (not claim_panel_ids or panel_id in claim_panel_ids)
                        and panel_id not in panel_ids
                    ):
                        panel_ids.append(panel_id)
                if panel_ids:
                    beat_evidence_panel_ids[beat_id] = tuple(panel_ids)
            candidates = pipeline._load_reference_panel_fallback_candidates(
                db,
                project_id,
                script_row,
                images,
                profile,
                review_source_upscale_policy=policy,
                section_evidence_panel_ids=beat_evidence_panel_ids,
                section_citations=dict.fromkeys(beat_evidence_panel_ids, ()),
                beats_by_section={beat_id: (beat_id,) for beat_id in beat_evidence_panel_ids},
                allow_persisted_panel_crop_fallback=policy is not None,
                review_source_root=review_source_root,
                allow_conservative_full_panel=policy is not None,
            )
        ledger = visual_narrative_repair.build_feasible_visual_ledger(
            candidates,
            profile=profile,
            model_identity_hash=self.runner.model_identity.identity_hash,
            allow_source_resolution_warning=bool(policy.allow_low_source_resolution_warning),
            allow_conservative_full_panel=policy is not None,
            editorial_sections=tuple(section_to_beats),
        )
        checkpoint_store = getattr(self, "store", None)
        if (
            isinstance(ledger, visual_narrative_repair.FeasibleVisualLedger)
            and checkpoint_store is not None
            and callable(getattr(checkpoint_store, "load", None))
            and callable(getattr(checkpoint_store, "save", None))
        ):
            checkpoint_record = checkpoint_store.load(project_id)
            if checkpoint_record is not None:
                checkpoint_identity = {
                    "version": "review-feasible-ledger-preflight-v1",
                    "repair_contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                    "model_identity_hash": self.runner.model_identity.identity_hash,
                    "visual_evidence_hash": current_visual.visual_evidence_hash,
                    "visual_source_hash": current_visual.source_hash,
                    "story_map_hash": current_story_map.story_map_hash,
                    "story_map_visual_evidence_hash": current_story_map.visual_evidence_hash,
                    "profile_hash": reference_profile.profile_hash(profile),
                    "upscale_policy_id": str(getattr(policy, "policy_id", "")),
                    "section_to_beats": {
                        str(section): [str(beat) for beat in beats]
                        for section, beats in sorted(section_to_beats.items())
                    },
                }
                checkpoint_record.stage_results["feasible_visual_ledger_preflight"] = {
                    "identity": checkpoint_identity,
                    "identity_hash": _hash(checkpoint_identity),
                    "ledger": ledger.as_dict(),
                }
                checkpoint_store.save(checkpoint_record)
        missing = visual_narrative_repair.repair_scope_sections(
            current_narration or {},
            ledger,
            section_to_beats,
        )
        durable_capacity_plan: Mapping[str, Any] | None = None
        durable_prompt_current = False
        if current_narration is not None:
            runner_prompts = getattr(self.runner, "prompts", {})
            repair_prompt = (
                runner_prompts.get("visual_narrative_repair")
                if isinstance(runner_prompts, Mapping)
                else None
            )
            prompt_registry_available = repair_prompt is not None
            durable_prompt_current = bool(
                not prompt_registry_available
                or (
                    current_narration.prompt_version == repair_prompt[0]
                    and current_narration.prompt_sha256 == repair_prompt[1]
                )
            )
            if durable_prompt_current and prompt_registry_available:
                durable_payload = visual_narrative_repair.build_repair_payload(
                    narration=current_narration.as_dict(),
                    story_map=current_story_map.as_dict(),
                    ledger=ledger,
                    section_to_beats=section_to_beats,
                )
                plan = durable_payload.get("capacity_safe_claim_plan")
                if isinstance(plan, Mapping):
                    durable_capacity_plan = plan
        if (
            current_narration is not None
            and durable_prompt_current
            and (durable_capacity_plan is not None or not prompt_registry_available)
            and _durable_visual_repair_covers_missing_sections(
                current_narration,
                ledger=ledger,
                section_to_beats=section_to_beats,
                missing_sections=missing,
                capacity_safe_claim_plan=durable_capacity_plan,
                expected_prompt_version=(repair_prompt[0] if prompt_registry_available else None),
                expected_prompt_sha256=(repair_prompt[1] if prompt_registry_available else None),
            )
        ):
            return result, ledger, missing
        if not ledger.entries:
            rejected = tuple(
                item
                for item in getattr(current_visual, "rejected_panels", ())
                if isinstance(item, Mapping)
            )
            if rejected:
                raise CloudStageError(
                    "visual.capacity_insufficient",
                    reviewable=True,
                    safe_metadata={
                        "accepted_panel_count": len(current_visual.panels),
                        "rejected_panel_count": len(rejected),
                        "rejected_panel_ids": sorted(
                            str(item.get("panel_id", ""))
                            for item in rejected
                            if str(item.get("panel_id", "")).strip()
                        ),
                        "rejection_codes": sorted(
                            {
                                str(item.get("rejection_code", ""))
                                for item in rejected
                                if str(item.get("rejection_code", "")).strip()
                            }
                        ),
                        "feasible_panel_count": len(ledger.entries),
                        "missing_sections": list(missing),
                        "failure_predicate": "no_feasible_visual_ledger_entries",
                    },
                )
            raise CloudStageError("visual.visual_unavailable", reviewable=True)
        repaired = self.runner.run_visual_narrative_repair(
            current_visual,
            current_story_map,
            current_narration,
            ledger,
            section_to_beats,
            panels=panels,
        )
        coalesced_passages, coalesce_provenance = (
            visual_narrative_repair.coalesce_adjacent_duplicate_panel_passages(
                repaired.passages,
                minimum_passage_count=len(section_to_beats),
            )
        )
        if coalesce_provenance:
            qc_report = dict(repaired.qc_report)
            qc_report["visual_sequence_coalesce_v1"] = list(coalesce_provenance)
            repaired = replace(
                repaired,
                passages=tuple(coalesced_passages),
                spoken_text="\n\n".join(
                    str(passage.get("text", "")).strip() for passage in coalesced_passages
                ),
                qc_report=qc_report,
            )
            claims = repaired.evidence_graph.get("claims", [])
            visual_narrative_repair.validate_repaired_panel_references(
                {
                    "claims": claims,
                    "passages": [dict(item) for item in repaired.passages],
                },
                ledger=ledger,
                allowed_claim_ids={
                    str(claim.get("claim_id", "")) for claim in claims if isinstance(claim, Mapping)
                },
            )
        try:
            visual_narrative_repair.validate_repaired_section_visual_coverage(
                repaired.passages,
                ledger=ledger,
                section_to_beats=section_to_beats,
                missing_sections=missing,
            )
        except visual_narrative_repair.VisualNarrativeRepairError as exc:
            raise CloudStageError(exc.code, reviewable=exc.reviewable) from None
        if current_narration is not None and repaired == current_narration:
            raise CloudStageError(
                "visual.narrative_repair_ungrounded",
                reviewable=True,
            )
        return (
            ChapterResult(
                ChapterState.READY_TO_RENDER, current_visual, current_story_map, repaired
            ),
            ledger,
            missing,
        )

    @_bound
    def run_batch(
        self, jobs: Mapping[str, Sequence[CloudPanelInput]]
    ) -> dict[str, ChapterJobRecord]:
        ordered_ids = sorted(jobs)
        if self.max_concurrent == 1 or len(ordered_ids) < 2:
            return {job_id: self.run_job(job_id, jobs[job_id]) for job_id in ordered_ids}
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            records = executor.map(lambda job_id: self.run_job(job_id, jobs[job_id]), ordered_ids)
            return dict(zip(ordered_ids, records, strict=True))

    @_bound
    def run_project(
        self,
        db: Any,
        project_id: str,
        *,
        actor_id: str = "",
        review_only_preview: bool = False,
        repair_for_production: bool = False,
        review_source_upscale_policy: str | None = None,
        review_source_root: Path | None = None,
        review_output_dir: Path | None = None,
        max_cloud_panels: int | None = None,
    ) -> ChapterJobRecord:
        """Run one DB-backed project and persist only after stage reconciliation."""

        try:
            repair_requested = review_only_preview or repair_for_production
            record = self.store.load(project_id) or ChapterJobRecord(project_id)
            cached_segmentation = record.stage_results.get("segmentation")
            prepared: tuple[tuple[CloudPanelInput, ...], dict[str, Any]] | None = None
            visual_stream: _StreamingVisualEvidenceSession | None = None
            manifest_loaded = False
            preparation_started = time.monotonic()
            manifest_raw = record.stage_results.get("prepared_panel_manifest")
            try:
                if not isinstance(manifest_raw, Mapping):
                    visual_stage = record.stage_results.get("visual")
                    if not isinstance(visual_stage, Mapping) or not isinstance(
                        cached_segmentation, Mapping
                    ):
                        raise prepared_panel_manifest.PreparedPanelManifestError(
                            "prepared manifest seed is unavailable"
                        )
                    manifest_raw = _build_cached_prepared_manifest(
                        db,
                        project_id,
                        visual_stage,
                        cached_segmentation,
                    )
                try:
                    prepared = _restore_project_prepared_manifest(
                        db,
                        project_id,
                        manifest_raw,
                        segmentation_state=(
                            cached_segmentation
                            if isinstance(cached_segmentation, Mapping)
                            else None
                        ),
                    )
                except TypeError as exc:
                    # Keep small test/integration adapters written for the
                    # pre-v3 two-argument helper source-compatible.
                    if "segmentation_state" not in str(exc):
                        raise
                    prepared = _restore_project_prepared_manifest(
                        db,
                        project_id,
                        manifest_raw,
                    )
                record.stage_results["prepared_panel_manifest"] = manifest_raw
                self.store.save(record)
                manifest_loaded = True
            except prepared_panel_manifest.PreparedPanelManifestError:
                prepared = None
            if prepared is None:
                stream_enabled = (
                    not manifest_loaded
                    and max_cloud_panels is None
                    and callable(getattr(self.runner, "start_visual_evidence_stream", None))
                )
                if stream_enabled:
                    visual_stream = self.runner.start_visual_evidence_stream()
                try:
                    prepared = prepare_project_panels(
                        db,
                        project_id,
                        boundary_assessor=self.runner.assess_strip_boundaries,
                        review_root=self.review_root,
                        return_segmentation=True,
                        review_only_auto_override=review_only_preview,
                        cached_segmentation=(
                            cached_segmentation
                            if isinstance(cached_segmentation, Mapping)
                            else None
                        ),
                        panel_sink=(visual_stream.submit if visual_stream is not None else None),
                        segmentation_checkpoint_identity=_segmentation_checkpoint_identity(
                            self.runner
                        ),
                    )
                except Exception:
                    if visual_stream is not None:
                        visual_stream.abort()
                    raise
            panels, segmentation_state = prepared
            streamed_visual: VisualStageResult | None = None
            if visual_stream is not None:
                try:
                    streamed_visual = visual_stream.finish(panels)
                except CloudStageError:
                    metrics = dict(self.runner.last_visual_stream_metrics)
                    if metrics:
                        record.stage_results["visual_stream_metrics"] = metrics
                        self.store.save(record)
                    raise
                record.stage_results["visual"] = streamed_visual.as_dict()
                record.stage_results["visual_stream_metrics"] = dict(
                    self.runner.last_visual_stream_metrics
                )
            restored_visual_subset: VisualStageResult | None = None
            if manifest_loaded and _visual_cache_requires_subset_restore(
                self.runner,
                record.stage_results.get("visual"),
                panels,
                allow_admitted_subset=review_only_preview,
            ):
                expected_source_hash = str(
                    manifest_raw.get("source_identity_hash", "")
                    if isinstance(manifest_raw, Mapping)
                    else ""
                )
                if expected_source_hash:
                    restored_visual_subset = _find_cached_visual_subset(
                        self.runner,
                        panels,
                        expected_source_hash=expected_source_hash,
                    )
                    if restored_visual_subset is not None:
                        record.stage_results["visual"] = restored_visual_subset.as_dict()
                        self.store.save(record)
            panels = _panels_for_cached_visual_stage(
                panels,
                record.stage_results.get("visual"),
            )
            if max_cloud_panels is not None and len(panels) > max_cloud_panels:
                panels = _subsample_panels(panels, max_cloud_panels)
            if restored_visual_subset is not None and restored_visual_subset.panel_ids == tuple(
                panel.panel_id for panel in panels
            ):
                _seed_visual_subset_cache(
                    self.runner,
                    panels,
                    restored_visual_subset,
                )
            targeted_materialization_ids: tuple[str, ...] = ()
            if manifest_loaded:
                targeted_materialization_ids = _visual_panel_ids_requiring_materialization(
                    self.runner,
                    panels,
                )
                if targeted_materialization_ids:
                    try:
                        panels = _materialize_metadata_only_panels(
                            db,
                            project_id,
                            panels,
                            required_panel_ids=targeted_materialization_ids,
                        )
                    except CloudStageError as exc:
                        return self._record_failure(record, exc)
                if restored_visual_subset is not None and restored_visual_subset.panel_ids == tuple(
                    panel.panel_id for panel in panels
                ):
                    _seed_visual_subset_cache(
                        self.runner,
                        panels,
                        restored_visual_subset,
                    )
            record.stage_results["segmentation"] = segmentation_state
            record.stage_results["preparation_metrics"] = {
                "contract_version": "prepared-panel-preparation-v1",
                "mode": (
                    "manifest_targeted_materialization"
                    if targeted_materialization_ids
                    else "manifest_metadata_only"
                    if manifest_loaded
                    else "cold_materialization"
                ),
                "panel_count": len(panels),
                "payload_bytes": sum(len(panel.payload) for panel in panels),
                "targeted_materialized_panel_count": len(targeted_materialization_ids),
                "elapsed_s": round(time.monotonic() - preparation_started, 3),
                "peak_rss_kb": _peak_rss_kb(),
                "source_decode_required": bool(not manifest_loaded or targeted_materialization_ids),
            }
            if not manifest_loaded:
                record.stage_results["prepared_panel_manifest"] = _build_project_prepared_manifest(
                    db,
                    project_id,
                    panels,
                    segmentation_state,
                )
            self.store.save(record)
            review_resume_result: ChapterResult | None = None
            if repair_requested and streamed_visual is None:
                try:
                    durable_visual = VisualStageResult.from_dict(
                        record.stage_results["visual"]
                    )
                    durable_story = StoryMapResult.from_dict(
                        record.stage_results["story_map"]
                    )
                    durable_narration = NarrationResult.from_dict(
                        record.stage_results["narration"]
                    )
                except (KeyError, TypeError, ValueError):
                    durable_visual = None
                    durable_story = None
                    durable_narration = None
                current_panel_ids = tuple(panel.panel_id for panel in panels)
                durable_visual_story_current = _review_resume_visual_story_is_current(
                    self.runner,
                    durable_visual,
                    durable_story,
                )
                if (
                    durable_visual_story_current
                    and durable_narration is not None
                    and durable_visual is not None
                    and durable_story is not None
                    and durable_visual.panel_ids == current_panel_ids
                    and durable_story.panel_ids == current_panel_ids
                    and durable_narration.visual_evidence_hash
                    == durable_visual.visual_evidence_hash
                ):
                    # Review-only resume owns its own current-prompt repair gate.
                    # Do not invalidate a grounded durable narration merely because
                    # the normal narration prompt advanced: doing so would spend
                    # fresh narration requests before visual repair can constrain
                    # the story to the feasible review ledger.
                    review_resume_result = ChapterResult(
                        state=ChapterState.READY_TO_RENDER,
                        visual=durable_visual,
                        story_map=durable_story,
                        narration=durable_narration,
                    )
                    record.state = ChapterState.READY_TO_RENDER
                    record.error_code = ""
                    record.error_message = ""
                    self.store.save(record)
            if streamed_visual is None and review_resume_result is None:
                record = self.run_job(project_id, panels)
            elif streamed_visual is not None:
                record = self.run_job(
                    project_id,
                    panels,
                    precomputed_visual=streamed_visual,
                )
            if (
                manifest_loaded
                and record.error_code == "cloud.prepared_manifest_requires_materialization"
            ):
                # A metadata-only manifest is usable only when its exact
                # content-addressed visual cache reconciles.  If the cache
                # is stale, materialize the current prepared panels and
                # rerun the affected visual/story stages; never mix old
                # evidence merely because panel IDs happen to match.
                fallback_started = time.monotonic()
                try:
                    prepared = prepare_project_panels(
                        db,
                        project_id,
                        boundary_assessor=self.runner.assess_strip_boundaries,
                        review_root=self.review_root,
                        return_segmentation=True,
                        review_only_auto_override=review_only_preview,
                        cached_segmentation=(
                            cached_segmentation
                            if isinstance(cached_segmentation, Mapping)
                            else None
                        ),
                        segmentation_checkpoint_identity=_segmentation_checkpoint_identity(
                            self.runner
                        ),
                    )
                    panels, segmentation_state = prepared
                    panels = _panels_for_cached_visual_stage(
                        panels,
                        record.stage_results.get("visual"),
                    )
                    manifest_loaded = False
                    record.stage_results["segmentation"] = segmentation_state
                    record.stage_results["preparation_metrics"] = {
                        "contract_version": "prepared-panel-preparation-v1",
                        "mode": "cold_materialization_after_metadata_cache_miss",
                        "panel_count": len(panels),
                        "payload_bytes": sum(len(panel.payload) for panel in panels),
                        "elapsed_s": round(time.monotonic() - fallback_started, 3),
                        "peak_rss_kb": _peak_rss_kb(),
                        "source_decode_required": True,
                    }
                    record.stage_results["prepared_panel_manifest"] = (
                        _build_project_prepared_manifest(
                            db,
                            project_id,
                            panels,
                            segmentation_state,
                        )
                    )
                    self.store.save(record)
                    record = self.run_job(project_id, panels)
                except CloudStageError as exc:
                    return self._record_failure(record, exc)
            visual_stage = record.stage_results.get("visual")
            if isinstance(visual_stage, Mapping):
                visual_panel_ids = {
                    str(item.get("panel_id"))
                    for item in visual_stage.get("panels", ())
                    if isinstance(item, Mapping) and str(item.get("panel_id", "")).strip()
                }
                if visual_panel_ids:
                    panels = tuple(panel for panel in panels if panel.panel_id in visual_panel_ids)
            record.stage_results["segmentation"] = segmentation_state
            self.store.save(record)
            repaired_result: ChapterResult | None = None
            repair_ledger: visual_narrative_repair.FeasibleVisualLedger | None = None
            repair_missing_sections: tuple[str, ...] = ()
            if record.state != ChapterState.READY_TO_RENDER:
                can_repair_initial_narration = (
                    repair_requested
                    and record.error_code
                    in {
                        "cloud.narrative_not_grounded",
                        "cloud.narrative_duration_out_of_range",
                        "cloud.narrative_repair_micro_compaction_unavailable",
                        "visual.narrative_repair_ungrounded",
                        "subtitle.overflow",
                    }
                    and isinstance(record.stage_results.get("visual"), Mapping)
                    and isinstance(record.stage_results.get("story_map"), Mapping)
                )
                if not can_repair_initial_narration:
                    return record
                initial_repair_result = None
                partial_narration = getattr(self.runner, "_last_narration_result", None)
                if partial_narration is None:
                    persisted_narration = record.stage_results.get("narration")
                    if isinstance(persisted_narration, Mapping):
                        try:
                            partial_narration = NarrationResult.from_dict(persisted_narration)
                        except (KeyError, TypeError, ValueError):
                            partial_narration = None
                initial_visual = VisualStageResult.from_dict(record.stage_results["visual"])
                initial_story_map = StoryMapResult.from_dict(record.stage_results["story_map"])
                if (
                    partial_narration is not None
                    and getattr(partial_narration, "visual_evidence_hash", "")
                    == initial_visual.visual_evidence_hash
                ):
                    initial_repair_result = ChapterResult(
                        state=ChapterState.READY_TO_RENDER,
                        visual=initial_visual,
                        story_map=initial_story_map,
                        narration=partial_narration,
                    )
                try:
                    repaired_result, repair_ledger, repair_missing_sections = (
                        self._repair_review_narrative(
                            db,
                            project_id,
                            None,
                            panels,
                            initial_repair_result,
                            visual=initial_visual,
                            story_map=initial_story_map,
                            review_source_upscale_policy=review_source_upscale_policy,
                            review_source_root=Path(
                                review_source_root or self.review_root or Path("final_test")
                            ),
                        )
                    )
                    record = self._merge_latest_durable_progress(record)
                except CloudStageError as exc:
                    print("REPAIR_FAILED:", exc.code, file=sys.stderr, flush=True)
                    return self._record_failure(record, exc)
                record.stage_results["narration"] = repaired_result.narration.as_dict()
                record.state = ChapterState.READY_TO_RENDER
                record.error_code = ""
                record.error_message = ""
                self.store.save(record)
            if repaired_result is None:
                result = ChapterResult(
                    state=ChapterState.READY_TO_RENDER,
                    visual=VisualStageResult.from_dict(record.stage_results["visual"]),
                    story_map=StoryMapResult.from_dict(record.stage_results["story_map"]),
                    narration=NarrationResult.from_dict(record.stage_results["narration"]),
                )
            else:
                result = repaired_result
            # Review resume must validate/repair the durable narration against the
            # current feasible visual ledger before persistence. Otherwise an old
            # READY_TO_RENDER narration can fail the newer analyzer contract before
            # the review repair path gets a chance to replace it.
            if repair_requested and repaired_result is None:
                from app.services import pipeline, review_source_upscale

                policy_id = (
                    review_source_upscale_policy
                    or review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID
                )
                preflight_script_row = (
                    pipeline.latest_script_row(db, project_id)
                    if hasattr(db, "scalars")
                    else None
                )
                try:
                    repaired_result, repair_ledger, repair_missing_sections = (
                        self._repair_review_narrative(
                            db,
                            project_id,
                            preflight_script_row,
                            panels,
                            result,
                            review_source_upscale_policy=policy_id,
                            review_source_root=Path(
                                review_source_root or self.review_root or Path("final_test")
                            ),
                        )
                    )
                    record = self._merge_latest_durable_progress(record)
                except CloudStageError as exc:
                    return self._record_failure(record, exc)
                result = repaired_result
                record.stage_results["narration"] = repaired_result.narration.as_dict()
                record.state = ChapterState.READY_TO_RENDER
                record.error_code = ""
                record.error_message = ""
                self.store.save(record)
            analysis, script_row = persist_cloud_chapter(
                db,
                project_id,
                panels,
                result,
                model_identity=self.runner.model_identity,
                actor_id=actor_id,
            )
            # Make reconciled analysis/script durable before the optional
            # review render. A later render failure must not roll back the
            # expensive cloud stages or prevent a safe resume.
            if hasattr(db, "commit"):
                db.commit()
            record.stage_results["persistence"] = {
                "analysis_id": analysis.id,
                "script_id": script_row.id,
                "script_version": script_row.version,
                "approval_required": True,
                "voice_timing_required": True,
            }
            if repair_requested:
                from app.services import review_source_upscale

                try:
                    policy_id = (
                        review_source_upscale_policy
                        or review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID
                    )
                    if repaired_result is None:
                        repaired_result, repair_ledger, repair_missing_sections = (
                            self._repair_review_narrative(
                                db,
                                project_id,
                                script_row,
                                panels,
                                result,
                                review_source_upscale_policy=policy_id,
                                review_source_root=Path(
                                    review_source_root or self.review_root or Path("final_test")
                                ),
                            )
                        )
                        record = self._merge_latest_durable_progress(record)
                    ledger = repair_ledger
                    missing_sections = repair_missing_sections
                    if ledger is None:
                        raise CloudStageError(
                            "visual.narrative_repair_stale_ledger", reviewable=True
                        )
                    record.stage_results["feasible_visual_ledger"] = ledger.as_dict()
                    if isinstance(ledger, visual_narrative_repair.FeasibleVisualLedger):
                        record.stage_results["feasible_render_plan"] = (
                            visual_narrative_repair.FeasibleRenderPlan.from_ledger(ledger).as_dict()
                        )
                    record.stage_results["visual_repair"] = {
                        "contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                        "missing_sections": list(missing_sections),
                        "attempted": bool(missing_sections),
                        "model_identity_hash": self.runner.model_identity.identity_hash,
                        "prompt_version": self.runner.prompts["visual_narrative_repair"][0],
                        "prompt_sha256": self.runner.prompts["visual_narrative_repair"][1],
                        "publish_allowed": False,
                    }
                    if repaired_result.narration != result.narration:
                        analysis, script_row = persist_cloud_chapter(
                            db,
                            project_id,
                            panels,
                            repaired_result,
                            model_identity=self.runner.model_identity,
                            actor_id=actor_id,
                        )
                        record.stage_results["narration"] = repaired_result.narration.as_dict()
                        record.stage_results["persistence"] = {
                            "analysis_id": analysis.id,
                            "script_id": script_row.id,
                            "script_version": script_row.version,
                            "approval_required": True,
                            "voice_timing_required": True,
                            "visual_repair": True,
                        }
                        # The repaired script is a durable cloud-stage result, not
                        # part of the optional review-render transaction. Commit it
                        # before timeline/render so a later local preview failure
                        # cannot roll it back.
                        if hasattr(db, "commit"):
                            db.commit()
                    # Persist the repaired ledger/scope before any optional local
                    # render work. The outer failure handler reloads this checkpoint.
                    record.state = ChapterState.READY_TO_RENDER
                    record.error_code = ""
                    record.error_message = ""
                    record.stage_results["publish_allowed"] = False
                    self.store.save(record)
                except CloudStageError as exc:
                    return self._record_failure(record, exc)
                if repair_for_production and not review_only_preview:
                    record.stage_results["voice_state"] = "WAITING_FOR_PRODUCTION"
                    record.state = ChapterState.READY_TO_RENDER
                    self.store.save(record)
                    return record
                try:
                    from app.services import pipeline

                    # Ledger entries are beat-keyed; the planner matches the
                    # script section names. Prefer explicit feasible panel IDs
                    # persisted by visual repair; fall back to beat eligibility
                    # for sections that did not need a cross-beat remap.
                    section_names = tuple(
                        str(section.get("section", ""))
                        for section in (getattr(script_row, "sections", ()) or ())
                        if isinstance(section, Mapping) and str(section.get("section", "")).strip()
                    )
                    story_map_row = StoryMapResult.from_dict(record.stage_results["story_map"])
                    section_to_beats = (
                        visual_narrative_repair.default_section_to_beats(
                            section_names,
                            story_map_row.beats,
                        )
                        if section_names
                        else {
                            str(section): (str(section),)
                            for section in story_map_row.panel_ids
                            if section
                        }
                    )
                    section_panel_ids = _review_section_panel_ids(
                        script_row,
                        ledger,
                        section_to_beats,
                    )
                    # Citations stay as persisted source orders; the planner
                    # resolves them to regions and re-runs every framing gate.
                    section_citations = {
                        str(section.get("section", "")): tuple(
                            int(value)
                            for value in section.get("citations", ())
                            if isinstance(value, int)
                        )
                        for section in script_row.sections
                        if isinstance(section, Mapping) and str(section.get("section", "")).strip()
                    }
                    section_panel_ids = {
                        str(section): tuple(
                            panel_id for panel_id in panel_ids if str(panel_id).strip()
                        )
                        for section, panel_ids in section_panel_ids.items()
                        if panel_ids
                    }
                    narration_qc = (
                        dict(getattr(result.narration, "qc_report", {}) or {})
                        if result is not None and result.narration is not None
                        else {}
                    )
                    duration_policy = narration_qc.get("duration_policy_contract")
                    if not isinstance(duration_policy, Mapping):
                        for container_key in (
                            "visual_repair_text_only_duration_repair_v1",
                            "narration_repair",
                        ):
                            container = narration_qc.get(container_key)
                            if isinstance(container, Mapping) and isinstance(
                                container.get("duration_policy_contract"), Mapping
                            ):
                                duration_policy = container["duration_policy_contract"]
                                break
                    provisional_duration_bounds_s = None
                    if isinstance(duration_policy, Mapping):
                        try:
                            provisional_duration_bounds_s = (
                                float(duration_policy["target_duration_min_s"]),
                                float(duration_policy["target_duration_max_s"]),
                            )
                        except (KeyError, TypeError, ValueError):
                            provisional_duration_bounds_s = None
                    pipeline.build_timeline(
                        db,
                        project_id,
                        actor_id=actor_id,
                        silent_reference_review=True,
                        review_source_upscale_policy=policy_id,
                        provisional_duration_s=float(script_row.estimated_duration),
                        provisional_duration_bounds_s=provisional_duration_bounds_s,
                        reference_section_panel_ids=section_panel_ids,
                        reference_section_citations=section_citations,
                        reference_beats_by_section={},
                        review_source_root=Path(
                            review_source_root or self.review_root or Path("final_test")
                        ),
                        allow_conservative_full_panel=True,
                    )
                    _render_job, artifacts = pipeline.render_silent_review_preview(
                        db,
                        project_id,
                        actor_id=actor_id,
                        review_source_upscale_policy=policy_id,
                        review_source_root=Path(
                            review_source_root or self.review_root or Path("final_test")
                        ),
                        output_dir=review_output_dir,
                    )
                except Exception as exc:  # noqa: BLE001 - convert to a durable review state
                    code = _review_failure_code(str(getattr(exc, "code", "") or exc))
                    raise CloudStageError(
                        code, "review preview was not produced", reviewable=True
                    ) from None
                record.stage_results["review_preview"] = artifacts.as_dict()
                record.stage_results["voice_state"] = "VISUAL_ONLY_WAITING_FOR_VOICE"
                record.stage_results["publish_allowed"] = False
                record.state = ChapterState.REVIEW_PREVIEW_READY
            else:
                record.state = ChapterState.READY_TO_RENDER
            record.error_code = ""
            record.error_message = ""
            self.store.save(record)
            return record
        except CloudStageError as exc:
            if hasattr(db, "rollback"):
                db.rollback()
            record = self.store.load(project_id) or ChapterJobRecord(project_id)
            record.model_identity_hash = self.runner.model_identity.identity_hash
            record.state = ChapterState.NEEDS_REVIEW if exc.reviewable else ChapterState.FAILED
            record.error_code = exc.code
            record.error_message = str(exc)
            if exc.reviewable:
                review_entry = {"code": exc.code, "reason": str(exc)}
                safe_metadata = dict(exc.safe_metadata)
                metrics_for_failure = getattr(
                    self.runner, "_response_shape_metrics_for_failure", None
                )
                if (
                    not safe_metadata
                    and callable(metrics_for_failure)
                    and (
                        exc.code.startswith("cloud.narrative_")
                        or exc.code == "cloud.request_budget_exceeded"
                    )
                ):
                    safe_metadata = metrics_for_failure(exc.code)
                if "array_key" in safe_metadata:
                    safe_metadata.setdefault("failed_code", exc.code)
                    safe_metadata.setdefault("failed_predicate", exc.code)
                if safe_metadata:
                    review_entry["safe_metadata"] = safe_metadata
                record.review_queue.append(review_entry)
            self.store.save(record)
            return record
