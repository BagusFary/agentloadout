"""Busy ports and instance identity (BRIEF-03 H7, K7)."""
import json
import os
import signal
import socket
import time

from conftest import Sandbox


def write_port(sb, port):
    sb.al.mkdir(parents=True, exist_ok=True)
    (sb.al / "config.toml").write_text(f"[web]\nport = {port}\n")


def test_health_reports_the_instance_and_ui_json_is_written(sb):
    web = sb.ui()
    iid = (sb.al / "instance_id").read_text().strip()
    from agentloadout import __version__
    assert web.request("GET", "/api/health")["json"] == {"ok": True, "instance": iid, "version": __version__}
    info = json.loads((sb.al / "ui.json").read_text())
    assert info["port"] == web.port and info["pid"] == web.proc.pid and info["instance"] == iid
    web.stop()
    assert not (sb.al / "ui.json").exists()  # removed on SIGTERM


def test_busy_port_moves_unless_explicit(sb, tmp_path):
    a = sb.ui()
    other = Sandbox(tmp_path / "b")
    write_port(other, a.port)
    b = other.ui(drop=("AGENTLOADOUT_PORT",))
    assert b.port and a.port < b.port <= a.port + 10, b.output()
    assert "pindah" in b.output()
    assert b.request("GET", "/api/health")["json"]["instance"] != a.request("GET", "/api/health")["json"]["instance"]

    explicit = other.cli("ui", "--port", str(a.port), timeout=30)
    assert explicit.returncode == 2 and "instance Agent Loadout lain" in explicit.stderr and "--port" in explicit.stderr
    env_explicit = other.cli("ui", extra_env={"AGENTLOADOUT_PORT": str(a.port)}, timeout=30)
    assert env_explicit.returncode == 2


def test_same_instance_twice_reports_the_running_one(sb):
    a = sb.ui()
    again = sb.cli("ui", timeout=30)
    assert again.returncode == 0 and f":{a.port}" in again.stdout and "sudah berjalan" in again.stdout


def test_port_held_by_another_program(sb):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        r = sb.cli("ui", "--port", str(port), timeout=30)
        assert r.returncode == 2 and "program lain" in r.stderr
    finally:
        s.close()


def test_open_dashboard_never_returns_a_foreign_instance(sb, tmp_path):
    a = sb.ui()
    other = Sandbox(tmp_path / "b")
    other.catalog()
    write_port(other, a.port)
    env = {k: v for k, v in other.env.items() if k != "AGENTLOADOUT_PORT"}
    import subprocess
    import sys
    code = "import json\nfrom agentloadout import mcp_server as m\nprint(json.dumps(m.open_dashboard()))"
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    try:
        assert out["running"] is True and out["url"] != f"http://127.0.0.1:{a.port}/", out
        info = json.loads((other.al / "ui.json").read_text())
        assert out["url"] == f"http://127.0.0.1:{info['port']}/"
        again = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        assert json.loads(again.stdout.strip().splitlines()[-1])["url"] == out["url"]  # reused, not a second UI
    finally:
        info = json.loads((other.al / "ui.json").read_text())
        os.kill(info["pid"], signal.SIGTERM)
        for _ in range(50):
            if not (other.al / "ui.json").exists():
                break
            time.sleep(0.1)


def test_public_health_hides_the_version(sb):
    """S17: outside the laptop /api/health only says ok + instance (open_dashboard needs no more)."""
    sb.catalog()
    web = sb.ui()
    ext = web.request("GET", "/api/health", headers={"X-Forwarded-For": "1.2.3.4"})["json"]
    assert set(ext) == {"ok", "instance"}
    funnel = web.request("GET", "/api/health", headers={"X-Forwarded-For": "1.2.3.4", "Tailscale-Funnel-Request": "?1"})["json"]
    assert set(funnel) == {"ok", "instance"}
    assert "version" in web.request("GET", "/api/health")["json"]
    sb.set_password("kata-sandi-uji-12")
    _, cookie = web.login("kata-sandi-uji-12", headers={"X-Forwarded-For": "1.2.3.4"})
    assert "version" in web.request("GET", "/api/health", headers={"X-Forwarded-For": "1.2.3.4"}, cookie=cookie)["json"]
    from agentloadout.web import server
    assert server.instance_at("127.0.0.1", web.port) == (sb.al / "instance_id").read_text().strip()
