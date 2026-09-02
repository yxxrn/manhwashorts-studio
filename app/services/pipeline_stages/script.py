"""Implementation details for the script pipeline stage.

Public callers should continue importing app.services.pipeline.
"""

from __future__ import annotations

from typing import Any


def generate_script(api, db, project_id, *, analysis_id, keep_locked, hook_count, seed, actor_id, narrative_profile_id):
    """Materialize provider passages from the latest reconciled evidence."""
    Mapping = api.Mapping
    PipelineError = api.PipelineError
    ProjectStatus = api.ProjectStatus
    ScriptSection = api.ScriptSection
    ScriptVersion = api.ScriptVersion
    StoryAnalysis = api.StoryAnalysis
    _VISION_ROLE_TO_SECTION = api._VISION_ROLE_TO_SECTION
    _requested_narrative_profile = api._requested_narrative_profile
    _validated_persisted_vision_output = api._validated_persisted_vision_output
    audit = api.audit
    editorial_qc = api.editorial_qc
    get_project = api.get_project
    latest_analysis = api.latest_analysis
    latest_script_row = api.latest_script_row
    quality_svc = api.quality_svc
    script_svc = api.script_svc
    project = get_project(db, project_id)
    row = db.get(StoryAnalysis, analysis_id) if analysis_id is not None else latest_analysis(db, project_id)
    if row is None:
        raise PipelineError('run vision analysis before generating a script')
    if row.project_id != project_id:
        raise PipelineError('analysis_project_mismatch')
    if row.state != 'RECONCILED':
        raise PipelineError('script generation requires reconciled vision analysis')
    profile = _requested_narrative_profile(row, narrative_profile_id)
    output, panels = _validated_persisted_vision_output(db, row)
    previous = latest_script_row(db, project_id)
    _ = (keep_locked, hook_count, seed, actor_id)
    claim_map = {claim['claim_id']: claim for claim in output['evidence_graph']['claims'] if isinstance(claim, Mapping)}
    panel_orders = {panel.panel_id: panel.source_order for panel in panels}
    sections: list[dict[str, Any]] = []
    passage_count = len(output['script_passages'])
    duration_estimator = script_svc.estimate_narration_duration if profile is not None else script_svc.estimate_duration
    for index, passage in enumerate(output['script_passages']):
        role = passage['editorial_role']
        claim_ids = list(passage['claim_ids'])
        evidence_panel_ids = list(passage['evidence_panel_ids'])
        evidence = [{'claim_id': claim_id, 'panel_ids': list(claim_map[claim_id]['evidence_panel_ids'])} for claim_id in claim_ids]
        if profile is None:
            section_name = _VISION_ROLE_TO_SECTION[role]
        elif index == 0:
            section_name = ScriptSection.HOOK.value
        elif index == 1:
            section_name = ScriptSection.SETUP.value
        elif index == passage_count - 1:
            section_name = ScriptSection.CTA.value
        elif index == passage_count - 2 and passage_count >= 5:
            section_name = ScriptSection.TWIST.value
        else:
            section_name = ScriptSection.CONFLICT.value
        sections.append({'section': section_name, 'text': passage['text'], 'locked': False, 'editorial_role': role, 'claim_ids': claim_ids, 'evidence_panel_ids': evidence_panel_ids, 'evidence': evidence, 'estimated_duration': duration_estimator(passage['text'], project.narration_style), 'citations': sorted({panel_orders[panel_id] for panel_id in evidence_panel_ids})})
    warnings: list[dict[str, Any]] = []
    if profile is not None:
        claims_for_screen = {claim['claim_id']: claim for claim in output['evidence_graph']['claims'] if isinstance(claim, Mapping) and isinstance(claim.get('claim_id'), str)}
        report = editorial_qc.screen_narrative_naturalness(output['script_passages'], claims_for_screen, profile)
        for check in quality_svc.check_narrative_naturalness(report):
            if not check.passed:
                warnings.append({'code': check.code, 'severity': check.severity, 'message': check.message, 'detail': dict(check.detail)})
        reconciliation = dict(row.reconciliation_json or {})
        reconciliation['narrative_screening_warning_codes'] = [warning['code'] for warning in warnings]
        row.reconciliation_json = reconciliation
    script_text = '\n'.join(section['text'] for section in sections)
    if profile is not None:
        estimated_duration = script_svc.estimate_narration_duration(script_text, project.narration_style)
        script_word_count = script_svc.narration_word_count(script_text)
    else:
        estimated_duration = round(sum(section['estimated_duration'] for section in sections), 2)
        script_word_count = script_svc.word_count(script_text)
    version = previous.version + 1 if previous else 1
    script_row = ScriptVersion(project_id=project_id, version=version, sections=sections, hook_options=[output['script_passages'][0]['text']], selected_hook=0, estimated_duration=estimated_duration, word_count=script_word_count, warnings=warnings, generator='vision_evidence_v3' if profile is not None else 'vision_evidence_v2', editorial_metadata={'analysis_id': row.id, 'analysis_run_id': row.analysis_run_id, 'instruction_version': row.instruction_version, 'instruction_sha256': row.instruction_sha256, 'human_review_required': True, 'editorial_review_confirmed': False, 'editorial_review_actor': ''})
    if profile is not None:
        script_row.editorial_metadata['narrative_identity'] = {'profile_id': profile.profile_id, 'version': profile.profile_version, 'sha256': profile.contract_sha256}
        script_row.editorial_metadata['duration_contract'] = script_svc.narration_duration_contract(project.narration_style)
        reconciliation = row.reconciliation_json if isinstance(row.reconciliation_json, Mapping) else {}
        duration_policy = reconciliation.get('duration_policy_contract')
        if isinstance(duration_policy, Mapping) and duration_policy.get('adaptive') is True:
            script_row.editorial_metadata['duration_policy_contract'] = dict(duration_policy)
    db.add(script_row)
    row.state = 'SCRIPT_DRAFT'
    project.status = ProjectStatus.REVIEW
    audit(db, 'script.generate', 'project', project_id, actor_id, version=version)
    db.flush()
    return script_row



