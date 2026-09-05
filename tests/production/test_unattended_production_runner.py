from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "production_run.py"


def _runner_module():
    spec = importlib.util.spec_from_file_location("production_run_test_module", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_launcher_sources_runtime_env_before_runner():
    launcher = (ROOT / "scripts" / "manhwashorts").read_text(encoding="utf-8")
    block = launcher.split("production-run)", 1)[1].split("youtube-account)", 1)[0]
    assert "source \"$RUNTIME_ENV\"" in block
    assert "scripts/production_run.py" in block
    assert block.index("source \"$RUNTIME_ENV\"") < block.index("scripts/production_run.py")
    assert "Missing runtime env" in block


def test_unattended_runner_accepts_explicit_voice_profile():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'parser.add_argument("--voice-id", default=DEFAULT_ENGLISH_VOICE_ID)' in source
    assert 'voice_id=args.voice_id' in source
    assert 'watermark_enabled=bool(args.watermark)' in source
    assert 'watermark_text=args.watermark_text' in source
    assert 'action=argparse.BooleanOptionalAction' in source
    assert 'Production preflight ready.' in source
    assert 'speed=1.0' in source


def test_analysis_stage_retry_is_narrow_and_fail_closed():
    runner = _runner_module()
    assert {
        "vision_provider_request_failed",
        "vision_response_invalid",
    } == runner.TRANSIENT_ANALYSIS_CODES
    assert "vision_capability_missing" not in runner.TRANSIENT_ANALYSIS_CODES
    assert "coverage_incomplete" not in runner.TRANSIENT_ANALYSIS_CODES
def test_production_retry_classifier_retries_transport_but_not_qc():
    runner = _runner_module()

    assert runner._transient_exception(RuntimeError("TTS HTTP request failed: 503"))
    assert runner._transient_exception(TimeoutError("provider timed out"))
    assert runner._transient_exception(RuntimeError("connection reset by peer"))
    assert not runner._transient_exception(RuntimeError("visual.blank_infeasible"))
    assert not runner._transient_exception(RuntimeError("quality_blocked"))


def test_run_state_identity_prevents_reusing_wrong_corpus(tmp_path):
    runner = _runner_module()
    state_path = tmp_path / "run8.json"
    state_path.write_text(
        '{"title":"Infinite Mage","chapter_from":156.0,"chapter_to":158.0,"source_id":"source-a"}',
        encoding="utf-8",
    )
    args = type(
        "Args",
        (),
        {
            "run_id": "run8",
            "title": "Infinite Mage",
            "chapter_from": 155.0,
            "chapter_to": 158.0,
            "source_id": "source-a",
            "language": "en",
        },
    )()
    with pytest.raises(RuntimeError, match="identity"):
        runner._load_state(state_path, args)
def test_analysis_transient_blocker_retries_and_logs_without_collision(tmp_path, monkeypatch):
    from types import SimpleNamespace

    runner = _runner_module()
    blocked = SimpleNamespace(
        state="BLOCKED",
        blocking_reasons_json={"codes": ["vision_response_invalid"]},
        reconciliation_json={},
        coverage_manifest_json={"processed_panels": 301},
    )
    reconciled = SimpleNamespace(
        state="RECONCILED",
        blocking_reasons_json={},
        reconciliation_json={"performance": {"observation": {}, "frameability": {}}},
        coverage_manifest_json={"processed_panels": 301},
    )
    calls = iter((blocked, reconciled))
    monkeypatch.setattr(runner.pl, "latest_analysis", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.pl, "run_analysis", lambda *_a, **_k: next(calls))
    monkeypatch.setattr(runner.time, "sleep", lambda *_a, **_k: None)

    class Db:
        def commit(self):
            return None

    args = SimpleNamespace(max_analysis_attempts=2, retry_delay_s=0.0)
    state = {"events": [], "stages": {}}
    result = runner._ensure_analysis(
        Db(), args, state, tmp_path / "state.json", SimpleNamespace(id="u"), SimpleNamespace(id="p")
    )
    assert result is reconciled
    attempts = [event for event in state["events"] if event["event"] == "analysis.attempt"]
    assert [event["analysis_state"] for event in attempts] == ["BLOCKED", "RECONCILED"]


def test_stage_preserves_domain_duration_and_records_wall_time(tmp_path, monkeypatch):
    runner = _runner_module()
    monkeypatch.setattr(runner.time, "perf_counter", lambda: 12.5)
    state = {"events": [], "stages": {}}
    runner._stage(
        state,
        tmp_path / "state.json",
        "final_validation",
        10.0,
        duration_s=50.667,
        qc_pass=True,
    )
    stage = state["stages"]["final_validation"]
    assert stage["duration_s"] == 50.667
    assert stage["stage_wall_s"] == 2.5
    event = state["events"][-1]
    assert event["duration_s"] == 50.667
    assert event["stage_wall_s"] == 2.5


def test_successful_state_does_not_keep_stale_failure(tmp_path, monkeypatch):
    runner = _runner_module()
    state_path = tmp_path / "run8.json"
    state = {
        "status": "PASS",
        "failure": {"type": "OldFailure"},
        "result": {"project_id": "p"},
    }
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_parse_args",
        lambda: type(
            "Args",
            (),
            {
                "run_id": "run8",
                "title": "Infinite Mage",
                "chapter_from": 156.0,
                "chapter_to": 158.0,
                "source_id": "6247824327199706550",
                "language": "en",
                "state_dir": str(tmp_path),
            },
        )(),
    )
    full = {
        "contract": "unattended-production-run-v1",
        "run_id": "run8",
        "title": "Infinite Mage",
        "chapter_from": 156.0,
        "chapter_to": 158.0,
        "source_id": "6247824327199706550",
        "language": "en",
        **state,
    }
    state_path.write_text(__import__("json").dumps(full), encoding="utf-8")
    assert runner.main() == 0
    saved = __import__("json").loads(state_path.read_text(encoding="utf-8"))
    assert saved["status"] == "PASS"
    assert "failure" not in saved


