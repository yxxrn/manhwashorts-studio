from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


def _settings_for(tmp_path):
    from app.config import Settings

    return Settings(
        data_dir=tmp_path,
        storage_dir=tmp_path / "storage",
        output_dir=tmp_path / "output",
        tmp_dir=tmp_path / "tmp",
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        secret_key=None,
        fernet_key=None,
        _env_file=None,
    )


def test_concurrent_settings_instances_resolve_one_secret_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MS_SECRET_KEY", raising=False)
    settings = [_settings_for(tmp_path) for _ in range(8)]
    barrier = Barrier(len(settings))

    def resolve(item):
        barrier.wait(timeout=5)
        return item.resolve_secret_key()

    with ThreadPoolExecutor(max_workers=len(settings)) as executor:
        values = list(executor.map(resolve, settings))

    assert len(set(values)) == 1
    assert (tmp_path / ".secret_key").read_text().strip() == values[0]
    assert not list(tmp_path.glob(".*.tmp"))


def test_fernet_key_is_cached_after_first_disk_read(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("MS_FERNET_KEY", raising=False)
    first = _settings_for(tmp_path)
    expected = first.resolve_fernet_key()
    second = _settings_for(tmp_path)

    reads = 0
    original = Path.read_bytes

    def counted_read(path):
        nonlocal reads
        reads += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read)
    assert second.resolve_fernet_key() == expected
    assert second.resolve_fernet_key() == expected
    assert reads == 1
