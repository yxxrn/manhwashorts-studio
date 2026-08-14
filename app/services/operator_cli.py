"""Windows-first interactive operator workflow.

The CLI is deliberately a thin terminal adapter around the existing BYOK,
ingest, cloud multimodal, and JSON job-state services. It never stores or
prints a plaintext API key and it never starts voice, audio, or publication
work.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models import Project, SourceAsset, User, Workspace

CLI_VERSION = "interactive-production-cli-v1"
SUPPORTED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_IGNORED_FOLDER_FILES = frozenset({".ds_store", "desktop.ini", "thumbs.db"})
_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_KEY_PATTERN = re.compile(r"(?:sk-|xi-|AIza)[A-Za-z0-9_-]{8,}")


class OperatorCliError(RuntimeError):
    """Safe, user-facing operator error with a stable code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = str(code)
        self.message = message.strip() or self.code
        super().__init__(f"{self.code}: {self.message}")


def _image_mime_type(path: Path) -> str:
    return _IMAGE_MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _providers():
    from app.services import providers

    return providers


def _cloud():
    from app.services import cloud_multimodal

    return cloud_multimodal


def _credentials():
    from app.services import credentials

    return credentials


def _ingest():
    from app.services import ingest

    return ingest


def _pipeline():
    from app.services import pipeline

    return pipeline


def _models():
    from app.models import Project, SourceAsset, User, Workspace

    return Project, SourceAsset, User, Workspace


def _session_scope():
    from app.db import session_scope

    return session_scope


def _settings():
    from app.config import settings

    return settings


@dataclass(frozen=True)
class EndpointConfig:
    """Validated provider base and model-list URLs without query strings."""

    base_url: str
    models_url: str


@dataclass(frozen=True)
class ModelChoice:
    model_id: str
    label: str = ""


@dataclass(frozen=True)
class ChapterFolder:
    folder: Path
    image_paths: tuple[Path, ...]


@dataclass(frozen=True)
class ChapterManifest:
    entries: tuple[tuple[str, str, int], ...]
    manifest_sha256: str


@dataclass(frozen=True)
class ImportedChapter:
    project_id: str
    created: bool
    image_count: int
    manifest_sha256: str


@dataclass(frozen=True)
class CapabilityResult:
    attempted: bool
    ok: bool
    code: str
    detail: str = ""


@dataclass(frozen=True)
class RunOptions:
    max_attempts: int = 2
    max_requests: int | None = None
    min_request_interval_s: float = 0.0
    estimated_cost_per_request: float = 0.0
    max_concurrent: int = 1


