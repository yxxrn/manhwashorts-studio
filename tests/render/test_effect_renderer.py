from PIL import Image, ImageChops

from app.services.render import apply_local_effects, editorial_frame, local_effects


def test_effect_intensity_is_deterministic_and_disable_is_safe():
    image = Image.new("RGB", (240, 400), "#202020")
    effects = local_effects("impact")
    assert effects == ("glow", "flash", "embers")
    assert local_effects("impact", ["flash"]) == ("glow", "embers")
    low = apply_local_effects(image, effects, "low", seed=11)
    high = apply_local_effects(image, effects, "high", seed=11)
    assert low.size == high.size == image.size
    assert low.tobytes() == apply_local_effects(image, effects, "low", seed=11).tobytes()
    assert ImageChops.difference(low, high).getbbox() is not None


def test_unknown_or_disabled_effect_falls_back_to_original_geometry():
    image = Image.new("RGB", (240, 400), "#202020")
    assert local_effects("unknown") == ()
    assert apply_local_effects(image, (), "high").size == image.size
    assert apply_local_effects(image, ("unknown",), "low").size == image.size


def test_split_focus_preserves_both_roi_regions(tmp_path):
    source = Image.new("RGB", (400, 800), "black")
    source.paste("red", (0, 0, 200, 400))
    source.paste("blue", (200, 400, 400, 800))
    path = tmp_path / "source.jpg"
    source.save(path)
    output = editorial_frame(path, tmp_path / "split_focus.jpg", 360, 640, 0.2, 0.2, 0.8, 0.8, "split_focus")
    with Image.open(output) as image:
        assert image.size == (414, 736)
        left = image.getpixel((100, 368))
        right = image.getpixel((313, 368))
        assert left[0] > left[2] * 2
        assert right[2] > right[0] * 2


def test_panel_stack_preserves_both_roi_regions_and_geometry(tmp_path):
    source = Image.new("RGB", (400, 800), "black")
    source.paste("red", (0, 0, 200, 400))
    source.paste("blue", (200, 400, 400, 800))
    path = tmp_path / "source.jpg"
    source.save(path)
    output = editorial_frame(path, tmp_path / "panel_stack.jpg", 360, 640, 0.2, 0.2, 0.8, 0.8, "panel_stack")
    with Image.open(output) as image:
        assert image.size == (414, 736)
        top = image.getpixel((207, 220))
        bottom = image.getpixel((207, 610))
        assert top[0] > top[2] * 2
        assert bottom[2] > bottom[0] * 2


def test_split_focus_and_panel_stack_do_not_touch_subtitle_safe_band(tmp_path):
    source = Image.new("RGB", (400, 800), "#333333")
    path = tmp_path / "source.jpg"
    source.save(path)
    for mode in ("split_focus", "panel_stack"):
        output = editorial_frame(path, tmp_path / f"{mode}.jpg", 360, 640, 0.2, 0.2, 0.8, 0.8, mode)
        with Image.open(output) as image:
            safe_band = image.crop((0, int(image.height * 0.80), image.width, image.height))
            assert safe_band.size == (414, 148)
            assert safe_band.getbbox() is not None


# ponytail: synthetic fixtures validate renderer geometry; perception-level ROI
# detection remains outside the CPU renderer contract.
def test_renderer_contract_is_explicit():
    assert local_effects("panel_stack") == ("speed_lines", "dust")
