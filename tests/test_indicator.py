"""Catalog fetch indicator, one lock for every refresh path, failures, "Perbarui sekarang", backups (BRIEF-04 H1-H3,
H11-H13 and Mas Irfan's extra findings). The collector never reaches GitHub here: search() is replaced."""
import datetime as dt
import json
import signal
import subprocess
import sys
import time

import pytest

from conftest import ROOT

DUMMY = "ghp_DUMMYGOOD0123456789abcdefghijkLEAK"
FAKE_WORKER = "import sys, time; time.sleep(float(sys.argv[1]))"


def status(web):
    return web.request("GET", "/api/status")["json"]


def offline_collect(extra=""):
    """Python snippet: run the collector with GitHub search replaced (no network)."""
    return ("from agentloadout import collector\ncollector.search = lambda q, pages=1: []\n"
            "collector.registry_lookup = lambda full: None\n" + extra)


@pytest.fixture
def web(sb):
    sb.catalog()
    return sb.ui()


# ------------------------------------------------------------------ running comes from the lock (H3)
def test_running_follows_the_lock_not_the_progress_meta(sb, web):
    from agentloadout import filelock
    assert status(web)["running"] is False
    fd = filelock.acquire(sb.al / "refresh.lock")
    try:
        s = status(web)
        assert s["running"] is True and s["progress"]["label"] == "Memulai pembaruan"
    finally:
        filelock.release(fd)
    # a killed worker leaves progress.running = true behind; without the lock it is shown as stopped
    sb.py("from agentloadout import db\ncon = db.connect()\n"
          "db.set_meta(con, 'progress', {'running': True, 'step': 2, 'label': 'Mencari repo di GitHub', 'at': '2026-11-16T00:00:00+00:00'})\n"
          "con.commit()")
    s = status(web)
    assert s["running"] is False and s["progress"]["stale"] is True


def test_killed_worker_is_not_running(sb, web):
    code = ("import sys, time\nsys.argv = ['x']\nfrom agentloadout import refresh, collector\n"
            "collector.main = lambda: time.sleep(60)\nrefresh.run_locked(source='cli', backup=False)")
    p = subprocess.Popen([sys.executable, "-c", code], env=sb.env)
    try:
        for _ in range(50):
            if status(web)["running"]:
                break
            time.sleep(0.1)
        assert status(web)["running"] is True
        p.kill()
        p.wait(10)
        assert status(web)["running"] is False
    finally:
        if p.poll() is None:
            p.kill()


# ------------------------------------------------------------------ progress, last_run, last_error (H1, H5, H12)
def test_offline_run_records_source_finish_time_and_token_source(sb):
    sb.catalog()
    t0 = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    sb.py(offline_collect("from agentloadout import refresh\nrefresh.run_locked(source='cli', backup=False)"),
          extra_env={"GITHUB_TOKEN": DUMMY})
    r = sb.py("import json\nfrom agentloadout import db\nc = db.connect()\n"
              "print(json.dumps([db.get_meta(c, 'last_run'), db.get_meta(c, 'progress'), db.get_meta(c, 'last_error')]))")
    lr, prog, err = json.loads(r.stdout.strip().splitlines()[-1])
    assert lr["source"] == "cli" and lr["token_source"] == "env" and lr["quota_exhausted"] == [] and lr["token"] is True
    assert dt.datetime.fromisoformat(lr["at"]) >= dt.datetime.fromisoformat(lr["started_at"]) >= t0
    assert prog["label"] == "Selesai" and prog["started_at"] == lr["started_at"] and err is None
    assert DUMMY not in r.stdout


def test_progress_shows_quota_wait_and_quota_exhausted(sb):
    sb.catalog()
    r = sb.py("import json, time\nfrom agentloadout import collector, db\ncon = db.connect()\n"
              "collector.WAIT['until'] = time.time() + 120\ncollector.QUOTA['core'] = time.time() + 3000\n"
              "collector.progress(con, 3, 'Membaca detail repo', 10, 630)\n"
              "print(json.dumps(db.get_meta(con, 'progress')))", extra_env={"AGENTLOADOUT_REFRESH_SOURCE": "web"})
    p = json.loads(r.stdout.strip().splitlines()[-1])
    assert p["note"].startswith("Menunggu kuota GitHub sampai") and "core" in p["quota_until"] and p["source"] == "web"


