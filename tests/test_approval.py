"""Approval PIN (H5): an agent cannot approve its own plan through the CLI, the web API or a plan edit."""
import datetime as dt
import json
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import AGENT_MARKERS

PIN = "24681357"


@pytest.fixture
def cat(sb):
    sb.catalog()
    (sb.home / ".cursor").mkdir()
    return sb


def status(sb, pid):
    return sb.plan_json(pid)["status"]


# ------------------------------------------------------------------ apply without approval
def test_apply_plan_without_approval_is_refused(cat):
    pid = cat.plan()
    r = cat.py(f"from agentloadout import plans; plans.apply({pid!r})", check=False)
    assert r.returncode != 0 and "belum disetujui" in r.stderr
    assert not (cat.home / ".cursor" / "mcp.json").exists()


# ------------------------------------------------------------------ CLI approve
def test_approve_without_tty_is_refused(cat):
    cat.set_pin(PIN)
    pid = cat.plan()
    r = cat.cli("approve", pid)
    assert r.returncode == 2 and "Ditolak" in r.stderr
    assert status(cat, pid) == "pending"


def test_old_noninteractive_env_has_no_effect(cat):
    cat.set_pin(PIN)
    pid = cat.plan()
    r = cat.cli("approve", pid, extra_env={"AGENTLOADOUT_ALLOW_NONINTERACTIVE_APPROVAL": "1"})
    assert r.returncode == 2
    assert status(cat, pid) == "pending"


def test_approve_has_no_yes_flag(cat):
    pid = cat.plan()
    r = cat.cli("approve", pid, "--yes")
    assert r.returncode == 2 and "unrecognized arguments" in r.stderr
    assert status(cat, pid) == "pending"


def test_pseudo_tty_without_pin_set_is_refused(cat):
    pid = cat.plan()
    rc, out = cat.pty("approve", pid)
    assert rc == 2 and "agentloadout set-pin" in out
    assert status(cat, pid) == "pending"


def test_pseudo_tty_with_wrong_or_empty_pin_is_refused(cat):
    cat.set_pin(PIN)
    pid = cat.plan()
    for typed in ("", "13579111"):
        rc, out = cat.pty("approve", pid, answers=[("PIN persetujuan", typed)])
        assert rc == 2 and "PIN salah" in out, out
        assert status(cat, pid) == "pending"


def test_pin_approval_in_terminal_then_apply(cat):
    cat.set_pin(PIN)
    pid = cat.plan()
    rc, out = cat.pty("approve", pid, answers=[("PIN persetujuan", PIN)])
    assert rc == 0, out
    assert PIN not in out  # getpass: no echo
    p = cat.plan_json(pid)
    assert p["status"] == "approved" and p["approved_by"] == "pin:cli"
    cat.py(f"from agentloadout import plans; plans.apply({pid!r})")
    assert "demo-mcp" in (cat.home / ".cursor" / "mcp.json").read_text()


def test_edited_plan_file_is_refused(cat):
    pid = cat.plan()
    f = cat.al / "plans" / f"{pid}.json"
    p = json.loads(f.read_text())
    p.update(status="approved", approved_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             approved_by="pin:cli")
    f.write_text(json.dumps(p))
    r = cat.py(f"from agentloadout import plans; plans.apply({pid!r})", check=False)
    assert r.returncode != 0 and "persetujuan rencana tidak valid" in r.stderr
    assert not (cat.home / ".cursor" / "mcp.json").exists()


# ------------------------------------------------------------------ set-pin
@pytest.mark.parametrize("marker", AGENT_MARKERS)
def test_set_pin_refused_inside_agent(sb, marker):
    rc, out = sb.pty("set-pin", extra_env={marker: "1"},
                     answers=[("PIN baru", PIN), ("Ulangi", PIN)])
    assert rc == 2 and "ditolak" in out
    assert not (sb.al / "approval_pin.json").exists()


def test_set_pin_needs_terminal(sb):
    r = sb.cli("set-pin")
    assert r.returncode == 2
    assert not (sb.al / "approval_pin.json").exists()


def test_set_pin_stores_scrypt_hash_0600_and_change_needs_old_pin(sb):
    rc, out = sb.pty("set-pin", answers=[("PIN baru", PIN), ("Ulangi", PIN)])
    assert rc == 0, out
    f = sb.al / "approval_pin.json"
    rec = json.loads(f.read_text())
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert rec["algo"] == "scrypt" and len(rec["salt"]) == 32 and PIN not in f.read_text()

    rc, out = sb.pty("set-pin", answers=[("PIN lama", "99999999"), ("PIN baru", "11111111"), ("Ulangi", "11111111")])
    assert rc == 2 and "PIN salah" in out
    assert json.loads(f.read_text())["hash"] == rec["hash"]

    rc, out = sb.pty("set-pin", answers=[("PIN lama", PIN), ("PIN baru", "1234567"), ("Ulangi", "1234567")])
    assert rc == 2 and "digit" in out  # too short

    rc, out = sb.pty("set-pin", answers=[("PIN lama", PIN), ("PIN baru", "11223344"), ("Ulangi", "11223344")])
    assert rc == 0, out
    sb.py("from agentloadout import pin; pin.verify('11223344')")


