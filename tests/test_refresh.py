"""Catalog refresh (H6, H7): PID lock, quota exhaustion, unchecked items, batch saving. No network."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOADOUT_HOME", str(tmp_path / "al"))
    return tmp_path / "al"


def _holder(path):
    """Another process that holds the refresh lock until killed."""
    code = ("import sys, time\nfrom agentloadout import filelock\nfilelock.acquire(__import__('pathlib').Path(sys.argv[1]))\n"
            "print('held', flush=True)\ntime.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_lock_file_with_live_foreign_pid_is_stale(home):
    """A PID in the file means nothing: an unrelated live process (reused PID) does not block a refresh."""
    from agentloadout import refresh
    other = subprocess.Popen(["sleep", "30"])
    try:
        refresh.lock_path().write_text(str(other.pid))  # e.g. left by v0.2.0-draft or a crashed refresh
        refresh.pid_path().write_text(str(other.pid))
        assert not refresh.running()
        fd = refresh.try_lock()
        assert fd is not None
        os.close(fd)
    finally:
        other.kill()
        other.wait()


def test_held_lock_blocks_and_is_freed_when_the_holder_dies(home):
    from agentloadout import refresh
    p = _holder(refresh.lock_path())
    try:
        assert refresh.running()
        assert refresh.try_lock() is None
        assert not refresh.start_background()
    finally:
        p.kill()  # SIGKILL: no cleanup code runs
        p.wait()
    assert not refresh.running()


def test_two_concurrent_start_background_start_one_worker(home, tmp_path, monkeypatch):
    from agentloadout import refresh
    marker = tmp_path / "workers.txt"
    monkeypatch.setattr(refresh, "WORKER", [sys.executable, "-c",
                        "import os, sys, time; open(sys.argv[1], 'a').write(f'{os.getpid()}\\n'); time.sleep(2)", str(marker)])
    gate, started = threading.Barrier(2), []

    def go():
        gate.wait()
        started.append(refresh.start_background())

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(started) == [False, True]
    assert refresh.running()  # the worker holds the lock after start_background returned
    deadline = time.time() + 15
    while refresh.running() and time.time() < deadline:
        time.sleep(0.2)
    assert not refresh.running()
    assert len(marker.read_text().splitlines()) == 1


def test_cli_refresh_uses_the_same_lock(sb):
    from agentloadout import filelock
    sb.al.mkdir(parents=True, exist_ok=True)
    fd = filelock.acquire(sb.al / "refresh.lock")
    try:
        r = sb.cli("refresh")
        assert r.returncode == 1 and "sedang berjalan" in r.stderr
    finally:
        filelock.release(fd)


# ------------------------------------------------------------------ collector without network
class FakeResp:
    def __init__(self, code, text="", headers=None):
        self.status_code, self.text, self.headers = code, text, headers or {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        pass


@pytest.fixture
def collector(home, monkeypatch):
    from agentloadout import collector as c
    monkeypatch.setattr(c, "QUOTA", {})
    return c


def test_exhausted_quota_raises_and_stops_calling_the_api(collector, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return FakeResp(403, "API rate limit exceeded", {"x-ratelimit-reset": str(int(time.time()) + 3000)})

    monkeypatch.setattr(collector.S, "get", fake_get)
    with pytest.raises(collector.QuotaExhausted):
        collector.gh_get("/repos/a/b/git/trees/main")
    with pytest.raises(collector.QuotaExhausted):
        collector.gh_get("/repos/a/b/git/trees/main")
    assert len(calls) == 1  # no waiting, no retry loop once the quota is gone


def test_without_token_readme_comes_from_raw(collector, monkeypatch):
    monkeypatch.setattr(collector, "_tok", "")
    monkeypatch.setattr(collector.S, "get", lambda *a, **k: pytest.fail("REST API used for README"))
    seen = []

    def raw_get(url, **kw):
        seen.append(url)
        return FakeResp(200, "# Demo\nexecute arbitrary commands") if url.endswith("/README.md") else FakeResp(404)

    monkeypatch.setattr(collector.RAW, "get", raw_get)
    assert "Demo" in collector.readme("acme/demo", "main")
    assert seen == ["https://raw.githubusercontent.com/acme/demo/main/README.md"]


def _repo(name, **kw):
    return {"name": name, "full_name": f"acme/{name}", "html_url": f"https://github.com/acme/{name}", "description": "skills",
            "topics": [], "stargazers_count": 50, "created_at": "2026-01-01T00:00:00Z", "pushed_at": "2026-11-01T00:00:00Z",
            "default_branch": "main", "license": {"spdx_id": "MIT"}, **kw}


def test_unchecked_item_is_marked_not_empty(collector, monkeypatch):
    monkeypatch.setattr(collector, "readme", lambda full, branch="HEAD": "")
    monkeypatch.setattr(collector, "package_hint", lambda full, branch="HEAD": None)

    def no_quota(*a, **k):
        raise collector.QuotaExhausted("core")

    monkeypatch.setattr(collector, "skill_paths", no_quota)
    r = collector.enrich({"repo": _repo("demo-skills"), "kind": "skill"})
    assert r["checked"] is False and r["kind"] == "skill"  # not silently turned into an "app"


def test_batches_are_committed_and_search_shows_unchecked(collector, home):
    from agentloadout import db
    con = db.connect()
    stamp = {"now": "2026-11-14T00:00:00+00:00", "today": "2026-11-14", "week_ago": "2026-11-07"}
    ok = {"repo": _repo("good"), "kind": "mcp", "reg": None, "install": [], "env": [], "risks": [], "paths": [],
          "skills": [], "sources": [], "checked": True}
    bad = {**ok, "repo": _repo("later"), "kind": "skill", "checked": False}
    assert collector.save_batch(con, [ok, bad], stamp) == 2
    con.close()
    other = db.connect()  # committed: visible to another connection
    rows = {r["id"]: r["checked"] for r in other.execute("SELECT id, checked FROM items")}
    assert rows == {"acme/good": 1, "acme/later": 0}

    from agentloadout import mcp_server
    row = other.execute("SELECT * FROM items WHERE id='acme/later'").fetchone()
    out = mcp_server._row(row)
    assert out["checked"] is False and "belum diperiksa" in out["note"]
    assert mcp_server._row(other.execute("SELECT * FROM items WHERE id='acme/good'").fetchone())["checked"] is True


def test_reuse_keeps_old_details_and_refreshes_dated_risks(collector):
    row = {"kind": "skill", "registry_name": None, "install": "[]", "env_vars": "[]", "skill_paths": '["skills/x"]',
           "skills": "[]", "risks": json.dumps([{"code": "shell", "label": "shell", "level": "high"},
                                                {"code": "stale", "label": "old", "level": "medium"}])}
    r = collector.from_db(row, _repo("x"))
    codes = [x["code"] for x in r["risks"]]
    assert "shell" in codes and "stale" not in codes and r["paths"] == ["skills/x"] and r["checked"] is True