def update_script(api, db, script_id, sections, *, selected_hook, actor_id):
    """Apply user edits. Editing clears approval so review cannot be bypassed."""
    PipelineError = api.PipelineError
    ScriptSection = api.ScriptSection
    ScriptVersion = api.ScriptVersion
    audit = api.audit
    get_project = api.get_project
    latest_analysis = api.latest_analysis
    script_svc = api.script_svc
    script = db.get(ScriptVersion, script_id)
    if script is None:
        raise PipelineError('script version not found')
    project = get_project(db, script.project_id)
    use_narration_duration_contract = script.generator == 'vision_evidence_v3'
    duration_estimator = script_svc.estimate_narration_duration if use_narration_duration_contract else script_svc.estimate_duration
    valid_sections = {s.value for s in ScriptSection}
    cleaned: list[dict] = []
    for section in sections:
        name = section.get('section')
        if name not in valid_sections:
            raise PipelineError(f'unknown script section: {name!r}')
        text = str(section.get('text', '')).strip()
        cleaned.append({'section': name, 'text': text, 'locked': bool(section.get('locked', False)), 'editorial_role': str(section.get('editorial_role', '')), 'claim_ids': list(section.get('claim_ids', []) or []), 'evidence_panel_ids': list(section.get('evidence_panel_ids', []) or []), 'evidence': list(section.get('evidence', []) or []), 'estimated_duration': duration_estimator(text, project.narration_style), 'citations': list(section.get('citations', []) or [])})
    script.sections = cleaned
    if selected_hook is not None:
        script.selected_hook = max(0, min(selected_hook, max(0, len(script.hook_options) - 1)))
    if use_narration_duration_contract:
        script.estimated_duration = script_svc.estimate_narration_duration(script.plain_text, project.narration_style)
        script.word_count = script_svc.narration_word_count(script.plain_text)
    else:
        script.estimated_duration = round(sum(s['estimated_duration'] for s in cleaned), 2)
        script.word_count = script_svc.word_count(script.plain_text)
    metadata = dict(script.editorial_metadata or {})
    metadata['human_review_required'] = True
    metadata['editorial_review_confirmed'] = False
    metadata['editorial_review_actor'] = ''
    if use_narration_duration_contract:
        metadata['duration_contract'] = script_svc.narration_duration_contract(project.narration_style)
    script.editorial_metadata = metadata
    script.approved_at = None
    script.approved_by = ''
    analysis = latest_analysis(db, script.project_id)
    if analysis is not None and (not metadata.get('analysis_id') or metadata.get('analysis_id') == analysis.id):
        analysis.state = 'SCRIPT_DRAFT'
    audit(db, 'script.update', 'script_version', script.id, actor_id)
    db.flush()
    return script