def parse_run_options(values: Mapping[str, str | int | float | None]) -> RunOptions:
    """Validate operator-visible retry, budget, pacing, and concurrency values."""

    try:
        max_attempts = int(values.get("max_attempts", 2) or 2)
        raw_requests = values.get("max_requests")
        max_requests = None if raw_requests in (None, "", 0, "0") else int(raw_requests)
        interval = float(values.get("min_request_interval_s", 0.0) or 0.0)
        cost = float(values.get("estimated_cost_per_request", 0.0) or 0.0)
        concurrent = int(values.get("max_concurrent", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise OperatorCliError("operator.request_budget_invalid", "request settings must be numeric") from exc
    if max_attempts < 1 or (max_requests is not None and max_requests < 1) or interval < 0 or cost < 0 or concurrent < 1:
        raise OperatorCliError("operator.request_budget_invalid", "request settings are outside safe bounds")
    return RunOptions(max_attempts, max_requests, interval, cost, concurrent)


@dataclass(frozen=True)
class _CapabilityPanel:
    panel_id: str
    source_asset_id: str
    source_order: int
    mime_type: str
    payload: bytes
    source_checksum: str
    payload_checksum: str
    panel_bounds: tuple[int, int, int, int]
    source_dimensions: tuple[int, int]

    def descriptor(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "source_asset_id": self.source_asset_id,
            "source_order": self.source_order,
            "mime_type": self.mime_type,
            "source_checksum": self.source_checksum,
            "payload_checksum": self.payload_checksum,
            "panel_bounds": self.panel_bounds,
            "source_dimensions": self.source_dimensions,
        }


def safe_error_text(error: BaseException, *, secret: str = "") -> str:
    """Return a short error without secrets, auth headers, or raw bodies."""

    text = str(error) or type(error).__name__
    if secret:
        text = text.replace(secret, "[redacted]")
    text = _KEY_PATTERN.sub("[redacted]", text)
    text = re.sub(r"(?i)\bauthorization\b(?:\s*[:=])?\s*(?:bearer\s+)?\S*", "[redacted]", text)
    text = re.sub(r"(?i)\bbearer\s+\S+", "[redacted]", text)
    text = re.sub(r"(?i)(?:body|payload|response)\s*[:=].*", "provider response was rejected", text)
    text = re.sub(r"[\r\n\t]+", " ", text).strip()
    return text[:300] or "provider request failed"


_LLM_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai-compatible": "openai",
    "openai_compatible": "openai",
}


def normalize_llm_provider(value: str | None) -> str:
    """Map friendly OpenAI-compatible labels to the registry's canonical key."""

    token = str(value or "").strip().casefold()
    return _LLM_PROVIDER_ALIASES.get(token, token)


def _looks_like_http_endpoint(value: str | None) -> bool:
    """Recognize a pasted endpoint without echoing or trusting its contents."""

    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _url_parts(raw: str, *, code: str = "operator.endpoint_invalid") -> urllib.parse.SplitResult:
    value = str(raw or "").strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise OperatorCliError(code, "endpoint port is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise OperatorCliError(code, "use a plain http(s) URL without credentials or query parameters")
    if len(value) > 300:
        raise OperatorCliError(code, "endpoint URL is too long")
    return parsed


def _url_without_query(parsed: urllib.parse.SplitResult) -> str:
    path = parsed.path.rstrip("/") or ""
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_endpoint(
    base_url: str | None,
    *,
    explicit_models_url: str | None = None,
    provider: str = "openai",
) -> EndpointConfig:
    """Normalize a base or explicit ``/models`` URL using provider defaults."""

    provider = normalize_llm_provider(provider)
    if not str(base_url or "").strip():
        providers = _providers()
        try:
            base_url = providers.get_spec("llm", provider).default_base_url
        except providers.ProviderError as exc:
            raise OperatorCliError("operator.endpoint_invalid", "unknown cloud provider") from exc
    if not base_url:
        raise OperatorCliError("operator.endpoint_invalid", "a base URL is required for this provider")

    base_parts = _url_parts(str(base_url))
    normalized_base = _url_without_query(base_parts)
    model_value = str(explicit_models_url or "").strip()
    if model_value:
        model_parts = _url_parts(model_value, code="operator.models_endpoint_invalid")
        model_path = model_parts.path.rstrip("/")
        if not model_path.endswith("/models"):
            raise OperatorCliError("operator.models_endpoint_invalid", "models URL must end with /models")
        derived_path = model_path[: -len("/models")].rstrip("/")
        derived_base = urllib.parse.urlunsplit(
            (model_parts.scheme, model_parts.netloc, derived_path, "", "")
        )
        if normalized_base != derived_base:
            raise OperatorCliError(
                "operator.endpoint_invalid",
                "base URL and models URL must refer to the same endpoint",
            )
        return EndpointConfig(normalized_base, _url_without_query(model_parts))

    if base_parts.path.rstrip("/").endswith("/models"):
        model_url = normalized_base
        derived_path = base_parts.path.rstrip("/")[: -len("/models")].rstrip("/")
        normalized_base = urllib.parse.urlunsplit(
            (base_parts.scheme, base_parts.netloc, derived_path, "", "")
        )
        return EndpointConfig(normalized_base, model_url)
    return EndpointConfig(normalized_base, f"{normalized_base}/models")


def parse_models_payload(payload: Any) -> tuple[ModelChoice, ...]:
    """Parse only the provider's structured ``{"data": [{"id": ...}]}`` list."""

    rows = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise OperatorCliError("operator.models_invalid", "provider returned no valid model list")
    seen: dict[str, ModelChoice] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise OperatorCliError("operator.models_invalid", "provider model entries are malformed")
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200:
            raise OperatorCliError("operator.models_invalid", "provider model entries are malformed")
        label = row.get("display_name") or row.get("label") or model_id
        if not isinstance(label, str):
            label = model_id
        seen.setdefault(model_id.strip(), ModelChoice(model_id.strip(), label[:120]))
    return tuple(sorted(seen.values(), key=lambda item: item.model_id))


def fetch_models(
    endpoint: EndpointConfig,
    api_key: str,
    *,
    request_get: Callable[..., Any] | None = None,
    retries: int = 2,
    timeout: float = 20.0,
) -> tuple[ModelChoice, ...]:
    """Fetch and validate models with bounded retries and no payload logging."""

    secret = str(api_key or "").strip()
    if not secret:
        raise OperatorCliError("operator.api_key_missing", "API key is required")
    if request_get is None:
        import httpx

        request_get = httpx.get
    last_error: str = "provider request failed"
    for _attempt in range(max(1, int(retries))):
        try:
            response = request_get(
                endpoint.models_url,
                headers={"Authorization": f"Bearer {secret}"},
                timeout=timeout,
            )
            response.raise_for_status()
            return parse_models_payload(response.json())
        except OperatorCliError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize every transport failure
            last_error = safe_error_text(exc, secret=secret)
    raise OperatorCliError("operator.provider_unreachable", last_error)


def select_model(
    models: Sequence[ModelChoice],
    *,
    selection: str,
    query: str = "",
) -> ModelChoice:
    """Select a listed model by filtered number, exact ID, or manual listed ID."""

    visible = [
        model
        for model in models
        if not query.strip()
        or query.casefold() in model.model_id.casefold()
        or query.casefold() in model.label.casefold()
    ]
    token = str(selection or "").strip()
    if token.casefold().startswith("manual:"):
        token = token.split(":", 1)[1].strip()
    if token.isdigit():
        index = int(token) - 1
        if index < 0 or index >= len(visible):
            raise OperatorCliError("operator.model_unavailable", "that model number is not listed")
        return visible[index]
    for model in visible or models:
        if model.model_id == token:
            return model
    raise OperatorCliError("operator.model_unavailable", "choose a model returned by the provider")


def discover_chapter_folder(value: str | Path) -> ChapterFolder:
    """Resolve one direct child folder containing supported images in stable order."""

    raw = str(value or "").strip().strip('"')
    folder = Path(raw).expanduser()
    try:
        folder = folder.resolve()
    except OSError as exc:
        raise OperatorCliError("operator.chapter_folder_invalid", "folder path cannot be resolved") from exc
    if not folder.is_dir():
        raise OperatorCliError("operator.chapter_folder_invalid", "choose an existing directory")
    children = sorted((item for item in folder.iterdir() if item.is_file()), key=lambda item: (item.name.casefold(), item.name))
    unsupported = [item.name for item in children if item.name.casefold() not in _IGNORED_FOLDER_FILES and item.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES]
    if unsupported:
        raise OperatorCliError("operator.unsupported_file", f"unsupported file in chapter folder: {unsupported[0]}")
    images = tuple(item for item in children if item.suffix.casefold() in SUPPORTED_IMAGE_SUFFIXES)
    if not images:
        raise OperatorCliError("operator.chapter_folder_empty", "folder contains no JPG, PNG, or WebP images")
    return ChapterFolder(folder=folder, image_paths=images)


def discover_batch_folders(value: str | Path) -> tuple[ChapterFolder, ...]:
    """Discover direct child chapter folders in deterministic filename order."""

    parent = Path(str(value or "").strip().strip('"')).expanduser()
    try:
        parent = parent.resolve()
    except OSError as exc:
        raise OperatorCliError("operator.batch_folder_invalid", "parent path cannot be resolved") from exc
    if not parent.is_dir():
        raise OperatorCliError("operator.batch_folder_invalid", "choose an existing parent directory")
    children = sorted((item for item in parent.iterdir() if item.is_dir()), key=lambda item: (item.name.casefold(), item.name))
    chapters = tuple(discover_chapter_folder(child) for child in children)
    if not chapters:
        raise OperatorCliError("operator.batch_empty", "parent has no chapter folders")
    return chapters


def chapter_manifest(chapter: ChapterFolder) -> ChapterManifest:
    entries: list[tuple[str, str, int]] = []
    for path in chapter.image_paths:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise OperatorCliError("operator.chapter_read_failed", f"cannot read {path.name}") from exc
        entries.append((path.name, hashlib.sha256(data).hexdigest(), len(data)))
    canonical = json.dumps(entries, ensure_ascii=True, separators=(",", ":"))
    return ChapterManifest(tuple(entries), hashlib.sha256(canonical.encode("utf-8")).hexdigest())


def _manifest_source_keys(manifest: ChapterManifest) -> set[tuple[str, str]]:
    ingest = _ingest()
    return {(ingest.derive_source_family(name), checksum) for name, checksum, _size in manifest.entries}


def _existing_project_for_manifest(db: Any, workspace_id: str, manifest: ChapterManifest) -> Project | None:
    from sqlalchemy import select

    Project, _SourceAsset, _User, _Workspace = _models()
    pipeline = _pipeline()
    expected = _manifest_source_keys(manifest)
    projects = db.scalars(
        select(Project).where(Project.workspace_id == workspace_id).order_by(Project.created_at)
    ).all()
    for project in projects:
        assets = [
            asset
            for asset in pipeline.project_assets(db, project.id)
            if str(getattr(asset.type, "value", asset.type)) == "image"
        ]
        actual = {
            (str(asset.source_family or ""), str(asset.original_checksum or asset.checksum or ""))
            for asset in assets
        }
        if actual == expected and actual:
            return project
    return None


def _asset_from_ingested(project_id: str, result: Any, order_index: int) -> SourceAsset:
    _Project, SourceAsset, _User, _Workspace = _models()
    x0, y0, x1, y1 = result.source_bounds
    return SourceAsset(
        project_id=project_id,
        type=result.type,
        original_filename=result.original_filename,
        storage_key=result.storage_key,
        mime_type=result.mime_type,
        size_bytes=result.size_bytes,
        checksum=result.checksum,
        extracted_text=result.extracted_text,
        width=result.width,
        height=result.height,
        duration=result.audio_duration,
        order_index=order_index,
        source_family=result.source_family,
        panel_bbox=result.panel_bbox or {},
        panel_quality=result.panel_quality or {},
        panel_decision=result.panel_decision,
        original_checksum=result.original_checksum,
        original_width=result.original_width,
        original_height=result.original_height,
        source_bounds_json={
            "x": x0,
            "y": y0,
            "width": x1 - x0,
            "height": y1 - y0,
        },
        strip_order=result.strip_order,
        region_order=result.region_order,
        trim_classification=result.trim_classification,
        coverage_map_hash=result.coverage_map_hash,
    )


def import_chapter_folder(
    db: Any,
    chapter: ChapterFolder,
    *,
    workspace_id: str,
    actor_id: str,
) -> ImportedChapter:
    """Create or reuse one project, using the existing ingest/storage boundary."""

    ingest = _ingest()
    pipeline = _pipeline()
    Project, _SourceAsset, _User, _Workspace = _models()
    manifest = chapter_manifest(chapter)
    existing = _existing_project_for_manifest(db, workspace_id, manifest)
    if existing is not None:
        return ImportedChapter(existing.id, False, len(chapter.image_paths), manifest.manifest_sha256)

    project = Project(
        workspace_id=workspace_id,
        title=chapter.folder.name[:200],
        chapter=chapter.folder.name[:60],
        content_type="chapter_recap",
        target_duration=int(_settings().default_target_seconds),
        status="draft",
    )
    db.add(project)
    db.flush()
    created_keys: list[str] = []
    order_index = 0
    try:
        for image_path in chapter.image_paths:
            results = ingest.ingest_upload_parts(
                project.id,
                image_path.name,
                _image_mime_type(image_path),
                image_path.read_bytes(),
            )
            for result in results:
                asset = _asset_from_ingested(project.id, result, order_index)
                order_index += 1
                created_keys.append(asset.storage_key)
                db.add(asset)
        if order_index == 0:
            raise OperatorCliError("operator.chapter_empty", "no images were ingested")
        pipeline.audit(
            db,
            "operator.chapter_import",
            "project",
            project.id,
            actor_id,
            image_count=order_index,
            manifest_sha256=manifest.manifest_sha256,
        )
        db.flush()
    except OperatorCliError:
        for key in created_keys:
            ingest.storage.delete(key)
        raise
    except Exception as exc:  # noqa: BLE001 - keep CLI failure sanitized
        for key in created_keys:
            ingest.storage.delete(key)
        raise OperatorCliError("operator.chapter_import_failed", safe_error_text(exc)) from None
    return ImportedChapter(project.id, True, order_index, manifest.manifest_sha256)


def _capability_png() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (48, 48), (24, 32, 48))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 40, 40), outline=(240, 240, 240), width=3)
    draw.ellipse((18, 18, 30, 30), fill=(220, 120, 80))
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def run_capability_probe(
    db: Any,
    workspace_id: str,
    *,
    consent: bool,
    model: str | None = None,
    runner_factory: Callable[..., Any] | None = None,
) -> CapabilityResult:
    """Make one explicit, billable vision probe; never infer capability by name."""

    if not consent:
        return CapabilityResult(False, False, "operator.capability_consent_required")
    cloud_multimodal = None
    use_real_panel = runner_factory is None
    if runner_factory is None:
        cloud_multimodal = _cloud()
        runner_factory = cloud_multimodal.resolve_cloud_runner
    payload = _capability_png()
    try:
        runner = runner_factory(db, workspace_id, model=model)
        checksum = hashlib.sha256(payload).hexdigest()
        panel_kwargs = {
            "panel_id": "operator-capability-probe",
            "source_asset_id": "operator-capability-probe",
            "source_order": 0,
            "mime_type": "image/png",
            "payload": payload,
            "payload_checksum": checksum,
            "source_checksum": checksum,
            "panel_bounds": (0, 0, 48, 48),
            "source_dimensions": (48, 48),
        }
        panel = (
            cloud_multimodal.CloudPanelInput(**panel_kwargs)
            if use_real_panel and cloud_multimodal is not None
            else _CapabilityPanel(**panel_kwargs)
        )
        result = runner.run_visual_evidence((panel,))
        if not isinstance(result, Mapping) and not hasattr(result, "as_dict"):
            return CapabilityResult(True, False, "operator.capability_response_invalid")
    except Exception as exc:  # noqa: BLE001 - provider boundary must stay safe
        code = str(getattr(exc, "code", "") or "")
        if code:
            return CapabilityResult(True, False, code)
        return CapabilityResult(True, False, "operator.capability_failed", safe_error_text(exc))
    return CapabilityResult(True, True, "operator.capability_verified", "structured visual response accepted")


