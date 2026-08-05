"""Synthetic, rights-safe source strips for the vision coverage red tests."""

from __future__ import annotations

import binascii
import struct
import zlib
from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class SyntheticSourceStrip:
    """One deterministic strip with source-space content-band metadata."""

    source_asset_id: str
    checksum: str
    width: int
    height: int
    source_bounds: tuple[int, int, int, int]
    strip_order: int
    content_bands: tuple[tuple[int, int, int, int], ...]
    payload: bytes

    @property
    def original_checksum(self) -> str:
        return self.checksum


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _synthetic_png(
    width: int,
    height: int,
    bands: tuple[tuple[int, int, int, int], ...],
    accent: int,
) -> bytes:
    """Return a tiny RGB PNG with colored content bands and white gutters."""

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        band_index = next(
            (
                index
                for index, (_, top, _, bottom) in enumerate(bands)
                if top <= y < bottom
            ),
            None,
        )
        if band_index is None:
            color = (255, 255, 255)
        else:
            color = (
                32 + accent * 11 + band_index * 19,
                76 + band_index * 23,
                144 + accent * 7,
            )
        rows.extend(bytes(color) * width)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def make_ordered_source_strips() -> tuple[SyntheticSourceStrip, ...]:
    """Create three ordered strips with three source-space bands each."""

    specs = (
        ("synthetic-strip-01", 64, 192, ((0, 0, 64, 48), (0, 64, 64, 112), (0, 128, 64, 192))),
        ("synthetic-strip-02", 72, 224, ((0, 0, 72, 56), (0, 72, 72, 136), (0, 152, 72, 224))),
        ("synthetic-strip-03", 80, 256, ((0, 0, 80, 64), (0, 80, 80, 152), (0, 168, 80, 256))),
    )
    strips: list[SyntheticSourceStrip] = []
    for strip_order, (source_asset_id, width, height, content_bands) in enumerate(specs):
        payload = _synthetic_png(width, height, content_bands, strip_order)
        strips.append(
            SyntheticSourceStrip(
                source_asset_id=source_asset_id,
                checksum=sha256(payload).hexdigest(),
                width=width,
                height=height,
                source_bounds=(0, 0, width, height),
                strip_order=strip_order,
                content_bands=content_bands,
                payload=payload,
            )
        )
    return tuple(strips)


__all__ = ["SyntheticSourceStrip", "make_ordered_source_strips"]
