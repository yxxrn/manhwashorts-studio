#!/usr/bin/env python3
"""Unattended, resumable no-publish production runner.

The shell entrypoint loads the deployment runtime environment first.  This
module then fails fast before project creation, checkpoints every durable
stage, and retries only narrowly transient provider failures.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from app.config import settings
from app.constants import DEFAULT_ENGLISH_VOICE_ID
from app.db import SessionLocal
from app.models import Publication
from app.routers import projects as project_router
from app.routers import sources as source_router
from app.schemas import ProjectCreate, SuwayomiImportRequest
from app.services import operator_cli, resolver
from app.services import pipeline as pl
from app.services.pipeline_stages import production as production_stage

TRANSIENT_ANALYSIS_CODES = frozenset({
    "vision_provider_request_failed",
    "vision_response_invalid",
})
TRANSIENT_TEXT = (
    "503",
    "429",
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
    "provider_request_failed",
    "tts http request failed",
)


def _json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
def _chapter_key(value: object) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _analysis_codes(analysis: object) -> set[str]:
    payload = getattr(analysis, "blocking_reasons_json", None) or {}
    return {str(code) for code in payload.get("codes", []) if str(code)}


def _transient_exception(exc: BaseException) -> bool:
    code = str(getattr(exc, "code", "") or "").casefold()
    text = f"{code} {type(exc).__name__} {exc}".casefold()
    return any(token in text for token in TRANSIENT_TEXT)


def _load_state(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if not path.exists():
        return {
            "contract": "unattended-production-run-v1",
            "run_id": args.run_id,
            "title": args.title,
            "chapter_from": args.chapter_from,
            "chapter_to": args.chapter_to,
            "source_id": args.source_id,
            "language": args.language,
            "voice_id": getattr(args, "voice_id", DEFAULT_ENGLISH_VOICE_ID),
            "watermark_enabled": bool(getattr(args, "watermark", False)),
            "watermark_text": str(getattr(args, "watermark_text", "") or ""),
            "status": "STARTING",
            "stages": {},
            "events": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    identity = (payload.get("title"), payload.get("chapter_from"), payload.get("chapter_to"), payload.get("source_id"))
    expected = (args.title, args.chapter_from, args.chapter_to, args.source_id)
    if identity != expected:
        raise RuntimeError("run state identity does not match requested corpus")
    if "voice_id" in payload and payload["voice_id"] != getattr(args, "voice_id", DEFAULT_ENGLISH_VOICE_ID):
        raise RuntimeError("run state identity does not match requested voice profile")
    if bool(payload.get("watermark_enabled", False)) != bool(getattr(args, "watermark", False)):
        raise RuntimeError("run state identity does not match requested watermark toggle")
    if str(payload.get("watermark_text", "")) != str(getattr(args, "watermark_text", "") or ""):
        raise RuntimeError("run state identity does not match requested watermark text")
    return payload
def _event(state: dict[str, Any], path: Path, name: str, **detail: Any) -> None:
    stamp = time.time()
    state["updated_at"] = stamp
    state.setdefault("events", []).append({"time": stamp, "event": name, **detail})
    _json_write(path, state)
    print(f"EVENT {name} {json.dumps(detail, sort_keys=True, default=str)}", flush=True)


def _stage(state: dict[str, Any], path: Path, name: str, started: float, **detail: Any) -> None:
    duration = round(time.perf_counter() - started, 6)
    record = dict(detail)
    event_detail = dict(detail)
    if "duration_s" in detail:
        # Some stages report a domain duration (for example final media length).
        # Keep that value and record launcher wall time under an unambiguous key.
        record["stage_wall_s"] = duration
        event_detail["stage_wall_s"] = duration
    else:
        record["duration_s"] = duration
        event_detail["duration_s"] = duration
    state.setdefault("stages", {})[name] = record
    _event(state, path, f"{name}.complete", **event_detail)


def _required_doctor_checks() -> list[str]:
    from scripts.doctor import collect

    failed = [item.name for item in collect() if item.required and not item.ok]
    return failed


def _preflight(db: Any, args: argparse.Namespace, state: dict[str, Any], state_path: Path) -> tuple[Any, Any]:
    started = time.perf_counter()
    if settings.environment != "production":
        raise RuntimeError(f"production runner requires MS_ENVIRONMENT=production, got {settings.environment!r}")
    doctor_failed = _required_doctor_checks()
    if doctor_failed:
        raise RuntimeError("machine doctor failed: " + ", ".join(doctor_failed))
    free_bytes = shutil.disk_usage(Path(settings.data_dir)).free
    if free_bytes < int(args.min_free_gb * 1024**3):
        raise RuntimeError(f"insufficient free disk: {free_bytes / 1024**3:.2f} GiB")
    user, workspace = operator_cli.resolve_operator_context(db)
    capability = operator_cli.run_capability_probe(db, workspace.id, consent=True)
    if not capability.ok:
        raise RuntimeError(f"vision preflight failed: {capability.code}")

    tts_provider, tts_resolution = resolver.resolve_tts(db, workspace.id)
    if not tts_provider.available():
        raise RuntimeError(f"TTS provider unavailable: {tts_resolution.provider}")
    tts_probe = Path(settings.tmp_dir) / f"{args.run_id}-tts-preflight.wav"
    try:
        clip = tts_provider.synthesize("Production preflight ready.", tts_probe, voice_id=args.voice_id, speed=1.0)
        if float(clip.duration) <= 0.0:
            raise RuntimeError("TTS preflight returned zero duration")
    finally:
        tts_probe.unlink(missing_ok=True)

    connector = source_router._ready_client()
    resolved = connector.resolve_range(
        args.title, args.chapter_from, args.chapter_to, args.language, args.source_id
    )
    resolved_source = str(resolved.source.get("id") or resolved.manga.get("sourceId") or "")
    chapters = {_chapter_key(row.get("chapterNumber")) for row in resolved.chapters}
    expected = {_chapter_key(value) for value in range(int(args.chapter_from), int(args.chapter_to) + 1)}
    if resolved_source != args.source_id:
        raise RuntimeError(f"source mismatch: resolved {resolved_source}, expected {args.source_id}")
    if chapters != expected:
        raise RuntimeError(f"chapter mismatch: resolved={sorted(chapters)} expected={sorted(expected)}")

    _stage(
        state,
        state_path,
        "preflight",
        started,
        environment=settings.environment,
        vision=capability.code,
        tts=tts_resolution.provider,
        free_gb=round(free_bytes / 1024**3, 3),
        chapters=sorted(chapters),
    )
    return user, workspace
def _ensure_project(db: Any, args: argparse.Namespace, state: dict[str, Any], state_path: Path, user: Any, workspace: Any) -> Any:
    project_id = str(state.get("project_id") or "")
    if project_id:
        project = pl.get_project(db, project_id)
        return project
    started = time.perf_counter()
    project = project_router.create_project(
        ProjectCreate(
            title=f"{args.title} Run {args.run_id}",
            manhwa_title=args.title,
            chapter=f"{_chapter_key(args.chapter_from)}-{_chapter_key(args.chapter_to)}",
            template="reference_matched_shorts_v2",
            target_duration=55,
            voice_id=args.voice_id,
            watermark_enabled=bool(args.watermark),
            watermark_text=args.watermark_text,
        ),
        db,
        workspace,
        user,
    )
    db.commit()
    state["project_id"] = project.id
    _stage(state, state_path, "project_create", started, project_id=project.id)
    return project


def _ensure_source(db: Any, args: argparse.Namespace, state: dict[str, Any], state_path: Path, user: Any, project: Any) -> dict[str, Any]:
    source_stage = state.get("stages", {}).get("source_import")
    if source_stage:
        return dict(source_stage)
    started = time.perf_counter()
    imported = source_router.import_suwayomi_range(
        SuwayomiImportRequest(
            title=args.title,
            chapter_from=args.chapter_from,
            chapter_to=args.chapter_to,
            language=args.language,
            source_id=args.source_id,
        ),
        project,
        db,
        user,
    )
    db.commit()
    chapters = {_chapter_key(value) for value in imported.get("chapters", [])}
    expected = {_chapter_key(value) for value in range(int(args.chapter_from), int(args.chapter_to) + 1)}
    if chapters != expected:
        raise RuntimeError(f"import chapter mismatch: {sorted(chapters)} != {sorted(expected)}")
    _stage(
        state,
        state_path,
        "source_import",
        started,
        pages=int(imported.get("pages_downloaded", 0)),
        assets=int(imported.get("assets_created", 0)),
        duplicates=int(imported.get("duplicates_skipped", 0)),
    )
    return imported
def _ensure_analysis(db: Any, args: argparse.Namespace, state: dict[str, Any], state_path: Path, user: Any, project: Any) -> Any:
    current = pl.latest_analysis(db, project.id)
    if current is not None and str(current.state) in {"RECONCILED", "SCRIPT_DRAFT", "SCRIPT_APPROVED"}:
        return current
    started = time.perf_counter()
    synthesis_durations: list[float] = []
    real_synthesize = pl._synthesize_with_cache

    def timed_synthesize(*call_args: Any, **call_kwargs: Any):
        call_started = time.perf_counter()
        try:
            return real_synthesize(*call_args, **call_kwargs)
        finally:
            synthesis_durations.append(time.perf_counter() - call_started)

    for attempt in range(1, args.max_analysis_attempts + 1):
        attempt_started = time.perf_counter()
        pl._synthesize_with_cache = timed_synthesize
        try:
            analysis = pl.run_analysis(db, project.id, user.id)
        finally:
            pl._synthesize_with_cache = real_synthesize
        db.commit()
        codes = _analysis_codes(analysis)
        _event(
            state,
            state_path,
            "analysis.attempt",
            attempt=attempt,
            analysis_state=str(analysis.state),
            codes=sorted(codes),
            duration_s=round(time.perf_counter() - attempt_started, 6),
        )
        if str(analysis.state) == "RECONCILED":
            perf = (analysis.reconciliation_json or {}).get("performance", {})
            observation = dict(perf.get("observation") or {})
            frameability = dict(perf.get("frameability") or {})
            _stage(
                state,
                state_path,
                "analysis",
                started,
                attempts=attempt,
                panels=int((analysis.coverage_manifest_json or {}).get("processed_panels", 0)),
                observation_wall_s=float(observation.get("wall_s", 0.0)),
                provider_calls=int(observation.get("provider_call_count", 0)),
                frameability_wall_s=float(frameability.get("wall_s", 0.0)),
                synthesis_calls=len(synthesis_durations),
                synthesis_wall_s=round(sum(synthesis_durations), 6),
                synthesis_call_durations_s=[round(value, 6) for value in synthesis_durations],
            )
            return analysis
        if codes and codes.issubset(TRANSIENT_ANALYSIS_CODES) and attempt < args.max_analysis_attempts:
            time.sleep(args.retry_delay_s)
            continue
        raise RuntimeError(f"analysis blocked: state={analysis.state} codes={sorted(codes)}")
    raise RuntimeError("analysis attempts exhausted")
def _ensure_script(db: Any, state: dict[str, Any], state_path: Path, user: Any, project: Any) -> Any:
    started = time.perf_counter()
    script = pl.latest_script_row(db, project.id)
    generated = False
    if script is None:
        script = pl.generate_script(db, project.id, actor_id=user.id)
        db.commit()
        generated = True
    if script.approved_at is None:
        script = pl.approve_script(
            db,
            script.id,
            user.id,
            editorial_review_confirmed=True,
            approval_actor_type="trusted_agent",
            approval_reason="explicit_user_production_request_no_publish",
        )
        db.commit()
    _stage(
        state,
        state_path,
        "script_approval",
        started,
        generated=generated,
        version=int(script.version),
        words=len(script.plain_text.split()),
        script_hash=pl._script_content_hash(script),
    )
    return script


def _install_production_profiler() -> tuple[dict[str, dict[str, float | int]], dict[tuple[object, str], object]]:
    telemetry: dict[str, dict[str, float | int]] = {}
    originals: dict[tuple[object, str], object] = {}

    def install(owner: object, name: str, label: str, classifier=None) -> None:
        original = getattr(owner, name)
        originals[(owner, name)] = original

        def wrapped(*args, **kwargs):
            key = classifier(args, kwargs) if classifier is not None else label
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                row = telemetry.setdefault(key, {"calls": 0, "wall_s": 0.0})
                row["calls"] = int(row["calls"]) + 1
                row["wall_s"] = round(float(row["wall_s"]) + time.perf_counter() - started, 6)

        setattr(owner, name, wrapped)

    install(pl, "generate_voiceover", "tts")
    install(pl, "build_timeline", "timeline")
    install(
        pl,
        "run_quality_checks",
        "quality",
        lambda args, kwargs: "post_render_qc" if kwargs.get("job") is not None else "pre_render_qc",
    )
    install(pl, "enqueue_render", "enqueue_render")
    install(pl, "execute_render", "render")
    install(pl, "_ensure_final_thumbnail", "thumbnail")
    install(production_stage, "_write_manual_upload_metadata", "metadata")
    return telemetry, originals


def _restore_production_profiler(originals: dict[tuple[object, str], object]) -> None:
    for (owner, name), original in originals.items():
        setattr(owner, name, original)


def _ensure_production(db: Any, args: argparse.Namespace, state: dict[str, Any], state_path: Path, user: Any, project: Any, script: Any) -> Any:
    started = time.perf_counter()
    script_hash = pl._script_content_hash(script)
    last_exc: BaseException | None = None
    for attempt in range(1, args.max_production_attempts + 1):
        attempt_started = time.perf_counter()
        telemetry, originals = _install_production_profiler()
        try:
            job = pl.run_production(
                db,
                project.id,
                actor_id=user.id,
                approved_script_hash=script_hash,
                approved_script_version=int(script.version),
                speed=args.speed,
                provider_name=None,
                encoder=args.encoder,
                profile=args.profile,
            )
            db.commit()
            _event(state, state_path, "production.attempt", attempt=attempt, status=str(job.status), duration_s=round(time.perf_counter() - attempt_started, 6), telemetry=telemetry)
            _stage(state, state_path, "production", started, attempts=attempt, job_id=job.id, status=str(job.status), telemetry=telemetry)
            return job
        except Exception as exc:  # noqa: BLE001 - stage boundary records safe exception class/text only
            db.rollback()
            last_exc = exc
            transient = _transient_exception(exc)
            _event(state, state_path, "production.attempt_failed", attempt=attempt, transient=transient, error_type=type(exc).__name__, error=str(exc)[:300])
            if not transient or attempt >= args.max_production_attempts:
                raise
            time.sleep(args.retry_delay_s)
        finally:
            _restore_production_profiler(originals)
    assert last_exc is not None
    raise last_exc
def _validate_final(db: Any, state: dict[str, Any], state_path: Path, project: Any, job: Any) -> dict[str, Any]:
    started = time.perf_counter()
    final_path = Path(str(job.output_key))
    if not final_path.is_file():
        raise RuntimeError(f"final artifact missing: {final_path}")
    qc_path = final_path.with_name("final.qc.json")
    thumb_qc_path = final_path.with_name("thumbnail.qc.json")
    metadata_path = final_path.with_name("metadata.json")
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    thumb_qc = json.loads(thumb_qc_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not qc.get("qc_pass") or qc.get("failures"):
        raise RuntimeError(f"final QC failed: {qc.get('failures')}")
    if not thumb_qc.get("qc_pass"):
        raise RuntimeError("thumbnail QC failed")
    if metadata.get("contract_version") != "manual-upload-package-v1":
        raise RuntimeError("manual-upload metadata contract missing")
    publication_count = int(
        db.scalar(select(func.count()).select_from(Publication).where(Publication.project_id == project.id)) or 0
    )
    if publication_count:
        raise RuntimeError(f"unexpected publication rows: {publication_count}")
    result = {
        "project_id": project.id,
        "job_id": job.id,
        "final_path": str(final_path),
        "sha256": _sha256(final_path),
        "duration_s": float(qc.get("duration", 0.0)),
        "headline": str(thumb_qc.get("selected_headline") or ""),
        "thumbnail_placement": str((thumb_qc.get("selected_variant_qc") or {}).get("placement") or ""),
        "publication_count": publication_count,
    }
    _stage(state, state_path, "final_validation", started, **result)
    return result
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one resumable production corpus without publishing")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--chapter-from", required=True, type=float)
    parser.add_argument("--chapter-to", required=True, type=float)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--voice-id", default=DEFAULT_ENGLISH_VOICE_ID)
    parser.add_argument("--watermark", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--watermark-text", default="")
    parser.add_argument("--max-analysis-attempts", type=int, default=2)
    parser.add_argument("--max-production-attempts", type=int, default=2)
    parser.add_argument("--retry-delay-s", type=float, default=15.0)
    parser.add_argument("--min-free-gb", type=float, default=2.0)
    parser.add_argument("--speed", type=float, default=1.15)
    parser.add_argument("--encoder", default="auto")
    parser.add_argument("--profile", default="Auto")
    parser.add_argument("--state-dir", default=str(Path(settings.data_dir) / "production-runs"))
    args = parser.parse_args()
    if args.chapter_to < args.chapter_from:
        parser.error("--chapter-to must be >= --chapter-from")
    if int(args.chapter_from) != args.chapter_from or int(args.chapter_to) != args.chapter_to:
        parser.error("production-run currently requires whole-number chapter bounds")
    if args.max_analysis_attempts < 1 or args.max_production_attempts < 1:
        parser.error("attempt counts must be >= 1")
    args.watermark_text = str(args.watermark_text or "").strip() if args.watermark else ""
    if args.watermark and not args.watermark_text:
        parser.error("--watermark requires nonempty --watermark-text")
    if len(args.watermark_text) > 120:
        parser.error("--watermark-text must be at most 120 characters")
    return args


def main() -> int:
    args = _parse_args()
    state_dir = Path(args.state_dir)
    state_path = state_dir / f"{args.run_id}.json"
    lock_path = state_dir / f"{args.run_id}.lock"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"Run {args.run_id} is already active", file=sys.stderr)
        return 3
    state = _load_state(state_path, args)
    if state.get("status") == "PASS":
        if state.pop("failure", None) is not None:
            _json_write(state_path, state)
        print(json.dumps(state, indent=2, ensure_ascii=False, default=str))
        return 0
    total_started = time.perf_counter()
    db = SessionLocal()
    try:
        state["status"] = "RUNNING"
        state["started_or_resumed_at"] = time.time()
        _json_write(state_path, state)
        user, workspace = _preflight(db, args, state, state_path)
        project = _ensure_project(db, args, state, state_path, user, workspace)
        _ensure_source(db, args, state, state_path, user, project)
        _ensure_analysis(db, args, state, state_path, user, project)
        script = _ensure_script(db, state, state_path, user, project)
        job = _ensure_production(db, args, state, state_path, user, project, script)
        result = _validate_final(db, state, state_path, project, job)
        state["status"] = "PASS"
        state.pop("failure", None)
        state["result"] = result
        state["execution_wall_s"] = round(time.perf_counter() - total_started, 6)
        _event(state, state_path, "run.complete", status="PASS", execution_wall_s=state["execution_wall_s"])
        print("PRODUCTION_RUN_PASS " + json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level durable failure record
        db.rollback()
        state["status"] = "FAILED"
        state["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
            "traceback": traceback.format_exc(limit=20),
        }
        state["execution_wall_s"] = round(time.perf_counter() - total_started, 6)
        _event(state, state_path, "run.failed", error_type=type(exc).__name__, message=str(exc)[:300])
        print(f"PRODUCTION_RUN_FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        db.close()
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
