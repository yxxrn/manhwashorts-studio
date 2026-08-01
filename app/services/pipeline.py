"""Pipeline orchestration (PRD sections 6 and 10).

Each function here is one stage of the journey and is safe to re-run: stages
replace their own outputs rather than appending, so a user can regenerate the
script, the voice-over, or the timeline independently without corrupting the
others.

Stage order:

    ingest -> analyse -> script -> approve -> voice -> timeline
           -> subtitles -> quality -> render -> publish
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import JobStatus, ProjectStatus, ScriptSection
from app.models import (
    AudioSegment,
    AuditLog,
    Project,
    QualityCheck,
    RenderJob,
    ScriptVersion,
    SourceAsset,
    StoryAnalysis,
    SubtitleCue,
    TimelineScene,
)
from app.services import analysis as analysis_svc
from app.services import quality as quality_svc
from app.services import resolver as resolver_svc
from app.services import script as script_svc
from app.services import storage, visual_scoring
from app.services import timeline as timeline_svc
from app.services import tts as tts_svc


class PipelineError(RuntimeError):
    """Raised when a stage cannot proceed. Message is user-facing."""


def _now() -> datetime:
    return datetime.now(UTC)


def audit(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str,
    actor_id: str = "",
    **detail,
) -> None:
    """Append an audit entry. Never records secrets."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )


def get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise PipelineError(f"project {project_id} not found")
    return project


def project_assets(db: Session, project_id: str) -> list[SourceAsset]:
    return list(
        db.scalars(
            select(SourceAsset)
            .where(SourceAsset.project_id == project_id)
            .order_by(SourceAsset.order_index, SourceAsset.created_at)
        )
    )


def text_sources(assets: list[SourceAsset]) -> list[tuple[int, str]]:
    """Numbered text sources for the analyzer, indexed by asset position."""
    return [(i, a.extracted_text) for i, a in enumerate(assets) if a.extracted_text.strip()]


def image_assets(assets: list[SourceAsset]) -> list[SourceAsset]:
    from app.constants import AssetType

    return [a for a in assets if a.type == AssetType.IMAGE]


# --- stage: analyse --------------------------------------------------------


def run_analysis(db: Session, project_id: str, actor_id: str = "") -> StoryAnalysis:
    """Extract story facts from all text assets, replacing any prior analysis."""
    project = get_project(db, project_id)
    assets = project_assets(db, project_id)
    sources = text_sources(assets)
    if not sources:
        raise PipelineError(
            "No text material to analyse. Paste a recap or upload a TXT/MD/PDF/DOCX first."
        )

    # BYOK: a verified workspace key wins over env config, which wins over rules.
    analyzer, decision = resolver_svc.resolve_analyzer(db, project.workspace_id)
    result = analyzer.analyze(sources)
    if decision.reason:
        result.low_confidence_notes.append(f"Analysis: {decision.reason}.")

    # One analysis row per project: replace rather than accumulate.
    for old in db.scalars(select(StoryAnalysis).where(StoryAnalysis.project_id == project_id)):
        db.delete(old)

    row = StoryAnalysis(
        project_id=project_id,
        characters=[
            {"name": c.name, "role": c.role, "aliases": c.aliases, "mentions": c.mentions,
             "source_index": c.source_index}
            for c in result.characters
        ],
        locations=result.locations,
        events=[
            {"order": e.order, "text": e.text, "kind": e.kind, "source_index": e.source_index}
            for e in result.events
        ],
        main_conflict=result.main_conflict,
        twist=result.twist,
        cliffhanger=result.cliffhanger,
        pronunciation_candidates=result.pronunciation_candidates,
        low_confidence_notes=result.low_confidence_notes,
    )
    db.add(row)
    project.status = ProjectStatus.GENERATING
    audit(
        db,
        "analysis.run",
        "project",
        project_id,
        actor_id,
        generator=result.generator,
        provider_source=decision.source,
        provider=decision.provider,
        model=decision.model,
    )
    db.flush()
    return row


