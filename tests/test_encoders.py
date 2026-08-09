"""Encoder selection tests: CPU or GPU (v1.2).

These run on any machine, with or without a GPU. Where a test needs a specific
backend to be "present", it constructs the Selection directly rather than
requiring the hardware, so CI on a GPU-less box still covers the flag building.

The behavioural contract under test:

* Detection proves an encoder *works*, not merely that FFmpeg lists it.
* An unavailable GPU falls back to CPU instead of failing a render.
* A fallback is always recorded, never silent.
* An unknown encoder *name* is rejected, because a typo must not be mistaken for
  a working GPU configuration.
"""

from __future__ import annotations

import pytest

from app.services import encoders as enc

pytestmark = pytest.mark.usefixtures("app_settings")


# --- catalogue -------------------------------------------------------------


def test_catalogue_covers_all_vendors():
    keys = {spec.key for spec in enc.known_encoders()}
    assert keys == {"cpu", "nvenc", "qsv", "vaapi", "videotoolbox"}


def test_cpu_is_never_marked_hardware():
    assert enc.CPU.hardware is False
    assert all(s.hardware for s in enc.known_encoders() if s.key != "cpu")


def test_unknown_encoder_name_is_rejected():
    """A typo must not silently become a CPU render.

    Note ``"NVENC "`` is deliberately absent: get_spec normalises case and
    whitespace, so that one is a valid request (see the case-insensitivity test).
    """
    for bad in ["nvnec", "gpu", "cuda", "; rm -rf /", "h264_nvenc"]:
        with pytest.raises(ValueError, match="unknown encoder"):
            enc.get_spec(bad)


def test_auto_is_not_a_concrete_spec():
    with pytest.raises(ValueError):
        enc.get_spec("auto")


def test_get_spec_is_case_insensitive():
    assert enc.get_spec("CPU").key == "cpu"
    assert enc.get_spec(" nvenc ").key == "nvenc"


# --- probing ---------------------------------------------------------------


def test_cpu_always_probes_successfully():
    works, _ = enc.probe("cpu")
    assert works, "libx264 must be usable or nothing can render"


def test_probe_results_are_cached():
    """Probing spawns a process; repeated calls must not."""
    first = enc.probe("cpu")
    second = enc.probe("cpu")
    assert first == second
    assert enc.probe.cache_info().hits > 0


def test_unavailable_backend_reports_a_reason():
    """Every unavailable encoder must explain itself, not just say False."""
    for spec in enc.known_encoders():
        works, reason = enc.probe(spec.key)
        if not works:
            assert reason.strip(), f"{spec.key} was unavailable with no explanation"


def test_vaapi_requires_a_render_node_not_just_a_card():
    """/dev/dri/card0 exists on VMs with a virtual VGA adapter that cannot encode."""
    works, reason = enc.probe("vaapi")
    if not works and not enc._vaapi_device_exists():
        assert "renderD" in reason


# --- selection and fallback ------------------------------------------------


def test_select_cpu_is_honoured():
    selection = enc.select("cpu")
    assert selection.key == "cpu"
    assert selection.fell_back is False
    assert selection.hardware is False


def test_select_auto_always_returns_something_usable():
    selection = enc.select("auto")
    assert selection.key in {s.key for s in enc.known_encoders()}
    assert enc.probe(selection.key)[0], "auto must pick a working encoder"
    assert selection.reason


def test_requesting_an_unavailable_gpu_falls_back_with_a_reason():
    """The headline safety property: a missing GPU slows a render, never breaks it."""
    unavailable = [s for s in enc.known_encoders() if s.hardware and not enc.probe(s.key)[0]]
    if not unavailable:
        pytest.skip("this machine has every GPU backend working")

    selection = enc.select(unavailable[0].key)
    assert selection.key == "cpu"
    assert selection.fell_back is True
    assert unavailable[0].label in selection.reason
    assert selection.reason != ""


def test_select_rejects_a_typo_rather_than_falling_back():
    """Distinct from a hardware fallback: a bad *name* is a config error."""
    with pytest.raises(ValueError):
        enc.select("nvnec")


def test_selection_serialises_for_the_api():
    payload = enc.select("cpu").as_dict()
    assert set(payload) == {
        "encoder", "label", "codec", "hardware", "requested", "fell_back", "reason",
    }


