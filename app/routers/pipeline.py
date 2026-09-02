"""Pipeline routes: analysis, script, voice, timeline, quality, render.

Covers PRD FR-03 through FR-09. Long-running work (render) is queued and
executed by the worker, so these endpoints stay fast and the UI polls status.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from app.constants import JobStatus, UploadStatus
from app.deps import CurrentUser, DbSession, OwnedProject
from app.models import (
    AudioSegment,
    QCOverrideEvent,
    QualityCheck,
    RenderJob,
    ScriptVersion,
    StoryAnalysis,
    SubtitleCue,
    TimelineScene,
)
from app.routing import CommitRoute
from app.schemas import (
    AnalysisOut,
    AnalysisRequest,
    AnalysisStatusOut,
    AnalysisUpdate,
    AudioSegmentOut,
    CueOut,
    CueUpdate,
    DraftOut,
    OverrideRequest,
    ProjectPipelineStatusOut,
    ProjectRunRequest,
    QCHistorySnapshotOut,
    QCOverrideEventOut,
    QualityCheckOut,
    QualitySummaryOut,
    RenderJobOut,
    RenderRequestIn,
    SceneOut,
    SceneUpdate,
    ScriptApproveRequest,
    ScriptGenerateRequest,
    ScriptOut,
    ScriptUpdate,
    VoiceAuditionOut,
    VoiceAuditionRequest,
    VoiceRequest,
)
from app.services import pipeline as pl
from app.services import publish as publish_svc
from app.services import quality as quality_svc
from app.services import timeline as timeline_svc
from app.services import voice_auditions as audition_svc

router = APIRouter(
    prefix="/api/projects/{project_id}", tags=["pipeline"], route_class=CommitRoute
)


def _guard(fn, *args, **kwargs):
    """Translate PipelineError into a 422 with the user-facing message."""
    try:
        return fn(*args, **kwargs)
    except pl.PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _analysis_view(row: StoryAnalysis) -> dict:
    payload = AnalysisOut.model_validate(row).model_dump()
    payload["blocking_reasons"] = row.blocking_reasons_json or {}
    reconciliation = row.reconciliation_json if isinstance(row.reconciliation_json, dict) else {}
    identity = reconciliation.get("narrative_identity")
    if isinstance(identity, dict):
        payload["narrative_profile_id"] = identity.get("profile_id")
        payload["narrative_profile_version"] = identity.get("version")
        payload["narrative_profile_sha256"] = identity.get("sha256")
    payload["narrative_screening_warning_codes"] = [
        code
        for code in reconciliation.get("narrative_screening_warning_codes", [])
        if isinstance(code, str)
    ] if isinstance(reconciliation.get("narrative_screening_warning_codes"), list) else []
    return payload


# --- analysis (FR-03) ------------------------------------------------------


@router.post("/analysis", response_model=AnalysisOut)
def run_analysis(
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
    payload: AnalysisRequest | None = None,
) -> dict:
    return _analysis_view(
        _guard(
            pl.run_analysis,
            db,
            project.id,
            user.id,
            narrative_profile_id=(
                payload.narrative_profile_id if payload is not None else None
            ),
        )
    )


@router.get("/analysis", response_model=AnalysisOut)
def get_analysis(project: OwnedProject, db: DbSession) -> StoryAnalysis:
    row = pl.latest_analysis(db, project.id)
    if row is None:
        raise HTTPException(status_code=404, detail="No analysis yet. Run it first.")
    return _analysis_view(row)


@router.get("/analysis/status", response_model=AnalysisStatusOut)
def get_analysis_status(project: OwnedProject, db: DbSession) -> dict:
    summary = pl.analysis_status(db, project.id)
    if summary is None:
        raise HTTPException(status_code=404, detail="No analysis yet. Run it first.")
    return summary


@router.patch("/analysis", response_model=AnalysisOut)
def update_analysis(
    payload: AnalysisUpdate,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> StoryAnalysis:
    """Apply user corrections to extracted facts (FR-03: results are editable)."""
    row = pl.latest_analysis(db, project.id)
    if row is None:
        raise HTTPException(status_code=404, detail="No analysis yet. Run it first.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.edited_by_user = True
    pl.audit(db, "analysis.update", "project", project.id, user.id)
    db.flush()
    return _analysis_view(row)


# --- script (FR-04) -------------------------------------------------------


@router.post("/script", response_model=ScriptOut)
def generate_script(
    payload: ScriptGenerateRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> ScriptVersion:
    return _guard(
        pl.generate_script,
        db,
        project.id,
        keep_locked=payload.keep_locked,
        hook_count=payload.hook_count,
        seed=payload.seed,
        actor_id=user.id,
        narrative_profile_id=payload.narrative_profile_id,
    )


@router.get("/script", response_model=ScriptOut)
def get_script(project: OwnedProject, db: DbSession) -> ScriptVersion:
    script = pl.latest_script_row(db, project.id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script yet. Generate one first.")
    return script


@router.get("/scripts", response_model=list[ScriptOut])
def list_scripts(project: OwnedProject, db: DbSession) -> list[ScriptVersion]:
    return pl.all_scripts(db, project.id)


@router.patch("/script", response_model=ScriptOut)
def update_script(
    payload: ScriptUpdate,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> ScriptVersion:
    script = pl.latest_script_row(db, project.id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script yet. Generate one first.")
    return _guard(
        pl.update_script,
        db,
        script.id,
        [s.model_dump() for s in payload.sections],
        selected_hook=payload.selected_hook,
        actor_id=user.id,
    )


@router.post("/script/approve", response_model=ScriptOut)
def approve_script(
    payload: ScriptApproveRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> ScriptVersion:
    script = pl.latest_script_row(db, project.id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script yet. Generate one first.")
    return _guard(
        pl.approve_script,
        db,
        script.id,
        user.id,
        editorial_review_confirmed=payload.editorial_review_confirmed,
    )


# --- voice (FR-05) --------------------------------------------------------


@router.post("/voice", response_model=list[AudioSegmentOut])
def generate_voice(
    payload: VoiceRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> list[AudioSegment]:
    return _guard(
        pl.generate_voiceover,
        db,
        project.id,
        speed=payload.speed,
        provider_name=payload.provider,
        actor_id=user.id,
    )


@router.get("/voice", response_model=list[AudioSegmentOut])
def list_voice(project: OwnedProject, db: DbSession) -> list[AudioSegment]:
    script = pl.current_script(db, project.id)
    if script is None:
        return []
    return pl.audio_segments(db, script.id)


@router.post("/voice/auditions", response_model=VoiceAuditionOut)
def generate_voice_auditions(
    payload: VoiceAuditionRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    return _guard(
        audition_svc.generate_auditions,
        db,
        project.id,
        payload.voice_ids,
        payload.speed,
        user.id,
    )


@router.get("/voice/auditions/{audition_id}.wav")
def download_voice_audition(audition_id: str, project: OwnedProject):
    try:
        path = audition_svc.audition_path(project.id, audition_id)
    except audition_svc.VoiceAuditionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Voice audition not found.")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{project.id}_{audition_id[:16]}.wav",
    )


# --- timeline and subtitles (FR-06, FR-07) --------------------------------


@router.post("/timeline", response_model=list[SceneOut])
def build_timeline(
    project: OwnedProject, db: DbSession, user: CurrentUser
) -> list[TimelineScene]:
    return _guard(pl.build_timeline, db, project.id, user.id)


@router.get("/timeline", response_model=list[SceneOut])
def get_timeline(project: OwnedProject, db: DbSession) -> list[TimelineScene]:
    return pl.project_scenes(db, project.id)


@router.patch("/timeline/{scene_id}", response_model=SceneOut)
def update_scene(
    scene_id: str,
    payload: SceneUpdate,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> TimelineScene:
    """Edit one scene: swap image, retarget the crop, change the effect."""
    scene = db.get(TimelineScene, scene_id)
    if scene is None or scene.project_id != project.id:
        raise HTTPException(status_code=404, detail="Scene not found.")

    changes = payload.model_dump(exclude_unset=True)
    if "asset_id" in changes and changes["asset_id"]:
        valid = {a.id for a in pl.project_assets(db, project.id)}
        if changes["asset_id"] not in valid:
            raise HTTPException(
                status_code=422, detail="That asset does not belong to this project."
            )
    for field, value in changes.items():
        setattr(scene, field, value)
    if scene.end_time <= scene.start_time:
        raise HTTPException(status_code=422, detail="Scene end must be after its start.")

    pl.audit(db, "scene.update", "scene", scene.id, user.id, fields=sorted(changes))
    db.flush()
    return scene


@router.get("/subtitles", response_model=list[CueOut])
def get_subtitles(project: OwnedProject, db: DbSession) -> list[SubtitleCue]:
    return pl.project_cues(db, project.id)


@router.patch("/subtitles/{cue_id}", response_model=CueOut)
def update_cue(
    cue_id: str,
    payload: CueUpdate,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> SubtitleCue:
    cue = db.get(SubtitleCue, cue_id)
    if cue is None or cue.project_id != project.id:
        raise HTTPException(status_code=404, detail="Subtitle cue not found.")
    changes = payload.model_dump(exclude_unset=True)
    if "text" in changes:
        display_text = timeline_svc.normalize_display_text(changes["text"])
        if not display_text:
            raise HTTPException(status_code=422, detail="Subtitle text must contain a display word.")
        if len(display_text.split()) != 1:
            raise HTTPException(status_code=422, detail="Subtitle text must contain exactly one display word.")
        changes["text"] = display_text
    for field, value in changes.items():
        setattr(cue, field, value)
    if cue.end_time <= cue.start_time:
        raise HTTPException(status_code=422, detail="Cue end must be after its start.")
    cue.edited_by_user = True
    pl.audit(db, "subtitle.update", "cue", cue.id, user.id)
    db.flush()
    return cue


@router.get("/subtitles.srt")
def download_srt(project: OwnedProject, db: DbSession):
    """Export subtitles as an SRT file (FR-07)."""
    from fastapi.responses import PlainTextResponse

    from app.services.timeline import to_srt

    cues = pl.cue_specs(pl.project_cues(db, project.id))
    if not cues:
        raise HTTPException(status_code=404, detail="No subtitles yet.")
    return PlainTextResponse(
        to_srt(cues),
        headers={"Content-Disposition": f'attachment; filename="{project.id}.srt"'},
    )


# --- local orchestration ---------------------------------------------------


_STAGE_ORDER = {name: index for index, name in enumerate(
    ("analysis", "draft", "voice", "timeline", "quality", "render", "publish")
)}


def _latest_row_time(rows):
    values = []
    for row in rows:
        value = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
        if value is not None:
            values.append(float(value.timestamp()))
    return max(values, default=None)


def _project_pipeline_status(project, db) -> dict:
    analysis = pl.latest_analysis(db, project.id)
    script = pl.latest_script_row(db, project.id)
    script_approved = bool(script is not None and pl.script_is_media_ready(db, project.id))

    segments = pl.audio_segments(db, script.id) if script is not None else []
    scenes = pl.project_scenes(db, project.id)
    checks = pl.project_quality_checks(db, project.id)
    render = pl.latest_render(db, project.id, "final")
    publications = publish_svc.project_publications(db, project.id)
    publication = publications[0] if publications else None
    published = bool(publication is not None and str(publication.upload_status) == UploadStatus.UPLOADED)

    voice_ready = bool(segments)
    voice_time = _latest_row_time(segments)
    scene_time = _latest_row_time(scenes)
    timeline_ready = bool(
        scenes
        and voice_ready
        and scene_time is not None
        and (voice_time is None or scene_time >= voice_time)
    )
    check_time = _latest_row_time(checks)
    quality_current = bool(
        checks
        and timeline_ready
        and check_time is not None
        and (scene_time is None or check_time >= scene_time)
    )
    blocking_codes = [
        str(check.code)
        for check in checks
        if quality_current and str(check.severity) == "error" and not bool(check.passed)
    ]
    quality_ready = quality_current and not blocking_codes
    render_time = _latest_row_time([render]) if render is not None else None
    render_current = bool(
        render is not None
        and quality_ready
        and render_time is not None
        and (check_time is None or render_time >= check_time)
    )

    if published:
        current_stage = "published"
    elif render_current and str(render.status) == JobStatus.SUCCEEDED:
        current_stage = "complete"
    elif render_current:
        current_stage = "render"
    elif quality_current:
        current_stage = "quality"
    elif timeline_ready:
        current_stage = "timeline"
    elif voice_ready:
        current_stage = "voice"
    elif script is not None:
        current_stage = "script_review" if not script_approved else "draft"
    else:
        current_stage = "analysis"

    action_required = None
    if script is not None and not script_approved:
        action_required = "script_approval"
    elif blocking_codes:
        action_required = "quality_blocked"
    elif render_current and str(render.status) in {JobStatus.FAILED, JobStatus.CANCELLED}:
        action_required = "render_retry"
    elif publication is not None and str(publication.upload_status) == UploadStatus.FAILED:
        action_required = "publish_retry"

    downloadable = bool(
        render_current and str(render.status) == JobStatus.SUCCEEDED and render.output_key
    )
    return {
        "project_id": project.id,
        "project_status": str(project.status),
        "current_stage": current_stage,
        "action_required": action_required,
        "analysis_state": str(analysis.state) if analysis is not None and analysis.state else None,
        "script_id": script.id if script is not None else None,
        "script_version": int(script.version) if script is not None else None,
        "script_approved": script_approved,
        "voice_ready": voice_ready,
        "timeline_ready": timeline_ready,
        "quality_ready": quality_ready,
        "quality_blocking_codes": blocking_codes,
        "render_job_id": render.id if render is not None else None,
        "render_status": str(render.status) if render is not None else None,
        "render_progress": int(render.progress) if render is not None else 0,
        "render_stage": str(render.stage or "") if render is not None else "",
        "download_url": (
            f"/api/projects/{project.id}/download/{render.id}" if downloadable else None
        ),
        "publication_id": publication.id if publication is not None else None,
        "publish_status": str(publication.upload_status) if publication is not None else None,
        "youtube_video_id": publication.youtube_video_id if publication is not None and publication.youtube_video_id else None,
        "youtube_url": (
            f"https://www.youtube.com/watch?v={publication.youtube_video_id}"
            if publication is not None and publication.youtube_video_id and not publication.youtube_video_id.startswith("dryrun_")
            else None
        ),
        "thumbnail_upload_status": publication.thumbnail_status if publication is not None else None,
        "thumbnail_upload_note": publication.thumbnail_note if publication is not None else "",
        "thumbnail_retry_url": publication.thumbnail_retry_url if publication is not None else None,
        "_quality_current": quality_current,
        "_render_current": render_current,
    }


@router.get("/status", response_model=ProjectPipelineStatusOut)
def get_pipeline_status(project: OwnedProject, db: DbSession) -> dict:
    """Return one compact, polling-friendly view of the project's pipeline state."""
    return _project_pipeline_status(project, db)


