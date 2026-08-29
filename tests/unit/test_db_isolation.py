import os

import pytest


def test_destructive_reset_requires_test_database():
    from app.db import Base, engine, safe_drop_all

    assert os.environ["MS_TEST_MODE"] == "1"
    safe_drop_all(Base.metadata, engine)

    os.environ["MS_TEST_MODE"] = "0"
    try:
        with pytest.raises(RuntimeError, match="disabled"):
            safe_drop_all(Base.metadata, engine)
    finally:
        os.environ["MS_TEST_MODE"] = "1"