# --- flag construction -----------------------------------------------------


def test_cpu_flags_match_the_v1_0_baseline():
    """Quality reference: changing this silently would alter every output."""
    args = enc.video_args(enc.select("cpu"))
    assert args == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p"]


def test_preview_trades_quality_for_speed():
    final = enc.video_args(enc.select("cpu"), preview=False)
    preview = enc.video_args(enc.select("cpu"), preview=True)
    assert preview != final
    assert "ultrafast" in preview


def _fake(spec: enc.EncoderSpec) -> enc.Selection:
    """A Selection for a backend this machine may not have, to test flag output."""
    return enc.Selection(spec=spec, requested=spec.key, reason="test")


@pytest.mark.parametrize(
    "spec",
    [enc.NVENC, enc.QSV, enc.VAAPI, enc.VIDEOTOOLBOX],
    ids=lambda s: s.key,
)
def test_every_hardware_backend_names_its_codec(spec):
    args = enc.video_args(_fake(spec))
    assert "-c:v" in args
    assert args[args.index("-c:v") + 1] == spec.codec


def test_vaapi_uploads_frames_and_skips_pix_fmt():
    """VAAPI encodes from GPU surfaces: -pix_fmt would fight the hwupload."""
    selection = _fake(enc.VAAPI)
    chain = enc.apply_filter_suffix(selection, "zoompan=z=1.1,format=yuv420p")

    assert chain.endswith("format=nv12,hwupload")
    # The CPU-side format must not survive alongside the hardware one.
    assert "format=yuv420p" not in chain
    assert "-pix_fmt" not in enc.video_args(selection)
    assert enc.input_args(selection) == ["-vaapi_device", "/dev/dri/renderD128"]


def test_software_backends_do_not_touch_the_filter_chain():
    for spec in (enc.CPU, enc.NVENC, enc.QSV, enc.VIDEOTOOLBOX):
        selection = _fake(spec)
        assert enc.apply_filter_suffix(selection, "scale=2:2") == "scale=2:2"


def test_filter_suffix_handles_an_empty_chain():
    assert enc.apply_filter_suffix(_fake(enc.VAAPI), "") == "format=nv12,hwupload"


def test_qsv_sets_up_its_device_before_input():
    assert enc.input_args(_fake(enc.QSV))[:2] == ["-init_hw_device", "qsv=hw"]


def test_cpu_needs_no_input_args():
    assert enc.input_args(enc.select("cpu")) == []


# --- capability report -----------------------------------------------------


def test_describe_reports_every_backend():
    report = enc.describe()
    assert {e["key"] for e in report["encoders"]} == {
        s.key for s in enc.known_encoders()
    }
    assert report["active"]["encoder"]
    assert isinstance(report["gpu_available"], bool)


def test_describe_marks_cpu_available():
    report = enc.describe()
    cpu = next(e for e in report["encoders"] if e["key"] == "cpu")
    assert cpu["available"] is True


def test_gpu_available_agrees_with_the_per_encoder_flags():
    report = enc.describe()
    expected = any(e["available"] and e["hardware"] for e in report["encoders"])
    assert report["gpu_available"] is expected


# --- HTTP surface ----------------------------------------------------------


def test_encoders_endpoint_is_public(client):
    """Static machine capability, no user data: the UI needs it before login."""
    response = client.get("/api/encoders")
    assert response.status_code == 200
    body = response.json()
    assert body["encoders"]
    assert body["active"]["encoder"]


def test_health_reports_the_active_encoder(client):
    body = client.get("/api/health").json()
    assert body["video_encoder"] in {s.key for s in enc.known_encoders()}
    assert isinstance(body["gpu_encoding"], bool)


@pytest.mark.parametrize("value", ["auto", "cpu", "nvenc", "qsv", "vaapi", "videotoolbox"])
def test_render_endpoint_accepts_every_valid_encoder(auth_client, value):
    """Valid names must pass validation; they then fail on pipeline state."""
    project = auth_client.post(
        "/api/projects", json={"title": "E", "manhwa_title": "M", "chapter": "1"}
    ).json()
    response = auth_client.post(
        f"/api/projects/{project['id']}/render",
        json={"kind": "final", "encoder": value},
    )
    # 422 here is the *pipeline* refusing (no timeline yet), not the schema.
    assert response.status_code == 422
    assert "pattern" not in str(response.json()["detail"])