def _analysis_to_result(row: StoryAnalysis) -> analysis_svc.AnalysisResult:
    """Rebuild the dataclass from a stored row so edits are respected."""
    return analysis_svc.AnalysisResult(
        characters=[
            analysis_svc.Character(
                name=c.get("name", ""),
                mentions=int(c.get("mentions", 0) or 0),
                role=c.get("role", ""),
                aliases=list(c.get("aliases", []) or []),
                source_index=int(c.get("source_index", 0) or 0),
            )
            for c in (row.characters or [])
        ],
        locations=list(row.locations or []),
        events=[
            analysis_svc.StoryEvent(
                order=int(e.get("order", i) or i),
                text=e.get("text", ""),
                kind=e.get("kind", "event"),
                source_index=int(e.get("source_index", 0) or 0),
            )
            for i, e in enumerate(row.events or [])
        ],
        main_conflict=row.main_conflict,
        twist=row.twist,
        cliffhanger=row.cliffhanger,
        pronunciation_candidates=list(row.pronunciation_candidates or []),
        low_confidence_notes=list(row.low_confidence_notes or []),
    )


def latest_script_row(db: Session, project_id: str) -> ScriptVersion | None:
    """Highest-numbered script version, queried directly.

    ``Project.scripts`` is a lazy relationship: once it has been read in a
    session it stays cached, so a script added later in the same transaction
    (as in ``generate_draft``) would be invisible. Querying avoids that.
    """
    return db.scalars(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id)
        .order_by(ScriptVersion.version.desc())
        .limit(1)
    ).first()


def approved_script_row(db: Session, project_id: str) -> ScriptVersion | None:
    return db.scalars(
        select(ScriptVersion)
        .where(
            ScriptVersion.project_id == project_id,
            ScriptVersion.approved_at.is_not(None),
        )
        .order_by(ScriptVersion.version.desc())
        .limit(1)
    ).first()


def current_script(db: Session, project_id: str) -> ScriptVersion | None:
    """The script the pipeline should act on: approved if any, else latest."""
    return approved_script_row(db, project_id) or latest_script_row(db, project_id)


def all_scripts(db: Session, project_id: str) -> list[ScriptVersion]:
    """Every script version, newest first (FR-04 version history)."""
    return list(
        db.scalars(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == project_id)
            .order_by(ScriptVersion.version.desc())
        )
    )


def all_render_jobs(db: Session, project_id: str) -> list[RenderJob]:
    """Every render job, newest first."""
    return list(
        db.scalars(
            select(RenderJob)
            .where(RenderJob.project_id == project_id)
            .order_by(RenderJob.created_at.desc())
        )
    )


def latest_analysis(db: Session, project_id: str) -> StoryAnalysis | None:
    return db.scalars(
        select(StoryAnalysis)
        .where(StoryAnalysis.project_id == project_id)
        .order_by(StoryAnalysis.created_at.desc())
    ).first()


# --- stage: script ---------------------------------------------------------


def generate_script(
    db: Session,
    project_id: str,
    *,
    keep_locked: bool = True,
    hook_count: int = 3,
    seed: int | None = None,
    actor_id: str = "",
) -> ScriptVersion:
    """Create the next script version. Locked sections carry over."""
    project = get_project(db, project_id)
    row = latest_analysis(db, project_id)
    if row is None:
        row = run_analysis(db, project_id, actor_id)
    result = _analysis_to_result(row)

    previous = latest_script_row(db, project_id)
    locked: dict[str, script_svc.Section] = {}
    if keep_locked and previous:
        for section in previous.sections or []:
            if section.get("locked"):
                locked[section["section"]] = script_svc.Section(
                    section=section["section"],
                    text=section.get("text", ""),
                    locked=True,
                    citations=list(section.get("citations", []) or []),
                )

    draft = script_svc.get_generator().generate(
        result,
        style=project.narration_style,
        target_seconds=float(project.target_duration),
        spoiler_level=project.spoiler_level,
        manhwa_title=project.manhwa_title,
        chapter=project.chapter,
        cta_text=project.cta_text,
        locked=locked,
        hook_count=hook_count,
        seed=seed,
    )

    version = (previous.version + 1) if previous else 1
    script_row = ScriptVersion(
        project_id=project_id,
        version=version,
        sections=[s.to_dict() for s in draft.sections],
        hook_options=draft.hook_options,
        selected_hook=draft.selected_hook,
        estimated_duration=draft.estimated_duration,
        word_count=draft.word_count,
        warnings=draft.warnings,
        generator=draft.generator,
    )
    db.add(script_row)
    project.status = ProjectStatus.REVIEW
    audit(db, "script.generate", "project", project_id, actor_id, version=version)
    db.flush()
    return script_row


