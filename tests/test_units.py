"""Unit tests for the pure logic: security, ingest, policy, script, timeline.

These cover the bugs found during development, so a regression fails loudly:
scrypt maxmem, cue overlap, gap absorption, and verbatim copying.
"""

from __future__ import annotations

import pytest

# --- security --------------------------------------------------------------


def test_password_hash_roundtrip():
    from app.security import hash_password, verify_password

    encoded = hash_password("correct horse battery")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery", encoded)
    assert not verify_password("wrong password", encoded)


def test_password_rejects_malformed_hash():
    """A corrupt hash must return False, never raise."""
    from app.security import verify_password

    for bad in ("", "garbage", "scrypt$notanumber$8$1$aa$bb", "md5$x$y"):
        assert verify_password("anything", bad) is False


def test_password_empty_rejected():
    from app.security import hash_password

    with pytest.raises(ValueError):
        hash_password("")


def test_credential_encryption_roundtrip(app_settings):
    from app.security import decrypt_json, encrypt_json

    payload = {"refresh_token": "secret-value", "scopes": ["a", "b"]}
    blob = encrypt_json(payload)
    assert "secret-value" not in blob  # must not be stored in the clear
    assert decrypt_json(blob) == payload


def test_decrypt_rejects_tampered_blob(app_settings):
    from app.security import decrypt_json, encrypt_json

    blob = encrypt_json({"a": 1})
    with pytest.raises(ValueError):
        decrypt_json(blob[:-4] + "AAAA")


def test_redact_hides_secret():
    from app.security import redact

    assert redact("ghp_supersecrettoken") == "...oken"
    assert redact(None) == "<unset>"


# --- storage ---------------------------------------------------------------


def test_storage_rejects_path_traversal(app_settings):
    from app.services import storage

    with pytest.raises(storage.StorageError):
        storage.path_for("../../etc/passwd")


def test_storage_is_content_addressed(app_settings):
    from app.services import storage

    a = storage.put_bytes("t", "x.txt", b"same content")
    b = storage.put_bytes("t", "y.txt", b"same content")
    assert a.checksum == b.checksum
    assert storage.read_bytes(a.storage_key) == b"same content"


# --- ingest ----------------------------------------------------------------


def test_ingest_text_requires_minimum_length(app_settings):
    from app.services import ingest

    with pytest.raises(ingest.IngestError):
        ingest.ingest_text("p1", "too short")


def test_ingest_rejects_fake_image(app_settings):
    """A renamed non-image must be refused, not trusted by extension."""
    from app.services import ingest

    with pytest.raises(ingest.IngestError, match="not a valid image"):
        ingest.ingest_image("p1", "evil.png", b"#!/bin/sh\necho pwned\n" * 20)


def test_ingest_accepts_real_image(app_settings, panel_bytes):
    from app.services import ingest

    asset = ingest.ingest_image("p1", "panel.jpg", panel_bytes)
    assert asset.width == 900 and asset.height == 1200
    assert asset.mime_type == "image/jpeg"


def test_ingest_rejects_unsupported_type(app_settings):
    from app.services import ingest

    with pytest.raises(ingest.IngestError, match="unsupported"):
        ingest.ingest_upload("p1", "movie.mkv", "video/x-matroska", b"\x00" * 500)


def test_rights_declaration_needs_owner_and_licence():
    from app.constants import LicenseType, RightsStatus
    from app.services.ingest import RightsDeclaration

    # Ticking the box alone is not enough.
    assert RightsDeclaration(declared=True).status == RightsStatus.UNDECLARED
    assert (
        RightsDeclaration(declared=True, rights_owner="Me", license_type=LicenseType.OWNED).status
        == RightsStatus.DECLARED
    )
    assert (
        RightsDeclaration(declared=False, rights_owner="Me", license_type=LicenseType.OWNED).status
        == RightsStatus.UNDECLARED
    )


# --- policy ----------------------------------------------------------------


def test_similarity_detects_verbatim_copy():
    from app.services.policy import similarity_ratio

    source = "Rian menemukan papan bercahaya di ruang paling bawah dungeon itu."
    assert similarity_ratio(source, source) == pytest.approx(1.0)
    assert similarity_ratio("Seorang pemburu lemah mendapat kekuatan rahasia.", source) < 0.1


def test_transformative_gate_blocks_copy_and_warns_on_paraphrase():
    from app.constants import CheckSeverity
    from app.services.policy import check_transformative

    source = (
        "Rian adalah pemburu peringkat E. Dia menemukan papan bercahaya di dasar "
        "dungeon. Papan itu memaksa dia berlatih setiap hari tanpa henti."
    )
    blocking = check_transformative(source, source)
    assert blocking and blocking[0].severity == CheckSeverity.ERROR
    assert blocking[0].blocking

    rewritten = "Pemburu lemah ini dapat sistem misterius yang memaksanya jadi kuat."
    assert check_transformative(rewritten, source) == []


