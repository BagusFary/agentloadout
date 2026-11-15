"""Forgotten PIN (BRIEF-03 H11): set-pin --reset, PIN gen on approvals, revoked plans, visible trail."""
import json

import pytest

from conftest import AGENT_MARKERS

PIN = "24681357"
NEW = "97531864"


@pytest.fixture
def cat(sb):
    sb.catalog()
    (sb.home / ".cursor").mkdir()
    return sb


def apply(sb, pid):
    return sb.py(f"from agentloadout import plans; plans.apply({pid!r})", check=False)


def lock_out(sb):
    for i in range(5):
        sb.py(f"from agentloadout import pin\ntry: pin.verify('3000000{i}')\nexcept pin.PinError: pass")


def test_reset_needs_typing_reset(cat):
    cat.set_pin(PIN)
    before = (cat.al / "approval_pin.json").read_text()
    rc, out = cat.pty("set-pin", "--reset", answers=[("Ketik RESET", "reset"), ("PIN baru", NEW), ("Ulangi", NEW)])
    assert rc == 1 and (cat.al / "approval_pin.json").read_text() == before


@pytest.mark.parametrize("marker", AGENT_MARKERS)
def test_reset_refused_inside_agent(cat, marker):
    cat.set_pin(PIN)
    rc, out = cat.pty("set-pin", "--reset", extra_env={marker: "1"},
                      answers=[("Ketik RESET", "RESET"), ("PIN baru", NEW), ("Ulangi", NEW)])
    assert rc == 2 and "ditolak" in out


@pytest.mark.parametrize("damage", ["locked", "corrupt", "missing"])
def test_reset_works_on_locked_corrupt_or_missing_record(cat, damage):
    cat.set_pin(PIN)
    pid = cat.plan()
    cat.approve(pid, PIN)
    f = cat.al / "approval_pin.json"
    if damage == "locked":
        lock_out(cat)
    elif damage == "corrupt":
        f.write_text('{"algo": "scrypt", "gen": "x"')
    else:
        f.unlink()
    rc, out = cat.pty("set-pin", "--reset", answers=[("Ketik RESET", "RESET"), ("PIN baru", NEW), ("Ulangi", NEW)])
    assert rc == 0, out
    rec = json.loads(f.read_text())
    assert rec["set_by"] == "cli-reset" and rec["reset_at"] and rec["fails"] == 0
    assert rec["gen"] != cat.plan_json(pid)["pin_gen"]
    assert NEW not in f.read_text()
    p = cat.plan_json(pid)
    assert p["status"] == "revoked" and p["revoked_reason"] == "PIN direset" and pid in out
    r = apply(cat, pid)
    assert r.returncode != 0 and "belum disetujui" in r.stderr
    cat.py(f"from agentloadout import pin; pin.verify({NEW!r})")


def test_changed_pin_invalidates_earlier_approvals(cat):
    """Even if the plan was not revoked (direct API change), apply refuses an approval from an older PIN gen."""
    cat.set_pin(PIN)
    pid = cat.plan()
    cat.approve(pid, PIN)
    assert cat.plan_json(pid)["pin_gen"] == json.loads((cat.al / "approval_pin.json").read_text())["gen"]
    cat.py(f"from agentloadout import pin; pin.set_pin({NEW!r}, {PIN!r})")
    r = apply(cat, pid)
    assert r.returncode != 0 and "PIN persetujuan sudah diganti" in r.stderr
    assert not (cat.home / ".cursor" / "mcp.json").exists()


def test_cli_pin_change_revokes_approved_plans(cat):
    cat.set_pin(PIN)
    pid = cat.plan()
    cat.approve(pid, PIN)
    rc, out = cat.pty("set-pin", answers=[("PIN lama", PIN), ("PIN baru", NEW), ("Ulangi", NEW)])
    assert rc == 0, out
    assert cat.plan_json(pid)["status"] == "revoked"


def test_deleting_the_pin_blocks_apply(cat):
    cat.set_pin(PIN)
    pid = cat.plan()
    cat.approve(pid, PIN)
    (cat.al / "approval_pin.json").unlink()
    r = apply(cat, pid)
    assert r.returncode != 0 and "PIN persetujuan" in r.stderr


def test_approval_race_with_reset_is_refused(cat):
    """verify() returns the gen it matched; a reset between verify and approve leaves the plan unusable."""
    cat.set_pin(PIN)
    pid = cat.plan()
    cat.py("from agentloadout import pin, plans\n"
           f"gen = pin.verify({PIN!r})\n"
           f"pin.reset_pin({NEW!r})\n"
           f"plans.approve({pid!r}, 'pin:test', pin_gen=gen)")
    r = apply(cat, pid)
    assert r.returncode != 0 and "sudah diganti" in r.stderr


def test_reset_is_visible_in_where_and_web(cat):
    cat.set_pin(PIN)
    rc, _ = cat.pty("set-pin", "--reset", answers=[("Ketik RESET", "RESET"), ("PIN baru", NEW), ("Ulangi", NEW)])
    assert rc == 0
    w = cat.cli("where").stdout
    assert "PIN direset pada" in w and "by cli-reset" in w
    web = cat.ui()
    plans = web.request("GET", "/api/plans")["json"]
    assert plans["pin_reset_recent"] is True and plans["pin_reset_at"]
    sec = web.request("GET", "/api/security")["json"]
    assert sec["pin"]["reset_at"] and sec["pin"]["set_by"] == "cli-reset" and "hash" not in json.dumps(sec)
    assert web.request("GET", "/api/items")["json"]["meta"]["pin_reset_recent"] is True


def test_old_v020_plans_and_records_still_work(cat):
    """A v0.2.0 PIN record has no gen/set_by; a plan approved under v0.2.0 has no pin_gen: both count as gen 0."""
    cat.set_pin(PIN)
    f = cat.al / "approval_pin.json"
    rec = json.loads(f.read_text())
    for k in ("gen", "set_by", "reset_at"):
        rec.pop(k)
    f.write_text(json.dumps(rec))
    pid = cat.plan()
    cat.py("import datetime as dt\nfrom agentloadout import plans\n"
           f"p = plans.load({pid!r})\n"
           "p.update(status='approved', approved_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), approved_by='pin:cli')\n"
           "p['approval_hash'] = plans._approval_hash(p)\nplans.save(p)")
    assert "pin_gen" not in cat.plan_json(pid)
    r = apply(cat, pid)
    assert r.returncode == 0, r.stderr
    assert cat.plan_json(pid)["status"] == "applied"
