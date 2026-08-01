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
gutter-like row within a search window. When no gutter exists nearby the ideal
position is used unchanged, which is no worse than a fixed split.

Pillow only, no new dependencies.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

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


@dataclass(frozen=True)
class StripSlice:
    """One piece of a sliced strip."""

    index: int
    top: int
    bottom: int
    data: bytes
    #: True when the cut landed on a detected gutter rather than a plain split.
    snapped_to_gutter: bool

    @property
    def height(self) -> int:
        return self.bottom - self.top


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

    means, variances = _row_stats(img)
    top, bottom = _trim_uniform_edges(means, variances, height)
    usable = bottom - top

    # Target height for one 9:16 frame at this width.
    frame_height = width / TARGET_RATIO
    parts = _choose_parts(usable, width, frame_height)

    if parts <= 1:
        return [(top, bottom)], [False]

    scores = [_gutter_score(means[i], variances[i]) for i in range(height)]
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
        cuts.append(row)
        snapped.append(hit)

    bounds = [top, *cuts, bottom]
    spans = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    # A span's flag reflects the cut that opened it; the first one is the trim.
    flags = [False, *snapped]
    return spans, flags


def slice_strip(data: bytes, *, quality: int = 92) -> list[StripSlice]:
    """Split a tall strip into scene-sized pieces.

    Returns a single slice holding the original bytes when the image is not tall
    enough to benefit, so callers can treat both cases the same way.
    """
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        source = img.convert("RGB")
        width, height = source.size

        if not settings.strip_slice_enabled:
            return [StripSlice(0, 0, height, data, False)]

        spans, flags = plan_cuts(source)
        if len(spans) <= 1:
            return [StripSlice(0, 0, height, data, False)]

        pieces: list[StripSlice] = []
        for (top, bottom), snapped in zip(spans, flags, strict=True):
            # ingest rejects anything under 200px; skip a sliver rather than fail.
            if bottom - top < 200:
                continue
            segment = source.crop((0, top, width, bottom))
            buffer = io.BytesIO()
            segment.save(buffer, "JPEG", quality=quality)
            pieces.append(
                StripSlice(
                    index=len(pieces),
                    top=top,
                    bottom=bottom,
                    data=buffer.getvalue(),
                    snapped_to_gutter=snapped,
                )
            )

        if not pieces:
            return [StripSlice(0, 0, height, data, False)]
        return pieces


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
