# Agent Loadout

**Discover, vet, and equip trending MCP servers, agent skills, and AI-agent apps — for any agent harness.**

Agent Loadout is a local MCP server, CLI, and web catalog. It tracks what is trending on GitHub and the
official MCP registry, labels the risks, and installs MCP servers and skills into the harnesses you use:
**Claude Code, Codex CLI, Cursor, Gemini CLI, OpenCode, and OpenBot** — only after you approve.

## Install (once)

```bash
uvx agentloadout setup
```

`setup` detects your harnesses, registers the `agentloadout` MCP server in the ones you pick, installs a
short guide skill so agents know the safe workflow, and optionally stores a GitHub token (no scopes
needed; it raises the API limit from 60 to 5,000 requests per hour). Restart your harness afterwards.

The catalog is fetched the first time an agent uses it (about 5 minutes) and refreshed automatically
when it is older than 24 hours. There is no background service to manage.

To remove everything: `uvx agentloadout uninstall`.

## Use it from your agent

> "Find an MCP server for Postgres in Agent Loadout and install it to Claude Code and Codex."

The agent will search, inspect the repo, and create a **plan**. You approve the plan in the local web
UI or in your own terminal, then the agent applies it.

| MCP tool | What it does |
|---|---|
| `search_catalog` | Search MCP servers, skills, and apps (trending, stars, newest) |
| `get_item` | Details, install configs, env vars, skills inside a repo |
| `inspect_repo` | Clone without running and flag risky patterns |
| `list_harnesses` | Harnesses detected on this computer |
| `plan_install` / `plan_remove` | Create a plan — changes nothing yet |
| `plan_status` / `apply_plan` | Apply a plan **after a human approved it** |
| `list_installed` | What is installed where |
| `open_dashboard` | Start the local web UI and return its address |

## Use it yourself

```bash
agentloadout ui            # local web catalog at http://127.0.0.1:8787
agentloadout plans         # plans waiting for approval
agentloadout approve <id>  # approve (interactive terminal only)
agentloadout harnesses     # detected harnesses and config paths
agentloadout where         # data, config and backup folders
```

## Supported harnesses

| Harness | MCP servers | Skills |
|---|---|---|
| Claude Code | `claude mcp add-json --scope user` | `~/.claude/skills/` |
| Codex CLI | `codex mcp add` (or `~/.codex/config.toml`) | `~/.agents/skills/` |
| Cursor | `~/.cursor/mcp.json` | `~/.agents/skills/` |
| Gemini CLI | `gemini mcp add -s user` (or `~/.gemini/settings.json`) | `~/.agents/skills/` |
| OpenCode | `opencode mcp add --global` (or `opencode.json`) | `~/.agents/skills/` |
| OpenBot | via an OpenBot agent (`openbot.*` tools) | via an OpenBot agent |

Every config file is backed up before it changes; removing an item restores it.

## Safety model

- **Agents propose, humans approve.** `apply_plan` refuses plans that are not approved, older than one
  hour, or modified after approval. Approval happens in the web UI or with `agentloadout approve` in an
  interactive terminal — not through the MCP connection.
- **Inspect before install.** `inspect_repo` clones repositories into a scratch folder and never runs
  them. It flags download-and-run commands, install scripts, credential access, obfuscation, and prompt
  injection text. It is pattern-based evidence, not a guarantee.
- **Pinned versions.** npm and PyPI packages are pinned to the version that was reviewed.
- **No placeholder secrets.** Missing API keys are reported, never written as dummy values.
- **Local web UI.** Binds to `127.0.0.1`, rejects cross-site requests, and checks the Host header.
- **Limits.** An agent with unrestricted shell access can bypass any local control by editing files
  directly. Keep your harness' own command approval prompts turned on.

## Configuration

`agentloadout where` shows the data folder (Linux `~/.local/share/agentloadout`, macOS
`~/Library/Application Support/agentloadout`, Windows `%APPDATA%\agentloadout`). Optional
`config.toml` there:

```toml
[github]
token_file = "~/.config/secrets/github_token"   # or env GITHUB_TOKEN

[web]
host = "127.0.0.1"
port = 8787
password_file = ""          # required if you expose the UI beyond localhost
idle_shutdown_minutes = 30  # on-demand UI stops when idle

[catalog]
refresh_hours = 24
```

## Development

```bash
uv run agentloadout ui
uv run python tests/e2e_mcp.py ~/.local/share/agentloadout/catalog.db
```

## License

MIT
