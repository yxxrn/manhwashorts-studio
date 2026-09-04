from __future__ import annotations

import subprocess

from PIL import Image, ImageStat

from app.config import settings
from app.services import render


def test_watermark_ass_is_lower_center_and_translucent(tmp_path):
    base = render.build_ass([], 320, 568, settings.subtitle_font_name)
    ass = render._append_watermark_ass(base, "@Rurushortss", 320, 568, 0.1)
    assert "Style: Watermark" in ass
    assert "Style: Watermark,Caacupe One" in ass
    assert render.WATERMARK_FONT_FILE.is_file()
    assert ",-1,0,0,0,100,100" in ass
    assert "&H8FFFFFFF" in ass
    assert "\\an2\\pos(160,506)" in ass
    assert "@Rurushortss" in ass

    ass_path = tmp_path / "watermark.ass"
    ass_path.write_text(ass, encoding="utf-8")
    output = tmp_path / "watermark.png"
    vf = (
        f"subtitles='{render._escape_filter_path(ass_path)}':"
        f"fontsdir='{render._escape_filter_path(render.WATERMARK_FONT_FILE.parent)}'"
    )
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
    manifest = render._watermark_manifest(" @Rurushortss ", 1080, 1920, enabled=True)
    assert manifest == {
        "contract_version": "render-watermark-v3",
        "font_name": "Caacupe One",
        "font_file": "CaacupeOne-Regular.ttf",
        "synthetic_bold": True,
        "font_sha256": "2f95e76b7df7f29c722c9bafb248cffd3970d92a19dd6b3f545e6934b64998cd",
        "enabled": True,
        "text": "@Rurushortss",
        "placement": "lower_center",
        "anchor": [0.5, 0.89],
        "font_size_px": 46,
        "text_opacity": 0.439,
    }
    assert render._watermark_manifest("ignored", 1080, 1920, enabled=False)["text"] == ""


def test_watermark_sidecar_is_refreshed_and_removed_when_disabled(tmp_path):
    output = tmp_path / "final.mp4"
    output.write_bytes(b"video")
    manifest = render._watermark_manifest("@Rurushortss", 1080, 1920, enabled=True)
    sidecar = render._write_watermark_sidecar(output, manifest, "abc123", preview=False)
    assert sidecar == tmp_path / "watermark.json"
    payload = __import__("json").loads(sidecar.read_text())
    assert payload["contract_version"] == "render-watermark-v3"
    assert payload["text"] == "@Rurushortss"
    assert payload["video_checksum"] == "abc123"
    assert payload["font_name"] == "Caacupe One"
    disabled = render._watermark_manifest("", 1080, 1920, enabled=False)
    assert render._write_watermark_sidecar(output, disabled, "def456", preview=False) is None
    assert not sidecar.exists()
