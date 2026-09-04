from __future__ import annotations

import base64
import hashlib
import importlib
import json
import string
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.services import visual_scoring

VISION_REQUEST_TIMEOUT = 600.0
SYNTHESIS_WIRE_CONTRACT_VERSION = "vision-synthesis-wire-v10"
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
_OCR_ONLY_EVIDENCE_SOURCES = frozenset(
    {"ocr_text_only", "text_only_ocr", "ocr_only"}
)
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
            and kind.strip().lower().replace("-", "_").replace(" ", "_")
            in _BALLOON_KIND_ALIASES
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
    retry_passages: tuple[Mapping[str, Any], ...] | None = None


class VisionObservationProvider(Protocol):
    def capability(self) -> VisionCapabilityReport:
        ...

    def observe(
        self, request: VisionObservationRequest
    ) -> list[Mapping[str, Any]]:
        ...

    def synthesize(
        self, request: VisionChapterSynthesisRequest
    ) -> Mapping[str, Any]:
        ...


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
    ) -> None:
        super().__init__(message)
        self.retryable = False
        self.status_code: int | None = None
        self.retry_after_s: float | None = None
        self.validation_subtype = validation_subtype
        self.passage_word_counts = passage_word_counts
        self.retry_passages = retry_passages


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
    if not isinstance(passages, list) or len(passages) != 5 or any(not isinstance(item, Mapping) for item in passages):
        return None
    return tuple(dict(item) for item in passages)


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
            normalized_provider.append({"chunk_id": chunk.get("chunk_id"), "panel_ids": chunk.get("panel_ids")})
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
            blocking_reason=None
            if available
            else VisionCapabilityError.code,
        )

    def observe(
        self, request: VisionObservationRequest
    ) -> list[Mapping[str, Any]]:
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
            raise VisionProviderRequestFailed(
                retryable=True, transport_subtype="connect"
            ) from None
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
            observations = [
                _normalize_provider_visual_evidence(item)
                for item in observations
            ] if isinstance(observations, list) else observations
        return _validate_observations(
            observations,
            panels,
            require_visual_evidence=request.visual_instruction_version is not None,
        )

    def synthesize(
        self, request: VisionChapterSynthesisRequest
    ) -> Mapping[str, Any]:
        try:
            expected_panel_ids, profile = _validate_synthesis_request(request)
        except VisionRequestInvalid:
            raise
        except Exception:
            raise VisionRequestInvalid() from None

        report = self.capability()
        if not report.available:
            raise VisionCapabilityError()

        payload = _build_synthesis_payload(
            request, expected_panel_ids, self._model, profile
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
            raise VisionProviderRequestFailed(
                retryable=True, transport_subtype="connect"
            ) from None
        except Exception:
            raise VisionProviderRequestFailed(retryable=False) from None

        try:
            provider_payload = response.json()
            content = provider_payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            result = _decode_json_content(content)
            result = _reconcile_synthesis_echo_fields(result, request)
        except VisionResponseInvalid:
            raise
        except Exception:
            raise VisionResponseInvalid() from None

        validation_candidate = result
        try:
            analyzer_contract = importlib.import_module(
                "app.services.analyzer_contract"
            )
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
                    )
                validated_result = projected
        except analyzer_contract.AnalyzerContractError as exc:
            counts: tuple[int, ...] | None = None
            passages = validation_candidate.get("script_passages") if isinstance(validation_candidate, Mapping) else None
            if isinstance(passages, list):
                values: list[int] = []
                for passage in passages:
                    if not isinstance(passage, Mapping) or not isinstance(passage.get("text"), str):
                        values = []
                        break
                    values.append(len(passage["text"].split()))
                if values:
                    counts = tuple(values)
            raise VisionResponseInvalid(
                validation_subtype=_safe_validation_subtype(str(exc)),
                passage_word_counts=counts,
                retry_passages=_retry_passages_from_candidate(validation_candidate),
            ) from None
        except Exception:
            raise VisionResponseInvalid() from None
        if not isinstance(validated_result, Mapping):
            raise VisionResponseInvalid()
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
            if not request.target_word_count_min <= total <= int(request.target_word_count_max or 0):
                raise VisionResponseInvalid(
                    validation_subtype="production_narration_word_count_out_of_range",
                    passage_word_counts=counts,
                    retry_passages=_retry_passages_from_candidate(validated_result),
                )
        if request.retry_visual_selection:
            validated_result = _complete_retry_visual_selection(validated_result, request)
        try:
            validate_synthesis_visual_selection(validated_result, request)
        except VisionResponseInvalid as exc:
            raise VisionResponseInvalid(
                validation_subtype=exc.validation_subtype,
                passage_word_counts=exc.passage_word_counts,
                retry_passages=_retry_passages_from_candidate(validated_result),
            ) from None
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

        if not isinstance(stage, str) or not stage.strip() or not isinstance(prompt_version, str) or not prompt_version.strip() or not isinstance(prompt_sha256, str) or len(prompt_sha256) != 64 or not isinstance(prompt_text, str):
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
                {"role": "user", "content": json.dumps(payload, sort_keys=True, separators=(",", ":"))},
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
            raise VisionProviderRequestFailed(
                retryable=True, transport_subtype="connect"
            ) from None
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
            raise VisionProviderRequestFailed(
                retryable=True, transport_subtype="connect"
            ) from None
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
        or not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in source_size)
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
    if (
        not isinstance(request.analysis_run_id, str)
        or not request.analysis_run_id.strip()
    ):
        raise VisionRequestInvalid()
    if (
        not isinstance(request.instruction_version, str)
        or not request.instruction_version.strip()
    ):
        raise VisionRequestInvalid()
    if (
        not isinstance(request.instruction_sha256, str)
        or len(request.instruction_sha256) != 64
        or any(
            character not in string.hexdigits
            for character in request.instruction_sha256
        )
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
            "a structured JSON object with exactly one top-level key named observations; its observations value must be a list. Each observation must contain panel_id, "
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
        if not isinstance(source, str) or not source.strip() or not isinstance(reason, str) or not reason.strip():
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
            allowed_kinds = {"background", "subject", "face", "action", "effect", "continuity_context"}
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
                or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in bbox)
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
                    or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in point)
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

        if status == "known_empty" and (
            balloon_regions or float(confidence) <= 0.0
        ):
            raise VisionResponseInvalid()
        if status == "unknown" and (
            balloon_regions
            or not any(
                marker in source.lower()
                for marker in ("unavailable", "insufficient", "unknown")
            )
        ):
            raise VisionResponseInvalid()
        if status == "known_nonempty":
            if not balloon_regions or any(
                region.get("mask_status") != "known_nonempty"
                for region in balloon_regions
            ):
                raise VisionResponseInvalid()
            if any(
                _is_ocr_only_evidence_source(region.get("evidence_source"))
                for region in balloon_regions
            ):
                raise VisionResponseInvalid()
        normalized = {
            key: observation[key]
            for key in sorted(_PROVIDER_VISUAL_KEYS)
        }
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
            if not isinstance(raw_text, str) or (raw_type is not None and not isinstance(raw_type, str)):
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
                not isinstance(reference, str)
                or reference not in requested_set
                for reference in evidence_refs
            )
        ):
            raise VisionResponseInvalid()
        row = {
            key: observation[key]
            for key in sorted(required_observation_keys)
        }
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
                row["visible_facts"] = grounded_visible_facts_from_visual_evidence(row["visual_evidence"])
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
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or number < 0
            ):
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
        evidence_refs = _synthesis_string_list(
            observation.get("evidence_refs"), allow_empty=False
        )
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
        if not isinstance(request.retry_passages, tuple) or len(request.retry_passages) != 5:
            raise VisionRequestInvalid()
        required_passage_keys = {"passage_id", "editorial_role", "text", "claim_ids", "evidence_panel_ids"}
        for passage in request.retry_passages:
            if not isinstance(passage, Mapping) or set(passage) != required_passage_keys:
                raise VisionRequestInvalid()
            if any(not _valid_synthesis_text(passage.get(key)) for key in ("passage_id", "editorial_role", "text")):
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
        analyzer_contract = importlib.import_module(
            "app.services.analyzer_contract"
        )
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

    _validate_synthesis_observations(
        request.ordered_observations, expected_panel_ids
    )
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
        narrative_identity = importlib.import_module(
            "app.services.narrative_identity"
        )
        profile = narrative_identity.get_narrative_identity(
            request.narrative_profile_id
        )
    except Exception:
        raise VisionRequestInvalid() from None
    if (
        request.narrative_profile_version != profile.profile_version
        or request.narrative_profile_sha256 != profile.contract_sha256
    ):
        raise VisionRequestInvalid()
    return profile