def approve_script(api, db, script_id, actor_id, *, editorial_review_confirmed, approval_actor_type="human", approval_reason=""):
    """Approve only a current, explicitly confirmed evidence-backed script."""
    Mapping = api.Mapping
    PipelineError = api.PipelineError
    ScriptVersion = api.ScriptVersion
    _VISION_ROLE_TO_SECTION = api._VISION_ROLE_TO_SECTION
    _VISION_SCRIPT_ROLES = api._VISION_SCRIPT_ROLES
    _narrative_identity_from_analysis = api._narrative_identity_from_analysis
    _now = api._now
    _script_content_hash = api._script_content_hash
    _validated_persisted_vision_output = api._validated_persisted_vision_output
    audit = api.audit
    editorial_qc = api.editorial_qc
    latest_analysis = api.latest_analysis
    latest_script_row = api.latest_script_row
    quality_svc = api.quality_svc
    script = db.get(ScriptVersion, script_id)
    if script is None:
        raise PipelineError('script version not found')
    if not actor_id.strip():
        raise PipelineError('an editorial review actor is required')
    if editorial_review_confirmed is not True:
        raise PipelineError('explicit editorial review confirmation is required')
    if approval_actor_type not in {'human', 'trusted_agent'}:
        raise PipelineError('unknown script approval actor type')
    if approval_actor_type == 'trusted_agent' and not str(approval_reason).strip():
        raise PipelineError('trusted-agent approval requires an explicit reason')
    latest_script = latest_script_row(db, script.project_id)
    if latest_script is None or latest_script.id != script.id:
        raise PipelineError('only the latest script version can be approved')
    metadata = script.editorial_metadata if isinstance(script.editorial_metadata, Mapping) else {}
    analysis = latest_analysis(db, script.project_id)
    if analysis is None or analysis.state != 'SCRIPT_DRAFT':
        raise PipelineError('script approval requires the linked SCRIPT_DRAFT analysis')
    if metadata.get('analysis_id') != analysis.id or metadata.get('analysis_run_id') != analysis.analysis_run_id:
        raise PipelineError('script is not linked to the latest analysis')
    output, panels = _validated_persisted_vision_output(db, analysis, required_state='SCRIPT_DRAFT')
    claim_map = {claim['claim_id']: claim for claim in output['evidence_graph']['claims'] if isinstance(claim, Mapping)}
    panel_ids = {panel.panel_id for panel in panels}
    profile = _narrative_identity_from_analysis(analysis)
    script_identity = metadata.get('narrative_identity')
    if profile is not None:
        if script.generator != 'vision_evidence_v3' or not isinstance(script_identity, Mapping) or script_identity.get('profile_id') != profile.profile_id or (script_identity.get('version') != profile.profile_version) or (script_identity.get('sha256') != profile.contract_sha256):
            raise PipelineError('narrative_identity_invalid')
        if metadata.get('human_review_required') is not True:
            raise PipelineError('narrative_identity_invalid')
        if not 4 <= len(script.sections or []) <= 6:
            raise PipelineError('script must contain four to six evidence-backed sections')
        for section in script.sections:
            if not isinstance(section, Mapping) or not str(section.get('editorial_role', '')).strip() or (not str(section.get('text', '')).strip()):
                raise PipelineError('script section roles or text are invalid')
            claim_ids = section.get('claim_ids')
            evidence_panel_ids = section.get('evidence_panel_ids')
            if not isinstance(claim_ids, list) or not claim_ids or (not isinstance(evidence_panel_ids, list)) or (not evidence_panel_ids) or (not set(claim_ids) <= set(claim_map)) or (not set(evidence_panel_ids) <= panel_ids):
                raise PipelineError('script section evidence is incomplete')
            required_evidence = set().union(*(set(claim_map[claim_id]['evidence_panel_ids']) for claim_id in claim_ids))
            if not required_evidence <= set(evidence_panel_ids):
                raise PipelineError('script section evidence does not cover its claims')
        current_passages = [{'text': section['text'], 'claim_ids': list(section['claim_ids']), 'evidence_panel_ids': list(section['evidence_panel_ids'])} for section in script.sections]
        report = editorial_qc.screen_narrative_naturalness(current_passages, claim_map, profile)
        refreshed_warnings = [{'code': check.code, 'severity': check.severity, 'message': check.message, 'detail': dict(check.detail)} for check in quality_svc.check_narrative_naturalness(report) if not check.passed]
        if any(item['severity'] == 'error' for item in refreshed_warnings):
            raise PipelineError('narrative editorial screening failed')
        script.warnings = refreshed_warnings
    else:
        if len(script.sections or []) != len(_VISION_SCRIPT_ROLES):
            raise PipelineError('script must contain exactly five evidence-backed sections')
        for expected_role, section in zip(_VISION_SCRIPT_ROLES, script.sections, strict=True):
            if section.get('section') != _VISION_ROLE_TO_SECTION[expected_role] or section.get('editorial_role') != expected_role or (not str(section.get('text', '')).strip()):
                raise PipelineError('script section roles or text are invalid')
            claim_ids = section.get('claim_ids')
            evidence_panel_ids = section.get('evidence_panel_ids')
            if not isinstance(claim_ids, list) or not claim_ids or (not isinstance(evidence_panel_ids, list)) or (not evidence_panel_ids) or (not set(claim_ids) <= set(claim_map)) or (not set(evidence_panel_ids) <= panel_ids):
                raise PipelineError('script section evidence is incomplete')
            required_evidence = set().union(*(set(claim_map[claim_id]['evidence_panel_ids']) for claim_id in claim_ids))
            if not required_evidence <= set(evidence_panel_ids):
                raise PipelineError('script section evidence does not cover its claims')
    blocking = [w for w in script.warnings or [] if w.get('severity') == 'error']
    if blocking:
        raise PipelineError('Fix these before approving: ' + '; '.join(w.get('message', w.get('code', '')) for w in blocking))
    if not script.plain_text.strip():
        raise PipelineError('script is empty')
    script.approved_at = _now()
    script.approved_by = actor_id
    approved_hash = _script_content_hash(script)
    script.editorial_metadata = {**metadata, 'human_review_required': True, 'editorial_review_confirmed': True, 'editorial_review_actor': actor_id, 'approval_actor_type': approval_actor_type, 'approval_reason': str(approval_reason), 'human_review_performed': approval_actor_type == 'human', 'approved_script_hash': approved_hash, 'approved_script_version': script.version}
    analysis.state = 'SCRIPT_APPROVED'
    audit(db, 'script.approve', 'script_version', script.id, actor_id, version=script.version, approval_actor_type=approval_actor_type, approval_reason=str(approval_reason))
    db.flush()
    return script



def generate_draft(api, db, project_id, actor_id, seed):
    """Materialize a vision-evidence script draft and stop before media stages.

    Voice-over, timeline, cues, and rendering require explicit human approval.
    This path never starts analysis or falls back to a text/rules workflow.
    """
    PipelineError = api.PipelineError
    ProjectStatus = api.ProjectStatus
    generate_script = api.generate_script
    get_project = api.get_project
    latest_analysis = api.latest_analysis
    row = latest_analysis(db, project_id)
    if row is None:
        raise PipelineError('run vision analysis before generating a draft')
    if row.state != 'RECONCILED':
        raise PipelineError('draft generation requires reconciled vision analysis')
    script = generate_script(db, project_id, seed=seed, actor_id=actor_id)
    project = get_project(db, project_id)
    project.status = ProjectStatus.REVIEW
    db.flush()
    return {'script_id': script.id, 'script_version': script.version, 'estimated_duration': script.estimated_duration, 'audio_duration': 0.0, 'segments': 0, 'scenes': 0, 'cues': 0, 'warnings': script.warnings}
