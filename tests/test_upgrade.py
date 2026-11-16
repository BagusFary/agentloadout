"""`agentloadout upgrade` (BRIEF-04 P1-C, H14-H24). Real `uv tool install` runs, all inside /tmp:
HOME, UV_TOOL_DIR, UV_TOOL_BIN_DIR and UV_CACHE_DIR are temporary, and the "official" repository is a local bare
repo (git+file://) built from this working copy with test tags. GitHub is never contacted."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

pytestmark = pytest.mark.skipif(not shutil.which("uv") or not shutil.which("git"), reason="needs uv and git")

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
PY = os.path.realpath(sys.executable)


def run(cmd, cwd=None, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    if check and r.returncode != 0:
        raise AssertionError(f"{cmd}: {r.stderr[-800:]}")
    return r


def set_version(src: Path, v: str, note: str):
    pp = src / "pyproject.toml"
    pp.write_text(pp.read_text().replace('version = "0.4.0"', f'version = "{v}"', 1))
    init = src / "src/agentloadout/__init__.py"
    init.write_text(init.read_text().replace('__version__ = "0.4.0"', f'__version__ = "{v}"'))
    cl = src / "CHANGELOG.md"
    cl.write_text(cl.read_text().replace("## [0.4.0]", f"## [{v}]\n\n### Fixed\n- {note}\n\n## [0.4.0]", 1))


def commit_tag(src: Path, msg: str, tag: str):
    run(GIT + ["add", "-A"], cwd=src)
    run(GIT + ["commit", "-q", "--allow-empty", "-m", msg], cwd=src)  # the working copy may equal HEAD
    run(GIT + ["tag", tag], cwd=src)
    return run(["git", "rev-parse", "HEAD"], cwd=src).stdout.strip()


@pytest.fixture(scope="module")
def repos(tmp_path_factory):
    """good.git: v0.2.0, v0.3.0 (real), v0.4.0 (this working copy), v0.4.1, v0.5.0-rc.1.
    broken.git: v0.4.0 plus v0.4.2 whose package cannot start (cli.py missing)."""
    t = tmp_path_factory.mktemp("upgrade-repos")
    src = t / "src"
    run(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(src)])
    files = run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT).stdout.split()
    for f in files:
        if f.startswith("data/") or not (ROOT / f).is_file():
            continue
        (src / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, src / f)
    shas = {"v0.4.0": commit_tag(src, "test v0.4.0 (working copy)", "v0.4.0")}
    set_version(src, "0.4.1", "uji upgrade ke 0.4.1")
    shas["v0.4.1"] = commit_tag(src, "test v0.4.1", "v0.4.1")
    pp = src / "pyproject.toml"
    pp.write_text(pp.read_text().replace('version = "0.4.1"', 'version = "0.5.0rc1"'))
    init = src / "src/agentloadout/__init__.py"
    init.write_text(init.read_text().replace('"0.4.1"', '"0.5.0rc1"'))
    shas["v0.5.0-rc.1"] = commit_tag(src, "test pre-release", "v0.5.0-rc.1")
    good = t / "good.git"
    run(["git", "clone", "-q", "--bare", str(src), str(good)])
    run(["git", "checkout", "-q", "v0.4.0"], cwd=src)
    run(GIT + ["checkout", "-q", "-b", "broken"], cwd=src)
    set_version(src, "0.4.2", "rusak")
    (src / "src/agentloadout/cli.py").unlink()
    shas["v0.4.2"] = commit_tag(src, "test broken v0.4.2", "v0.4.2")
    broken = t / "broken.git"
    run(["git", "clone", "-q", "--bare", str(src), str(broken)])
    run(["git", "--git-dir", str(broken), "tag", "-d", "v0.4.1", "v0.5.0-rc.1"])
    return {"good": f"file://{good}", "broken": f"file://{broken}", "good_dir": good, "shas": shas, "cache": t / "uvcache"}


class Tool:
    """An uv-tool install of agentloadout inside one sandbox."""

    def __init__(self, sb, repos, which="good", tag="v0.4.0"):
        self.sb, self.repo = sb, repos[which]
        self.env = {**sb.env, "UV_CACHE_DIR": str(repos["cache"]), "AGENTLOADOUT_UPGRADE_REPO": self.repo}
        run(["uv", "tool", "install", "--python", PY, f"git+{self.repo}@{tag}"], env=self.env)
        self.bin = Path(self.env["UV_TOOL_BIN_DIR"]) / "agentloadout"
        self.prefix = Path(self.env["UV_TOOL_DIR"]) / "agentloadout"
        self.mcp_path = self.prefix / "bin" / "agentloadout"

    def cli(self, *args, extra=None):
        return subprocess.run([str(self.bin), *args], env={**self.env, **(extra or {})}, capture_output=True,
                              text=True, timeout=900, stdin=subprocess.DEVNULL)

    def pty(self, *args, answers=(), extra=None):
        return self.sb.pty(*args, answers=answers, exe=str(self.bin), timeout=600,
                           extra_env={**{k: v for k, v in self.env.items() if k not in self.sb.env or self.sb.env[k] != v}, **(extra or {})})

    def state(self):
        ver = self.cli("--version").stdout.strip().split()[-1]
        du = next(self.prefix.glob("lib/python*/site-packages/agentloadout-*.dist-info/direct_url.json"))
        return ver, json.loads(du.read_text())["vcs_info"]["commit_id"]

    def history(self):
        return json.loads((self.sb.al / "upgrade_history.json").read_text())


def test_check_only_reads_and_caches(sb, repos):
    tool = Tool(sb, repos)
    before = tool.state()
    r = tool.cli("upgrade", "--check")
    assert r.returncode == 0 and "Versi baru tersedia: 0.4.1" in r.stdout and "uji upgrade ke 0.4.1" in r.stdout
    assert tool.state() == before
    cache = json.loads((sb.al / "update_check.json").read_text())
    assert cache["tag"] == "v0.4.1" and cache["sha"] == repos["shas"]["v0.4.1"]
    pre = tool.cli("upgrade", "--check", "--pre")
    assert "0.5.0rc1" in pre.stdout
    tool.cli("upgrade", "--check")  # back to releases only
    # where reads the cache only: it still answers with the repo gone
    w = tool.cli("where", extra={"AGENTLOADOUT_UPGRADE_REPO": "file:///nonexistent/repo.git"})
    assert "terbaru 0.4.1" in w.stdout and "agentloadout upgrade" in w.stdout and "uv-tool-git" in w.stdout


def test_agent_upgrades_to_latest_and_downgrade_rules(sb, repos):
    sb.catalog()
    (sb.home / ".cursor").mkdir()
    tool = Tool(sb, repos)
    assert tool.cli("setup", "--yes", "--to", "cursor").returncode == 0
    (sb.al / "github_token").write_text("ghp_DUMMYGOOD0123456789abcdefghijkUPGR\n")
    r = tool.cli("upgrade", "--yes", extra={"CLAUDECODE": "1"})  # an agent, on the user's request
    assert r.returncode == 0, r.stdout + r.stderr
    assert tool.state() == ("0.4.1", repos["shas"]["v0.4.1"])
    spec = json.loads((sb.home / ".cursor" / "mcp.json").read_text())["mcpServers"]["agentloadout"]
    assert spec["command"] == str(tool.mcp_path) and Path(spec["command"]).exists()  # MCP path unchanged (H16)
    h = tool.history()[-1]
    assert h["status"] == "ok" and h["from"] == "0.4.0" and h["to"] == "0.4.1" and h["to_sha"] == repos["shas"]["v0.4.1"]
    bk = Path(h["backup"])
    assert (bk / "catalog.db").exists() and not (bk / "github_token").exists()
    assert "ghp_DUMMY" not in "".join(p.read_text(errors="replace") for p in bk.rglob("*") if p.is_file() and p.suffix != ".db")
    assert tool.cli("upgrade", "--yes").stdout.startswith("Sudah versi 0.4.1")

    # downgrades and --to: a human at a terminal only
    r = tool.cli("upgrade", "--to", "v0.4.0", "--yes")
    assert r.returncode == 2 and "terminal" in r.stdout
    rc, out = tool.pty("upgrade", "--to", "v0.4.0", answers=[("Lanjutkan", "y")], extra={"CLAUDECODE": "1"})
    assert rc == 2 and "bukan agen" in out
    rc, out = tool.pty("upgrade", "--to", "v0.2.0")
    assert rc == 2 and "lebih lama dari 0.3.0" in out
    r = tool.cli("upgrade", "--to", "v0.2.0", "--force-insecure", "--yes")
    assert r.returncode == 2
    rc, out = tool.pty("upgrade", "--to", "v0.2.0", "--force-insecure", answers=[("INSECURE", "no")])
    assert rc == 1 and "Dibatalkan" in out
    assert tool.state()[0] == "0.4.1"
    rc, out = tool.pty("upgrade", "--to", "v0.9.9")
    assert rc == 2 and "tidak ada" in out
    rc, out = tool.pty("upgrade", "--to", "v0.4.0", answers=[("Lanjutkan", "y")])
    assert rc == 0, out
    assert tool.state() == ("0.4.0", repos["shas"]["v0.4.0"])


def test_moved_tag_is_refused_until_checked_again(sb, repos, tmp_path):
    tool = Tool(sb, repos)
    assert tool.cli("upgrade", "--check").returncode == 0
    # the owner (or an attacker) moves v0.4.1 to another commit in the "official" repo
    work = tmp_path / "w"
    run(["git", "clone", "-q", str(repos["good_dir"]), str(work)])
    run(["git", "checkout", "-q", "v0.4.1"], cwd=work)
    (work / "CHANGELOG.md").write_text((work / "CHANGELOG.md").read_text().replace("uji upgrade ke 0.4.1", "tag dipindah"))
    run(GIT + ["commit", "-q", "-am", "moved"], cwd=work)
    run(GIT + ["tag", "-f", "v0.4.1"], cwd=work)
    run(["git", "push", "-q", "-f", "origin", "refs/tags/v0.4.1"], cwd=work)
    moved = run(["git", "rev-parse", "HEAD"], cwd=work).stdout.strip()
    try:
        r = tool.cli("upgrade", "--yes")
        assert r.returncode == 2 and "berpindah" in r.stdout and tool.state()[0] == "0.4.0"
        assert tool.cli("upgrade", "--check").returncode == 0
        r = tool.cli("upgrade", "--yes")
        assert r.returncode == 0, r.stdout
        assert tool.state() == ("0.4.1", moved)  # installed by the SHA it resolved, and verified
    finally:  # restore the module repo for other tests
        run(["git", "push", "-q", "-f", "origin", f"{repos['shas']['v0.4.1']}:refs/tags/v0.4.1"], cwd=work)


def test_failed_verification_rolls_back(sb, repos):
    tool = Tool(sb, repos, which="broken")
    before = tool.state()
    r = tool.cli("upgrade", "--yes")
    assert r.returncode == 1 and "rollback" in r.stdout and "kembali ke 0.4.0" in r.stdout, r.stdout
    assert tool.state() == before
    h = tool.history()[-1]
    assert h["status"] == "failed" and h["rollback"] == "ok" and h["to"] == "0.4.2"


def test_refusals(sb, repos):
    # a checkout / editable install (like the owner's instance): the dev venv of this repo
    r = sb.cli("upgrade", "--yes")
    assert r.returncode == 2 and "git pull" in r.stdout
    sb.catalog()
    tool = Tool(sb, repos)
    from agentloadout import filelock
    fd = filelock.acquire(sb.al / "refresh.lock")
    try:
        r = tool.cli("upgrade", "--yes")
        assert r.returncode == 2 and "pembaruan katalog sedang berjalan" in r.stdout
    finally:
        filelock.release(fd)
    pid = sb.plan()
    sb.approve(pid)
    r = tool.cli("upgrade", "--yes")
    assert r.returncode == 2 and pid in r.stdout and tool.state()[0] == "0.4.0"
    sb.py(f"from agentloadout import plans; plans.reject({pid!r}, 'test')")
    # a different source than the official repo is refused even if the override env says so
    r = tool.cli("upgrade", "--yes", extra={"AGENTLOADOUT_UPGRADE_REPO": repos["broken"]})
    assert r.returncode == 2 and "bukan dari repo resmi" in r.stdout
    assert tool.cli("update").returncode == 2 and "upgrade" in tool.cli("update").stderr


def test_install_kind_tells_uvx_from_uv_tool(monkeypatch, tmp_path):
    from agentloadout import selfupdate
    git = {"url": "https://github.com/BagusFary/agentloadout", "vcs_info": {"vcs": "git", "commit_id": "a" * 40}}
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))
    monkeypatch.setattr(selfupdate, "_direct_url", lambda: git)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "tools" / "agentloadout"))
    assert selfupdate.install_kind()["kind"] == "uv-tool-git"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "cache" / "archive-v0" / "abc"))
    assert selfupdate.install_kind()["kind"] == "uvx"
    monkeypatch.setattr(selfupdate, "_direct_url", lambda: {"url": "file:///x", "dir_info": {"editable": True}})
    assert selfupdate.install_kind()["kind"] == "checkout"


def test_versions_and_changelog_parsing():
    from agentloadout import selfupdate as s
    tags = ["v0.10.0", "v0.4.1", "v0.5.0-rc.1", "v0.4.0", "nightly", "v1.0"]
    vs = sorted((s.parse_tag(t) for t in tags if s.parse_tag(t)), key=s.vkey, reverse=True)
    assert [v[:3] for v in vs] == [(0, 10, 0), (0, 5, 0), (0, 4, 1), (0, 4, 0)] and vs[1][3] == ("rc", 1)
    assert s.tag_version("v0.5.0-rc.1") == "0.5.0rc1" and s.parse_version("0.5.0rc1")[3] == ("rc", 1)
    text = "# C\n\n## [0.4.1]\n### Fixed\n- a\n\n## [0.4.0]\n- b\n"
    assert s.changelog_section(text, "0.4.1") == "### Fixed\n- a" and s.changelog_section(text, "9.9.9") == ""


def test_notice_in_web_where_and_mcp_reads_the_cache(sb):
    sb.catalog()
    sb.al.mkdir(exist_ok=True)
    (sb.al / "update_check.json").write_text(json.dumps({
        "checked_at": "2026-11-16T00:00:00+00:00", "latest": "9.9.9", "tag": "v9.9.9", "sha": "b" * 40,
        "notes": "### Added\n- <script>alert(1)</script> hal baru", "error": None}))
    env = {"AGENTLOADOUT_UPGRADE_REPO": "file:///nonexistent/repo.git"}  # any network check would fail loudly
    web = sb.ui(extra_env=env)
    st = web.request("GET", "/api/status")["json"]
    assert st["update"]["latest"] == "9.9.9" and "git pull" in st["update"]["command"]
    assert web.request("GET", "/api/health")["json"]["version"]
    w = sb.cli("where", extra_env=env).stdout
    assert "terbaru 9.9.9" in w
    r = sb.py("import json\nfrom agentloadout import mcp_server as m\n"
              "a = m.search_catalog(query='demo')\nb = m.search_catalog(query='demo')\nh = m.list_harnesses()\n"
              "print(json.dumps([('agentloadout_update' in a), ('agentloadout_update' in b), h['agentloadout']]))",
              extra_env=env)
    first, second, info = json.loads(r.stdout.strip().splitlines()[-1])
    assert first is True and second is False and info["latest"] == "9.9.9" and info["newer"] is True
    js = (ROOT / "src/agentloadout/web/static/app.js").read_text()
    assert "esc(u.notes)" in js and "data-copy" in js and "/api/upgrade" not in js  # escaped notes, no upgrade button


def test_old_process_warns_about_newer_files(sb, monkeypatch):
    from agentloadout import selfupdate
    monkeypatch.setattr(selfupdate, "disk_version", lambda: "9.9.9")
    assert "9.9.9" in selfupdate.stale_process_note()
    monkeypatch.setattr(selfupdate, "disk_version", lambda: selfupdate.__version__)
    assert selfupdate.stale_process_note() is None
    web = sb.ui()
    ui = json.loads((sb.al / "ui.json").read_text())
    ui["version"] = "0.0.1"
    (sb.al / "ui.json").write_text(json.dumps(ui))
    assert "masih versi 0.0.1" in sb.cli("where").stdout
    web.stop()
