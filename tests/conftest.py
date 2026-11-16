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
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_MARKERS = ("CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                 "OPENBOT_AGENT_PROCESS", "GEMINI_CLI", "GEMINI_CLI_IN_SHELL", "OPENCODE", "CURSOR_AGENT")
_SCRUB_PREFIXES = ("XDG_", "UV_", "CLAUDE_", "CODEX_", "OPENBOT_", "GEMINI_", "OPENCODE", "CURSOR_",
                   "AGENTLOADOUT_", "GITHUB_TOKEN", "VIRTUAL_ENV")
_SCRUB = set(AGENT_MARKERS) | {"CLAUDECODE", "AI_AGENT", "CODEX_HOME", "AGENT", "CI", "NO_COLOR"}

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


def fake_token(body: str, kind: str = "classic") -> str:
    """A dummy GitHub token built at runtime (N7): no token-like literal ever sits in the repository."""
    prefix = {"classic": "gh" + "p_", "fine-grained": "github" + "_pat_"}[kind]
    return prefix + body


class _GitHubMock:
    """Fake api.github.com /rate_limit for token tests. Tokens containing GOOD are accepted, others get 401.
    ghp_ tokens with SCOPE report scopes; github_pat_ tokens report an expiry (ODDEXP: an unknown format)."""

    def __init__(self):
        import http.server
        import threading
        mock = self
        self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                auth = self.headers.get("Authorization", "")
                tok = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
                mock.seen.append(tok[-4:] if tok else "")
                if tok and "GOOD" not in tok:
                    body, code, hdrs = b'{"message":"Bad credentials"}', 401, {}
                else:
                    limit = 5000 if tok else 60
                    body = json.dumps({"resources": {"core": {"limit": limit, "remaining": limit - 1, "reset": 2000000000, "used": 1},
                                                     "search": {"limit": 30 if tok else 10, "remaining": 9, "reset": 2000000000, "used": 1}}}).encode()
                    code, hdrs = 200, {}
                    if tok.startswith("ghp_"):
                        hdrs["X-OAuth-Scopes"] = "repo, gist" if "SCOPE" in tok else ""
                    if tok.startswith("github_pat_"):
                        hdrs["GitHub-Authentication-Token-Expiration"] = "kira-kira Februari" if "ODDEXP" in tok else "2027-02-14 10:00:00 UTC"
                self.send_response(code)
                for k, v in hdrs.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


GITHUB = _GitHubMock()
os.environ["AGENTLOADOUT_GITHUB_API"] = GITHUB.url  # this pytest process never talks to the real GitHub


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.al = root / "al"
        self.home.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        env = {k: v for k, v in os.environ.items() if k not in _SCRUB and not k.startswith(_SCRUB_PREFIXES)}
        env.update(HOME=str(self.home), AGENTLOADOUT_HOME=str(self.al), AGENTLOADOUT_PORT=str(self.port),
                   AGENTLOADOUT_GITHUB_API=GITHUB.url,
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

    def pty_run(self, *args, answers=(), extra_env=None, timeout=60, exe=None, cols=80, rows=24,
                stdout_pipe=False, env=None) -> dict:
        """Run the CLI on a pseudo-terminal through tests/pty_driver.py (a separate process, N6)."""
        argv = [exe, *args] if exe else [sys.executable, "-m", "agentloadout.cli", *args]
        spec = {"argv": argv, "env": env if env is not None else {**self.env, **(extra_env or {})},
                "answers": [list(a) for a in answers], "timeout": timeout, "cols": cols, "rows": rows,
                "stdout_pipe": stdout_pipe}
        r = subprocess.run([sys.executable, str(ROOT / "tests" / "pty_driver.py")], input=json.dumps(spec),
                           capture_output=True, text=True, timeout=timeout + 30)
        if r.returncode != 0:
            raise AssertionError(f"pty driver failed: {r.stderr[-2000:]}")
        return json.loads(r.stdout)

    def pty(self, *args, answers=(), extra_env=None, timeout=60, exe=None):
        """Run the CLI on a pseudo-terminal; answers = [(prompt substring, text to type), ...] -> (rc, output)."""
        res = self.pty_run(*args, answers=answers, extra_env=extra_env, timeout=timeout, exe=exe)
        return (res["rc"] if res["rc"] is not None else -9), res["out"]

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

    def approve(self, pid, pin_value="13572468"):
        """Test stand-in for the human: approve with the PIN through the Python API (sets the PIN if needed)."""
        self.py("from agentloadout import pin, plans\n"
                f"if not pin.is_set(): pin.set_pin({pin_value!r})\n"
                f"plans.approve({pid!r}, 'pin:test', pin_gen=pin.verify({pin_value!r}))")

    def set_password(self, pw: str):
        self.py(f"from agentloadout import web_password; web_password.set_password({pw!r}, None)")

    def ui(self, *args, extra_env=None, drop=()):
        return UI(self, args, extra_env, drop)


class UI:
    """`agentloadout ui` of a sandbox, with a raw HTTP client that can send any Host / proxy header."""

    def __init__(self, sb, args=(), extra_env=None, drop=()):
        self.sb = sb
        self.log = sb.root / f"ui-{time.time_ns()}.log"
        env = {k: v for k, v in {**sb.env, **(extra_env or {})}.items() if k not in drop}
        self.proc = subprocess.Popen([sys.executable, "-m", "agentloadout.cli", "ui", *args], env=env,
                                     stdout=open(self.log, "w"), stderr=subprocess.STDOUT)
        _UIS.append(self)
        self.port = None
        for _ in range(150):
            time.sleep(0.1)
            if self.proc.poll() is not None:
                break
            try:
                info = json.loads((sb.al / "ui.json").read_text())
                if info.get("pid") == self.proc.pid:
                    self.port = info["port"]
                    break
            except (OSError, ValueError):
                pass
        assert self.port != 8787 and self.port != 8788

    def output(self) -> str:
        return self.log.read_text()

    def request(self, method, path, headers=None, body=None, cookie=None, host=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", host if host is not None else f"127.0.0.1:{self.port}")
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        data = b""
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
            h.setdefault("X-Agentloadout", "1")
        h["Content-Length"] = str(len(data))
        for k, v in h.items():
            c.putheader(k, v)
        c.endheaders(data)
        r = c.getresponse()
        raw = r.read()
        out = {"status": r.status, "headers": {k.lower(): v for k, v in r.getheaders()},
               "cookies": r.headers.get_all("Set-Cookie") or [], "text": raw.decode("utf-8", "replace")}
        try:
            out["json"] = json.loads(raw)
        except ValueError:
            out["json"] = None
        c.close()
        return out

    def login(self, password, headers=None):
        from urllib.parse import urlencode
        r = self.request("POST", "/login", headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
                         body=urlencode({"password": password}).encode())
        cookie = next((c.split(";")[0] for c in r["cookies"] if c.startswith("al_sesi_") and "Max-Age=0" not in c), None)
        return r, cookie

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


@pytest.fixture
def sb(tmp_path):
    box = Sandbox(tmp_path)
    yield box
    for ui in list(_UIS):
        ui.stop()
    _UIS.clear()


_UIS = []  # every UI started by a test; stopped when the test ends


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
