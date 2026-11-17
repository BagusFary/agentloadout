"""Interactive menu (BRIEF-05). Terminal tests run through tests/pty_driver.py (a separate process, N6)."""
import json
import subprocess
import sys
import time

import pytest

from conftest import ROOT

PIN = "24681357"
MENU = "Pilih [0-12]"


def menu_env(sb, **extra):
    env = {**sb.env, "TERM": "xterm-256color", "COLUMNS": "100", "LINES": "40", "AGENTLOADOUT_MENU_IDLE": "60",
           "NO_COLOR": "1"}
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


def run_menu(sb, answers=(), timeout=60, cols=100, **extra):
    env = menu_env(sb, **extra)
    env["COLUMNS"] = str(cols)
    return sb.pty_run(answers=answers, env=env, timeout=timeout, cols=cols)


# ------------------------------------------------------------------ when the menu does NOT appear
def test_without_a_terminal_nothing_waits(sb):
    t = time.time()
    r = sb.cli()
    assert r.returncode == 2 and "required: cmd" in r.stderr and "AGENT" not in r.stdout
    assert time.time() - t < 2
    p = subprocess.Popen([sys.executable, "-m", "agentloadout.cli"], env=sb.env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # stdin open, no data
    try:
        out, err = p.communicate(timeout=5)
    finally:
        p.stdin and p.stdin.close()
    assert p.returncode == 2 and "#####" not in out
    m = sb.cli("menu")
    assert m.returncode == 2 and "tidak tersedia" in m.stderr


def test_stdout_to_a_pipe_means_no_menu(sb):
    res = sb.pty_run(env=menu_env(sb), stdout_pipe=True, timeout=10)  # stdin is a terminal, stdout is not
    assert res["rc"] == 2 and "#####" not in res["out"] + res["pipe_out"] and "Pilih" not in res["out"]


@pytest.mark.parametrize("name,value", [
    ("CLAUDECODE", "1"), ("CODEX_THREAD_ID", "x"), ("OPENBOT_AGENT_PROCESS", "1"), ("AI_AGENT", "x"),
    ("GEMINI_CLI", "1"), ("GEMINI_CLI_IN_SHELL", "1"), ("OPENCODE", "1"), ("CURSOR_AGENT", "1"), ("AGENT", "1"),
    ("CI", "true"), ("TERM", "dumb"), ("AGENTLOADOUT_NO_MENU", "1")])
def test_agents_and_automation_never_get_the_menu(sb, name, value):
    res = run_menu(sb, timeout=10, **{name: value})
    assert not res["timed_out"] and res["rc"] == 2, res
    assert "#####" not in res["out"] and "Pilih" not in res["out"]


def test_open_stdin_without_input_does_not_hang(sb):
    """An agent driving a pseudo-terminal whose input stays open but empty (H7b): the idle limit closes it."""
    res = run_menu(sb, timeout=30, AGENTLOADOUT_MENU_IDLE="2")
    assert not res["timed_out"] and res["rc"] == 0 and "menu ditutup" in res["out"] and res["elapsed"] < 15


def test_mcp_stays_clean(sb):
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    p = subprocess.Popen([sys.executable, "-m", "agentloadout.cli", "mcp"], env=menu_env(sb), stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        p.stdin.write(init + "\n")
        p.stdin.flush()
        first = p.stdout.readline()
    finally:
        p.kill()
        p.wait()
    assert json.loads(first)["jsonrpc"] == "2.0"
    res = sb.pty_run("mcp", answers=[("", init)], env=menu_env(sb), timeout=8)  # on a terminal too
    assert '"jsonrpc":"2.0","id":1,"result"' in res["out"].replace(" ", "")
    assert "#####" not in res["out"] and "Sehari-hari" not in res["out"]


def test_menu_module_is_quiet_and_not_loaded_for_mcp(sb):
    r = sb.py("import sys, io\nbuf = io.StringIO(); old = sys.stdout; sys.stdout = buf\n"
              "import agentloadout.menu\nsys.stdout = old\nprint(repr(buf.getvalue()))")
    assert r.stdout.strip() == "''"
    r = sb.py("import sys\nfrom agentloadout import cli, mcp_server\nmcp_server.main = lambda: None\n"
              "ap = cli.build_parser()\ncli.run(ap.parse_args(['mcp']), ap)\n"
              "print('agentloadout.menu' in sys.modules)")
    assert r.stdout.strip().splitlines()[-1] == "False"


# ------------------------------------------------------------------ the menu itself (terminal)
def test_menu_shows_banner_status_and_exits_cleanly(sb):
    res = run_menu(sb, answers=[(MENU, "0")])
    out = res["out"]
    assert res["rc"] == 0 and "Keluar." in out and "Traceback" not in out
    assert "#   #  ###  ##### #   #   #   #####" in out and "Sehari-hari" in out and "Instalasi & perawatan" in out
    from agentloadout import __version__
    assert f"v{__version__}" in out and "katalog belum ada" in out
    assert not (sb.al / "catalog.db").exists()  # the status line never creates the catalog (H12)
    assert all(ord(c) < 0x80 for c in out), "menu output must be plain ASCII"
    assert "\x1b[" not in out  # NO_COLOR


@pytest.mark.parametrize("keys", ["^C", "^D"], ids=["ctrl-c", "eof"])
def test_ctrl_c_and_eof_at_the_main_prompt(sb, keys):
    res = run_menu(sb, answers=[(MENU, keys)])
    assert res["rc"] == 0 and "Keluar." in res["out"] and "Traceback" not in res["out"]


def test_ctrl_c_inside_a_choice_returns_to_the_menu(sb):
    res = run_menu(sb, answers=[(MENU, "5"), ("Pilih [1-3]", "^C"), (MENU, "0")])
    out = res["out"]
    assert res["rc"] == 0 and "Dibatalkan, kembali ke menu." in out and out.count(MENU) >= 2 and "Traceback" not in out


def test_colour_only_for_the_banner_and_only_when_allowed(sb):
    res = run_menu(sb, answers=[(MENU, "0")], NO_COLOR=None)
    assert "\x1b[1;36m" in res["out"]
    assert all(ord(c) < 0x80 for c in res["out"])


@pytest.mark.parametrize("cols,marker", [(100, " ###   #### ##### #   # ##### #      ###"),
                                         (60, "#      ###   ###  ####   ###  #   # #####"),
                                         (30, "== AGENTLOADOUT ==")])
def test_banner_fits_the_terminal(sb, cols, marker):
    res = run_menu(sb, answers=[(MENU, "0")], cols=cols)
    assert marker in res["out"]
    for line in res["out"].replace("\r", "").split("\n"):
        assert len(line) <= cols, (cols, line)


def test_status_from_a_real_catalog_without_network(sb):
    sb.catalog()
    sb.set_pin(PIN)
    res = run_menu(sb, answers=[(MENU, "4"), ("Enter untuk kembali", ""), (MENU, "0")])
    out = res["out"]
    assert "katalog 4 item" in out and "PIN [x]" in out and "data    :" in out and res["rc"] == 0


# ------------------------------------------------------------------ approvals keep every check
def test_approve_from_the_menu_needs_the_pin(sb):
    sb.catalog()
    (sb.home / ".cursor").mkdir()
    pid = sb.plan()
    flow = [(MENU, "1"), ("Nomor rencana", "1"), ("[s]etujui", "s"), ("Enter untuk kembali", ""), (MENU, "0")]
    res = run_menu(sb, answers=flow)  # no PIN set yet
    assert "set-pin" in res["out"] and sb.plan_json(pid)["status"] == "pending"
    sb.set_pin(PIN)
    flow = [(MENU, "1"), ("Nomor rencana", "1"), ("[s]etujui", "s"), ("PIN persetujuan", "11112222"),
            ("Enter untuk kembali", ""), (MENU, "0")]
    res = run_menu(sb, answers=flow)
    assert "PIN salah" in res["out"] and sb.plan_json(pid)["status"] == "pending"
    flow = [(MENU, "1"), ("Nomor rencana", "1"), ("[s]etujui", "s"), ("PIN persetujuan", PIN),
            ("Enter untuk kembali", ""), (MENU, "0")]
    res = run_menu(sb, answers=flow)
    p = sb.plan_json(pid)
    assert p["status"] == "approved" and p["approved_by"] == "pin:cli" and "pin_gen" in p, res["out"]
    assert PIN not in res["out"]


def test_menu_respects_agent_marker_even_for_set_pin(sb):
    """A marker set after the menu started still blocks set-pin: the menu only calls the normal command."""
    r = sb.pty("set-pin", extra_env={"GEMINI_CLI": "1"}, answers=[("PIN baru", PIN), ("Ulangi", PIN)])
    assert r[0] == 2 and "ditolak" in r[1]
    r = sb.pty("set-pin", extra_env={"AGENT": "1", "TERM": "xterm"}, answers=[("PIN baru", PIN), ("Ulangi", PIN)])
    assert r[0] == 0  # AGENT alone is too generic to block set-pin (it only keeps the menu away)


# ------------------------------------------------------------------ S21: Ctrl-C at existing prompts
def test_ctrl_c_at_questions_has_no_traceback(sb):
    sb.catalog()
    (sb.home / ".cursor").mkdir()
    rc, out = sb.pty("setup", "--to", "cursor", answers=[("Lanjutkan?", "^C")])
    assert "Traceback" not in out and rc == 1 and "Dibatalkan" in out
    sb.set_pin(PIN)
    pid = sb.plan()
    rc, out = sb.pty("approve", pid, answers=[("PIN persetujuan", "^C")])
    assert "Traceback" not in out and rc == 130 and sb.plan_json(pid)["status"] == "pending"


def test_ctrl_c_in_a_long_command_is_clean(sb):
    sb.catalog()
    res = sb.pty_run("ui", env=menu_env(sb), answers=[("Agent Loadout di http", "^C")], timeout=20)
    assert res["rc"] == 130 and "Dibatalkan." in res["out"] and "Traceback" not in res["out"]
    assert not (sb.al / "ui.json").exists()


# ------------------------------------------------------------------ each choice calls the right command (in-process)
@pytest.fixture
def m(tmp_path, monkeypatch):
    from conftest import make_catalog
    monkeypatch.setenv("AGENTLOADOUT_HOME", str(tmp_path / "al"))
    (tmp_path / "al").mkdir()
    make_catalog(tmp_path / "al" / "catalog.db")
    from agentloadout import menu
    calls, feed = [], []
    monkeypatch.setattr(menu, "run_cmd", lambda *argv: calls.append(list(argv)) or 0)

    def fake_read(prompt):
        if not feed:
            raise EOFError()
        return feed.pop(0)

    monkeypatch.setattr(menu, "read_line", fake_read)
    menu.calls, menu.feed = calls, feed
    return menu


@pytest.mark.parametrize("handler,answers,expected", [
    ("h_status", [], [["where"]]),
    ("h_token", ["1"], [["set-token"]]),
    ("h_token", ["2"], [["set-token", "--test"]]),
    ("h_token", ["3"], [["set-token", "--clear"]]),
    ("h_token", [""], []),
    ("h_pin", ["1"], [["set-pin"]]),
    ("h_pin", ["2"], [["set-pin", "--reset"]]),
    ("h_password", ["1"], [["set-password"]]),
    ("h_password", ["2"], [["set-password", "--reset"]]),
    ("h_password", ["3"], [["set-password", "--off"]]),
    ("h_setup", ["cursor,codex"], [["setup", "--to", "cursor,codex"]]),
    ("h_setup", [""], [["setup"]]),
    ("h_setup", ["-"], []),
    ("h_backup", ["1"], [["backup"]]),
    ("h_check_updates", [], [["check-updates"]]),
    ("h_uninstall", ["LEPAS"], [["uninstall", "--yes"]]),
    ("h_uninstall", ["lepas"], []),
])
def test_each_choice_runs_the_matching_command(m, handler, answers, expected):
    m.feed += answers
    getattr(m, handler)()
    assert m.calls == expected


def test_plans_choice_approves_or_rejects_through_the_cli(m, tmp_path):
    from agentloadout import plans
    pid = plans.create("acme/demo-mcp", ["cursor"])["id"]
    m.feed += ["1", "s"]
    m.h_plans()
    assert m.calls == [["approve", pid]]
    m.calls.clear()
    m.feed += ["1", "t", "y"]
    m.h_plans()
    assert m.calls == [["reject", pid]]


def test_refresh_and_web_run_in_the_background(m, monkeypatch):
    from agentloadout import refresh
    from agentloadout.web import server
    started = []
    monkeypatch.setattr(refresh, "running", lambda: False)
    monkeypatch.setattr(refresh, "start_background", lambda source="mcp": started.append(source) or True)
    m.h_refresh()
    assert started == ["menu"]
    monkeypatch.setattr(server, "ensure_ui", lambda wait=10.0: {"host": "127.0.0.1", "port": 18999})
    m.feed += ["n"]
    m.h_web()
    assert m.calls == []


def test_upgrade_choice_exits_the_menu_after_success(m, monkeypatch):
    from agentloadout import selfupdate
    monkeypatch.setattr(selfupdate, "read_cache", lambda: {"newer": True, "latest": "9.9.9"})
    monkeypatch.setattr(selfupdate, "disk_version", lambda: "9.9.9")
    m.feed += ["y"]
    assert m.h_upgrade() == m.EXIT
    assert m.calls == [["upgrade", "--check"], ["upgrade", "--yes"]]  # newest only: no --to, no --pre
    m.calls.clear()
    m.feed += ["9"]
    monkeypatch.setattr(m, "handlers", lambda: {"9": lambda: m.EXIT})
    assert m.run_menu() == 0 and m.feed == []  # left right after the upgrade, no further prompt


def test_upgrade_choice_stays_when_nothing_changed(m, monkeypatch):
    from agentloadout import selfupdate
    monkeypatch.setattr(selfupdate, "read_cache", lambda: {"newer": True, "latest": "9.9.9"})
    monkeypatch.setattr(selfupdate, "disk_version", lambda: m.__version__)
    m.feed += ["y"]
    assert m.h_upgrade() is None


def test_status_needs_no_network_and_creates_nothing(tmp_path, monkeypatch):
    import socket
    monkeypatch.setenv("AGENTLOADOUT_HOME", str(tmp_path / "fresh"))

    def no_net(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    from agentloadout import menu
    text = menu.render(100)
    assert "katalog belum ada" in text
    assert not list((tmp_path / "fresh").rglob("*.db"))


@pytest.mark.parametrize("w", [100, 72, 71, 60, 42, 41, 30, 20])
def test_render_width_and_ascii(w, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOADOUT_HOME", str(tmp_path / "al"))
    monkeypatch.setenv("NO_COLOR", "1")
    from agentloadout import menu
    text = menu.render(w)
    assert all(len(line) <= w for line in text.split("\n"))
    assert all(ord(c) < 0x80 for c in text)
    assert all(ord(c) < 0x80 for line in menu.BANNER for c in line) and max(len(x.rstrip()) for x in menu.BANNER) == 71


def test_menu_requires_every_condition():
    from agentloadout import menu

    class T:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty

    yes, no = T(True), T(False)
    base = {"TERM": "xterm"}
    assert menu.not_interactive_reason(base, yes, yes) is None
    assert menu.not_interactive_reason(base, no, yes) and menu.not_interactive_reason(base, yes, no)
    for extra in ({"CI": "1"}, {"TERM": "dumb"}, {"AGENTLOADOUT_NO_MENU": "1"}, {"AGENT": "1"}, {"GEMINI_CLI": "1"}):
        assert menu.not_interactive_reason({**base, **extra}, yes, yes), extra


# ------------------------------------------------------------------ N7: no token-like literals in the repository
def test_no_token_literals_in_the_repository():
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                           text=True).stdout.split()
    import re
    pat = re.compile(r"(gh[pousr]|github_pat)_[A-Za-z0-9_]{8,}")
    hits = []
    for f in files:
        p = ROOT / f
        if p.is_file() and not f.startswith("data/") and p.suffix not in (".db", ".png", ".ico"):
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{f}:{n}")
    assert hits == []


# ------------------------------------------------------------------ review fixes: W5 (restore exit code), S22 (no folders)
FAKE_BROKEN_SQLITE = """
import sqlite3
class _Con:
    def execute(self, q):
        class R:
            def fetchone(self_inner):
                return ("rusak: halaman 2",) if "integrity_check" in q else (0,)
        return R()
    def close(self):
        pass
sqlite3.connect = lambda *a, **k: _Con()   # every backup now looks damaged
"""


def test_restore_module_and_script_exit_1_for_a_broken_backup(sb, tmp_path):
    backup = tmp_path / "rusak.db"
    backup.write_text("x")
    target = tmp_path / "target.db"
    env = {**sb.env, "AGENTLOADOUT_BACKUP_DIR": str(tmp_path / "bk")}
    code = FAKE_BROKEN_SQLITE + ("import runpy, sys\nsys.argv = ['restore', %r, '--target', %r]\n"
                                 "runpy.run_module('agentloadout.restore', run_name='__main__', alter_sys=True)") % (str(backup), str(target))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "cadangan rusak" in r.stderr, (r.returncode, r.stderr)
    # the owner's scripts/restore.py wrapper (copied to /tmp, its src/ pointing at this repo)
    inst = tmp_path / "instance"
    (inst / "scripts").mkdir(parents=True)
    import shutil
    shutil.copy2(ROOT / "scripts" / "restore.py", inst / "scripts" / "restore.py")
    (inst / "src").symlink_to(ROOT / "src")
    code = FAKE_BROKEN_SQLITE + ("import runpy, sys\nsys.argv = [%r, %r, '--target', %r]\nrunpy.run_path(%r, run_name='__main__')"
                                 % (str(inst / "scripts" / "restore.py"), str(backup), str(target), str(inst / "scripts" / "restore.py")))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "cadangan rusak" in r.stderr, (r.returncode, r.stderr)
    assert not target.exists()
    cli = sb.cli("restore", str(backup), extra_env={"AGENTLOADOUT_BACKUP_DIR": str(tmp_path / "bk")})
    assert cli.returncode == 1  # the CLI path keeps its code too (a real non-database file here)


def test_menu_on_an_empty_home_creates_no_folder(sb):
    assert not sb.al.exists()
    res = run_menu(sb, answers=[(MENU, "0")])
    assert res["rc"] == 0 and "belum ada folder data" in res["out"]
    assert not sb.al.exists(), sorted(str(p) for p in sb.al.rglob("*"))


def test_render_on_an_empty_home_creates_no_folder(tmp_path, monkeypatch):
    fresh = tmp_path / "nothing-here"
    monkeypatch.setenv("AGENTLOADOUT_HOME", str(fresh))
    from agentloadout import menu
    text = menu.render(100)
    assert "rencana menunggu 0" in text and "katalog belum ada" in text
    assert not fresh.exists()