def update_script(
    db: Session,
    script_id: str,
    sections: list[dict],
    *,
    selected_hook: int | None = None,
    actor_id: str = "",
) -> ScriptVersion:
    """Apply user edits. Editing clears approval so review cannot be bypassed."""
    script = db.get(ScriptVersion, script_id)
    if script is None:
        raise PipelineError("script version not found")
    project = get_project(db, script.project_id)

    valid_sections = {s.value for s in ScriptSection}
    cleaned: list[dict] = []
    for section in sections:
        name = section.get("section")
        if name not in valid_sections:
            raise PipelineError(f"unknown script section: {name!r}")
        text = str(section.get("text", "")).strip()
        cleaned.append(
            {
                "section": name,
                "text": text,
                "locked": bool(section.get("locked", False)),
                "estimated_duration": script_svc.estimate_duration(
                    text, project.narration_style
                ),
                "citations": list(section.get("citations", []) or []),
            }
        )

    script.sections = cleaned
    if selected_hook is not None:
        script.selected_hook = max(0, min(selected_hook, max(0, len(script.hook_options) - 1)))
    script.estimated_duration = round(
        sum(s["estimated_duration"] for s in cleaned), 2
    )
    script.word_count = script_svc.word_count(script.plain_text)

    draft = script_svc.ScriptDraft(
        sections=[script_svc.Section(**s) for s in cleaned],
        estimated_duration=script.estimated_duration,
        word_count=script.word_count,
    )
    script.warnings = script_svc.check_script(draft, float(project.target_duration))

    # Any edit invalidates a previous approval.
    script.approved_at = None
    script.approved_by = ""
    audit(db, "script.update", "script_version", script.id, actor_id)
    db.flush()
    return script


def approve_script(db: Session, script_id: str, actor_id: str = "") -> ScriptVersion:
    """Mark a script reviewed. Blocking script warnings must be fixed first."""
    script = db.get(ScriptVersion, script_id)
    if script is None:
        raise PipelineError("script version not found")
    blocking = [w for w in (script.warnings or []) if w.get("severity") == "error"]
    if blocking:
        raise PipelineError(
            "Fix these before approving: "
            + "; ".join(w.get("message", w.get("code", "")) for w in blocking)
        )
    if not script.plain_text.strip():
        raise PipelineError("script is empty")

    script.approved_at = _now()
    script.approved_by = actor_id
    audit(db, "script.approve", "script_version", script.id, actor_id, version=script.version)
    db.flush()
    return script


# --- stage: voice-over -----------------------------------------------------