def test_failure_is_visible_sanitized_and_never_called_updated(sb, web):
    home = str(sb.home)  # the HOME of the process that fails
    boom = f"boom {DUMMY} di {home}/rahasia/katalog.db"
    sb.py(offline_collect(f"collector.run = lambda con, t0: (_ for _ in ()).throw(RuntimeError({boom!r}))\n"
                          "from agentloadout import refresh\nrefresh.run_locked(source='web', backup=False)"),
          extra_env={"GITHUB_TOKEN": DUMMY}, check=False)
    s = status(web)
    assert s["running"] is False and s["last_error"]["source"] == "web"
    msg = s["last_error"]["message"]
    assert "boom" in msg and DUMMY not in msg and home not in msg and "~/rahasia" in msg
    assert s["progress"]["label"].startswith("Gagal")
    # the web page must not announce a failed run as "updated" (app.js guards the toast with last_error)
    js = (ROOT / "src/agentloadout/web/static/app.js").read_text()
    i = js.index('toast("Data katalog sudah diperbarui")')
    assert "if (j.last_error)" in js[i - 300:i] and js.count("Data katalog sudah diperbarui") == 1
    # a later successful run hides the old error
    sb.py(offline_collect("from agentloadout import refresh\nrefresh.run_locked(source='cli', backup=False)"))
    assert status(web)["last_error"] is None


def test_sigterm_is_recorded(sb, web):
    code = ("import time\nfrom agentloadout import refresh, collector\n"
            "def slow():\n    try:\n        time.sleep(60)\n    except BaseException:\n        raise\n"
            "collector.main = slow\nrefresh.run_locked(source='routine', backup=False)")
    p = subprocess.Popen([sys.executable, "-c", code], env=sb.env)
    for _ in range(50):
        if status(web)["running"]:
            break
        time.sleep(0.1)
    p.send_signal(signal.SIGTERM)
    p.wait(10)
    s = status(web)
    assert s["running"] is False and "sinyal" in s["last_error"]["message"] and s["last_error"]["source"] == "routine"


def test_eta_and_status_fields(sb, web):
    sb.py("from agentloadout import db\nc = db.connect()\n"
          "db.set_meta(c, 'last_run', {'at': '2026-11-16T00:10:00+00:00', 'seconds': 300, 'token': False, 'items': 4})\nc.commit()")
    from agentloadout import filelock
    started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=60)).isoformat(timespec="seconds")
    sb.py("from agentloadout import db\nc = db.connect()\n"
          f"db.set_meta(c, 'progress', {{'running': True, 'step': 2, 'steps': 4, 'label': 'Mencari repo di GitHub', "
          f"'started_at': '{started}', 'source': 'web'}})\nc.commit()")
    fd = filelock.acquire(sb.al / "refresh.lock")
    try:
        s = status(web)
        assert s["running"] and s["started_at"] == started and 200 <= s["eta_seconds"] <= 250
        assert s["summary"]["items"] == 4 and s["unchecked"] == 1 and s["token_active"] is False
    finally:
        filelock.release(fd)


def test_unchecked_items_are_marked(sb, web):
    items = web.request("GET", "/api/items")["json"]["items"]
    assert {i["id"]: i["checked"] for i in items}["acme/unchecked-skills"] == 0
    js = (ROOT / "src/agentloadout/web/static/app.js").read_text()
    assert "it.checked === 0" in js and "Belum diperiksa" in js and "#f-unchecked" in js


# ------------------------------------------------------------------ one lock for the daily routine too (H2)
def test_collect_script_skips_while_another_refresh_runs(sb):
    from agentloadout import filelock
    sb.catalog()
    db_file = sb.al / "catalog.db"
    env = {**sb.env, "AGENTLOADOUT_DB": str(db_file), "AGENTLOADOUT_BACKUP_DIR": str(sb.root / "bk")}
    fd = filelock.acquire(sb.al / "refresh.lock")
    try:
        before = db_file.stat().st_mtime
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "collect.py")], env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and "dilewati" in r.stdout
        assert db_file.stat().st_mtime == before
    finally:
        filelock.release(fd)


