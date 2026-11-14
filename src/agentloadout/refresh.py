"""Catalog refresh in its own process.

The MCP server starts `python -m agentloadout.refresh` in a new session, so the refresh keeps running when
the harness stops the MCP server. <home>/refresh.lock holds the PID of the running refresh; a lock whose
PID is gone is stale and is taken over.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from . import paths

LEGACY_GRACE = 60  # seconds a lock without a readable PID counts as alive (it is being written)
_proc: subprocess.Popen | None = None


def lock_path() -> Path:
    return paths.home() / "refresh.lock"


def log_path() -> Path:
    return paths.home() / "refresh.log"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _proc is not None and _proc.pid == pid:
        return _proc.poll() is None  # our own child: a zombie is dead
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=10).stdout
            return str(pid) in out
        except (OSError, subprocess.SubprocessError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    try:  # a zombie (exited, not yet reaped) is not running
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def lock_pid() -> int | None:
    try:
        return int(lock_path().read_text().strip())
    except (OSError, ValueError):
        return None


def running() -> bool:
    """True when a refresh is running (lock present and its process alive)."""
    f = lock_path()
    if not f.exists():
        return False
    pid = lock_pid()
    if pid is None:
        try:
            return time.time() - f.stat().st_mtime < LEGACY_GRACE
        except OSError:
            return False
    return _pid_alive(pid)


def acquire(pid: int | None = None) -> bool:
    """Create the lock for `pid` (default: this process). Stale locks are replaced. False if a refresh runs."""
    f = lock_path()
    for _ in range(2):
        try:
            fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if running():
                return False
            f.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(str(pid or os.getpid()))
        return True
    return False


def release():
    if lock_pid() == os.getpid():
        lock_path().unlink(missing_ok=True)


def start_background() -> bool:
    """Start a detached refresh process unless one is running. Returns True if started."""
    global _proc
    if not acquire():
        return False
    log = open(log_path(), "a")
    try:
        _proc = subprocess.Popen([sys.executable, "-m", "agentloadout.refresh"], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=os.environ.copy())
    except OSError:
        lock_path().unlink(missing_ok=True)
        raise
    finally:
        log.close()
    lock_path().write_text(str(_proc.pid))
    return True


def main():
    """Worker: back up, then collect. Holds the lock (own PID) for its whole run."""
    pid = lock_pid()
    if pid not in (None, os.getpid(), os.getppid()) and _pid_alive(pid):
        print("agentloadout: refresh lain sedang berjalan", file=sys.stderr)
        return 1
    lock_path().write_text(str(os.getpid()))
    try:
        from . import backup, collector
        backup.main_args([])
        collector.main()
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"agentloadout: refresh gagal: {e}", file=sys.stderr)
        return 1
    finally:
        release()


if __name__ == "__main__":
    sys.exit(main())