_SYNTHESIS_TOP_KEYS = frozenset({
    "observations", "continuity_ledger", "evidence_graph",
    "coverage_manifest", "narrative_outline", "script_passages",
})
_SYNTHESIS_STORY_SPINE_FIELDS = frozenset({
    "who_wants_what", "obstacle", "decision", "consequence",
    "changed_stakes", "unresolved_question",
})


def _project_provider_synthesis_output(
    result: Any,
    request: VisionChapterSynthesisRequest,
) -> Mapping[str, Any]:
    """Attach caller-owned deterministic lineage to provider semantic synthesis."""
    if not isinstance(result, Mapping) or set(result) != _SYNTHESIS_TOP_KEYS:
        raise VisionResponseInvalid()
    if not isinstance(result.get("observations"), list):
        raise VisionResponseInvalid()
    if not isinstance(result.get("coverage_manifest"), Mapping):
        raise VisionResponseInvalid()

    continuity = result.get("continuity_ledger")
    if not isinstance(continuity, Mapping):
        raise VisionResponseInvalid()
    semantic_continuity_keys = ("entities", "motives", "state_changes", "causal_links")
    if any(not isinstance(continuity.get(key), list) for key in semantic_continuity_keys):
        raise VisionResponseInvalid()
    if not continuity.get("entities") or continuity.get("reconciled_after_final_chunk") is not True:
        raise VisionResponseInvalid()

    graph = result.get("evidence_graph")
    claims = graph.get("claims") if isinstance(graph, Mapping) else None
    if not isinstance(claims, list) or not claims:
        raise VisionResponseInvalid()

    outline = result.get("narrative_outline")
    if not isinstance(outline, Mapping):
        raise VisionResponseInvalid()
    projected_outline = dict(outline)
    if request.narrative_profile_id is None and "story_spine" not in projected_outline:
        if set(projected_outline) != _SYNTHESIS_STORY_SPINE_FIELDS:
            raise VisionResponseInvalid()
        projected_outline = {"story_spine": projected_outline}

    passages = result.get("script_passages")
    if not isinstance(passages, list):
        raise VisionResponseInvalid()

    expected_set = set(request.expected_panel_ids)
    claim_evidence: dict[str, tuple[str, ...]] = {}
    for raw_claim in claims:
        if not isinstance(raw_claim, Mapping):
            raise VisionResponseInvalid()
        claim_id = raw_claim.get("claim_id")
        evidence = raw_claim.get("evidence_panel_ids")
        if (
            not isinstance(claim_id, str)
            or not claim_id.strip()
            or claim_id in claim_evidence
            or not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(panel_id, str) or panel_id not in expected_set for panel_id in evidence)
        ):
            raise VisionResponseInvalid()
        claim_evidence[claim_id] = tuple(evidence)

    projected_passages: list[dict[str, Any]] = []
    for raw_passage in passages:
        if not isinstance(raw_passage, Mapping):
            raise VisionResponseInvalid()
        passage = dict(raw_passage)
        claim_ids = passage.get("claim_ids")
        current_evidence = passage.get("evidence_panel_ids", [])
        if (
            not isinstance(claim_ids, list)
            or not claim_ids
            or any(not isinstance(claim_id, str) or claim_id not in claim_evidence for claim_id in claim_ids)
            or not isinstance(current_evidence, list)
            or any(not isinstance(panel_id, str) or panel_id not in expected_set for panel_id in current_evidence)
        ):
            raise VisionResponseInvalid()
        needed = {panel_id for claim_id in claim_ids for panel_id in claim_evidence[claim_id]}
        if not needed.issubset(current_evidence):
            present = set(current_evidence)
            passage["evidence_panel_ids"] = list(current_evidence) + [
                panel_id for panel_id in request.expected_panel_ids
                if panel_id in needed and panel_id not in present
            ]
        projected_passages.append(passage)

    projected_entities: list[dict[str, Any]] = []
    for raw_entity in continuity["entities"]:
        if not isinstance(raw_entity, Mapping):
            raise VisionResponseInvalid()
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