def generate_voiceover(
    db: Session,
    project_id: str,
    *,
    speed: float = 0.90,
    provider_name: str | None = None,
    actor_id: str = "",
) -> list[AudioSegment]:
    """Synthesise one clip per script section, replacing any previous audio."""
    project = get_project(db, project_id)
    script = current_script(db, project_id)
    if script is None:
        raise PipelineError("generate a script before the voice-over")

    # BYOK: a verified speech key wins unless the caller forced a local provider.
    provider, tts_decision = resolver_svc.resolve_tts(
        db, project.workspace_id, override=provider_name
    )
    work = storage.workspace_dir(project_id, "audio")

    # Remove old segments and their files so storage does not grow unbounded.
    existing = list(
        db.scalars(select(AudioSegment).where(AudioSegment.script_version_id == script.id))
    )
    for segment in existing:
        if not segment.user_uploaded:
            storage.delete(segment.storage_key)
        db.delete(segment)
    db.flush()

    prepared: list[tuple[int, dict, str]] = []
    for index, section in enumerate(script.sections or []):
        text = (section.get("text") or "").strip()
        if text:
            prepared.append((index, section, script_svc.apply_pronunciations(text, project.pronunciations or {})))
    if not prepared:
        raise PipelineError("script has no spoken text")

    try:
        if isinstance(provider, tts_svc.HttpProvider):
            clips = provider.synthesize_sections(
                [spoken for _, _, spoken in prepared], work, project.voice_id, speed
            )
        else:
            clips = [
                provider.synthesize(
                    spoken, work / f"{index:02d}_{section['section']}.wav", project.voice_id, speed
                )
                for index, section, spoken in prepared
            ]
    except tts_svc.TTSError as exc:
        raise PipelineError(f"voice-over failed: {exc}") from exc

    created: list[AudioSegment] = []
    for (index, section, _), clip in zip(prepared, clips, strict=True):
        text = (section.get("text") or "").strip()
        stored = storage.put_file(f"projects/{project_id}/audio", clip.path, clip.path.name)
        segment = AudioSegment(
            script_version_id=script.id,
            section=section["section"],
            order_index=index,
            text=text,
            voice_id=project.voice_id,
            provider=clip.provider,
            storage_key=stored.storage_key,
            duration=clip.duration,
            word_timings=clip.word_timings,
        )
        db.add(segment)
        created.append(segment)

    if not created:
        raise PipelineError("script has no spoken text")

    # Lay segments onto the master timeline.
    cursor = 0.0
    gap = 0.18
    for i, segment in enumerate(created):
        segment.start_time = round(cursor, 3)
        segment.end_time = round(cursor + segment.duration, 3)
        cursor = segment.end_time + (gap if i < len(created) - 1 else 0.0)

    audit(
        db, "voice.generate", "project", project_id, actor_id,
        segments=len(created), provider=provider.name,
        provider_source=tts_decision.source, model=tts_decision.model,
    )
    db.flush()
    return created


def audio_segments(db: Session, script_id: str) -> list[AudioSegment]:
    return list(
        db.scalars(
            select(AudioSegment)
            .where(AudioSegment.script_version_id == script_id)
            .order_by(AudioSegment.order_index)
        )
    )


def spans_from_segments(segments: list[AudioSegment]) -> list[timeline_svc.AudioSpan]:
    """Rebuild timeline spans from stored segments.

    Segment ``start_time``/``end_time`` are absolute, but ``word_timings`` are
    stored relative to their own clip (that is what the TTS provider returns).
    They must be shifted onto the master timeline here, otherwise every span's
    subtitles restart at zero and overlap each other.
    """
    spans: list[timeline_svc.AudioSpan] = []
    for s in segments:
        shifted = [
            {
                "word": t.get("word", ""),
                "start": round(float(t.get("start", 0.0)) + s.start_time, 3),
                "end": round(float(t.get("end", 0.0)) + s.start_time, 3),
            }
            for t in (s.word_timings or [])
        ]
        spans.append(
            timeline_svc.AudioSpan(
                section=s.section,
                text=s.text,
                start_time=s.start_time,
                end_time=s.end_time,
                word_timings=shifted,
            )
        )
    return spans


# --- stage: timeline and subtitles ----------------------------------------


def build_timeline(db: Session, project_id: str, actor_id: str = "") -> list[TimelineScene]:
    """Derive scenes and subtitle cues from the current voice-over."""
    get_project(db, project_id)  # validates the project exists
    script = current_script(db, project_id)
    if script is None:
        raise PipelineError("generate a script first")

    segments = audio_segments(db, script.id)
    if not segments:
        raise PipelineError("generate the voice-over before building the timeline")

    assets = project_assets(db, project_id)
    images = image_assets(assets)
    spans = spans_from_segments(segments)

    for old in db.scalars(select(TimelineScene).where(TimelineScene.project_id == project_id)):
        db.delete(old)
    for old_cue in db.scalars(select(SubtitleCue).where(SubtitleCue.project_id == project_id)):
        db.delete(old_cue)
    db.flush()

    scored = visual_scoring.analyze_assets(images, storage.read_bytes)
    planned = visual_scoring.plan_content_aware_scenes(spans, scored)
    specs = [
        timeline_svc.SceneSpec(
            order_index=shot["order_index"],
            section=shot["section"],
            start_time=shot["start_time"],
            end_time=shot["end_time"],
            asset_id=shot["asset_id"],
            focus_x=shot["focus_x"],
            focus_y=shot["focus_y"],
            effect=shot["effect"],
            transition="fade" if shot["order_index"] else "none",
        )
        for shot in planned
    ]
    scenes: list[TimelineScene] = []
    for spec in specs:
        scene = TimelineScene(
            project_id=project_id,
            asset_id=spec.asset_id,
            order_index=spec.order_index,
            section=spec.section,
            start_time=spec.start_time,
            end_time=spec.end_time,
            focus_x=spec.focus_x,
            focus_y=spec.focus_y,
            effect=spec.effect,
            transition=spec.transition,
        )
        db.add(scene)
        scenes.append(scene)

    for cue in timeline_svc.build_cues(spans):
        db.add(
            SubtitleCue(
                project_id=project_id,
                order_index=cue.order_index,
                text=cue.text,
                start_time=cue.start_time,
                end_time=cue.end_time,
            )
        )

    audit(db, "timeline.build", "project", project_id, actor_id, scenes=len(scenes))
    db.flush()
    return scenes


