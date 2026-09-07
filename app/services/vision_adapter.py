from __future__ import annotations

import base64
import hashlib
import importlib
import json
import re
import string
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.services import visual_scoring

VISION_REQUEST_TIMEOUT = 600.0
SYNTHESIS_WIRE_CONTRACT_VERSION = "vision-synthesis-wire-v13"
ANALYSIS_WINDOW_CONTRACT_VERSION = "visual-analysis-windows-v1"
ANALYSIS_WINDOW_MAX_COUNT = 12

_REQUIRED_OBSERVATION_KEYS = frozenset(
    {
        "panel_id",
        "visible_facts",
        "dialogue_or_ocr",
        "inferences",
        "uncertainties",
        "entities",
        "state_changes",
        "causal_links",
        "evidence_refs",
    }
)

_REQUIRED_SYNTHESIS_OBSERVATION_KEYS = frozenset(
    {
        "panel_id",
        "source_asset_id",
        "strip_region_id",
        "source_index",
        "region_bounds",
        "coverage_map_version",
        "coverage_map_hash",
        "visible_facts",
        "dialogue_or_ocr",
        "inferences",
        "uncertainties",
        "evidence_refs",
    }
)

_PROVIDER_VISUAL_KEYS = frozenset(
    {
        "balloon_mask_status",
        "balloon_regions",
        "protected_regions",
        "mask_confidence",
        "evidence_source",
        "mask_reason",
        "panel_id",
        "source_asset_id",
        "source_order",
    }
)
_PROVIDER_REGION_KEYS = frozenset(
    {
        "region_id",
        "kind",
        "normalized_bbox",
        "normalized_polygon",
        "confidence",
        "evidence_source",
        "mask_status",
    }
)
_PROTECTED_REGION_KEYS = frozenset(
    {
        "region_id",
        "kind",
        "normalized_bbox",
        "normalized_polygon",
        "confidence",
        "evidence_source",
        "required",
        "minimum_coverage",
    }
)
_OCR_ONLY_EVIDENCE_SOURCES = frozenset({"ocr_text_only", "text_only_ocr", "ocr_only"})
_BALLOON_KIND_ALIASES = frozenset(
    {
        "caption",
        "shout",
        "speech",
        "speech_edge",
        "speech_tail",
        "shout_balloon",
        "tail",
        "thought",
        "thought_edge",
        "thought_balloon",
    }
)
_BALLOON_MASK_STATUS_ALIASES = frozenset({"covered", "mask_required"})


