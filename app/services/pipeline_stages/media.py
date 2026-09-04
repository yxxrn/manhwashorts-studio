"""Implementation details for the media pipeline stage.

Public callers should continue importing app.services.pipeline.
"""

from __future__ import annotations


def generate_voiceover(api, db, project_id, *, speed, provider_name, actor_id, duration_bounds_s=None):
    """Synthesise one clip per script section, replacing any previous audio."""
    AudioSegment = api.AudioSegment
    PipelineError = api.PipelineError
    _script_for_media = api._script_for_media
    audit = api.audit
    editorial_timing = api.editorial_timing
    get_project = api.get_project
    resolver_svc = api.resolver_svc
    script_svc = api.script_svc
    select = api.select
    storage = api.storage
    timeline_svc = api.timeline_svc
    tts_svc = api.tts_svc
    project = get_project(db, project_id)
    script = _script_for_media(db, project_id)
    provider, tts_decision = resolver_svc.resolve_tts(db, project.workspace_id, override=provider_name)
    if provider.name != 'null' and (not provider.available()):
        raise PipelineError(f'selected voice provider is unavailable: {provider.name}; no fallback voice is allowed')
    editorial_errors = [warning for warning in script.warnings or [] if warning.get('severity') == 'error']
    if editorial_errors:
        raise PipelineError('editorial validation failed before TTS: ' + '; '.join(item.get('message', item.get('code', '')) for item in editorial_errors[:4]))
    work = storage.workspace_dir(project_id, 'audio')
    existing = list(db.scalars(select(AudioSegment).where(AudioSegment.script_version_id == script.id)))
    for segment in existing:
        if not segment.user_uploaded:
            storage.delete(segment.storage_key)
        db.delete(segment)
    db.flush()
    prepared: list[tuple[int, dict, str]] = []
    for index, section in enumerate(script.sections or []):
        text = (section.get('text') or '').strip()
        if text:
            prepared.append((index, section, script_svc.apply_pronunciations(text, project.pronunciations or {})))
    if not prepared:
        raise PipelineError('script has no spoken text')
    requested_voice_id = project.voice_id
    try:
        if isinstance(provider, tts_svc.HttpProvider):
            clips = provider.synthesize_sections([spoken for _, _, spoken in prepared], work, requested_voice_id, speed)
        else:
            clips = [provider.synthesize(spoken, work / f"{index:02d}_{section['section']}.wav", requested_voice_id, speed) for index, section, spoken in prepared]
    except tts_svc.TTSError as exc:
        raise PipelineError(f'voice-over failed: {exc}') from exc
    gap = 0.18
    timing_policy = None
    if duration_bounds_s is not None:
        try:
            duration_min_s, duration_max_s = (float(duration_bounds_s[0]), float(duration_bounds_s[1]))
            clips, timing_policy = tts_svc.normalize_speech_clips_to_duration_window(
                clips,
                duration_min_s=duration_min_s,
                duration_max_s=duration_max_s,
                gap_s=gap,
            )
        except (IndexError, TypeError, ValueError, tts_svc.TTSError) as exc:
            raise PipelineError(f'voice-over duration normalization failed: {exc}') from exc
    created: list[AudioSegment] = []
    profile_hashes = {clip.voice_profile_hash for clip in clips}
    if len(profile_hashes) != 1:
        raise PipelineError('voice profile changed between chunks; refusing mixed narrator output')
    profile_hash = next(iter(profile_hashes))
    for (index, section, spoken), clip in zip(prepared, clips, strict=True):
        text = (section.get('text') or '').strip()
        display_text = timeline_svc.normalize_display_text(text)
        stored = storage.put_file(f'projects/{project_id}/audio', clip.path, clip.path.name)
        segment = AudioSegment(script_version_id=script.id, section=section['section'], order_index=index, text=text, spoken_text=spoken, display_text=display_text, voice_id=clip.voice_id, provider=clip.provider, voice_profile_hash=profile_hash, voice_profile=clip.voice_profile, storage_key=stored.storage_key, duration=clip.duration, word_timings=clip.word_timings, dramatic_events=editorial_timing.dramatic_events(clip.word_timings, project.language))
        db.add(segment)
        created.append(segment)
    if not created:
        raise PipelineError('script has no spoken text')
    cursor = 0.0
    for i, segment in enumerate(created):
        segment.start_time = round(cursor, 3)
        segment.end_time = round(cursor + segment.duration, 3)
        cursor = segment.end_time + (gap if i < len(created) - 1 else 0.0)
    audit(db, 'voice.generate', 'project', project_id, actor_id, segments=len(created), provider=provider.name, provider_source=tts_decision.source, model=tts_decision.model, timing_policy=dict(timing_policy or {}))
    db.flush()
    return created



