from __future__ import annotations

import subprocess

from PIL import Image, ImageStat

from app.config import settings
from app.services import render


def test_watermark_ass_is_lower_center_and_translucent(tmp_path):
    base = render.build_ass([], 320, 568, settings.subtitle_font_name)
    ass = render._append_watermark_ass(base, "@rurushortss", 320, 568, 0.1)
    assert "Style: Watermark" in ass
    assert "&H8FFFFFFF" in ass
    assert "\\an2\\pos(160,506)" in ass
    assert "@rurushortss" in ass

    ass_path = tmp_path / "watermark.ass"
    ass_path.write_text(ass, encoding="utf-8")
    output = tmp_path / "watermark.png"
    vf = f"subtitles='{render._escape_filter_path(ass_path)}'"
    result = subprocess.run([settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=#404040:s=320x568:d=0.1", "-vf", vf, "-frames:v", "1", str(output)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    with Image.open(output) as image:
        gray = image.convert("L")
        top = ImageStat.Stat(gray.crop((0, 0, 320, 440))).extrema[0]
        lower = ImageStat.Stat(gray.crop((40, 480, 280, 550))).extrema[0]
    assert max(lower) > max(top)


def test_watermark_ass_rejects_empty_text():
    base = render.build_ass([], 1080, 1920, settings.subtitle_font_name)
    try:
        render._append_watermark_ass(base, "   ", 1080, 1920, 1.0)
    except render.RenderError as exc:
        assert exc.code == "watermark_text_missing"
    else:
        raise AssertionError("empty enabled watermark was accepted")


def test_watermark_manifest_matches_visible_contract():
    manifest = render._watermark_manifest(" @rurushortss ", 1080, 1920, enabled=True)
    assert manifest == {
        "contract_version": "render-watermark-v1",
        "enabled": True,
        "text": "@rurushortss",
        "placement": "lower_center",
        "anchor": [0.5, 0.89],
        "font_size_px": 46,
        "text_opacity": 0.439,
    }
    assert render._watermark_manifest("ignored", 1080, 1920, enabled=False)["text"] == ""