def project_scenes(db: Session, project_id: str) -> list[TimelineScene]:
    return list(
        db.scalars(
            select(TimelineScene)
            .where(TimelineScene.project_id == project_id)
            .order_by(TimelineScene.order_index)
        )
    )


def project_cues(db: Session, project_id: str) -> list[SubtitleCue]:
    return list(
        db.scalars(
            select(SubtitleCue)
            .where(SubtitleCue.project_id == project_id)
            .order_by(SubtitleCue.order_index)
        )
    )


def cue_specs(cues: list[SubtitleCue]) -> list[timeline_svc.CueSpec]:
    return [
        timeline_svc.CueSpec(
            order_index=c.order_index,
            text=c.text,
            start_time=c.start_time,
            end_time=c.end_time,
        )
        for c in cues
    ]


# --- stage: quality -------------------------------------------------------


def run_quality_checks(
    db: Session,
    project_id: str,
    job: RenderJob | None = None,
    actor_id: str = "",
) -> list[quality_svc.CheckResult]:
    """Run every gate and persist the results for the review UI."""
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

    results = quality_svc.run_all(
        project, assets, script, segments, scenes, cues, job=job, duration=duration
    )

    for old in db.scalars(select(QualityCheck).where(QualityCheck.project_id == project_id)):
        db.delete(old)
    db.flush()
    for result in results:
        db.add(
            QualityCheck(
                project_id=project_id,
                code=result.code,
                severity=result.severity,
                message=result.message,
                passed=result.passed,
            )
        )

    summary = quality_svc.summarise(results)
    audit(db, "quality.run", "project", project_id, actor_id, **summary)
    db.flush()
    return results


def project_quality_checks(db: Session, project_id: str) -> list[QualityCheck]:
    """Stored check results for the review UI, errors first."""
    return list(
        db.scalars(
            select(QualityCheck)
            .where(QualityCheck.project_id == project_id)
            .order_by(QualityCheck.severity, QualityCheck.code)
        )
    )


def override_warning(
    db: Session, project_id: str, code: str, reason: str, actor_id: str = ""
) -> QualityCheck:
    """Record an explicit, attributed override for a non-blocking warning."""
    if not reason.strip():
        raise PipelineError("an override reason is required")
    check = db.scalars(
        select(QualityCheck).where(
            QualityCheck.project_id == project_id, QualityCheck.code == code
        )
    ).first()
    if check is None:
        raise PipelineError(f"no quality check named {code!r} for this project")
    if check.severity == "error":
        raise PipelineError(f"{code} is a blocking error and cannot be overridden")
    check.override_reason = reason.strip()
    check.overridden_by = actor_id
    check.passed = True
    audit(db, "quality.override", "project", project_id, actor_id, code=code, reason=reason.strip())
    db.flush()
    return check


# --- stage: render --------------------------------------------------------


