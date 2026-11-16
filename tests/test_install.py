"""Install path (H1, H2, H9), non-interactive setup/uninstall (H3) and the GitHub token (H4)."""
import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from conftest import ROOT, fake_token


# ------------------------------------------------------------------ mcp_spec (in-process, no files written)
def _fake_env(monkeypatch, exe, prefix=None):
    from agentloadout import cli
    monkeypatch.delenv("AGENTLOADOUT_MCP_COMMAND", raising=False)
    monkeypatch.setattr(sys, "executable", exe)
    monkeypatch.setattr(sys, "prefix", prefix or str(Path(exe).parent.parent))
    return cli


@pytest.mark.parametrize("exe", [
    "/x/.cache/uv/archive-v0/abc123/bin/python",
    "/x/somewhere/uvcache/archive-v0/abc123/bin/python",
    "/x/.cache/uv/builds-v0/.tmpXYZ/bin/python",
])
def test_mcp_spec_refuses_uvx_environment(monkeypatch, exe):
    cli = _fake_env(monkeypatch, exe)
    with pytest.raises(cli.SetupError, match="uv tool install"):
        cli.mcp_spec()


def test_mcp_spec_refuses_custom_uv_cache_dir(monkeypatch, tmp_path):
    cache = tmp_path / "mycache"
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    cli = _fake_env(monkeypatch, str(cache / "some-env" / "bin" / "python"))
    with pytest.raises(cli.SetupError):
        cli.mcp_spec()


def test_mcp_spec_refuses_bare_uvx_override(monkeypatch):
    from agentloadout import cli
    monkeypatch.setenv("AGENTLOADOUT_MCP_COMMAND", "uvx agentloadout mcp")
    with pytest.raises(cli.SetupError, match="--from"):
        cli.mcp_spec()


@pytest.mark.parametrize("cmd", ["uv tool run agentloadout mcp", "uv x agentloadout mcp", "/usr/bin/uvx agentloadout mcp"])
def test_mcp_spec_refuses_pypi_runner_override(monkeypatch, cmd):
    from agentloadout import cli
    monkeypatch.setenv("AGENTLOADOUT_MCP_COMMAND", cmd)
    with pytest.raises(cli.SetupError):
        cli.mcp_spec()


def test_mcp_spec_override_is_shell_split(monkeypatch):
    from agentloadout import cli
    monkeypatch.setenv("AGENTLOADOUT_MCP_COMMAND", '"/opt/my tools/agentloadout" mcp')
    assert cli.mcp_spec() == {"command": "/opt/my tools/agentloadout", "args": ["mcp"]}
    monkeypatch.setenv("AGENTLOADOUT_MCP_COMMAND", "uv tool run --from git+https://example.invalid/x agentloadout mcp")
    assert cli.mcp_spec()["command"] == "uv"


def test_mcp_spec_tool_install_uses_absolute_path(monkeypatch, tmp_path):
    tools = tmp_path / "tools"
    bindir = tools / "agentloadout" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "agentloadout").write_text("#!/bin/sh\n")
    monkeypatch.setenv("UV_TOOL_DIR", str(tools))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    cli = _fake_env(monkeypatch, str(bindir / "python"))
    spec = cli.mcp_spec()
    assert spec == {"command": str(bindir / "agentloadout"), "args": ["mcp"]}


# ------------------------------------------------------------------ non-interactive (H3)
@pytest.mark.parametrize("cmd", ["setup", "uninstall"])
def test_without_tty_and_without_yes_exits_2(sb, cmd):
    (sb.home / ".cursor").mkdir()
    r = sb.cli(cmd)
    assert r.returncode == 2, r
    assert "--yes" in r.stderr
    assert not (sb.home / ".cursor" / "mcp.json").exists()


