"""Implementation details for the rendering pipeline stage.

Public callers should continue importing app.services.pipeline.
"""

from __future__ import annotations

from pathlib import Path


def render_silent_review_preview(api, db, project_id, *, actor_id, review_source_upscale_policy, review_source_root, output_dir):
    """Render and persist one video-only review attempt through the regular path."""
    JobStatus = api.JobStatus
    Path = api.Path
    PipelineError = api.PipelineError
    ProjectStatus = api.ProjectStatus
    RenderJob = api.RenderJob
    _now = api._now
    _review_failure_code = api._review_failure_code
    audit = api.audit
    build_render_request = api.build_render_request
    get_project = api.get_project
    project_scenes = api.project_scenes
    reference_profile = api.reference_profile
    settings = api.settings
    time = api.time
    from app.services import render as render_svc
    from app.services import review_preview
    project = get_project(db, project_id)
    if not project_scenes(db, project_id):
        raise PipelineError('visual.panel_lineage_unavailable: build the review timeline first')
    root = Path(output_dir or settings.output_dir) / project_id / 'review'
    output = root / 'silent_preview.mp4'
    job = RenderJob(project_id=project_id, kind='preview', status=JobStatus.QUEUED, stage='review-preview-queued', encoder_requested='auto', render_profile='Auto')
    db.add(job)
    db.flush()
    started = time.monotonic()
    try:
        request = build_render_request(db, job, silent_reference_review=True, output_override=output, review_source_upscale_policy=review_source_upscale_policy, review_source_root=Path(review_source_root))
        result = render_svc.render_video(request)
        artifacts = review_preview.write_review_preview_bundle(db, project_id, result, output_dir=root, subtitle_contract=request.subtitle_contract, subtitle_timing_source=request.subtitle_timing_source, blank_target_fraction=reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION)
    except (render_svc.RenderError, PipelineError, review_preview.ReviewPreviewError) as exc:
        job.status = JobStatus.FAILED
        job.stage = 'review-preview-failed'
        job.error_code = _review_failure_code(exc)[:80]
        job.error_message = str(exc)[:1000]
        project.status = ProjectStatus.REVIEW
        db.flush()
        raise PipelineError(f'{job.error_code}: review preview failed') from exc
    job.status = JobStatus.SUCCEEDED
    job.progress = 100
    job.stage = 'review-preview-ready'
    job.completed_at = _now()
    job.output_key = str(result.output_path)
    job.subtitle_key = str(result.subtitle_path) if result.subtitle_path else ''
    job.checksum = result.checksum
    job.duration = result.duration
    job.width = result.width
    job.height = result.height
    job.encoder = result.encoder
    job.encoder_hardware = result.encoder_hardware
    job.encoder_fell_back = result.encoder_fell_back
    job.encoder_reason = result.encoder_reason[:1000]
    job.render_wall_seconds = round(time.monotonic() - started, 3)
    project.status = ProjectStatus.REVIEW
    project.error_message = ''
    audit(db, 'review.preview.ready', 'render_job', job.id, actor_id, output=str(result.output_path), checksum=result.checksum, duration=result.duration, publish_allowed=False, voice_state='VISUAL_ONLY_WAITING_FOR_VOICE')
    db.flush()
    return (job, artifacts)