def build_timeline(api, db, project_id, actor_id, *, silent_reference_review, review_source_upscale_policy, provisional_duration_s, provisional_duration_bounds_s, reference_section_panel_ids, reference_section_citations, reference_beats_by_section, review_source_root, allow_conservative_full_panel, adaptive_reference_production, adaptive_reference_duration_bounds_s, standard_reference_production):
    """Derive scenes/cues from voice timing or explicit silent-review pacing."""
    PipelineError = api.PipelineError
    SubtitleCue = api.SubtitleCue
    TimelineScene = api.TimelineScene
    _bind_reference_panel_regions = api._bind_reference_panel_regions
    _enforce_silent_review_transition_contract = api._enforce_silent_review_transition_contract
    _load_reference_panel_fallback_candidates = api._load_reference_panel_fallback_candidates
    _panel_bounds_json = api._panel_bounds_json
    _reference_duration_bounds = api._reference_duration_bounds
    _review_provisional_spans = api._review_provisional_spans
    _script_for_media = api._script_for_media
    audio_segments = api.audio_segments
    audit = api.audit
    director_svc = api.director_svc
    get_project = api.get_project
    image_assets = api.image_assets
    math = api.math
    project_assets = api.project_assets
    reference_profile = api.reference_profile
    reference_visual_review = api.reference_visual_review
    review_source_upscale = api.review_source_upscale
    select = api.select
    spans_from_segments = api.spans_from_segments
    storage = api.storage
    timeline_svc = api.timeline_svc
    visual_scoring = api.visual_scoring
    project = get_project(db, project_id)
    profile = reference_profile.resolve_reference_profile(project.template)
    script = _script_for_media(db, project_id, allow_unapproved_review=silent_reference_review)
    cadence_adapted_reference = bool(
        silent_reference_review or adaptive_reference_production or standard_reference_production
    )
    review_policy = None
    if silent_reference_review:
        if profile is None:
            raise PipelineError('review.upscale_requires_reference_profile: silent review requires reference mode')
        try:
            review_policy = review_source_upscale.validate_review_upscale_request(review_source_upscale_policy, silent_reference_review=True, publish_allowed=False)
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise PipelineError(str(exc)) from exc
        if review_policy is None:
            raise PipelineError('review.upscale_policy_required: silent source review requires the explicit upscale policy')
        audio_duration = float(provisional_duration_s if provisional_duration_s is not None else getattr(script, 'estimated_duration', 0.0) or 0.0)
        if not audio_duration:
            audio_duration = 51.3
        spans = _review_provisional_spans(script, audio_duration)
        segments: list[object] = []
    else:
        segments = audio_segments(db, script.id)
        if not segments:
            raise PipelineError('generate the voice-over before building the timeline')
        audio_duration = max((float(segment.end_time) for segment in segments), default=0.0)
        spans = spans_from_segments(segments)
    adaptive_duration_bounds = provisional_duration_bounds_s if silent_reference_review else adaptive_reference_duration_bounds_s if adaptive_reference_production else None
    if (silent_reference_review or adaptive_reference_production) and adaptive_duration_bounds is not None:
        try:
            duration_min_s, duration_max_s = (float(adaptive_duration_bounds[0]), float(adaptive_duration_bounds[1]))
        except (IndexError, TypeError, ValueError):
            raise PipelineError('review.provisional_duration_bounds_invalid: adaptive review bounds are malformed') from None
        if not math.isfinite(duration_min_s) or not math.isfinite(duration_max_s) or duration_min_s <= 0.0 or (duration_max_s < duration_min_s):
            raise PipelineError('review.provisional_duration_bounds_invalid: adaptive review bounds are malformed')
    else:
        duration_min_s, duration_max_s = _reference_duration_bounds(profile, silent_reference_review=silent_reference_review) if profile is not None else (0.0, float('inf'))
    if profile is not None and (not duration_min_s <= audio_duration <= duration_max_s):
        raise PipelineError(f'{profile.profile_id} requires audio duration between {duration_min_s:.1f} and {duration_max_s:.1f} seconds')
    assets = project_assets(db, project_id)
    images = image_assets(assets)
    # Exact reference planning builds and scores its cited panel crops below;
    # the full source-asset score list is never consumed by that planner path.
    # Avoid rescanning every chapter image before immediately discarding the
    # result. Non-reference projects still need the full asset analysis.
    scored = (
        []
        if profile is not None
        else visual_scoring.analyze_assets(images, storage.read_bytes)
    )
    from app.services import editorial_visual_planner
    candidate_registry: dict[str, object] = {}
    reference_candidates: tuple[object, ...] | None = None
    if profile is not None:
        if any(value is not None for value in (review_policy, reference_section_panel_ids, reference_section_citations, reference_beats_by_section)):
            reference_candidates = _load_reference_panel_fallback_candidates(db, project_id, script, images, profile, review_source_upscale_policy=review_policy, section_evidence_panel_ids=reference_section_panel_ids, section_citations=reference_section_citations, beats_by_section=reference_beats_by_section, review_source_root=review_source_root, allow_persisted_panel_crop_fallback=silent_reference_review, **({'pixel_refinement_preflight': True} if standard_reference_production else {}), **({'allow_conservative_full_panel': True} if allow_conservative_full_panel else {}))
        else:
            reference_candidates = _load_reference_panel_fallback_candidates(db, project_id, script, images, profile, **({'pixel_refinement_preflight': True} if standard_reference_production else {}), **({'allow_conservative_full_panel': True} if allow_conservative_full_panel else {}))
        candidate_registry = {str(candidate.panel_region_id): candidate for candidate in reference_candidates}
    try:
        planned = editorial_visual_planner.plan(spans, scored, profile=profile, cited_asset_ids_by_section=None if profile is not None else None, citation_alignment_reasons_by_section=None if profile is not None else None, reference_panel_candidates=reference_candidates, allow_source_resolution_warning=bool(adaptive_reference_production or (review_policy is not None and review_policy.allow_low_source_resolution_warning)), allow_review_cadence_adaptation=silent_reference_review or adaptive_reference_production, allow_standard_cadence_adaptation=standard_reference_production, allow_review_duration=silent_reference_review or adaptive_reference_production, review_duration_bounds_s=adaptive_duration_bounds, **{'allow_conservative_full_panel': True} if allow_conservative_full_panel else {})
    except editorial_visual_planner.ReferencePlanningError as exc:
        raise PipelineError(f'reference_planning_failed: {exc.code}: {exc}') from exc
    if profile is not None:
        planned = _bind_reference_panel_regions(
            db,
            project_id,
            script,
            images,
            planned,
            candidate_registry=candidate_registry,
            review_source_upscale_policy=review_policy,
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
        for shot in planned:
            ledger = shot.get('fallback_attempts')
            if isinstance(ledger, list):
                try:
                    shot['rejected_candidates'] = reference_visual_review.attach_accepted_mask_snapshot(shot, candidate_registry)
                except reference_visual_review.ReferenceReviewError as exc:
                    raise PipelineError(f'{exc.code}: {exc}') from exc
    if profile is not None and cadence_adapted_reference and len(planned) > 1:
        _enforce_silent_review_transition_contract(planned)
    for old in db.scalars(select(TimelineScene).where(TimelineScene.project_id == project_id)):
        db.delete(old)
    for old_cue in db.scalars(select(SubtitleCue).where(SubtitleCue.project_id == project_id)):
        db.delete(old_cue)
    db.flush()
    editorial_issues = director_svc.audit_sequence(planned)
    if editorial_issues:
        audit(db, 'director.audit', 'project', project_id, actor_id, issues=editorial_issues)
    specs = [timeline_svc.SceneSpec(order_index=shot['order_index'], section=shot['section'], start_time=shot['start_time'], end_time=shot['end_time'], asset_id=shot['asset_id'], source_family=shot.get('source_family', ''), focus_x=shot['focus_x'], focus_y=shot['focus_y'], focus_end_x=shot.get('focus_end_x', shot['focus_x']), focus_end_y=shot.get('focus_end_y', shot['focus_y']), roi_label=shot.get('roi_label', ''), camera_curve=shot.get('camera_curve', shot['effect']), motion_mode=shot.get('motion_mode', 'hold'), motion_intensity=shot.get('motion_intensity', 'low'), motion_reason=shot.get('motion_reason', ''), camera_intent=shot.get('camera_intent', 'neutral'), narration_timing=shot.get('narration_timing', 'narration_lead'), effect=shot['effect'], disabled_effects=shot.get('disabled_effects', []), overlay_text=shot.get('overlay_text', ''), transition=shot.get('transition', 'fade' if shot['order_index'] else 'none'), alignment_score=shot.get('alignment_score', 0.0), alignment_reasons=shot.get('alignment_reasons', []), rejected_candidates=shot.get('rejected_candidates', []), visual_signature=shot.get('visual_signature', ''), panel_region_id=shot.get('panel_region_id'), panel_id=shot.get('panel_id', ''), panel_bounds=shot.get('panel_bounds'), visual_evidence=shot.get('visual_evidence'), source_asset_checksum=shot.get('source_asset_checksum', '')) for shot in planned]
    scenes: list[TimelineScene] = []
    for spec in specs:
        scene = TimelineScene(project_id=project_id, asset_id=spec.asset_id, source_family=spec.source_family, order_index=spec.order_index, section=spec.section, start_time=spec.start_time, end_time=spec.end_time, focus_x=spec.focus_x, focus_y=spec.focus_y, focus_end_x=spec.focus_end_x, focus_end_y=spec.focus_end_y, roi_label=spec.roi_label, camera_curve=spec.camera_curve, motion_mode=spec.motion_mode, motion_intensity=spec.motion_intensity, motion_reason=spec.motion_reason, camera_intent=spec.camera_intent, narration_timing=spec.narration_timing, effect=spec.effect, disabled_effects=spec.disabled_effects, transition=spec.transition, alignment_score=getattr(spec, 'alignment_score', 0.0), alignment_reasons=getattr(spec, 'alignment_reasons', []), rejected_candidates=getattr(spec, 'rejected_candidates', []), visual_signature=getattr(spec, 'visual_signature', ''), panel_region_id=spec.panel_region_id, panel_id=spec.panel_id, panel_bounds_json=_panel_bounds_json(spec.panel_bounds) if spec.panel_bounds is not None else {}, visual_evidence_json=dict(spec.visual_evidence or {}), source_asset_checksum=spec.source_asset_checksum)
        db.add(scene)
        scenes.append(scene)
    for cue in timeline_svc.build_cues(spans, media_duration=max((span.end_time for span in spans), default=0.0)):
        db.add(SubtitleCue(project_id=project_id, order_index=cue.order_index, text=cue.text, start_time=cue.start_time, end_time=cue.end_time))
    audit(db, 'timeline.build', 'project', project_id, actor_id, scenes=len(scenes))
    db.flush()
    return scenes
