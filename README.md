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
uv tool install "git+https://github.com/BagusFary/agentloadout@v0.3.0"
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

The PIN has at least 8 digits; a longer one is safer (see *Limits*). Every install or removal plan needs
this PIN to be approved, in the terminal or in the web UI.

> **Agents: never approve a plan.** Do not run `agentloadout approve` or `set-pin`, do not call the
> approval web API, do not edit files in the Agent Loadout data folder, do not use a pseudo-terminal,
> and never ask for the PIN. Create the plan and ask the user to approve it.

**First catalog.** The catalog is fetched in the background the first time an agent uses it and refreshed
when it is older than 24 hours.

How long that takes:

| | First run | Complete data |
|---|---|---|
| With a GitHub token | about 6 minutes (measured with v0.1.0: 374 s, 630 items) | after the first run |
| Without a token | about 6 minutes (measured: 342 s, 630 items) | estimate: about 5 more hours of use |

Without a token, GitHub allows 60 API requests per hour. READMEs and package files are then read from
raw.githubusercontent.com, so every MCP server is complete after the first run (measured: 290 of 290).
Finding the skills inside a repository needs the API, so in the measured run 281 skill and app repositories
were saved as **not checked yet** (`checked: false` in `search_catalog`) instead of with empty data. While
an agent keeps using the catalog, a new run starts when the last one is over an hour old and checks about
60 more; the estimate above follows from that and was not measured. A token avoids the wait.

**Uninstall:**

```bash
"$(uv tool dir --bin)/agentloadout" uninstall --yes   # MCP entries + the guide skill setup installed
uv tool uninstall agentloadout                         # the program
```

Config backups and the data folder (catalog, plans, PIN) stay; `uninstall` prints where. A skill of your
own that happens to be called `agentloadout` is never touched.

**Note:** there is no PyPI release. The package called `agentloadout` on PyPI belongs to a different
project; do not install it from PyPI (with pip, uv or uvx).

## OpenBot

OpenBot keeps its MCP servers and skills inside the app, so `setup` cannot write them; an OpenBot agent
does it with its own tools. Ask your OpenBot agent to install Agent Loadout following this README. Inside
an OpenBot agent (`OPENBOT_AGENT_PROCESS` is set), `setup --yes` without `--to` means `--to openbot` and
touches no other harness on the computer. It prints:

- the `openbot.save_mcp_server` call (name `agentloadout`, the absolute command from `uv tool install`);
- the guide skill folder for `openbot.create_skill` + `openbot.install_local_skill`.

The agent runs both, then checks with `openbot.list_mcp_servers`. `uninstall` prints the matching
`openbot.remove_mcp_server` call.

