# Agent Loadout

**Discover, vet, and equip trending MCP servers, agent skills, and AI-agent apps — for any agent harness.**

Agent Loadout is a local MCP server, CLI, and web catalog. It tracks what is trending on GitHub and the
official MCP registry, labels the risks, and installs MCP servers and skills into the harnesses you use:
**Claude Code, Codex CLI, Cursor, Gemini CLI, OpenCode, and OpenBot** — only after you approve.

## Install with your AI agent

Paste this to your agent (Claude Code, Codex, Cursor, Gemini CLI, OpenCode):

> Install Agent Loadout from https://github.com/BagusFary/agentloadout following its README.

**Prerequisites:** [uv](https://docs.astral.sh/uv/) and `git`.

**1. Install and register** (the agent can run this; nothing here needs a person):

```bash
uv tool install "git+https://github.com/BagusFary/agentloadout@v0.2.0"
"$(uv tool dir --bin)/agentloadout" setup --yes
```

`setup` detects your harnesses, registers the `agentloadout` MCP server with the absolute path of the
installed program, and installs a short guide skill so agents know the safe workflow. Without a terminal,
`setup` and `uninstall` need `--yes`; otherwise they stop with exit code 2. Use
`setup --yes --to claude-code,codex` to pick harnesses.

**2. GitHub token (optional, recommended).** A token without scopes raises the GitHub API limit from 60
to 5,000 requests per hour. Your harness does not pass the agent's shell variables to MCP servers, so
store it once:

```bash
GITHUB_TOKEN=... "$(uv tool dir --bin)/agentloadout" setup --yes --token-from-env
```

The token is written to `<data folder>/github_token` (mode 0600) and is never printed.

**3. Restart your harness** so it starts the MCP server: Claude Code, Codex, Gemini CLI and OpenCode —
start a new session; Cursor — reload the window.

**4. The user sets an approval PIN, once, in their own terminal** (not through the agent):

```bash
agentloadout set-pin      # or "$(uv tool dir --bin)/agentloadout" set-pin
```

Every install or removal plan needs this PIN to be approved, in the terminal or in the web UI.

> **Agents: never approve a plan.** Do not run `agentloadout approve` or `set-pin`, do not call the
> approval web API, do not edit files in the Agent Loadout data folder, do not use a pseudo-terminal,
> and never ask for the PIN. Create the plan and ask the user to approve it.

**First catalog.** The catalog is fetched in the background the first time an agent uses it and refreshed
when it is older than 24 hours. FIRST_CATALOG_NUMBERS

**Uninstall:**

```bash
"$(uv tool dir --bin)/agentloadout" uninstall --yes   # MCP entries + the guide skill setup installed
uv tool uninstall agentloadout                         # the program
```

Config backups and the data folder (catalog, plans, PIN) stay; `uninstall` prints where. A skill of your
own that happens to be called `agentloadout` is never touched.

**Note:** there is no PyPI release. The package called `agentloadout` on PyPI belongs to a different
project; do not install it from PyPI (with pip, uv or uvx).

## Use it from your agent

> "Find an MCP server for Postgres in Agent Loadout and install it to Claude Code and Codex."

The agent will search, inspect the repo, and create a **plan**. You approve the plan with your PIN in
the local web UI or in your own terminal, then the agent applies it.

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
agentloadout approve <id>  # approve (interactive terminal + PIN)
agentloadout set-pin       # set or change the approval PIN
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

- **Agents propose, humans approve with a PIN.** `apply_plan` refuses plans that are not approved, older
  than one hour, or modified after approval. Approval needs the approval PIN (set with
  `agentloadout set-pin`, stored as a salted scrypt hash, mode 0600) — in the web UI or with
  `agentloadout approve` in an interactive terminal, never through the MCP connection. The web UI asks
  for the PIN even when it has no password. Five wrong PINs lock approvals for 15 minutes.
  `set-pin` refuses to run inside an agent session (`CLAUDECODE`, `AI_AGENT`, `CLAUDE_CODE_SESSION_ID`,
  `CODEX_SESSION_ID`, `CODEX_THREAD_ID`) and changing the PIN needs the old one. The plan records how it
  was approved (`approved_by: pin:cli` or `pin:web`).
- **Inspect before install.** `inspect_repo` clones repositories into a scratch folder and never runs
  them. It flags download-and-run commands, install scripts, credential access, obfuscation, and prompt
  injection text. It is pattern-based evidence, not a guarantee.
- **Pinned versions.** npm and PyPI packages are pinned to the version that was reviewed.
- **No placeholder secrets.** Missing API keys are reported, never written as dummy values.
- **Local web UI.** Binds to `127.0.0.1`, rejects cross-site requests, and checks the Host header.
- **Limits.** Everything runs as your user, so an agent with unrestricted shell access can still bypass
  local controls on purpose: it can edit a plan file (the approval record is hashed, but the hash is not
  secret and can be recomputed), call the Python API, delete the PIN file and set a new PIN through a
  pseudo-terminal with the agent variables removed, or edit harness configs directly. None of these is a
  documented one-step path, and each is a deliberate file change your harness shows you. Keep your
  harness' own command approval prompts turned on and never tell an agent your PIN.

## Configuration

`agentloadout where` shows the data folder (Linux `~/.local/share/agentloadout`, macOS
`~/Library/Application Support/agentloadout`, Windows `%APPDATA%\agentloadout`). Optional
`config.toml` there:

```toml
[github]
# token_file = "/path/to/github_token"   # optional; default: env GITHUB_TOKEN, then <data folder>/github_token

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
uv run pytest                                   # unit + install tests (some need network: uv, GitHub deps)
uv run python tests/e2e_mcp.py /path/to/catalog.db   # end-to-end MCP test on a copy of a catalog
uv run agentloadout ui
```

## License

MIT