# ------------------------------------------------------------------ token (H4) + default config
def test_token_from_env_is_saved_0600_and_never_printed(sb):
    token = fake_token("TESTONLYGOOD0123456789abcdefNOTREAL")
    r = sb.cli("setup", "--yes", "--to", "cursor", "--token-from-env", extra_env={"GITHUB_TOKEN": token})
    assert r.returncode == 0, r.stderr
    f = sb.al / "github_token"
    assert f.read_text().strip() == token
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert token not in r.stdout + r.stderr
    w = sb.cli("where")
    assert "github token: set" in w.stdout and token not in w.stdout
    cfg = tomllib.loads((sb.al / "config.toml").read_text())
    assert "token_file" not in cfg.get("github", {}), "default config must not set an example token_file"


def test_setup_registers_absolute_command_not_uvx(sb):
    r = sb.cli("setup", "--yes", "--to", "cursor")
    assert r.returncode == 0, r.stderr
    spec = json.loads((sb.home / ".cursor" / "mcp.json").read_text())["mcpServers"]["agentloadout"]
    assert os.path.isabs(spec["command"]) and "uvx" not in Path(spec["command"]).name
    assert "set-pin" in r.stdout  # tells the user to set the approval PIN


# ------------------------------------------------------------------ git install, offline MCP (H1, H2)
@pytest.mark.skipif(not shutil.which("uv") or not shutil.which("git"), reason="needs uv and git")
def test_git_install_registers_tool_path_and_mcp_runs_offline(sb):
    """Installs the *committed* HEAD of this repo with `uv tool install git+file://` (needs network for deps)."""
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    url = f"git+file://{ROOT}@{head}"
    r = subprocess.run(["uv", "tool", "install", "--python", sys.executable, url], env=sb.env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    tool_dir, bin_dir = Path(sb.env["UV_TOOL_DIR"]), Path(sb.env["UV_TOOL_BIN_DIR"])
    exe = bin_dir / "agentloadout"
    assert exe.exists()

    direct = next((tool_dir / "agentloadout").glob("lib/python*/site-packages/agentloadout-*.dist-info/direct_url.json"))
    info = json.loads(direct.read_text())
    assert info["vcs_info"]["vcs"] == "git" and info["vcs_info"]["commit_id"] == head

    r = sb.cli("setup", "--yes", "--to", "cursor", exe=str(exe))
    assert r.returncode == 0, r.stdout + r.stderr
    spec = json.loads((sb.home / ".cursor" / "mcp.json").read_text())["mcpServers"]["agentloadout"]
    assert spec["command"].startswith(str(tool_dir)), spec
    assert "uvx" not in spec["command"] and spec["args"] == ["mcp"]

    sb.catalog()
    subprocess.run(["uv", "cache", "clean"], env=sb.env, capture_output=True, timeout=120, check=True)
    out = asyncio.run(_mcp_session(spec, {**sb.env, "UV_OFFLINE": "1"}))
    assert out["server"] == "agentloadout"
    assert "cursor" in [h["id"] for h in out["harnesses"]]
    assert [x["id"] for x in out["search"]["results"]] == ["acme/postgres-mcp"]


async def _mcp_session(spec, env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=spec["command"], args=spec["args"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            h = await s.call_tool("list_harnesses", {})
            q = await s.call_tool("search_catalog", {"query": "postgres"})
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo")
            return {"server": info.name, "harnesses": _data(h)["harnesses"], "search": _data(q)}


def _data(res):
    sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


# ------------------------------------------------------------------ setup offers the web password (BRIEF-03)
def test_setup_in_terminal_offers_the_web_password(sb):
    (sb.home / ".cursor").mkdir()
    pw = "kata-sandi-setup-1"
    rc, out = sb.pty("setup", "--to", "cursor", answers=[("Lanjutkan?", "y"), ("skill panduan", "y"), ("Tempel token", ""),
                                                        ("Pasang password web", "y"), ("Password baru", pw), ("Ulangi", pw)])
    assert rc == 0, out
    assert (sb.al / "web_password.json").exists() and pw not in out


def test_setup_with_yes_only_prints_the_password_hint(sb):
    (sb.home / ".cursor").mkdir()
    r = sb.cli("setup", "--yes", "--to", "cursor")
    assert r.returncode == 0 and "agentloadout set-password" in r.stdout
    assert not (sb.al / "web_password.json").exists()