def build_render_request(api, db, job, *, silent_reference_review, output_override, review_source_upscale_policy, review_source_root):
    """Assemble a RenderRequest from persisted state."""
    Mapping = api.Mapping
    PipelineError = api.PipelineError
    SourceAsset = api.SourceAsset
    _approved_adaptive_reference_policy = api._approved_adaptive_reference_policy
    _build_silent_reference_request = api._build_silent_reference_request
    _materialize_reference_panel_crop = api._materialize_reference_panel_crop
    audio_segments = api.audio_segments
    cue_specs = api.cue_specs
    current_script = api.current_script
    get_project = api.get_project
    project_assets = api.project_assets
    project_cues = api.project_cues
    project_scenes = api.project_scenes
    reference_profile = api.reference_profile
    review_source_upscale = api.review_source_upscale
    settings = api.settings
    storage = api.storage
    subtitle_karaoke = api.subtitle_karaoke
    tts_svc = api.tts_svc
    from app.services import render as render_svc
    project = get_project(db, job.project_id)
    editorial_profile = reference_profile.resolve_reference_profile(project.template)
    try:
        upscale_policy = review_source_upscale.validate_review_upscale_request(review_source_upscale_policy, silent_reference_review=silent_reference_review, publish_allowed=False)
    except review_source_upscale.ReviewSourceUpscaleError as exc:
        raise PipelineError(str(exc)) from exc
    if upscale_policy is not None and editorial_profile is None:
        raise PipelineError('review.upscale_requires_reference_profile: source upscale requires reference mode')
    if silent_reference_review and editorial_profile is not None:
        return _build_silent_reference_request(db, job, project, editorial_profile, output_override=output_override, review_source_upscale_policy=upscale_policy, review_source_root=review_source_root)
    script = current_script(db, job.project_id)
    if script is None:
        raise PipelineError('no script to render')
    approved_reference_conservative = bool(
        editorial_profile is not None
        and getattr(script, 'approved_at', None) is not None
        and (getattr(script, 'editorial_metadata', None) or {}).get('editorial_review_confirmed') is True
    )
    stabilized_reference_motion = bool(editorial_profile is not None)
    segments = audio_segments(db, script.id)
    if not segments:
        if editorial_profile is not None:
            raise PipelineError('subtitle.word_timing_missing: regular reference render requires authoritative audio timing')
        raise PipelineError('no voice-over to render')
    sentence_groups: tuple[object, ...] = ()
    subtitle_contract: dict[str, object] | None = None
    subtitle_contract_version = ''
    subtitle_timing_source = ''
    scenes = project_scenes(db, job.project_id)
    if not scenes:
        raise PipelineError('no scenes to render')
    if editorial_profile is not None:
        for scene in scenes:
            if not scene.asset_id:
                raise PipelineError('visual.panel_lineage_unavailable: reference scene has no source asset')
            asset = db.get(SourceAsset, scene.asset_id)
            if asset is None or not storage.exists(asset.storage_key):
                raise PipelineError('visual.panel_lineage_unavailable: reference scene asset is unavailable')
        try:
            sentence_groups = subtitle_karaoke.build_sentence_groups_from_segments(segments)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc
        try:
            sentence_groups = render_svc.fit_sentence_karaoke_groups(sentence_groups, editorial_profile.final_width, editorial_profile.final_height, max_chars=subtitle_karaoke.CAPTION_MAX_CHARS, max_lines=subtitle_karaoke.CAPTION_MAX_LINES, active_scale=subtitle_karaoke.CAPTION_ACTIVE_SCALE, font_height_ratio=subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO, safe_margin_px=subtitle_karaoke.CAPTION_SAFE_MARGIN_PX)
        except render_svc.RenderError as exc:
            raise PipelineError(f'{exc.code}: {exc}') from exc
        subtitle_contract = subtitle_karaoke.contract_manifest(editorial_profile)
        subtitle_contract_version = subtitle_karaoke.SUBTITLE_CONTRACT_VERSION
        subtitle_timing_source = 'audio_segment.word_timings'
    work = storage.workspace_dir(job.project_id, 'audio')
    clip_paths = [storage.path_for(s.storage_key) for s in segments]
    missing = [p for p in clip_paths if not p.is_file()]
    if missing:
        raise PipelineError(f'{len(missing)} audio file(s) are missing. Regenerate the voice-over.')
    voice_path = work / 'voice_master.wav'
    tts_svc.concat_audio(clip_paths, voice_path, gap=0.18)
    audio_duration = tts_svc.probe_duration(voice_path)
    scene_end_times = [scene.end_time for scene in scenes]
    scene_end_times[-1] = max(scene_end_times[-1], audio_duration)
    rendered_frames = sum((max(1, int(round(max(0.1, round(end_time - scene.start_time, 3)) * settings.video_fps))) for scene, end_time in zip(scenes, scene_end_times, strict=True)))
    media_duration = round(min(audio_duration, rendered_frames / settings.video_fps), 3)
    persisted_cues = project_cues(db, job.project_id)
    cues = cue_specs(persisted_cues)
    for persisted, cue in zip(persisted_cues, cues, strict=True):
        cue.start_time = round(min(max(0.0, cue.start_time), media_duration), 3)
        cue.end_time = round(min(max(cue.start_time, cue.end_time), media_duration), 3)
        persisted.start_time = cue.start_time
        persisted.end_time = cue.end_time
    db.flush()
    scene_inputs: list = []
    music_path: Path | None = None
    audio_assets = [asset for asset in project_assets(db, job.project_id) if asset.type in {'audio', 'music'} and asset.is_publishable and storage.exists(asset.storage_key)]
    if audio_assets:
        music_path = storage.path_for(audio_assets[0].storage_key)
    profile = job.render_profile or 'Auto'
    panel_workspace = storage.workspace_dir(job.project_id, 'reference-panels') if editorial_profile is not None else None
    for index, scene in enumerate(scenes):
        end_time = scene.end_time
        if index == len(scenes) - 1:
            end_time = max(end_time, audio_duration)
        start_time = scene.start_time
        motion_mode = scene.motion_mode
        camera_curve = scene.camera_curve
        if profile == 'No motion' or (profile == 'Calm' and scene.camera_intent not in {'impact', 'explosion'}):
            motion_mode, camera_curve = ('hold', 'static')
        elif profile == 'Dynamic' and scene.camera_intent in {'action', 'attack'}:
            motion_mode = 'guided_pan' if scene.motion_mode == 'hold' else scene.motion_mode
        image_path: Path | None = None
        if scene.asset_id:
            asset = db.get(SourceAsset, scene.asset_id)
            if asset and storage.exists(asset.storage_key):
                image_path = storage.path_for(asset.storage_key)
                if editorial_profile is not None:
                    image_path = _materialize_reference_panel_crop(
                        db,
                        asset,
                        scene,
                        panel_workspace / f'scene-{index:04d}.png',
                        **({"allow_conservative_full_panel": True} if approved_reference_conservative else {}),
                    )
            elif editorial_profile is not None:
                raise PipelineError('visual.panel_lineage_unavailable: reference scene asset is unavailable')
        elif editorial_profile is not None:
            raise PipelineError('visual.panel_lineage_unavailable: reference scene has no source asset')
        reference_ledger = list(getattr(scene, 'rejected_candidates', []) or []) if editorial_profile is not None else []
        accepted_reference = next((entry for entry in reference_ledger if isinstance(entry, Mapping) and entry.get('accepted') is True), {})
        reference_telemetry = accepted_reference.get('telemetry') if isinstance(accepted_reference, Mapping) else None
        reference_roi = reference_telemetry.get('selected_roi') if isinstance(reference_telemetry, Mapping) else None
        reference_mask = accepted_reference.get('border_mask') if isinstance(accepted_reference, Mapping) else None
        scene_inputs.append(render_svc.SceneInput(image_path=image_path, start_time=start_time, end_time=end_time, focus_x=scene.focus_x, focus_y=scene.focus_y, focus_end_x=scene.focus_end_x, focus_end_y=scene.focus_end_y, camera_curve=camera_curve, motion_mode=motion_mode, motion_intensity=scene.motion_intensity, motion_reason=scene.motion_reason, effect=scene.effect, disabled_effects=scene.disabled_effects, transition=scene.transition, overlay_text=scene.overlay_text, panel_region_id=getattr(scene, 'panel_region_id', None) if editorial_profile else None, panel_id=getattr(scene, 'panel_id', '') if editorial_profile else '', panel_bounds=(int(scene.panel_bounds_json['x']), int(scene.panel_bounds_json['y']), int(scene.panel_bounds_json['x']) + int(scene.panel_bounds_json['width']), int(scene.panel_bounds_json['y']) + int(scene.panel_bounds_json['height'])) if editorial_profile and isinstance(getattr(scene, 'panel_bounds_json', None), Mapping) else None, visual_evidence=getattr(scene, 'visual_evidence_json', None) if editorial_profile else None, source_asset_checksum=getattr(scene, 'source_asset_checksum', '') if editorial_profile else '', source_asset_id=scene.asset_id if editorial_profile else '', source_order=accepted_reference.get('source_order') if isinstance(accepted_reference, Mapping) else None, panel_size=(int(scene.panel_bounds_json['width']), int(scene.panel_bounds_json['height'])) if editorial_profile and isinstance(getattr(scene, 'panel_bounds_json', None), Mapping) else None, evidence_hash=accepted_reference.get('evidence_hash', '') if isinstance(accepted_reference, Mapping) else '', border_mask=reference_mask, selected_roi=reference_roi, fallback_attempts=reference_ledger, framing_telemetry=reference_telemetry, publish_allowed=not bool(editorial_profile)))
    filename = 'preview.mp4' if job.kind == 'preview' else 'final.mp4'
    return render_svc.RenderRequest(project_id=job.project_id, scenes=scene_inputs, audio_path=voice_path, cues=cues, output_path=storage.output_path(job.project_id, filename), preview=job.kind == 'preview', title_text='' if editorial_profile else project.title, profile=editorial_profile, music_path=music_path, music_gain_db=-24.0, encoder=job.encoder_requested or None, sentence_groups=list(sentence_groups), subtitle_contract_version=subtitle_contract_version, subtitle_timing_source=subtitle_timing_source, subtitle_contract=subtitle_contract, persisted_reference_framing=bool(editorial_profile), stabilized_reference_motion=stabilized_reference_motion, allow_conservative_full_panel=approved_reference_conservative)