def enqueue_render(
    db: Session,
    project_id: str,
    kind: str = "final",
    actor_id: str = "",
    encoder: str = "auto",
) -> RenderJob:
    """Queue a render. Final renders require passing quality checks.

    ``encoder`` is stored on the job rather than resolved now: the worker may run
    on a different machine than the API, so the GPU probe has to happen where the
    encoding actually happens.
    """
    project = get_project(db, project_id)
    if kind not in {"preview", "final"}:
        raise PipelineError("render kind must be 'preview' or 'final'")

    # Reject an unknown name here so the user finds out at request time rather
    # than discovering a silent CPU fallback after the render.
    from app.services import encoders as encoders_svc

    requested = (encoder or "auto").strip().lower()
    if requested != "auto":
        try:
            encoders_svc.get_spec(requested)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc

    scenes = project_scenes(db, project_id)
    if not scenes:
        raise PipelineError("build the timeline before rendering")

    if kind == "final":
        results = run_quality_checks(db, project_id, actor_id=actor_id)
        blocking = [r for r in results if r.blocking]
        if blocking:
            raise PipelineError(
                "Quality checks must pass before a final render: "
                + "; ".join(r.message for r in blocking[:3])
            )

    job = RenderJob(
        project_id=project_id,
        kind=kind,
        status=JobStatus.QUEUED,
        stage="queued",
        encoder_requested=requested,
    )
    db.add(job)
    project.status = ProjectStatus.RENDERING
    project.error_message = ""
    audit(db, "render.enqueue", "project", project_id, actor_id, kind=kind, encoder=requested)
    db.flush()
    return job


def latest_render(db: Session, project_id: str, kind: str = "final") -> RenderJob | None:
    return db.scalars(
        select(RenderJob)
        .where(RenderJob.project_id == project_id, RenderJob.kind == kind)
        .order_by(RenderJob.created_at.desc())
    ).first()


def successful_render(db: Session, project_id: str) -> RenderJob | None:
    return db.scalars(
        select(RenderJob)
        .where(
            RenderJob.project_id == project_id,
            RenderJob.kind == "final",
            RenderJob.status == JobStatus.SUCCEEDED,
        )
        .order_by(RenderJob.completed_at.desc())
    ).first()


def build_render_request(db: Session, job: RenderJob):
    """Assemble a RenderRequest from persisted state."""
    from pathlib import Path

    from app.services import render as render_svc

    project = get_project(db, job.project_id)
    script = current_script(db, job.project_id)
    if script is None:
        raise PipelineError("no script to render")

    segments = audio_segments(db, script.id)
    if not segments:
        raise PipelineError("no voice-over to render")

    scenes = project_scenes(db, job.project_id)
    if not scenes:
        raise PipelineError("no scenes to render")

    # Concatenate the narration into one track.
    work = storage.workspace_dir(job.project_id, "audio")
    clip_paths = [storage.path_for(s.storage_key) for s in segments]
    missing = [p for p in clip_paths if not p.is_file()]
    if missing:
        raise PipelineError(
            f"{len(missing)} audio file(s) are missing. Regenerate the voice-over."
        )
    voice_path = work / "voice_master.wav"
    tts_svc.concat_audio(clip_paths, voice_path, gap=0.18)

    scene_inputs: list = []
    for scene in scenes:
        image_path: Path | None = None
        if scene.asset_id:
            asset = db.get(SourceAsset, scene.asset_id)
            if asset and storage.exists(asset.storage_key):
                image_path = storage.path_for(asset.storage_key)
        scene_inputs.append(
            render_svc.SceneInput(
                image_path=image_path,
                start_time=scene.start_time,
                end_time=scene.end_time,
                focus_x=scene.focus_x,
                focus_y=scene.focus_y,
                effect=scene.effect,
                overlay_text=scene.overlay_text,
            )
        )

    filename = "preview.mp4" if job.kind == "preview" else "final.mp4"
    return render_svc.RenderRequest(
        project_id=job.project_id,
        scenes=scene_inputs,
        audio_path=voice_path,
        cues=cue_specs(project_cues(db, job.project_id)),
        output_path=storage.output_path(job.project_id, filename),
        preview=job.kind == "preview",
        title_text=project.title,
        encoder=job.encoder_requested or None,
    )