def test_banned_words_block():
    from app.services.policy import check_banned_words

    findings = check_banned_words("Ini mengandung katakasar sekali", ["katakasar"])
    assert findings and findings[0].blocking


def test_public_publish_requires_config(app_settings, monkeypatch):
    from app.services import policy

    monkeypatch.setattr(app_settings, "allow_public_publish", False)
    assert policy.check_public_publish("public")[0].blocking
    assert policy.check_public_publish("private") == []


# --- script ---------------------------------------------------------------


def test_summarise_clause_shortens_and_rewords():
    from app.services.script import summarise_clause

    source = (
        "Bab ini dibuka dengan Rian, pemburu peringkat E yang namanya jarang "
        "disebut siapa pun di asosiasi."
    )
    out = summarise_clause(source)
    assert out
    assert len(out.split()) < len(source.split())
    assert not out.lower().startswith("bab ini dibuka")


def test_generated_script_is_not_verbatim(recap_text):
    """The generator must summarise, or the transformative gate will block it."""
    from app.services.analysis import RulesAnalyzer
    from app.services.policy import check_transformative, similarity_ratio
    from app.services.script import RulesScriptGenerator

    analysis = RulesAnalyzer().analyze([(0, recap_text)])
    draft = RulesScriptGenerator().generate(
        analysis, target_seconds=60, manhwa_title="Test", chapter="1", seed=42
    )
    ratio = similarity_ratio(draft.plain_text, recap_text)
    assert ratio < 0.5, f"narration is {ratio:.0%} verbatim"
    assert not any(f.blocking for f in check_transformative(draft.plain_text, recap_text))


def test_locked_sections_survive_regeneration(recap_text):
    from app.services.analysis import RulesAnalyzer
    from app.services.script import RulesScriptGenerator, Section

    analysis = RulesAnalyzer().analyze([(0, recap_text)])
    generator = RulesScriptGenerator()
    locked = {"hook": Section(section="hook", text="Hook buatan saya.", locked=True)}
    draft = generator.generate(analysis, target_seconds=60, locked=locked, seed=7)
    hook = next(s for s in draft.sections if s.section == "hook")
    assert hook.text == "Hook buatan saya."
    assert hook.locked is True


def test_script_generation_is_deterministic_with_seed(recap_text):
    from app.services.analysis import RulesAnalyzer
    from app.services.script import RulesScriptGenerator

    analysis = RulesAnalyzer().analyze([(0, recap_text)])
    generator = RulesScriptGenerator()
    a = generator.generate(analysis, target_seconds=60, seed=99)
    b = generator.generate(analysis, target_seconds=60, seed=99)
    assert a.plain_text == b.plain_text
    assert a.hook_options == b.hook_options


def test_minimal_spoiler_removes_reveal():
    from app.constants import SpoilerLevel
    from app.services.script import _strip_spoiler

    reveal = "Ternyata dia adalah raja iblis."
    out = _strip_spoiler(reveal, SpoilerLevel.MINIMAL)
    assert "raja iblis" not in out.lower()
    assert _strip_spoiler(reveal, SpoilerLevel.FULL) == reveal


def test_all_five_sections_present(recap_text):
    from app.constants import ScriptSection
    from app.services.analysis import RulesAnalyzer
    from app.services.script import RulesScriptGenerator

    analysis = RulesAnalyzer().analyze([(0, recap_text)])
    draft = RulesScriptGenerator().generate(analysis, target_seconds=60, seed=1)
    assert [s.section for s in draft.sections] == [s.value for s in ScriptSection]
    assert all(s.text.strip() for s in draft.sections)


# --- analysis -------------------------------------------------------------


def test_analysis_repairs_hard_wrapped_text():
    """Soft line wraps must not split sentences into fragments."""
    from app.services.analysis import RulesAnalyzer

    wrapped = (
        "Rian masuk ke dungeon yang gelap dan menemukan sebuah papan\n"
        "bercahaya di ruang bawah. Papan itu memaksa dia berlatih.\n"
    )
    events = RulesAnalyzer().analyze([(0, wrapped)]).events
    assert any("papan bercahaya di ruang bawah" in e.text for e in events)


def test_analysis_extracts_characters_and_beats(recap_text):
    from app.services.analysis import RulesAnalyzer

    result = RulesAnalyzer().analyze([(0, recap_text)])
    assert any(c.name == "Rian" for c in result.characters)
    assert result.twist
    assert any(e.kind == "conflict" for e in result.events)
    # Every event traces back to a source index.
    assert all(e.source_index == 0 for e in result.events)