def test_production_profiler_records_and_restores_wrapped_boundaries(monkeypatch):
    runner = _runner_module()

    replacements = {
        (runner.pl, "generate_voiceover"): lambda *a, **k: "tts-ok",
        (runner.pl, "build_timeline"): lambda *a, **k: "timeline-ok",
        (runner.pl, "run_quality_checks"): lambda *a, **k: [],
        (runner.pl, "enqueue_render"): lambda *a, **k: "enqueue-ok",
        (runner.pl, "execute_render"): lambda *a, **k: "render-ok",
        (runner.pl, "_ensure_final_thumbnail"): lambda *a, **k: "thumb-ok",
        (runner.production_stage, "_write_manual_upload_metadata"): lambda *a, **k: "meta-ok",
    }
    for (owner, name), replacement in replacements.items():
        monkeypatch.setattr(owner, name, replacement)

    telemetry, originals = runner._install_production_profiler()
    try:
        assert runner.pl.generate_voiceover() == "tts-ok"
        assert runner.pl.run_quality_checks(job=None) == []
        assert runner.pl.run_quality_checks(job=object()) == []
        assert runner.production_stage._write_manual_upload_metadata() == "meta-ok"
    finally:
        runner._restore_production_profiler(originals)

    assert telemetry["tts"]["calls"] == 1
    assert telemetry["pre_render_qc"]["calls"] == 1
    assert telemetry["post_render_qc"]["calls"] == 1
    assert telemetry["metadata"]["calls"] == 1
    for (owner, name), replacement in replacements.items():
        assert getattr(owner, name) is replacement

def test_preflight_tts_probe_retries_only_transient_failures():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    start = source.index('tts_probe = Path(settings.tmp_dir)')
    end = source.index('connector = source_router._ready_client()', start)
    block = source[start:end]
    assert 'for tts_attempt in range(1, 4):' in block
    assert '_transient_exception(exc)' in block
    assert 'tts_attempt >= 3' in block
    assert '"preflight.tts_retry"' in block


def test_script_resume_regenerates_when_latest_analysis_changes():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    start = source.index('def _ensure_script(')
    end = source.index('def _install_production_profiler', start)
    block = source[start:end]
    assert 'analysis = pl.latest_analysis(db, project.id)' in block
    assert 'script_metadata.get("analysis_id") != analysis.id' in block
    assert 'analysis_id=analysis.id' in block
