"""Visual methods extracted from cloud_multimodal."""

# ruff: noqa: F821 -- runtime globals are refreshed from the compatibility facade.
from __future__ import annotations

from .runtime import runtime_bound

_RUNTIME_NAMES = (
    'CloudStageError',
    'CloudStageRunner',
    'Mapping',
    'MemoryStageCache',
    'VISUAL_CACHE_IDENTITY_VERSION',
    'VISUAL_WINDOW_GEOMETRY_VERSION',
    'VISUAL_WINDOW_GEOMETRY_WORKERS',
    'VisionObservationRequest',
    'VisualStageResult',
    '_cache_key',
    '_hash',
    '_reconcile_window_geometry',
    '_visual_analysis_windows',
    '_visual_cached_row_is_reusable',
    '_visual_chunk_cache_key',
    '_visual_observation_failure_predicate',
    '_visual_panel_chunks',
    '_visual_panel_identities',
    '_visual_panel_identity_hashes',
    '_visual_request_panel',
    'analyzer_contract',
    'replace',
    'sys',
    'threading',
    'visual_scoring',
)
_bound = runtime_bound(_RUNTIME_NAMES)


class VisualEvidenceMixin:
    @_bound
    def run_visual_evidence(self, panels: Sequence[CloudPanelInput]) -> VisualStageResult:
        ordered = self._ordered_panels(panels)
        prompt = self.prompts["visual"]
        repair_prompt = visual_scoring.load_visual_evidence_repair_instruction()
        source = list(_visual_panel_identities(ordered))
        key = _cache_key("visual", source, self.model_identity, prompt)
        cached_reusable: dict[str, dict[str, Any]] = {}
        cached = self.cache.get(key) if self.cache is not None else None
        if isinstance(cached, Mapping):
            try:
                cached_result = VisualStageResult.from_dict(cached)
            except (KeyError, TypeError, ValueError):
                cached_result = None
            if cached_result is not None:
                panels_by_id = {panel.panel_id: panel for panel in ordered}
                cached_reusable = {
                    str(row["panel_id"]): dict(row)
                    for row in cached_result.panels
                    if isinstance(row, Mapping)
                    and _visual_cached_row_is_reusable(
                        row,
                        panels_by_id.get(str(row.get("panel_id", ""))),
                    )
                    if panels_by_id.get(str(row.get("panel_id", ""))) is not None
                }
                if len(cached_reusable) == len(ordered) and tuple(
                    cached_reusable[panel.panel_id]["panel_id"] for panel in ordered
                ) == tuple(panel.panel_id for panel in ordered):
                    return cached_result
        if (
            cached is None
            and (migrated := self._migrate_legacy_visual_cache(ordered, key=key, prompt=prompt))
            is not None
        ):
            return VisualStageResult.from_dict(migrated)
        if any(
            getattr(panel, "metadata_only", False) and panel.panel_id not in cached_reusable
            for panel in ordered
        ):
            raise CloudStageError("cloud.prepared_manifest_requires_materialization")
        instruction_version, instruction_sha256, _ = analyzer_contract.load_analyzer_instruction()
        from concurrent.futures import ThreadPoolExecutor

        chunks = list(_visual_panel_chunks(ordered))
        reconciled_by_id: dict[str, dict[str, Any]] = dict(cached_reusable)
        skipped_codes: list[str] = []
        failure_predicates: dict[str, int] = {}
        unknown_failure_metadata: dict[str, Any] | None = None
        reconcile_lock = threading.Lock()
        VISUAL_PARALLEL_WORKERS = self.visual_parallel_workers
        checkpoint_scope = self._checkpoint_scope(source, prompt)
        _checkpoint_seed = self._checkpoint_load(checkpoint_scope)
        panel_identity_by_id = {
            panel.panel_id: identity_hash
            for panel, identity_hash in zip(
                ordered,
                _visual_panel_identity_hashes(ordered),
                strict=True,
            )
        }
        analysis_window_cache: dict[str, tuple[dict[str, Any], ...]] = {}

        def panel_analysis_windows(item: CloudPanelInput) -> tuple[dict[str, Any], ...]:
            cached_windows = analysis_window_cache.get(item.panel_id)
            if cached_windows is None:
                cached_windows = _visual_analysis_windows(item)
                analysis_window_cache[item.panel_id] = cached_windows
            return cached_windows

        def visual_row_entry(
            item: CloudPanelInput,
            raw: Mapping[str, Any],
        ) -> tuple[dict[str, Any] | None, tuple[dict[str, Any], dict[str, Any]] | None]:
            """Reconcile one row and return unknown geometry separately.

            A row with valid semantic facts is retained even when its geometry
            is unknown.  The caller retries that panel once within the normal
            bounded attempt budget, then converts only that row to the typed
            conservative whole-panel provenance.  Other schema/lineage errors
            remain retryable panel failures and never contaminate valid rows.
            """

            if raw.get("panel_id") != item.panel_id:
                raise CloudStageError(
                    message=(
                        f"cloud.panel_lineage_invalid: want={item.panel_id} "
                        f"got={raw.get('panel_id')}"
                    ),
                    reviewable=True,
                )
            raw_visual = raw.get("visual_evidence")
            if raw_visual is not None and not isinstance(raw_visual, Mapping):
                raise CloudStageError("visual.evidence_invalid", reviewable=True)
            if isinstance(raw_visual, Mapping) and raw_visual.get("evidence_hash"):
                raise CloudStageError("cloud.provider_hash_forbidden")
            visual = dict(raw_visual) if isinstance(raw_visual, Mapping) else None
            if visual is not None:
                visual.setdefault(
                    "contract_version",
                    visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
                )
                visual.pop("evidence_hash", None)
            merged, evidence = visual_scoring.ensure_panel_visual_evidence(
                {**dict(raw), "visual_evidence": visual},
                panel_id=item.panel_id,
                source_asset_id=item.source_asset_id,
                source_order=item.source_order,
            )
            source_values = {
                evidence.evidence_source,
                *(region.evidence_source for region in evidence.balloon_regions),
            }
            if any("ocr" in value.lower() for value in source_values):
                raise CloudStageError("visual.balloon_geometry_invalid", reviewable=True)
            evidence_json = visual_scoring.panel_visual_evidence_json(evidence)
            merged["visual_evidence"] = evidence_json
            failed_predicate = _visual_observation_failure_predicate(merged)
            if failed_predicate is not None:
                raise CloudStageError(
                    "cloud.visual_evidence_invalid",
                    safe_metadata={"failed_predicate": failed_predicate},
                )
            entry = {
                "panel_id": item.panel_id,
                "source_asset_id": item.source_asset_id,
                "source_order": item.source_order,
                "source_checksum": item.source_checksum,
                "observation": merged,
                "visual_evidence": evidence_json,
                "evidence_hash": evidence_json["evidence_hash"],
            }
            if evidence.balloon_mask_status == "unknown":
                return None, (merged, evidence_json)
            return entry, None

        def repair_tall_window_geometry(
            item: CloudPanelInput,
            merged_observation: Mapping[str, Any],
            *,
            chunk_index: int,
        ) -> dict[str, Any] | None:
            windows = panel_analysis_windows(item)
            if not windows:
                return None

            def analyze_window(
                window: Mapping[str, Any],
            ) -> tuple[int, Mapping[str, Any] | None, int, dict[str, int], float]:
                window_index = int(window["window_index"])
                transient_panel_id = f"{item.panel_id}::window::{window_index}"
                worker_runner = CloudStageRunner(
                    provider=self.provider,
                    model_identity=self.model_identity,
                    cache=MemoryStageCache(),
                    max_attempts=1,
                    min_request_interval_s=self.min_request_interval_s,
                    estimated_cost_per_request=self.estimated_cost_per_request,
                    allow_balloon_unknown=self.allow_balloon_unknown,
                    visual_parallel_workers=self.visual_parallel_workers,
                    provider_concurrency_gate=self._provider_concurrency_gate,
                )
                visual: Mapping[str, Any] | None = None
                attempts = max(1, self.max_attempts)
                for repair_attempt in range(attempts):
                    request = VisionObservationRequest(
                        analysis_run_id=(
                            f"cloud-{_hash(source)[:20]}-window-{chunk_index}-"
                            f"{item.source_order}-{window_index}-attempt-{repair_attempt}"
                        ),
                        instruction_version=instruction_version,
                        instruction_sha256=instruction_sha256,
                        chunk_index=window_index,
                        panels=(
                            {
                                "panel_id": transient_panel_id,
                                "source_asset_id": item.source_asset_id,
                                "source_order": item.source_order,
                                "mime_type": str(window["mime_type"]),
                                "payload": window["payload"],
                            },
                        ),
                        visual_instruction_version=repair_prompt[0],
                        visual_instruction_sha256=repair_prompt[1],
                    )
                    try:
                        rows = worker_runner._call(
                            lambda request=request: self.provider.observe(request),
                            request_stage="other",
                        )
                    except CloudStageError as exc:
                        if (
                            exc.code == "cloud.provider_response_invalid"
                            and repair_attempt + 1 < attempts
                        ):
                            continue
                        break
                    if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], Mapping):
                        candidate = rows[0].get("visual_evidence")
                        if isinstance(candidate, Mapping):
                            visual = dict(candidate)
                            break
                return (
                    window_index,
                    visual,
                    worker_runner.request_count,
                    dict(worker_runner.request_counts),
                    worker_runner.estimated_cost_usd,
                )

            worker_count = min(
                len(windows),
                VISUAL_WINDOW_GEOMETRY_WORKERS,
                max(1, self.visual_parallel_workers),
            )
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                results = list(executor.map(analyze_window, windows))
            evidence_by_window: dict[int, Mapping[str, Any]] = {}
            request_count_delta = 0
            request_counts_delta = dict.fromkeys(self.request_counts, 0)
            estimated_cost_delta = 0.0
            for window_index, visual, request_count, request_counts, cost in results:
                request_count_delta += int(request_count)
                estimated_cost_delta += float(cost)
                for key, value in request_counts.items():
                    if key in request_counts_delta:
                        request_counts_delta[key] += int(value)
                if isinstance(visual, Mapping):
                    evidence_by_window[window_index] = visual
            with reconcile_lock:
                self.request_count += request_count_delta
                self.estimated_cost_usd += estimated_cost_delta
                for key, value in request_counts_delta.items():
                    self.request_counts[key] += value
            canonical = _reconcile_window_geometry(item, windows, evidence_by_window)
            if canonical is None:
                return None
            repaired_observation = dict(merged_observation)
            repaired_observation["visual_evidence"] = canonical
            try:
                repaired_merged, repaired_evidence = visual_scoring.ensure_panel_visual_evidence(
                    repaired_observation,
                    panel_id=item.panel_id,
                    source_asset_id=item.source_asset_id,
                    source_order=item.source_order,
                )
            except visual_scoring.VisualEvidenceError:
                return None
            evidence_json = visual_scoring.panel_visual_evidence_json(repaired_evidence)
            repaired_merged["visual_evidence"] = evidence_json
            if _visual_observation_failure_predicate(repaired_merged) is not None:
                return None
            return {
                "panel_id": item.panel_id,
                "source_asset_id": item.source_asset_id,
                "source_order": item.source_order,
                "source_checksum": item.source_checksum,
                "observation": repaired_merged,
                "visual_evidence": evidence_json,
                "evidence_hash": evidence_json["evidence_hash"],
                "geometry_mode": VISUAL_WINDOW_GEOMETRY_VERSION,
                "window_geometry_request_count": request_count_delta,
            }

        def observe_chunk(chunk_index: int, chunk: Sequence[CloudPanelInput]) -> None:
            # Every accepted row is checkpointed before only its own failed
            # rows are retried.  Whole-request transport failures retain the
            # existing bounded binary reduction path.
            nonlocal reconciled_by_id, unknown_failure_metadata
            chunk_cache_key = _visual_chunk_cache_key(
                chunk,
                chunk_index=chunk_index,
                batch_count=len(chunks),
                model_identity=self.model_identity,
                prompt=prompt,
            )
            checkpoint_seeded = {
                item.panel_id
                for item in chunk
                if (
                    item.panel_id in _checkpoint_seed
                    and _checkpoint_seed[item.panel_id].get("cache_identity_hash")
                    == panel_identity_by_id[item.panel_id]
                    and _checkpoint_seed[item.panel_id].get("chunk_cache_key") == chunk_cache_key
                    and _visual_cached_row_is_reusable(_checkpoint_seed[item.panel_id], item)
                )
            }
            with reconcile_lock:
                seeded = {item.panel_id for item in chunk if item.panel_id in reconciled_by_id}
                seeded.update(checkpoint_seeded)
                for panel_id in checkpoint_seeded:
                    reconciled_by_id[panel_id] = _checkpoint_seed[panel_id]
            live = [item for item in chunk if item.panel_id not in seeded]
            if not live:
                print(
                    f"VISUAL_CHUNK_OK chunk={chunk_index} panels=0(from checkpoint)",
                    file=sys.stderr,
                    flush=True,
                )
                return
            chunk = tuple(live)
            retryable_visual_codes = {
                "cloud.provider_response_invalid",
                "cloud.panel_lineage_invalid",
                "cloud.provider_hash_forbidden",
                "cloud.visual_evidence_invalid",
                "visual.evidence_invalid",
                "visual.region_invalid",
                "visual.lineage_invalid",
                "visual.balloon_mask_unknown",
                "visual.balloon_geometry_invalid",
            }
            for attempt in range(self.max_attempts):
                current_chunk = tuple(chunk)
                request_panels = [_visual_request_panel(item) for item in current_chunk]
                request = VisionObservationRequest(
                    analysis_run_id=f"cloud-{_hash(source)[:24]}",
                    instruction_version=instruction_version,
                    instruction_sha256=instruction_sha256,
                    chunk_index=chunk_index,
                    panels=tuple(request_panels),
                    visual_instruction_version=(prompt[0] if attempt == 0 else repair_prompt[0]),
                    visual_instruction_sha256=(prompt[1] if attempt == 0 else repair_prompt[1]),
                )
                try:
                    attempt_request = replace(
                        request,
                        analysis_run_id=f"{request.analysis_run_id}-attempt-{attempt}",
                    )
                    raw_rows = self._call(
                        lambda request=attempt_request: self.provider.observe(request),
                        request_stage="other",
                    )
                    rows_by_id: dict[str, Mapping[str, Any]] = {}
                    if isinstance(raw_rows, list):
                        for raw in raw_rows:
                            if isinstance(raw, Mapping):
                                panel_id = str(raw.get("panel_id", ""))
                                if panel_id and panel_id not in rows_by_id:
                                    rows_by_id[panel_id] = raw
                    chunk_reconciled: dict[str, dict[str, Any]] = {}
                    unknown_rows: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
                    failed_rows: dict[str, str] = {}
                    failed_predicates_by_id: dict[str, str] = {}
                    for index, item in enumerate(current_chunk):
                        raw = (
                            raw_rows[index]
                            if isinstance(raw_rows, list) and len(raw_rows) == len(current_chunk)
                            else rows_by_id.get(item.panel_id)
                        )
                        if not isinstance(raw, Mapping):
                            failed_rows[item.panel_id] = "cloud.provider_response_invalid"
                            failed_predicates_by_id[item.panel_id] = "provider_response_invalid"
                            failure_predicates["provider_response_invalid"] = (
                                failure_predicates.get("provider_response_invalid", 0) + 1
                            )
                            continue
                        if item.panel_id in reconciled_by_id:
                            continue
                        try:
                            entry, unknown = visual_row_entry(item, raw)
                        except visual_scoring.VisualEvidenceError as exc:
                            failed_rows[item.panel_id] = getattr(
                                exc, "code", "cloud.visual_evidence_invalid"
                            )
                            predicate = getattr(exc, "code", "visual_validator")
                            failed_predicates_by_id[item.panel_id] = predicate
                            failure_predicates[predicate] = failure_predicates.get(predicate, 0) + 1
                            continue
                        except CloudStageError as exc:
                            failed_rows[item.panel_id] = exc.code
                            predicate = str(exc.safe_metadata.get("failed_predicate", exc.code))
                            failed_predicates_by_id[item.panel_id] = predicate
                            failure_predicates[predicate] = failure_predicates.get(predicate, 0) + 1
                            continue
                        if unknown is not None:
                            unknown_rows[item.panel_id] = unknown
                        elif entry is not None:
                            chunk_reconciled[item.panel_id] = entry
                    with reconcile_lock:
                        reconciled_by_id.update(chunk_reconciled)
                    for _entry in chunk_reconciled.values():
                        panel_id = str(_entry["panel_id"])
                        _entry["cache_identity_hash"] = panel_identity_by_id[panel_id]
                        _entry["cache_identity_version"] = VISUAL_CACHE_IDENTITY_VERSION
                        _entry["chunk_cache_key"] = _visual_chunk_cache_key(
                            current_chunk,
                            chunk_index=chunk_index,
                            batch_count=len(chunks),
                            model_identity=self.model_identity,
                            prompt=prompt,
                        )
                        self._checkpoint_append(checkpoint_scope, _entry)
                    # Geometry-unknown rows and semantic-attention misses are
                    # already valid enough to target precisely. Rebatching them
                    # tends to repeat the same attention failure and wastes a
                    # provider round-trip. Keep batch retry only for other
                    # provider/schema failures; targeted repair below remains
                    # fail-closed and uses the same validators.
                    pending_ids = tuple(
                        panel_id
                        for panel_id in failed_rows
                        if failed_predicates_by_id.get(panel_id) != "visible_facts_nonempty"
                    )
                    if pending_ids and attempt + 1 < self.max_attempts:
                        chunk = tuple(
                            item for item in current_chunk if item.panel_id in pending_ids
                        )
                        continue
                    semantic_repair_ids = tuple(
                        item.panel_id
                        for item in current_chunk
                        if (
                            item.panel_id in failed_rows
                            and failed_predicates_by_id.get(item.panel_id)
                            == "visible_facts_nonempty"
                        )
                    )
                    if semantic_repair_ids:
                        # A batched response can omit semantic facts when the
                        # provider spreads attention across several panels.
                        # Retry only those rows as singleton semantic repairs;
                        # the normal row validator remains authoritative.
                        for item in current_chunk:
                            if item.panel_id not in semantic_repair_ids:
                                continue
                            try:
                                targeted_panel = _visual_request_panel(item)
                                targeted_request = VisionObservationRequest(
                                    analysis_run_id=(
                                        f"{request.analysis_run_id}-semantic-"
                                        f"{chunk_index}-{item.source_order}"
                                    ),
                                    instruction_version=instruction_version,
                                    instruction_sha256=instruction_sha256,
                                    chunk_index=chunk_index,
                                    panels=(targeted_panel,),
                                    visual_instruction_version=repair_prompt[0],
                                    visual_instruction_sha256=repair_prompt[1],
                                )
                                targeted_rows = self._call(
                                    lambda targeted_request=targeted_request: self.provider.observe(
                                        targeted_request
                                    ),
                                    request_stage="other",
                                )
                                target_raw = None
                                if isinstance(targeted_rows, list):
                                    for raw in targeted_rows:
                                        if (
                                            isinstance(raw, Mapping)
                                            and str(raw.get("panel_id", "")) == item.panel_id
                                        ):
                                            target_raw = raw
                                            break
                                if not isinstance(target_raw, Mapping):
                                    continue
                                target_entry, target_unknown = visual_row_entry(item, target_raw)
                                if target_unknown is not None:
                                    # Semantic repair succeeded but geometry is
                                    # still unknown. Preserve that progress and
                                    # let the strict geometry-repair path below
                                    # resolve it instead of discarding the row.
                                    unknown_rows[item.panel_id] = target_unknown
                                    failed_rows.pop(item.panel_id, None)
                                    failed_predicates_by_id.pop(item.panel_id, None)
                                    continue
                                if target_entry is None:
                                    continue
                                target_entry["cache_identity_hash"] = panel_identity_by_id[
                                    item.panel_id
                                ]
                                target_entry["cache_identity_version"] = (
                                    VISUAL_CACHE_IDENTITY_VERSION
                                )
                                target_entry["chunk_cache_key"] = _visual_chunk_cache_key(
                                    current_chunk,
                                    chunk_index=chunk_index,
                                    batch_count=len(chunks),
                                    model_identity=self.model_identity,
                                    prompt=prompt,
                                )
                                with reconcile_lock:
                                    reconciled_by_id[item.panel_id] = target_entry
                                self._checkpoint_append(checkpoint_scope, target_entry)
                                failed_rows.pop(item.panel_id, None)
                                failed_predicates_by_id.pop(item.panel_id, None)
                            except (CloudStageError, visual_scoring.VisualEvidenceError):
                                # Keep this row missing; semantic repair never
                                # converts an invalid response into evidence.
                                continue
                    if unknown_rows:
                        # Tall connected scenes retain their canonical panel ID,
                        # but geometry can be recovered from complete overlapping
                        # detail windows.  Repair only these unknown rows and keep
                        # the ordinary singleton path as a conservative fallback.
                        for item in current_chunk:
                            if item.panel_id not in unknown_rows or not panel_analysis_windows(
                                item
                            ):
                                continue
                            merged_observation, _unknown_json = unknown_rows[item.panel_id]
                            repaired_entry = repair_tall_window_geometry(
                                item,
                                merged_observation,
                                chunk_index=chunk_index,
                            )
                            if repaired_entry is None:
                                continue
                            repaired_entry["cache_identity_hash"] = panel_identity_by_id[
                                item.panel_id
                            ]
                            repaired_entry["cache_identity_version"] = VISUAL_CACHE_IDENTITY_VERSION
                            repaired_entry["chunk_cache_key"] = _visual_chunk_cache_key(
                                current_chunk,
                                chunk_index=chunk_index,
                                batch_count=len(chunks),
                                model_identity=self.model_identity,
                                prompt=prompt,
                            )
                            with reconcile_lock:
                                reconciled_by_id[item.panel_id] = repaired_entry
                            self._checkpoint_append(checkpoint_scope, repaired_entry)
                            unknown_rows.pop(item.panel_id, None)
                    if unknown_rows:
                        # A multi-panel response can be semantically valid but
                        # omit balloon geometry because the provider's visual
                        # attention is shared across the batch.  Before using
                        # the conservative whole-panel review fallback, retry
                        # each unknown row as its own bounded geometry request.
                        # This remains missing-only and keeps the strict local
                        # evidence/lineage validator authoritative.
                        for item in current_chunk:
                            if item.panel_id not in unknown_rows:
                                continue
                            try:
                                targeted_panel = _visual_request_panel(item)
                                targeted_request = VisionObservationRequest(
                                    analysis_run_id=(
                                        f"{request.analysis_run_id}-geometry-"
                                        f"{chunk_index}-{item.source_order}"
                                    ),
                                    instruction_version=instruction_version,
                                    instruction_sha256=instruction_sha256,
                                    chunk_index=chunk_index,
                                    panels=(targeted_panel,),
                                    visual_instruction_version=repair_prompt[0],
                                    visual_instruction_sha256=repair_prompt[1],
                                )
                                targeted_rows = self._call(
                                    lambda targeted_request=targeted_request: self.provider.observe(
                                        targeted_request
                                    ),
                                    request_stage="other",
                                )
                                target_raw = None
                                if isinstance(targeted_rows, list):
                                    for raw in targeted_rows:
                                        if (
                                            isinstance(raw, Mapping)
                                            and str(raw.get("panel_id", "")) == item.panel_id
                                        ):
                                            target_raw = raw
                                            break
                                if not isinstance(target_raw, Mapping):
                                    continue
                                target_entry, target_unknown = visual_row_entry(item, target_raw)
                                if target_entry is None or target_unknown is not None:
                                    continue
                                target_entry["cache_identity_hash"] = panel_identity_by_id[
                                    item.panel_id
                                ]
                                target_entry["cache_identity_version"] = (
                                    VISUAL_CACHE_IDENTITY_VERSION
                                )
                                target_entry["chunk_cache_key"] = _visual_chunk_cache_key(
                                    current_chunk,
                                    chunk_index=chunk_index,
                                    batch_count=len(chunks),
                                    model_identity=self.model_identity,
                                    prompt=prompt,
                                )
                                with reconcile_lock:
                                    reconciled_by_id[item.panel_id] = target_entry
                                self._checkpoint_append(checkpoint_scope, target_entry)
                                unknown_rows.pop(item.panel_id, None)
                            except (CloudStageError, visual_scoring.VisualEvidenceError):
                                # Keep the original unknown row for the
                                # explicitly audited conservative fallback.
                                continue
                    if unknown_rows:
                        for item in current_chunk:
                            if item.panel_id not in unknown_rows:
                                continue
                            merged, _unknown_json = unknown_rows[item.panel_id]
                            conservative = visual_scoring.conservative_full_panel_visual_evidence(
                                panel_id=item.panel_id,
                                source_asset_id=item.source_asset_id,
                                source_order=item.source_order,
                                reason="provider geometry remained unknown after bounded targeted retry",
                            )
                            evidence_json = visual_scoring.panel_visual_evidence_json(conservative)
                            merged = dict(merged)
                            merged["visual_evidence"] = evidence_json
                            entry = {
                                "panel_id": item.panel_id,
                                "source_asset_id": item.source_asset_id,
                                "source_order": item.source_order,
                                "source_checksum": item.source_checksum,
                                "observation": merged,
                                "visual_evidence": evidence_json,
                                "evidence_hash": evidence_json["evidence_hash"],
                                "fallback_mode": "conservative_full_panel_v1",
                                "targeted_geometry_repair_attempted": True,
                            }
                            entry["cache_identity_hash"] = panel_identity_by_id[item.panel_id]
                            entry["cache_identity_version"] = VISUAL_CACHE_IDENTITY_VERSION
                            entry["chunk_cache_key"] = _visual_chunk_cache_key(
                                current_chunk,
                                chunk_index=chunk_index,
                                batch_count=len(chunks),
                                model_identity=self.model_identity,
                                prompt=prompt,
                            )
                            with reconcile_lock:
                                reconciled_by_id[item.panel_id] = entry
                            self._checkpoint_append(checkpoint_scope, entry)
                            unknown_failure_metadata = unknown_failure_metadata or {
                                "stage": "visual",
                                "chunk_index": chunk_index,
                                "panel_count": 1,
                                "fallback_mode": "conservative_full_panel_v1",
                            }
                    if failed_rows:
                        with reconcile_lock:
                            skipped_codes.extend(code for code in failed_rows.values())
                    print(
                        f"VISUAL_CHUNK_OK chunk={chunk_index} panels={len(chunk_reconciled) + len(unknown_rows)} "
                        f"retries={max(0, attempt)}",
                        file=sys.stderr,
                        flush=True,
                    )
                    return
                except CloudStageError as exc:
                    if exc.code in retryable_visual_codes and attempt + 1 < self.max_attempts:
                        continue
                    if exc.code == "cloud.provider_request_failed":
                        raise
                    # binary reduction: subdivide the failing chunk and retry the
                    # halves so only genuinely poisonous panels are dropped.
                    if len(chunk) > 1:
                        half = len(chunk) // 2
                        for subchunk in (chunk[:half], chunk[half:]):
                            observe_chunk(chunk_index, subchunk)
                    else:
                        with reconcile_lock:
                            skipped_codes.append(exc.code)
                        print(
                            f"VISUAL_SKIP_PANEL panel={chunk[0].panel_id} code={exc.code}",
                            file=sys.stderr,
                            flush=True,
                        )
                    return
            for item in chunk:
                with reconcile_lock:
                    skipped_codes.append("cloud.provider_response_invalid")
                print(
                    f"VISUAL_SKIP_PANEL panel={item.panel_id} code=exhausted",
                    file=sys.stderr,
                    flush=True,
                )

        with ThreadPoolExecutor(max_workers=VISUAL_PARALLEL_WORKERS) as executor:
            futures = [
                executor.submit(observe_chunk, chunk_index, chunk)
                for chunk_index, chunk in enumerate(chunks)
            ]
            for future in futures:
                future.result()
        self.last_visual_failure_predicates = dict(failure_predicates)
        if set(reconciled_by_id) != {item.panel_id for item in ordered}:
            print(
                f"VISUAL_SKIP_TOTAL missing={len(ordered) - len(reconciled_by_id)} "
                f"of {len(ordered)}",
                file=sys.stderr,
                flush=True,
            )
        if not reconciled_by_id:
            code = skipped_codes[0] if skipped_codes else "cloud.panel_coverage_incomplete"
            if code == "visual.balloon_mask_unknown" and unknown_failure_metadata is not None:
                raise CloudStageError(
                    code,
                    reviewable=True,
                    safe_metadata=unknown_failure_metadata,
                )
            raise CloudStageError(
                code,
                reviewable=code.startswith(("visual.", "segmentation.")),
            )
        # preview: continue with the reconciled subset (skipped panels drop out),
        # preserving the deterministic chapter order for downstream stages.
        reconciled = [
            reconciled_by_id[item.panel_id] for item in ordered if item.panel_id in reconciled_by_id
        ]
        result = VisualStageResult(
            panels=tuple(reconciled),
            source_hash=_hash(source),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            cache_identity_version=VISUAL_CACHE_IDENTITY_VERSION,
            panel_identity_hashes=tuple(_hash(item) for item in source),
        )
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result
