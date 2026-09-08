"""Cross-platform advisory locks for one-process-at-a-time resources."""
from __future__ import annotations

import errno
import os
from typing import TextIO


class LockBusyError(RuntimeError):
    """Raised when a non-blocking lock is already held elsewhere."""


def try_lock(handle: TextIO) -> None:
    """Acquire an exclusive non-blocking lock on an open file handle."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EDEADLK, errno.EAGAIN}:
                raise LockBusyError("lock is already held") from exc
            raise
        return

    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise LockBusyError("lock is already held") from exc


def unlock(handle: TextIO) -> None:
    """Release a lock previously acquired with :func:`try_lock`."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            return
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