# ------------------------------------------------------------------ web approve
class Web:
    def __init__(self, sb):
        self.sb = sb
        self.log = sb.root / "ui.log"
        self.proc = subprocess.Popen([sys.executable, "-m", "agentloadout.cli", "ui"], env=sb.env,
                                     stdout=open(self.log, "w"), stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{sb.port}/api/health", timeout=1)
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("UI did not start")

    def approve(self, pid, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.sb.port}/api/plans/{pid}/approve",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Agentloadout": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)


@pytest.fixture
def web(cat):
    assert cat.port != 8787
    w = Web(cat)
    yield w
    w.stop()


def test_web_approve_needs_pin_even_without_password(cat, web):
    pid = cat.plan()
    code, res = web.approve(pid, {"apply": True})
    assert code == 409 and "set-pin" in res["error"]  # PIN not set yet
    cat.set_pin(PIN)
    code, res = web.approve(pid, {"apply": True})
    assert code == 403
    code, res = web.approve(pid, {"apply": True, "pin": "00000000"})
    assert code == 403 and "PIN salah" in res["error"]
    assert status(cat, pid) == "pending"
    code, res = web.approve(pid, {"apply": True, "pin": PIN})
    assert code == 200, res
    p = cat.plan_json(pid)
    assert p["status"] == "applied" and p["approved_by"] == "pin:web"
    assert PIN not in web.log.read_text() and PIN not in json.dumps(p)


def test_web_approve_locks_after_five_wrong_pins(cat, web):
    cat.set_pin(PIN)
    pid = cat.plan()
    codes = [web.approve(pid, {"pin": f"1000000{i}"})[0] for i in range(5)]
    assert codes == [403, 403, 403, 403, 429], codes
    code, res = web.approve(pid, {"pin": PIN})
    assert code == 429 and "dikunci" in res["error"]
    assert status(cat, pid) == "pending"
    # the lock is shared with the terminal
    rc, out = cat.pty("approve", pid, answers=[("PIN persetujuan", PIN)])
    assert rc == 2 and "dikunci" in out
    log = web.log.read_text()
    assert PIN not in log and "10000004" not in log


# ------------------------------------------------------------------ the lock cannot be reset on its own (W1)
def _lock_out(sb, pid):
    for i in range(5):
        sb.py(f"from agentloadout import pin\ntry: pin.verify('3000000{i}')\nexcept pin.PinError: pass")
    rec = json.loads((sb.al / "approval_pin.json").read_text())
    assert rec["fails"] == 5 and rec["locked_until"] > time.time()
    assert not (sb.al / "approval_pin.fails").exists()  # no separate counter file to delete


def test_corrupted_pin_record_keeps_approvals_locked(cat, web):
    cat.set_pin(PIN)
    pid = cat.plan()
    _lock_out(cat, pid)
    (cat.al / "approval_pin.json").write_text('{"algo": "scrypt", "fails": 0')  # an agent "resets" the counter
    rc, out = cat.pty("approve", pid, answers=[("PIN persetujuan", PIN)])
    assert rc == 2 and "tidak valid" in out, out
    code, res = web.approve(pid, {"pin": PIN})
    assert code == 429 and "tidak valid" in res["error"]
    rc, out = cat.pty("set-pin", answers=[("PIN lama", PIN), ("PIN baru", "55556666"), ("Ulangi", "55556666")])
    assert rc == 2  # cannot set a new PIN over a broken record without deleting it
    assert status(cat, pid) == "pending"


def test_deleting_the_counter_deletes_the_pin(cat, web):
    cat.set_pin(PIN)
    pid = cat.plan()
    _lock_out(cat, pid)
    (cat.al / "approval_pin.json").unlink()
    rc, out = cat.pty("approve", pid, answers=[("PIN persetujuan", PIN)])
    assert rc == 2 and "set-pin" in out
    code, res = web.approve(pid, {"pin": PIN})
    assert code == 409
    assert status(cat, pid) == "pending"


def test_cli_and_web_failures_are_counted_together(cat, web):
    """Three CLI processes and two web requests fail at the same moment: no count may be lost."""
    cat.set_pin(PIN)
    pid = cat.plan()
    code = "from agentloadout import pin\ntry: pin.verify('11111111')\nexcept pin.PinError as e: print(type(e).__name__)"
    procs = [subprocess.Popen([sys.executable, "-c", code], env=cat.env, stdout=subprocess.PIPE, text=True) for _ in range(3)]
    results = []
    threads = [threading.Thread(target=lambda: results.append(web.approve(pid, {"pin": "22222222"})[0])) for _ in range(2)]
    for t in threads:
        t.start()
    outs = [p.communicate(timeout=60)[0].strip() for p in procs]
    for t in threads:
        t.join(60)
    rec = json.loads((cat.al / "approval_pin.json").read_text())
    assert rec["fails"] == 5 and rec["locked_until"] > time.time(), (rec, outs, results)
    assert sorted(results) in ([403, 403], [403, 429]) and outs.count("PinLocked") + results.count(429) == 1
    assert status(cat, pid) == "pending"
