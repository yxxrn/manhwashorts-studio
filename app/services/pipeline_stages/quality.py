"""Implementation details for the quality pipeline stage.

Public callers should continue importing app.services.pipeline.
"""

from __future__ import annotations


def run_quality_checks(api, db, project_id, job, actor_id):
    """Run every gate and persist the results for the review UI."""
    QCHistorySnapshot = api.QCHistorySnapshot
    QualityCheck = api.QualityCheck
    _approved_adaptive_reference_policy = api._approved_adaptive_reference_policy
    asdict = api.asdict
    audio_segments = api.audio_segments
    audit = api.audit
    cue_specs = api.cue_specs
    current_script = api.current_script
    get_project = api.get_project
    project_assets = api.project_assets
    project_cues = api.project_cues
    project_scenes = api.project_scenes
    quality_svc = api.quality_svc
    reference_profile = api.reference_profile
    select = api.select
    subtitle_karaoke = api.subtitle_karaoke
    project = get_project(db, project_id)
    assets = project_assets(db, project_id)
    script = current_script(db, project_id)
    segments = audio_segments(db, script.id) if script else []
    scenes = project_scenes(db, project_id)
    cues = cue_specs(project_cues(db, project_id))
    duration = 0.0
    if segments:
        duration = max(s.end_time for s in segments)
    if job and job.duration:
        duration = job.duration
    profile = reference_profile.resolve_reference_profile(project.template)
    adaptive_reference_contract = _approved_adaptive_reference_policy(script)
    caption_groups: tuple[object, ...] | None = None
    subtitle_contract: dict[str, object] | None = None
    subtitle_timing_error: str | None = None
    if profile is not None:
        subtitle_contract = subtitle_karaoke.contract_manifest(profile)
        from app.services import render as render_svc
        try:
            caption_groups = subtitle_karaoke.build_sentence_groups_from_segments(segments)
            caption_groups = render_svc.fit_sentence_karaoke_groups(caption_groups, profile.final_width, profile.final_height, max_chars=subtitle_karaoke.CAPTION_MAX_CHARS, max_lines=subtitle_karaoke.CAPTION_MAX_LINES, active_scale=subtitle_karaoke.CAPTION_ACTIVE_SCALE, font_height_ratio=subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO, safe_margin_px=subtitle_karaoke.CAPTION_SAFE_MARGIN_PX)
        except (ValueError, render_svc.RenderError) as exc:
            subtitle_timing_error = str(exc)
    results = quality_svc.run_all(project, assets, script, segments, scenes, cues, job=job, duration=duration, caption_groups=caption_groups, subtitle_contract=subtitle_contract, subtitle_timing_error=subtitle_timing_error, adaptive_reference_contract=adaptive_reference_contract)
    for old in db.scalars(select(QualityCheck).where(QualityCheck.project_id == project_id)):
        db.delete(old)
    db.flush()
    for result in results:
        db.add(QualityCheck(project_id=project_id, code=result.code, severity=result.severity, message=result.message, passed=result.passed))
    summary = quality_svc.summarise(results)
    db.add(QCHistorySnapshot(project_id=project_id, render_job_id=job.id if job else None, passed=not any(result.blocking for result in results), report={'checks': [asdict(result) for result in results], 'summary': summary}))
    audit(db, 'quality.run', 'project', project_id, actor_id, **summary)
    db.flush()
    return results



def override_warning(api, db, project_id, code, reason, actor_id):
    """Record an explicit, attributed override for a non-blocking warning."""
    PipelineError = api.PipelineError
    QCOverrideEvent = api.QCOverrideEvent
    QualityCheck = api.QualityCheck
    audit = api.audit
    select = api.select
    if not reason.strip():
        raise PipelineError('an override reason is required')
    check = db.scalars(select(QualityCheck).where(QualityCheck.project_id == project_id, QualityCheck.code == code)).first()
    if check is None:
        raise PipelineError(f'no quality check named {code!r} for this project')
    if check.severity == 'error':
        raise PipelineError(f'{code} is a blocking error and cannot be overridden')
    check.override_reason = reason.strip()
    check.overridden_by = actor_id
    check.passed = True
    db.add(QCOverrideEvent(project_id=project_id, quality_code=code, actor_id=actor_id, reason=reason.strip(), before_passed=False, after_passed=True))
    audit(db, 'quality.override', 'project', project_id, actor_id, code=code, reason=reason.strip())
    db.flush()
    return check
