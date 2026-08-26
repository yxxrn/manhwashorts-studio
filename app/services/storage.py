"""Local content-addressed object storage.

Mirrors the S3 interface the PRD calls for (``storage_key`` + signed URL), but
backed by the filesystem so v1.0 runs locally with no cloud dependency.
Swapping in S3 means reimplementing this module only.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import settings


class StorageError(RuntimeError):
    """Raised when a storage operation cannot be completed safely."""


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    path: Path
    size_bytes: int
    checksum: str


def _root() -> Path:
    settings.ensure_dirs()
    return settings.storage_dir


def _safe_key(storage_key: str) -> Path:
    """Resolve a key under the storage root, rejecting traversal attempts."""
    root = _root().resolve()
    candidate = (root / storage_key.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise StorageError(f"storage key escapes root: {storage_key!r}")
    return candidate


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_part_path(dest: Path) -> Path:
    """Create a writer-private temp file next to ``dest`` for atomic replace.

    A deterministic ``dest + '.part'`` races when two workers materialize the
    same content-addressed object concurrently: one rename removes the shared
    temp path out from under the other.  A unique sibling keeps writes isolated
    while preserving same-filesystem atomic ``replace`` semantics.
    """
    fd, name = tempfile.mkstemp(
        prefix=f".{dest.name}.", suffix=".part", dir=dest.parent
    )
    os.close(fd)
    return Path(name)


def put_bytes(prefix: str, filename: str, data: bytes) -> StoredObject:
    """Store raw bytes under ``prefix/`` keeping the original extension."""
    checksum = sha256_bytes(data)
    suffix = Path(filename).suffix.lower()
    key = f"{prefix.strip('/')}/{checksum[:16]}{suffix}"
    dest = _safe_key(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = _unique_part_path(dest)
        try:
            tmp.write_bytes(data)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
    return StoredObject(key, dest, len(data), checksum)


def put_file(prefix: str, source: Path, filename: str | None = None) -> StoredObject:
    """Copy an existing file into storage."""
    source = Path(source)
    if not source.is_file():
        raise StorageError(f"source not found: {source}")
    checksum = sha256_file(source)
    suffix = Path(filename or source.name).suffix.lower()
    key = f"{prefix.strip('/')}/{checksum[:16]}{suffix}"
    dest = _safe_key(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = _unique_part_path(dest)
        try:
            shutil.copy2(source, tmp)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
    return StoredObject(key, dest, dest.stat().st_size, checksum)


def path_for(storage_key: str) -> Path:
    """Absolute path for a stored key."""
    return _safe_key(storage_key)


def exists(storage_key: str) -> bool:
    if not storage_key:
        return False
    try:
        return _safe_key(storage_key).is_file()
    except StorageError:
        return False


def read_bytes(storage_key: str) -> bytes:
    path = _safe_key(storage_key)
    if not path.is_file():
        raise StorageError(f"object not found: {storage_key}")
    return path.read_bytes()


def delete(storage_key: str) -> bool:
    """Remove an object. Returns True if a file was deleted."""
    if not storage_key:
        return False
    try:
        path = _safe_key(storage_key)
    except StorageError:
        return False
    if path.is_file():
        path.unlink()
        return True
    return False


def workspace_dir(project_id: str, name: str) -> Path:
    """Scratch directory for a project's render pipeline."""
    d = settings.tmp_dir / project_id / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_path(project_id: str, filename: str) -> Path:
    d = settings.output_dir / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d / filename
