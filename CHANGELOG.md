# Changelog

All notable changes to Agent Loadout. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow `vMAJOR.MINOR.PATCH` git tags. `agentloadout upgrade` shows the section of the new version
(read from this file at the release commit), so every release needs an entry here (see RELEASING.md).

## [0.5.0] - 2026-11-16

### Added
- Interactive menu: `agentloadout` without arguments (or `agentloadout menu`) in a terminal shows the
  AGENTLOADOUT banner, a status line and 12 numbered choices (plans, web, refresh, status, token, PIN,
  password, setup, upgrade, backup and restore, item updates, uninstall). Every choice runs the normal
  command with its normal checks. Only for a human at a terminal; scripts and agents get the old usage +
  exit 2. Idle limit 15 minutes. `AGENTLOADOUT_NO_MENU=1` turns it off.

### Changed
- More agent markers: `GEMINI_CLI`, `GEMINI_CLI_IN_SHELL`, `OPENCODE`, `CURSOR_AGENT` (they also block
  `set-pin` and `set-password`); `AGENT` only keeps the menu away.
- The CLI returns exit codes instead of exiting inside each command; `restore` no longer changes `sys.argv`.
- The MCP tool `open_dashboard` and the menu share one way to start the web UI.

### Fixed
- Ctrl-C at a question (`Lanjutkan?`), at the PIN prompt of `approve`, or in `ui` ends with "Dibatalkan."
  instead of a traceback (exit 130).

## [0.4.0] - 2026-11-16

### Added
- Settings menu (gear icon) with a GitHub token section: save & test, test, remove; shows the active
  source, token kind, last 4 characters and expiry. CLI: `agentloadout set-token [--from-env|--test|--clear]`.
- Catalog fetch indicator: stage, progress, start time, estimate, quota waits; summary after a run, red
  banner when a run failed, notes for unchecked items, exhausted quota or a missing token.
- "Belum diperiksa" label and filter for items GitHub's quota did not let us examine.
- "Perbarui sekarang" button (laptop or logged-in non-Funnel session, 15-minute gap).
- `agentloadout upgrade [--check] [--to vX.Y.Z] [--yes] [--pre]`: upgrade an `uv tool` install to the
  newest release tag, installed by commit SHA, verified, rolled back on failure. Downgrades below 0.3.0
  are refused; downgrades need a human at a terminal. `agentloadout update` points to `upgrade`.
- Notice of a newer release in the web UI, `agentloadout where` and MCP responses (from a cached check).

### Changed
- Token order: `GITHUB_TOKEN` > `GITHUB_TOKEN_FILE` > token saved in Agent Loadout > `token_file` in config.
- Every refresh path (MCP, web, CLI, daily routine) takes the same lock; the routine skips (exit 0) when
  another refresh runs.

### Fixed
- A failed refresh no longer shows "Data katalog sudah diperbarui".
- "Running" comes from the refresh lock, so a killed refresh no longer shows as running for 40 minutes.
- A second backup on the same day no longer overwrites the first one.
- The web log no longer records query strings.
- Saving the token is atomic.

## [0.3.0] - 2026-11-15

### Added
- Hashed web password (`agentloadout set-password [--reset|--off]`), Security page, log out everywhere.
- Forgotten password/PIN resets with a visible trail; a PIN reset revokes approved plans.
- OpenBot setup prints the agent instructions; honest `awaiting_agent` status with `report_agent_step`.

### Security
- Outside access needs the web password; proxy identity headers grant nothing; Funnel is view-only.

## [0.2.0] - 2026-11-14

### Added
- Install by an AI agent from GitHub (`uv tool install git+…`), no PyPI.
- Approval PIN for every install or removal plan, in the terminal and the web UI.
- Catalog refresh in its own process with a kernel lock.

## [0.1.0] - 2026-11-14

### Added
- MCP server, CLI and web catalog of trending MCP servers, agent skills and agent apps.