def test_analysis_flags_missing_material():
    from app.services.analysis import RulesAnalyzer

    result = RulesAnalyzer().analyze([])
    assert result.low_confidence_notes


# --- timeline -------------------------------------------------------------


def _spans(gap: float = 0.18):
    from app.services.timeline import lay_out_audio
    from app.services.tts import estimate_word_timings

    segments = []
    for section, text, duration in [
        ("hook", "Semua orang mengira dia akan gagal total.", 3.9),
        ("setup", "Dia pemburu peringkat rendah yang diremehkan.", 4.2),
        ("conflict", "Sistem memaksa latihan brutal setiap hari tanpa henti.", 9.6),
        ("twist", "Kegagalan justru menaikkan batas kekuatannya.", 6.2),
        ("cta", "Komentar di bawah.", 3.3),
    ]:
        segments.append((section, text, duration, estimate_word_timings(text, duration)))
    return lay_out_audio(segments, gap=gap)


def test_word_timings_are_monotonic():
    from app.services.tts import estimate_word_timings

    timings = estimate_word_timings("Satu dua tiga empat lima enam.", 4.0)
    assert timings[0]["start"] == 0.0
    assert timings[-1]["end"] == pytest.approx(4.0)
    for a, b in zip(timings, timings[1:], strict=False):
        assert a["end"] <= b["start"] + 1e-6


def test_scenes_cover_audio_including_gaps():
    """Regression: gaps between beats must be absorbed or audio gets clipped."""
    from app.services.timeline import plan_scenes

    spans = _spans()
    scenes = plan_scenes(spans, ["a", "b", "c", "d", "e", "f"])
    assert scenes[-1].end_time == pytest.approx(spans[-1].end_time, abs=0.01)
    for a, b in zip(scenes, scenes[1:], strict=False):
        assert b.start_time == pytest.approx(a.end_time, abs=0.01), "timeline has a hole"


def test_cues_never_overlap_or_go_backwards():
    """Regression: clip-relative timings once made every cue restart at zero."""
    from app.services.timeline import build_cues, validate_cues

    cues = build_cues(_spans())
    assert cues
    for cue in cues:
        assert cue.end_time > cue.start_time, "cue has non-positive duration"
    for a, b in zip(cues, cues[1:], strict=False):
        assert b.start_time >= a.end_time - 0.01
    assert not [w for w in validate_cues(cues, 28, 2) if w["severity"] == "error"]


def test_cues_respect_line_limit():
    from app.services.timeline import build_cues, wrap_caption

    for cue in build_cues(_spans()):
        assert len(wrap_caption(cue.text, 28)) <= 2, cue.text


def test_srt_format_is_valid():
    from app.services.timeline import build_cues, to_srt

    srt = to_srt(build_cues(_spans()))
    assert srt.startswith("1\n")
    assert " --> " in srt
    # Timestamps use comma for milliseconds, per the SRT spec.
    assert "," in srt.split("\n")[1]


def test_redistribute_rescales_to_target():
    from app.services.timeline import plan_scenes, redistribute

    scenes = plan_scenes(_spans(), ["a", "b"])
    redistribute(scenes, 30.0)
    assert scenes[0].start_time == 0.0
    assert scenes[-1].end_time == pytest.approx(30.0, abs=0.01)


# --- render helpers -------------------------------------------------------


def test_crop_to_vertical_produces_exact_ratio(tmp_path, panel_bytes):
    from PIL import Image

    from app.services.render import crop_to_vertical

    src = tmp_path / "in.jpg"
    src.write_bytes(panel_bytes)
    out = crop_to_vertical(src, tmp_path / "out.jpg", 1080, 1920, 0.5, 0.4)
    with Image.open(out) as img:
        width, height = img.size
    assert width / height == pytest.approx(1080 / 1920, abs=0.001)


def test_crop_clamps_extreme_focal_point(tmp_path, panel_bytes):
    """A focal point at the very edge must still yield a full-size crop."""
    from PIL import Image

    from app.services.render import crop_to_vertical

    src = tmp_path / "in.jpg"
    src.write_bytes(panel_bytes)
    out = crop_to_vertical(src, tmp_path / "edge.jpg", 1080, 1920, 0.0, 1.0)
    with Image.open(out) as img:
        assert img.size[0] / img.size[1] == pytest.approx(1080 / 1920, abs=0.001)


