from __future__ import annotations

import pytest

from app.services import file_lock


def test_nonblocking_lock_excludes_second_handle(tmp_path):
    path = tmp_path / "resource.lock"
    first = path.open("a+")
    second = path.open("a+")
    try:
        file_lock.try_lock(first)
        with pytest.raises(file_lock.LockBusyError):
            file_lock.try_lock(second)
        file_lock.unlock(first)
        file_lock.try_lock(second)
        file_lock.unlock(second)
    finally:
        first.close()
        second.close()