def execute_render(db: Session, job_id: str) -> RenderJob:
    """Run a queued render to completion. Called by the worker."""
    from app.services import render as render_svc

    job = db.get(RenderJob, job_id)
    if job is None:
        raise PipelineError(f"render job {job_id} not found")

    # Claim the job so an inline background task and a standalone worker cannot
    # render the same id twice. Anything not still queued has been taken.
    if job.status != JobStatus.QUEUED:
        return job

    project = get_project(db, job.project_id)
    job.status = JobStatus.RUNNING
    job.started_at = _now()
    job.progress = 0
    job.error_code = ""
    job.error_message = ""
    db.flush()
    db.commit()

    def progress(pct: int, stage: str) -> None:
        job.progress = max(0, min(100, int(pct)))
        job.stage = stage[:80]
        db.flush()
        db.commit()

    try:
        request = build_render_request(db, job)
        result = render_svc.render_video(request, progress=progress)
    except (render_svc.RenderError, PipelineError, tts_svc.TTSError) as exc:
        job.status = JobStatus.FAILED
        job.completed_at = _now()
        job.error_code = getattr(exc, "code", "pipeline_error")
        job.error_message = str(exc)[:1000]
        job.log_tail = getattr(exc, "log_tail", "")[:4000]
        project.status = ProjectStatus.FAILED
        project.error_message = str(exc)[:1000]
        audit(db, "render.failed", "render_job", job.id, error=job.error_code)
        db.flush()
        db.commit()
        return job

    job.status = JobStatus.SUCCEEDED
    job.completed_at = _now()
    job.progress = 100
    job.stage = "done"
    job.output_key = str(result.output_path)
    job.subtitle_key = str(result.subtitle_path) if result.subtitle_path else ""
    job.thumbnail_key = str(result.thumbnail_path) if result.thumbnail_path else ""
    job.checksum = result.checksum
    job.duration = result.duration
    job.width = result.width
    job.height = result.height
    job.encoder = result.encoder
    job.encoder_hardware = result.encoder_hardware
    job.encoder_fell_back = result.encoder_fell_back
    job.encoder_reason = result.encoder_reason[:1000]
    project.status = ProjectStatus.READY
    project.error_message = ""
    audit(
        db, "render.succeeded", "render_job", job.id,
        duration=result.duration, size=result.size_bytes,
        encoder=result.encoder, gpu=result.encoder_hardware,
        encoder_fell_back=result.encoder_fell_back,
    )
    db.flush()
    db.commit()
    return job


def retry_render(db: Session, job_id: str, actor_id: str = "") -> RenderJob:
    """Queue a fresh attempt, preserving the failed job for the audit trail."""
    old = db.get(RenderJob, job_id)
    if old is None:
        raise PipelineError("render job not found")
    if old.status == JobStatus.RUNNING:
        raise PipelineError("this render is still running")

    job = RenderJob(
        project_id=old.project_id,
        kind=old.kind,
        status=JobStatus.QUEUED,
        stage="queued",
        attempt=old.attempt + 1,
        # Keep the original encoder choice so a retry reproduces the same run.
        encoder_requested=old.encoder_requested or "auto",
    )
    db.add(job)
    project = get_project(db, old.project_id)
    project.status = ProjectStatus.RENDERING
    project.error_message = ""
    audit(db, "render.retry", "render_job", job.id, actor_id, previous=old.id, attempt=job.attempt)
    db.flush()
    return job


def next_queued_job(db: Session) -> RenderJob | None:
    return db.scalars(
        select(RenderJob)
        .where(RenderJob.status == JobStatus.QUEUED)
        .order_by(RenderJob.created_at)
        .limit(1)
    ).first()


# --- convenience: full draft ----------------------------------------------


def generate_draft(db: Session, project_id: str, actor_id: str = "", seed: int | None = None) -> dict:
    """Run analyse -> script -> voice -> timeline in one call.

    This is the "draft in under 10 minutes" path from the PRD. It stops short of
    approval and rendering, which stay manual by design.
    """
    run_analysis(db, project_id, actor_id)
    script = generate_script(db, project_id, seed=seed, actor_id=actor_id)
    segments = generate_voiceover(db, project_id, actor_id=actor_id)
    scenes = build_timeline(db, project_id, actor_id)
    cues = project_cues(db, project_id)
    project = get_project(db, project_id)
    project.status = ProjectStatus.REVIEW
    db.flush()
    return {
        "script_id": script.id,
        "script_version": script.version,
        "estimated_duration": script.estimated_duration,
        "audio_duration": round(max((s.end_time for s in segments), default=0.0), 2),
        "segments": len(segments),
        "scenes": len(scenes),
        "cues": len(cues),
        "warnings": script.warnings,
    }