def _job_summary(record: Any) -> dict[str, Any]:
    data = record.as_dict() if hasattr(record, "as_dict") else dict(record)
    return {
        "job_id": str(data.get("job_id", "")),
        "state": str(data.get("state", "")),
        "error_code": str(data.get("error_code", "")),
        "error_message": safe_error_text(RuntimeError(str(data.get("error_message", "")))) if data.get("error_message") else "",
        "review_queue_count": len(data.get("review_queue", [])) if isinstance(data.get("review_queue", []), list) else 0,
    }


def list_job_states(state_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(state_dir)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name.casefold()):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, Mapping) or not data.get("job_id"):
                raise ValueError("invalid job state")
            rows.append(_job_summary(data))
        except (OSError, ValueError, TypeError, KeyError):
            rows.append({"job_id": path.stem, "state": "FAILED", "error_code": "cloud.job_state_invalid", "error_message": "job state is invalid", "review_queue_count": 0})
    return rows


@contextmanager
def _db_context(db: Any, db_factory: Callable[[], Any]) -> Iterator[Any]:
    if db is not None:
        yield db
        return
    context = db_factory()
    if hasattr(context, "__enter__"):
        with context as opened:
            yield opened
    else:
        yield context


def run_projects(
    db: Any,
    project_ids: Sequence[str],
    *,
    state_dir: str | Path = "data/cloud-multimodal-jobs",
    review_dir: str | Path = "data/segmentation-review",
    actor_id: str = "",
    model: str | None = None,
    runner_factory: Callable[..., Any] | None = None,
    service_factory: Callable[..., Any] | None = None,
    db_factory: Callable[[], Any] | None = None,
    max_attempts: int = 2,
    max_requests: int | None = None,
    min_request_interval_s: float = 0.0,
    estimated_cost_per_request: float = 0.0,
    max_concurrent: int = 1,
    run_options: RunOptions | None = None,
) -> list[dict[str, Any]]:
    """Run sorted isolated jobs through the existing CloudBatchService."""

    cloud_multimodal = None
    pipeline = None
    if service_factory is None or runner_factory is not None:
        cloud_multimodal = _cloud()
    if run_options is not None:
        max_attempts = run_options.max_attempts
        max_requests = run_options.max_requests
        min_request_interval_s = run_options.min_request_interval_s
        estimated_cost_per_request = run_options.estimated_cost_per_request
        max_concurrent = run_options.max_concurrent
    if service_factory is None:
        service_factory = cloud_multimodal.CloudBatchService
    if runner_factory is not None:
        pipeline = _pipeline()
    if db_factory is None and db is None:
        db_factory = _session_scope()
    ordered_ids = sorted({str(project_id).strip() for project_id in project_ids if str(project_id).strip()})
    if not ordered_ids:
        return []
    with _db_context(db, db_factory or (lambda: None)) as run_db:
        runner = None
        if runner_factory is not None:
            try:
                first_project = pipeline.get_project(run_db, ordered_ids[0])
                runner = runner_factory(
                    run_db,
                    first_project.workspace_id,
                    model=model,
                    max_attempts=max_attempts,
                    max_requests=max_requests,
                    min_request_interval_s=min_request_interval_s,
                    estimated_cost_per_request=estimated_cost_per_request,
                )
            except Exception as exc:
                code = str(getattr(exc, "code", "") or "operator.provider_unavailable")
                if not code.startswith("cloud."):
                    code = "operator.provider_unavailable"
                return [{"job_id": job_id, "state": "FAILED", "error_code": code, "error_message": "provider is not ready", "review_queue_count": 0} for job_id in ordered_ids]
        store = cloud_multimodal.JsonJobStore(Path(state_dir)) if cloud_multimodal is not None else None
        service = service_factory(
            runner=runner,
            store=store,
            max_concurrent=max_concurrent,
            review_root=Path(review_dir),
        )
        rows: list[dict[str, Any]] = []
        for project_id in ordered_ids:
            try:
                rows.append(_job_summary(service.run_project(run_db, project_id, actor_id=actor_id)))
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                code = str(getattr(exc, "code", "") or "cloud.provider_request_failed")
                rows.append({"job_id": project_id, "state": "FAILED", "error_code": code, "error_message": "job blocked safely", "review_queue_count": 0})
        return rows


