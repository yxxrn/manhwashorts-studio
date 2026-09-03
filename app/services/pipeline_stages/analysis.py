"""Implementation details for the analysis pipeline stage.

Public callers should continue importing app.services.pipeline.
"""

from __future__ import annotations


def run_legacy_text_analysis(api, db, project_id, actor_id):
    """Extract story facts from all text assets, replacing any prior analysis."""
    PipelineError = api.PipelineError
    ProjectStatus = api.ProjectStatus
    StoryAnalysis = api.StoryAnalysis
    audit = api.audit
    get_project = api.get_project
    project_assets = api.project_assets
    resolver_svc = api.resolver_svc
    select = api.select
    text_sources = api.text_sources
    project = get_project(db, project_id)
    assets = project_assets(db, project_id)
    sources = text_sources(assets)
    if not sources:
        raise PipelineError('No text material to analyse. Paste a recap or upload a TXT/MD/PDF/DOCX first.')
    analyzer, decision = resolver_svc.resolve_analyzer(db, project.workspace_id)
    result = analyzer.analyze(sources)
    if decision.reason:
        result.low_confidence_notes.append(f'Analysis: {decision.reason}.')
    for old in db.scalars(select(StoryAnalysis).where(StoryAnalysis.project_id == project_id)):
        db.delete(old)
    row = StoryAnalysis(project_id=project_id, characters=[{'name': c.name, 'role': c.role, 'aliases': c.aliases, 'mentions': c.mentions, 'source_index': c.source_index} for c in result.characters], locations=result.locations, events=[{'order': e.order, 'text': e.text, 'kind': e.kind, 'source_index': e.source_index} for e in result.events], main_conflict=result.main_conflict, twist=result.twist, cliffhanger=result.cliffhanger, pronunciation_candidates=result.pronunciation_candidates, low_confidence_notes=result.low_confidence_notes)
    db.add(row)
    project.status = ProjectStatus.GENERATING
    audit(db, 'analysis.run', 'project', project_id, actor_id, generator=result.generator, provider_source=decision.source, provider=decision.provider, model=decision.model)
    db.flush()
    return row



