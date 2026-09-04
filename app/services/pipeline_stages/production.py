"""Implementation details for the production pipeline stage.

Public callers should continue importing app.services.pipeline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.constants import (
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
)


def _write_manual_upload_metadata(api, db, project, script, job, thumbnail_manifest):
    """Persist the upload-ready metadata package beside the final artifact."""
    from app.services.youtube_metadata import build_metadata

    if not getattr(job, "output_key", ""):
        raise api.PipelineError("manual upload metadata requires a final video artifact")
    assets = api.project_assets(db, project.id)
    attributions = sorted(
        {str(asset.attribution).strip() for asset in assets if str(asset.attribution or "").strip()}
    )
    output_dir = Path(job.output_key).parent
    thumbnail_path = str((thumbnail_manifest or {}).get("thumbnail_path", "") or "")
    subtitle_path = str(getattr(job, "subtitle_key", "") or "")
    identity_payload = {
        "contract_version": "manual-upload-package-v1",
        "project_title": str(project.title or ""),
        "manhwa_title": str(project.manhwa_title or ""),
        "chapter": str(project.chapter or ""),
        "script_text": str(script.plain_text or ""),
        "attribution": "; ".join(attributions),
        "language": str(project.language or "en"),
        "video_checksum": str(getattr(job, "checksum", "") or ""),
        "thumbnail": Path(thumbnail_path).name if thumbnail_path else "",
        "subtitles": Path(subtitle_path).name if subtitle_path else "",
    }
    identity = hashlib.sha256(
        json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    target = output_dir / "metadata.json"
    identity_path = output_dir / "metadata.identity.json"
    try:
        if target.is_file() and identity_path.is_file():
            cached_identity = json.loads(identity_path.read_text(encoding="utf-8"))
            cached_payload = json.loads(target.read_text(encoding="utf-8"))
            if (
                isinstance(cached_identity, dict)
                and cached_identity.get("identity") == identity
                and isinstance(cached_payload, dict)
                and cached_payload.get("contract_version") == "manual-upload-package-v1"
                and cached_payload.get("project_id") == str(project.id)
            ):
                return target, cached_payload
    except (OSError, json.JSONDecodeError):
        pass
    generated = build_metadata(
        project_title=project.title,
        manhwa_title=project.manhwa_title,
        chapter=project.chapter,
        script_text=script.plain_text,
        attribution="; ".join(attributions),
        language=project.language,
    )
    if not thumbnail_path and getattr(job, "thumbnail_key", ""):
        thumbnail_path = str(job.thumbnail_key)
    payload = {
        "contract_version": "manual-upload-package-v1",
        "project_id": str(project.id),
        "language": str(project.language or "en"),
        "title": generated["title"],
        "description": generated["description"],
        "tags": list(generated["tags"]),
        "video": Path(job.output_key).name,
        "thumbnail": Path(thumbnail_path).name if thumbnail_path else "",
        "subtitles": Path(subtitle_path).name if subtitle_path else "",
    }
    temporary = output_dir / ".metadata.json.tmp"
    identity_tmp = output_dir / ".metadata.identity.json.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        identity_tmp.write_text(
            json.dumps({"identity": identity}, indent=2) + "\n", encoding="utf-8"
        )
        identity_tmp.replace(identity_path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        identity_tmp.unlink(missing_ok=True)
        raise api.PipelineError(f"manual upload metadata could not be written: {exc}") from exc
    return target, payload


def run_production(api, db, project_id, *, actor_id, approved_script_hash, approved_script_version, speed, provider_name, encoder, profile):
    """Run the explicit, local production path through post-render QC.

    This is intentionally separate from the review-only cloud workflow.  The
    caller must provide the hash and version that the operator approved.  Each
    boundary is durable and re-used only when it still belongs to that exact
    script; a changed script therefore cannot inherit an older voice, timeline,
    or render artifact.
    """
    JobStatus = api.JobStatus
    PipelineError = api.PipelineError
    _approved_adaptive_reference_policy = api._approved_adaptive_reference_policy
    _audio_stage_ready = api._audio_stage_ready
    _ensure_final_thumbnail = api._ensure_final_thumbnail
    _persist_production_metadata = api._persist_production_metadata
    _render_stage_ready = api._render_stage_ready
    _render_output_identity = api._render_output_identity
    _script_content_hash = api._script_content_hash
    _script_for_media = api._script_for_media
    _timeline_stage_ready = api._timeline_stage_ready
    build_timeline = api.build_timeline
    enqueue_render = api.enqueue_render
    execute_render = api.execute_render
    generate_voiceover = api.generate_voiceover
    get_project = api.get_project
    reference_profile = api.reference_profile
    run_quality_checks = api.run_quality_checks
    tts_svc = api.tts_svc
    if not actor_id.strip():
        raise PipelineError('production operator identity is required')
    script = _script_for_media(db, project_id)
    script_hash = _script_content_hash(script)
    if str(approved_script_hash).strip() != script_hash or approved_script_version is None or int(approved_script_version) != int(script.version):
        raise PipelineError('production approval does not match the latest script')
    metadata = dict(script.editorial_metadata or {})
    production = dict(metadata.get('production') or {})
    if production.get('script_hash') != script_hash:
        production = {'script_hash': script_hash, 'script_version': script.version}
    adaptive_policy = _approved_adaptive_reference_policy(script)
    if adaptive_policy is not None:
        lower = float(adaptive_policy['target_duration_min_s'])
        upper = float(adaptive_policy['target_duration_max_s'])
        if (
            lower < STANDARD_FINAL_DURATION_MIN_SECONDS
            or upper > STANDARD_FINAL_DURATION_MAX_SECONDS
        ):
            raise PipelineError(
                'production duration policy must stay within the standard 50-60 second window; '
                'adaptive shortfall is review-only and requires more grounded visual capacity'
            )
    existing = _render_stage_ready(db, project_id, script_hash)
    if existing is not None:
        results = run_quality_checks(db, project_id, job=existing, actor_id=actor_id)
        if not any(result.blocking for result in results):
            thumbnail_manifest = _ensure_final_thumbnail(db, existing, script=script, required=True)
            if thumbnail_manifest is not None:
                metadata_path, upload_metadata = _write_manual_upload_metadata(
                    api, db, project=get_project(db, project_id), script=script,
                    job=existing, thumbnail_manifest=thumbnail_manifest,
                )
                production.update({
                    'render_output_identity': _render_output_identity(get_project(db, project_id)),
                    'thumbnail_status': 'passed',
                    'thumbnail_path': thumbnail_manifest.get('thumbnail_path', ''),
                    'thumbnail_headline': thumbnail_manifest.get('headline', ''),
                    'upload_metadata_status': 'passed',
                    'upload_metadata_path': str(metadata_path),
                    'upload_metadata_title': upload_metadata.get('title', ''),
                })
                metadata = _persist_production_metadata(db, script, metadata, production)
            return existing
        # A stricter/new QC contract may invalidate a previously successful
        # artifact even when the approved script is unchanged.  Do not strand
        # production behind the stale render: fall through and rebuild the
        # timeline/render under the current policy.
        existing = None
    project = get_project(db, project_id)
    resolved_profile = reference_profile.resolve_reference_profile(project.template)
    audio_duration_bounds = (
        (float(resolved_profile.duration_min_s), float(resolved_profile.duration_max_s))
        if resolved_profile is not None
        else None
    )
    audio_timing_identity = {
        'version': tts_svc.PRODUCTION_AUDIO_TIMING_POLICY_VERSION,
        'requested_speed': round(float(speed), 4),
        'provider_override': str(provider_name or ''),
        'duration_bounds_s': list(audio_duration_bounds) if audio_duration_bounds is not None else [],
    }
    audio_reusable = bool(
        production.get('audio_script_hash') == script_hash
        and production.get('audio_timing_identity') == audio_timing_identity
        and _audio_stage_ready(db, script)
    )
    if not audio_reusable:
        generate_voiceover(
            db,
            project_id,
            speed=speed,
            provider_name=provider_name,
            actor_id=actor_id,
            duration_bounds_s=audio_duration_bounds,
        )
    production['audio_script_hash'] = script_hash
    production['audio_timing_identity'] = audio_timing_identity
    metadata = _persist_production_metadata(db, script, metadata, production)
    bounds = (
        (float(adaptive_policy['target_duration_min_s']), float(adaptive_policy['target_duration_max_s']))
        if adaptive_policy is not None
        else None
    )
    standard_reference_production = bool(
        resolved_profile is not None and adaptive_policy is None
    )
    timeline_planning_identity = {
        'version': reference_profile.PRODUCTION_REFERENCE_CADENCE_POLICY_VERSION,
        'profile_id': str(getattr(resolved_profile, 'profile_id', '') or ''),
        'profile_version': str(getattr(resolved_profile, 'version', '') or ''),
        'standard_reference_production': standard_reference_production,
        'adaptive_reference_production': adaptive_policy is not None,
        'duration_bounds_s': (
            list(bounds)
            if bounds is not None
            else list(audio_duration_bounds or ())
        ),
    }
    timeline_reusable = bool(
        production.get('timeline_script_hash') == script_hash
        and production.get('timeline_planning_identity') == timeline_planning_identity
        and _timeline_stage_ready(db, project_id)
    )
    if not timeline_reusable:
        build_timeline(
            db,
            project_id,
            actor_id=actor_id,
            allow_conservative_full_panel=resolved_profile is not None,
            adaptive_reference_production=adaptive_policy is not None,
            adaptive_reference_duration_bounds_s=bounds,
            standard_reference_production=standard_reference_production,
        )
    production['timeline_script_hash'] = script_hash
    production['timeline_planning_identity'] = timeline_planning_identity
    metadata = _persist_production_metadata(db, script, metadata, production)
    preflight = run_quality_checks(db, project_id, actor_id=actor_id)
    if any(result.blocking for result in preflight):
        raise PipelineError('pre-render QC failed: ' + '; '.join(result.message for result in preflight if result.blocking)[:1000])
    job = enqueue_render(db, project_id, kind='final', actor_id=actor_id, encoder=encoder, profile=profile)
    job = execute_render(db, job.id)
    if job.status != JobStatus.SUCCEEDED:
        raise PipelineError(f'final render failed: {job.error_message or job.error_code}')
    postflight = run_quality_checks(db, project_id, job=job, actor_id=actor_id)
    if any(result.blocking for result in postflight):
        raise PipelineError('post-render QC failed: ' + '; '.join(result.message for result in postflight if result.blocking)[:1000])
    thumbnail_manifest = _ensure_final_thumbnail(db, job, script=script, required=True)
    metadata_path, upload_metadata = _write_manual_upload_metadata(
        api, db, project=project, script=script, job=job,
        thumbnail_manifest=thumbnail_manifest,
    )
    production.update({
        'script_hash': script_hash,
        'script_version': script.version,
        'render_job_id': job.id,
        'render_output_identity': _render_output_identity(project),
        'post_render_qc': 'passed',
        'thumbnail_status': 'passed' if thumbnail_manifest is not None else 'disabled',
        'thumbnail_path': (thumbnail_manifest or {}).get('thumbnail_path', ''),
        'thumbnail_headline': (thumbnail_manifest or {}).get('headline', ''),
        'upload_metadata_status': 'passed',
        'upload_metadata_path': str(metadata_path),
        'upload_metadata_title': upload_metadata.get('title', ''),
    })
    _persist_production_metadata(db, script, metadata, production)
    return job
