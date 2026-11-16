"""The owner's daily routine script (scripts/update.sh) and related review fixes (BRIEF-04 W1, S12, S13).
update.sh runs from a copy of the scripts folder in /tmp; nothing of the real instance is touched."""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from conftest import ROOT

STUB = {
    "backup.py": 'print("cadangan OK: stub.db (0.1 MB)")',
    "translate.py": 'import sys\nprint("diekspor 0 teks" if "--export" in sys.argv else "terjemahan diterapkan 0")',
    "check_updates.py": 'print("update tersedia: 0")',
    "collect.py": 'print("selesai dalam 5 detik: {}")',
}


@pytest.fixture
def instance(tmp_path, sb):
    """A miniature instance folder: scripts/ (update.sh copied, other scripts stubbed), data/, src -> this repo."""
    inst = tmp_path / "instance"
    (inst / "scripts").mkdir(parents=True)
    (inst / "data").mkdir()
    shutil.copy2(ROOT / "scripts" / "update.sh", inst / "scripts" / "update.sh")
    for name, code in STUB.items():
        (inst / "scripts" / name).write_text(code + "\n")
    (inst / "src").symlink_to(ROOT / "src")
    pybin = tmp_path / "pybin"
    pybin.mkdir()
    (pybin / "python3").symlink_to(sys.executable)
    env = {**sb.env, "PATH": f"{pybin}:{sb.env['PATH']}", "AGENTLOADOUT_HOME": str(inst / "data"),
           "AGENTLOADOUT_DB": str(inst / "data" / "katalog.db"), "AGENTLOADOUT_BACKUP_DIR": str(tmp_path / "bk")}
    return inst, env


def run_update(inst, env):
    return subprocess.run(["bash", str(inst / "scripts" / "update.sh")], env=env, capture_output=True, text=True, timeout=120)


def test_normal_run_prints_all_four_summary_lines(instance):
    inst, env = instance
    r = run_update(inst, env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["cadangan OK: stub.db (0.1 MB)", "selesai dalam 5 detik: {}", "diekspor 0 teks",
                                     "update tersedia: 0"]


def test_skipped_collection_does_not_fail_the_routine(instance):
    """Real scripts/collect.py, with the refresh lock held by another refresh."""
    inst, env = instance
    shutil.copy2(ROOT / "scripts" / "collect.py", inst / "scripts" / "collect.py")
    from agentloadout import filelock
    fd = filelock.acquire(inst / "data" / "refresh.lock")
    try:
        r = run_update(inst, env)
    finally:
        filelock.release(fd)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert lines[0].startswith("cadangan OK") and "dilewati" in lines[1] and lines[2:] == ["diekspor 0 teks", "update tersedia: 0"]
    assert not (inst / "data" / "katalog.db").exists()  # the collector did not run


def test_missing_summary_lines_are_tolerated(instance):
    inst, env = instance
    (inst / "scripts" / "check_updates.py").write_text("pass\n")  # prints nothing
    r = run_update(inst, env)
    assert r.returncode == 0 and len(r.stdout.splitlines()) == 3


def test_failed_backup_still_stops_the_routine(instance):
    inst, env = instance
    (inst / "scripts" / "backup.py").write_text("raise SystemExit(3)\n")
    r = run_update(inst, env)
    assert r.returncode == 1 and "CADANGAN GAGAL" in r.stdout


# ------------------------------------------------------------------ S12: an old error is not shown for a new run
def test_new_run_clears_the_previous_error(sb):
    sb.catalog()
    sb.py("from agentloadout import refresh\nrefresh.record_error('kegagalan lama')")
    code = ("import time\nfrom agentloadout import refresh, collector\ncollector.main = lambda: time.sleep(60)\n"
            "refresh.run_locked(source='cli', backup=False)")
    p = subprocess.Popen([sys.executable, "-c", code], env=sb.env)
    try:
        for _ in range(100):
            r = sb.py("import json\nfrom agentloadout import db, refresh\nc = db.connect()\n"
                      "print(json.dumps([refresh.running(), db.get_meta(c, 'last_error')]))")
            running, err = json.loads(r.stdout.strip().splitlines()[-1])
            if running:
                break
            time.sleep(0.1)
        assert running and err is None
        p.kill()  # kill -9: the run cannot record anything
        p.wait(10)
        web = sb.ui()
        st = web.request("GET", "/api/status")["json"]
        assert st["running"] is False and st["last_error"] is None and st["progress"]["stale"] is True
        assert "kegagalan lama" not in json.dumps(st)
    finally:
        if p.poll() is None:
            p.kill()


# ------------------------------------------------------------------ S13: restore --list in time order
def test_restore_list_is_in_time_order_and_marks_the_first_copy(sb):
    bk = sb.root / "bk" / "daily"
    bk.mkdir(parents=True)
    now = time.time()
    for name, age in (("katalog-2026-11-16.db", 300), ("katalog-2026-11-16-101010.db", 200),
                      ("katalog-2026-11-16-121212.db", 100), ("katalog-2026-11-15.db", 90000)):
        f = bk / name
        f.write_text("x")
        os.utime(f, (now - age, now - age))
    r = sb.cli("restore", extra_env={"AGENTLOADOUT_BACKUP_DIR": str(sb.root / "bk")})
    lines = [x.strip() for x in r.stdout.splitlines() if "katalog-" in x]
    assert [x.split("/")[-1].split()[0] for x in lines] == ["katalog-2026-11-15.db", "katalog-2026-11-16.db",
                                                          "katalog-2026-11-16-101010.db", "katalog-2026-11-16-121212.db"]
    assert lines[1].endswith("(salinan pertama hari itu)") and not lines[3].endswith("(salinan pertama hari itu)")
