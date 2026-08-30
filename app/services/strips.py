"""Slice tall webtoon strips into scene-sized pieces (v1.4.0).

Manhwa pages are published as one long vertical strip — commonly 1:5 or taller.
Cropping such a page to a single 9:16 frame keeps under a third of it, so an
entire story beat (the action, the reaction, the follow-up) is reduced to
whichever slice happened to sit at the focal point. Measured on a real
720x4372 page: 70.7% of the image discarded.

So instead of cropping once, split the strip into consecutive 9:16-ish pieces
and let each one become its own scene. Reading top to bottom is how the page was
meant to be read, and it matches the scroll feel of the original.

Cut placement matters. A naive split every N pixels slices through faces and
speech balloons. Webtoon pages already separate their beats with gutters —
near-uniform white or black bands — so each cut is nudged to the most
gutter-like row within a search window. When no verified separator exists nearby, the source remains connected and
is handled later through overlapping vision windows instead of a forced cut.

Pillow only, no new dependencies.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from math import sqrt

from PIL import Image

from app.config import settings

#: Output aspect ratio (9:16 portrait video).
TARGET_RATIO = 9 / 16

#: Rows whose mean brightness is at least this are treated as a white gutter.
_BRIGHT = 236
#: Rows whose mean brightness is at most this are treated as a black gutter.
_DARK = 22
#: Wider band that still counts, at reduced confidence.
_BRIGHT_SOFT = 215
_DARK_SOFT = 45

#: Fraction of a segment's height searched either side of an ideal cut.
_SEARCH_FRACTION = 0.18

#: A cut is never placed closer than this fraction of a segment to its neighbour.
_MIN_SEGMENT_FRACTION = 0.55

# This detector is intentionally based on structure rather than brightness.
# The version participates in downstream cache/review identities.
COLOR_AGNOSTIC_DETECTOR_VERSION = "color-agnostic-gutter-v5"
_MICRO_GUTTER_GAP_MAX_ROWS = 8
VERIFIED_BLANK_DETECTOR_VERSION = "extreme-full-width-blank-v2"
_BLANK_VARIANCE_MAX = 25.0
_BLANK_BRIGHT_MIN = 245.0
_BLANK_DARK_MAX = 10.0


@dataclass(frozen=True)
class SeparatorCandidate:
    """A deterministic, source-row separator candidate."""

    position: int
    confidence: float
    score: float
    run_top: int
    run_bottom: int
    reason: str


@dataclass(frozen=True)
class StripSlice:
    """One piece of a sliced strip."""

    index: int
    top: int
    bottom: int
    data: bytes
    #: True when the cut landed on a detected gutter rather than a plain split.
    snapped_to_gutter: bool
    original_checksum: str = ""
    original_width: int = 0
    original_height: int = 0
    source_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    strip_order: int = 0
    region_order: int = 0
    trim_classification: str = "unsliced"
    coverage_map_hash: str = ""

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _colour_distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))


def _row_structure_features(
    img: Image.Image,
    *,
    max_sample_width: int = 64,
) -> tuple[list[tuple[float, float, float]], list[float], list[float]]:
    """Return mean colour, within-row variance, and texture for every source row.

    The horizontal sample is bounded while the vertical axis is preserved, so
    every candidate remains in the original source coordinate space. No
    brightness or colour-class threshold is used here.
    """
    source = img.convert("RGB")
    width, height = source.size
    sample_width = max(1, min(width, max_sample_width))
    sampled = source.resize((sample_width, height), Image.Resampling.BOX)
    pixels = sampled.load()
    means: list[tuple[float, float, float]] = []
    variances: list[float] = []
    textures: list[float] = []
    for y in range(height):
        row = [pixels[x, y] for x in range(sample_width)]
        mean = tuple(sum(pixel[channel] for pixel in row) / sample_width for channel in range(3))
        variance = sum(
            (pixel[channel] - mean[channel]) ** 2
            for pixel in row
            for channel in range(3)
        ) / (sample_width * 3)
        texture = (
            sum(
                abs(row[index][channel] - row[index - 1][channel])
                for index in range(1, sample_width)
                for channel in range(3)
            )
            / max(1, (sample_width - 1) * 3)
        )
        means.append(mean)
        variances.append(variance)
        textures.append(texture)
    return means, variances, textures


def color_agnostic_separator_candidates(
    img: Image.Image,
    *,
    max_pixels: int = 24_000_000,
) -> tuple[SeparatorCandidate, ...]:
    """Find sustained structural gutters without assuming white or black.

    A candidate needs low local variance/texture, continuity through the band,
    and textured/contrasting context on both sides. This rejects a broad flat
    sky or wall when it has no separator context. Results are sorted by stable
    confidence then source position.
    """
    width, height = img.size
    if width <= 0 or height <= 0:
        return ()
    if width * height > max_pixels:
        raise ValueError("segmentation.pixel_budget_exceeded")
    means, variances, textures = _row_structure_features(img)
    low_information = [variance <= 90.0 and texture <= 10.0 for variance, texture in zip(variances, textures, strict=True)]
    min_run = max(24, min(96, int(height * 0.006)))
    candidates: list[SeparatorCandidate] = []
    index = 0
    while index < height:
        if not low_information[index]:
            index += 1
            continue
        start = index
        while index < height and low_information[index]:
            if index > start and _colour_distance(means[index], means[index - 1]) > 8.0:
                break
            index += 1
        end = index
        run_length = end - start
        if run_length < min_run or start == 0 or end == height:
            continue
        left_delta = _colour_distance(means[start], means[start - 1])
        right_delta = _colour_distance(means[end - 1], means[end])
        edge_score = min(1.0, (left_delta + right_delta) / 180.0)
        neighbour_texture = max(
            max(textures[max(0, start - 4):start], default=0.0),
            max(textures[end:min(height, end + 4)], default=0.0),
        )
        context_score = min(1.0, edge_score * 0.55 + neighbour_texture / 35.0)
        flatness = min(
            1.0,
            max(0.0, 1.0 - (sum(variances[start:end]) / run_length) / 90.0),
        )
        band_score = min(1.0, run_length / max(1.0, min_run * 2.0))
        confidence = round(
            0.45 * flatness + 0.25 * edge_score + 0.20 * context_score + 0.10 * band_score,
            6,
        )
        if confidence < 0.7 or edge_score < 0.22 or context_score < 0.2:
            continue
        candidates.append(
            SeparatorCandidate(
                position=(start + end) // 2,
                confidence=confidence,
                score=confidence,
                run_top=start,
                run_bottom=end,
                reason=(
                    f"{COLOR_AGNOSTIC_DETECTOR_VERSION};low_variance_texture;"
                    f"edge_context={edge_score:.3f};continuity={context_score:.3f}"
                ),
            )
        )
    blank_candidates = verified_blank_spans(img, row_textures=textures)
    combined: list[SeparatorCandidate] = list(blank_candidates)
    for candidate in candidates:
        if any(
            candidate.run_top < blank.run_bottom
            and candidate.run_bottom > blank.run_top
            for blank in blank_candidates
        ):
            continue
        combined.append(candidate)
    return tuple(sorted(combined, key=lambda item: (-item.confidence, item.position)))


def color_agnostic_row_classifications(
    img: Image.Image,
) -> list[tuple[str, float, str]]:
    """Classify rows for source-space coverage reconciliation."""
    _, height = img.size
    classifications = [
        (
            "canonical_panel",
            0.9,
            f"coverage.content.{COLOR_AGNOSTIC_DETECTOR_VERSION}",
        )
        for _ in range(height)
    ]
    for candidate in color_agnostic_separator_candidates(img):
        for row in range(candidate.run_top, candidate.run_bottom):
            classifications[row] = (
                "verified_gutter",
                candidate.confidence,
                f"coverage.gutter.{candidate.reason}",
            )

    # Detector spans from different gutter signals can occasionally leave a
    # microscopic full-width content island between two verified separators
    # (observed in production at 1-7 source rows). Such a strip cannot carry a
    # usable visual panel and some vision providers reject the resulting image
    # outright. Bridge only tiny islands that are bounded by verified gutters
    # on both sides; real story bands remain untouched.
    index = 0
    while index < height:
        if classifications[index][0] != "canonical_panel":
            index += 1
            continue
        start = index
        while index < height and classifications[index][0] == "canonical_panel":
            index += 1
        end = index
        is_micro = end - start <= _MICRO_GUTTER_GAP_MAX_ROWS
        left_gutter = start > 0 and classifications[start - 1][0] == "verified_gutter"
        right_gutter = end < height and classifications[end][0] == "verified_gutter"
        bounded_gap = left_gutter and right_gutter
        boundary_sliver = (start == 0 and right_gutter) or (end == height and left_gutter)
        if is_micro and (bounded_gap or boundary_sliver):
            neighbor_confidences = []
            if left_gutter:
                neighbor_confidences.append(float(classifications[start - 1][1]))
            if right_gutter:
                neighbor_confidences.append(float(classifications[end][1]))
            confidence = min(neighbor_confidences)
            bridge_kind = "micro_gap_bridge" if bounded_gap else "micro_boundary_bridge"
            evidence = (
                f"coverage.gutter.{COLOR_AGNOSTIC_DETECTOR_VERSION};"
                f"{bridge_kind};rows={start}-{end}"
            )
            for row in range(start, end):
                classifications[row] = ("verified_gutter", confidence, evidence)
    return classifications


def color_agnostic_row_scores(img: Image.Image) -> list[float]:
    scores = [0.0] * img.size[1]
    for candidate in color_agnostic_separator_candidates(img):
        for row in range(candidate.run_top, candidate.run_bottom):
            scores[row] = max(scores[row], candidate.confidence)
    return scores


def _row_stats(img: Image.Image) -> tuple[list[float], list[float]]:
    """Per-row mean brightness and variance.

    Resizing to a single column with a BOX filter averages each row in C, which
    is far cheaper than cropping thousands of rows in Python. Variance comes
    from the same trick applied to a squared-value copy: ``E[v^2] - E[v]^2``.
    The lookup table divides by 255 to stay inside 8-bit range, so the mean of
    squares is scaled back up afterwards.
    """
    gray = img.convert("L")
    _, height = gray.size

    # tobytes() rather than getdata(): getdata() is deprecated in Pillow 12 and
    # removed in 14, while its replacement does not exist on older versions.
    # A mode "L" image is one byte per pixel, so bytes are the values directly.
    means = [float(v) for v in gray.resize((1, height), Image.Resampling.BOX).tobytes()]
    squared = gray.point(lambda v: (v * v) // 255)
    sq_means = [float(v) for v in squared.resize((1, height), Image.Resampling.BOX).tobytes()]

    variances = [max(0.0, 255.0 * sq_means[i] - means[i] ** 2) for i in range(height)]
    return means, variances


def verified_blank_spans(
    img: Image.Image,
    *,
    min_run: int | None = None,
    row_textures: list[float] | None = None,
) -> tuple[SeparatorCandidate, ...]:
    """Return only full-width, extreme, low-variance blank bands.

    Unlike the structural gutter detector, this fast path does not require
    textured context on both sides.  That makes it suitable for large blank
    regions at page ends and between webtoon beats.  The thresholds are
    deliberately strict so pale artwork, skies, walls, and effects remain
    canonical content unless they are truly near-uniform across the full row.
    """
    width, height = img.size
    if width <= 0 or height <= 0:
        return ()
    means, variances = _row_stats(img)
    required_run = (
        max(24, min(96, int(round(width / 18.0))))
        if min_run is None
        else max(1, int(min_run))
    )
    candidates: list[SeparatorCandidate] = []
    index = 0
    while index < height:
        is_blank = (
            variances[index] <= _BLANK_VARIANCE_MAX
            and (
                means[index] >= _BLANK_BRIGHT_MIN
                or means[index] <= _BLANK_DARK_MAX
            )
        )
        if not is_blank:
            index += 1
            continue
        start = index
        index += 1
        while index < height and (
            variances[index] <= _BLANK_VARIANCE_MAX
            and (
                means[index] >= _BLANK_BRIGHT_MIN
                or means[index] <= _BLANK_DARK_MAX
            )
        ):
            index += 1
        end = index
        run_length = end - start
        if run_length < required_run:
            continue
        mean_variance = sum(variances[start:end]) / run_length
        extremity = max(
            0.0,
            max(
                min(means[start:end], default=255.0) - _BLANK_BRIGHT_MIN,
                _BLANK_DARK_MAX - max(means[start:end], default=0.0),
            ),
        )
        flatness = max(0.0, 1.0 - mean_variance / max(1.0, _BLANK_VARIANCE_MAX))
        run_score = min(1.0, run_length / max(1.0, required_run * 3.0))
        confidence = round(min(1.0, 0.9 + 0.05 * flatness + 0.03 * run_score + 0.02 * min(1.0, extremity / 10.0)), 6)
        tone = "near_white" if sum(means[start:end]) / run_length >= 127.5 else "near_black"
        candidates.append(
            SeparatorCandidate(
                position=(start + end) // 2,
                confidence=confidence,
                score=confidence,
                run_top=start,
                run_bottom=end,
                reason=(
                    f"{VERIFIED_BLANK_DETECTOR_VERSION};{tone};"
                    f"mean_variance={mean_variance:.3f};run={run_length}"
                ),
            )
        )
    if len(candidates) <= 1:
        return tuple(candidates)
    textures = row_textures
    if textures is None:
        _, _, textures = _row_structure_features(img)
    max_gap = max(12, min(128, int(round(width * 0.12))))
    merged: list[SeparatorCandidate] = [candidates[0]]
    for candidate in candidates[1:]:
        previous = merged[-1]
        gap_top = previous.run_bottom
        gap_bottom = candidate.run_top
        gap = gap_bottom - gap_top
        bridge = False
        if 0 < gap <= max_gap:
            gap_means = means[gap_top:gap_bottom]
            gap_textures = textures[gap_top:gap_bottom]
            same_extreme_tone = bool(gap_means) and (
                all(value >= _BLANK_BRIGHT_MIN for value in gap_means)
                or all(value <= _BLANK_DARK_MAX for value in gap_means)
            )
            low_texture = bool(gap_textures) and (
                sum(gap_textures) / len(gap_textures) <= 3.0
                and max(gap_textures) <= 8.0
            )
            gap_variances = variances[gap_top:gap_bottom]
            faint_extreme_mark = bool(gap_variances) and (
                sum(gap_variances) / len(gap_variances)
                <= _BLANK_VARIANCE_MAX
            )
            bridge = same_extreme_tone and (
                low_texture or faint_extreme_mark
            )
        if not bridge:
            merged.append(candidate)
            continue
        merged[-1] = SeparatorCandidate(
            position=(previous.run_top + candidate.run_bottom) // 2,
            confidence=min(previous.confidence, candidate.confidence),
            score=min(previous.score, candidate.score),
            run_top=previous.run_top,
            run_bottom=candidate.run_bottom,
            reason=(
                f"{VERIFIED_BLANK_DETECTOR_VERSION};bridged_low_texture_blank;"
                f"gap={gap}"
            ),
        )
    return tuple(merged)


def _gutter_score(mean: float, variance: float) -> float:
    """How much a row looks like a panel gutter. Higher is a better cut.

    A gutter is flat (low variance) *and* extreme (near-white or near-black).
    Flatness alone is not enough: a large area of even mid-tone sky is smooth
    but cutting through it splits the artwork.
    """
    uniformity = 1.0 / (1.0 + variance / 40.0)

    if mean >= _BRIGHT or mean <= _DARK:
        extremity = 1.0
    elif mean >= _BRIGHT_SOFT or mean <= _DARK_SOFT:
        extremity = 0.55
    else:
        extremity = 0.15

    return uniformity * extremity


def _trim_uniform_edges(
    means: list[float], variances: list[float], height: int
) -> tuple[int, int]:
    """Drop flat white/black margins at the very top and bottom.

    Scans are often exported with a blank band on each end. Including it wastes
    part of the first and last scene on empty space. Only genuinely flat and
    extreme rows are removed, and never more than 12% from either end so a
    legitimately pale panel is not eaten.
    """
    max_trim = int(height * 0.12)

    def is_blank(i: int) -> bool:
        return variances[i] < 25.0 and (means[i] >= _BRIGHT or means[i] <= _DARK)

    top = 0
    while top < max_trim and is_blank(top):
        top += 1

    bottom = height
    while bottom > height - max_trim and is_blank(bottom - 1):
        bottom -= 1

    # Guard against a page that is blank end to end.
    if bottom - top < height * 0.5:
        return 0, height
    return top, bottom


def _best_cut(scores: list[float], ideal: int, radius: int, low: int, high: int) -> tuple[int, bool]:
    """Pick the most gutter-like row near ``ideal``, staying within [low, high).

    Ties break toward ``ideal`` so cuts stay evenly spaced when the page has no
    usable gutter. Returns the row and whether a real gutter was found.
    """
    start = max(low, ideal - radius)
    stop = min(high, ideal + radius + 1)
    if start >= stop:
        return ideal, False

    best_row = ideal
    best_score = -1.0
    for row in range(start, stop):
        # Slight preference for staying near the ideal position.
        distance_penalty = abs(row - ideal) / max(1, radius) * 0.08
        score = scores[row] - distance_penalty
        if score > best_score:
            best_score = score
            best_row = row

    # Below this the row is ordinary artwork, so the split is arbitrary anyway.
    return best_row, scores[best_row] >= 0.35


def _retention(usable: int, width: int, frame_height: float, parts: int) -> float:
    """Fraction of the strip that survives if it is split into ``parts`` pieces.

    Each piece is later forced to exactly 9:16 by ``crop_to_vertical``, and which
    dimension gets trimmed depends on the piece's shape:

    * Piece taller than a frame -> height is cropped, so *story* is lost. This is
      the expensive kind: a speech balloon or a whole beat can disappear.
    * Piece shorter than a frame -> width is cropped, so the *sides* are lost.
      Webtoon art keeps its subject near the horizontal centre, so this is
      usually only margin.

    Both are expressed as a kept-fraction here so the caller can compare them
    directly rather than assuming one is always better.
    """
    if parts < 1:
        return 0.0
    segment = usable / parts
    if segment > frame_height:
        return frame_height / segment
    return (segment * TARGET_RATIO) / width


def _choose_parts(usable: int, width: int, frame_height: float) -> int:
    """Pick the piece count that keeps the most of the page.

    Rounding down always yields pieces at least a frame tall, which sounds safe
    but silently crops height — on a 720x3667 page that discards 30% of the
    story, while rounding up costs only 5% of the side margins. Neither rule
    wins everywhere, so both candidates are scored and the better one is used.
    """
    if frame_height <= 0:
        return 1

    lower = int(usable // frame_height)
    upper = lower + 1
    candidates = {
        max(1, min(n, settings.strip_slice_max_parts))
        for n in (lower, upper)
    }
    return max(candidates, key=lambda n: (_retention(usable, width, frame_height, n), -n))


def plan_cuts(img: Image.Image) -> tuple[list[tuple[int, int]], list[bool]]:
    """Work out where to split ``img``. Returns (spans, snapped flags).

    A single span covering the whole image means it should not be sliced.
    """
    width, height = img.size
    if width <= 0 or height <= 0:
        return [(0, height)], [False]

    ratio = height / width
    if ratio < settings.strip_slice_min_ratio:
        return [(0, height)], [False]

    # Keep the complete source extent. Older versions trimmed extreme end
    # bands, which could silently drop source pixels from a persisted family.
    top, bottom = 0, height
    usable = height

    # Target height for one 9:16 frame at this width.
    frame_height = width / TARGET_RATIO
    parts = _choose_parts(usable, width, frame_height)

    if parts <= 1:
        return [(top, bottom)], [False]

    scores = color_agnostic_row_scores(img)
    segment = usable / parts
    radius = int(segment * _SEARCH_FRACTION)
    min_gap = int(segment * _MIN_SEGMENT_FRACTION)

    cuts: list[int] = []
    snapped: list[bool] = []
    for i in range(1, parts):
        ideal = int(round(top + segment * i))
        low = (cuts[-1] if cuts else top) + min_gap
        # Leave room for the remaining cuts plus a final segment.
        high = bottom - min_gap * (parts - i)
        row, hit = _best_cut(scores, ideal, radius, low, max(low + 1, high))
        # Never persist an arbitrary geometry cut through artwork.  A tall
        # connected scene can be inspected later through overlapping vision
        # windows without destroying its canonical source lineage.
        if not hit:
            continue
        cuts.append(row)
        snapped.append(True)

    bounds = [top, *cuts, bottom]
    spans = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    # A span's flag reflects the cut that opened it; the first one is the trim.
    flags = [False, *snapped]
    return spans, flags


def _normalise_spans(
    spans: list[tuple[int, int]],
    flags: list[bool],
    height: int,
) -> tuple[list[tuple[int, int]], list[bool]]:
    # Make cuts a complete partition and merge, rather than drop, slivers.
    raw: list[tuple[int, int, bool]] = []
    for index, (top, bottom) in enumerate(spans):
        if bottom <= 0 or top >= height or bottom <= top:
            continue
        raw.append((
            max(0, top),
            min(height, bottom),
            bool(flags[index]) if index < len(flags) else False,
        ))
    if not raw:
        return [(0, height)], [False]
    raw.sort(key=lambda item: (item[0], item[1]))
    boundaries = [0]
    for _, bottom, _ in raw[:-1]:
        if boundaries[-1] < bottom < height:
            boundaries.append(bottom)
    if boundaries[-1] != height:
        boundaries.append(height)
    normalised = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
    ]
    normalised_flags = [
        raw[index][2] if index < len(raw) else False
        for index in range(len(normalised))
    ]
    while len(normalised) > 1:
        sliver_index = next(
            (index for index, (top, bottom) in enumerate(normalised) if bottom - top < 200),
            None,
        )
        if sliver_index is None:
            break
        if sliver_index == 0:
            left_index, right_index = 0, 1
        elif sliver_index == len(normalised) - 1:
            left_index, right_index = sliver_index - 1, sliver_index
        else:
            left_height = normalised[sliver_index - 1][1] - normalised[sliver_index - 1][0]
            right_height = normalised[sliver_index + 1][1] - normalised[sliver_index + 1][0]
            if left_height >= right_height:
                left_index, right_index = sliver_index - 1, sliver_index
            else:
                left_index, right_index = sliver_index, sliver_index + 1
        merged = (normalised[left_index][0], normalised[right_index][1])
        merged_flag = normalised_flags[left_index] or normalised_flags[right_index]
        normalised[left_index:right_index + 1] = [merged]
        normalised_flags[left_index:right_index + 1] = [merged_flag]
    return normalised, normalised_flags


def slice_strip(data: bytes, *, quality: int = 92) -> list[StripSlice]:
    """Split a strip while preserving complete original source lineage."""
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        source = img.convert("RGB")
        width, height = source.size
        original_checksum = hashlib.sha256(data).hexdigest()
        full_bounds = (0, 0, width, height)

        def full_slice() -> StripSlice:
            return StripSlice(
                index=0,
                top=0,
                bottom=height,
                data=data,
                snapped_to_gutter=False,
                original_checksum=original_checksum,
                original_width=width,
                original_height=height,
                source_bounds=full_bounds,
                strip_order=0,
                region_order=0,
                trim_classification="unsliced",
                coverage_map_hash="",
            )

        if not settings.strip_slice_enabled:
            return [full_slice()]
        spans, flags = plan_cuts(source)
        spans, flags = _normalise_spans(spans, flags, height)
        if len(spans) <= 1:
            return [full_slice()]

        pieces: list[StripSlice] = []
        for index, ((top, bottom), snapped) in enumerate(zip(spans, flags, strict=True)):
            segment = source.crop((0, top, width, bottom))
            buffer = io.BytesIO()
            segment.save(buffer, "JPEG", quality=quality)
            pieces.append(
                StripSlice(
                    index=index,
                    top=top,
                    bottom=bottom,
                    data=buffer.getvalue(),
                    snapped_to_gutter=snapped,
                    original_checksum=original_checksum,
                    original_width=width,
                    original_height=height,
                    source_bounds=(0, top, width, bottom),
                    strip_order=0,
                    region_order=index,
                    trim_classification=(
                        "gutter_snapped" if snapped else "deterministic_split"
                    ),
                    coverage_map_hash="",
                )
            )
        return pieces or [full_slice()]


def describe(data: bytes) -> dict:
    """Read-only summary used by the UI and by tests. Never raises on content."""
    with Image.open(io.BytesIO(data)) as img:
        width, height = img.size
        ratio = height / width if width else 0.0
        source = img.convert("RGB")
        spans, _ = plan_cuts(source)

    frame_height = width / TARGET_RATIO if width else 1.0
    single_crop_kept = min(1.0, frame_height / height) if height else 0.0

    # Score each piece the same way _retention does, so a piece shorter than a
    # frame is charged for lost width rather than counted as a free win.
    if height and spans:
        kept_rows = 0.0
        for top, bottom in spans:
            segment = bottom - top
            if segment > frame_height:
                kept_rows += frame_height          # height cropped: story lost
            else:
                kept_rows += segment * ((segment * TARGET_RATIO) / width)
        sliced_kept = kept_rows / height
    else:
        sliced_kept = 0.0

    return {
        "width": width,
        "height": height,
        "ratio": round(ratio, 2),
        "slices": len(spans),
        "kept_single_crop": round(single_crop_kept * 100, 1),
        "kept_sliced": round(sliced_kept * 100, 1),
    }
