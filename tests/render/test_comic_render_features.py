from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services import render


def test_adaptive_karaoke_avoids_yellow_on_yellow_frame(tmp_path: Path) -> None:
    frame = tmp_path / "yellow.jpg"
    Image.new("RGB", (1080, 1920), (250, 225, 30)).save(frame, "JPEG", quality=95)
    result = render._adaptive_karaoke_color_for_frame(frame)
    assert result["version"] == render.ADAPTIVE_KARAOKE_CONTRAST_VERSION
    assert result["name"] != "yellow"
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


def test_adaptive_color_span_is_scene_locked() -> None:
    spans = [
        {"start_time": 0.0, "end_time": 2.0, "ass_color": "4646FF"},
        {"start_time": 2.0, "end_time": 4.0, "ass_color": "FFFF28"},
    ]
    assert render._karaoke_color_for_time(spans, 1.0) == "4646FF"
    assert render._karaoke_color_for_time(spans, 3.0) == "FFFF28"
    assert render._karaoke_color_for_time(spans, 8.0) == "00FFFF"