@router.post("/run", response_model=ProjectPipelineStatusOut)
def run_pipeline(
    payload: ProjectRunRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
    background: BackgroundTasks,
) -> dict:
    """Advance the existing pipeline to ``until`` and safely resume completed stages."""
    target = _STAGE_ORDER[payload.until]
    analysis = pl.latest_analysis(db, project.id)
    if analysis is None or str(analysis.state) not in {"RECONCILED", "SCRIPT_DRAFT", "SCRIPT_APPROVED"}:
        _guard(
            pl.run_analysis,
            db,
            project.id,
            user.id,
            narrative_profile_id=payload.narrative_profile_id,
        )
        db.flush()
    if target == _STAGE_ORDER["analysis"]:
        return _project_pipeline_status(project, db)

    script = pl.latest_script_row(db, project.id)
    if script is None:
        _guard(
            pl.generate_script,
            db,
            project.id,
            seed=payload.seed,
            actor_id=user.id,
            narrative_profile_id=payload.narrative_profile_id,
        )
        db.flush()
        script = pl.latest_script_row(db, project.id)
    if target == _STAGE_ORDER["draft"]:
        return _project_pipeline_status(project, db)

    status_view = _project_pipeline_status(project, db)
    if not status_view["script_approved"]:
        trusted_publish = bool(
            payload.until == "publish"
            and payload.approval_mode == "trusted_agent"
            and payload.confirm_publish_intent is True
            and script is not None
        )
        if not trusted_publish:
            return status_view
        _guard(
            pl.approve_script,
            db,
            script.id,
            user.id,
            editorial_review_confirmed=True,
            approval_actor_type="trusted_agent",
            approval_reason="explicit_user_publish_request",
        )
        db.commit()

    if script is not None and not pl.audio_segments(db, script.id):
        _guard(pl.generate_voiceover, db, project.id, actor_id=user.id)
        db.flush()
    if target == _STAGE_ORDER["voice"]:
        return _project_pipeline_status(project, db)

    status_view = _project_pipeline_status(project, db)
    if not status_view["timeline_ready"]:
        _guard(pl.build_timeline, db, project.id, user.id)
        db.flush()
    if target == _STAGE_ORDER["timeline"]:
        return _project_pipeline_status(project, db)

    status_view = _project_pipeline_status(project, db)
    if not status_view["quality_ready"] and status_view["action_required"] != "quality_blocked":
        _guard(pl.run_quality_checks, db, project.id, None, user.id)
        db.flush()
    status_view = _project_pipeline_status(project, db)
    if target == _STAGE_ORDER["quality"] or status_view["quality_blocking_codes"]:
        return status_view

    if not status_view["_render_current"]:
        render = _guard(pl.enqueue_render, db, project.id, "final", user.id, "auto", "Auto")
        job_id = render.id
        db.commit()
        background.add_task(_run_render_task, job_id)
        return _project_pipeline_status(project, db)

    if target == _STAGE_ORDER["render"]:
        return status_view
    if str(status_view.get("render_status") or "") != JobStatus.SUCCEEDED:
        return status_view
    if status_view.get("publish_status") == UploadStatus.UPLOADED:
        return status_view

    _guard(
        publish_svc.publish,
        db,
        project.id,
        channel_id=payload.channel_id,
        video_title="",
        description="",
        tags=[],
        privacy_status=payload.privacy_status,
        scheduled_at=payload.scheduled_at,
        confirm_public=payload.confirm_public,
        actor_id=user.id,
    )
    return _project_pipeline_status(project, db)