Plans with an OpenBot target do not pretend to be finished: after approval and `apply_plan` their status
is **awaiting_agent** ("Menunggu agen OpenBot"). The OpenBot agent runs the `agent_instructions` in the
result, checks with `openbot.list_mcp_servers`, and reports with the MCP tool
`report_agent_step(plan_id, harness="openbot", ok, detail)`; the plan then becomes `applied`, `failed`
or `partial`. Without a report within one hour it becomes **incomplete**. The report is bookkeeping, not
an approval: it cannot approve or apply anything.

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
agentloadout set-pin       # set or change the approval PIN       (--reset: forgot it)
agentloadout set-password  # web password, needed for your phone   (--reset: forgot it, --off: remove)
agentloadout harnesses     # detected harnesses and config paths
agentloadout where         # data, config and backup folders
```

## Open it from your phone (Akses dari HP)

The web UI listens on `127.0.0.1` only. To reach it from a phone, put an HTTPS proxy in front of it and
**set a web password first**, in your own terminal:

```bash
agentloadout set-password
```

Agent Loadout treats every request that is not plainly local as coming from outside: a proxy header
(`X-Forwarded-*`, `Forwarded`, `X-Real-IP`, `Tailscale-*`, `Cf-*`, `Ngrok-*`), a Host other than
`127.0.0.1` / `localhost` / `[::1]`, or a non-loopback peer. Outside requests without a password only get
a "set a password first" page. Once a password is set it is required on the laptop too.

| Way | Who can reach it | What it allows |
|---|---|---|
| `tailscale serve --bg 8787` | your tailnet devices (HTTPS) | log in with the password, then act; approving still needs the PIN |
| Tailscale Funnel / ngrok / Cloudflare | anyone on the internet who has the link | log in with the password; through Funnel the UI is view-only |

The `tailscale serve` and `tailscale funnel` syntax above is from `tailscale serve --help` of Tailscale
1.104.7 (`tailscale serve [--bg] <port>`; HTTPS is the default mode). Use the port shown by
`agentloadout where`.

Rules that stay the same from anywhere:

- Approving a plan always needs the approval PIN; a session cookie alone never approves.
- Identity headers such as `Tailscale-User-Login` grant nothing.
- The session cookie is named per instance (`al_sesi_<id>`), and marked `Secure` only when the request
  came over HTTPS, so plain-HTTP LAN access does not loop back to the login page.
- **Do not use a raw TCP tunnel** (`ssh -R`, `tailscale serve --tcp`, frp/bore in TCP mode): it forwards
  bytes unchanged, so an outsider can send `Host: 127.0.0.1` and look local. Use an HTTP(S) proxy.
- A reverse proxy on the same laptop (nginx, Caddy) counts as outside and needs the password.
- `agentloadout ui --host 0.0.0.0` prints a warning: on a LAN the password travels unencrypted.

The web UI's **Security** page (shield icon) shows the password and PIN status and when they were set or
reset, can log out every device, and can change an existing hashed password (old password required).
It cannot create or reset a password, and it cannot touch the PIN: those happen only in the terminal.

## Forgot the password or PIN

Both are reset by the human, in their own terminal on the laptop. Agents are refused (agent variables
such as `CLAUDECODE`, `CODEX_SESSION_ID`, `OPENBOT_AGENT_PROCESS`) and there is no web or MCP way.

```bash
agentloadout set-password --reset   # new web password without the old one; every device is logged out
agentloadout set-pin --reset        # type RESET, then a new PIN; works even when the PIN is locked or broken
```

A PIN reset is recorded: approved plans that were not applied yet become `revoked`, apply refuses any
approval made with an earlier PIN (each approval stores the PIN generation it was made with), and
"PIN direset pada <time>" shows in `agentloadout where` and as a banner in the web UI for 7 days. If you
did not reset it yourself, something on your account did: reset it again and check your agents. Changing
the PIN normally (`set-pin`, old PIN required) revokes approved plans the same way.

If the web UI says the port is busy: without `--port`, Agent Loadout picks the next free port (up to +10)
and `agentloadout where` shows it; with `--port` (or `AGENTLOADOUT_PORT`) it stops with a clear error.
`open_dashboard` only returns the address of its own instance.

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
  for the PIN even when it has no password. Five wrong PINs lock approvals for 15 minutes; the counter
  is stored in the same file as the PIN hash, so deleting it also deletes the PIN, and an unreadable
  record locks approvals.
  `set-pin` and `set-password` refuse to run inside an agent session (`CLAUDECODE`, `AI_AGENT`,
  `CLAUDE_CODE_SESSION_ID`, `CODEX_SESSION_ID`, `CODEX_THREAD_ID`, `OPENBOT_AGENT_PROCESS`) and changing
  either needs the old one (or `--reset`, which leaves a visible trail). The plan records how it was
  approved (`approved_by: pin:cli` or `pin:web`) and with which PIN generation.
- **Inspect before install.** `inspect_repo` clones repositories into a scratch folder and never runs
  them. It flags download-and-run commands, install scripts, credential access, obfuscation, and prompt
  injection text. It is pattern-based evidence, not a guarantee.
- **Pinned versions.** npm and PyPI packages are pinned to the version that was reviewed.
- **No placeholder secrets.** Missing API keys are reported, never written as dummy values.
- **Local web UI.** Binds to `127.0.0.1` and rejects cross-site requests. Anything that does not look
  plainly local needs the web password (a salted scrypt hash, mode 0600); see "Open it from your phone".
- **Limits.** Everything runs as your user, so an agent with unrestricted shell access can still bypass
  local controls on purpose: it can edit a plan file (the approval record is hashed, but the hash is not
  secret and can be recomputed), call the Python API, delete the PIN file and set a new PIN through a
  pseudo-terminal with the agent variables removed, or edit harness configs directly. None of these is a
  documented one-step path, and each is a deliberate file change your harness shows you. The resets
  (`set-pin --reset`, `set-password --reset`) are traces, not barriers: a program running as you could
  run them too, but the reset time stays visible in `where` and the web UI. Keep your harness' own
  command approval prompts turned on and never tell an agent your PIN or password.
- **The PIN hash can be guessed offline.** `approval_pin.json` is readable by your user, so a program
  running as you can copy it and try PINs without the 5-try limit. scrypt makes each try slow (measured: about
  0.19 s per try on one laptop core). Estimate: trying every 8-digit PIN takes about 220 core-days, so
  a machine with many cores, or a GPU, can still do it.
  Use 10 or more digits if agents run with broad shell access.

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
password_file = ""          # older installs: plain-text password file; `agentloadout set-password` replaces it
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
