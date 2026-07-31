# Migrations

Alembic reads the database URL from `app.config`, not `alembic.ini`, so the app
and the migrations cannot disagree and no credentials are committed.

## Applying

```bash
.venv/bin/alembic upgrade head
```

`init_db()` also creates missing tables on startup, which is enough for a fresh
install. Migrations matter when upgrading an existing database, because
`create_all` never alters existing tables.

## Creating a new one

```bash
# after editing app/models.py
.venv/bin/alembic revision --autogenerate -m "add scene overlay style"
```

Always read the generated file before committing. Autogenerate reliably detects
added and removed tables and columns, but it misses renames (it emits a drop plus
an add, which loses data) and it does not always get server defaults or
constraint changes right.

## SQLite caveat

SQLite cannot `ALTER` most columns. `env.py` sets `render_as_batch=True` for
SQLite, which makes Alembic rewrite the table instead. This works, but it is why
migrations should be tested against a copy of a real database:

```bash
cp data/manhwashorts.db /tmp/test.db
MS_DATABASE_URL=sqlite:////tmp/test.db .venv/bin/alembic upgrade head
```

## Rolling back

```bash
.venv/bin/alembic downgrade -1
```

Back up first. Downgrades that drop columns discard the data in them
irreversibly.