def resolve_operator_context(db: Any) -> tuple[User, Workspace]:
    return ensure_local_operator_context(db)


_LOCAL_OPERATOR_EMAIL = "local-operator@local.invalid"
_LOCAL_OPERATOR_NAME = "Local Operator"


def ensure_local_operator_context(db: Any) -> tuple[User, Workspace]:
    """Return an idempotent local operator/workspace without a web-login gate."""

    from sqlalchemy import select

    _Project, _SourceAsset, User, Workspace = _models()
    user_created = False
    user = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.created_at)).first()
    if user is None:
        user = db.scalars(select(User).where(User.email == _LOCAL_OPERATOR_EMAIL)).first()
        user_created = user is None
        if user is None:
            user = User(
                email=_LOCAL_OPERATOR_EMAIL,
                name=_LOCAL_OPERATOR_NAME,
                password_hash="",
                is_active=True,
            )
            db.add(user)
            db.flush()
        elif not user.is_active:
            user.is_active = True
    workspace = db.scalars(select(Workspace).where(Workspace.owner_id == user.id).order_by(Workspace.created_at)).first()
    workspace_created = workspace is None
    if workspace is None:
        workspace = Workspace(owner_id=user.id, name="My Workspace")
        db.add(workspace)
        db.flush()
    if user_created or workspace_created:
        _pipeline().audit(
            db,
            "operator.local_context_bootstrap",
            "workspace",
            workspace.id,
            user.id,
            origin="local_operator_cli",
            user_created=user_created,
            workspace_created=workspace_created,
        )
        db.flush()
    return user, workspace