@pytest.mark.parametrize("value", ["nvnec", "gpu", "CPU", "cpu; touch /tmp/x", ""])
def test_render_endpoint_rejects_invalid_encoders(auth_client, value):
    """An encoder name reaches an FFmpeg command line, so it must be constrained."""
    project = auth_client.post(
        "/api/projects", json={"title": "E", "manhwa_title": "M", "chapter": "1"}
    ).json()
    response = auth_client.post(
        f"/api/projects/{project['id']}/render",
        json={"kind": "final", "encoder": value},
    )
    assert response.status_code == 422
    assert "pattern" in str(response.json()["detail"])


# --- persistence -----------------------------------------------------------


def test_job_records_the_requested_encoder(db, recap_text, declared_rights, panel_bytes):
    """The worker may run elsewhere, so the choice travels on the job row."""
    from app.constants import AssetType, LicenseType, RightsStatus
    from app.models import Project, SourceAsset, User, Workspace
    from app.security import hash_password
    from app.services import pipeline as pl
    from app.services import storage

    user = User(email="enc@example.com", password_hash=hash_password("testpass1234"))
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="WS")
    db.add(workspace)
    db.flush()
    project = Project(
        workspace_id=workspace.id,
        title="Enc",
        manhwa_title="M",
        chapter="1",
        template="classic",
        language="id",
        voice_id="id",
    )
    db.add(project)
    db.flush()

    stored = storage.put_bytes(f"projects/{project.id}/text", "r.txt", recap_text.encode())
    db.add(
        SourceAsset(
            project_id=project.id,
            type=AssetType.TEXT,
            original_filename="r.txt",
            storage_key=stored.storage_key,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
            mime_type="text/plain",
            extracted_text=recap_text,
            rights_status=RightsStatus.DECLARED,
            license_type=LicenseType.OWNED,
            rights_owner="Tester",
        )
    )
    image_stored = storage.put_bytes(
        f"projects/{project.id}/images", "panel.jpg", panel_bytes
    )
    db.add(
        SourceAsset(
            project_id=project.id,
            type=AssetType.IMAGE,
            original_filename="panel.jpg",
            storage_key=image_stored.storage_key,
            size_bytes=image_stored.size_bytes,
            checksum=image_stored.checksum,
            mime_type="image/jpeg",
            width=900,
            height=1200,
            original_width=900,
            original_height=1200,
            original_checksum=image_stored.checksum,
            rights_status=RightsStatus.DECLARED,
            license_type=LicenseType.OWNED,
            rights_owner="Tester",
            order_index=1,
        )
    )
    db.flush()

    db.commit()
    from test_vision_status_api import seed_reconciled_analysis_for_project_images

    seed_reconciled_analysis_for_project_images(project.id)
    db.expire_all()
    draft = pl.generate_draft(db, project.id, seed=42, actor_id=user.id)
    assert draft["script_version"] == 1
    assert draft["segments"] == 0
    script = pl.latest_script_row(db, project.id)
    assert script is not None
    pl.approve_script(
        db,
        script.id,
        user.id,
        editorial_review_confirmed=True,
    )
    segments = pl.generate_voiceover(db, project.id, actor_id=user.id)
    assert segments
    scenes = pl.build_timeline(db, project.id, actor_id=user.id)
    assert scenes

    job = pl.enqueue_render(db, project.id, "final", user.id, encoder="cpu")
    assert job.encoder_requested == "cpu"

    # A retry must reproduce the original choice rather than resetting to auto.
    job.status = "failed"
    db.flush()
    retried = pl.retry_render(db, job.id, user.id)
    assert retried.encoder_requested == "cpu"


def test_enqueue_rejects_an_unknown_encoder(db):
    """Caught at enqueue time, not discovered after a surprise CPU render."""
    from app.models import Project, User, Workspace
    from app.security import hash_password
    from app.services import pipeline as pl

    user = User(email="enc2@example.com", password_hash=hash_password("testpass1234"))
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="WS")
    db.add(workspace)
    db.flush()
    project = Project(workspace_id=workspace.id, title="E", manhwa_title="M", chapter="1")
    db.add(project)
    db.flush()

    with pytest.raises(pl.PipelineError, match="unknown encoder"):
        pl.enqueue_render(db, project.id, "final", user.id, encoder="nvnec")