def _is_ocr_only_evidence_source(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in _OCR_ONLY_EVIDENCE_SOURCES


def _normalize_provider_bbox(value: Any) -> Any:
    """Normalize a strict, unambiguous provider xywh alias to xyxy.

    Some compatible endpoints occasionally return normalized_bbox as
    [x, y, width, height].  We only reinterpret it when it is impossible as
    xyxy (x1 <= x0 or y1 <= y0) and the xywh conversion remains completely
    inside the unit frame.  Ambiguous valid xyxy boxes are never changed.
    """

    if not isinstance(value, list) or len(value) != 4:
        return value
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return value
    x0, y0, third, fourth = (float(item) for item in value)
    if not all(0.0 <= item <= 1.0 for item in (x0, y0, third, fourth)):
        return value
    if third > x0 and fourth > y0:
        return value
    if third <= 0.0 or fourth <= 0.0:
        return value
    x1 = x0 + third
    y1 = y0 + fourth
    if x1 > 1.0 or y1 > 1.0:
        return value
    return [x0, y0, x1, y1]


def _normalize_provider_visual_evidence(observation: Any) -> Any:
    """Normalize one conservative provider alias before local validation.

    Some OpenAI-compatible multimodal endpoints describe balloon regions with
    semantic labels (for example ``speech_edge``) and a covered mask as
    ``covered`` or ``mask_required``.  These are conservatively mapped to a
    local speech-balloon/non-empty geometry.  All other values remain
    untrusted and fail in the validator.
    """

    if not isinstance(observation, Mapping):
        return observation
    visual = observation.get("visual_evidence")
    if not isinstance(visual, Mapping):
        return observation
    regions = visual.get("balloon_regions")
    if not isinstance(regions, list):
        return observation
    normalized_regions: list[Any] = []
    changed = False
    for region in regions:
        if not isinstance(region, Mapping):
            normalized_regions.append(region)
            continue
        kind = region.get("kind")
        mask_status = region.get("mask_status")
        normalized_kind = (
            "speech_balloon"
            if isinstance(kind, str)
            and kind.strip().lower().replace("-", "_").replace(" ", "_") in _BALLOON_KIND_ALIASES
            else kind
        )
        normalized_mask_status = (
            "known_nonempty"
            if isinstance(mask_status, str)
            and mask_status.strip().lower().replace("-", "_").replace(" ", "_")
            in _BALLOON_MASK_STATUS_ALIASES
            else mask_status
        )
        normalized_bbox = _normalize_provider_bbox(region.get("normalized_bbox"))
        if (
            normalized_kind != kind
            or normalized_mask_status != mask_status
            or normalized_bbox != region.get("normalized_bbox")
        ):
            item = dict(region)
            item["kind"] = normalized_kind
            item["mask_status"] = normalized_mask_status
            item["normalized_bbox"] = normalized_bbox
            normalized_regions.append(item)
            changed = True
        else:
            normalized_regions.append(region)
    protected_regions = visual.get("protected_regions")
    normalized_protected_regions: list[Any] = []
    if isinstance(protected_regions, list):
        for region in protected_regions:
            if not isinstance(region, Mapping):
                normalized_protected_regions.append(region)
                continue
            normalized_bbox = _normalize_provider_bbox(region.get("normalized_bbox"))
            if normalized_bbox != region.get("normalized_bbox"):
                item = dict(region)
                item["normalized_bbox"] = normalized_bbox
                normalized_protected_regions.append(item)
                changed = True
            else:
                normalized_protected_regions.append(region)
    else:
        normalized_protected_regions = protected_regions
    if not changed:
        return observation
    normalized_visual = dict(visual)
    normalized_visual["balloon_regions"] = normalized_regions
    if isinstance(protected_regions, list):
        normalized_visual["protected_regions"] = normalized_protected_regions
    normalized_observation = dict(observation)
    normalized_observation["visual_evidence"] = normalized_visual
    return normalized_observation


@dataclass(frozen=True)
class VisionCapabilityReport:
    provider_type: str
    provider_name: str
    model: str | None
    image_input: bool
    structured_json: bool
    available: bool
    blocking_reason: str | None


@dataclass(frozen=True)
class VisionObservationRequest:
    analysis_run_id: str
    instruction_version: str
    instruction_sha256: str
    chunk_index: int
    panels: tuple[Mapping[str, Any], ...]
    visual_instruction_version: str | None = None
    visual_instruction_sha256: str | None = None


@dataclass(frozen=True)
class VisionChapterSynthesisRequest:
    analysis_run_id: str
    instruction_version: str
    instruction_sha256: str
    instruction_text: str
    expected_panel_ids: tuple[str, ...]
    coverage_manifest: Mapping[str, Any]
    ordered_observations: tuple[Mapping[str, Any], ...]
    chunks: tuple[Mapping[str, Any], ...]
    narrative_profile_id: str | None = None
    narrative_profile_version: str | None = None
    narrative_profile_sha256: str | None = None
    target_word_count_min: int | None = None
    target_word_count_max: int | None = None
    preferred_visual_panel_ids: tuple[str, ...] = ()
    preferred_visual_panel_ids_by_section: Mapping[str, tuple[str, ...]] | None = None
    retry_word_counts: tuple[int, ...] | None = None
    retry_visual_selection: bool = False
    retry_evidence_lineage: bool = False
    retry_dialogue_paraphrase: bool = False
    retry_claim_qualification: bool = False
    retry_claim_semantic_grounding: bool = False
    retry_claim_semantic_diagnostics: Mapping[str, Any] | None = None
    retry_causal_arc: bool = False
    retry_causal_diagnostics: Mapping[str, Any] | None = None
    retry_visual_story_alignment: bool = False
    retry_visual_story_diagnostics: Mapping[str, Any] | None = None
    retry_projection_contract: bool = False
    retry_local_claim_grounding: bool = False
    retry_passages: tuple[Mapping[str, Any], ...] | None = None
    retry_text_only_locked_output: Mapping[str, Any] | None = None


class VisionObservationProvider(Protocol):
    def capability(self) -> VisionCapabilityReport: ...

    def observe(self, request: VisionObservationRequest) -> list[Mapping[str, Any]]: ...

    def synthesize(self, request: VisionChapterSynthesisRequest) -> Mapping[str, Any]: ...


class VisionCapabilityError(RuntimeError):
    """Safe, machine-readable failure for vision capability or response gates."""

    code = "vision_capability_missing"


class VisionRequestInvalid(VisionCapabilityError):
    code = "vision_request_invalid"

    def __init__(self, message: str = "invalid vision request") -> None:
        super().__init__(message)
        self.retryable = False
        self.status_code: int | None = None
        self.retry_after_s: float | None = None


class VisionResponseInvalid(VisionCapabilityError):
    code = "vision_response_invalid"

    def __init__(
        self,
        message: str = "invalid vision response",
        *,
        validation_subtype: str | None = None,
        passage_word_counts: tuple[int, ...] | None = None,
        retry_passages: tuple[Mapping[str, Any], ...] | None = None,
        selection_diagnostics: Mapping[str, Any] | None = None,
        retry_locked_output: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = False
        self.status_code: int | None = None
        self.retry_after_s: float | None = None
        self.validation_subtype = validation_subtype
        self.passage_word_counts = passage_word_counts
        self.retry_passages = retry_passages
        self.selection_diagnostics = dict(selection_diagnostics or {})
        self.retry_locked_output = (
            dict(retry_locked_output) if isinstance(retry_locked_output, Mapping) else None
        )


class VisionProviderRequestFailed(VisionCapabilityError):
    code = "vision_provider_request_failed"

    def __init__(
        self,
        message: str = "vision provider request failed",
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
        retryable: bool = True,
        timeout: bool = False,
        transport_subtype: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        self.timeout = bool(timeout)
        self.transport_subtype = transport_subtype


def _retry_passages_from_candidate(value: Any) -> tuple[Mapping[str, Any], ...] | None:
    passages = value.get("script_passages") if isinstance(value, Mapping) else None
    if (
        not isinstance(passages, list)
        or not 4 <= len(passages) <= 6
        or any(not isinstance(item, Mapping) for item in passages)
    ):
        return None
    return tuple(dict(item) for item in passages)


TEXT_ONLY_SYNTHESIS_RETRY_SUBTYPES = frozenset(
    {
        "script_passage_word_count_is_outside_its_role_guardrail",
        "script_passage_narration_must_contain_90-125_words",
        "script_passage_copies_source_dialogue",
        "production_narration_word_count_out_of_range",
        "retention_hook_must_contain_8-14_words",
        "retention_hook_must_be_one_sentence",
        "production_subtitle_overflow",
    }
)


def _apply_text_only_retry_lock(
    result: Mapping[str, Any], request: VisionChapterSynthesisRequest
) -> Mapping[str, Any]:
    locked = request.retry_text_only_locked_output
    if locked is None:
        return result

    locked_passages = locked.get("script_passages") if isinstance(locked, Mapping) else None
    new_passages = result.get("script_passages") if isinstance(result, Mapping) else None
    if (
        not isinstance(locked_passages, list)
        or not isinstance(new_passages, list)
        or len(locked_passages) != len(new_passages)
    ):
        raise VisionResponseInvalid(validation_subtype="synthesis_text_only_lock_invalid")

    text_by_id: dict[str, str] = {}
    for passage in new_passages:
        if not isinstance(passage, Mapping):
            raise VisionResponseInvalid(validation_subtype="synthesis_text_only_lock_invalid")
        passage_id = passage.get("passage_id")
        text = passage.get("text")
        if (
            not isinstance(passage_id, str)
            or not passage_id
            or not isinstance(text, str)
            or not text.strip()
            or passage_id in text_by_id
        ):
            raise VisionResponseInvalid(validation_subtype="synthesis_text_only_lock_invalid")
        text_by_id[passage_id] = text

    rebuilt_passages: list[dict[str, Any]] = []
    locked_ids: set[str] = set()
    for passage in locked_passages:
        if not isinstance(passage, Mapping):
            raise VisionResponseInvalid(validation_subtype="synthesis_text_only_lock_invalid")
        passage_id = str(passage.get("passage_id", ""))
        if not passage_id or passage_id not in text_by_id or passage_id in locked_ids:
            raise VisionResponseInvalid(validation_subtype="synthesis_text_only_lock_invalid")
        locked_ids.add(passage_id)
        rebuilt = dict(passage)
        rebuilt["text"] = text_by_id[passage_id]
        rebuilt_passages.append(rebuilt)

    if set(text_by_id) != locked_ids:
        raise VisionResponseInvalid(validation_subtype="synthesis_text_only_lock_invalid")
    rebuilt_output = dict(locked)
    rebuilt_output["script_passages"] = rebuilt_passages
    return rebuilt_output


def _safe_validation_subtype(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = "_".join(value.lower().strip().split())
    safe = "".join(ch for ch in normalized if ch.isalnum() or ch in {"_", "-"})
    return safe[:96] or None


def _chat_completion_content(response: httpx.Response) -> str:
    """Extract assistant text from JSON or OpenAI-style SSE responses."""

    headers = getattr(response, "headers", {})
    content_type = str(headers.get("content-type", "")).lower()
    if "text/event-stream" not in content_type:
        payload = response.json()
        return payload["choices"][0]["message"]["content"]

    fragments: list[str] = []
    for raw_line in response.text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except (TypeError, ValueError):
            raise VisionResponseInvalid() from None
        if not isinstance(event, Mapping):
            continue
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, Mapping):
            continue
        delta = choice.get("delta")
        message = choice.get("message")
        value = (
            delta.get("content")
            if isinstance(delta, Mapping)
            else message.get("content")
            if isinstance(message, Mapping)
            else choice.get("text")
        )
        if isinstance(value, str):
            fragments.append(value)
        elif isinstance(value, list):
            fragments.extend(
                str(part["text"])
                for part in value
                if isinstance(part, Mapping) and isinstance(part.get("text"), str)
            )
    if not fragments:
        raise VisionResponseInvalid()
    return "".join(fragments)


def _decode_json_content(content: str) -> Any:
    """Decode strict JSON, accepting only a whole-response JSON code fence."""

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if (
            len(lines) < 3
            or lines[-1].strip() != "```"
            or lines[0].strip().lower() not in {"```", "```json"}
        ):
            raise ValueError("invalid JSON code fence")
        text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def _raise_http_failure(response: httpx.Response) -> None:
    """Map known provider request errors without retaining provider payloads."""

    try:
        body = response.json()
    except Exception:
        body = None
    if (
        response.status_code == 400
        and isinstance(body, Mapping)
        and body.get("code") == "invalid_image"
    ):
        raise VisionRequestInvalid() from None
    retry_after: float | None = None
    raw_retry_after = getattr(response, "headers", {}).get("retry-after")
    if isinstance(raw_retry_after, str):
        try:
            retry_after = max(0.0, min(60.0, float(raw_retry_after.strip())))
        except (TypeError, ValueError):
            retry_after = None
    retryable = response.status_code == 429 or response.status_code >= 500
    raise VisionProviderRequestFailed(
        status_code=int(response.status_code),
        retry_after_s=retry_after,
        retryable=retryable,
    ) from None


def _reconcile_synthesis_echo_fields(
    result: Any,
    request: VisionChapterSynthesisRequest,
) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise VisionResponseInvalid()
    reconciled = dict(result)
    canonical_observations = [dict(item) for item in request.ordered_observations]
    provider_observations = reconciled.get("observations")
    if provider_observations not in (None, []) and provider_observations != canonical_observations:
        raise VisionResponseInvalid()
    reconciled["observations"] = canonical_observations

    canonical_coverage = dict(request.coverage_manifest)
    provider_coverage = reconciled.get("coverage_manifest")
    if provider_coverage not in (None, {}):
        if not isinstance(provider_coverage, Mapping):
            raise VisionResponseInvalid()
        for key, value in provider_coverage.items():
            if key in canonical_coverage and canonical_coverage[key] != value:
                raise VisionResponseInvalid()
    reconciled["coverage_manifest"] = canonical_coverage

    ledger = reconciled.get("continuity_ledger")
    if not isinstance(ledger, Mapping):
        raise VisionResponseInvalid()
    ledger = dict(ledger)
    canonical_chunks = [dict(chunk) for chunk in request.chunks]
    canonical_identity = [
        {"chunk_id": chunk["chunk_id"], "panel_ids": list(chunk["panel_ids"])}
        for chunk in canonical_chunks
    ]
    provider_chunks = ledger.get("chunks")
    if provider_chunks in (None, []):
        ledger["chunks"] = canonical_chunks
    else:
        if not isinstance(provider_chunks, list):
            raise VisionResponseInvalid()
        normalized_provider = []
        for chunk in provider_chunks:
            if not isinstance(chunk, Mapping):
                raise VisionResponseInvalid()
            normalized_provider.append(
                {"chunk_id": chunk.get("chunk_id"), "panel_ids": chunk.get("panel_ids")}
            )
        if normalized_provider != canonical_identity:
            raise VisionResponseInvalid()
    reconciled["continuity_ledger"] = ledger
    return reconciled


class OpenAICompatibleVisionProvider:
    """Minimal OpenAI-compatible multimodal adapter with fail-closed parsing."""

    def __init__(self, *, base_url: str, model: str, api_key: str) -> None:
        self._base_url = base_url.strip() if isinstance(base_url, str) else ""
        self._model = model.strip() if isinstance(model, str) else ""
        self._api_key = api_key.strip() if isinstance(api_key, str) else ""
        # Process-local only: encoded image data is never persisted or exposed
        # in stage payloads.  The bounded cache avoids re-encoding the same
        # panel on a missing-only retry.
        self._ephemeral_image_cache: dict[str, str] = {}
        self._ephemeral_image_cache_lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleVisionProvider("
            f"base_url={self._base_url!r}, model={self._model!r}, "
            "api_key='[redacted]')"
        )

    @property
    def model_id(self) -> str:
        """The configured model identity, safe to expose in stage metadata."""

        return self._model

    @property
    def endpoint(self) -> str:
        """Configured endpoint without credentials, for pinned stage identity."""

        return self._base_url

    def _encoded_image(self, *, mime_type: str, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        key = f"{mime_type.lower()}:{digest}"
        with self._ephemeral_image_cache_lock:
            encoded = self._ephemeral_image_cache.get(key)
        if encoded is None:
            encoded = base64.b64encode(payload).decode("ascii")
            with self._ephemeral_image_cache_lock:
                existing = self._ephemeral_image_cache.get(key)
                if existing is not None:
                    return existing
                if len(self._ephemeral_image_cache) >= 256:
                    self._ephemeral_image_cache.pop(next(iter(self._ephemeral_image_cache)))
                self._ephemeral_image_cache[key] = encoded
        return encoded

    def capability(self) -> VisionCapabilityReport:
        available = self._configured()
        return VisionCapabilityReport(
            provider_type="openai_compatible",
            provider_name="openai_compatible",
            model=self._model or None,
            image_input=available,
            structured_json=available,
            available=available,
            blocking_reason=None if available else VisionCapabilityError.code,
        )

    def observe(self, request: VisionObservationRequest) -> list[Mapping[str, Any]]:
        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()

        panels = _validate_request(request)
        payload = _build_payload(
            request,
            panels,
            self._model,
            encode_image=self._encoded_image,
        )
        try:
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=VISION_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_http_failure(exc.response)
        except httpx.TimeoutException:
            raise VisionProviderRequestFailed(
                timeout=True, retryable=True, transport_subtype="timeout"
            ) from None
        except httpx.TransportError:
            raise VisionProviderRequestFailed(retryable=True, transport_subtype="connect") from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None

        try:
            content = _chat_completion_content(response)
        except Exception:
            raise VisionResponseInvalid() from None
        if not isinstance(content, str):
            raise VisionResponseInvalid()

        try:
            observations = _decode_json_content(content)
        except (TypeError, ValueError):
            raise VisionResponseInvalid() from None
        if isinstance(observations, Mapping):
            if set(observations) != {"observations"}:
                raise VisionResponseInvalid()
            observations = observations.get("observations")
        if request.visual_instruction_version is not None:
            observations = (
                [_normalize_provider_visual_evidence(item) for item in observations]
                if isinstance(observations, list)
                else observations
            )
        return _validate_observations(
            observations,
            panels,
            require_visual_evidence=request.visual_instruction_version is not None,
        )

    def synthesize(self, request: VisionChapterSynthesisRequest) -> Mapping[str, Any]:
        try:
            expected_panel_ids, profile = _validate_synthesis_request(request)
        except VisionRequestInvalid:
            raise
        except Exception:
            raise VisionRequestInvalid() from None

        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()

        payload = _build_synthesis_payload(request, expected_panel_ids, self._model, profile)
        try:
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=VISION_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_http_failure(exc.response)
        except httpx.TimeoutException:
            raise VisionProviderRequestFailed(
                timeout=True, retryable=True, transport_subtype="timeout"
            ) from None
        except httpx.TransportError:
            raise VisionProviderRequestFailed(retryable=True, transport_subtype="connect") from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None

        try:
            provider_payload = response.json()
            content = provider_payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            result = _decode_json_content(content)
        except Exception:
            raise VisionResponseInvalid(
                validation_subtype="synthesis_provider_json_invalid"
            ) from None
        try:
            if request.retry_text_only_locked_output is not None:
                result = _apply_text_only_retry_lock(result, request)
            else:
                result = _reconcile_synthesis_echo_fields(result, request)
        except VisionResponseInvalid as exc:
            raise VisionResponseInvalid(
                validation_subtype=exc.validation_subtype or "synthesis_echo_lineage_invalid"
            ) from None

        validation_candidate = result
        try:
            analyzer_contract = importlib.import_module("app.services.analyzer_contract")
            try:
                if profile is None:
                    analyzer_contract.validate_analyzer_output(
                        result, expected_panel_ids=expected_panel_ids
                    )
                else:
                    analyzer_contract.validate_analyzer_output(
                        result,
                        expected_panel_ids=expected_panel_ids,
                        narrative_profile_id=profile.profile_id,
                        validate_text_checks=False,
                    )
                validated_result = result
            except analyzer_contract.AnalyzerContractError:
                projected = _project_provider_synthesis_output(result, request)
                validation_candidate = projected
                if profile is None:
                    analyzer_contract.validate_analyzer_output(
                        projected, expected_panel_ids=expected_panel_ids
                    )
                else:
                    analyzer_contract.validate_analyzer_output(
                        projected,
                        expected_panel_ids=expected_panel_ids,
                        narrative_profile_id=profile.profile_id,
                        validate_text_checks=False,
                    )
                validated_result = projected
        except VisionResponseInvalid as exc:
            raise VisionResponseInvalid(
                validation_subtype=exc.validation_subtype or "synthesis_projection_invalid",
                retry_passages=_retry_passages_from_candidate(validation_candidate),
                selection_diagnostics=getattr(exc, "selection_diagnostics", None),
            ) from None
        except analyzer_contract.AnalyzerContractError as exc:
            current_exc: Any = exc
            repair_attempts = 0
            while (
                current_exc is not None
                and _safe_validation_subtype(str(current_exc))
                == "claim_evidence_lacks_semantic_anchor"
                and repair_attempts < 8
            ):
                repaired = (
                    _repair_semantic_claim_evidence_from_diagnostics(
                        validation_candidate,
                        getattr(current_exc, "diagnostics", None),
                        expected_panel_ids,
                    )
                    if isinstance(validation_candidate, Mapping)
                    else None
                )
                if repaired is None and isinstance(validation_candidate, Mapping):
                    repaired = _repair_semantic_claim_text_from_diagnostics(
                        validation_candidate,
                        getattr(current_exc, "diagnostics", None),
                        expected_panel_ids,
                    )
                if repaired is None and isinstance(validation_candidate, Mapping):
                    repaired = _repair_unsupported_must_from_diagnostics(
                        validation_candidate,
                        getattr(current_exc, "diagnostics", None),
                        expected_panel_ids,
                    )
                if repaired is None and isinstance(validation_candidate, Mapping):
                    repaired = _repair_semantic_claim_from_candidate_excerpt(
                        validation_candidate,
                        getattr(current_exc, "diagnostics", None),
                        expected_panel_ids,
                    )
                if repaired is None:
                    break
                validation_candidate = repaired
                repair_attempts += 1
                try:
                    if profile is None:
                        analyzer_contract.validate_analyzer_output(
                            repaired, expected_panel_ids=expected_panel_ids
                        )
                    else:
                        analyzer_contract.validate_analyzer_output(
                            repaired,
                            expected_panel_ids=expected_panel_ids,
                            narrative_profile_id=profile.profile_id,
                            validate_text_checks=False,
                        )
                except analyzer_contract.AnalyzerContractError as repaired_exc:
                    current_exc = repaired_exc
                    continue
                validated_result = repaired
                current_exc = None
            if (
                current_exc is not None
                and _safe_validation_subtype(str(current_exc))
                == "retention_passage_introduces_disconnected_claim"
                and isinstance(validation_candidate, Mapping)
            ):
                repaired = _repair_disconnected_passage_from_diagnostics(
                    validation_candidate,
                    getattr(current_exc, "diagnostics", None),
                    expected_panel_ids,
                )
                if repaired is not None:
                    validation_candidate = repaired
                    try:
                        if profile is None:
                            analyzer_contract.validate_analyzer_output(
                                repaired, expected_panel_ids=expected_panel_ids
                            )
                        else:
                            analyzer_contract.validate_analyzer_output(
                                repaired,
                                expected_panel_ids=expected_panel_ids,
                                narrative_profile_id=profile.profile_id,
                                validate_text_checks=False,
                            )
                    except analyzer_contract.AnalyzerContractError as repaired_exc:
                        current_exc = repaired_exc
                    else:
                        validated_result = repaired
                        current_exc = None
            if current_exc is not None:
                exc = current_exc
                counts: tuple[int, ...] | None = None
                passages = (
                    validation_candidate.get("script_passages")
                    if isinstance(validation_candidate, Mapping)
                    else None
                )
                if isinstance(passages, list):
                    values = [
                        len(p["text"].split())
                        for p in passages
                        if isinstance(p, Mapping) and isinstance(p.get("text"), str)
                    ]
                    counts = tuple(values) if len(values) == len(passages) else None
                validation_subtype = _safe_validation_subtype(str(exc))
                diagnostics: Mapping[str, Any] | None = getattr(exc, "diagnostics", None)
                if validation_subtype == "script_passage_copies_source_dialogue":
                    diagnostics = analyzer_contract.source_dialogue_copy_diagnostics(
                        request.ordered_observations, passages
                    )
                raise VisionResponseInvalid(
                    validation_subtype=validation_subtype,
                    passage_word_counts=counts,
                    retry_passages=_retry_passages_from_candidate(validation_candidate),
                    selection_diagnostics=diagnostics,
                    retry_locked_output=(
                        validation_candidate
                        if validation_subtype in TEXT_ONLY_SYNTHESIS_RETRY_SUBTYPES
                        and isinstance(validation_candidate, Mapping)
                        else None
                    ),
                ) from None
        except Exception:
            raise VisionResponseInvalid(
                validation_subtype="synthesis_validation_internal_error"
            ) from None
        if not isinstance(validated_result, Mapping):
            raise VisionResponseInvalid(validation_subtype="synthesis_validated_result_invalid")
        if request.retry_visual_selection:
            validated_result = _complete_retry_visual_selection(validated_result, request)
        try:
            validate_synthesis_visual_selection(validated_result, request)
        except VisionResponseInvalid as exc:
            raise VisionResponseInvalid(
                validation_subtype=exc.validation_subtype,
                passage_word_counts=exc.passage_word_counts,
                retry_passages=_retry_passages_from_candidate(validated_result),
                selection_diagnostics=getattr(exc, "selection_diagnostics", None),
            ) from None

        if profile is not None:
            try:
                analyzer_contract.validate_analyzer_output(
                    validated_result,
                    expected_panel_ids=expected_panel_ids,
                    narrative_profile_id=profile.profile_id,
                )
            except analyzer_contract.AnalyzerContractError as exc:
                passages = (
                    validated_result.get("script_passages")
                    if isinstance(validated_result, Mapping)
                    else None
                )
                counts: tuple[int, ...] | None = None
                if isinstance(passages, list):
                    values: list[int] = []
                    for passage in passages:
                        if not isinstance(passage, Mapping) or not isinstance(
                            passage.get("text"), str
                        ):
                            values = []
                            break
                        values.append(len(passage["text"].split()))
                    if values:
                        counts = tuple(values)
                validation_subtype = _safe_validation_subtype(str(exc))
                diagnostics: Mapping[str, Any] | None = getattr(exc, "diagnostics", None)
                if validation_subtype == "script_passage_copies_source_dialogue":
                    diagnostics = analyzer_contract.source_dialogue_copy_diagnostics(
                        request.ordered_observations,
                        passages,
                    )
                raise VisionResponseInvalid(
                    validation_subtype=validation_subtype,
                    passage_word_counts=counts,
                    retry_passages=_retry_passages_from_candidate(validated_result),
                    selection_diagnostics=diagnostics,
                    retry_locked_output=(
                        validated_result
                        if validation_subtype in TEXT_ONLY_SYNTHESIS_RETRY_SUBTYPES
                        and isinstance(validated_result, Mapping)
                        else None
                    ),
                ) from None

        passages = validated_result.get("script_passages")
        if request.target_word_count_min is not None and isinstance(passages, list):
            counts = tuple(
                len(passage["text"].split())
                for passage in passages
                if isinstance(passage, Mapping) and isinstance(passage.get("text"), str)
            )
            if len(counts) != len(passages):
                raise VisionResponseInvalid()
            total = sum(counts)
            if (
                not request.target_word_count_min
                <= total
                <= int(request.target_word_count_max or 0)
            ):
                raise VisionResponseInvalid(
                    validation_subtype="production_narration_word_count_out_of_range",
                    passage_word_counts=counts,
                    retry_passages=_retry_passages_from_candidate(validated_result),
                    retry_locked_output=validated_result,
                )
        return validated_result

    def complete_json(
        self,
        *,
        stage: str,
        prompt_version: str,
        prompt_sha256: str,
        prompt_text: str = "",
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Run a strict JSON text stage through the same configured model.

        The cloud orchestration layer supplies stage prompts and performs all
        semantic reconciliation.  This adapter only speaks the existing
        OpenAI-compatible wire format and never returns provider error text.
        """

        if (
            not isinstance(stage, str)
            or not stage.strip()
            or not isinstance(prompt_version, str)
            or not prompt_version.strip()
            or not isinstance(prompt_sha256, str)
            or len(prompt_sha256) != 64
            or not isinstance(prompt_text, str)
        ):
            raise VisionRequestInvalid()
        if not isinstance(payload, Mapping):
            raise VisionRequestInvalid()
        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()
        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{prompt_text.rstrip()}\n\n"
                        f"Stage: {stage}. Prompt version: {prompt_version}. "
                        f"Prompt SHA-256: {prompt_sha256}. Return only valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 65536,
        }
        try:
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=VISION_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            content = _chat_completion_content(response)
            value = _decode_json_content(content)
        except httpx.HTTPStatusError as exc:
            _raise_http_failure(exc.response)
        except httpx.TimeoutException:
            raise VisionProviderRequestFailed(
                timeout=True, retryable=True, transport_subtype="timeout"
            ) from None
        except httpx.TransportError:
            raise VisionProviderRequestFailed(retryable=True, transport_subtype="connect") from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None
        if not isinstance(value, Mapping):
            raise VisionResponseInvalid()
        return value

    def complete_json_with_images(
        self,
        *,
        stage: str,
        prompt_version: str,
        prompt_sha256: str,
        prompt_text: str = "",
        payload: Mapping[str, Any],
        images: tuple[Mapping[str, Any], ...],
    ) -> Mapping[str, Any]:
        """Send structured JSON plus real multimodal image content.

        ``payload`` remains metadata-only.  Raw bytes are encoded only while
        constructing the request body and are retained solely in the bounded
        process-local image cache for a missing-only retry.
        """

        if (
            not isinstance(stage, str)
            or not stage.strip()
            or not isinstance(prompt_version, str)
            or not prompt_version.strip()
            or not isinstance(prompt_sha256, str)
            or len(prompt_sha256) != 64
            or not isinstance(prompt_text, str)
            or not isinstance(payload, Mapping)
            or not isinstance(images, tuple)
            or not images
        ):
            raise VisionRequestInvalid()
        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"{prompt_text.rstrip()}\n\nStage: {stage}. Prompt version: "
                    f"{prompt_version}. Prompt SHA-256: {prompt_sha256}. "
                    "Return only valid JSON. Metadata: "
                    f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
                ),
            }
        ]
        for image in images:
            mime_type = image.get("mime_type")
            image_bytes = image.get("payload")
            if (
                not isinstance(mime_type, str)
                or not mime_type.lower().startswith("image/")
                or not isinstance(image_bytes, bytes)
                or not image_bytes
            ):
                raise VisionRequestInvalid()
            encoded = self._encoded_image(
                mime_type=mime_type,
                payload=image_bytes,
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                    },
                }
            )
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 65536,
        }
        try:
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=VISION_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            value = _decode_json_content(_chat_completion_content(response))
        except httpx.HTTPStatusError as exc:
            _raise_http_failure(exc.response)
        except httpx.TimeoutException:
            raise VisionProviderRequestFailed(
                timeout=True, retryable=True, transport_subtype="timeout"
            ) from None
        except httpx.TransportError:
            raise VisionProviderRequestFailed(retryable=True, transport_subtype="connect") from None
        except (TypeError, ValueError, KeyError):
            raise VisionResponseInvalid() from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None
        if not isinstance(value, Mapping):
            raise VisionResponseInvalid()
        return value

    def _configured(self) -> bool:
        parsed = urlparse(self._base_url)
        return bool(
            self._base_url
            and parsed.scheme in {"http", "https"}
            and parsed.netloc
            and self._model
            and self._api_key
        )


def _validated_analysis_windows(
    panel: Mapping[str, Any],
) -> tuple[str | None, tuple[int, int] | None, tuple[dict[str, Any], ...]]:
    version = panel.get("analysis_window_version")
    source_size = panel.get("analysis_window_source_size")
    raw_windows = panel.get("analysis_windows")
    supplied = version is not None or source_size is not None or raw_windows is not None
    if not supplied:
        return None, None, ()
    if version != ANALYSIS_WINDOW_CONTRACT_VERSION:
        raise VisionRequestInvalid()
    if (
        not isinstance(source_size, (list, tuple))
        or len(source_size) != 2
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in source_size
        )
        or not isinstance(raw_windows, (list, tuple))
        or not raw_windows
        or len(raw_windows) > ANALYSIS_WINDOW_MAX_COUNT
    ):
        raise VisionRequestInvalid()
    width, height = int(source_size[0]), int(source_size[1])
    normalized: list[dict[str, Any]] = []
    previous_end: int | None = None
    for expected_index, raw in enumerate(raw_windows):
        if not isinstance(raw, Mapping):
            raise VisionRequestInvalid()
        index = raw.get("window_index")
        y0 = raw.get("y0")
        y1 = raw.get("y1")
        overlap_above = raw.get("overlap_above")
        overlap_below = raw.get("overlap_below")
        mime_type = raw.get("mime_type")
        payload = raw.get("payload")
        if (
            index != expected_index
            or isinstance(y0, bool)
            or not isinstance(y0, int)
            or isinstance(y1, bool)
            or not isinstance(y1, int)
            or y0 < 0
            or y1 <= y0
            or y1 > height
            or isinstance(overlap_above, bool)
            or not isinstance(overlap_above, int)
            or overlap_above < 0
            or isinstance(overlap_below, bool)
            or not isinstance(overlap_below, int)
            or overlap_below < 0
            or not isinstance(mime_type, str)
            or not mime_type.lower().startswith("image/")
            or not isinstance(payload, bytes)
            or not payload
        ):
            raise VisionRequestInvalid()
        if expected_index == 0:
            if y0 != 0 or overlap_above != 0:
                raise VisionRequestInvalid()
        else:
            if previous_end is None or y0 >= previous_end:
                raise VisionRequestInvalid()
            if overlap_above != previous_end - y0:
                raise VisionRequestInvalid()
        normalized.append(
            {
                "window_index": expected_index,
                "y0": y0,
                "y1": y1,
                "overlap_above": overlap_above,
                "overlap_below": overlap_below,
                "mime_type": mime_type,
                "payload": payload,
            }
        )
        previous_end = y1
    if normalized[-1]["y1"] != height or normalized[-1]["overlap_below"] != 0:
        raise VisionRequestInvalid()
    for left, right in zip(normalized, normalized[1:], strict=False):
        if left["overlap_below"] != left["y1"] - right["y0"]:
            raise VisionRequestInvalid()
    return version, (width, height), tuple(normalized)


def _validate_request(
    request: VisionObservationRequest,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(request, VisionObservationRequest):
        raise VisionRequestInvalid()
    if not isinstance(request.analysis_run_id, str) or not request.analysis_run_id.strip():
        raise VisionRequestInvalid()
    if not isinstance(request.instruction_version, str) or not request.instruction_version.strip():
        raise VisionRequestInvalid()
    if (
        not isinstance(request.instruction_sha256, str)
        or len(request.instruction_sha256) != 64
        or any(character not in string.hexdigits for character in request.instruction_sha256)
    ):
        raise VisionRequestInvalid()
    if (
        isinstance(request.chunk_index, bool)
        or not isinstance(request.chunk_index, int)
        or request.chunk_index < 0
    ):
        raise VisionRequestInvalid()
    if not request.panels:
        raise VisionRequestInvalid()

    visual_fields_supplied = (
        request.visual_instruction_version is not None
        or request.visual_instruction_sha256 is not None
    )
    if visual_fields_supplied:
        if not isinstance(request.visual_instruction_version, str) or not isinstance(
            request.visual_instruction_sha256, str
        ):
            raise VisionRequestInvalid()
        try:
            if (
                request.visual_instruction_version
                == visual_scoring.VISUAL_EVIDENCE_REPAIR_PROMPT_VERSION
            ):
                expected_visual_version, expected_visual_sha256, _ = (
                    visual_scoring.load_visual_evidence_repair_instruction()
                )
            else:
                expected_visual_version, expected_visual_sha256, _ = (
                    visual_scoring.load_visual_evidence_instruction()
                )
        except Exception:
            raise VisionRequestInvalid() from None
        if (
            request.visual_instruction_version != expected_visual_version
            or request.visual_instruction_sha256 != expected_visual_sha256
        ):
            raise VisionRequestInvalid()

    normalized: list[dict[str, Any]] = []
    seen_panel_ids: set[str] = set()
    previous_order = -1
    for panel in request.panels:
        if not isinstance(panel, Mapping):
            raise VisionRequestInvalid()
        panel_id = panel.get("panel_id")
        source_asset_id = panel.get("source_asset_id")
        source_order = panel.get("source_order")
        mime_type = panel.get("mime_type")
        payload = panel.get("payload")
        if (
            not isinstance(panel_id, str)
            or not panel_id.strip()
            or panel_id in seen_panel_ids
            or not isinstance(source_asset_id, str)
            or not source_asset_id.strip()
            or isinstance(source_order, bool)
            or not isinstance(source_order, int)
            or source_order < 0
            or source_order <= previous_order
            or not isinstance(mime_type, str)
            or not mime_type.lower().startswith("image/")
            or not isinstance(payload, bytes)
            or not payload
        ):
            raise VisionRequestInvalid()
        window_version, window_source_size, windows = _validated_analysis_windows(panel)
        seen_panel_ids.add(panel_id)
        previous_order = source_order
        normalized_panel = {
            "panel_id": panel_id,
            "source_asset_id": source_asset_id,
            "source_order": source_order,
            "mime_type": mime_type,
            "payload": payload,
        }
        if windows:
            normalized_panel["analysis_window_version"] = window_version
            normalized_panel["analysis_window_source_size"] = window_source_size
            normalized_panel["analysis_windows"] = windows
        normalized.append(normalized_panel)
    return tuple(normalized)


def _build_payload(
    request: VisionObservationRequest,
    panels: tuple[dict[str, Any], ...],
    model: str,
    *,
    encode_image=None,
) -> dict[str, Any]:
    panel_metadata: list[dict[str, Any]] = []
    for panel in panels:
        item = {
            "panel_id": panel["panel_id"],
            "source_asset_id": panel["source_asset_id"],
            "source_order": panel["source_order"],
        }
        windows = panel.get("analysis_windows", ())
        if windows:
            item["analysis_window_version"] = panel["analysis_window_version"]
            item["analysis_window_source_size"] = list(panel["analysis_window_source_size"])
            item["analysis_windows"] = [
                {
                    "window_index": window["window_index"],
                    "y0": window["y0"],
                    "y1": window["y1"],
                    "overlap_above": window["overlap_above"],
                    "overlap_below": window["overlap_below"],
                }
                for window in windows
            ]
        panel_metadata.append(item)
    metadata = {
        "analysis_run_id": request.analysis_run_id,
        "instruction_version": request.instruction_version,
        "instruction_sha256": request.instruction_sha256,
        "chunk_index": request.chunk_index,
        "panels": panel_metadata,
    }
    has_analysis_windows = any(panel.get("analysis_windows") for panel in panels)
    window_instruction = (
        " Some canonical panels include one overview followed by overlapping detail "
        "windows. Detail windows are alternate views of the SAME panel, never new "
        "panels. Reconcile all windows into exactly one observation for that panel_id. "
        "Window y0/y1 coordinates are local to the canonical panel. Any balloon or "
        "protected-region geometry MUST be normalized to the full canonical panel "
        "dimensions in analysis_window_source_size, not to an individual window. "
        "Use the overlap to resolve objects crossing window seams and do not duplicate "
        "facts merely because they appear in two windows."
        if has_analysis_windows
        else ""
    )
    if request.visual_instruction_version is not None:
        metadata["visual_instruction_version"] = request.visual_instruction_version
        metadata["visual_instruction_sha256"] = request.visual_instruction_sha256
    if request.visual_instruction_version is not None:
        if (
            request.visual_instruction_version
            == visual_scoring.VISUAL_EVIDENCE_REPAIR_PROMPT_VERSION
        ):
            _, _, visual_prompt = visual_scoring.load_visual_evidence_repair_instruction()
        else:
            _, _, visual_prompt = visual_scoring.load_visual_evidence_instruction()
        legacy_fields = ", ".join(sorted(_REQUIRED_OBSERVATION_KEYS))
        instruction = (
            f"{visual_prompt.rstrip()}\n\n"
            "Return one JSON object with exactly one top-level key named observations. Its observations value must be an array containing the exact legacy observation fields "
            f"{legacy_fields} plus exactly one visual_evidence object per panel. "
            "Every legacy field is mandatory; return no markdown fences or commentary. "
            f"Return exactly {len(panels)} observations in the same order as Request metadata panels. Do not add, omit, or rename observation keys. "
            "Before returning, verify every list-typed legacy field is a JSON array, evidence_refs is non-empty and contains that observation panel_id, and visual_evidence panel/source identities exactly match Request metadata. "
            "visible_facts must contain at least one concise, objective fact for every panel; never return an empty visible_facts list. If there is no action or dialogue, describe only the clearly visible subject, object, expression, setting, or composition without guessing. "
            "Visual sidecar keys exactly: balloon_mask_status, balloon_regions, "
            "protected_regions, mask_confidence, evidence_source, mask_reason, "
            "panel_id, source_asset_id, source_order. "
            "Balloon region keys exactly: region_id, kind, normalized_bbox, "
            "normalized_polygon, confidence, evidence_source, mask_status. "
            "Protected region keys exactly: region_id, kind, normalized_bbox, "
            "normalized_polygon, confidence, evidence_source, required, "
            "minimum_coverage. Local reconciliation owns the evidence hash. "
            "evidence_refs must include the panel_id and be non-empty. "
            f"{window_instruction} "
            "Request metadata: "
            f"{json.dumps(metadata, sort_keys=True, separators=(',', ':'))}"
        )
    else:
        instruction = (
            "Observe every supplied image panel in the ordered manifest. Return only "
            "a structured JSON object with exactly one top-level key named observations; its observations value must be a list. "
            f"Return exactly {len(panels)} observations in the same order as Request metadata panels, with no omitted, renamed, or extra observation keys. "
            "Before returning, verify every list-typed field is a JSON array and evidence_refs is non-empty and contains that observation panel_id. Each observation must contain panel_id, "
            "visible_facts, dialogue_or_ocr, inferences, uncertainties, entities, "
            "state_changes, causal_links, and evidence_refs. visible_facts must contain at least one concise objective fact per panel and must never be empty. Do not write a recap "
            "or use file labels or list positions as evidence; never infer missing "
            f"panels.{window_instruction} Request metadata: "
            f"{json.dumps(metadata, sort_keys=True, separators=(',', ':'))}"
        )
    content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
    for panel in panels:
        content.append(
            {
                "type": "text",
                "text": f"Canonical panel {panel['panel_id']} overview.",
            }
        )
        encoded = (
            encode_image(mime_type=panel["mime_type"], payload=panel["payload"])
            if encode_image is not None
            else base64.b64encode(panel["payload"]).decode("ascii")
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{panel['mime_type']};base64,{encoded}",
                },
            }
        )
        source_size = panel.get("analysis_window_source_size")
        for window in panel.get("analysis_windows", ()):
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"Detail window for canonical panel {panel['panel_id']}: "
                        f"index={window['window_index']} y0={window['y0']} y1={window['y1']} "
                        f"panel_size={list(source_size)}. Reconcile to the same panel_id."
                    ),
                }
            )
            window_encoded = (
                encode_image(mime_type=window["mime_type"], payload=window["payload"])
                if encode_image is not None
                else base64.b64encode(window["payload"]).decode("ascii")
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{window['mime_type']};base64,{window_encoded}",
                    },
                }
            )
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 65536,
    }


def validate_visual_evidence_observation(
    observation: Mapping[str, Any],
    *,
    expected_panel_id: str,
    expected_source_asset_id: str,
    expected_source_order: int,
) -> Mapping[str, Any]:
    """Validate untrusted provider visual geometry without hashing it."""

    try:
        if (
            not isinstance(observation, Mapping)
            or not _PROVIDER_VISUAL_KEYS.issubset(observation)
            or "evidence_hash" in observation
        ):
            raise VisionResponseInvalid()
        if (
            observation.get("panel_id") != expected_panel_id
            or observation.get("source_asset_id") != expected_source_asset_id
            or observation.get("source_order") != expected_source_order
        ):
            raise VisionResponseInvalid()
        status = observation.get("balloon_mask_status")
        if status not in {"unknown", "known_empty", "known_nonempty"}:
            raise VisionResponseInvalid()
        confidence = observation.get("mask_confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise VisionResponseInvalid()
        source = observation.get("evidence_source")
        reason = observation.get("mask_reason")
        if (
            not isinstance(source, str)
            or not source.strip()
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise VisionResponseInvalid()
        if status in {"known_empty", "known_nonempty"} and _is_ocr_only_evidence_source(source):
            raise VisionResponseInvalid()

        balloon_regions = observation.get("balloon_regions")
        protected_regions = observation.get("protected_regions")
        if not isinstance(balloon_regions, list) or not isinstance(protected_regions, list):
            raise VisionResponseInvalid()
        region_ids: set[str] = set()

        def validate_region(raw: Any, *, protected: bool) -> dict[str, Any]:
            required_keys = _PROTECTED_REGION_KEYS if protected else _PROVIDER_REGION_KEYS
            if not isinstance(raw, Mapping) or not required_keys.issubset(raw):
                raise VisionResponseInvalid()
            region_id = raw.get("region_id")
            kind = raw.get("kind")
            if not isinstance(region_id, str) or not region_id.strip() or region_id in region_ids:
                raise VisionResponseInvalid()
            allowed_kinds = {
                "background",
                "subject",
                "face",
                "action",
                "effect",
                "continuity_context",
            }
            if not protected:
                allowed_kinds = {"speech_balloon"}
            if not isinstance(kind, str) or kind not in allowed_kinds:
                raise VisionResponseInvalid()
            region_ids.add(region_id)
            bbox = raw.get("normalized_bbox")
            polygon = raw.get("normalized_polygon")
            if bbox is not None and (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or any(
                    isinstance(item, bool) or not isinstance(item, (int, float)) for item in bbox
                )
                or not all(0.0 <= float(item) <= 1.0 for item in bbox)
                or bbox[2] <= bbox[0]
                or bbox[3] <= bbox[1]
            ):
                raise VisionResponseInvalid()
            if polygon is not None and not isinstance(polygon, list):
                raise VisionResponseInvalid()
            if polygon and len(polygon) < 3:
                raise VisionResponseInvalid()
            for point in polygon or []:
                if (
                    not isinstance(point, list)
                    or len(point) != 2
                    or any(
                        isinstance(item, bool) or not isinstance(item, (int, float))
                        for item in point
                    )
                    or not all(0.0 <= float(item) <= 1.0 for item in point)
                ):
                    raise VisionResponseInvalid()
            region_confidence = raw.get("confidence")
            if (
                isinstance(region_confidence, bool)
                or not isinstance(region_confidence, (int, float))
                or not 0.0 <= float(region_confidence) <= 1.0
            ):
                raise VisionResponseInvalid()
            evidence_source = raw.get("evidence_source")
            if not isinstance(evidence_source, str) or not evidence_source.strip():
                raise VisionResponseInvalid()
            if not protected and raw.get("mask_status") not in {
                "unknown",
                "known_nonempty",
            }:
                raise VisionResponseInvalid()
            if protected:
                required = raw.get("required")
                minimum_coverage = raw.get("minimum_coverage")
                if not isinstance(required, bool) or (
                    isinstance(minimum_coverage, bool)
                    or not isinstance(minimum_coverage, (int, float))
                    or not 0.0 <= float(minimum_coverage) <= 1.0
                ):
                    raise VisionResponseInvalid()
            elif raw.get("mask_status") == "known_nonempty" and bbox is None and not polygon:
                raise VisionResponseInvalid()
            return {key: raw[key] for key in required_keys}

        normalized_balloon_regions = [
            validate_region(region, protected=False) for region in balloon_regions
        ]
        normalized_protected_regions = [
            validate_region(region, protected=True) for region in protected_regions
        ]

        if status == "known_empty" and (balloon_regions or float(confidence) <= 0.0):
            raise VisionResponseInvalid()
        if status == "unknown" and (
            balloon_regions
            or not any(
                marker in source.lower() for marker in ("unavailable", "insufficient", "unknown")
            )
        ):
            raise VisionResponseInvalid()
        if status == "known_nonempty":
            if not balloon_regions or any(
                region.get("mask_status") != "known_nonempty" for region in balloon_regions
            ):
                raise VisionResponseInvalid()
            if any(
                _is_ocr_only_evidence_source(region.get("evidence_source"))
                for region in balloon_regions
            ):
                raise VisionResponseInvalid()
        normalized = {key: observation[key] for key in sorted(_PROVIDER_VISUAL_KEYS)}
        normalized["balloon_regions"] = normalized_balloon_regions
        normalized["protected_regions"] = normalized_protected_regions
        return normalized
    except VisionResponseInvalid:
        raise
    except (KeyError, TypeError, ValueError):
        raise VisionResponseInvalid() from None


def normalize_dialogue_or_ocr_items(value: Any) -> list[str]:
    """Project provider dialogue/OCR variants onto the canonical string contract."""
    if not isinstance(value, list):
        raise VisionResponseInvalid()
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, Mapping) and set(item) <= {"text", "type"}:
            raw_text = item.get("text")
            raw_type = item.get("type")
            if not isinstance(raw_text, str) or (
                raw_type is not None and not isinstance(raw_type, str)
            ):
                raise VisionResponseInvalid()
            text = raw_text.strip()
        else:
            raise VisionResponseInvalid()
        if not text:
            raise VisionResponseInvalid()
        result.append(text)
    return result


def grounded_visible_facts_from_visual_evidence(value: Any) -> list[str]:
    """Derive minimal objective facts only from validated visual sidecar data."""
    if not isinstance(value, Mapping):
        raise VisionResponseInvalid()
    labels = {
        "subject": "A subject is visibly localized in the panel.",
        "face": "A face is visibly localized in the panel.",
        "action": "An action region is visibly localized in the panel.",
        "effect": "A visual effect is visibly localized in the panel.",
        "background": "A background region is visibly localized in the panel.",
        "continuity_context": "A continuity-context region is visibly localized in the panel.",
        "speech_balloon": "A speech balloon is visibly localized in the panel.",
    }
    facts: list[str] = []
    seen: set[str] = set()
    regions = list(value.get("protected_regions", [])) + list(value.get("balloon_regions", []))
    for raw in regions:
        if not isinstance(raw, Mapping):
            raise VisionResponseInvalid()
        kind = raw.get("kind")
        fact = labels.get(kind) if isinstance(kind, str) else None
        if fact and fact not in seen:
            seen.add(fact)
            facts.append(fact)
    if facts:
        return facts
    reason = value.get("mask_reason")
    if isinstance(reason, str) and reason.strip():
        return [f"Validated visual evidence note: {reason.strip()}"]
    source = value.get("evidence_source")
    if isinstance(source, str) and source.strip():
        return [f"Validated visual evidence source: {source.strip()}"]
    raise VisionResponseInvalid()


def _validate_observations(
    observations: Any,
    panels: tuple[dict[str, Any], ...],
    *,
    require_visual_evidence: bool = False,
) -> list[Mapping[str, Any]]:
    if not isinstance(observations, list):
        raise VisionResponseInvalid()

    requested_ids = [panel["panel_id"] for panel in panels]
    requested_by_panel_id = {panel["panel_id"]: panel for panel in panels}
    requested_set = set(requested_by_panel_id)
    by_panel_id: dict[str, Mapping[str, Any]] = {}
    required_observation_keys = _REQUIRED_OBSERVATION_KEYS | (
        {"visual_evidence"} if require_visual_evidence else set()
    )
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise VisionResponseInvalid()
        panel_id = observation.get("panel_id")
        if (
            not isinstance(panel_id, str)
            or panel_id not in requested_set
            or panel_id in by_panel_id
            or not required_observation_keys.issubset(observation)
            or any(
                not isinstance(observation[key], list)
                for key in _REQUIRED_OBSERVATION_KEYS
                if key != "panel_id"
            )
        ):
            raise VisionResponseInvalid()
        visible_facts = observation["visible_facts"]
        if any(not isinstance(item, str) or not item.strip() for item in visible_facts):
            raise VisionResponseInvalid()
        evidence_refs = observation["evidence_refs"]
        if (
            not evidence_refs
            or panel_id not in evidence_refs
            or any(
                not isinstance(reference, str) or reference not in requested_set
                for reference in evidence_refs
            )
        ):
            raise VisionResponseInvalid()
        row = {key: observation[key] for key in sorted(required_observation_keys)}
        row["dialogue_or_ocr"] = normalize_dialogue_or_ocr_items(row["dialogue_or_ocr"])
        for key in ("visible_facts", "inferences", "uncertainties"):
            if any(not isinstance(item, str) or not item.strip() for item in row[key]):
                raise VisionResponseInvalid()
        if require_visual_evidence:
            requested_panel = requested_by_panel_id[panel_id]
            row["visual_evidence"] = dict(
                validate_visual_evidence_observation(
                    row["visual_evidence"],
                    expected_panel_id=panel_id,
                    expected_source_asset_id=requested_panel["source_asset_id"],
                    expected_source_order=requested_panel["source_order"],
                )
            )
            if not row["visible_facts"]:
                row["visible_facts"] = grounded_visible_facts_from_visual_evidence(
                    row["visual_evidence"]
                )
        if not row["visible_facts"]:
            raise VisionResponseInvalid()
        by_panel_id[panel_id] = row

    if set(by_panel_id) != requested_set:
        raise VisionResponseInvalid()
    return [by_panel_id[panel_id] for panel_id in requested_ids]


def _valid_synthesis_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _synthesis_string_list(value: Any, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise VisionRequestInvalid()
    if not allow_empty and not value:
        raise VisionRequestInvalid()
    if any(not _valid_synthesis_text(item) for item in value):
        raise VisionRequestInvalid()
    return tuple(value)


def _validate_synthesis_observations(
    observations: tuple[Mapping[str, Any], ...],
    expected_panel_ids: tuple[str, ...],
) -> None:
    if len(observations) != len(expected_panel_ids):
        raise VisionRequestInvalid()
    expected_set = set(expected_panel_ids)
    for source_index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise VisionRequestInvalid()
        if set(observation) != _REQUIRED_SYNTHESIS_OBSERVATION_KEYS:
            raise VisionRequestInvalid()
        panel_id = observation.get("panel_id")
        if panel_id != expected_panel_ids[source_index]:
            raise VisionRequestInvalid()
        if not _valid_synthesis_text(observation.get("source_asset_id")):
            raise VisionRequestInvalid()
        if not _valid_synthesis_text(observation.get("strip_region_id")):
            raise VisionRequestInvalid()
        observed_index = observation.get("source_index")
        if (
            isinstance(observed_index, bool)
            or not isinstance(observed_index, int)
            or observed_index != source_index
        ):
            raise VisionRequestInvalid()

        bounds = observation.get("region_bounds")
        if not isinstance(bounds, Mapping) or set(bounds) != {
            "x",
            "y",
            "width",
            "height",
        }:
            raise VisionRequestInvalid()
        for coordinate in ("x", "y", "width", "height"):
            number = bounds.get(coordinate)
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise VisionRequestInvalid()
        if bounds["width"] == 0 or bounds["height"] == 0:
            raise VisionRequestInvalid()

        if not _valid_synthesis_text(observation.get("coverage_map_version")):
            raise VisionRequestInvalid()
        if not _valid_synthesis_text(observation.get("coverage_map_hash")):
            raise VisionRequestInvalid()
        for field in (
            "visible_facts",
            "dialogue_or_ocr",
            "inferences",
            "uncertainties",
        ):
            _synthesis_string_list(observation.get(field))
        evidence_refs = _synthesis_string_list(observation.get("evidence_refs"), allow_empty=False)
        if not set(evidence_refs) <= expected_set:
            raise VisionRequestInvalid()
        if panel_id not in evidence_refs:
            raise VisionRequestInvalid()


def _validate_synthesis_coverage(
    coverage_manifest: Mapping[str, Any], expected_panel_ids: tuple[str, ...]
) -> None:
    if not isinstance(coverage_manifest, Mapping):
        raise VisionRequestInvalid()
    required = {
        "total_panels",
        "processed_panels",
        "panel_ids",
        "source_content_coverage_ratio",
        "unresolved_material_area",
        "material_unresolved_regions",
        "reconciliation_complete",
    }
    if not required <= set(coverage_manifest):
        raise VisionRequestInvalid()
    if coverage_manifest["total_panels"] != len(expected_panel_ids):
        raise VisionRequestInvalid()
    if coverage_manifest["processed_panels"] != len(expected_panel_ids):
        raise VisionRequestInvalid()
    if (
        not isinstance(coverage_manifest["panel_ids"], list)
        or tuple(coverage_manifest["panel_ids"]) != expected_panel_ids
    ):
        raise VisionRequestInvalid()
    ratio = coverage_manifest["source_content_coverage_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise VisionRequestInvalid()
    if ratio != 1.0:
        raise VisionRequestInvalid()
    unresolved = coverage_manifest["unresolved_material_area"]
    if isinstance(unresolved, bool) or unresolved != 0:
        raise VisionRequestInvalid()
    if coverage_manifest["material_unresolved_regions"] != []:
        raise VisionRequestInvalid()
    if coverage_manifest["reconciliation_complete"] is not True:
        raise VisionRequestInvalid()


def _declared_overlap(
    chunk: Mapping[str, Any], field: str, expected_panel_ids: tuple[str, ...]
) -> tuple[str, ...]:
    value = chunk.get(field, [])
    declared = _synthesis_string_list(value)
    if len(set(declared)) != len(declared):
        raise VisionRequestInvalid()
    if not set(declared) <= set(expected_panel_ids):
        raise VisionRequestInvalid()
    return declared


def _validate_synthesis_chunks(
    chunks: tuple[Mapping[str, Any], ...], expected_panel_ids: tuple[str, ...]
) -> None:
    if not chunks:
        raise VisionRequestInvalid()
    expected_set = set(expected_panel_ids)
    chunk_ids: set[str] = set()
    chunk_panel_ids: list[tuple[str, ...]] = []
    flattened: list[str] = []
    seen: set[str] = set()
    positions = {panel_id: index for index, panel_id in enumerate(expected_panel_ids)}

    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise VisionRequestInvalid()
        chunk_id = chunk.get("chunk_id")
        if not _valid_synthesis_text(chunk_id) or chunk_id in chunk_ids:
            raise VisionRequestInvalid()
        chunk_ids.add(chunk_id)
        panel_ids = _synthesis_string_list(chunk.get("panel_ids"), allow_empty=False)
        if len(set(panel_ids)) != len(panel_ids):
            raise VisionRequestInvalid()
        if not set(panel_ids) <= expected_set:
            raise VisionRequestInvalid()
        if tuple(sorted(panel_ids, key=positions.__getitem__)) != panel_ids:
            raise VisionRequestInvalid()
        chunk_panel_ids.append(panel_ids)
        for panel_id in panel_ids:
            if panel_id not in seen:
                flattened.append(panel_id)
                seen.add(panel_id)

    if set(seen) != expected_set or tuple(flattened) != expected_panel_ids:
        raise VisionRequestInvalid()

    if _declared_overlap(chunks[0], "overlap_with_previous", expected_panel_ids):
        raise VisionRequestInvalid()
    if _declared_overlap(chunks[-1], "overlap_with_next", expected_panel_ids):
        raise VisionRequestInvalid()
    for index, (previous, current) in enumerate(
        zip(chunk_panel_ids, chunk_panel_ids[1:], strict=False)
    ):
        intersection = set(previous).intersection(current)
        expected_overlap = tuple(
            panel_id for panel_id in expected_panel_ids if panel_id in intersection
        )
        if not expected_overlap:
            raise VisionRequestInvalid()
        if (
            _declared_overlap(
                chunks[index],
                "overlap_with_next",
                expected_panel_ids,
            )
            != expected_overlap
            or _declared_overlap(
                chunks[index + 1],
                "overlap_with_previous",
                expected_panel_ids,
            )
            != expected_overlap
        ):
            raise VisionRequestInvalid()


def _validate_synthesis_request(
    request: VisionChapterSynthesisRequest,
) -> tuple[tuple[str, ...], Any | None]:
    if not isinstance(request, VisionChapterSynthesisRequest):
        raise VisionRequestInvalid()
    if not _valid_synthesis_text(request.analysis_run_id):
        raise VisionRequestInvalid()
    if not _valid_synthesis_text(request.instruction_version):
        raise VisionRequestInvalid()
    if not _valid_synthesis_text(request.instruction_sha256):
        raise VisionRequestInvalid()
    if not _valid_synthesis_text(request.instruction_text):
        raise VisionRequestInvalid()
    if not isinstance(request.expected_panel_ids, tuple):
        raise VisionRequestInvalid()
    expected_panel_ids = request.expected_panel_ids
    if (
        not expected_panel_ids
        or any(not _valid_synthesis_text(panel_id) for panel_id in expected_panel_ids)
        or len(set(expected_panel_ids)) != len(expected_panel_ids)
    ):
        raise VisionRequestInvalid()
    if not isinstance(request.ordered_observations, tuple):
        raise VisionRequestInvalid()
    if not isinstance(request.chunks, tuple):
        raise VisionRequestInvalid()

    profile = validate_narrative_identity(request)
    target_min = request.target_word_count_min
    target_max = request.target_word_count_max
    if (target_min is None) != (target_max is None):
        raise VisionRequestInvalid()
    if target_min is not None and (
        isinstance(target_min, bool)
        or isinstance(target_max, bool)
        or not isinstance(target_min, int)
        or not isinstance(target_max, int)
        or target_min <= 0
        or target_max < target_min
    ):
        raise VisionRequestInvalid()
    if request.retry_passages is not None:
        if not isinstance(request.retry_passages, tuple):
            raise VisionRequestInvalid()
        if profile is None:
            retry_passage_count_valid = len(request.retry_passages) == 5
        else:
            retry_passage_count_valid = (
                profile.passage_min <= len(request.retry_passages) <= profile.passage_max
            )
        if not retry_passage_count_valid:
            raise VisionRequestInvalid()
        required_passage_keys = {
            "passage_id",
            "editorial_role",
            "text",
            "claim_ids",
            "evidence_panel_ids",
        }
        for passage in request.retry_passages:
            if not isinstance(passage, Mapping) or set(passage) != required_passage_keys:
                raise VisionRequestInvalid()
            if any(
                not _valid_synthesis_text(passage.get(key))
                for key in ("passage_id", "editorial_role", "text")
            ):
                raise VisionRequestInvalid()
            _synthesis_string_list(passage.get("claim_ids"), allow_empty=False)
            _synthesis_string_list(passage.get("evidence_panel_ids"), allow_empty=False)
    preferred = request.preferred_visual_panel_ids
    preferred_by_section = request.preferred_visual_panel_ids_by_section or {}
    if (
        not isinstance(preferred, tuple)
        or any(not _valid_synthesis_text(panel_id) for panel_id in preferred)
        or len(set(preferred)) != len(preferred)
        or not set(preferred) <= set(expected_panel_ids)
        or not isinstance(preferred_by_section, Mapping)
        or set(preferred_by_section) - {"hook", "setup", "conflict", "twist", "cta"}
        or not isinstance(request.retry_visual_selection, bool)
        or not isinstance(request.retry_evidence_lineage, bool)
        or not isinstance(request.retry_dialogue_paraphrase, bool)
        or not isinstance(request.retry_claim_qualification, bool)
        or not isinstance(request.retry_claim_semantic_grounding, bool)
        or (
            request.retry_claim_semantic_diagnostics is not None
            and not isinstance(request.retry_claim_semantic_diagnostics, Mapping)
        )
        or not isinstance(request.retry_causal_arc, bool)
        or (
            request.retry_causal_diagnostics is not None
            and not isinstance(request.retry_causal_diagnostics, Mapping)
        )
        or not isinstance(request.retry_visual_story_alignment, bool)
        or (
            request.retry_visual_story_diagnostics is not None
            and not isinstance(request.retry_visual_story_diagnostics, Mapping)
        )
        or not isinstance(request.retry_projection_contract, bool)
        or not isinstance(request.retry_local_claim_grounding, bool)
        or (
            request.retry_text_only_locked_output is not None
            and not isinstance(request.retry_text_only_locked_output, Mapping)
        )
    ):
        raise VisionRequestInvalid()
    for _section, panel_ids in preferred_by_section.items():
        if (
            not isinstance(panel_ids, tuple)
            or any(not _valid_synthesis_text(panel_id) for panel_id in panel_ids)
            or len(set(panel_ids)) != len(panel_ids)
            or not set(panel_ids) <= set(preferred)
        ):
            raise VisionRequestInvalid()

    try:
        analyzer_contract = importlib.import_module("app.services.analyzer_contract")
        committed = analyzer_contract.load_analyzer_instruction(
            narrative_profile_id=profile.profile_id if profile is not None else None
        )
    except Exception:
        raise VisionRequestInvalid() from None
    if (
        not isinstance(committed, tuple)
        or len(committed) != 3
        or (request.instruction_version, request.instruction_sha256, request.instruction_text)
        != committed
    ):
        raise VisionRequestInvalid()

    _validate_synthesis_observations(request.ordered_observations, expected_panel_ids)
    _validate_synthesis_coverage(request.coverage_manifest, expected_panel_ids)
    _validate_synthesis_chunks(request.chunks, expected_panel_ids)
    return expected_panel_ids, profile


def validate_narrative_identity(
    request: VisionChapterSynthesisRequest,
) -> Any | None:
    """Validate the explicit immutable identity carried by a synthesis request."""

    values = (
        request.narrative_profile_id,
        request.narrative_profile_version,
        request.narrative_profile_sha256,
    )
    if all(value is None for value in values):
        return None
    if (
        not isinstance(request.narrative_profile_id, str)
        or not request.narrative_profile_id.strip()
        or not isinstance(request.narrative_profile_version, str)
        or not request.narrative_profile_version.strip()
        or not isinstance(request.narrative_profile_sha256, str)
        or len(request.narrative_profile_sha256) != 64
        or any(character not in string.hexdigits for character in request.narrative_profile_sha256)
    ):
        raise VisionRequestInvalid()
    try:
        narrative_identity = importlib.import_module("app.services.narrative_identity")
        profile = narrative_identity.get_narrative_identity(request.narrative_profile_id)
    except Exception:
        raise VisionRequestInvalid() from None
    if (
        request.narrative_profile_version != profile.profile_version
        or request.narrative_profile_sha256 != profile.contract_sha256
    ):
        raise VisionRequestInvalid()
    return profile


_SYNTHESIS_TOP_KEYS = frozenset(
    {
        "observations",
        "continuity_ledger",
        "evidence_graph",
        "coverage_manifest",
        "narrative_outline",
        "script_passages",
    }
)
_SYNTHESIS_REDUNDANT_CONTINUITY_KEYS = frozenset(
    {
        "entities",
        "motives",
        "state_changes",
        "causal_links",
    }
)
_SYNTHESIS_STORY_SPINE_FIELDS = frozenset(
    {
        "who_wants_what",
        "obstacle",
        "decision",
        "consequence",
        "changed_stakes",
        "unresolved_question",
    }
)


def _repair_semantic_claim_evidence_from_diagnostics(
    output: Mapping[str, Any],
    diagnostics: Mapping[str, Any] | None,
    expected_panel_ids: Sequence[str],
) -> Mapping[str, Any] | None:
    if not isinstance(diagnostics, Mapping):
        return None
    claim_id = str(diagnostics.get("claim_id", ""))
    required = diagnostics.get("required_anchor_matches")
    matched_raw = diagnostics.get("matched_claim_anchors")
    candidates_raw = diagnostics.get("candidate_panels")
    claim_anchors_raw = diagnostics.get("claim_anchors")
    if (
        not claim_id
        or isinstance(required, bool)
        or not isinstance(required, int)
        or required < 1
        or not isinstance(matched_raw, list)
        or not isinstance(candidates_raw, list)
        or not isinstance(claim_anchors_raw, list)
    ):
        return None
    expected = {str(value) for value in expected_panel_ids}
    claim_anchors = {str(value) for value in claim_anchors_raw if isinstance(value, str)}
    matched = {str(value) for value in matched_raw if isinstance(value, str)} & claim_anchors
    critical_raw = diagnostics.get("critical_claim_anchors")
    critical = (
        {str(value) for value in critical_raw if isinstance(value, str)} & claim_anchors
        if isinstance(critical_raw, list)
        else set()
    )
    span_raw = diagnostics.get("semantic_window_max_span")
    max_span = (
        span_raw
        if isinstance(span_raw, int) and not isinstance(span_raw, bool) and span_raw >= 0
        else None
    )

    cloned = json.loads(json.dumps(output))
    graph = cloned.get("evidence_graph") if isinstance(cloned, dict) else None
    claims = graph.get("claims") if isinstance(graph, dict) else None
    passages = cloned.get("script_passages") if isinstance(cloned, dict) else None
    if not isinstance(claims, list) or not isinstance(passages, list):
        return None
    target = next(
        (
            row
            for row in claims
            if isinstance(row, dict) and str(row.get("claim_id", "")) == claim_id
        ),
        None,
    )
    if target is None or not isinstance(target.get("evidence_panel_ids"), list):
        return None
    evidence = [str(value) for value in target["evidence_panel_ids"] if str(value) in expected]
    evidence_set = set(evidence)

    candidate_rows: list[dict[str, Any]] = []
    for raw in candidates_raw:
        if not isinstance(raw, Mapping):
            continue
        panel_id = str(raw.get("panel_id", ""))
        overlap_raw = raw.get("overlap_anchors")
        if panel_id not in expected or not isinstance(overlap_raw, list):
            continue
        overlap = {str(value) for value in overlap_raw if isinstance(value, str)} & claim_anchors
        order_raw = raw.get("source_order")
        order = (
            order_raw if isinstance(order_raw, int) and not isinstance(order_raw, bool) else None
        )
        candidate_rows.append({"panel_id": panel_id, "overlap": overlap, "order": order})
    if not candidate_rows:
        return None

    selected: list[str] = []
    local_rows = [row for row in candidate_rows if row["order"] is not None]
    if max_span is not None and local_rows:
        local_rows.sort(key=lambda row: (row["order"], row["panel_id"]))
        feasible: list[tuple[tuple[int, int, int, int], list[dict[str, Any]]]] = []
        for left in range(len(local_rows)):
            union: set[str] = set()
            window: list[dict[str, Any]] = []
            for right in range(left, len(local_rows)):
                if local_rows[right]["order"] - local_rows[left]["order"] > max_span:
                    break
                window.append(local_rows[right])
                union.update(local_rows[right]["overlap"])
                if len(union) >= required and critical <= union:
                    span = local_rows[right]["order"] - local_rows[left]["order"]
                    score = (span, len(window), -len(union), local_rows[left]["order"])
                    feasible.append((score, list(window)))
                    break
        if not feasible:
            return None
        _score, chosen_window = min(feasible, key=lambda item: item[0])
        covered: set[str] = set()
        for row in chosen_window:
            if row["panel_id"] in evidence_set:
                covered.update(row["overlap"])
        remaining = [row for row in chosen_window if row["panel_id"] not in evidence_set]
        while len(covered) < required or not critical <= covered:
            best_index = -1
            best_score = (-1, -1)
            missing_critical = critical - covered
            for index, row in enumerate(remaining):
                critical_gain = len(row["overlap"] & missing_critical)
                total_gain = len(row["overlap"] - covered)
                score = (critical_gain, total_gain)
                if score > best_score:
                    best_index, best_score = index, score
            if best_index < 0 or best_score == (0, 0):
                return None
            chosen = remaining.pop(best_index)
            selected.append(chosen["panel_id"])
            covered.update(chosen["overlap"])
    else:
        if len(matched) >= required:
            return None
        remaining = list(candidate_rows)
        covered = set(matched)
        while len(covered) < required:
            best_index = -1
            best_gain = 0
            for index, row in enumerate(remaining):
                gain = len(row["overlap"] - covered)
                if gain > best_gain:
                    best_index, best_gain = index, gain
            if best_index < 0 or best_gain <= 0:
                return None
            chosen = remaining.pop(best_index)
            selected.append(chosen["panel_id"])
            covered.update(chosen["overlap"])

    added = [panel_id for panel_id in selected if panel_id not in evidence_set]
    if not added:
        return None
    target["evidence_panel_ids"] = evidence + added
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        passage_claim_ids = [str(value) for value in (passage.get("claim_ids") or [])]
        if claim_id not in passage_claim_ids:
            continue
        passage_evidence = [str(value) for value in (passage.get("evidence_panel_ids") or [])]
        passage["evidence_panel_ids"] = passage_evidence + [
            value for value in added if value not in passage_evidence
        ]
    return cloned


def _repair_semantic_claim_text_from_diagnostics(
    output: Mapping[str, Any],
    diagnostics: Mapping[str, Any] | None,
    expected_panel_ids: Sequence[str],
) -> Mapping[str, Any] | None:
    """Shrink a non-critical claim to anchors already proven by its local evidence."""
    if not isinstance(diagnostics, Mapping):
        return None
    claim_id = str(diagnostics.get("claim_id", ""))
    matched_raw = diagnostics.get("matched_claim_anchors")
    critical_raw = diagnostics.get("missing_critical_anchors")
    claim_anchors_raw = diagnostics.get("claim_anchors")
    if (
        not claim_id
        or not isinstance(matched_raw, list)
        or not isinstance(claim_anchors_raw, list)
        or (isinstance(critical_raw, list) and critical_raw)
    ):
        return None
    claim_anchors = {str(value) for value in claim_anchors_raw if isinstance(value, str)}
    matched = sorted(
        {str(value) for value in matched_raw if isinstance(value, str)} & claim_anchors
    )
    if not matched:
        return None

    expected = {str(value) for value in expected_panel_ids}
    cloned = json.loads(json.dumps(output))
    graph = cloned.get("evidence_graph") if isinstance(cloned, dict) else None
    claims = graph.get("claims") if isinstance(graph, dict) else None
    passages = cloned.get("script_passages") if isinstance(cloned, dict) else None
    if not isinstance(claims, list) or not isinstance(passages, list):
        return None
    target = next(
        (
            row
            for row in claims
            if isinstance(row, dict) and str(row.get("claim_id", "")) == claim_id
        ),
        None,
    )
    if target is None or not isinstance(target.get("evidence_panel_ids"), list):
        return None
    evidence = [str(value) for value in target["evidence_panel_ids"] if str(value) in expected]
    if not evidence:
        return None

    original_text = str(target.get("text", "")).strip()
    replacement = " ".join(matched).strip()
    if not replacement or replacement.casefold() == original_text.casefold():
        return None
    target["text"] = replacement
    target["qualification"] = "Atomic evidence key grounded in the cited local observation."
    return cloned


def _repair_semantic_claim_from_candidate_excerpt(
    output: Mapping[str, Any],
    diagnostics: Mapping[str, Any] | None,
    expected_panel_ids: Sequence[str],
) -> Mapping[str, Any] | None:
    """Replace one unsupported sole-claim beat with an exact local evidence excerpt."""
    if not isinstance(diagnostics, Mapping):
        return None
    claim_id = str(diagnostics.get("claim_id", "")).strip()
    claim_anchors_raw = diagnostics.get("claim_anchors")
    candidates_raw = diagnostics.get("candidate_panels")
    if (
        not claim_id
        or not isinstance(claim_anchors_raw, list)
        or not isinstance(candidates_raw, list)
    ):
        return None

    claim_anchors = {
        str(value) for value in claim_anchors_raw if isinstance(value, str) and value
    }
    if not claim_anchors:
        return None
    expected = {str(value) for value in expected_panel_ids}
    cloned = json.loads(json.dumps(output))
    graph = cloned.get("evidence_graph") if isinstance(cloned, dict) else None
    claims = graph.get("claims") if isinstance(graph, dict) else None
    passages = cloned.get("script_passages") if isinstance(cloned, dict) else None
    if not isinstance(claims, list) or not isinstance(passages, list):
        return None
    target = next(
        (
            row
            for row in claims
            if isinstance(row, dict) and str(row.get("claim_id", "")) == claim_id
        ),
        None,
    )
    if target is None:
        return None

    referencing_passages: list[dict[str, Any]] = []
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        passage_claim_ids = [str(value) for value in (passage.get("claim_ids") or [])]
        if claim_id not in passage_claim_ids:
            continue
        if passage_claim_ids != [claim_id]:
            return None
        referencing_passages.append(passage)
    if not referencing_passages:
        return None

    try:
        analyzer_contract = importlib.import_module("app.services.analyzer_contract")
        semantic_tokens = analyzer_contract._semantic_anchor_tokens
        semantic_overlap = analyzer_contract._semantic_anchor_overlap
    except Exception:
        return None

    ranked: list[tuple[int, int, int, str, str]] = []
    for candidate_index, raw in enumerate(candidates_raw):
        if not isinstance(raw, Mapping):
            continue
        panel_id = str(raw.get("panel_id", ""))
        if panel_id not in expected:
            continue
        order_raw = raw.get("source_order")
        source_order = (
            order_raw
            if isinstance(order_raw, int) and not isinstance(order_raw, bool)
            else 10**9
        )
        excerpts_raw = raw.get("evidence_excerpt")
        excerpts = excerpts_raw if isinstance(excerpts_raw, list) else []
        for excerpt_index, excerpt_raw in enumerate(excerpts):
            if not isinstance(excerpt_raw, str) or not excerpt_raw.strip():
                continue
            excerpt = excerpt_raw.strip()
            overlap = semantic_overlap(claim_anchors, semantic_tokens(excerpt))
            if not overlap:
                continue
            ranked.append(
                (-len(overlap), source_order, candidate_index * 10 + excerpt_index, panel_id, excerpt)
            )
    if not ranked:
        return None
    _score, _order, _stable_index, panel_id, replacement = min(ranked)

    original_text = str(target.get("text", "")).strip()
    if replacement.casefold() == original_text.casefold():
        return None
    target["text"] = replacement
    target["qualification"] = "Direct local observation from the cited panel."
    target["evidence_panel_ids"] = [panel_id]
    for passage in referencing_passages:
        passage["text"] = replacement
        passage["evidence_panel_ids"] = [panel_id]
    return cloned


def _repair_disconnected_passage_from_diagnostics(
    output: Mapping[str, Any],
    diagnostics: Mapping[str, Any] | None,
    expected_panel_ids: Sequence[str],
) -> Mapping[str, Any] | None:
    """Swap one disconnected sole-claim passage to an existing reachable claim."""
    if not isinstance(diagnostics, Mapping):
        return None
    passage_id = str(diagnostics.get("passage_id", "")).strip()
    claim_id = str(diagnostics.get("claim_id", "")).strip()
    candidates_raw = diagnostics.get("reachable_candidate_claims")
    if not passage_id or not claim_id or not isinstance(candidates_raw, list):
        return None

    expected = {str(value) for value in expected_panel_ids}
    cloned = json.loads(json.dumps(output))
    graph = cloned.get("evidence_graph") if isinstance(cloned, dict) else None
    claims = graph.get("claims") if isinstance(graph, dict) else None
    passages = cloned.get("script_passages") if isinstance(cloned, dict) else None
    if not isinstance(claims, list) or not isinstance(passages, list):
        return None
    claim_by_id = {
        str(row.get("claim_id", "")): row
        for row in claims
        if isinstance(row, dict) and str(row.get("claim_id", ""))
    }
    target_passage = next(
        (
            row
            for row in passages
            if isinstance(row, dict) and str(row.get("passage_id", "")) == passage_id
        ),
        None,
    )
    if target_passage is None:
        return None
    if [str(value) for value in (target_passage.get("claim_ids") or [])] != [claim_id]:
        return None

    for raw in candidates_raw:
        if not isinstance(raw, Mapping):
            continue
        candidate_id = str(raw.get("claim_id", "")).strip()
        candidate = claim_by_id.get(candidate_id)
        if not candidate_id or candidate_id == claim_id or not isinstance(candidate, dict):
            continue
        actual_text = str(candidate.get("text", "")).strip()
        diagnostic_text = str(raw.get("claim_text", "")).strip()
        if not actual_text or (diagnostic_text and diagnostic_text != actual_text):
            continue
        actual_evidence = [
            str(value)
            for value in (candidate.get("evidence_panel_ids") or [])
            if str(value) in expected
        ]
        diagnostic_evidence = [
            str(value)
            for value in (raw.get("evidence_panel_ids") or [])
            if str(value) in expected
        ]
        if not actual_evidence or not diagnostic_evidence:
            continue
        if not set(diagnostic_evidence) <= set(actual_evidence):
            continue
        target_passage["claim_ids"] = [candidate_id]
        target_passage["evidence_panel_ids"] = actual_evidence
        target_passage["text"] = actual_text
        return cloned
    return None


def _repair_unsupported_must_from_diagnostics(
    output: Mapping[str, Any],
    diagnostics: Mapping[str, Any] | None,
    expected_panel_ids: Sequence[str],
) -> Mapping[str, Any] | None:
    if not isinstance(diagnostics, Mapping):
        return None
    missing = diagnostics.get("missing_critical_anchors")
    if not isinstance(missing, list) or "require" not in {str(v) for v in missing}:
        return None
    claim_id = str(diagnostics.get("claim_id", ""))
    candidates = diagnostics.get("candidate_panels")
    if not claim_id or not isinstance(candidates, list):
        return None

    expected = {str(value) for value in expected_panel_ids}
    cloned = json.loads(json.dumps(output))
    graph = cloned.get("evidence_graph") if isinstance(cloned, dict) else None
    claims = graph.get("claims") if isinstance(graph, dict) else None
    passages = cloned.get("script_passages") if isinstance(cloned, dict) else None
    if not isinstance(claims, list) or not isinstance(passages, list):
        return None
    target = next(
        (
            row
            for row in claims
            if isinstance(row, dict) and str(row.get("claim_id", "")) == claim_id
        ),
        None,
    )
    if target is None or not isinstance(target.get("text"), str):
        return None
    if re.search(r"\bmust\b", target["text"], flags=re.IGNORECASE) is None:
        return None

    claim_anchors_raw = diagnostics.get("claim_anchors")
    claim_anchors = (
        {str(value) for value in claim_anchors_raw if isinstance(value, str)}
        if isinstance(claim_anchors_raw, list)
        else set()
    )
    non_modal_anchors = claim_anchors - {"require"}
    ranked: list[tuple[int, int, str]] = []
    for index, raw in enumerate(candidates):
        if not isinstance(raw, Mapping):
            continue
        panel_id = str(raw.get("panel_id", ""))
        if panel_id not in expected:
            continue
        excerpt_raw = raw.get("evidence_excerpt")
        excerpt = (
            " ".join(str(value) for value in excerpt_raw)
            if isinstance(excerpt_raw, list)
            else str(excerpt_raw or "")
        )
        if re.search(r"\bwill\b", excerpt, flags=re.IGNORECASE) is None:
            continue
        overlap_raw = raw.get("overlap_anchors")
        overlap = (
            {str(value) for value in overlap_raw if isinstance(value, str)} & non_modal_anchors
            if isinstance(overlap_raw, list)
            else set()
        )
        if len(overlap) < 2:
            continue
        ranked.append((-len(overlap), index, panel_id))
    if not ranked:
        return None
    _score, _index, support_panel_id = min(ranked)

    for passage in passages:
        if not isinstance(passage, dict):
            continue
        passage_claim_ids = [str(value) for value in (passage.get("claim_ids") or [])]
        if claim_id not in passage_claim_ids:
            continue
        passage_text = passage.get("text")
        if (
            isinstance(passage_text, str)
            and re.search(r"\bmust\b", passage_text, flags=re.IGNORECASE)
            and passage_claim_ids != [claim_id]
        ):
            return None

    target["text"] = re.sub(r"\bmust\b", "will", target["text"], flags=re.IGNORECASE)
    target_evidence = target.get("evidence_panel_ids")
    if not isinstance(target_evidence, list):
        return None
    if support_panel_id not in target_evidence:
        target_evidence.append(support_panel_id)
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        passage_claim_ids = [str(value) for value in (passage.get("claim_ids") or [])]
        if passage_claim_ids != [claim_id]:
            continue
        if isinstance(passage.get("text"), str):
            passage["text"] = re.sub(r"\bmust\b", "will", passage["text"], flags=re.IGNORECASE)
        passage_evidence = passage.get("evidence_panel_ids")
        if isinstance(passage_evidence, list) and support_panel_id not in passage_evidence:
            passage_evidence.append(support_panel_id)
    return cloned


def _project_provider_synthesis_output(
    result: Any,
    request: VisionChapterSynthesisRequest,
) -> Mapping[str, Any]:
    """Attach caller-owned deterministic lineage to provider semantic synthesis."""

    def _top_level_error(value: Any) -> VisionResponseInvalid:
        diagnostics: dict[str, Any] = {}
        if isinstance(value, Mapping):
            keys = [str(key) for key in value]
            diagnostics = {
                "top_level_key_count": len(keys),
                "top_level_keys": sorted(keys)[:20],
                "top_level_value_types": {
                    str(key): type(item).__name__ for key, item in value.items()
                },
            }
            if len(value) == 1:
                nested = next(iter(value.values()))
                if isinstance(nested, Mapping):
                    nested_keys = [str(key) for key in nested]
                    diagnostics["nested_key_count"] = len(nested_keys)
                    diagnostics["nested_keys"] = sorted(nested_keys)[:20]
        return VisionResponseInvalid(
            validation_subtype="synthesis_projection_top_level_invalid",
            selection_diagnostics=diagnostics,
        )

    if not isinstance(result, Mapping):
        raise _top_level_error(result)
    result = dict(result)
    if "analysis_run_id" in result:
        if result["analysis_run_id"] != request.analysis_run_id:
            raise _top_level_error(result)
        result.pop("analysis_run_id")
    root_keys = set(result)
    redundant_shape = _SYNTHESIS_TOP_KEYS | _SYNTHESIS_REDUNDANT_CONTINUITY_KEYS
    if root_keys == redundant_shape:
        continuity = result.get("continuity_ledger")
        if not isinstance(continuity, Mapping) or any(
            key not in continuity or result[key] != continuity[key]
            for key in _SYNTHESIS_REDUNDANT_CONTINUITY_KEYS
        ):
            raise _top_level_error(result)
        result = {key: result[key] for key in _SYNTHESIS_TOP_KEYS}
    if set(result) != _SYNTHESIS_TOP_KEYS:
        if len(result) == 1:
            nested = next(iter(result.values()))
            if isinstance(nested, Mapping) and set(nested) == _SYNTHESIS_TOP_KEYS:
                result = nested
            else:
                raise _top_level_error(result)
        else:
            raise _top_level_error(result)
    if not isinstance(result.get("observations"), list):
        raise VisionResponseInvalid(
            validation_subtype="synthesis_projection_observations_shape_invalid"
        )
    if not isinstance(result.get("coverage_manifest"), Mapping):
        raise VisionResponseInvalid(
            validation_subtype="synthesis_projection_coverage_shape_invalid"
        )

    continuity = result.get("continuity_ledger")
    if not isinstance(continuity, Mapping):
        raise VisionResponseInvalid(
            validation_subtype="synthesis_projection_continuity_shape_invalid"
        )
    semantic_continuity_keys = ("entities", "motives", "state_changes", "causal_links")
    if any(not isinstance(continuity.get(key), list) for key in semantic_continuity_keys):
        raise VisionResponseInvalid(
            validation_subtype="synthesis_projection_continuity_arrays_invalid"
        )
    if not continuity.get("entities") or continuity.get("reconciled_after_final_chunk") is not True:
        raise VisionResponseInvalid(
            validation_subtype="synthesis_projection_continuity_identity_invalid"
        )

    graph = result.get("evidence_graph")
    claims = graph.get("claims") if isinstance(graph, Mapping) else None
    if not isinstance(claims, list) or not claims:
        raise VisionResponseInvalid(validation_subtype="synthesis_projection_claims_shape_invalid")

    outline = result.get("narrative_outline")
    if not isinstance(outline, Mapping):
        raise VisionResponseInvalid(validation_subtype="synthesis_projection_outline_shape_invalid")
    projected_outline = dict(outline)
    if request.narrative_profile_id is None and "story_spine" not in projected_outline:
        if set(projected_outline) != _SYNTHESIS_STORY_SPINE_FIELDS:
            raise VisionResponseInvalid(
                validation_subtype="synthesis_projection_legacy_outline_shape_invalid"
            )
        projected_outline = {"story_spine": projected_outline}

    passages = result.get("script_passages")
    if not isinstance(passages, list):
        raise VisionResponseInvalid(
            validation_subtype="synthesis_projection_passages_shape_invalid"
        )

    expected_set = set(request.expected_panel_ids)
    claim_evidence: dict[str, tuple[str, ...]] = {}
    for raw_claim in claims:
        if not isinstance(raw_claim, Mapping):
            raise VisionResponseInvalid(
                validation_subtype="synthesis_projection_claim_shape_invalid"
            )
        claim_id = raw_claim.get("claim_id")
        evidence = raw_claim.get("evidence_panel_ids")
        if (
            not isinstance(claim_id, str)
            or not claim_id.strip()
            or claim_id in claim_evidence
            or not isinstance(evidence, list)
            or not evidence
            or any(
                not isinstance(panel_id, str) or panel_id not in expected_set
                for panel_id in evidence
            )
        ):
            raise VisionResponseInvalid(
                validation_subtype="synthesis_projection_claim_evidence_invalid"
            )
        claim_evidence[claim_id] = tuple(evidence)

    projected_passages: list[dict[str, Any]] = []
    for raw_passage in passages:
        if not isinstance(raw_passage, Mapping):
            raise VisionResponseInvalid(
                validation_subtype="synthesis_projection_passage_shape_invalid"
            )
        passage = dict(raw_passage)
        claim_ids = passage.get("claim_ids")
        current_evidence = passage.get("evidence_panel_ids", [])
        if (
            not isinstance(claim_ids, list)
            or not claim_ids
            or any(
                not isinstance(claim_id, str) or claim_id not in claim_evidence
                for claim_id in claim_ids
            )
            or not isinstance(current_evidence, list)
            or any(
                not isinstance(panel_id, str) or panel_id not in expected_set
                for panel_id in current_evidence
            )
        ):
            raise VisionResponseInvalid(
                validation_subtype="synthesis_projection_passage_evidence_invalid"
            )
        needed = {panel_id for claim_id in claim_ids for panel_id in claim_evidence[claim_id]}
        if not needed.issubset(current_evidence):
            present = set(current_evidence)
            passage["evidence_panel_ids"] = list(current_evidence) + [
                panel_id
                for panel_id in request.expected_panel_ids
                if panel_id in needed and panel_id not in present
            ]
        projected_passages.append(passage)

    projected_entities: list[dict[str, Any]] = []
    for raw_entity in continuity["entities"]:
        if not isinstance(raw_entity, Mapping):
            raise VisionResponseInvalid(
                validation_subtype="synthesis_projection_entity_shape_invalid"
            )
        entity = dict(raw_entity)
        entity.setdefault("aliases", [])
        projected_entities.append(entity)
    projected_continuity = {
        "chunks": [dict(chunk) for chunk in request.chunks],
        "entities": projected_entities,
        "motives": list(continuity["motives"]),
        "state_changes": list(continuity["state_changes"]),
        "causal_links": list(continuity["causal_links"]),
        "reconciled_after_final_chunk": True,
    }
    return {
        "observations": [dict(observation) for observation in request.ordered_observations],
        "continuity_ledger": projected_continuity,
        "evidence_graph": {"claims": list(claims)},
        "coverage_manifest": dict(request.coverage_manifest),
        "narrative_outline": projected_outline,
        "script_passages": projected_passages,
    }


_VISUAL_SUPPORT_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "into",
        "onto",
        "then",
        "when",
        "while",
        "where",
        "what",
        "who",
        "why",
        "how",
        "his",
        "her",
        "their",
        "they",
        "them",
        "was",
        "were",
        "are",
        "has",
        "have",
        "had",
        "but",
        "not",
        "only",
    }
)
_VISUAL_SUPPORT_MORPHOLOGY = {
    "marry": "marry",
    "marries": "marry",
    "married": "marry",
    "marriage": "marry",
    "require": "require",
    "requires": "require",
    "required": "require",
    "requirement": "require",
    "requirements": "require",
    "purify": "purify",
    "purifies": "purify",
    "purified": "purify",
    "purification": "purify",
    "declare": "declare",
    "declares": "declare",
    "declared": "declare",
    "declaration": "declare",
    "heal": "heal",
    "heals": "heal",
    "healed": "heal",
    "healing": "heal",
    "exhaust": "exhaust",
    "exhausted": "exhaust",
    "exhaustion": "exhaust",
}

_VISUAL_ROLE_SECTIONS = {
    "hook": "hook",
    "setup": "setup",
    "escalation": "conflict",
    "editorial_insight": "twist",
    "payoff_open_loop": "cta",
}


def _visual_section_for_passage(
    passage: Mapping[str, Any],
    index: int,
    passage_count: int,
    request: VisionChapterSynthesisRequest,
) -> str:
    if request.narrative_profile_id == "retention_story_v1":
        if index == 0:
            return "hook"
        if index == 1:
            return "setup"
        if index == passage_count - 1:
            return "cta"
        if index == passage_count - 2 and passage_count >= 5:
            return "twist"
        return "conflict"
    return _VISUAL_ROLE_SECTIONS.get(str(passage.get("editorial_role", "")), "")


def _visual_support_tokens(value: object) -> set[str]:
    text = (
        str(value or "").casefold().translate(str.maketrans(dict.fromkeys(string.punctuation, " ")))
    )
    result: set[str] = set()
    for token in text.split():
        token = _VISUAL_SUPPORT_MORPHOLOGY.get(token, token)
        if len(token) >= 5 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) >= 5 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        token = _VISUAL_SUPPORT_MORPHOLOGY.get(token, token)
        if len(token) >= 3 and token not in _VISUAL_SUPPORT_STOPWORDS:
            result.add(token)
    return result


def _passage_relevant_visual_ids(
    passage: Mapping[str, Any],
    candidate_ids: Sequence[str],
    claim_by_id: Mapping[str, Mapping[str, Any]],
    observation_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    ordered_ids = tuple(str(value) for value in candidate_ids)
    fallback_positions = {panel_id: index for index, panel_id in enumerate(observation_by_id)}
    anchors = _visual_support_tokens(passage.get("text"))
    semantic_seed_anchors: set[str] = set()
    semantic_positions: list[int] = []
    semantic_assets: set[str] = set()
    claim_evidence_ids: set[str] = set()
    for claim_id in passage.get("claim_ids") or ():
        claim = claim_by_id.get(str(claim_id))
        if claim is None:
            continue
        claim_anchors = _visual_support_tokens(claim.get("text"))
        semantic_seed_anchors.update(claim_anchors)
        anchors.update(claim_anchors)
        anchors.update(_visual_support_tokens(claim.get("qualification")))
        for raw_id in claim.get("evidence_panel_ids") or ():
            panel_id = str(raw_id)
            claim_evidence_ids.add(panel_id)
            obs = observation_by_id.get(panel_id)
            if obs is None:
                continue
            evidence_text = " ".join(
                str(v)
                for key in ("visible_facts", "dialogue_or_ocr", "inferences")
                for v in (obs.get(key) or ())
            )
            if not (claim_anchors & _visual_support_tokens(evidence_text)):
                continue
            raw_index = obs.get("source_index")
            semantic_positions.append(
                int(raw_index)
                if isinstance(raw_index, int) and not isinstance(raw_index, bool)
                else fallback_positions.get(panel_id, 10**9)
            )
            asset_id = str(obs.get("source_asset_id", ""))
            if asset_id:
                semantic_assets.add(asset_id)

    indexed: list[tuple[int, str]] = []
    seeds: set[str] = set()
    for panel_id in ordered_ids:
        obs = observation_by_id.get(panel_id)
        if obs is None:
            continue
        raw_index = obs.get("source_index")
        source_index = (
            int(raw_index)
            if isinstance(raw_index, int) and not isinstance(raw_index, bool)
            else fallback_positions.get(panel_id, 10**9)
        )
        indexed.append((source_index, panel_id))
        support_text = " ".join(
            str(v)
            for key in ("visible_facts", "dialogue_or_ocr", "inferences")
            for v in (obs.get(key) or ())
        )
        overlap_basis = semantic_seed_anchors or anchors
        overlap_count = len(overlap_basis & _visual_support_tokens(support_text))
        local = min((abs(source_index - pos) for pos in semantic_positions), default=99) <= 4
        asset_id = str(obs.get("source_asset_id", ""))
        same_asset = bool(asset_id and asset_id in semantic_assets)
        direct = panel_id in claim_evidence_ids
        if direct or overlap_count >= 3 or local or same_asset:
            seeds.add(panel_id)
    if not seeds:
        return ()

    indexed.sort()
    seed_positions = [source_index for source_index, panel_id in indexed if panel_id in seeds]
    relevant = {
        panel_id
        for source_index, panel_id in indexed
        if panel_id in seeds
        or min((abs(source_index - seed_pos) for seed_pos in seed_positions), default=10**9) <= 12
    }
    return tuple(panel_id for panel_id in ordered_ids if panel_id in relevant)


def _retention_visual_story_alignment_missing(
    output: Mapping[str, Any], request: VisionChapterSynthesisRequest
) -> tuple[str, ...]:
    if request.narrative_profile_id != "retention_story_v1":
        return ()
    passages = output.get("script_passages") if isinstance(output, Mapping) else None
    graph = output.get("evidence_graph") if isinstance(output, Mapping) else None
    claims = graph.get("claims") if isinstance(graph, Mapping) else None
    if not isinstance(passages, list) or not isinstance(claims, list):
        return ()
    claim_by_id = {str(c.get("claim_id")): c for c in claims if isinstance(c, Mapping)}
    obs_by_id = {
        str(o.get("panel_id")): o for o in request.ordered_observations if isinstance(o, Mapping)
    }
    preferred_by_section = request.preferred_visual_panel_ids_by_section or {}
    missing: list[str] = []
    for index, passage in enumerate(passages):
        if not isinstance(passage, Mapping):
            continue
        section = _visual_section_for_passage(passage, index, len(passages), request)
        safe_ids = tuple(str(v) for v in preferred_by_section.get(section, ()))
        if not safe_ids:
            continue
        anchors = _visual_support_tokens(passage.get("text"))
        claim_evidence: set[str] = set()
        semantic_evidence_positions: list[int] = []
        semantic_evidence_assets: set[str] = set()
        for claim_id in passage.get("claim_ids") or ():
            claim = claim_by_id.get(str(claim_id))
            if claim is None:
                continue
            claim_anchors = _visual_support_tokens(claim.get("text"))
            anchors.update(claim_anchors)
            anchors.update(_visual_support_tokens(claim.get("qualification")))
            for value in claim.get("evidence_panel_ids") or ():
                panel_ref = str(value)
                claim_evidence.add(panel_ref)
                evidence_obs = obs_by_id.get(panel_ref)
                if evidence_obs is None:
                    continue
                evidence_text = " ".join(
                    str(v)
                    for key in ("visible_facts", "dialogue_or_ocr", "inferences")
                    for v in (evidence_obs.get(key) or ())
                )
                if claim_anchors & _visual_support_tokens(evidence_text):
                    raw_source_index = evidence_obs.get("source_index")
                    if isinstance(raw_source_index, int) and not isinstance(raw_source_index, bool):
                        semantic_evidence_positions.append(raw_source_index)
                    source_asset_id = str(evidence_obs.get("source_asset_id", ""))
                    if source_asset_id:
                        semantic_evidence_assets.add(source_asset_id)
        supported = False
        for panel_id in safe_ids:
            obs = obs_by_id.get(panel_id)
            if obs is None:
                continue
            support_text = " ".join(
                str(v)
                for key in ("visible_facts", "dialogue_or_ocr", "inferences")
                for v in (obs.get(key) or ())
            )
            overlap = bool(anchors & _visual_support_tokens(support_text))
            source_asset_id = str(obs.get("source_asset_id", ""))
            raw_source_index = obs.get("source_index")
            semantic_local = False
            if isinstance(raw_source_index, int) and not isinstance(raw_source_index, bool):
                semantic_local = (
                    min(
                        (abs(raw_source_index - pos) for pos in semantic_evidence_positions),
                        default=99,
                    )
                    <= 4
                )
            same_semantic_asset = bool(
                source_asset_id and source_asset_id in semantic_evidence_assets
            )
            direct_safe = panel_id in claim_evidence
            if request.retry_visual_story_alignment:
                supported = overlap or semantic_local or same_semantic_asset
            else:
                supported = direct_safe or overlap or semantic_local or same_semantic_asset
            if supported:
                break
        if not supported:
            missing.append(str(passage.get("editorial_role") or section or index))
    return tuple(missing)


def _retention_visual_story_alignment_diagnostics(
    output: Mapping[str, Any],
    request: VisionChapterSynthesisRequest,
    missing_alignment: Sequence[str],
) -> Mapping[str, Any]:
    passages = output.get("script_passages") if isinstance(output, Mapping) else None
    graph = output.get("evidence_graph") if isinstance(output, Mapping) else None
    claims = graph.get("claims") if isinstance(graph, Mapping) else None
    if not isinstance(passages, list) or not isinstance(claims, list):
        return {"missing_roles": list(missing_alignment)}
    claim_by_id = {str(c.get("claim_id")): c for c in claims if isinstance(c, Mapping)}
    obs_by_id = {
        str(o.get("panel_id")): o for o in request.ordered_observations if isinstance(o, Mapping)
    }
    positions = {panel_id: index for index, panel_id in enumerate(obs_by_id)}
    source_positions = {}
    for panel_id, obs in obs_by_id.items():
        raw_index = obs.get("source_index")
        source_positions[panel_id] = (
            int(raw_index)
            if isinstance(raw_index, int) and not isinstance(raw_index, bool)
            else positions[panel_id]
        )
    preferred_by_section = request.preferred_visual_panel_ids_by_section or {}
    rows: list[dict[str, Any]] = []
    missing_set = set(missing_alignment)
    for index, passage in enumerate(passages):
        if not isinstance(passage, Mapping):
            continue
        section = _visual_section_for_passage(passage, index, len(passages), request)
        role = str(passage.get("editorial_role") or section or index)
        if role not in missing_set:
            continue
        anchors = _visual_support_tokens(passage.get("text"))
        claim_evidence: set[str] = set()
        claim_ids = [str(value) for value in (passage.get("claim_ids") or ())]
        for claim_id in claim_ids:
            claim = claim_by_id.get(claim_id)
            if claim is None:
                continue
            anchors.update(_visual_support_tokens(claim.get("text")))
            anchors.update(_visual_support_tokens(claim.get("qualification")))
            claim_evidence.update(str(v) for v in claim.get("evidence_panel_ids") or ())
        direct_positions = [positions[v] for v in claim_evidence if v in positions]
        direct_source_positions = [
            source_positions[v] for v in claim_evidence if v in source_positions
        ]
        direct_assets = {
            str(obs_by_id[v].get("source_asset_id", "")) for v in claim_evidence if v in obs_by_id
        }
        candidates: list[dict[str, Any]] = []
        for panel_id in preferred_by_section.get(section, ()):
            panel_id = str(panel_id)
            obs = obs_by_id.get(panel_id)
            if obs is None:
                continue
            support_values = [
                str(v)
                for key in ("visible_facts", "dialogue_or_ocr", "inferences")
                for v in (obs.get(key) or ())
            ]
            overlap = sorted(anchors & _visual_support_tokens(" ".join(support_values)))
            source_asset_id = str(obs.get("source_asset_id", ""))
            same_scene = bool(
                source_asset_id
                and source_asset_id in direct_assets
                and panel_id in positions
                and min((abs(positions[panel_id] - pos) for pos in direct_positions), default=99)
                <= 4
            )
            source_index = source_positions.get(panel_id)
            chronology_distance = (
                min((abs(source_index - pos) for pos in direct_source_positions), default=10**9)
                if source_index is not None
                else 10**9
            )
            candidates.append(
                {
                    "panel_id": panel_id,
                    "source_index": source_index,
                    "chronology_distance": None
                    if chronology_distance == 10**9
                    else chronology_distance,
                    "direct_evidence": panel_id in claim_evidence,
                    "same_scene": same_scene,
                    "overlap_tokens": overlap[:16],
                    "excerpt": " | ".join(support_values)[:500],
                }
            )
        candidates.sort(
            key=lambda row: (
                not row["direct_evidence"],
                not row["same_scene"],
                -len(row["overlap_tokens"]),
                row["chronology_distance"] if row["chronology_distance"] is not None else 10**9,
                row["source_index"] if row["source_index"] is not None else 10**9,
                row["panel_id"],
            )
        )
        usable = candidates[:8]
        if request.retry_visual_story_alignment:
            replacement_required = bool(usable) and not any(
                row["direct_evidence"] and row["overlap_tokens"] for row in usable
            )
        else:
            replacement_required = bool(usable) and not any(
                row["direct_evidence"] or row["same_scene"] or row["overlap_tokens"]
                for row in usable
            )
        row = {
            "passage_id": str(passage.get("passage_id", "")),
            "editorial_role": role,
            "section": section,
            "replacement_required": replacement_required,
            "candidate_panels": usable,
        }
        if replacement_required:
            row["replacement_instruction"] = (
                "Discard the rejected beat completely. Select a candidate_panel first and build a new granular claim only from that panel's supplied excerpt."
            )
        else:
            row.update(
                {
                    "text": str(passage.get("text", ""))[:500],
                    "claim_ids": claim_ids,
                    "claim_evidence_panel_ids": sorted(claim_evidence),
                }
            )
        rows.append(row)
    return {"missing_roles": list(missing_alignment), "passages": rows}


def _complete_retry_visual_selection(
    output: Mapping[str, Any], request: VisionChapterSynthesisRequest
) -> Mapping[str, Any]:
    """Deterministically complete corrective visual support without weakening gates.

    Section-safe and generic-safe requirements are independent validator constraints.
    Corrective completion therefore fills both independently, and global uniqueness
    counts only generic preferred panels exactly as the validator does.
    """
    if not request.retry_visual_selection or not request.preferred_visual_panel_ids:
        return output
    passages = output.get("script_passages") if isinstance(output, Mapping) else None
    graph = output.get("evidence_graph") if isinstance(output, Mapping) else None
    claims = graph.get("claims") if isinstance(graph, Mapping) else None
    if not isinstance(passages, list) or not isinstance(claims, list):
        return output
    claim_by_id = {str(item.get("claim_id")): item for item in claims if isinstance(item, Mapping)}
    observation_by_id = {
        str(item.get("panel_id")): item
        for item in request.ordered_observations
        if isinstance(item, Mapping) and item.get("panel_id")
    }
    positions = {panel_id: index for index, panel_id in enumerate(observation_by_id)}
    preferred_order = tuple(str(value) for value in request.preferred_visual_panel_ids)
    preferred = set(preferred_order)
    preferred_by_section = {
        str(section): tuple(str(value) for value in panel_ids)
        for section, panel_ids in (request.preferred_visual_panel_ids_by_section or {}).items()
    }
    cloned = dict(output)
    cloned_passages = [dict(item) if isinstance(item, Mapping) else item for item in passages]
    cloned["script_passages"] = cloned_passages

    relevant_generic_by_index: dict[int, tuple[str, ...]] = {}
    relevant_section_by_index: dict[int, tuple[str, ...]] = {}
    global_relevant: set[str] = set()
    global_used: set[str] = set()
    for passage_index, passage in enumerate(cloned_passages):
        if not isinstance(passage, Mapping):
            continue
        section = _visual_section_for_passage(passage, passage_index, len(cloned_passages), request)
        relevant_generic = _passage_relevant_visual_ids(
            passage, preferred_order, claim_by_id, observation_by_id
        )
        relevant_section = _passage_relevant_visual_ids(
            passage, preferred_by_section.get(section, ()), claim_by_id, observation_by_id
        )
        relevant_generic_by_index[passage_index] = relevant_generic
        relevant_section_by_index[passage_index] = relevant_section
        global_relevant.update(relevant_generic)
        evidence = {str(value) for value in (passage.get("evidence_panel_ids") or ())}
        global_used.update(evidence & set(relevant_generic))

    def ranked_candidates(passage: Mapping[str, Any], candidate_ids: Sequence[str]) -> list[str]:
        evidence = [str(value) for value in (passage.get("evidence_panel_ids") or ())]
        evidence_set = set(evidence)
        anchors = _visual_support_tokens(passage.get("text"))
        for claim_id in passage.get("claim_ids") or ():
            claim = claim_by_id.get(str(claim_id))
            if claim is not None:
                anchors.update(_visual_support_tokens(claim.get("text")))
                anchors.update(_visual_support_tokens(claim.get("qualification")))
        grounded_positions = [positions[value] for value in evidence if value in positions]
        grounded_source_assets = {
            str(observation_by_id[value].get("source_asset_id", ""))
            for value in evidence
            if value in observation_by_id
            and str(observation_by_id[value].get("source_asset_id", ""))
        }
        rows: list[tuple[tuple[int, int, int, int, int], str]] = []
        for allow_index, panel_id in enumerate(candidate_ids):
            panel_id = str(panel_id)
            if panel_id in evidence_set:
                continue
            observation = observation_by_id.get(panel_id)
            if observation is None:
                continue
            support_text = " ".join(
                str(value)
                for key in ("visible_facts", "dialogue_or_ocr")
                for value in (observation.get(key) or ())
            )
            overlap = len(anchors & _visual_support_tokens(support_text))
            distance = min(
                (abs(positions[panel_id] - value) for value in grounded_positions), default=10**9
            )
            source_asset_id = str(observation.get("source_asset_id", ""))
            same_source_scene = bool(source_asset_id and source_asset_id in grounded_source_assets)
            rows.append(
                (
                    (
                        0 if panel_id not in global_used else 1,
                        -overlap,
                        0 if same_source_scene else 1,
                        distance,
                        allow_index,
                    ),
                    panel_id,
                )
            )
        rows.sort(key=lambda item: item[0])
        return [panel_id for _score, panel_id in rows]

    for passage_index, passage in enumerate(cloned_passages):
        if not isinstance(passage, dict):
            continue
        section = _visual_section_for_passage(passage, passage_index, len(cloned_passages), request)
        if not section:
            continue
        evidence = [str(value) for value in (passage.get("evidence_panel_ids") or ())]
        section_order = relevant_section_by_index.get(passage_index, ())
        section_safe = set(section_order)
        selected_section = {value for value in evidence if value in section_safe}
        for candidate in ranked_candidates(passage, section_order):
            if len(selected_section) >= min(4, len(section_safe)):
                break
            if candidate not in evidence:
                evidence.append(candidate)
            selected_section.add(candidate)
            if candidate in preferred:
                global_used.add(candidate)
        passage["evidence_panel_ids"] = evidence

        relevant_generic = relevant_generic_by_index.get(passage_index, ())
        relevant_generic_set = set(relevant_generic)
        selected_generic = {value for value in evidence if value in relevant_generic_set}
        generic_order = tuple(value for value in relevant_generic if value in section_safe) + tuple(
            value for value in relevant_generic if value not in section_safe
        )
        for candidate in ranked_candidates(passage, generic_order):
            if len(selected_generic) >= min(4, len(relevant_generic_set)):
                break
            if candidate not in evidence:
                evidence.append(candidate)
            selected_generic.add(candidate)
            global_used.add(candidate)
        passage["evidence_panel_ids"] = evidence

    unique_min = min(18, len(global_relevant))
    if len(global_used) < unique_min:
        for passage_index, passage in enumerate(cloned_passages):
            if not isinstance(passage, dict):
                continue
            section = _visual_section_for_passage(
                passage, passage_index, len(cloned_passages), request
            )
            section_safe = set(relevant_section_by_index.get(passage_index, ()))
            relevant_generic = relevant_generic_by_index.get(passage_index, ())
            generic_order = tuple(
                value for value in relevant_generic if value in section_safe
            ) + tuple(value for value in relevant_generic if value not in section_safe)
            evidence = [str(value) for value in (passage.get("evidence_panel_ids") or ())]
            for candidate in ranked_candidates(passage, generic_order):
                if len(global_used) >= unique_min:
                    break
                if candidate in global_used:
                    continue
                if candidate not in evidence:
                    evidence.append(candidate)
                global_used.add(candidate)
            passage["evidence_panel_ids"] = evidence
            if len(global_used) >= unique_min:
                break
    return cloned


def validate_synthesis_visual_selection(
    output: Mapping[str, Any], request: VisionChapterSynthesisRequest
) -> None:
    preferred = tuple(request.preferred_visual_panel_ids)
    if request.target_word_count_min is None or not preferred:
        return
    passages = output.get("script_passages") if isinstance(output, Mapping) else None
    if not isinstance(passages, list) or not passages:
        raise VisionResponseInvalid()
    graph = output.get("evidence_graph") if isinstance(output, Mapping) else None
    claims = graph.get("claims") if isinstance(graph, Mapping) else None
    if not isinstance(claims, list):
        raise VisionResponseInvalid()
    claim_by_id = {str(item.get("claim_id")): item for item in claims if isinstance(item, Mapping)}
    observation_by_id = {
        str(item.get("panel_id")): item
        for item in request.ordered_observations
        if isinstance(item, Mapping) and item.get("panel_id")
    }
    missing_alignment = _retention_visual_story_alignment_missing(output, request)
    if missing_alignment:
        raise VisionResponseInvalid(
            validation_subtype="retention_visual_story_alignment_missing",
            selection_diagnostics=_retention_visual_story_alignment_diagnostics(
                output, request, missing_alignment
            ),
        )
    preferred_set = set(preferred)
    locked_passages = tuple(dict(passage) for passage in passages if isinstance(passage, Mapping))
    preferred_by_section = request.preferred_visual_panel_ids_by_section or {}
    relevant_generic_by_index: dict[int, tuple[str, ...]] = {}
    relevant_section_by_index: dict[int, tuple[str, ...]] = {}
    global_relevant: set[str] = set()
    for passage_index, passage in enumerate(passages):
        if not isinstance(passage, Mapping):
            continue
        section = _visual_section_for_passage(passage, passage_index, len(passages), request)
        relevant_generic = _passage_relevant_visual_ids(
            passage, preferred, claim_by_id, observation_by_id
        )
        relevant_section = _passage_relevant_visual_ids(
            passage, preferred_by_section.get(section, ()), claim_by_id, observation_by_id
        )
        relevant_generic_by_index[passage_index] = relevant_generic
        relevant_section_by_index[passage_index] = relevant_section
        global_relevant.update(relevant_generic)
    unique_min = min(18, len(global_relevant))
    used_preferred: set[str] = set()

    def selection_diagnostics() -> dict[str, Any]:
        rows = []
        all_used: set[str] = set()
        for passage_index, item in enumerate(passages):
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("editorial_role", ""))
            passage_id = str(item.get("passage_id", ""))
            section = _visual_section_for_passage(item, passage_index, len(passages), request)
            evidence = (
                [str(value) for value in item.get("evidence_panel_ids", ())]
                if isinstance(item.get("evidence_panel_ids"), list)
                else []
            )
            claim_ids = [str(value) for value in (item.get("claim_ids") or ())]
            claim_rows: list[dict[str, Any]] = []
            claim_evidence: set[str] = set()
            for claim_id in claim_ids:
                claim = claim_by_id.get(claim_id)
                if not isinstance(claim, Mapping):
                    continue
                claim_panel_ids = [str(value) for value in (claim.get("evidence_panel_ids") or ())]
                claim_evidence.update(claim_panel_ids)
                claim_rows.append(
                    {
                        "claim_id": claim_id,
                        "text": str(claim.get("text", ""))[:500],
                        "qualification": str(claim.get("qualification", ""))[:300],
                        "evidence_panel_ids": claim_panel_ids,
                    }
                )
            section_safe_ordered = tuple(
                str(value) for value in preferred_by_section.get(section, ())
            )
            section_safe = set(section_safe_ordered)
            relevant_section = set(relevant_section_by_index.get(passage_index, ()))
            relevant_generic = set(relevant_generic_by_index.get(passage_index, ()))
            selected_section = {value for value in evidence if value in relevant_section}
            selected_preferred = {value for value in evidence if value in relevant_generic}
            all_used.update(selected_preferred)
            reference_positions: list[int] = []
            for panel_id in set(evidence) | claim_evidence:
                observation = observation_by_id.get(panel_id)
                if not isinstance(observation, Mapping):
                    continue
                raw_source_index = observation.get("source_index")
                if isinstance(raw_source_index, int) and not isinstance(raw_source_index, bool):
                    reference_positions.append(raw_source_index)
            safe_pool = section_safe_ordered or tuple(str(value) for value in preferred)
            candidate_panels: list[dict[str, Any]] = []
            for panel_id in safe_pool:
                observation = observation_by_id.get(panel_id)
                if not isinstance(observation, Mapping):
                    continue
                raw_source_index = observation.get("source_index")
                source_index = (
                    raw_source_index
                    if isinstance(raw_source_index, int) and not isinstance(raw_source_index, bool)
                    else None
                )
                chronology_distance = (
                    min(abs(source_index - value) for value in reference_positions)
                    if source_index is not None and reference_positions
                    else None
                )
                excerpt = " | ".join(
                    str(value)
                    for key in ("visible_facts", "dialogue_or_ocr", "inferences")
                    for value in (observation.get(key) or ())
                )[:500]
                candidate_panels.append(
                    {
                        "panel_id": panel_id,
                        "source_index": source_index,
                        "chronology_distance": chronology_distance,
                        "currently_relevant": (
                            panel_id in relevant_section or panel_id in relevant_generic
                        ),
                        "excerpt": excerpt,
                    }
                )
            candidate_panels.sort(
                key=lambda row: (
                    row["chronology_distance"]
                    if row["chronology_distance"] is not None
                    else 10**9,
                    row["source_index"] if row["source_index"] is not None else 10**9,
                    row["panel_id"],
                )
            )
            capacity_zero = (
                bool(preferred_by_section and section) and not relevant_section
            ) or not relevant_generic
            rows.append(
                {
                    "passage_id": passage_id,
                    "role": role,
                    "section": section,
                    "section_capacity": len(section_safe),
                    "relevant_section_capacity": len(relevant_section),
                    "relevant_generic_capacity": len(relevant_generic),
                    "capacity_zero": capacity_zero,
                    "required_section": min(4, len(relevant_section)),
                    "selected_section": len(selected_section),
                    "selected_preferred": len(selected_preferred),
                    "evidence_count": len(evidence),
                    "evidence_panel_ids": evidence,
                    "claim_ids": claim_ids,
                    "claims": claim_rows,
                    "safe_candidate_panels": candidate_panels[:8],
                }
            )
        return {
            "preferred_total": len(preferred_set),
            "relevant_total": len(global_relevant),
            "unique_min": unique_min,
            "used_preferred": len(all_used),
            "section_capacities": {str(k): len(v) for k, v in preferred_by_section.items()},
            "passages": rows,
        }

    for passage_index, passage in enumerate(passages):
        if not isinstance(passage, Mapping):
            raise VisionResponseInvalid()
        evidence = passage.get("evidence_panel_ids")
        if not isinstance(evidence, list):
            raise VisionResponseInvalid()
        section = _visual_section_for_passage(passage, passage_index, len(passages), request)
        relevant_section = set(relevant_section_by_index.get(passage_index, ()))
        if preferred_by_section and section:
            if not relevant_section:
                raise VisionResponseInvalid(
                    validation_subtype="production_visual_section_capacity_insufficient",
                    retry_passages=locked_passages,
                    selection_diagnostics=selection_diagnostics(),
                )
            required_section = min(4, len(relevant_section))
            selected_section = {panel_id for panel_id in evidence if panel_id in relevant_section}
            if len(selected_section) < required_section:
                raise VisionResponseInvalid(
                    validation_subtype="production_visual_selection_insufficient",
                    retry_passages=locked_passages,
                    selection_diagnostics=selection_diagnostics(),
                )
        relevant_generic = set(relevant_generic_by_index.get(passage_index, ()))
        if not relevant_generic:
            raise VisionResponseInvalid(
                validation_subtype="production_visual_section_capacity_insufficient",
                retry_passages=locked_passages,
                selection_diagnostics=selection_diagnostics(),
            )
        required_generic = min(4, len(relevant_generic))
        selected = {panel_id for panel_id in evidence if panel_id in relevant_generic}
        if len(selected) < required_generic:
            raise VisionResponseInvalid(
                validation_subtype="production_visual_selection_insufficient",
                retry_passages=locked_passages,
                selection_diagnostics=selection_diagnostics(),
            )
        used_preferred.update(selected)
    if len(used_preferred) < unique_min:
        raise VisionResponseInvalid(
            validation_subtype="production_visual_selection_insufficient",
            retry_passages=locked_passages,
            selection_diagnostics=selection_diagnostics(),
        )


def _preferred_visual_evidence_digest(
    request: VisionChapterSynthesisRequest,
) -> list[dict[str, Any]]:
    section_order = ("hook", "setup", "conflict", "twist", "cta")
    sections_by_panel: dict[str, list[str]] = {}
    for section in section_order:
        for panel_id in (request.preferred_visual_panel_ids_by_section or {}).get(section, ()):
            value = str(panel_id)
            sections_by_panel.setdefault(value, []).append(section)
    preferred = {str(value) for value in request.preferred_visual_panel_ids}
    rows: list[dict[str, Any]] = []
    for source_index, observation in enumerate(request.ordered_observations):
        if not isinstance(observation, Mapping):
            continue
        panel_id = str(observation.get("panel_id", "") or "")
        if not panel_id or panel_id not in preferred or panel_id not in sections_by_panel:
            continue
        raw_index = observation.get("source_order")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raw_index = observation.get("source_index", source_index)
        chronology_index = (
            int(raw_index)
            if isinstance(raw_index, int) and not isinstance(raw_index, bool)
            else source_index
        )

        def excerpts(key: str, limit: int, row: Mapping[str, Any] = observation) -> list[str]:
            values = row.get(key) or ()
            if not isinstance(values, (list, tuple)):
                return []
            return [str(value).strip()[:240] for value in values if str(value).strip()][:limit]

        rows.append(
            {
                "panel_id": panel_id,
                "source_index": chronology_index,
                "safe_sections": list(sections_by_panel[panel_id]),
                "visible_facts": excerpts("visible_facts", 2),
                "dialogue_or_ocr": excerpts("dialogue_or_ocr", 3),
            }
        )
    rows.sort(key=lambda row: (int(row["source_index"]), str(row["panel_id"])))
    return rows


def _build_synthesis_payload(
    request: VisionChapterSynthesisRequest,
    expected_panel_ids: tuple[str, ...],
    model: str,
    profile: Any | None = None,
) -> dict[str, Any]:
    ledger = {
        "analysis_run_id": request.analysis_run_id,
        "instruction_version": request.instruction_version,
        "instruction_sha256": request.instruction_sha256,
        "expected_panel_ids": list(expected_panel_ids),
        "coverage_manifest": dict(request.coverage_manifest),
        "ordered_observations": [dict(observation) for observation in request.ordered_observations],
        "chunks": [dict(chunk) for chunk in request.chunks],
        "preferred_visual_panel_ids": list(request.preferred_visual_panel_ids),
        "preferred_visual_panel_ids_by_section": {
            str(section): list(panel_ids)
            for section, panel_ids in sorted(
                (request.preferred_visual_panel_ids_by_section or {}).items()
            )
        },
        "preferred_visual_evidence": _preferred_visual_evidence_digest(request),
    }
    ledger_json = json.dumps(ledger, ensure_ascii=False, separators=(",", ":"))
    retention_profile = bool(
        profile is not None and getattr(profile, "profile_id", "") == "retention_story_v1"
    )
    target_instruction = (
        f"For this production, total narration MUST be {request.target_word_count_min}-{request.target_word_count_max} words. "
        if request.target_word_count_min is not None
        else "Keep total narration within the committed analyzer word-count contract. "
    )
    if retention_profile:
        allocation_instruction = (
            "Use four to six passages. The first hook is exactly one sentence of 8-14 whitespace-counted words; "
            "distribute the remaining words naturally around the dominant arc so the total production target is met. "
            "Do not force legacy setup/escalation/editorial_insight/payoff role quotas. "
        )
    else:
        allocation_instruction = (
            "Production passage ranges are mandatory: hook 16-18 words, setup 24-26, escalation 33-35, "
            "editorial_insight 24-26, payoff_open_loop 18-20. These ranges sum to exactly 115-125 words. "
            if request.target_word_count_min is not None
            else "Distribute words naturally across roles without exceeding any hard role limit. "
        )
    visual_selection_instruction = ""
    if request.preferred_visual_panel_ids:
        if retention_profile:
            visual_selection_instruction = (
                "For production visual coverage, evidence_panel_ids may include grounded visual-support panels beyond claim evidence. "
                "Each passage MUST include up to four semantically relevant panel IDs from preferred_visual_panel_ids (four whenever at least four are available), and across all passages "
                "use at least eighteen distinct preferred_visual_panel_ids when that many are available. These preferred panels have "
                "known balloon geometry, protected visual subjects, and at least one production-safe balloon-avoiding ROI. Select them for the exact narrated beat, not merely because they are visually attractive. "
                "Before drafting the dominant arc, inspect preferred_visual_evidence. Choose the production-safe visual anchor BEFORE writing each passage claim or narration. "
                "The hook MUST center on a preferred_visual_evidence row whose safe_sections contains hook, and setup MUST center on a row whose safe_sections contains setup. The central narrated event must be directly present in that row's visible_facts or dialogue_or_ocr. "
                "You may add non-safe claim-evidence panels afterward for factual detail, but they cannot replace the safe visual anchor. Do not make hook/setup center on a fact that exists only in ordered_observations and has no semantically matching preferred_visual_evidence row. "
                "Later passages likewise require a semantically matching safe visual anchor from preferred_visual_evidence for their mapped section. Build the story from what can actually be shown, then connect only grounded causal details. "
                "preferred_visual_panel_ids_by_section is framing metadata for downstream canonical sections; do not force legacy editorial role names merely to match those keys. Claim evidence must still be fully covered. "
            )
        else:
            visual_selection_instruction = (
                "For production visual coverage, evidence_panel_ids may include grounded visual-support panels beyond claim evidence. "
                "Each passage MUST include at least four panel IDs from preferred_visual_panel_ids, and across all five passages "
                "use at least eighteen distinct preferred_visual_panel_ids when that many are available. These preferred panels have "
                "known balloon geometry, protected visual subjects, and at least one production-safe balloon-avoiding ROI; choose only panels whose ordered observation supports the "
                "same passage meaning. When preferred_visual_panel_ids_by_section is present in the evidence ledger, each passage MUST include at least four IDs from its corresponding "
                "section allowlist (hook, setup, conflict for escalation, twist for editorial_insight, cta for payoff_open_loop). Those allowlists are already validated against the exact "
                "section-specific face, subject, balloon, blank-area, and crop-quality gates. Claim evidence must still be fully covered. "
            )
        if request.retry_visual_selection:
            visual_selection_instruction += (
                "Corrective retry: the prior response selected too few preferred visual panels. Keep narration, claims, passage IDs, "
                "roles, and grounded meaning stable, but broaden evidence_panel_ids with semantically relevant preferred panels. "
            )
    evidence_lineage_retry_instruction = ""
    if request.retry_evidence_lineage:
        evidence_lineage_retry_instruction = (
            "Corrective retry: the prior response referenced at least one panel ID outside expected_panel_ids. "
            "Regenerate every semantic structure from the same ordered evidence ledger. Every panel_id, panel_ids, "
            "from_panel_id, to_panel_id, and evidence_panel_ids value MUST be an exact member of expected_panel_ids. "
            "Do not invent, repair, substitute, or silently omit evidence; keep all claims and continuity grounded only "
            "in the supplied observations. "
        )
    claim_qualification_retry_instruction = ""
    if request.retry_claim_qualification:
        claim_qualification_retry_instruction = (
            "Corrective retry: every evidence_graph claim MUST contain qualification as a non-empty string. "
            "For a fact, briefly state the direct visible/dialogue evidence basis; for an interpretation, state the grounded uncertainty or limiting condition. "
            "Never leave qualification blank and never add facts beyond the supplied observations. "
        )
    projection_retry_instruction = ""
    if request.retry_projection_contract:
        projection_retry_instruction = (
            "Corrective retry: the previous response failed the synthesis projection contract. "
            "Return the complete required top-level structure exactly. continuity_ledger MUST preserve the supplied chunk continuity, "
            "contain nonempty entities, motives, state_changes, and causal_links arrays as supported by observations, set reconciled_after_final_chunk=true, and fill all six story_spine fields with nonempty grounded strings. "
            "Every motives[*].entity_id and state_changes[*].entity_id MUST exactly equal an entity_id declared in continuity_ledger.entities. If a motive or state change cannot be reconciled to a declared observed entity, drop that unsupported row rather than inventing a new identity. "
            "Do not drop grounded entity identity or replace the continuity ledger with a story summary. Add no unsupported facts. "
        )
    claim_semantic_retry_instruction = ""
    if request.retry_claim_semantic_grounding:
        diagnostic_text = ""
        if request.retry_claim_semantic_diagnostics:
            diagnostic_text = (
                " The rejected claim diagnostic was: "
                + json.dumps(
                    dict(request.retry_claim_semantic_diagnostics),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "."
            )
        claim_semantic_retry_instruction = (
            "Corrective retry: at least one evidence_graph claim cited panels whose supplied observations did not semantically support the claim. "
            "Rebuild the evidence_graph and script_passages from the same observation ledger. Every claim must be anchored by explicit words, visible facts, OCR/dialogue, or bounded inferences present in at least one cited evidence panel. "
            "Treat evidence_graph claim.text as an evidence key, not polished narration. On this semantic retry, rewrite each rejected claim to the SMALLEST atomic proposition whose content words are directly supported inside one permitted local evidence window. Prefer source-near predicates and nouns from candidate excerpts; remove unsupported subject labels, timing/location phrases, causal connectors, evaluative adjectives, inferred relationships, and other decorative detail. Do not substitute a stylistic synonym when the supplied evidence uses a clearer literal predicate. Natural paraphrase belongs in script_passages after the claim itself is grounded. "
            "For the rejected claim specifically, either cite observation panels that explicitly support its missing semantic anchors or DROP/REWRITE the claim; never keep the claim while citing merely adjacent context panels. If the local evidence proves only the core event, state only that core event; do not append who/when/where/why unless the same local window supports those anchors. "
            "When the diagnostic includes candidate_panels, use those excerpts as the primary repair shortlist: if the claim is preserved, choose one or more candidates whose supplied excerpt directly supports the claim rather than ignoring the shortlist or reusing rejected evidence IDs. Respect candidate source_order and semantic_window_max_span: anchors used to justify one claim MUST come from one local chronology window no wider than that span. Never borrow a rare anchor from a distant scene merely to satisfy vocabulary coverage. "
            "When the diagnostic includes forbidden_claim_ids, those claim IDs are retired after a no-progress retry. Remove every forbidden ID from evidence_graph and every script_passages claim_ids list. Do not paraphrase a forbidden claim under the same ID. Choose a permitted candidate panel/window first, then mint a new claim_id whose atomic text is directly supported by that candidate excerpt; if no candidate can support the concept, drop the concept. "
            "The diagnostic may include matched_claim_anchors, required_anchor_matches, critical_claim_anchors, and missing_critical_anchors. Before returning the repaired claim, compute the UNION of overlap_anchors only across cited candidates inside one permitted local window. That local union MUST contain at least required_anchor_matches distinct claim anchors and every critical claim anchor. If no local candidate window can supply a missing critical anchor, DROP or REWRITE that concept instead of searching farther away. "
            "Preserve modality exactly. A source statement that someone WILL do something, plans to do it, or declares it will happen does NOT prove MUST, REQUIRED, FORCED, OBLIGATED, or HAS TO. Likewise CAN or MAY does not prove MUST. Never strengthen descriptive/future wording into obligation unless an evidence panel in the same local window explicitly supplies that obligation. "
            "Keep claims atomic. If one sentence combines separate propositions such as a treatment requirement, legal trigger, relationship consequence, threat, or outcome whose direct support lives on different panels, SPLIT it into granular claims and cite the direct panels for each proposition. Do not use one partially matching panel to justify a compound claim. "
            "Do not preserve unsupported entities, motives, targets, causality, trust, resource transfer, threats, laws, relationships, or consequences from the rejected response."
            + diagnostic_text
            + " "
        )
    causal_arc_retry_instruction = ""
    if request.retry_causal_arc:
        causal_diagnostic_text = ""
        if request.retry_causal_diagnostics:
            causal_diagnostic_text = (
                " The rejected causal diagnostic was: "
                + json.dumps(
                    dict(request.retry_causal_diagnostics),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "."
            )
        causal_arc_retry_instruction = (
            "Corrective retry: the previous retention response failed the causal contract because a body claim was disconnected or a causal link moved backward in source chronology. "
            "Rebuild narrative_outline, evidence_graph, continuity_ledger semantic arrays, and script_passages from the same observations. "
            "Keep one forward causal story chain in the BODY. The first passage may be a later teaser and must never be used as a backward causal seed. The body begins at setup (passage two); from the next body passage onward, every newly introduced claim MUST be reachable through continuity_ledger.causal_links from evidence already used by an earlier BODY passage. "
            "Every continuity_ledger causal link MUST move forward in source chronology. If the hook teases a later consequence, the body must eventually reach that hook evidence; never create a hook-to-setup backward link. Preserve the grounded setup/body anchor unless evidence forces a correction, then repair downstream body passages rather than attaching a disconnected higher-stakes thread. "
            "Prefer an already-evidenced social, legal, romantic, comedic, logistical, reputational, treatment, or immediate consequence reachable from that anchor. "
            "Each downstream passage should earn its place with concrete evidence-backed progression when available. The final passage should resolve on a grounded changed fact or concrete consequence rather than generic uncertainty, without inventing novelty solely to satisfy progression. "
            "Do not preserve a disconnected villain, remote threat, prophecy, side quest, or unrelated stakes merely because it is dramatic. Add no unsupported causal link just to satisfy this rule. "
            "When the causal diagnostic identifies an offending claim_id, repair that exact downstream claim first. If reachable_candidate_claims are supplied, prefer those already-grounded candidate claim_ids and their cited evidence as the replacement shortlist; do not invent a bridge to keep the rejected claim. If forbidden_claim_ids are supplied, those claims already failed a targeted retry and MUST be removed from downstream script_passages and from the selected story chain rather than paraphrased back into it. "
            "If forward_candidate_claims are supplied, they are already-grounded claims that occur after the current body frontier but are NOT yet causally reachable. Treat them only as an ordered shortlist for rebuilding the downstream chain: prefer the earliest candidate whose observation-supported meaning can be connected by one or more explicit forward causal_links from the existing body. Never add a causal link merely because a candidate is chronologically nearby. If none can be connected from supplied evidence, drop the rejected concept and choose a different grounded chain. "
            + causal_diagnostic_text
            + " "
        )
    visual_story_retry_instruction = ""
    if request.retry_visual_story_alignment:
        visual_diagnostic_text = ""
        if request.retry_visual_story_diagnostics:
            visual_diagnostic_text = (
                " The rejected visual-story diagnostic was: "
                + json.dumps(
                    dict(request.retry_visual_story_diagnostics),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "."
            )
        visual_story_retry_instruction = (
            "Corrective retry: the selected retention arc lacks direct or equivalent production-safe visual support for one or more passages. "
            "Rebuild narrative_outline, evidence_graph, continuity_ledger semantic arrays, and script_passages from the same observations. "
            "Choose a different truthful causal arc when necessary. Every passage must have at least one semantically matching panel from its corresponding preferred_visual_panel_ids_by_section, or a near same-source scene of its direct claim evidence. "
            "After a visual-story failure, correction is safe-first: EVERY passage must attach at least one corresponding section-safe panel directly to evidence_graph evidence_panel_ids for a claim used by that passage. That same safe panel MUST itself semantically support the claim through its visible_facts, dialogue_or_ocr, or bounded inference; adding a safe panel as filler beside unrelated supporting evidence is invalid. Overlap-only, nearby-scene, or filler evidence is not enough on this retry. Build that granular claim from what the safe panel visibly shows, then use other grounded panels only for connected supporting detail. "
            "If relevant visual capacity is zero, adding filler panels cannot repair the passage. Discard/rewrite the beat from production-safe evidence: choose a safe_candidate_panel first, make its supplied excerpt the central granular claim, then rebuild only the causal links needed around that replacement. "
            "If the diagnostic marks hook or setup as missing, the previous opening arc is not production-frameable: discard that opening and reselect the whole arc from grounded safe visual evidence rather than preserving its first two beats. "
            "When the diagnostic contains candidate_panels, treat those excerpts as the primary frameable shortlist for that passage: preserve the beat only if a candidate directly supports it; otherwise rewrite/drop the beat and choose a causal beat that one of the safe candidates actually shows. "
            "If replacement_required=true for a passage, the rejected passage text, claim_ids, and central claim are INVALID for this retry: do not paraphrase or restate them. Select a candidate_panel first, make the visible event in its excerpt the central beat, cite that panel in the passage evidence, and create only a granular claim directly supported by that observation. Then connect later passages through grounded causal links. "
            "A replacement-required hook/setup must be safe-first: begin from what the selected production-safe panel visibly shows, then add only details whose cited evidence remains causally connected. Do not force treatment, law, romance, villain, or other prior themes merely because they appeared in the rejected response. "
            "Do not attach unrelated frameable panels merely to satisfy visual counts, and do not keep a story beat whose only direct evidence cannot pass the supplied production-safe visual allowlists. "
            + visual_diagnostic_text
            + " "
        )
    local_claim_retry_instruction = ""
    if request.retry_local_claim_grounding:
        local_claim_retry_instruction = (
            "Corrective retry: passage-to-claim evidence grounding is incomplete. "
            "Make claims granular to the beat. Every claim_id listed by a passage MUST have at least one of that claim's evidence_panel_ids also listed in the same passage. "
            "Across all passages that reference a claim, the union of their evidence_panel_ids MUST cover every evidence_panel_id declared on that claim; remove redundant claim evidence only when it is genuinely unnecessary. "
            "Preserve narration meaning and chronology; revise evidence_graph claims, passage claim_ids, and passage evidence_panel_ids only as needed, without inventing facts or panel IDs. "
        )
    locked_passage_instruction = ""
    if request.retry_passages is not None:
        locked_json = json.dumps(
            [dict(item) for item in request.retry_passages],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        visual_capacity_targets: list[Mapping[str, Any]] = []
        if (
            request.retry_visual_story_alignment
            and isinstance(request.retry_visual_story_diagnostics, Mapping)
        ):
            diagnostic_passages = request.retry_visual_story_diagnostics.get("passages")
            if isinstance(diagnostic_passages, list):
                visual_capacity_targets = [
                    item
                    for item in diagnostic_passages
                    if isinstance(item, Mapping) and bool(item.get("capacity_zero"))
                ]
        if visual_capacity_targets:
            target_json = json.dumps(
                [dict(item) for item in visual_capacity_targets],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            locked_passage_instruction = (
                "Production-safe capacity retry: use the previous script_passages as a targeted correction base. "
                "The diagnostic target passages have zero semantically relevant production-safe visual capacity, so adding more evidence IDs or filler panels cannot repair them. "
                "Preserve every non-target passage_id, editorial_role, text, claim_ids, evidence_panel_ids, ordering, and grounded meaning unless a direct forward causal dependency becomes invalid; if that happens, rewrite only the minimum downstream passages needed. "
                "For each target, discard its rejected central beat, choose one safe_candidate_panel first, build a granular replacement claim only from that panel's supplied excerpt, and update only the related evidence_graph claims and continuity links needed to keep chronology and causality valid. "
                f"Capacity-zero targets: {target_json}. "
                f"Previous correction-base script_passages: {locked_json}. "
            )
        elif request.retry_causal_arc:
            anchor_json = json.dumps(
                [dict(item) for item in request.retry_passages[:2]],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            locked_passage_instruction = (
                "Causal-arc retry anchor: use the first two previous passages as the grounded opening chain. "
                "Do not lock passages three onward; replace them with reachable consequences from the same chain. "
                f"Previous first-two anchor passages: {anchor_json}. "
            )
        elif request.retry_claim_semantic_grounding:
            semantic_claim_id = ""
            if isinstance(request.retry_claim_semantic_diagnostics, Mapping):
                semantic_claim_id = str(
                    request.retry_claim_semantic_diagnostics.get("claim_id", "")
                ).strip()
            locked_passage_instruction = (
                "Semantic-grounding retry: use the previous script_passages as a TARGETED correction base. "
                "Preserve every passage that does not reference the rejected claim_id exactly: keep its passage_id, editorial_role, text, claim_ids, evidence_panel_ids, ordering, and grounded meaning unchanged. "
                "For passages that reference the rejected claim_id, preserve passage_id, editorial_role, and ordering; rewrite only the minimum text, claim_ids, evidence_panel_ids, evidence_graph claim rows, and forward causal links needed to replace the unsupported claim with an atomic locally evidenced claim. "
                "Do not rewrite healthy claims or unrelated passages just to make the story more dramatic. If candidate_panels are supplied in the semantic diagnostic, use one permitted local candidate window and its excerpt as the repair source; never join anchors from distant scenes. "
                "If replacing the rejected claim invalidates a direct downstream causal dependency, rewrite only the minimum downstream passage needed; otherwise leave downstream passages untouched. "
                f"Rejected claim_id: {semantic_claim_id or 'unknown'}. "
                f"Previous correction-base script_passages: {locked_json}. "
            )
        elif request.retry_visual_selection:
            locked_passage_instruction = (
                "The previous response passed semantic/narration structure but failed production visual selection. "
                "Use these previous script_passages as a LOCKED correction base. Preserve every passage_id, editorial_role, text, "
                "claim_ids, ordering, claims, and grounded meaning exactly; change only evidence_panel_ids. Broaden evidence_panel_ids "
                "with semantically relevant IDs from the corresponding section allowlist while preserving all claim evidence. "
                f"Previous locked script_passages: {locked_json}. "
            )
        else:
            if request.retry_local_claim_grounding:
                locked_passage_instruction = (
                    "The previous narration text passed its editorial shape but its claim links were not locally grounded. "
                    "Use these previous script_passages as a LOCKED narration base. Preserve passage_id, editorial_role, text, ordering, and grounded meaning. "
                    "You MAY revise claim_ids and evidence_panel_ids and MAY split or rewrite evidence_graph claims so each passage claim has local evidence. "
                    f"Previous locked script_passages: {locked_json}. "
                )
            elif request.retry_dialogue_paraphrase:
                locked_passage_instruction = (
                    "The previous response passed semantic/evidence structure but copied or too closely mirrored source dialogue/OCR. "
                    "Use these previous script_passages as a LOCKED correction base. Preserve every passage_id, editorial_role, claim_ids, "
                    "evidence_panel_ids, ordering, claims, and grounded meaning exactly; rewrite only passage text in fresh spoken prose. "
                    "Do not quote source dialogue and do not reuse any contiguous four-or-more-word source phrase. Add no facts. "
                    f"Previous locked script_passages: {locked_json}. "
                )
            elif retention_profile:
                locked_passage_instruction = (
                    "The previous response passed semantic/evidence structure but failed a narration-length or subtitle-layout gate. "
                    "Use these previous script_passages as a LOCKED correction base. Preserve every passage_id, editorial_role, claim_ids, "
                    "evidence_panel_ids, ordering, claims, and grounded meaning exactly; change only passage text. Keep the first hook at 8-14 words in one sentence and keep the total narration within the production target. Prefer shorter ordinary words and balanced phrase lengths for the fixed two-line subtitle layout. "
                    f"Previous locked script_passages: {locked_json}. "
                )
            else:
                locked_passage_instruction = (
                    "The previous response passed semantic/evidence structure but failed a narration-length or subtitle-layout gate. "
                    "Use these previous script_passages as a LOCKED correction base. Preserve every passage_id, editorial_role, claim_ids, "
                    "evidence_panel_ids, ordering, claims, and grounded meaning exactly; change only passage text. Keep the exact production word ranges, prefer shorter ordinary words and balanced phrase lengths, and avoid long token combinations that cannot fit the fixed two-line subtitle layout. "
                    f"Previous locked script_passages: {locked_json}. "
                )
    retry_instruction = ""
    if request.retry_word_counts is not None:
        previous_total = sum(request.retry_word_counts)
        if retention_profile:
            retry_instruction = (
                f"Corrective retry: the previous passage word counts were {list(request.retry_word_counts)} for a total of {previous_total}. "
                f"Rewrite only passage text lengths as needed so total narration is {request.target_word_count_min}-{request.target_word_count_max} whitespace-separated words. "
                "Keep the same passage count, IDs, roles, claims, evidence references, ordering, and grounded meaning; keep the first hook one sentence at 8-14 words. "
            )
        else:
            retry_instruction = (
                f"Corrective retry: the previous five passage word counts were {list(request.retry_word_counts)} "
                f"for a total of {previous_total}. Rewrite only the passage text lengths as needed so the total "
                "matches this exact five-passage word-count target using whitespace-separated words: "
                "hook=17, setup=25, escalation=34, editorial_insight=25, payoff_open_loop=19 (total=120). "
                "Do not approximate these counts. Preserve the same claims, evidence references, passage IDs, roles, and grounded meaning. "
            )
    if retention_profile:
        script_contract_instruction = (
            "script_passages must contain four to six passages in chronological dominant-arc order; each passage exactly "
            "{passage_id,editorial_role,text,claim_ids,evidence_panel_ids}. editorial_role is a meaningful semantic label, not a legacy fixed vocabulary. "
            "The first passage is the hook and must be one sentence of 8-14 whitespace-counted words. "
            "Every claim_id listed by a passage must be locally grounded: that passage evidence_panel_ids must include at least one panel from that claim's evidence_panel_ids. Use granular claims for distinct beats instead of attaching one broad claim everywhere. "
            "From the third passage onward, prefer a factual delta when the evidence naturally advances the story. Do not invent a new claim merely to make passages differ; a grounded reused claim is acceptable when it is the truthful continuation or payoff. "
            "The final passage should land on a concrete grounded consequence, reveal, reversal, threat, or changed fact. Prefer a fresh factual payoff when evidence supports one, but do not invent a new claim solely for novelty; avoid generic uncertainty such as the outcome is uncertain, danger keeps growing, or equivalent filler. "
            "narrative_outline.ending_kind must be cliffhanger or consequence, and the final passage must state a grounded consequence, reveal, reversal, threat, or unresolved concrete fact without ending in a question mark. "
        )
        final_role_instruction = ""
    else:
        script_contract_instruction = (
            "script_passages must contain exactly five passages in order hook, setup, escalation, editorial_insight, payoff_open_loop; "
            "each passage exactly {passage_id,editorial_role,text,claim_ids,evidence_panel_ids}. "
            "Hard word limits: hook 8-18, setup 15-28, escalation 22-38, editorial_insight 15-30, payoff_open_loop 10-24. "
        )
        final_role_instruction = (
            "Never exceed the hard limit for any role. "
            "payoff_open_loop must end with an evidence-grounded question. "
        )
    ledger_instruction = (
        f"Synthesis wire contract: {SYNTHESIS_WIRE_CONTRACT_VERSION}. "
        "Synthesize the chapter from this complete ordered evidence ledger. "
        "Return exactly the six top-level analyzer structures required by the system contract. "
        "Return those six structures directly at the JSON root; do not wrap them under analysis, result, output, data, or any other container. "
        "The caller already owns validated observations, coverage, and chunk lineage; return observations=[], "
        "coverage_manifest={}, and continuity_ledger.chunks=[]. "
        "continuity_ledger must also contain reconciled_after_final_chunk=true and semantic arrays with exact schemas. "
        "Keep entities, motives, state_changes, and causal_links only inside continuity_ledger; never duplicate those arrays at the JSON root. "
        "entities: {entity_id:string,canonical_name:string,aliases:list[string],panel_ids:nonempty list[panel_id]}; "
        "motives: {entity_id:string,text:string,evidence_panel_ids:nonempty list[panel_id]}; "
        "state_changes: {entity_id:string,from:string,to:string,evidence_panel_ids:nonempty list[panel_id]}; "
        "causal_links: {from_panel_id:panel_id,to_panel_id:panel_id,reason:string,evidence_panel_ids:nonempty list[panel_id]}. "
        "Use aliases=[] when no alias is evidenced; never omit panel_ids or evidence_panel_ids. "
        "evidence_graph must be {claims:[...]} and every claim exactly "
        "{claim_id:string,claim_type:'fact'|'interpretation',text:string,qualification:nonempty string,evidence_panel_ids:nonempty list[panel_id]}. "
        "narrative_outline.story_spine must contain exactly who_wants_what, obstacle, decision, consequence, changed_stakes, unresolved_question. "
        + script_contract_instruction
        + target_instruction
        + allocation_instruction
        + locked_passage_instruction
        + retry_instruction
        + evidence_lineage_retry_instruction
        + claim_qualification_retry_instruction
        + claim_semantic_retry_instruction
        + causal_arc_retry_instruction
        + visual_story_retry_instruction
        + projection_retry_instruction
        + local_claim_retry_instruction
        + visual_selection_instruction
        + final_role_instruction
        + "Every semantic evidence reference must use only panel IDs from expected_panel_ids, every claim_id referenced by a passage must exist, "
        "and passage evidence must cover every referenced claim. Never invent, repair, or omit semantic evidence.\n"
        f"{ledger_json}"
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": request.instruction_text},
            {"role": "user", "content": ledger_instruction},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 65536,
    }
    if profile is not None:
        payload["narrative_identity"] = {
            "profile_id": profile.profile_id,
            "version": profile.profile_version,
            "sha256": profile.contract_sha256,
        }
    return payload
