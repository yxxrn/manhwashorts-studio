from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

from app.services import render, thumbnail


def test_adaptive_karaoke_locks_one_non_white_color_for_entire_video(tmp_path: Path) -> None:
    yellow = tmp_path / "yellow.jpg"
    dark = tmp_path / "dark.jpg"
    Image.new("RGB", (1080, 1920), (250, 225, 30)).save(yellow, "JPEG", quality=95)
    Image.new("RGB", (1080, 1920), (35, 42, 55)).save(dark, "JPEG", quality=95)
    result = render._adaptive_karaoke_color_for_video([yellow, dark])
    assert result["version"] == render.ADAPTIVE_KARAOKE_CONTRAST_VERSION
    assert result["mode"] == "per_video_locked"
    assert result["name"] in {"yellow", "cyan", "red"}
    assert result["name"] != "white"
    assert result["sample_count"] == 2
    assert len(str(result["ass_color"])) == 6


def test_comic_cleanup_changes_only_render_derivative(tmp_path: Path) -> None:
    original = tmp_path / "original.jpg"
    derivative = tmp_path / "derivative.jpg"
    image = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=34)
    draw.text((70, 100), "DOCTOR DOOM", fill="black", font=font)
    draw.text((70, 160), "SCIENCE AND MAGIC", fill="black", font=font)
    image.save(original, "JPEG", quality=95)
    derivative.write_bytes(original.read_bytes())
    original_hash = render.sha256_file(original)
    result = render._comic_text_cleanup_frame(derivative)
    assert result["version"] == render.COMIC_TEXT_CLEANUP_VERSION
    assert result["applied"] is True
    assert render.sha256_file(original) == original_hash
    assert render.sha256_file(derivative) != original_hash


def test_adaptive_color_span_is_video_locked() -> None:
    spans = [{"start_time": 0.0, "end_time": 50.0, "ass_color": "FFFF28"}]
    assert render._karaoke_color_for_time(spans, 1.0) == "FFFF28"
    assert render._karaoke_color_for_time(spans, 35.0) == "FFFF28"
    assert render._karaoke_color_for_time(spans, 50.5) == "00FFFF"


def test_comic_topic_thumbnail_headline_is_bound_not_random() -> None:
    script = SimpleNamespace(
        editorial_metadata={
            "render_features": {
                "topic_title": "Why Doctor Doom Is So Dangerous: Science + Sorcery",
                "thumbnail_headline": "WHY DR. DOOM IS SO DANGEROUS",
                "thumbnail_panel_ids": ["doom-panel"],
                "thumbnail_accent_words": ["DOOM", "DANGEROUS"],
                "thumbnail_palette": "comic_yellow_red_v1",
                "thumbnail_text_cleanup": True,
            }
        },
        sections=[{"section": "hook", "text": "Doctor Doom combines science and sorcery."}],
        hook_options=[],
        selected_hook=0,
    )
    rows, _sections, language = thumbnail.generate_headlines(script, ["OLD RANDOM HEADLINE"])
    assert language == "en"
    assert [row.text for row in rows] == ["WHY DR. DOOM IS SO DANGEROUS"]
    assert rows[0].style == "topic_bound_v1"
    config = thumbnail._topic_thumbnail_config(script)
    assert config["panel_ids"] == ("doom-panel",)
    assert config["comic_palette"] is True
    assert config["text_cleanup"] is True