def run_analysis(api, db, project_id, actor_id, *, narrative_profile_id):
    """Run only the complete, fail-closed vision-first analysis flow."""
    Mapping = api.Mapping
    PipelineError = api.PipelineError
    ProjectStatus = api.ProjectStatus
    StoryAnalysis = api.StoryAnalysis
    VisionCapabilityError = api.VisionCapabilityError
    VisionChapterSynthesisRequest = api.VisionChapterSynthesisRequest
    VisionProviderRequestFailed = api.VisionProviderRequestFailed
    VisionResponseInvalid = api.VisionResponseInvalid
    _AnalysisBlocked = api._AnalysisBlocked
    _VISION_BLOCKING_CODES = api._VISION_BLOCKING_CODES
    _build_source_inputs = api._build_source_inputs
    _classify_synthesis_output = api._classify_synthesis_output
    _coverage_manifest = api._coverage_manifest
    _coverage_overviews = api._coverage_overviews
    _derive_legacy_fields = api._derive_legacy_fields
    _enrich_observations = api._enrich_observations
    _observe_chunks = api._observe_chunks
    _panel_region_bounds = api._panel_region_bounds
    _panel_transport = api._panel_transport
    _vision_transport_estimated_bytes = api._vision_transport_estimated_bytes
    _synthesize_with_cache = api._synthesize_with_cache
    _persist_blocked_analysis = api._persist_blocked_analysis
    _persist_panel_regions = api._persist_panel_regions
    _preferred_visual_panel_ids = api._preferred_visual_panel_ids
    _frameable_preferred_visual_panel_ids = api._frameable_preferred_visual_panel_ids
    _frameable_preferred_visual_panel_selection = api._frameable_preferred_visual_panel_selection
    analyzer_contract = api.analyzer_contract
    audit = api.audit
    build_observation_chunks = api.build_observation_chunks
    get_project = api.get_project
    image_assets = api.image_assets
    narrative_identity = api.narrative_identity
    reference_profile = api.reference_profile
    project_assets = api.project_assets
    resolver_svc = api.resolver_svc
    secrets = api.secrets
    segmentation = api.segmentation
    select = api.select
    visual_scoring = api.visual_scoring
    project = get_project(db, project_id)
    resolved_reference_profile = reference_profile.resolve_reference_profile(project.template)
    target_word_count_min = None
    target_word_count_max = None
    if resolved_reference_profile is not None and (
        float(resolved_reference_profile.duration_min_s) >= 50.0
        and float(resolved_reference_profile.duration_max_s) <= 60.0
    ):
        target_word_count_min = 115
        target_word_count_max = 125
    selected_profile = None
    if narrative_profile_id is not None:
        try:
            selected_profile = narrative_identity.get_narrative_identity(narrative_profile_id)
            narrative_identity.load_narrative_instruction(narrative_profile_id)
        except narrative_identity.NarrativeIdentityError:
            raise PipelineError('narrative_profile_invalid') from None
    assets = image_assets(project_assets(db, project_id))
    run_id = secrets.token_hex(16)
    for old in db.scalars(select(StoryAnalysis).where(StoryAnalysis.project_id == project_id)):
        db.delete(old)
    db.flush()
    row = StoryAnalysis(project_id=project_id, analysis_run_id=run_id, state='PROCESSING', instruction_version=analyzer_contract.PROMPT_VERSION)
    db.add(row)
    project.status = ProjectStatus.GENERATING
    db.flush()
    try:
        try:
            instruction_version, instruction_sha256, instruction_text = analyzer_contract.load_analyzer_instruction(narrative_profile_id=narrative_profile_id)
        except analyzer_contract.AnalyzerContractError:
            raise _AnalysisBlocked('analyzer_contract_invalid', stage='instruction_load') from None
        try:
            visual_instruction_version, visual_instruction_sha256, _ = visual_scoring.load_visual_evidence_instruction()
        except Exception:
            raise _AnalysisBlocked('analyzer_contract_invalid', stage='visual_instruction_load') from None
        row.instruction_version = instruction_version
        row.instruction_sha256 = instruction_sha256
        if not assets:
            raise _AnalysisBlocked('vision_capability_missing', stage='image_input')
        try:
            inputs, asset_by_id = _build_source_inputs(assets)
            coverage = segmentation.build_complete_coverage_map(inputs, segmentation_version=segmentation.SEGMENTATION_VERSION)
        except _AnalysisBlocked:
            raise
        except Exception:
            raise _AnalysisBlocked('coverage_incomplete', stage='coverage_build') from None
        overview_errors = segmentation.verify_segmentation_completeness(_coverage_overviews(inputs, coverage), coverage)
        coverage_errors = tuple(sorted(set(coverage.reconciliation_errors + overview_errors)))
        row.coverage_manifest_json = _coverage_manifest(inputs, coverage)
        if coverage_errors or coverage.source_content_coverage_ratio != 1.0 or coverage.unresolved_material_area != 0:
            raise _AnalysisBlocked('coverage_incomplete', stage='coverage_reconcile', error_count=len(coverage_errors), coverage_map_hash=coverage.map_sha256)
        panel_regions = _persist_panel_regions(db, row, coverage, asset_by_id)
        if not panel_regions:
            raise _AnalysisBlocked('coverage_incomplete', stage='panel_persistence')
        input_by_asset = {item.source_asset_id: item for item in inputs}
        try:
            provider, capability = resolver_svc.resolve_vision(db, project.workspace_id)
        except Exception:
            raise _AnalysisBlocked('vision_capability_missing', stage='vision_resolve') from None
        if provider is None or capability is None or (not capability.available):
            code = getattr(capability, 'blocking_reason', None)
            if code not in _VISION_BLOCKING_CODES:
                code = 'vision_capability_missing'
            row.provider_type = getattr(capability, 'provider_type', None)
            row.provider_name = getattr(capability, 'provider_name', None)
            row.model_name = getattr(capability, 'model', None)
            raise _AnalysisBlocked(str(code), stage='vision_capability')
        row.provider_type = capability.provider_type
        row.provider_name = capability.provider_name
        row.model_name = capability.model
        panel_transports = {panel.panel_id: _panel_transport(panel, input_by_asset[panel.source_asset_id], coverage) for panel in panel_regions}
        estimated_bytes = {
            panel_id: _vision_transport_estimated_bytes(transport)
            for panel_id, transport in panel_transports.items()
        }
        chunks = build_observation_chunks(
            panel_regions,
            estimated_bytes_by_panel_id=estimated_bytes,
        )
        semantic, chunk_ledger, first_chunk = _observe_chunks(provider, chunks, panel_transports, analysis_run_id=run_id, instruction_version=instruction_version, instruction_sha256=instruction_sha256, visual_instruction_version=visual_instruction_version, visual_instruction_sha256=visual_instruction_sha256)
        enriched, chain_observations = _enrich_observations(panel_regions, semantic, first_chunk, coverage)
        preferred_visual_panel_ids, preferred_visual_panel_ids_by_section = (
            _frameable_preferred_visual_panel_selection(
                panel_regions, input_by_asset, resolved_reference_profile
            )
        )
        duplicate_observations = sum(len(chunk) for chunk in chunks) - len(enriched)
        manifest = _coverage_manifest(inputs, coverage, processed_panels=len(enriched), duplicate_observations=duplicate_observations)
        row.coverage_manifest_json = manifest
        synthesis_chunks = tuple({'chunk_id': item['chunk_id'], 'panel_ids': list(item['panel_ids']), 'observation_ids': list(item['observation_ids']), 'overlap_with_previous': list(item['overlap_with_previous']), 'overlap_with_next': list(item['overlap_with_next'])} for item in chunk_ledger)
        expected_panel_ids = tuple(panel.panel_id for panel in panel_regions)
        synthesis_request = VisionChapterSynthesisRequest(analysis_run_id=run_id, instruction_version=instruction_version, instruction_sha256=instruction_sha256, instruction_text=instruction_text, expected_panel_ids=expected_panel_ids, coverage_manifest=manifest, ordered_observations=tuple(enriched[panel_id] for panel_id in expected_panel_ids), chunks=synthesis_chunks, narrative_profile_id=selected_profile.profile_id if selected_profile is not None else None, narrative_profile_version=selected_profile.profile_version if selected_profile is not None else None, narrative_profile_sha256=selected_profile.contract_sha256 if selected_profile is not None else None, target_word_count_min=target_word_count_min, target_word_count_max=target_word_count_max, preferred_visual_panel_ids=preferred_visual_panel_ids if target_word_count_min is not None else (), preferred_visual_panel_ids_by_section=preferred_visual_panel_ids_by_section if target_word_count_min is not None else None)
        try:
            synthesis_output = _synthesize_with_cache(provider, synthesis_request)
        except analyzer_contract.AnalyzerContractError:
            raise _AnalysisBlocked('analyzer_contract_invalid', stage='synthesis_request') from None
        except VisionResponseInvalid as exc:
            raise _AnalysisBlocked(
                'vision_response_invalid',
                stage='synthesis_response',
                validation_subtype=str(getattr(exc, 'validation_subtype', '') or ''),
                passage_word_counts=list(getattr(exc, 'passage_word_counts', ()) or ()),
            ) from None
        except VisionProviderRequestFailed as exc:
            raise _AnalysisBlocked(
                'vision_provider_request_failed',
                stage='synthesis_provider',
                status_code=exc.status_code if exc.status_code is not None else 0,
                retryable=bool(exc.retryable),
                timeout=bool(getattr(exc, 'timeout', False)),
                transport_subtype=str(getattr(exc, 'transport_subtype', '') or ''),
            ) from None
        except VisionCapabilityError:
            raise _AnalysisBlocked('vision_provider_request_failed', stage='synthesis_provider') from None
        except Exception:
            raise _AnalysisBlocked('vision_provider_request_failed', stage='synthesis_provider') from None
        synthesis_output = _classify_synthesis_output(synthesis_output, expected_panel_ids, synthesis_chunks)
        try:
            analyzer_contract.validate_analyzer_output(synthesis_output, expected_panel_ids=expected_panel_ids, narrative_profile_id=narrative_profile_id)
        except analyzer_contract.AnalyzerContractError:
            raise _AnalysisBlocked('analysis_incomplete', stage='analyzer_validation') from None
        claims = synthesis_output['evidence_graph']['claims']
        panel_chain = [{'panel_id': panel.panel_id, 'source_asset_id': panel.source_asset_id, 'source_order': panel.source_order, 'bounds': _panel_region_bounds(panel)} for panel in panel_regions]
        reconciled, chain_errors = segmentation.reconcile_coverage_chain(coverage, panel_chain, chain_observations, chunk_ledger, claims)
        if not reconciled:
            if any(error.startswith('chain.chunk') for error in chain_errors):
                raise _AnalysisBlocked('analysis_chunk_link_missing', stage='chain_reconcile')
            if any(error.startswith('chain.claim') for error in chain_errors):
                raise _AnalysisBlocked('analysis_claim_evidence_missing', stage='chain_reconcile')
            raise _AnalysisBlocked('analysis_incomplete', stage='chain_reconcile')
        claim_refs = {claim['claim_id']: list(claim['evidence_panel_ids']) for claim in claims if isinstance(claim, Mapping)}
        manifest['claim_to_panel_refs'] = claim_refs
        row.coverage_manifest_json = manifest
        row.continuity_ledger_json = synthesis_output['continuity_ledger']
        row.evidence_graph_json = dict(synthesis_output['evidence_graph'])
        row.evidence_graph_json['script_passages'] = list(synthesis_output['script_passages'])
        row.story_spine_json = dict(synthesis_output['narrative_outline']['story_spine'])
        row.reconciliation_json = {'coverage_map_hash': coverage.map_sha256, 'coverage_map_version': coverage.version, 'canonical_panel_count': coverage.panel_count, 'processed_panel_count': len(enriched), 'duplicate_overlap_observations': duplicate_observations, 'chain_reconciled': True, 'chain_errors': list(chain_errors), 'narrative_screening_warning_codes': []}
        if selected_profile is not None:
            row.reconciliation_json['narrative_identity'] = {'profile_id': selected_profile.profile_id, 'version': selected_profile.profile_version, 'sha256': selected_profile.contract_sha256}
            row.reconciliation_json['narrative_ending_kind'] = synthesis_output['narrative_outline']['ending_kind']
        row.blocking_reasons_json = None
        _derive_legacy_fields(row, synthesis_output)
        row.state = 'RECONCILED'
        project.status = ProjectStatus.REVIEW
        audit(db, 'analysis.run', 'project', project_id, actor_id, generator='vision_first', provider=capability.provider_name, model=capability.model, state=row.state, panel_count=coverage.panel_count, processed_panel_count=len(enriched))
        db.flush()
        return row
    except _AnalysisBlocked as blocked:
        return _persist_blocked_analysis(db, project, row, [blocked.code], [blocked.finding])



