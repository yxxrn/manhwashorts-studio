from __future__ import annotations

from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.config import settings
from app.services import thumbnail


def _script() -> SimpleNamespace:
    return SimpleNamespace(
        sections=[
            {"section": "hook", "text": "Energy erupts from a forbidden sword."},
            {"section": "conflict", "text": "No one expects what happens next."},
            {"section": "twist", "text": "A mysterious figure changes the entire fight."},
        ],
        hook_options=["Energy erupts from a forbidden sword."],
        selected_hook=0,
    )


def _scene(asset_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        asset_id=asset_id,
        panel_bounds_json={},
        rejected_candidates=[],
        focus_x=0.5,
        focus_y=0.28,
        section="hook",
        alignment_score=0.9,
        source_family="fixture-family",
        roi_label="face_action",
    )


def _source_image(path) -> None:
    image = Image.new("RGB", (900, 1600), (226, 232, 242))
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 80, 830, 1520), fill=(72, 92, 132))
    draw.ellipse((210, 180, 690, 720), fill=(224, 178, 152), outline=(18, 18, 24), width=12)
    draw.ellipse((315, 350, 365, 405), fill=(18, 18, 24))
    draw.ellipse((535, 350, 585, 405), fill=(18, 18, 24))
    draw.line((450, 690, 770, 1280), fill=(230, 230, 238), width=34)
    draw.line((450, 690, 770, 1280), fill=(30, 30, 38), width=12)
    image.save(path, "PNG")


def test_generate_thumbnail_package_is_upload_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "rules")
    source = tmp_path / "source.png"
    _source_image(source)
    video = tmp_path / "final.mp4"
    video.write_bytes(b"stable-final-fixture")
    scene = _scene("asset-1")

    manifest = thumbnail.generate_thumbnail_package(
        video_path=video,
        output_dir=tmp_path,
        script=_script(),
        scenes=[scene],
        resolve_asset_path=lambda asset_id: source if asset_id == "asset-1" else None,
        force=True,
    )

    assert manifest["qc_pass"] is True
    assert manifest["headline"] == "WHAT DID THAT SWORD JUST AWAKEN?!"
    assert 1 <= len(manifest["variants"]) <= 3
    assert (tmp_path / "thumbnail.jpg").is_file()
    assert (tmp_path / "thumbnail_clean.jpg").is_file()
    with Image.open(tmp_path / "thumbnail.jpg") as rendered:
        assert rendered.size == thumbnail.TARGET_SIZE
    assert all(row["qc"]["qc_pass"] for row in manifest["variants"])
    assert all(row["qc"]["background_style"] == "outline_only" for row in manifest["variants"])
    assert len(manifest["headline"].split()) <= thumbnail.MAX_HEADLINE_WORDS

    monkeypatch.setattr(
        thumbnail,
        "generate_headlines",
        lambda _script: (_ for _ in ()).throw(AssertionError("warm thumbnail reuse called headline provider")),
    )
    reused = thumbnail.generate_thumbnail_package(
        video_path=video,
        output_dir=tmp_path,
        script=_script(),
        scenes=[scene],
        resolve_asset_path=lambda _asset_id: source,
    )
    assert reused["video_checksum"] == manifest["video_checksum"]
    assert reused["headline"] == manifest["headline"]



def test_headlines_reject_unsupported_gender_pronouns(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "rules")
    script = SimpleNamespace(
        sections=[
            {"section": "hook", "text": "Kim Suho awakens a dangerous power as he faces the demon king."},
            {"section": "conflict", "text": "He sacrifices his strength to keep a promise."},
        ],
        hook_options=[],
        selected_hook=0,
    )
    headlines, _sections, language = thumbnail.generate_headlines(script)
    assert language == "en"
    assert headlines
    assert all("SHE" not in row.text.split() for row in headlines)
    assert any("POWER" in row.text for row in headlines)

def test_text_placement_avoids_face_region():
    frame = Image.new("RGB", thumbnail.TARGET_SIZE, (120, 120, 120))
    placement, _score, overlap = thumbnail._safe_text_placement(
        frame,
        ((0.15, 0.03, 0.85, 0.42),),
        0.22,
    )
    assert placement == "bottom"
    assert overlap == 0.0


def test_generate_thumbnail_requires_clean_visual_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "rules")
    video = tmp_path / "final.mp4"
    video.write_bytes(b"fixture")
    try:
        thumbnail.generate_thumbnail_package(
            video_path=video,
            output_dir=tmp_path,
            script=_script(),
            scenes=[_scene("missing")],
            resolve_asset_path=lambda _asset_id: None,
            force=True,
        )
    except thumbnail.ThumbnailError as exc:
        assert "no_visual_candidate" in str(exc)
    else:
        raise AssertionError("missing source panel did not block thumbnail generation")


def test_thumbnail_history_rejects_exact_prior_headline(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "output_dir", tmp_path / "output")
    monkeypatch.setattr(settings, "llm_provider", "rules")
    prior = "WHAT POWER JUST AWAKENED?!"
    thumbnail._record_headline_history(prior, story_hash="a" * 64, video_checksum="b" * 64)
    history = thumbnail._load_headline_history()
    headlines, _sections, _language = thumbnail.generate_headlines(_script(), history)
    assert history == [prior]
    assert headlines
    assert all(row.text != prior for row in headlines)
    assert all(thumbnail._headline_is_novel(row.text, history) for row in headlines)


def test_thumbnail_placement_pool_includes_middle_when_safe():
    frame = Image.new("RGB", thumbnail.TARGET_SIZE, (100, 110, 120))
    placements = thumbnail._safe_text_placements(frame, (), 0.12)
    names = {row[0] for row in placements}
    assert names == {"top", "middle", "bottom"}


def test_thumbnail_accent_color_is_from_closed_palette():
    frame = Image.new("RGB", thumbnail.TARGET_SIZE, (235, 235, 235))
    name, rgba = thumbnail._accent_color(frame, "middle")
    assert name in {"yellow", "red", "blue", "green"}
    assert rgba == thumbnail._THUMBNAIL_ACCENT_COLORS[name]
