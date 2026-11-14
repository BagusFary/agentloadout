"""Shared test helpers.

Every test runs in a throwaway HOME under the system temp folder: HOME, AGENTLOADOUT_HOME, UV_TOOL_DIR,
UV_TOOL_BIN_DIR and UV_CACHE_DIR point there, XDG_* / CODEX_HOME / agent markers are removed, and PATH
holds only uv, uvx, git and the system folders, so no real harness CLI (claude, codex, ...) or harness
config is touched. The web UI is started on a free port, never the default 8787.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pty
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_MARKERS = ("CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID")
_SCRUB_PREFIXES = ("XDG_", "UV_", "CLAUDE_", "CODEX_", "AGENTLOADOUT_", "GITHUB_TOKEN", "VIRTUAL_ENV")
_SCRUB = set(AGENT_MARKERS) | {"CLAUDECODE", "AI_AGENT", "CODEX_HOME"}

# isolate this pytest process itself before any agentloadout module is imported
_SESSION = Path(tempfile.mkdtemp(prefix="al-pytest-"))
for _k in list(os.environ):
    if _k in _SCRUB or _k.startswith(_SCRUB_PREFIXES):
        os.environ.pop(_k)
os.environ["HOME"] = str(_SESSION / "home")
os.environ["AGENTLOADOUT_HOME"] = str(_SESSION / "al")
(_SESSION / "home").mkdir()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SESSION, ignore_errors=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert port != 8787
    return port


def _safe_path(root: Path) -> str:
    """PATH with uv, uvx and git only (plus /usr/bin:/bin), so real harness CLIs are never found."""
    bindir = root / "pathbin"
    bindir.mkdir(exist_ok=True)
    for tool in ("uv", "uvx", "git"):
        src = shutil.which(tool)
        if src and not (bindir / tool).exists():
            (bindir / tool).symlink_to(src)
    return f"{bindir}:/usr/bin:/bin"


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.al = root / "al"
        self.home.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        env = {k: v for k, v in os.environ.items() if k not in _SCRUB and not k.startswith(_SCRUB_PREFIXES)}
        env.update(HOME=str(self.home), AGENTLOADOUT_HOME=str(self.al), AGENTLOADOUT_PORT=str(self.port),
                   UV_TOOL_DIR=str(root / "uv" / "tools"), UV_TOOL_BIN_DIR=str(root / "uv" / "bin"),
                   UV_CACHE_DIR=str(root / "uv" / "cache"), UV_PYTHON_DOWNLOADS="never",
                   PATH=_safe_path(root), PYTHONUNBUFFERED="1", LANG="C.UTF-8")
        self.env = env

    # ---------------------------------------------------------------- runners
    def cli(self, *args, stdin=subprocess.DEVNULL, extra_env=None, exe=None, timeout=120):
        cmd = [exe] if exe else [sys.executable, "-m", "agentloadout.cli"]
        return subprocess.run([*cmd, *args], env={**self.env, **(extra_env or {})}, stdin=stdin,
                              capture_output=True, text=True, timeout=timeout)

    def py(self, code: str, extra_env=None, check=True):
        r = subprocess.run([sys.executable, "-c", code], env={**self.env, **(extra_env or {})},
                           capture_output=True, text=True, timeout=120)
        if check and r.returncode != 0:
            raise AssertionError(f"python failed: {r.stderr}")
        return r

    def pty(self, *args, answers=(), extra_env=None, timeout=60):
        """Run the CLI on a pseudo-terminal; answers = [(prompt substring, text to type), ...]."""
        argv = [sys.executable, "-m", "agentloadout.cli", *args]
        env = {**self.env, **(extra_env or {})}
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        out, pos, i, deadline = b"", 0, 0, time.time() + timeout
        answers = list(answers)
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            if i < len(answers) and answers[i][0].encode() in out[pos:]:
                pos = len(out)
                os.write(fd, (answers[i][1] + "\n").encode())
                i += 1
        else:
            os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        os.close(fd)
        return os.waitstatus_to_exitcode(status), out.decode(errors="replace")

    # ---------------------------------------------------------------- data
    def catalog(self) -> Path:
        self.al.mkdir(parents=True, exist_ok=True)
        dest = self.al / "catalog.db"
        make_catalog(dest)
        return dest

    def plan(self, item="acme/demo-mcp", targets=("cursor",)) -> str:
        r = self.py(f"from agentloadout import plans; print(plans.create({item!r}, {list(targets)!r})['id'])")
        return r.stdout.strip()

    def plan_json(self, pid) -> dict:
        return json.loads((self.al / "plans" / f"{pid}.json").read_text())

    def set_pin(self, pin_value: str):
        self.py(f"from agentloadout import pin; pin.set_pin({pin_value!r})")


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


def make_catalog(path: Path):
    """A tiny catalog: no network, no refresh (last_run is now)."""
    sys.path.insert(0, str(ROOT / "src"))
    from agentloadout import db
    con = db.connect(path)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def mcp(name, pkg):
        return json.dumps([{"client": "JSON (mcpServers)", "lang": "json",
                            "snippet": json.dumps({"mcpServers": {name: {"command": "npx", "args": ["-y", pkg]}}})}])

    rows = [
        ("acme/demo-mcp", "mcp", "demo-mcp", "Demo MCP server for tests", mcp("demo", "demo-mcp@1.0.0"), "[]", 1),
        ("acme/postgres-mcp", "mcp", "postgres-mcp", "Postgres MCP server", mcp("postgres", "pg-mcp@2.0.0"), "[]", 1),
        ("acme/demo-skills", "skill", "demo-skills", "Agent skills", "[]", '["skills/demo"]', 1),
        ("acme/unchecked-skills", "skill", "unchecked-skills", "Not examined yet", "[]", "[]", 0),
    ]
    for id_, kind, name, desc, install, skill_paths, checked in rows:
        con.execute("""INSERT OR REPLACE INTO items(id, kind, name, full_name, url, description, topics, stars, created_at,
                       pushed_at, install, env_vars, risks, skill_paths, momentum, sources, first_seen, updated_at, checked)
                       VALUES(?,?,?,?,?,?,'[]',100,?,?,?,'[]','[]',?,1.0,'[]',?,?,?)""",
                    (id_, kind, name, id_, f"https://github.com/{id_}", desc, now, now, install, skill_paths, now, now, checked))
    db.set_meta(con, "last_run", {"at": now, "seconds": 1, "counts": {}})
    con.commit()
    con.close()
