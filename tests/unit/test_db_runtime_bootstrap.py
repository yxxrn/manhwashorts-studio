from __future__ import annotations


def test_init_db_uses_create_all_only_in_test_mode(monkeypatch):
    from app import db

    seen = {"create": 0, "upgrade": 0}
    monkeypatch.setenv("MS_TEST_MODE", "1")
    monkeypatch.setattr(
        db.Base.metadata,
        "create_all",
        lambda bind=None: seen.__setitem__("create", seen["create"] + 1),
    )
    monkeypatch.setattr(
        db,
        "_upgrade_runtime_schema",
        lambda: seen.__setitem__("upgrade", seen["upgrade"] + 1),
    )

    db.init_db()

    assert seen == {"create": 1, "upgrade": 0}


def test_init_db_uses_alembic_for_normal_runtime(monkeypatch):
    from app import db

    seen = {"create": 0, "upgrade": 0}
    monkeypatch.delenv("MS_TEST_MODE", raising=False)
    monkeypatch.setattr(
        db.Base.metadata,
        "create_all",
        lambda bind=None: seen.__setitem__("create", seen["create"] + 1),
    )
    monkeypatch.setattr(
        db,
        "_upgrade_runtime_schema",
        lambda: seen.__setitem__("upgrade", seen["upgrade"] + 1),
    )

    db.init_db()

    assert seen == {"create": 0, "upgrade": 1}