def execute_render(api, db, job_id):
    """Run a queued render to completion. Called by the worker."""
    JobStatus = api.JobStatus
    Path = api.Path
    PipelineError = api.PipelineError
    ProjectStatus = api.ProjectStatus
    RenderJob = api.RenderJob
    SimpleNamespace = api.SimpleNamespace
    _approved_adaptive_reference_policy = api._approved_adaptive_reference_policy
    _ensure_final_thumbnail = api._ensure_final_thumbnail
    _now = api._now
    asdict = api.asdict
    audio_segments = api.audio_segments
    audit = api.audit
    build_render_request = api.build_render_request
    claim_render_job = api.claim_render_job
    cue_specs = api.cue_specs
    current_script = api.current_script
    get_project = api.get_project
    json = api.json
    policy_svc = api.policy_svc
    project_assets = api.project_assets
    project_cues = api.project_cues
    project_scenes = api.project_scenes
    reference_profile = api.reference_profile
    resource = api.resource
    settings = api.settings
    storage = api.storage
    time = api.time
    timedelta = api.timedelta
    tts_svc = api.tts_svc
    visual_scoring = api.visual_scoring
    from app.services import render as render_svc
    job = db.get(RenderJob, job_id)
    if job is None:
        raise PipelineError(f'render job {job_id} not found')
    if not claim_render_job(db, job_id):
        return job
    project = get_project(db, job.project_id)

    def progress(pct: int, stage: str) -> None:
        job.progress = max(0, min(100, int(pct)))
        job.stage = stage[:80]
        job.heartbeat_at = _now()
        job.lease_until = job.heartbeat_at + timedelta(seconds=1800)
        db.flush()
        db.commit()
    started_wall = time.monotonic()
    started_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        request = build_render_request(db, job)
        result = render_svc.render_video(request, progress=progress)
    except (render_svc.RenderError, PipelineError, tts_svc.TTSError) as exc:
        scratch = storage.workspace_dir(job.project_id, 'render')
        if scratch.exists():
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)
        job.status = JobStatus.FAILED
        job.completed_at = _now()
        job.error_code = getattr(exc, 'code', 'pipeline_error')
        job.error_message = str(exc)[:1000]
        job.log_tail = getattr(exc, 'log_tail', '')[:4000]
        job.render_wall_seconds = round(time.monotonic() - started_wall, 3)
        job.peak_rss_bytes = max(0, int((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - started_rss) * 1024))
        project.status = ProjectStatus.FAILED
        project.error_message = str(exc)[:1000]
        audit(db, 'render.failed', 'render_job', job.id, error=job.error_code)
        db.flush()
        db.commit()
        return job
    job.status = JobStatus.SUCCEEDED
    job.completed_at = _now()
    job.progress = 100
    job.stage = 'done'
    job.output_key = str(result.output_path)
    job.subtitle_key = str(result.subtitle_path) if result.subtitle_path else ''
    job.thumbnail_key = str(result.thumbnail_path) if result.thumbnail_path else ''
    job.checksum = result.checksum
    job.duration = result.duration
    job.width = result.width
    job.height = result.height
    job.encoder = result.encoder
    job.encoder_hardware = result.encoder_hardware
    job.encoder_fell_back = result.encoder_fell_back
    job.encoder_reason = result.encoder_reason[:1000]
    job.render_wall_seconds = round(time.monotonic() - started_wall, 3)
    job.peak_rss_bytes = max(0, int((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - started_rss) * 1024))
    job.scratch_bytes = result.scratch_bytes
    from app.services import editorial_qc
    scenes = project_scenes(db, job.project_id)
    cues = cue_specs(project_cues(db, job.project_id))
    assets = project_assets(db, job.project_id)
    source_findings = policy_svc.check_source_cleanliness(assets)
    test_only = any('NOT_FOR_PUBLICATION' in (asset.original_filename or '').upper() or 'NOT_FOR_PUBLICATION' in (asset.source_name or '').upper() for asset in assets)
    rights_confidence = 5 if not settings.require_rights_declaration else 0 if test_only else 5 if all(asset.is_publishable for asset in assets) else 0
    source_cleanliness = 0 if test_only or source_findings else 5
    if test_only and (not any(getattr(f, 'code', '') == 'source.test_only' for f in source_findings)):
        source_findings.append(policy_svc.PolicyFinding('source.test_only', policy_svc.CheckSeverity.ERROR, 'NOT_FOR_PUBLICATION source is test-only.'))
    render_profile = reference_profile.resolve_reference_profile(project.template)
    qc_scenes = scenes
    panel_evidence_by_key: dict[tuple[str, str], object] = {}
    panel_border_masks_by_key: dict[tuple[str, str], object] = {}
    panel_sizes_by_key: dict[tuple[str, str], tuple[int, int]] = {}
    telemetry_by_key: dict[tuple[str, str], object] = {}
    if render_profile is not None:
        enriched: list[object] = []
        for persisted_scene, input_scene in zip(scenes, request.scenes, strict=True):
            values = {key: value for key, value in vars(persisted_scene).items() if not key.startswith('_')}
            values.update({'source_asset_id': input_scene.source_asset_id, 'source_order': input_scene.source_order, 'panel_region_id': input_scene.panel_region_id, 'panel_id': input_scene.panel_id, 'panel_bounds': input_scene.panel_bounds, 'panel_size': input_scene.panel_size, 'visual_evidence': input_scene.visual_evidence, 'border_mask': input_scene.border_mask, 'selected_roi': input_scene.selected_roi, 'roi': input_scene.selected_roi, 'fallback_attempts': input_scene.fallback_attempts, 'framing_telemetry': input_scene.framing_telemetry, 'evidence_hash': input_scene.evidence_hash, 'source_asset_checksum': input_scene.source_asset_checksum})
            enriched.append(SimpleNamespace(**values))
            key = (str(input_scene.source_asset_id), str(input_scene.panel_region_id))
            if not key[0] or not key[1]:
                continue
            try:
                evidence = visual_scoring.parse_panel_visual_evidence(input_scene.visual_evidence or {})
                mask = render_svc._reference_border_mask_from_mapping(input_scene.border_mask)
            except (render_svc.RenderError, visual_scoring.VisualEvidenceError, TypeError, ValueError):
                continue
            panel_evidence_by_key[key] = evidence
            panel_border_masks_by_key[key] = mask
            if input_scene.panel_size is not None:
                panel_sizes_by_key[key] = tuple(input_scene.panel_size)
            telemetry_by_key[key] = input_scene.framing_telemetry
        qc_scenes = enriched
    render_script = current_script(db, job.project_id)
    adaptive_reference_contract = _approved_adaptive_reference_policy(render_script)
    standard_reference_cadence = False
    if render_script is not None and render_profile is not None and adaptive_reference_contract is None:
        metadata = render_script.editorial_metadata if isinstance(render_script.editorial_metadata, dict) else {}
        production = metadata.get("production") if isinstance(metadata, dict) else None
        identity = production.get("timeline_planning_identity") if isinstance(production, dict) else None
        standard_reference_cadence = bool(
            isinstance(identity, dict)
            and identity.get("version") == reference_profile.PRODUCTION_REFERENCE_CADENCE_POLICY_VERSION
            and identity.get("standard_reference_production") is True
        )
    qc = editorial_qc.build_report(scenes=qc_scenes, cues=cues, duration=result.duration, job_path=Path(result.output_path), rights_confidence=rights_confidence, source_cleanliness=source_cleanliness, voice_profile_count=len({segment.voice_profile_hash for segment in audio_segments(db, current_script(db, job.project_id).id) if segment.voice_profile_hash}), preview=job.kind == 'preview', profile=render_profile, caption_groups=request.sentence_groups, subtitle_contract=request.subtitle_contract, subtitle_timing_error=None, panel_evidence_by_key=panel_evidence_by_key if render_profile is not None else None, panel_border_masks_by_key=panel_border_masks_by_key if render_profile is not None else None, panel_sizes_by_key=panel_sizes_by_key if render_profile is not None else None, telemetry_by_key=telemetry_by_key if render_profile is not None else None, adaptive_reference_contract=adaptive_reference_contract, standard_reference_cadence=standard_reference_cadence)
    report_dir = Path(result.output_path).parent
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / 'final.qc.json').write_text(json.dumps(qc.as_dict(), indent=2), encoding='utf-8')
    (report_dir / 'shot_list.json').write_text(json.dumps([{'order_index': s.order_index, 'asset_id': s.asset_id, 'source_family': s.source_family, 'roi_label': s.roi_label, 'start_time': s.start_time, 'end_time': s.end_time, 'camera_intent': s.camera_intent, 'camera_curve': s.camera_curve, 'motion_mode': s.motion_mode, 'motion_reason': s.motion_reason, 'alignment_score': s.alignment_score, 'alignment_reasons': s.alignment_reasons, 'rejected_candidates': s.rejected_candidates, 'visual_signature': s.visual_signature} for s in scenes], indent=2), encoding='utf-8')
    (report_dir / 'subtitle_list.json').write_text(json.dumps([asdict(c) for c in cues], indent=2), encoding='utf-8')
    (report_dir / 'panel_to_script_mapping.json').write_text(json.dumps([{'shot': s.order_index, 'panel': s.asset_id, 'section': s.section, 'roi': s.roi_label, 'alignment_score': s.alignment_score, 'alignment_reasons': s.alignment_reasons, 'rejected_candidates': s.rejected_candidates} for s in scenes], indent=2), encoding='utf-8')
    (report_dir / 'source_rights_report.json').write_text(json.dumps({'rights_confidence': rights_confidence, 'source_cleanliness': source_cleanliness, 'findings': [f.__dict__ for f in source_findings], 'publishable': not source_findings and rights_confidence == 5}, indent=2), encoding='utf-8')
    (report_dir / 'panel_catalog.json').write_text(json.dumps([{'asset_id': asset.id, 'filename': asset.original_filename, 'source_family': asset.source_family, 'order_index': asset.order_index, 'bbox': asset.panel_bbox, 'quality': asset.panel_quality, 'decision': asset.panel_decision} for asset in assets if asset.type == 'image'], indent=2), encoding='utf-8')
    try:
        from PIL import Image, ImageDraw
        panel_assets = [asset for asset in assets if asset.type == 'image' and asset.panel_decision != 'reject']
        thumbs = []
        for asset in panel_assets[:24]:
            path = storage.path_for(asset.storage_key)
            with Image.open(path) as image:
                thumb = image.convert('RGB')
                thumb.thumbnail((180, 260))
                card = Image.new('RGB', (200, 300), 'white')
                card.paste(thumb, ((200 - thumb.width) // 2, 8))
                ImageDraw.Draw(card).text((8, 275), f'{asset.order_index}: {asset.original_filename[:24]}', fill='black')
                thumbs.append(card)
        if thumbs:
            columns = 4
            rows = (len(thumbs) + columns - 1) // columns
            sheet = Image.new('RGB', (columns * 200, rows * 300), '#d8d8d8')
            for index, thumb in enumerate(thumbs):
                sheet.paste(thumb, (index % columns * 200, index // columns * 300))
            sheet.save(report_dir / 'contact_sheet.jpg', quality=88)
    except (OSError, ValueError):
        pass
    audit(db, 'editorial.qc', 'render_job', job.id, qc=qc.as_dict())
    if qc.qc_pass and job.kind == 'final':
        _ensure_final_thumbnail(db, job, required=False)
    if qc.qc_pass:
        job.status = JobStatus.SUCCEEDED
        job.stage = 'done'
        project.status = ProjectStatus.READY
    else:
        job.status = JobStatus.FAILED
        job.stage = 'post-render-qc-blocked'
        job.error_code = 'render.qc_blocked'
        job.error_message = '; '.join(qc.failures)[:1000]
        project.status = ProjectStatus.REVIEW
    project.error_message = '' if qc.qc_pass else '; '.join(qc.failures)
    audit(db, 'render.succeeded' if qc.qc_pass else 'render.qc_blocked', 'render_job', job.id, duration=result.duration, size=result.size_bytes, encoder=result.encoder, gpu=result.encoder_hardware, encoder_fell_back=result.encoder_fell_back)
    db.flush()
    db.commit()
    return job



def retry_render(api, db, job_id, actor_id):
    """Queue a fresh attempt, preserving the failed job for the audit trail."""
    JobStatus = api.JobStatus
    PipelineError = api.PipelineError
    ProjectStatus = api.ProjectStatus
    RenderJob = api.RenderJob
    audit = api.audit
    get_project = api.get_project
    old = db.get(RenderJob, job_id)
    if old is None:
        raise PipelineError('render job not found')
    if old.status == JobStatus.RUNNING:
        raise PipelineError('this render is still running')
    job = RenderJob(project_id=old.project_id, kind=old.kind, status=JobStatus.QUEUED, stage='queued', attempt=old.attempt + 1, encoder_requested=old.encoder_requested or 'auto')
    db.add(job)
    project = get_project(db, old.project_id)
    project.status = ProjectStatus.RENDERING
    project.error_message = ''
    audit(db, 'render.retry', 'render_job', job.id, actor_id, previous=old.id, attempt=job.attempt)
    db.flush()
    return job



def claim_render_job(api, db, job_id, lease_seconds):
    """Claim a queued job or reclaim an expired running lease."""
    JobStatus = api.JobStatus
    RenderJob = api.RenderJob
    _now = api._now
    secrets = api.secrets
    timedelta = api.timedelta
    now = _now()
    job = db.get(RenderJob, job_id)
    if job is None:
        return False
    reclaimable = job.status == JobStatus.RUNNING and job.lease_until and (job.lease_until < now)
    if job.status != JobStatus.QUEUED and (not reclaimable):
        return False
    job.status = JobStatus.RUNNING
    job.started_at = job.started_at or now
    job.heartbeat_at = now
    job.lease_until = now + timedelta(seconds=lease_seconds)
    job.lease_token = secrets.token_hex(16)
    job.progress = 0
    job.error_code = ''
    job.error_message = ''
    db.flush()
    db.commit()
    return True



def recover_stale_jobs(api, db):
    """Requeue expired workers, retaining the audit trail."""
    JobStatus = api.JobStatus
    RenderJob = api.RenderJob
    _now = api._now
    select = api.select
    now = _now()
    stale = list(db.scalars(select(RenderJob).where(RenderJob.status == JobStatus.RUNNING, RenderJob.lease_until.is_not(None), RenderJob.lease_until < now)))
    for job in stale:
        job.status = JobStatus.QUEUED
        job.stage = 'recovered stale lease'
        job.lease_token = ''
        job.lease_until = None
        job.heartbeat_at = None
        job.attempt += 1
    db.flush()
    return len(stale)
