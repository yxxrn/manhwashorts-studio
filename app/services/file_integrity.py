"""Streaming file-integrity helpers shared by render, review, and publish paths."""

from __future__ import annotations

import hashlib
from pathlib import Path

DEFAULT_HASH_CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path, *, chunk_size: int = DEFAULT_HASH_CHUNK_BYTES) -> str:
    """Return a SHA-256 digest without loading the complete file into memory."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
