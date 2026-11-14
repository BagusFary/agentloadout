"""Catalog refresh in its own process, guarded by a kernel file lock.

The MCP server starts `python -m agentloadout.refresh` in a new session, so the refresh keeps running when
the harness stops the MCP server. Whoever refreshes (that worker or `agentloadout refresh`) holds an
exclusive lock on <home>/refresh.lock for the whole run. The kernel drops the lock when the process dies,
so a crashed refresh never blocks the next one and a reused PID means nothing. The PID is only written
to <home>/refresh.pid as information.

start_background() takes the lock *before* it starts the worker and hands the locked file to it, so two
concurrent callers cannot both start one.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import filelock, paths

WORKER = [sys.executable, "-m", "agentloadout.refresh"]  # tests replace this


def lock_path() -> Path:
    return paths.home() / "refresh.lock"


def pid_path() -> Path:
    return paths.home() / "refresh.pid"


def log_path() -> Path:
    return paths.home() / "refresh.log"


def _write_pid(pid: int):
    tmp = pid_path().with_name("refresh.pid.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(str(pid))
    os.replace(tmp, pid_path())


def try_lock() -> int | None:
    """The locked fd, or None when a refresh holds the lock."""
    try:
        return filelock.acquire(lock_path(), blocking=False)
    except BlockingIOError:
        return None


def running() -> bool:
    """True while some process holds the refresh lock. Lock-file contents (old PIDs) do not matter."""
    if not lock_path().exists():
        return False
    fd = try_lock()
    if fd is None:
        return True
    filelock.release(fd)
    return False


def start_background() -> bool:
    """Start a detached refresh process unless one is running. Returns True if this call started it."""
    fd = try_lock()
    if fd is None:
        return False
    try:
        with open(log_path(), "a") as log:
            kw = {"pass_fds": (fd,)} if os.name != "nt" else {}
            proc = subprocess.Popen([*WORKER, "--lock-fd", str(fd)] if os.name != "nt" else WORKER,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, env=os.environ.copy(), **kw)
        _write_pid(proc.pid)
    finally:
        os.close(fd)  # the worker holds the same open file, so the lock stays until it exits
    return True


def run_locked(fd: int | None = None) -> int:
    """Back up and collect while holding the lock (`fd` = lock inherited from start_background)."""
    if fd is None:
        fd = try_lock()
        if fd is None:
            print("agentloadout: refresh lain sedang berjalan; coba lagi nanti.", file=sys.stderr)
            return 1
    _write_pid(os.getpid())
    try:
        from . import backup, collector
        backup.main_args([])
        collector.main()
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"agentloadout: refresh gagal: {e}", file=sys.stderr)
        return 1
    finally:
        pid_path().unlink(missing_ok=True)
        os.close(fd)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    fd = int(argv[1]) if len(argv) == 2 and argv[0] == "--lock-fd" else None
    return run_locked(fd)


if __name__ == "__main__":
    sys.exit(main())
