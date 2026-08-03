"""Shared pytest fixtures.

Every test runs against a throwaway SQLite database and storage root inside
tmp_path, so tests never touch the developer's real data/ directory. TTS is
forced to the null provider so the suite does not depend on espeak-ng and stays
fast.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Set before collection: app settings/engine may initialize during imports.
TEST_RUN_DIR = ROOT / "data" / "test_runs" / f"pid{os.getpid()}"
os.environ["MS_TEST_MODE"] = "1"
os.environ["MS_DATA_DIR"] = str(TEST_RUN_DIR)
os.environ["MS_STORAGE_DIR"] = str(TEST_RUN_DIR / "storage")
os.environ["MS_OUTPUT_DIR"] = str(TEST_RUN_DIR / "output")
os.environ["MS_TMP_DIR"] = str(TEST_RUN_DIR / "tmp")
os.environ["MS_DATABASE_URL"] = f"sqlite:///{TEST_RUN_DIR / 'test.db'}"
os.environ["MS_TTS_PROVIDER"] = "null"
os.environ["MS_SECRET_KEY"] = "test-secret-key-not-for-production-use"
os.environ["MS_ENVIRONMENT"] = "local"
os.environ["MS_YOUTUBE_ENABLED"] = "false"


@pytest.fixture(scope="session", autouse=True)
def _isolate_environment() -> Iterator[None]:
    """Point the app at a scratch data directory before it is imported.

    Deliberately NOT pytest's tmp_path_factory: that lives under /tmp, which is
    often a small tmpfs (1.9 GB here). The render tests write real MP4s and
    exhausted it, producing confusing "Disk quota exceeded" failures. A
    disk-backed directory inside the repo avoids that, and is removed afterwards.
    """
    data_dir = TEST_RUN_DIR
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ["MS_DATA_DIR"] = str(data_dir)
    os.environ["MS_STORAGE_DIR"] = str(data_dir / "storage")
    os.environ["MS_OUTPUT_DIR"] = str(data_dir / "output")
    os.environ["MS_TMP_DIR"] = str(data_dir / "tmp")
    os.environ["MS_DATABASE_URL"] = f"sqlite:///{data_dir / 'test.db'}"
    os.environ["MS_TTS_PROVIDER"] = "null"
    os.environ["MS_SECRET_KEY"] = "test-secret-key-not-for-production-use"
    os.environ["MS_ENVIRONMENT"] = "local"
    os.environ["MS_YOUTUBE_ENABLED"] = "false"

    yield

    # Rendered MP4s add up fast; do not leave them behind.
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def app_settings(_isolate_environment):
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()
    return settings


@pytest.fixture()
def db(app_settings):
    """A fresh database per test."""
    from app import models  # noqa: F401
    from app.db import Base, SessionLocal, engine, safe_drop_all

    safe_drop_all(Base.metadata, engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def client(app_settings):
    """TestClient with a clean database."""
    from fastapi.testclient import TestClient

    from app.db import Base, engine, safe_drop_all
    from app.main import app

    safe_drop_all(Base.metadata, engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_client(client):
    """A logged-in client plus its user id."""
    response = client.post(
        "/api/auth/register",
        json={"email": "tester@example.com", "password": "testpass1234", "name": "Tester"},
    )
    assert response.status_code == 201, response.text
    return client


@pytest.fixture()
def recap_text() -> str:
    """Original filler recap. Contains no third-party material."""
    return (
        "Bab ini dibuka dengan Rian, pemburu peringkat E yang namanya jarang "
        "disebut siapa pun di asosiasi. Selama tiga tahun dia hanya mendapat "
        "misi sisa di pinggiran kota.\n\n"
        "Ketika sebuah gerbang tak terdaftar muncul di bawah stasiun tua, tim "
        "peringkat A menolak masuk karena bayarannya terlalu kecil. Rian "
        "menerima misi itu sendirian.\n\n"
        "Rian terpisah dari jalur keluar ketika lantai runtuh. Di ruang paling "
        "bawah dia menemukan papan bercahaya yang hanya bisa dilihat olehnya. "
        "Papan itu memaksa Rian menyelesaikan latihan harian atau dipindahkan "
        "ke zona hukuman yang berbahaya.\n\n"
        "Ternyata setiap kegagalan tidak menghapus kemajuannya. Papan itu "
        "menyimpan hasilnya dan menaikkan batas kekuatan Rian sedikit demi "
        "sedikit.\n\n"
        "Di akhir bab, Sera, ketua tim peringkat A, berdiri di depan gerbang "
        "yang sudah tertutup dan membaca laporan bahwa hanya satu orang masuk."
    )


@pytest.fixture()
def panel_bytes() -> bytes:
    """A small valid JPEG for upload tests."""
    import io

    from PIL import Image

    img = Image.new("RGB", (900, 1200), (40, 40, 70))
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


@pytest.fixture(scope="session")
def mock_provider_url() -> Iterator[str]:
    """A local stand-in for an AI vendor, so BYOK tests need no network or key.

    Session-scoped: one server serves every test, on a port the OS picks so
    parallel runs cannot collide.
    """
    import threading

    sys.path.insert(0, str(ROOT / "tests"))
    import mock_provider

    server = mock_provider.serve(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def good_key() -> str:
    """The only key the mock provider accepts."""
    sys.path.insert(0, str(ROOT / "tests"))
    import mock_provider

    return mock_provider.GOOD_KEY


@pytest.fixture()
def declared_rights() -> dict:
    return {
        "rights_owner": "Tester",
        "license_type": "owned",
        "source_name": "Written for tests",
        "attribution": "Tester",
        "declared": True,
    }