# ------------------------------------------------------------------ "Perbarui sekarang" (K3)
@pytest.fixture
def fast_worker(sb):
    """The UI's refresh worker becomes a 2-second sleeper (no collector, no network)."""
    site = sb.root / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import sys\nfrom agentloadout import refresh\n"
        f"refresh.WORKER = [sys.executable, '-c', {FAKE_WORKER!r}, '2']\n")
    return {"PYTHONPATH": str(site)}


def test_refresh_now_button(sb, fast_worker):
    sb.catalog()
    web = sb.ui(extra_env=fast_worker)
    r = web.request("POST", "/api/refresh", body={})
    assert r["status"] == 202 and "Tanpa token" in r["json"]["warning"]
    assert status(web)["running"] is True
    assert web.request("POST", "/api/refresh", body={})["status"] == 409
    for _ in range(60):
        if not status(web)["running"]:
            break
        time.sleep(0.1)
    again = web.request("POST", "/api/refresh", body={})
    assert again["status"] == 429 and 800 < again["json"]["retry_after"] <= 900
    assert status(web)["cooldown_left"] > 800
    assert json.loads((sb.al / "refresh.state.json").read_text())["source"] == "web"


def test_refresh_now_is_refused_for_funnel_and_cross_site(sb, fast_worker):
    sb.catalog()
    sb.set_password("kata-sandi-uji-12")
    web = sb.ui(extra_env=fast_worker)
    _, cookie = web.login("kata-sandi-uji-12")
    funnel = {"X-Forwarded-For": "1.2.3.4", "X-Forwarded-Proto": "https", "Tailscale-Funnel-Request": "?1"}
    assert web.request("POST", "/api/refresh", headers=funnel, cookie=cookie, body={})["status"] == 403
    assert web.request("POST", "/api/refresh", headers={"X-Agentloadout": "0"}, cookie=cookie, body={})["status"] == 403
    assert web.request("POST", "/api/refresh", body={})["status"] == 401
    assert not (sb.al / "refresh.state.json").exists()


# ------------------------------------------------------------------ backups of the same day (H13)
def test_repeated_backups_keep_the_first_copy_of_the_day(sb):
    sb.catalog()
    bk = sb.root / "bk"
    env = {"AGENTLOADOUT_BACKUP_DIR": str(bk)}
    sb.py("from agentloadout import backup; backup.main_args([])", extra_env=env)
    today = dt.date.today().isoformat()
    first = bk / "daily" / f"katalog-{today}.db"
    sig = (first.stat().st_mtime_ns, first.read_bytes())
    for i in range(5):
        time.sleep(1.05)  # extra copies are named per second
        sb.py("from agentloadout import backup; backup.main_args([])", extra_env=env)
    assert (first.stat().st_mtime_ns, first.read_bytes()) == sig
    extras = sorted(p.name for p in (bk / "daily").glob(f"katalog-{today}-*.db"))
    assert len(extras) == 3  # KEEP_EXTRA_PER_DAY
    assert len(list((bk / "daily").glob(f"katalog-{today}-*.state.json.gz"))) == 3


def test_rotation_keeps_seven_days_with_extras(tmp_path):
    from agentloadout import backup
    for d in range(10):
        day = (dt.date(2026, 11, 1) + dt.timedelta(days=d)).isoformat()
        for stem in (f"katalog-{day}", f"katalog-{day}-101010", f"katalog-{day}-121212"):
            for ext in (".db", ".state.json.gz"):
                (tmp_path / (stem + ext)).write_text("x")
    removed = backup.rotate(str(tmp_path), 7, keep_extra=1)
    days = sorted({p.name[8:18] for p in tmp_path.iterdir()})
    assert days == [(dt.date(2026, 11, 1) + dt.timedelta(days=d)).isoformat() for d in range(3, 10)]
    left = sorted(p.name for p in tmp_path.glob("katalog-2026-11-10*.db"))
    assert left == ["katalog-2026-11-10-121212.db", "katalog-2026-11-10.db"] and removed == 3 * 3 * 2 + 7 * 2