# --- draft shortcut -------------------------------------------------------


@router.post("/draft", response_model=DraftOut)
def generate_draft(
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
    seed: int | None = None,
) -> dict:
    """Generate a script draft from an already reconciled vision analysis."""
    return _guard(pl.generate_draft, db, project.id, user.id, seed)


# --- quality (FR-08) ------------------------------------------------------


@router.post("/quality", response_model=QualitySummaryOut)
def run_quality(project: OwnedProject, db: DbSession, user: CurrentUser) -> dict:
    job = pl.latest_render(db, project.id, "final")
    results = _guard(pl.run_quality_checks, db, project.id, job, user.id)
    summary = quality_svc.summarise(results)
    summary["checks"] = pl.project_quality_checks(db, project.id)
    return summary


@router.get("/quality", response_model=list[QualityCheckOut])
def get_quality(project: OwnedProject, db: DbSession) -> list[QualityCheck]:
    return pl.project_quality_checks(db, project.id)


@router.post("/quality/override", response_model=QualityCheckOut)
def override_check(
    payload: OverrideRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> QualityCheck:
    """Accept a warning with a recorded reason. Errors cannot be overridden."""
    return _guard(pl.override_warning, db, project.id, payload.code, payload.reason, user.id)


@router.get("/quality/overrides", response_model=list[QCOverrideEventOut])
def get_quality_overrides(project: OwnedProject, db: DbSession) -> list[QCOverrideEvent]:
    return pl.project_qc_overrides(db, project.id)


@router.get("/quality/history", response_model=list[QCHistorySnapshotOut])
def get_quality_history(project: OwnedProject, db: DbSession) -> list:
    return pl.project_qc_history(db, project.id)


# --- render (FR-09) -------------------------------------------------------


@router.post("/render", response_model=RenderJobOut)
def enqueue_render(
    payload: RenderRequestIn,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
    background: BackgroundTasks,
) -> RenderJob:
    job = _guard(pl.enqueue_render, db, project.id, payload.kind, user.id, payload.encoder, payload.profile)
    job_id = job.id
    db.commit()

    # Run inline for the single-process local setup. A separate worker process
    # (scripts/worker.py) can also pick queued jobs up; execute_render is
    # guarded so a job is never rendered twice.
    background.add_task(_run_render_task, job_id)
    return job


def _run_render_task(job_id: str) -> None:
    from app.db import session_scope

    # execute_render records failures on the job row itself, which the client
    # polls; there is nowhere to propagate an exception to from here.
    with session_scope() as db, contextlib.suppress(pl.PipelineError):
        pl.execute_render(db, job_id)


@router.get("/render", response_model=list[RenderJobOut])
def list_renders(project: OwnedProject, db: DbSession) -> list[RenderJob]:
    return pl.all_render_jobs(db, project.id)


@router.get("/render/{job_id}", response_model=RenderJobOut)
def get_render(job_id: str, project: OwnedProject, db: DbSession) -> RenderJob:
    job = db.get(RenderJob, job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(status_code=404, detail="Render job not found.")
    return job


@router.post("/render/{job_id}/retry", response_model=RenderJobOut)
def retry_render(
    job_id: str,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
    background: BackgroundTasks,
) -> RenderJob:
    old = db.get(RenderJob, job_id)
    if old is None or old.project_id != project.id:
        raise HTTPException(status_code=404, detail="Render job not found.")
    job = _guard(pl.retry_render, db, job_id, user.id)
    new_id = job.id
    db.commit()
    background.add_task(_run_render_task, new_id)
    return job


@router.get("/download/{job_id}")
def download_video(job_id: str, project: OwnedProject, db: DbSession):
    """Download the rendered MP4 (FR-09)."""
    job = db.get(RenderJob, job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(status_code=404, detail="Render job not found.")
    if job.status != JobStatus.SUCCEEDED or not job.output_key:
        raise HTTPException(status_code=409, detail="This render has no output file.")
    path = Path(job.output_key)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="The rendered file is no longer on disk.")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{project.id}_{job.kind}.mp4",
    )


@router.get("/download/{job_id}/thumbnail")
def download_thumbnail(job_id: str, project: OwnedProject, db: DbSession):
    """Download the auto-generated upload thumbnail for a successful final render."""
    job = db.get(RenderJob, job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(status_code=404, detail="Render job not found.")
    if job.status != JobStatus.SUCCEEDED or not job.output_key:
        raise HTTPException(status_code=409, detail="This render has no publishable thumbnail.")
    path = Path(job.output_key).parent / "thumbnail.jpg"
    if not path.is_file():
        raise HTTPException(status_code=410, detail="The thumbnail is no longer on disk.")
    return FileResponse(path, media_type="image/jpeg", filename=f"{project.id}_thumbnail.jpg")
