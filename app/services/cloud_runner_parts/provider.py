"""Provider methods extracted from cloud_multimodal."""

# ruff: noqa: F821 -- runtime globals are refreshed from the compatibility facade.
from __future__ import annotations

from app.constants import (
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
)

from .runtime import runtime_bound

_RUNTIME_NAMES = (
    'CloudStageError',
    'Mapping',
    'StoryMapResult',
    'VISUAL_CHECKPOINT_VERSION',
    'VisionCapabilityError',
    'VisionProviderRequestFailed',
    'VisionRequestInvalid',
    'VisionResponseInvalid',
    'VisualStageResult',
    '_hash',
    '_legacy_visual_descriptor_from_row',
    '_legacy_visual_model_identity',
    '_migrate_visual_cache_identity',
    'json',
    'math',
    'random',
    're',
    'script',
    'strip_segmentation',
    'time',
)
_bound = runtime_bound(_RUNTIME_NAMES)


class ProviderMixin:
    @_bound
    def _response_shape_metrics_for_failure(self, code: str) -> dict[str, Any]:
        """Attach only current positional shape metadata to a later safe error."""

        metrics = dict(self.last_response_shape_metrics)
        if not metrics:
            return {}
        metrics["failed_code"] = code
        if not metrics.get("failed_predicate"):
            metrics["failed_predicate"] = metrics.get("reconciled_failed_predicate") or code
        metrics["request_count"] = self.request_count
        metrics["request_counts"] = dict(self.request_counts)
        self.last_response_shape_metrics = dict(metrics)
        return metrics

    @staticmethod
    @_bound
    def _narration_repair_result_shape_metrics(
        result: NarrationResult,
        visual: VisualStageResult,
        *,
        scope_ok: bool | None = None,
    ) -> dict[str, Any]:
        """Describe reconstructed repair gates without retaining provider prose."""

        duration_metrics = script.narration_duration_metrics(
            result.spoken_text,
            "dramatic",
        )
        canonical_duration = float(duration_metrics["estimated_duration_s"])
        spoken_word_count = int(duration_metrics["word_count"])
        expected_display = tuple(re.findall(r"[A-Z0-9]+", result.spoken_text.upper()))
        observation_ids = tuple(
            str(item.get("panel_id", ""))
            for item in result.observations
            if isinstance(item, Mapping)
        )
        visual_ids = tuple(
            str(item.get("panel_id", "")) for item in visual.panels if isinstance(item, Mapping)
        )
        failed: list[str] = []
        try:
            duration = float(result.estimated_duration_s)
        except (TypeError, ValueError, OverflowError):
            duration = None
        try:
            reported_word_count = int(result.word_count)
        except (TypeError, ValueError, OverflowError):
            reported_word_count = None
        if (
            duration is None
            or not STANDARD_FINAL_DURATION_MIN_SECONDS
            <= duration
            <= STANDARD_FINAL_DURATION_MAX_SECONDS
        ):
            failed.append("duration_bounds")
        elif not math.isclose(
            duration,
            canonical_duration,
            rel_tol=0.0,
            abs_tol=0.001,
        ):
            failed.append("duration_reconciliation")
        if reported_word_count is None or not 115 <= reported_word_count <= 125:
            failed.append("word_bounds")
        if reported_word_count != spoken_word_count:
            failed.append("word_count_reconciliation")
        if not 4 <= len(result.passages) <= 6:
            failed.append("passage_count")
        if tuple(str(word) for word in result.display_words) != expected_display:
            failed.append("display_derivation")
        if len(observation_ids) != len(visual_ids):
            failed.append("observation_count")
        elif observation_ids != visual_ids:
            failed.append("observation_panel_order")
        if scope_ok is False:
            failed.append("scope_compatibility")
        return {
            "reconciled_word_count": reported_word_count,
            "reconciled_spoken_word_count": spoken_word_count,
            "reconciled_duration_s": duration,
            "reconciled_canonical_duration_s": canonical_duration,
            "reconciled_duration_contract": duration_metrics,
            "reconciled_passage_count": len(result.passages),
            "reconciled_observation_count": len(result.observations),
            "reconciled_visual_panel_count": len(visual.panels),
            "reconciled_display_word_count": len(result.display_words),
            "reconciled_scope_ok": scope_ok,
            "reconciled_failed_predicates": failed,
            "reconciled_failed_predicate": failed[0] if failed else None,
        }

    @_bound
    def _call(self, operation, *, request_stage: str = "other") -> Any:
        last_error: Exception | None = None
        stage = request_stage if request_stage in self.request_counts else "other"
        for attempt in range(self.max_attempts):
            stage_limit = (
                self.max_narration_requests
                if stage == "narration"
                else self.max_repair_requests
                if stage == "narration_repair"
                else None
            )
            if stage_limit is not None and self.request_counts[stage] >= stage_limit:
                raise CloudStageError("cloud.request_budget_exceeded", reviewable=True)
            if (
                self._use_legacy_global_request_budget
                and self.max_requests is not None
                and self.request_count >= self.max_requests
            ):
                raise CloudStageError("cloud.request_budget_exceeded", reviewable=True)
            if self.min_request_interval_s:
                wait = self.min_request_interval_s - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    time.sleep(wait)
            self.request_count += 1
            self.request_counts[stage] += 1
            self.estimated_cost_usd += self.estimated_cost_per_request
            self._last_request_at = time.monotonic()
            try:
                if self._provider_concurrency_gate is not None:
                    return self._provider_concurrency_gate.call(operation)
                return operation()
            except CloudStageError:
                raise
            except (VisionRequestInvalid, VisionResponseInvalid) as exc:
                last_error = exc
                break
            except VisionProviderRequestFailed as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= self.max_attempts:
                    break
                delay = exc.retry_after_s
                if delay is None:
                    base = min(8.0, 0.5 * (2**attempt))
                    delay = base + random.uniform(0.0, base * 0.25)
                if delay > 0:
                    time.sleep(float(delay))
            except VisionCapabilityError as exc:
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    base = min(8.0, 0.5 * (2**attempt))
                    time.sleep(base + random.uniform(0.0, base * 0.25))
        provider_error_code = str(getattr(last_error, "code", "") or "")
        provider_error_mapping = {
            "vision_response_invalid": "cloud.provider_response_invalid",
            "vision_provider_request_failed": "cloud.provider_request_failed",
            "vision_request_invalid": "cloud.provider_request_invalid",
            "vision_capability_missing": "cloud.capability_missing",
        }
        mapped_code = provider_error_mapping.get(provider_error_code)
        if mapped_code is not None:
            metadata = {
                "provider_error_code": provider_error_code,
                "request_stage": stage,
            }
            status_code = getattr(last_error, "status_code", None)
            retry_after = getattr(last_error, "retry_after_s", None)
            if isinstance(status_code, int):
                metadata["status_code"] = status_code
            if isinstance(retry_after, (int, float)):
                metadata["retry_after_s"] = float(retry_after)
            if isinstance(last_error, VisionProviderRequestFailed):
                metadata["timeout"] = bool(getattr(last_error, "timeout", False))
                metadata["retryable"] = bool(getattr(last_error, "retryable", False))
                subtype = getattr(last_error, "transport_subtype", None)
                if isinstance(subtype, str) and subtype:
                    metadata["transport_subtype"] = subtype
                if metadata.get("timeout"):
                    metadata["provider_error_category"] = "timeout"
                elif metadata.get("status_code") == 429:
                    metadata["provider_error_category"] = "rate_limit"
                elif (
                    isinstance(metadata.get("status_code"), int) and metadata["status_code"] >= 500
                ):
                    metadata["provider_error_category"] = "server"
                else:
                    metadata["provider_error_category"] = "transport"
            raise CloudStageError(
                mapped_code,
                reviewable=mapped_code == "cloud.provider_response_invalid",
                safe_metadata=metadata,
            ) from None
        raise CloudStageError("cloud.provider_request_failed") from None

    @_bound
    def assess_strip_boundaries(
        self, request: strip_segmentation.BoundaryRequest
    ) -> Mapping[str, Any]:
        """Validate strip boundaries using real images or a local fallback.

        Generated strip tiles carry ephemeral bytes.  New requests use the
        provider's multimodal method when available; legacy metadata-only
        fixtures retain the text contract for backward-compatible reads.
        """
        prompt = self.prompts["segmentation"]
        payload = request.as_payload()
        images = tuple(
            tile
            for tile in request.tiles
            if isinstance(tile.get("payload"), bytes) and tile.get("payload")
        )
        complete_with_images = getattr(self.provider, "complete_json_with_images", None)
        if images and callable(complete_with_images):
            raw = self._call(
                lambda: complete_with_images(
                    stage="strip_segmentation",
                    prompt_version=prompt[0],
                    prompt_sha256=prompt[1],
                    prompt_text=prompt[2],
                    payload=payload,
                    images=images,
                ),
                request_stage="other",
            )
        elif images:
            # A provider without multimodal support must not receive bytes as
            # JSON text.  The deterministic local assessment is conservative:
            # only candidates already scored by the local detector can pass.
            raw = {
                "source_asset_id": request.source_asset_id,
                "source_checksum": request.source_checksum,
                "random_sampling": False,
                "boundaries": [
                    {
                        "y": candidate.position,
                        "accepted": candidate.confidence >= 0.7,
                        "confidence": candidate.confidence,
                        "reason": "segmentation.local_detector_assessment",
                        "protected_regions": [],
                    }
                    for candidate in request.candidates
                ],
            }
        else:
            raw = self._call(
                lambda: self.provider.complete_json(
                    stage="strip_segmentation",
                    prompt_version=prompt[0],
                    prompt_sha256=prompt[1],
                    prompt_text=prompt[2],
                    payload=payload,
                ),
                request_stage="other",
            )
        if not isinstance(raw, Mapping):
            return raw
        if (
            raw.get("source_asset_id") == request.source_asset_id
            and raw.get("source_checksum") == request.source_checksum
        ):
            return raw
        raise strip_segmentation.StripSegmentationError("segmentation.provider_lineage_invalid")

    @staticmethod
    @_bound
    def _ordered_panels(panels: Sequence[CloudPanelInput]) -> tuple[CloudPanelInput, ...]:
        # Chapter order first (source_family: 204__* < 205__* < 206__*), then
        # the per-asset strip order, so the provider and the story map see the
        # panels in the real chapter sequence.
        values = tuple(
            sorted(
                panels,
                key=lambda item: (str(item.source_family or ""), item.source_order, item.panel_id),
            )
        )
        if not values or len({item.panel_id for item in values}) != len(values):
            raise CloudStageError("cloud.panel_coverage_incomplete")
        if len({item.source_order for item in values}) != len(values):
            raise CloudStageError("cloud.panel_lineage_invalid")
        return values

    @_bound
    def _checkpoint_scope(
        self,
        source: Sequence[Mapping[str, Any]],
        prompt: tuple[str, str, str],
    ) -> str:
        return _hash(
            {
                "checkpoint_version": VISUAL_CHECKPOINT_VERSION,
                "model_identity_hash": self.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
            }
        )

    @_bound
    def _checkpoint_load(self, scope: str) -> dict[str, dict[str, Any]]:
        path = self.visual_checkpoint_path
        out: dict[str, dict[str, Any]] = {}
        if path is None or not path.is_file():
            return out
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if (
                not isinstance(item, Mapping)
                or item.get("checkpoint_scope") != scope
                or item.get("checkpoint_version") != VISUAL_CHECKPOINT_VERSION
            ):
                continue
            panel_id = item.get("panel_id")
            if isinstance(panel_id, str) and panel_id.strip():
                clean = dict(item)
                clean.pop("checkpoint_scope", None)
                clean.pop("checkpoint_version", None)
                out[panel_id] = clean
        return out

    @_bound
    def _checkpoint_append(self, scope: str, entry: Mapping[str, Any]) -> None:
        path = self.visual_checkpoint_path
        if path is None:
            return
        record = dict(entry)
        record["checkpoint_scope"] = scope
        record["checkpoint_version"] = VISUAL_CHECKPOINT_VERSION
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            with self._checkpoint_lock, path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            return

    @_bound
    def _migrate_legacy_visual_cache(
        self,
        ordered: Sequence[CloudPanelInput],
        *,
        key: str,
        prompt: tuple[str, str, str],
    ) -> dict[str, Any] | None:
        """Materialize one exact legacy visual record under the current key.

        The scan is deliberately read-only over old records and accepts only
        one candidate whose ordered IDs, source lineage, payload descriptor,
        model identity, and visual prompt all reconcile.  Ambiguous matches
        fail closed instead of choosing an arbitrary cache file.
        """

        if self.cache is None:
            return None
        iter_records = getattr(self.cache, "iter_records", None)
        legacy_identity = _legacy_visual_model_identity(self.model_identity)
        if not callable(iter_records) or legacy_identity is None:
            return None
        candidates: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
        try:
            records = iter_records()
            for record in records:
                migrated = _migrate_visual_cache_identity(
                    record,
                    ordered,
                    model_identity=legacy_identity,
                    prompt=prompt,
                )
                if migrated is not None:
                    candidates.append((record, migrated))
        except (OSError, TypeError, ValueError):
            return None
        if len(candidates) != 1:
            return None
        legacy_record, migrated = candidates[0]
        try:
            legacy_result = VisualStageResult.from_dict(legacy_record)
        except (KeyError, TypeError, ValueError):
            return None
        migrated["model_identity_hash"] = self.model_identity.identity_hash
        migrated["legacy_model_identity_hash"] = str(legacy_record.get("model_identity_hash", ""))
        migrated["legacy_visual_evidence_hash"] = legacy_result.visual_evidence_hash
        migrated["cache_identity_migration_proof"] = "legacy_model_identity_and_descriptor_hash"
        self.cache.put(key, migrated)
        return migrated

    @_bound
    def _legacy_visual_evidence_hashes(
        self,
        visual: VisualStageResult,
    ) -> set[str]:
        """Find old visual evidence identities that exactly match this visual set."""

        if self.cache is None:
            return set()
        iter_records = getattr(self.cache, "iter_records", None)
        legacy_identity = _legacy_visual_model_identity(self.model_identity)
        if not callable(iter_records) or legacy_identity is None:
            return set()
        current_rows = tuple(visual.panels)
        hashes: set[str] = set()
        try:
            records = iter_records()
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                if (
                    str(record.get("model_identity_hash", "")) != legacy_identity.identity_hash
                    or str(record.get("prompt_version", "")) != self.prompts["visual"][0]
                    or str(record.get("prompt_sha256", "")) != self.prompts["visual"][1]
                ):
                    continue
                try:
                    old_result = VisualStageResult.from_dict(record)
                except (KeyError, TypeError, ValueError):
                    continue
                if old_result.panel_ids != visual.panel_ids:
                    continue
                old_rows = tuple(old_result.panels)
                if len(old_rows) != len(current_rows):
                    continue
                if any(
                    any(
                        old_row.get(field) != current_row.get(field)
                        for field in (
                            "panel_id",
                            "source_asset_id",
                            "source_order",
                            "source_checksum",
                            "mime_type",
                            "payload_checksum",
                            "panel_bounds",
                            "source_dimensions",
                            "strip_region_id",
                            "coverage_map_version",
                            "coverage_map_hash",
                            "segmentation_version",
                        )
                    )
                    for old_row, current_row in zip(old_rows, current_rows, strict=True)
                ):
                    continue
                legacy_descriptors = [_legacy_visual_descriptor_from_row(row) for row in old_rows]
                if any(descriptor is None for descriptor in legacy_descriptors):
                    continue
                if str(record.get("source_hash", "")) != _hash(legacy_descriptors):
                    continue
                hashes.add(old_result.visual_evidence_hash)
        except (OSError, TypeError, ValueError):
            return set()
        return hashes

    @_bound
    def _migrate_legacy_story_map_cache(
        self,
        visual: VisualStageResult,
        *,
        key: str,
        prompt: tuple[str, str, str],
    ) -> dict[str, Any] | None:
        """Materialize one exact story map after a visual-only identity bump."""

        if self.cache is None:
            return None
        iter_records = getattr(self.cache, "iter_records", None)
        legacy_identity = _legacy_visual_model_identity(self.model_identity)
        old_visual_hashes = self._legacy_visual_evidence_hashes(visual)
        if not callable(iter_records) or legacy_identity is None or not old_visual_hashes:
            return None
        candidates: list[dict[str, Any]] = []
        panel_ids = visual.panel_ids
        panel_set = set(panel_ids)
        try:
            for record in iter_records():
                if not isinstance(record, Mapping):
                    continue
                if (
                    str(record.get("model_identity_hash", "")) != legacy_identity.identity_hash
                    or str(record.get("prompt_version", "")) != prompt[0]
                    or str(record.get("prompt_sha256", "")) != prompt[1]
                    or str(record.get("visual_evidence_hash", "")) not in old_visual_hashes
                ):
                    continue
                try:
                    result = StoryMapResult.from_dict(record)
                except (KeyError, TypeError, ValueError):
                    continue
                if result.panel_ids != panel_ids:
                    continue
                if result.story_map_hash != _hash(
                    {
                        "beats": list(result.beats),
                        "claims": list(result.claims),
                        "chain": list(result.causal_chain),
                    }
                ):
                    continue
                if any(
                    str(panel_id) not in panel_set
                    for row in (*result.beats, *result.claims)
                    for panel_id in row.get("panel_ids", row.get("evidence_panel_ids", ()))
                    if isinstance(row, Mapping)
                ):
                    continue
                migrated = result.as_dict()
                migrated["model_identity_hash"] = self.model_identity.identity_hash
                migrated["visual_evidence_hash"] = visual.visual_evidence_hash
                migrated["cache_identity_migration_proof"] = (
                    "legacy_model_identity_and_visual_evidence_hash"
                )
                candidates.append(migrated)
        except (OSError, TypeError, ValueError):
            return None
        if len(candidates) != 1:
            return None
        migrated = candidates[0]
        self.cache.put(key, migrated)
        return migrated