_VISUAL_SUPPORT_STOPWORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "from", "into", "onto", "then",
    "when", "while", "where", "what", "who", "why", "how", "his", "her", "their",
    "they", "them", "was", "were", "are", "has", "have", "had", "but", "not", "only",
})

_VISUAL_ROLE_SECTIONS = {
    "hook": "hook",
    "setup": "setup",
    "escalation": "conflict",
    "editorial_insight": "twist",
    "payoff_open_loop": "cta",
}

def _visual_support_tokens(value: object) -> set[str]:
    text = str(value or "").casefold().translate(str.maketrans(dict.fromkeys(string.punctuation, " ")))
    return {token for token in text.split() if len(token) >= 3 and token not in _VISUAL_SUPPORT_STOPWORDS}

def _complete_retry_visual_selection(
    output: Mapping[str, Any], request: VisionChapterSynthesisRequest
) -> Mapping[str, Any]:
    """Deterministically complete corrective visual support without changing semantic claims.

    Added panels must already be exact section-safe preferred panels and must either
    share visible/dialogue tokens with the locked passage/claims or sit immediately
    beside existing grounded evidence in the ordered panel ledger. If that is not
    sufficient, the normal fail-closed validator still rejects the response.
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
    preferred = set(request.preferred_visual_panel_ids)
    preferred_by_section = request.preferred_visual_panel_ids_by_section or {}
    cloned = dict(output)
    cloned_passages = [dict(item) if isinstance(item, Mapping) else item for item in passages]
    cloned["script_passages"] = cloned_passages

    global_used = {
        str(panel_id)
        for passage in cloned_passages
        if isinstance(passage, Mapping)
        for panel_id in (passage.get("evidence_panel_ids") or ())
        if str(panel_id) in preferred
    }

    def ranked_candidates(passage: Mapping[str, Any], section: str) -> list[str]:
        evidence = [str(value) for value in (passage.get("evidence_panel_ids") or ())]
        evidence_set = set(evidence)
        anchors = _visual_support_tokens(passage.get("text"))
        for claim_id in passage.get("claim_ids") or ():
            claim = claim_by_id.get(str(claim_id))
            if claim is not None:
                anchors.update(_visual_support_tokens(claim.get("text")))
                anchors.update(_visual_support_tokens(claim.get("qualification")))
        grounded_positions = [positions[value] for value in evidence if value in positions]
        rows: list[tuple[tuple[int, int, int, int], str]] = []
        for allow_index, panel_id in enumerate(preferred_by_section.get(section, ())):
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
            distance = min((abs(positions[panel_id] - value) for value in grounded_positions), default=10**9)
            if overlap <= 0 and distance > 2:
                continue
            rows.append(((0 if panel_id not in global_used else 1, -overlap, distance, allow_index), panel_id))
        rows.sort(key=lambda item: item[0])
        return [panel_id for _score, panel_id in rows]

    for passage in cloned_passages:
        if not isinstance(passage, dict):
            continue
        section = _VISUAL_ROLE_SECTIONS.get(str(passage.get("editorial_role", "")), "")
        if not section:
            continue
        evidence = [str(value) for value in (passage.get("evidence_panel_ids") or ())]
        section_safe = set(preferred_by_section.get(section, ()))
        selected = {value for value in evidence if value in section_safe}
        for candidate in ranked_candidates(passage, section):
            if len(selected) >= min(4, len(section_safe)):
                break
            if candidate not in evidence:
                evidence.append(candidate)
            selected.add(candidate)
            global_used.add(candidate)
        passage["evidence_panel_ids"] = evidence

    unique_min = min(18, len(preferred))
    if len(global_used) < unique_min:
        for passage in cloned_passages:
            if not isinstance(passage, dict):
                continue
            section = _VISUAL_ROLE_SECTIONS.get(str(passage.get("editorial_role", "")), "")
            if not section:
                continue
            evidence = [str(value) for value in (passage.get("evidence_panel_ids") or ())]
            for candidate in ranked_candidates(passage, section):
                if len(global_used) >= unique_min:
                    break
                if candidate in global_used:
                    continue
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
    preferred_set = set(preferred)
    locked_passages = tuple(dict(passage) for passage in passages if isinstance(passage, Mapping))
    preferred_by_section = request.preferred_visual_panel_ids_by_section or {}
    role_sections = {
        "hook": "hook",
        "setup": "setup",
        "escalation": "conflict",
        "editorial_insight": "twist",
        "payoff_open_loop": "cta",
    }
    per_passage_min = min(4, len(preferred_set))
    unique_min = min(18, len(preferred_set))
    used_preferred: set[str] = set()
    for passage in passages:
        if not isinstance(passage, Mapping):
            raise VisionResponseInvalid()
        evidence = passage.get("evidence_panel_ids")
        if not isinstance(evidence, list):
            raise VisionResponseInvalid()
        editorial_role = str(passage.get("editorial_role", ""))
        section = role_sections.get(editorial_role, "")
        section_preferred = set(preferred_by_section.get(section, ()))
        if preferred_by_section and section:
            if len(section_preferred) < 4:
                raise VisionResponseInvalid(
                    validation_subtype="production_visual_section_capacity_insufficient"
                )
            selected_section = {panel_id for panel_id in evidence if panel_id in section_preferred}
            if len(selected_section) < 4:
                raise VisionResponseInvalid(
                    validation_subtype="production_visual_selection_insufficient",
                    retry_passages=locked_passages,
                )
        selected = {panel_id for panel_id in evidence if panel_id in preferred_set}
        if len(selected) < per_passage_min:
            raise VisionResponseInvalid(
                validation_subtype="production_visual_selection_insufficient",
                retry_passages=locked_passages,
            )
        used_preferred.update(selected)
    if len(used_preferred) < unique_min:
        raise VisionResponseInvalid(
            validation_subtype="production_visual_selection_insufficient",
            retry_passages=locked_passages,
        )


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
        "ordered_observations": [
            dict(observation) for observation in request.ordered_observations
        ],
        "chunks": [dict(chunk) for chunk in request.chunks],
        "preferred_visual_panel_ids": list(request.preferred_visual_panel_ids),
        "preferred_visual_panel_ids_by_section": {
            str(section): list(panel_ids)
            for section, panel_ids in sorted((request.preferred_visual_panel_ids_by_section or {}).items())
        },
    }
    ledger_json = json.dumps(
        ledger, ensure_ascii=False, separators=(",", ":")
    )
    target_instruction = (
        f"For this production, total narration MUST be {request.target_word_count_min}-{request.target_word_count_max} words. "
        if request.target_word_count_min is not None
        else "Keep total narration within the committed analyzer word-count contract. "
    )
    allocation_instruction = (
        "Production passage ranges are mandatory: hook 16-18 words, setup 24-26, escalation 33-35, "
        "editorial_insight 24-26, payoff_open_loop 18-20. These ranges sum to exactly 115-125 words. "
        if request.target_word_count_min is not None
        else "Distribute words naturally across roles without exceeding any hard role limit. "
    )
    visual_selection_instruction = ""
    if request.preferred_visual_panel_ids:
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
    locked_passage_instruction = ""
    if request.retry_passages is not None:
        locked_json = json.dumps([dict(item) for item in request.retry_passages], ensure_ascii=False, separators=(",", ":"))
        if request.retry_visual_selection:
            locked_passage_instruction = (
                "The previous response passed semantic/narration structure but failed production visual selection. "
                "Use these previous script_passages as a LOCKED correction base. Preserve every passage_id, editorial_role, text, "
                "claim_ids, ordering, claims, and grounded meaning exactly; change only evidence_panel_ids. Broaden evidence_panel_ids "
                "with semantically relevant IDs from the corresponding section allowlist while preserving all claim evidence. "
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
        retry_instruction = (
            f"Corrective retry: the previous five passage word counts were {list(request.retry_word_counts)} "
            f"for a total of {previous_total}. Rewrite only the passage text lengths as needed so the total "
            "matches this exact five-passage word-count target using whitespace-separated words: "
            "hook=17, setup=25, escalation=34, editorial_insight=25, payoff_open_loop=19 (total=120). "
            "Do not approximate these counts. Preserve the same claims, evidence references, passage IDs, roles, and grounded meaning. "
        )
    ledger_instruction = (
        f"Synthesis wire contract: {SYNTHESIS_WIRE_CONTRACT_VERSION}. "
        "Synthesize the chapter from this complete ordered evidence ledger. "
        "Return exactly the six top-level analyzer structures required by the system contract. "
        "The caller already owns validated observations, coverage, and chunk lineage; return observations=[], "
        "coverage_manifest={}, and continuity_ledger.chunks=[]. "
        "continuity_ledger must also contain reconciled_after_final_chunk=true and semantic arrays with exact schemas. "
        "entities: {entity_id:string,canonical_name:string,aliases:list[string],panel_ids:nonempty list[panel_id]}; "
        "motives: {entity_id:string,text:string,evidence_panel_ids:nonempty list[panel_id]}; "
        "state_changes: {entity_id:string,from:string,to:string,evidence_panel_ids:nonempty list[panel_id]}; "
        "causal_links: {from_panel_id:panel_id,to_panel_id:panel_id,reason:string,evidence_panel_ids:nonempty list[panel_id]}. "
        "Use aliases=[] when no alias is evidenced; never omit panel_ids or evidence_panel_ids. "
        "evidence_graph must be {claims:[...]} and every claim exactly "
        "{claim_id:string,claim_type:'fact'|'interpretation',text:string,qualification:string,evidence_panel_ids:nonempty list[panel_id]}. "
        "narrative_outline.story_spine must contain exactly who_wants_what, obstacle, decision, consequence, changed_stakes, unresolved_question. "
        "script_passages must contain exactly five passages in order hook, setup, escalation, editorial_insight, payoff_open_loop; "
        "each passage exactly {passage_id,editorial_role,text,claim_ids,evidence_panel_ids}. "
        "Hard word limits: hook 8-18, setup 15-28, escalation 22-38, editorial_insight 15-30, payoff_open_loop 10-24. "
        + target_instruction
        + allocation_instruction
        + locked_passage_instruction
        + retry_instruction
        + evidence_lineage_retry_instruction
        + visual_selection_instruction
        + "Never exceed the hard limit for any role. "
        "payoff_open_loop must end with an evidence-grounded question. "
        "Every semantic evidence reference must use only panel IDs from expected_panel_ids, every claim_id referenced by a passage must exist, "
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