def test_join_scene_clips_preserves_duration_with_editorial_fade(tmp_path):
    """A panel dissolve must not shorten the audio-locked visual timeline."""
    from PIL import Image

    from app.services import encoders
    from app.services.render import (
        SceneInput,
        join_scene_clips,
        probe,
        render_scene_clip,
    )

    encoder = encoders.select("cpu")
    clips = []
    scenes = []
    for index, colour in enumerate(("red", "blue")):
        image = tmp_path / f"panel{index}.jpg"
        clip = tmp_path / f"clip{index}.mp4"
        Image.new("RGB", (800, 1200), colour).save(image)
        scene = SceneInput(
            image, index * 2.0, (index + 1) * 2.0,
            camera_curve="slow_push_in", transition="none" if index == 0 else "fade",
        )
        render_scene_clip(scene, image, clip, 360, 640, 30, encoder=encoder)
        clips.append(clip)
        scenes.append(scene)

    output = tmp_path / "joined.mp4"
    join_scene_clips(clips, scenes, output, 30, encoder)
    assert probe(output)["duration"] == pytest.approx(4.0, abs=0.05)


def test_editorial_fade_contains_real_intermediate_frames(tmp_path):
    """A fade boundary must mix outgoing and incoming panels, not flash black."""
    import subprocess

    from PIL import Image, ImageStat

    from app.services import encoders
    from app.services.render import SceneInput, join_scene_clips, render_scene_clip

    encoder = encoders.select("cpu")
    clips = []
    scenes = []
    for index, colour in enumerate(("red", "blue")):
        image = tmp_path / f"fade_panel{index}.jpg"
        clip = tmp_path / f"fade_clip{index}.mp4"
        Image.new("RGB", (800, 1200), colour).save(image)
        scene = SceneInput(
            image, index * 2.0, (index + 1) * 2.0,
            camera_curve="static", transition="none" if index == 0 else "fade",
        )
        render_scene_clip(scene, image, clip, 360, 640, 30, encoder=encoder)
        clips.append(clip)
        scenes.append(scene)

    output = tmp_path / "fade_joined.mp4"
    join_scene_clips(clips, scenes, output, 30, encoder)
    frame = tmp_path / "fade_frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "1.94",
         "-i", str(output), "-frames:v", "1", str(frame)],
        check=True,
    )
    mean = ImageStat.Stat(Image.open(frame).convert("RGB")).mean
    assert 10 < mean[0] < 245
    assert 10 < mean[2] < 245


def test_ass_subtitle_stays_in_safe_area():
    from app.services.render import build_ass
    from app.services.timeline import CueSpec

    cues = [CueSpec(0, "Baris subtitle pertama", 0.0, 2.0)]
    ass = build_ass(cues, 1080, 1920)
    assert "[Events]" in ass and "Dialogue:" in ass
    # MarginV must keep text clear of the YouTube UI overlay.
    style_line = next(line for line in ass.splitlines() if line.startswith("Style: Caption"))
    margin_v = int(style_line.split(",")[-2])
    assert margin_v >= int(1920 * 0.2)


def test_ass_escapes_braces():
    """Braces are ASS override blocks; user text must not be able to inject them."""
    from app.services.render import build_ass
    from app.services.timeline import CueSpec

    ass = build_ass([CueSpec(0, "teks {\\an8} aneh", 0.0, 1.0)], 1080, 1920)
    assert "{\\an8}" not in ass


def test_ass_uses_karaoke_word_highlight():
    from app.services.render import build_ass
    from app.services.timeline import CueSpec

    ass = build_ass([CueSpec(0, "This is a test", 0.0, 2.0)], 1080, 1920, "Anton")
    assert "Style: Caption,Anton" in ass
    assert ass.count("Dialogue:") == 4
    assert "\\c&H0000FFFF&" in ass
    assert "THIS" in ass and "TEST" in ass


def test_environment_check_returns_list():
    from app.services.render import check_environment

    assert isinstance(check_environment(), list)


# --- quality --------------------------------------------------------------


def test_duration_over_limit_is_blocking(app_settings):
    from app.services.quality import check_duration

    results = check_duration(75.0, 60.0)
    assert any(r.blocking and r.code == "duration.too_long" for r in results)


def test_duration_within_limit_passes(app_settings):
    from app.services.quality import check_duration

    assert all(r.passed for r in check_duration(58.0, 60.0))


def test_youtube_metadata_within_limits():
    from app.services.youtube import build_metadata

    meta = build_metadata("Proyek", "Judul Manhwa Panjang", "12", "Narasi contoh.", "Kreator")
    assert len(meta["title"]) <= 100
    assert len(meta["description"]) <= 5000
    assert len(meta["tags"]) <= 15
    # Rights notice belongs in every description.
    assert "hak" in meta["description"].lower()
