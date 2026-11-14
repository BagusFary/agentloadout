"""Inter-process file locks: fcntl.flock on POSIX, msvcrt.locking on Windows.

The kernel releases the lock when the process that holds it dies, so a crashed holder never leaves a
lock behind and a reused PID cannot keep one alive.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

if os.name == "nt":  # pragma: no cover - not tested on Windows yet
    import msvcrt

    def _lock(fd: int, blocking: bool):
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        except OSError as e:
            raise BlockingIOError(str(e)) from e

    def _unlock(fd: int):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd: int, blocking: bool):
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))

    def _unlock(fd: int):
        fcntl.flock(fd, fcntl.LOCK_UN)


def acquire(path: Path, blocking: bool = True) -> int:
    """Open `path` (created 0600) and lock it exclusively. Returns the fd; the lock lives until it is closed.
    Raises BlockingIOError when blocking=False and another process holds it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _lock(fd, blocking)
    except BaseException:
        os.close(fd)
        raise
    return fd


def release(fd: int):
    try:
        _unlock(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def exclusive(path: Path, blocking: bool = True):
    fd = acquire(path, blocking)
    try:
        yield fd
    finally:
        release(fd)
