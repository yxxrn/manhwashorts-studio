"""Strip slicing tests (v1.4.0).

A webtoon page is one long vertical strip. Forcing it into a single 9:16 frame
keeps under a third of the page, so a whole story beat disappears. Measured on a
real 720x4372 page: 70.7% discarded. These tests pin the behaviour that replaced
it, plus the commit-visibility fix that the same real-case run uncovered.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

pytestmark = pytest.mark.usefixtures("app_settings")


def _jpeg(width: int, height: int, *, gutters: list[int] | None = None) -> bytes:
    """A strip with mid-tone artwork and optional near-white gutter bands."""
    img = Image.new("RGB", (width, height), (70, 90, 110))
    draw = ImageDraw.Draw(img)
    # Texture, so rows are not uniformly flat and cannot be mistaken for gutters.
    for y in range(0, height, 40):
        draw.rectangle([0, y, width, y + 18], fill=(120, 60, 80))
    for y in gutters or []:
        draw.rectangle([0, y - 14, width, y + 14], fill=(252, 252, 252))
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


# --- when to slice ---------------------------------------------------------


def test_normal_panel_is_not_sliced():
    """A regular portrait panel must pass through untouched."""
    from app.services import strips

    data = _jpeg(900, 1200)
    pieces = strips.slice_strip(data)
    assert len(pieces) == 1
    assert pieces[0].data == data, "unsliced image should keep its original bytes"


def test_ratio_just_below_threshold_is_left_alone():
    from app.services import strips

    # 1:2.4 sits under the 2.5 cutoff.
    assert len(strips.slice_strip(_jpeg(720, 1728))) == 1


def test_tall_strip_without_verified_gutter_is_not_force_sliced():
    from app.services import strips

    data = _jpeg(720, 4372)
    pieces = strips.slice_strip(data)
    assert len(pieces) == 1
    assert pieces[0].data == data
    assert pieces[0].source_bounds == (0, 0, 720, 4372)


def test_slices_are_contiguous_and_ordered():
    """Gaps would drop artwork; overlaps would repeat it."""
    from app.services import strips

    height = 4372
    pieces = strips.slice_strip(
        _jpeg(720, height, gutters=[height // 3, 2 * height // 3])
    )
    assert len(pieces) > 1
    # strict=False: pieces[1:] is one shorter by construction.
    for earlier, later in zip(pieces, pieces[1:], strict=False):
        assert later.top == earlier.bottom, "slices must tile the strip exactly"
    assert [p.index for p in pieces] == list(range(len(pieces)))


def test_every_slice_is_a_valid_image_above_the_minimum():
    """ingest rejects anything under 200px, so no slice may be smaller."""
    from app.services import strips

    for piece in strips.slice_strip(_jpeg(720, 4372)):
        with Image.open(io.BytesIO(piece.data)) as img:
            assert img.size[0] == 720
            assert img.size[1] >= 200


def test_part_count_is_capped():
    from app.config import get_settings
    from app.services import strips

    cap = get_settings().strip_slice_max_parts
    # 1:40 would otherwise produce dozens of pieces.
    assert len(strips.slice_strip(_jpeg(400, 16000))) <= cap


def test_slicing_can_be_disabled(monkeypatch):
    """The kill switch must actually reach the slicer.

    ``strips`` binds ``settings`` at import time, so the object it reads is the
    module-level instance — patching a fresh ``get_settings()`` result would
    leave the slicer untouched and make this test pass for the wrong reason.
    """
    from app.services import strips

    monkeypatch.setattr(strips.settings, "strip_slice_enabled", False)
    data = _jpeg(720, 4372)
    pieces = strips.slice_strip(data)
    assert len(pieces) == 1
    assert pieces[0].data == data


# --- cut quality -----------------------------------------------------------


def test_cuts_prefer_a_gutter():
    """Cutting through art splits faces and speech balloons; gutters are free."""
    from app.services import strips

    # Three even gutters in a strip that wants three pieces.
    height = 4372
    gutters = [height // 3, 2 * height // 3]
    pieces = strips.slice_strip(_jpeg(720, height, gutters=gutters))

    assert len(pieces) >= 2
    assert any(p.snapped_to_gutter for p in pieces), "no cut snapped to a gutter"

    # Each interior cut should land close to the gutter it was offered.
    for piece in pieces[1:]:
        if piece.snapped_to_gutter:
            assert min(abs(piece.top - g) for g in gutters) <= 40


def test_color_agnostic_detector_finds_a_mid_colour_separator():
    """A separator must not depend on white/black brightness extremes."""
    from app.services import strips

    image = Image.new("RGB", (400, 2200), (72, 91, 113))
    draw = ImageDraw.Draw(image)
    for y in range(0, 2200, 36):
        draw.rectangle((0, y, 399, y + 15), fill=(118, 64, 96))
    for top in (710, 1450):
        draw.rectangle((0, top, 399, top + 42), fill=(132, 78, 171))

    candidates = strips.color_agnostic_separator_candidates(image)

    assert candidates
    assert any(abs(candidate.position - top - 21) <= 35 for top in (710, 1450) for candidate in candidates)
    assert all(candidate.confidence >= 0.7 for candidate in candidates)


def test_color_agnostic_detector_does_not_call_a_flat_sky_gutter():
    from app.services import strips

    image = Image.new("RGB", (400, 2200), (132, 178, 219))

    candidates = strips.color_agnostic_separator_candidates(image)

    assert candidates == ()


@pytest.mark.parametrize("band_colour", [(0, 0, 0), (255, 255, 255)])
def test_color_agnostic_detector_keeps_black_and_white_support(band_colour):
    from app.services import strips

    image = Image.new("RGB", (400, 1200), (82, 95, 116))
    draw = ImageDraw.Draw(image)
    for y in range(0, 1200, 36):
        draw.rectangle((0, y, 399, y + 15), fill=(132, 64, 94))
    draw.rectangle((0, 560, 399, 600), fill=band_colour)

    candidates = strips.color_agnostic_separator_candidates(image)

    assert any(abs(candidate.position - 580) <= 25 for candidate in candidates)


def test_color_agnostic_detector_accepts_a_mildly_textured_mid_colour_gutter():
    from app.services import strips

    image = Image.new("RGB", (400, 1200), (82, 95, 116))
    draw = ImageDraw.Draw(image)
    for y in range(0, 1200, 36):
        draw.rectangle((0, y, 399, y + 15), fill=(132, 64, 94))
    for y in range(560, 601):
        for x in range(400):
            delta = (x + y) % 3 - 1
            image.putpixel((x, y), (120 + delta, 136 + delta, 150 + delta))

    candidates = strips.color_agnostic_separator_candidates(image)

    assert any(abs(candidate.position - 580) <= 25 for candidate in candidates)


def test_slicing_keeps_far_more_of_the_page_than_one_crop():
    """The whole point: retention should improve substantially."""
    from app.services import strips

    height = 4372
    report = strips.describe(
        _jpeg(720, height, gutters=[height // 3, 2 * height // 3])
    )
    assert report["slices"] > 1
    assert report["kept_single_crop"] < 35.0
    assert report["kept_sliced"] > 80.0
    assert report["kept_sliced"] > report["kept_single_crop"] * 2


def test_describe_reports_geometry():
    from app.services import strips

    report = strips.describe(_jpeg(720, 3667))
    assert report["width"] == 720
    assert report["height"] == 3667
    assert report["ratio"] == pytest.approx(5.09, abs=0.01)


def test_part_count_maximises_retention():
    """Rounding down is not always right.

    On a 720x3667 page, two pieces means each is taller than a frame and 30% of
    the *story* is cropped away, while three pieces costs only ~5% of the side
    margins. The chooser must compare both instead of assuming.
    """
    from app.services import strips

    frame = 720 / strips.TARGET_RATIO
    for usable in (3667, 3642, 4372, 3309):
        chosen = strips._choose_parts(usable, 720, frame)
        lower = max(1, int(usable // frame))
        best = max(
            (lower, lower + 1),
            key=lambda n: strips._retention(usable, 720, frame, n),
        )
        assert chosen == best, f"{usable}px: chose {chosen}, better was {best}"


# --- ingest integration ----------------------------------------------------


def test_ingest_turns_one_strip_into_several_assets():
    from app.services import ingest

    height = 4372
    assets = ingest.ingest_image_parts(
        "proj-strip",
        "page01.jpg",
        _jpeg(720, height, gutters=[height // 3, 2 * height // 3]),
    )
    assert len(assets) > 1
    # Zero-padded names so lexical order matches reading order.
    assert [a.original_filename for a in assets] == [
        f"page01_p{i:02d}.jpg" for i in range(1, len(assets) + 1)
    ]
    for asset in assets:
        assert asset.width == 720
        assert asset.height >= 200
        assert asset.checksum and asset.storage_key


def test_ingest_keeps_a_normal_panel_as_one_asset():
    from app.services import ingest

    assets = ingest.ingest_image_parts("proj-strip", "panel.jpg", _jpeg(900, 1200))
    assert len(assets) == 1
    assert assets[0].original_filename == "panel.jpg"


def test_sliced_panels_keep_one_persisted_source_family():
    from app.services import ingest

    assets = ingest.ingest_image_parts("proj-strip", "chapter/page01.jpg", _jpeg(720, 4372))
    assert {asset.source_family for asset in assets} == {"chapter/page01"}


def test_double_underscore_input_pages_do_not_share_a_slice_family():
    from app.services import ingest

    families = {
        ingest.derive_source_family(name)
        for name in ("001__001.jpg", "001__002.jpg", "001__003.jpg")
    }

    assert families == {"001__001", "001__002", "001__003"}


def test_source_family_override_is_exposed_by_asset_route(client, declared_rights):
    assert client.post(
        "/api/auth/register",
        json={"email": "family@example.com", "password": "familypass1234"},
    ).status_code == 201
    project = client.post("/api/projects", json={"title": "Family"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/assets/upload",
        files={"files": ("panel.jpg", _jpeg(900, 1200), "image/jpeg")},
        data=declared_rights,
    )
    assert response.status_code == 201, response.text
    asset = response.json()[0]
    updated = client.patch(
        f"/api/projects/{project['id']}/assets/{asset['id']}/family",
        json={"source_family": "manual/page-a"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["source_family"] == "manual/page-a"
    assert updated.json()["source_family_manual"] is True


def test_ingest_parts_still_rejects_a_fake_image():
    """Slicing must never bypass content verification."""
    from app.services import ingest

    with pytest.raises(ingest.IngestError, match="not a valid image"):
        ingest.ingest_image_parts("proj-strip", "evil.jpg", b"#!/bin/sh\necho pwned\n" * 40)


def test_upload_endpoint_persists_one_immutable_source_page(client, declared_rights):
    """New uploads keep the page once; segmentation owns canonical panels."""
    assert client.post(
        "/api/auth/register",
        json={"email": "strip@example.com", "password": "strippass1234"},
    ).status_code == 201

    project = client.post(
        "/api/projects",
        json={"title": "Strip", "manhwa_title": "S", "chapter": "1", "target_duration": 45},
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/assets/upload",
        files=[("files", ("page.jpg", _jpeg(720, 4372), "image/jpeg"))],
        data={k: str(v) for k, v in declared_rights.items()},
    )
    assert response.status_code == 201, response.text
    assets = response.json()
    assert len(assets) == 1
    assert assets[0]["original_filename"] == "page.jpg"
    from app.db import SessionLocal
    from app.models import SourceAsset

    with SessionLocal() as db:
        stored = db.get(SourceAsset, assets[0]["id"])
        assert stored.source_bounds_json == {
            "x": 0,
            "y": 0,
            "width": 720,
            "height": 4372,
        }

    # Source order is dense; canonical PanelRegion order is derived later.
    assert [a["order_index"] for a in assets] == [0]


def test_upload_order_index_stays_dense_across_mixed_files(client, declared_rights):
    """A strip plus a normal panel must not leave gaps in order_index."""
    assert client.post(
        "/api/auth/register",
        json={"email": "strip2@example.com", "password": "strippass1234"},
    ).status_code == 201

    project = client.post(
        "/api/projects",
        json={"title": "Mixed", "manhwa_title": "S", "chapter": "1", "target_duration": 45},
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/assets/upload",
        files=[
            ("files", ("tall.jpg", _jpeg(720, 4372), "image/jpeg")),
            ("files", ("flat.jpg", _jpeg(900, 1200), "image/jpeg")),
        ],
        data={k: str(v) for k, v in declared_rights.items()},
    )
    assert response.status_code == 201, response.text
    assets = response.json()
    assert [a["order_index"] for a in assets] == list(range(len(assets)))
    assert assets[-1]["original_filename"] == "flat.jpg"
    assert assets[0]["original_filename"] == "tall.jpg"


def test_verified_blank_detector_accepts_large_full_width_edge_blank():
    from app.services import strips
    image = Image.new("RGB", (400, 1600), (90, 110, 130))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 399, 299), fill=(252, 252, 252))
    spans = strips.verified_blank_spans(image)
    assert spans
    assert spans[0].run_top == 0
    assert spans[0].run_bottom >= 295
    rows = strips.color_agnostic_row_classifications(image)
    assert all(rows[y][0] == "verified_gutter" for y in range(40, 260))
    assert rows[600][0] == "canonical_panel"


def test_verified_blank_detector_rejects_flat_midtone_art():
    from app.services import strips
    image = Image.new("RGB", (400, 1600), (132, 178, 219))
    assert strips.verified_blank_spans(image) == ()


def test_verified_blank_detector_bridges_low_texture_compression_gap():
    from app.services import strips
    image = Image.new("RGB", (400, 1200), (252, 252, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 500, 399, 539), fill=(249, 249, 249))
    spans = strips.verified_blank_spans(image)
    assert len(spans) == 1
    assert spans[0].run_top == 0
    assert spans[0].run_bottom == 1200


def test_verified_blank_detector_bridges_short_faint_edge_title_band():
    from app.services import strips
    image = Image.new("RGB", (900, 1200), (252, 252, 252))
    draw = ImageDraw.Draw(image)
    # A faint scan-credit/title mark can interrupt an otherwise blank edge.
    # It is not story art and must not become a canonical provider panel.
    draw.rectangle((310, 500, 590, 595), fill=(248, 248, 248))
    draw.line((350, 530, 550, 565), fill=(246, 246, 246), width=3)
    spans = strips.verified_blank_spans(image)
    assert len(spans) == 1
    assert spans[0].run_top == 0
    assert spans[0].run_bottom == 1200


def test_verified_blank_detector_does_not_bridge_textured_story_gap():
    from app.services import strips
    image = Image.new("RGB", (400, 1200), (252, 252, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 500, 399, 559), fill=(252, 252, 252))
    for x in range(20, 380, 20):
        draw.rectangle((x, 500, x + 6, 559), fill=(30, 30, 30))
    spans = strips.verified_blank_spans(image)
    assert len(spans) >= 2
    rows = strips.color_agnostic_row_classifications(image)
    assert any(rows[y][0] == "canonical_panel" for y in range(500, 560))


def test_color_agnostic_rows_bridge_micro_content_island_between_gutters(monkeypatch):
    from app.services import strips

    image = Image.new("RGB", (900, 700), (120, 120, 120))
    candidates = (
        strips.SeparatorCandidate(250, 0.95, 0.95, 200, 300, "left"),
        strips.SeparatorCandidate(354, 0.94, 0.94, 307, 400, "right"),
    )
    monkeypatch.setattr(strips, "color_agnostic_separator_candidates", lambda _image: candidates)

    rows = strips.color_agnostic_row_classifications(image)

    assert all(rows[y][0] == "verified_gutter" for y in range(200, 400))
    assert all("micro_gap_bridge" in rows[y][2] for y in range(300, 307))


def test_color_agnostic_rows_keep_non_micro_story_band_between_gutters(monkeypatch):
    from app.services import strips

    image = Image.new("RGB", (900, 700), (120, 120, 120))
    candidates = (
        strips.SeparatorCandidate(250, 0.95, 0.95, 200, 300, "left"),
        strips.SeparatorCandidate(355, 0.94, 0.94, 309, 400, "right"),
    )
    monkeypatch.setattr(strips, "color_agnostic_separator_candidates", lambda _image: candidates)

    rows = strips.color_agnostic_row_classifications(image)

    assert all(rows[y][0] == "canonical_panel" for y in range(300, 309))



def test_color_agnostic_rows_bridge_trailing_micro_content_at_source_boundary(monkeypatch):
    from app.services import strips

    image = Image.new("RGB", (900, 700), (120, 120, 120))
    candidates = (
        strips.SeparatorCandidate(650, 0.95, 0.95, 600, 693, "trailing"),
    )
    monkeypatch.setattr(strips, "color_agnostic_separator_candidates", lambda _image: candidates)

    rows = strips.color_agnostic_row_classifications(image)

    assert all(rows[y][0] == "verified_gutter" for y in range(600, 700))
    assert all("micro_boundary_bridge" in rows[y][2] for y in range(693, 700))


def test_color_agnostic_rows_bridge_leading_micro_content_at_source_boundary(monkeypatch):
    from app.services import strips

    image = Image.new("RGB", (900, 700), (120, 120, 120))
    candidates = (
        strips.SeparatorCandidate(55, 0.95, 0.95, 8, 100, "leading"),
    )
    monkeypatch.setattr(strips, "color_agnostic_separator_candidates", lambda _image: candidates)

    rows = strips.color_agnostic_row_classifications(image)

    assert all(rows[y][0] == "verified_gutter" for y in range(0, 100))
    assert all("micro_boundary_bridge" in rows[y][2] for y in range(0, 8))


def test_color_agnostic_rows_keep_nine_row_story_band_at_source_boundary(monkeypatch):
    from app.services import strips

    image = Image.new("RGB", (900, 700), (120, 120, 120))
    candidates = (
        strips.SeparatorCandidate(650, 0.95, 0.95, 600, 691, "trailing"),
    )
    monkeypatch.setattr(strips, "color_agnostic_separator_candidates", lambda _image: candidates)

    rows = strips.color_agnostic_row_classifications(image)

    assert all(rows[y][0] == "canonical_panel" for y in range(691, 700))
