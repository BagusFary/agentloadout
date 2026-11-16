"""Catalog refresh in its own process, guarded by a kernel file lock.

The MCP server starts `python -m agentloadout.refresh` in a new session, so the refresh keeps running when
the harness stops the MCP server. Whoever refreshes (that worker or `agentloadout refresh`) holds an
exclusive lock on <home>/refresh.lock for the whole run. The kernel drops the lock when the process dies,
so a crashed refresh never blocks the next one and a reused PID means nothing. The PID is only written
to <home>/refresh.pid as information.

start_background() takes the lock *before* it starts the worker and hands the locked file to it, so two
concurrent callers cannot both start one. The daily routine (scripts/collect.py) takes the same lock.

Every run records where it came from (source: mcp | web | cli | routine) and when it started in the
`progress` meta and in <home>/refresh.state.json; a failure is stored as `last_error` (sanitized: no
token, no home path). Whether a refresh is *running* is decided by the lock, never by `progress`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import signal
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


def state_path() -> Path:
    return paths.home() / "refresh.state.json"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_state(**kw):
    st = {**read_state(), **kw}
    paths.write_private(state_path(), json.dumps(st))


_TOKEN_PATTERNS = re.compile(r"(github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]{10,}|Bearer\s+\S+)")


def sanitize(text: str) -> str:
    """Short error text that is safe to show: no token, no home folder path."""
    text = str(text or "")
    try:
        from . import config
        tok = config.github_token()
        if tok:
            text = text.replace(tok, "***")
    except Exception:  # noqa: BLE001
        pass
    text = _TOKEN_PATTERNS.sub("***", text)
    home = os.path.expanduser("~")
    if home and home != "/":
        text = text.replace(home, "~")
    return " ".join(text.split())[:300]


def record_error(message: str):
    """Store last_error in the catalog meta (for /api/status) and the state file."""
    err = {"at": now_iso(), "message": sanitize(message), "source": os.environ.get("AGENTLOADOUT_REFRESH_SOURCE", "cli")}
    try:
        from . import db
        con = db.connect()
        prog = db.get_meta(con, "progress") or {}
        prog.update(running=False, label="Gagal: " + err["message"][:120], at=err["at"])
        db.set_meta(con, "progress", prog)
        db.set_meta(con, "last_error", err)
        con.commit()
        con.close()
    except Exception:  # noqa: BLE001
        pass
    _write_state(last_error=err)


def _clear_last_error():
    try:
        from . import db
        con = db.connect()
        db.set_meta(con, "last_error", None)
        con.commit()
        con.close()
    except Exception:  # noqa: BLE001
        pass


def write_progress(**fields):
    from . import db
    con = db.connect()
    db.set_meta(con, "progress", {"running": True, "steps": 4, "at": now_iso(), **fields})
    con.commit()
    con.close()


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


def start_background(source: str = "mcp") -> bool:
    """Start a detached refresh process unless one is running. Returns True if this call started it."""
    fd = try_lock()
    if fd is None:
        return False
    try:
        _write_state(started_at=now_iso(), source=source)
        env = {**os.environ, "AGENTLOADOUT_REFRESH_SOURCE": source}
        with open(log_path(), "a") as log:
            kw = {"pass_fds": (fd,)} if os.name != "nt" else {}
            proc = subprocess.Popen([*WORKER, "--lock-fd", str(fd)] if os.name != "nt" else WORKER,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, env=env, **kw)
        _write_pid(proc.pid)
    finally:
        os.close(fd)  # the worker holds the same open file, so the lock stays until it exits
    return True


def _on_term(signum, frame):
    raise SystemExit(f"dihentikan oleh sinyal {signum}")


def run_locked(fd: int | None = None, source: str | None = None, backup: bool = True) -> int:
    """Back up (optional) and collect while holding the lock (`fd` = lock inherited from start_background).
    Used by the background worker, `agentloadout refresh` and the daily routine (scripts/collect.py)."""
    if fd is None:
        fd = try_lock()
        if fd is None:
            print("agentloadout: refresh lain sedang berjalan; coba lagi nanti.", file=sys.stderr)
            return 1
    source = source or os.environ.get("AGENTLOADOUT_REFRESH_SOURCE") or "cli"
    started = now_iso()
    os.environ["AGENTLOADOUT_REFRESH_SOURCE"] = source
    os.environ["AGENTLOADOUT_REFRESH_STARTED"] = started
    _write_pid(os.getpid())
    _write_state(started_at=started, source=source, pid=os.getpid(), finished_at=None, last_error=None)
    _clear_last_error()  # an error of an earlier run must not look like this run's (e.g. after kill -9)
    old_term = signal.signal(signal.SIGTERM, _on_term) if os.name != "nt" else None
    try:
        # always a fresh progress entry: a run killed before it writes anything shows as stopped, not as the old run
        write_progress(step=0, label="Mencadangkan katalog" if backup else "Memulai pembaruan", started_at=started,
                       source=source)
        if backup:
            from . import backup as bk
            bk.main_args([])
        from . import collector
        collector.main()
        _write_state(finished_at=now_iso(), ok=True)
        try:  # newer Agent Loadout release? at most once per 24 h, silent on failure (cache read by web/where/MCP)
            from . import selfupdate
            selfupdate.check()
        except Exception:  # noqa: BLE001
            pass
        return 0
    except BaseException as e:  # noqa: BLE001  (SIGTERM / Ctrl-C too: record why it stopped)
        msg = str(e) or type(e).__name__
        if isinstance(e, KeyboardInterrupt):
            msg = "dihentikan (Ctrl-C)"
        record_error(msg)
        _write_state(finished_at=now_iso(), ok=False)
        print(f"agentloadout: refresh gagal: {sanitize(msg)}", file=sys.stderr)
        return 1
    finally:
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)
        pid_path().unlink(missing_ok=True)
        os.close(fd)


def cooldown_left(seconds: int) -> int:
    """Seconds until another run may start, counted from the last start of any source."""
    started = read_state().get("started_at")
    if not started:
        return 0
    try:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return 0
    return max(int(seconds - age), 0)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    fd = int(argv[1]) if len(argv) == 2 and argv[0] == "--lock-fd" else None
    return run_locked(fd)


if __name__ == "__main__":
    sys.exit(main())
