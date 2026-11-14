"""Uninstall (H10): other MCP servers and the user's own `agentloadout` skill stay untouched."""
import json

USER_SKILL = "---\nname: agentloadout\ndescription: my own notes\n---\nmine\n"


def _harness_configs(sb):
    (sb.home / ".cursor").mkdir()
    (sb.home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}}}))
    (sb.home / ".codex").mkdir()
    (sb.home / ".codex" / "config.toml").write_text('[mcp_servers.other]\ncommand = "other"\nargs = []\n')


def _user_skill(sb, where):
    d = sb.home / where / "skills" / "agentloadout"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(USER_SKILL)
    return d


def test_uninstall_keeps_other_servers_and_user_skill(sb):
    _harness_configs(sb)
    user_claude = _user_skill(sb, ".claude")
    r = sb.cli("setup", "--yes", "--to", "cursor,codex")
    assert r.returncode == 0, r.stderr
    guide = sb.home / ".agents" / "skills" / "agentloadout"
    assert (guide / ".installed-by-agentloadout").exists()

    r = sb.cli("uninstall", "--yes")
    assert r.returncode == 0, r.stderr
    cursor = json.loads((sb.home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert sorted(cursor) == ["github"]
    codex = (sb.home / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.other]" in codex and "agentloadout" not in codex
    assert not guide.exists()
    assert (user_claude / "SKILL.md").read_text() == USER_SKILL
    assert "uv tool uninstall agentloadout" in r.stdout
    assert str(sb.al) in r.stdout and "config-backups" in r.stdout
    assert list((sb.al / "config-backups" / "skills").glob("*-removed-agents-agentloadout"))


def test_setup_does_not_overwrite_a_user_skill_named_agentloadout(sb):
    _harness_configs(sb)
    user_agents = _user_skill(sb, ".agents")
    r = sb.cli("setup", "--yes", "--to", "cursor")
    assert r.returncode == 0 and "dilewati" in r.stdout
    assert (user_agents / "SKILL.md").read_text() == USER_SKILL
    r = sb.cli("uninstall", "--yes")
    assert r.returncode == 0
    assert (user_agents / "SKILL.md").read_text() == USER_SKILL