def analysis_status(api, db, project_id):
    """Return a safe scalar/count summary of the latest analysis."""
    Mapping = api.Mapping
    _SAFE_STATUS_FINDING_KEYS = api._SAFE_STATUS_FINDING_KEYS
    _VISION_BLOCKING_CODES = api._VISION_BLOCKING_CODES
    latest_analysis = api.latest_analysis
    row = latest_analysis(db, project_id)
    if row is None:
        return None
    manifest = row.coverage_manifest_json if isinstance(row.coverage_manifest_json, Mapping) else {}
    reconciliation = row.reconciliation_json if isinstance(row.reconciliation_json, Mapping) else {}
    graph = row.evidence_graph_json if isinstance(row.evidence_graph_json, Mapping) else {}
    blocking = row.blocking_reasons_json if isinstance(row.blocking_reasons_json, Mapping) else {}
    safe_findings = []
    for finding in blocking.get('findings', []) if isinstance(blocking.get('findings', []), list) else []:
        if not isinstance(finding, Mapping):
            continue
        safe_findings.append({key: value for key, value in finding.items() if key in _SAFE_STATUS_FINDING_KEYS and isinstance(value, (str, int, float, bool))})
    return {'state': row.state, 'run_id': row.analysis_run_id, 'provider_type': row.provider_type, 'provider_name': row.provider_name, 'model': row.model_name, 'instruction_version': row.instruction_version, 'instruction_sha256': row.instruction_sha256, 'coverage_map_version': manifest.get('coverage_map_version'), 'coverage_map_hash': manifest.get('coverage_map_hash'), 'total_panels': manifest.get('total_panels', 0), 'processed_panels': manifest.get('processed_panels', 0), 'source_content_coverage_ratio': manifest.get('source_content_coverage_ratio', 0.0), 'unresolved_material_area': manifest.get('unresolved_material_area', 0), 'reconciliation_complete': manifest.get('reconciliation_complete', False), 'chain_reconciled': reconciliation.get('chain_reconciled', False), 'claim_count': len(graph.get('claims', [])) if isinstance(graph.get('claims'), list) else 0, 'passage_count': len(graph.get('script_passages', [])) if isinstance(graph.get('script_passages'), list) else 0, 'narrative_profile_id': reconciliation.get('narrative_identity', {}).get('profile_id') if isinstance(reconciliation.get('narrative_identity'), Mapping) else None, 'narrative_profile_version': reconciliation.get('narrative_identity', {}).get('version') if isinstance(reconciliation.get('narrative_identity'), Mapping) else None, 'narrative_profile_sha256': reconciliation.get('narrative_identity', {}).get('sha256') if isinstance(reconciliation.get('narrative_identity'), Mapping) else None, 'narrative_screening_warning_codes': [code for code in reconciliation.get('narrative_screening_warning_codes', []) if isinstance(code, str)] if isinstance(reconciliation.get('narrative_screening_warning_codes'), list) else [], 'blocking_codes': [code for code in blocking.get('codes', []) if isinstance(code, str) and code in _VISION_BLOCKING_CODES] if isinstance(blocking.get('codes', []), list) else [], 'findings': safe_findings}