class OperatorCLI:
    """Small dependency-injectable menu used by the launcher and tests."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        secret_fn: Callable[[str], str] | None = None,
        db_factory: Callable[[], Any] | None = None,
        state_dir: str | Path = "data/cloud-multimodal-jobs",
        review_dir: str | Path = "data/segmentation-review",
    ) -> None:
        import getpass

        from app.services import operator_ui

        self.input_fn = input_fn
        self.output_fn = output_fn
        self.secret_fn = secret_fn or getpass.getpass
        self.db_factory = db_factory or _session_scope()
        self.state_dir = Path(state_dir)
        self.review_dir = Path(review_dir)
        self._operator_ui = operator_ui
        self._color = operator_ui.color_enabled()

    def _print(self, message: str = "") -> None:
        self.output_fn(str(message))

    def _ask(self, prompt: str, default: str = "") -> str:
        try:
            value = self.input_fn(prompt)
        except EOFError:
            return default
        return value.strip() or default

    def _confirm(self, prompt: str) -> bool:
        return self._ask(f"{prompt} [y/N]: ").casefold() in {"y", "yes", "ya"}

    def _run_options(self) -> RunOptions:
        options = parse_run_options(
            {
                "max_attempts": self._ask("Max attempts [2]: ", "2"),
                "max_requests": self._ask("Max provider requests, blank=unlimited: "),
                "min_request_interval_s": self._ask("Minimum seconds between requests [0]: ", "0"),
                "estimated_cost_per_request": self._ask("Estimated cost per request USD [0]: ", "0"),
                "max_concurrent": self._ask("Concurrent chapters [1]: ", "1"),
            }
        )
        self._print(
            "Run budget: "
            f"attempts={options.max_attempts}, requests={options.max_requests or 'unlimited'}, "
            f"interval={options.min_request_interval_s:g}s, cost/request=${options.estimated_cost_per_request:g}, "
            f"concurrent={options.max_concurrent}"
        )
        return options

    def _db(self):
        return _db_context(None, self.db_factory)

    def run(self) -> int:
        self._print(f"ManhwaShorts operator console ({CLI_VERSION})")
        while True:
            jobs = list_job_states(self.state_dir)
            job_summary = "none" if not jobs else ", ".join(
                f"{row['job_id']}={row['state']}" for row in jobs[:2]
            )
            if len(jobs) > 2:
                job_summary += f" (+{len(jobs) - 2})"
            self._print(
                self._operator_ui.render_menu(
                    provider="use menu 1 to configure",
                    model="use menu 3 to select",
                    project="use menu 4/5 to import",
                    job=job_summary,
                    color=self._color,
                )
            )
            choice = self._ask("Choose: ", "0")
            if choice == "0":
                self._print("Exit / Keluar")
                return 0
            try:
                handlers = {
                    "1": self.setup_provider,
                    "2": self.test_connection,
                    "3": self.select_provider_model,
                    "4": self.import_and_run_one,
                    "5": self.import_and_run_batch,
                    "6": self.resume_jobs,
                    "7": self.show_status,
                }
                handler = handlers.get(choice)
                if handler is None:
                    raise OperatorCliError("operator.menu_choice_invalid", "choose a number from the menu")
                handler()
            except KeyboardInterrupt:
                self._print("Interrupted safely. Existing stage checkpoints remain resumable.")
                return 130
            except OperatorCliError as exc:
                self._print(f"Blocked safely: {exc}")
            except Exception as exc:  # noqa: BLE001 - no traceback or secret leak in operator console
                self._print(f"Blocked safely: operator.unexpected_error: {safe_error_text(exc)}")

    def setup_provider(self) -> None:
        providers = _providers()
        credentials = _credentials()
        provider_input = self._ask("Nama provider/profil [openai]: ", "openai")
        endpoint_from_profile: str | None = None
        while True:
            if _looks_like_http_endpoint(provider_input):
                endpoint_from_profile = provider_input
                provider_input = "openai"
                self._print("Endpoint dikenali dari input pertama; profil openai digunakan.")
            provider = normalize_llm_provider(provider_input)
            try:
                providers.get_spec("llm", provider)
                break
            except providers.ProviderError:
                self._print(
                    "Provider tidak didukung (unsupported). Pilih openai, "
                    "openai-compatible, openai_compatible, atau custom_openai."
                )
                provider_input = self._ask("Nama provider/profil [openai]: ", "openai")

        base_url = endpoint_from_profile or self._ask(
            "Endpoint API (contoh http://host:port/v1): "
        )
        endpoint = normalize_endpoint(base_url, provider=provider)
        api_key = self.secret_fn("API key (hidden, never echoed): ")
        models_url = self._ask("Optional explicit models URL (blank derives /models): ")
        if models_url:
            endpoint = normalize_endpoint(
                base_url,
                explicit_models_url=models_url,
                provider=provider,
            )
        label = self._ask("Nama tampilan provider (optional): ")
        models = fetch_models(endpoint, api_key)
        try:
            with self._db() as db:
                user, workspace = resolve_operator_context(db)
                try:
                    row, _result = credentials.save_credential(
                        db,
                        workspace_id=workspace.id,
                        actor_id=user.id,
                        kind="llm",
                        provider=provider,
                        api_key=api_key,
                        base_url=endpoint.base_url,
                        label=label,
                        verify=True,
                    )
                except OperatorCliError:
                    raise
                except Exception as exc:  # noqa: BLE001 - never expose key-bearing errors
                    raise OperatorCliError(
                        "operator.credential_save_failed",
                        safe_error_text(exc, secret=api_key),
                    ) from None
                self._print(f"Verified and encrypted credential {row.key_hint}; {len(models)} model(s) available.")
                self._print("Next: choose menu 3 to select a listed model and optionally test visual capability.")
        finally:
            del api_key

    def test_connection(self) -> None:
        credentials = _credentials()
        with self._db() as db:
            user, workspace = resolve_operator_context(db)
            rows = credentials.list_credentials(db, workspace.id, "llm")
            row = next((item for item in rows if item.is_default), rows[0] if rows else None)
            if row is None:
                raise OperatorCliError("operator.credential_missing", "set up a cloud provider first")
            refreshed, result = credentials.refresh_models(db, workspace.id, row.id, user.id)
            self._print(f"Connection: {'verified' if result.ok else 'blocked'}; models={len(result.models)}; key={refreshed.key_hint}")
            if not result.ok:
                self._print(f"Reason: {safe_error_text(RuntimeError(result.message))}")

    def select_provider_model(self) -> None:
        credentials = _credentials()
        with self._db() as db:
            user, workspace = resolve_operator_context(db)
            rows = credentials.list_credentials(db, workspace.id, "llm")
            row = next((item for item in rows if item.is_default), rows[0] if rows else None)
            if row is None:
                raise OperatorCliError("operator.credential_missing", "set up a cloud provider first")
            row, result = credentials.refresh_models(db, workspace.id, row.id, user.id)
            if not result.ok:
                raise OperatorCliError(
                    "operator.provider_unreachable",
                    safe_error_text(RuntimeError(result.message)),
                )
            models = tuple(ModelChoice(item.id, item.label) for item in result.models)
            for index, model in enumerate(models, start=1):
                self._print(f"{index}) {model.model_id}")
            query = self._ask("Filter (blank for all): ")
            selection = self._ask("Model number or manual:<listed-id>: ")
            chosen = select_model(models, query=query, selection=selection)
            credentials.select_model(db, workspace.id, row.id, chosen.model_id, user.id)
            credentials.set_default(db, workspace.id, row.id, user.id)
            self._print(f"Selected model: {chosen.model_id}")
            if self._confirm("Run one optional billable visual capability test now?"):
                result = run_capability_probe(db, workspace.id, consent=True, model=chosen.model_id)
                self._print(f"Capability: {result.code}")

    def _confirm_chapter(self, chapter: ChapterFolder) -> None:
        self._print(f"Chapter folder: {chapter.folder}; images: {len(chapter.image_paths)}")
        self._print("Order: " + ", ".join(path.name for path in chapter.image_paths[:8]) + (" ..." if len(chapter.image_paths) > 8 else ""))
        if not self._confirm("Continue and allow cloud analysis calls after import?"):
            raise OperatorCliError("operator.cancelled", "operator cancelled before provider calls")

    def _import_one(self, chapter: ChapterFolder) -> ImportedChapter:
        with self._db() as db:
            user, workspace = resolve_operator_context(db)
            imported = import_chapter_folder(db, chapter, workspace_id=workspace.id, actor_id=user.id)
            self._print(f"Project {'reused' if not imported.created else 'created'}: {imported.project_id}")
            return imported

    def import_and_run_one(self) -> None:
        cloud_multimodal = _cloud()
        chapter = discover_chapter_folder(self._ask("Paste/drag one chapter folder: "))
        self._confirm_chapter(chapter)
        imported = self._import_one(chapter)
        rows = run_projects(
            None,
            [imported.project_id],
            state_dir=self.state_dir,
            review_dir=self.review_dir,
            runner_factory=cloud_multimodal.resolve_cloud_runner,
            run_options=self._run_options(),
        )
        for row in rows:
            self._print(f"{row['job_id']}: {row['state']} {row['error_code']}")
        self._print("READY_TO_RENDER means AI/narrative stages are ready; final voiced render remains blocked until authoritative voice timing and approval gates pass.")

    def import_and_run_batch(self) -> None:
        cloud_multimodal = _cloud()
        chapters = discover_batch_folders(self._ask("Paste/drag batch parent folder: "))
        self._print(f"Found {len(chapters)} chapter folder(s).")
        options = self._run_options()
        if not self._confirm("Import and run this batch with the configured request budget?"):
            raise OperatorCliError("operator.cancelled", "operator cancelled before provider calls")
        imported = [self._import_one(chapter) for chapter in chapters]
        rows = run_projects(
            None,
            [item.project_id for item in imported],
            state_dir=self.state_dir,
            review_dir=self.review_dir,
            runner_factory=cloud_multimodal.resolve_cloud_runner,
            run_options=options,
        )
        for row in rows:
            self._print(f"{row['job_id']}: {row['state']} {row['error_code']}")

    def resume_jobs(self) -> None:
        cloud_multimodal = _cloud()
        rows = list_job_states(self.state_dir)
        pending = [row["job_id"] for row in rows if row.get("state") not in {"READY_TO_RENDER", "RENDERED"}]
        if not pending:
            self._print("No failed or pending jobs to resume.")
            return
        self._print("Resumable jobs: " + ", ".join(pending))
        rows = run_projects(
            None,
            pending,
            state_dir=self.state_dir,
            review_dir=self.review_dir,
            runner_factory=cloud_multimodal.resolve_cloud_runner,
            run_options=self._run_options(),
        )
        for row in rows:
            self._print(f"{row['job_id']}: {row['state']} {row['error_code']}")

    def show_status(self) -> None:
        credentials = _credentials()
        self._print("Review-only workflow: voice/TTS/audio/publication are disabled.")
        self._print("Jobs:")
        for row in list_job_states(self.state_dir):
            self._print(f"- {row['job_id']}: {row['state']} {row['error_code']}")
        try:
            with self._db() as db:
                _user, workspace = resolve_operator_context(db)
                rows = credentials.list_credentials(db, workspace.id, "llm")
                if not rows:
                    self._print("Cloud provider: not configured (offline menu remains available).")
                for row in rows:
                    self._print(f"Cloud provider: {row.provider} {row.key_hint} status={row.status} model={row.model or 'not selected'}")
        except OperatorCliError as exc:
            self._print(f"Cloud provider: {exc.code}; {exc.message}")


def main() -> int:
    try:
        from app.db import init_db

        init_db()
        return OperatorCLI().run()
    except KeyboardInterrupt:
        print("Interrupted safely; durable job checkpoints were not deleted.")
        return 130
    except OperatorCliError as exc:
        print(f"Blocked safely: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - launcher must not expose traceback/provider data
        print(f"Blocked safely: operator.startup_failed: {safe_error_text(exc)}")
        return 1


__all__ = [
    "CLI_VERSION",
    "CapabilityResult",
    "ChapterFolder",
    "ChapterManifest",
    "EndpointConfig",
    "ImportedChapter",
    "ModelChoice",
    "OperatorCLI",
    "OperatorCliError",
    "RunOptions",
    "chapter_manifest",
    "discover_batch_folders",
    "discover_chapter_folder",
    "fetch_models",
    "import_chapter_folder",
    "list_job_states",
    "main",
    "normalize_endpoint",
    "normalize_llm_provider",
    "parse_models_payload",
    "parse_run_options",
    "resolve_operator_context",
    "run_capability_probe",
    "run_projects",
    "safe_error_text",
    "select_model",
]
