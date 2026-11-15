"""OpenBot setup and honest plan status (BRIEF-03 H9, H10, K5, K6)."""
import datetime as dt
import json

import pytest

PIN = "24681357"


def mcp(sb, call: str):
    r = sb.py("import json\nfrom agentloadout import mcp_server as m\n"
              f"print(json.dumps(m.{call}))")
    return json.loads(r.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------ setup / uninstall
def test_setup_inside_openbot_touches_only_openbot(sb):
    claude_cfg = sb.home / ".claude.json"
    claude_cfg.write_text('{"mcpServers": {}}')
    (sb.home / ".cursor").mkdir()
    (sb.home / ".config" / "OpenBot").mkdir(parents=True)
    r = sb.cli("setup", "--yes", extra_env={"OPENBOT_AGENT_PROCESS": "1"})
    assert r.returncode == 0, r.stderr
    assert "OpenBot" in r.stdout.splitlines()[0]  # listed as detected
    assert "openbot.save_mcp_server" in r.stdout and "✗" not in r.stdout
    block = json.loads(next(line.strip() for line in r.stdout.splitlines() if line.strip().startswith('{"tool"')))
    assert block["arguments"]["name"] == "agentloadout" and block["arguments"]["args"] == ["mcp"]
    assert "openbot.create_skill" in r.stdout and "skill/agentloadout" in r.stdout
    assert claude_cfg.read_text() == '{"mcpServers": {}}'
    assert not (sb.home / ".cursor" / "mcp.json").exists() and not (sb.home / ".claude" / "skills").exists()
    assert "Server settings" not in r.stdout


def test_setup_to_openbot_prints_instructions(sb):
    r = sb.cli("setup", "--yes", "--to", "openbot")
    assert r.returncode == 0 and "ℹ OpenBot" in r.stdout and "openbot.save_mcp_server" in r.stdout and "✗" not in r.stdout


def test_uninstall_prints_openbot_instructions(sb):
    (sb.home / ".config" / "OpenBot").mkdir(parents=True)
    r = sb.cli("uninstall", "--yes")
    assert r.returncode == 0 and "openbot.remove_mcp_server" in r.stdout
    r = sb.cli("uninstall", "--yes", extra_env={"OPENBOT_AGENT_PROCESS": "1"})
    assert r.returncode == 0 and "openbot.remove_mcp_server" in r.stdout


# ------------------------------------------------------------------ awaiting_agent -> report_agent_step
@pytest.fixture
def cat(sb):
    sb.catalog()
    (sb.home / ".cursor").mkdir()
    sb.set_pin(PIN)
    return sb


def applied(sb, targets):
    pid = sb.plan(targets=targets)
    sb.approve(pid, PIN)
    return pid, mcp(sb, f"apply_plan({pid!r})")


def test_openbot_plan_waits_for_the_agent_then_applies(cat):
    pid, out = applied(cat, ["openbot"])
    assert out["status"] == "awaiting_agent" and "report_agent_step" in out["next"]
    assert out["results"][0]["ok"] is None and out["results"][0]["agent_instructions"]["tool"] == "openbot.save_mcp_server"
    st = mcp(cat, f"plan_status({pid!r})")
    assert st["status"] == "awaiting_agent" and st["label"] == "Menunggu agen OpenBot"
    assert mcp(cat, "list_installed()")["installed"] == []  # not counted as installed yet
    rep = mcp(cat, f"report_agent_step({pid!r}, 'openbot', True, 'list_mcp_servers shows demo')")
    assert rep["status"] == "applied" and rep["results"][0]["ok"] is True
    assert [(x["item_id"], x["harness"]) for x in mcp(cat, "list_installed()")["installed"]] == [("acme/demo-mcp", "openbot")]
    again = mcp(cat, f"report_agent_step({pid!r}, 'openbot', True, '')")
    assert "error" in again


def test_failed_and_partial_reports(cat):
    pid, _ = applied(cat, ["openbot"])
    assert mcp(cat, f"report_agent_step({pid!r}, 'openbot', False, 'save_mcp_server error')")["status"] == "failed"
    pid2, out = applied(cat, ["cursor", "openbot"])
    assert out["status"] == "awaiting_agent"
    assert "demo" in (cat.home / ".cursor" / "mcp.json").read_text()
    assert mcp(cat, f"report_agent_step({pid2!r}, 'openbot', False, 'no')")["status"] == "partial"
    assert "error" in mcp(cat, f"report_agent_step({pid2!r}, 'cursor', True, '')")


def test_no_report_within_an_hour_is_incomplete(cat):
    pid, _ = applied(cat, ["openbot"])
    f = cat.al / "plans" / f"{pid}.json"
    p = json.loads(f.read_text())
    p["applied_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    f.write_text(json.dumps(p))
    st = mcp(cat, f"plan_status({pid!r})")
    assert st["status"] == "incomplete" and "1 jam" in st["label"]
    web = cat.ui()
    plans = web.request("GET", "/api/plans")["json"]["plans"]
    assert [x["status"] for x in plans if x["id"] == pid] == ["incomplete"]
    late = mcp(cat, f"report_agent_step({pid!r}, 'openbot', True, 'late but done')")
    assert late["status"] == "applied"


def test_report_is_not_an_approval(cat):
    pid = cat.plan(targets=["openbot"])
    out = mcp(cat, f"report_agent_step({pid!r}, 'openbot', True, '')")
    assert "error" in out and cat.plan_json(pid)["status"] == "pending"
