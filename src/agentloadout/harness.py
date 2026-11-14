"""Adapters that install MCP servers and skills into agent harnesses.

Each adapter knows how to detect its harness and how to add/remove a user-level MCP server.
Rules:
  * prefer the harness' own CLI (claude/codex/gemini/opencode `mcp add`), fall back to editing
    its config file only when the format is simple and safe;
  * back up every config file before it changes (paths.config_backups_dir());
  * never overwrite a file we cannot parse — report it instead.

MCP spec format used everywhere:
  stdio  {"command": "npx", "args": [...], "env": {"KEY": "value"}}
  remote {"url": "https://...", "headers": {"Authorization": "Bearer ..."}}

Skills are folders with SKILL.md. Two user folders cover the supported harnesses:
  ~/.claude/skills  -> Claude Code (also read by Cursor and OpenCode)
  ~/.agents/skills  -> Codex, Cursor, Gemini CLI, OpenCode
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

HOME = Path.home()
EXE = ".exe" if os.name == "nt" else ""


class HarnessError(RuntimeError):
    pass


def which(name: str) -> str | None:
    return shutil.which(name) or (shutil.which(name + ".cmd") if os.name == "nt" else None)


def backup_file(harness: str, f: Path) -> str | None:
    if not f.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = paths.config_backups_dir() / harness / f"{stamp}-{f.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, dest)
    return str(dest)


def run(cmd: list[str], timeout=60) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HarnessError(f"{cmd[0]} gagal dijalankan: {e}") from e
    if p.returncode != 0:
        raise HarnessError(f"`{' '.join(cmd[:4])} …` gagal: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def _load_json(f: Path) -> dict:
    if not f.exists():
        return {}
    text = f.read_text()
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError as e:
        raise HarnessError(f"{f} bukan JSON murni (mungkin ada komentar); tidak diubah otomatis: {e}") from e


def _save_json(f: Path, data: dict):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, f)


@dataclass
class Change:
    """One planned change, shown to the human before approval."""
    harness: str
    action: str            # add | remove | enable | disable
    target: str            # file path or "cli"
    detail: str            # human-readable summary
    command: list[str] = field(default_factory=list)


class Adapter:
    id = ""
    name = ""
    skills_dir: Path | None = None
    supports_mcp_toggle = False

    def detect(self) -> bool:
        raise NotImplementedError

    def config_file(self) -> Path | None:
        return None

    def list_mcp(self) -> dict:
        return {}

    def plan_add(self, name: str, spec: dict) -> Change:
        raise NotImplementedError

    def plan_remove(self, name: str) -> Change:
        raise NotImplementedError

    def apply(self, ch: Change, name: str, spec: dict | None) -> str | None:
        """Apply a change. Returns the backup path of the edited config, if any."""
        raise NotImplementedError

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "detected": self.detect(),
                "config": str(self.config_file()) if self.config_file() else None,
                "skills_dir": str(self.skills_dir) if self.skills_dir else None,
                "mcp_toggle": self.supports_mcp_toggle}


# --------------------------------------------------------------------------- Claude Code
class ClaudeCode(Adapter):
    id, name = "claude-code", "Claude Code"
    skills_dir = HOME / ".claude" / "skills"

    def config_file(self):
        base = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(base) / ".claude.json" if base else HOME / ".claude.json"

    def detect(self):
        return bool(which("claude")) or self.config_file().exists()

    def list_mcp(self):
        try:
            return dict(_load_json(self.config_file()).get("mcpServers", {}))
        except HarnessError:
            return {}

    def plan_add(self, name, spec):
        cmd = ["claude", "mcp", "add-json", "--scope", "user", name, json.dumps(_claude_spec(spec))]
        return Change(self.id, "add", "cli", f"claude mcp add-json --scope user {name}", cmd)

    def plan_remove(self, name):
        cmd = ["claude", "mcp", "remove", name, "--scope", "user"]
        return Change(self.id, "remove", "cli", f"claude mcp remove {name} --scope user", cmd)

    def apply(self, ch, name, spec):
        bk = backup_file(self.id, self.config_file())
        if not which("claude"):
            raise HarnessError("perintah `claude` tidak ditemukan di PATH")
        run(ch.command)
        return bk


def _claude_spec(spec):
    if "url" in spec:
        out = {"type": "http", "url": spec["url"]}
        if spec.get("headers"):
            out["headers"] = spec["headers"]
        return out
    out = {"command": spec["command"], "args": spec.get("args", [])}
    if spec.get("env"):
        out["env"] = spec["env"]
    return out


# --------------------------------------------------------------------------- Codex CLI
class Codex(Adapter):
    id, name = "codex", "Codex CLI"
    skills_dir = HOME / ".agents" / "skills"
    supports_mcp_toggle = True

    def home(self):
        return Path(os.environ.get("CODEX_HOME", HOME / ".codex"))

    def config_file(self):
        return self.home() / "config.toml"

    def detect(self):
        return bool(which("codex")) or self.home().exists()

    def list_mcp(self):
        f = self.config_file()
        if not f.exists():
            return {}
        try:
            import tomllib
            return dict(tomllib.loads(f.read_text()).get("mcp_servers", {}))
        except Exception:  # noqa: BLE001
            return {}

    def plan_add(self, name, spec):
        if which("codex"):
            if "url" in spec:
                cmd = ["codex", "mcp", "add", name, "--url", spec["url"]]
            else:
                cmd = ["codex", "mcp", "add", name]
                for k, v in (spec.get("env") or {}).items():
                    cmd += ["--env", f"{k}={v}"]
                cmd += ["--", spec["command"], *spec.get("args", [])]
            return Change(self.id, "add", "cli", " ".join(cmd[:4]) + " …", cmd)
        return Change(self.id, "add", str(self.config_file()), f"tambah [mcp_servers.{name}] ke config.toml")

    def plan_remove(self, name):
        if which("codex"):
            cmd = ["codex", "mcp", "remove", name]
            return Change(self.id, "remove", "cli", " ".join(cmd), cmd)
        return Change(self.id, "remove", str(self.config_file()), f"hapus [mcp_servers.{name}] dari config.toml")

    def apply(self, ch, name, spec):
        f = self.config_file()
        bk = backup_file(self.id, f)
        if ch.command:
            run(ch.command)
            return bk
        text = f.read_text() if f.exists() else ""
        text = _toml_remove_table(text, name)
        if ch.action == "add":
            text = text.rstrip() + ("\n\n" if text.strip() else "") + _toml_block(name, spec)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return bk


def _toml_str(v):
    return json.dumps(str(v))  # JSON string escaping is valid TOML basic-string escaping


def _toml_block(name, spec):
    key = name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml_str(name)
    lines = [f"[mcp_servers.{key}]"]
    if "url" in spec:
        lines.append(f"url = {_toml_str(spec['url'])}")
    else:
        lines.append(f"command = {_toml_str(spec['command'])}")
        lines.append("args = [" + ", ".join(_toml_str(a) for a in spec.get("args", [])) + "]")
    if spec.get("env"):
        lines.append(f"[mcp_servers.{key}.env]")
        lines += [f"{_toml_str(k) if not re.fullmatch(r'[A-Za-z0-9_-]+', k) else k} = {_toml_str(v)}" for k, v in spec["env"].items()]
    return "\n".join(lines) + "\n"


def _toml_remove_table(text, name):
    """Drop [mcp_servers.<name>] and its sub-tables."""
    out, skip = [], False
    pat = re.compile(r"^\s*\[\s*mcp_servers\.(\"?)" + re.escape(name) + r"\1(\.[^\]]*)?\s*\]\s*$")
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            skip = bool(pat.match(line))
        if not skip:
            out.append(line)
    return "\n".join(out).rstrip() + ("\n" if out else "")


# --------------------------------------------------------------------------- Cursor
class Cursor(Adapter):
    id, name = "cursor", "Cursor"
    skills_dir = HOME / ".agents" / "skills"

    def config_file(self):
        return HOME / ".cursor" / "mcp.json"

    def detect(self):
        return (HOME / ".cursor").exists() or bool(which("cursor")) or bool(which("cursor-agent"))

    def list_mcp(self):
        try:
            return dict(_load_json(self.config_file()).get("mcpServers", {}))
        except HarnessError:
            return {}

    def plan_add(self, name, spec):
        return Change(self.id, "add", str(self.config_file()), f"tambah mcpServers.{name} di ~/.cursor/mcp.json")

    def plan_remove(self, name):
        return Change(self.id, "remove", str(self.config_file()), f"hapus mcpServers.{name} dari ~/.cursor/mcp.json")

    def apply(self, ch, name, spec):
        f = self.config_file()
        data = _load_json(f)
        bk = backup_file(self.id, f)
        servers = data.setdefault("mcpServers", {})
        if ch.action == "add":
            if "url" in spec:
                servers[name] = {"url": spec["url"], **({"headers": spec["headers"]} if spec.get("headers") else {})}
            else:
                servers[name] = {"type": "stdio", "command": spec["command"], "args": spec.get("args", []),
                                 **({"env": spec["env"]} if spec.get("env") else {})}
        else:
            servers.pop(name, None)
        _save_json(f, data)
        return bk


# --------------------------------------------------------------------------- Gemini CLI
class Gemini(Adapter):
    id, name = "gemini", "Gemini CLI"
    skills_dir = HOME / ".agents" / "skills"

    def config_file(self):
        return Path(os.environ.get("GEMINI_CLI_HOME", HOME)) / ".gemini" / "settings.json"

    def detect(self):
        return bool(which("gemini")) or self.config_file().parent.exists()

    def list_mcp(self):
        try:
            return dict(_load_json(self.config_file()).get("mcpServers", {}))
        except HarnessError:
            return {}

    def plan_add(self, name, spec):
        if which("gemini"):
            if "url" in spec:
                cmd = ["gemini", "mcp", "add", "-s", "user", "--transport", "http", name, spec["url"]]
            else:
                cmd = ["gemini", "mcp", "add", "-s", "user"]
                for k, v in (spec.get("env") or {}).items():
                    cmd += ["-e", f"{k}={v}"]
                cmd += [name, spec["command"], *spec.get("args", [])]
            return Change(self.id, "add", "cli", " ".join(cmd[:5]) + " …", cmd)
        return Change(self.id, "add", str(self.config_file()), f"tambah mcpServers.{name} di ~/.gemini/settings.json")

    def plan_remove(self, name):
        if which("gemini"):
            cmd = ["gemini", "mcp", "remove", "-s", "user", name]
            return Change(self.id, "remove", "cli", " ".join(cmd), cmd)
        return Change(self.id, "remove", str(self.config_file()), f"hapus mcpServers.{name}")

    def apply(self, ch, name, spec):
        f = self.config_file()
        bk = backup_file(self.id, f)
        if ch.command:
            run(ch.command)
            return bk
        data = _load_json(f)
        servers = data.setdefault("mcpServers", {})
        if ch.action == "add":
            servers[name] = ({"httpUrl": spec["url"], **({"headers": spec["headers"]} if spec.get("headers") else {})}
                             if "url" in spec else
                             {"command": spec["command"], "args": spec.get("args", []), **({"env": spec["env"]} if spec.get("env") else {})})
        else:
            servers.pop(name, None)
        _save_json(f, data)
        return bk


# --------------------------------------------------------------------------- OpenCode
class OpenCode(Adapter):
    id, name = "opencode", "OpenCode"
    skills_dir = HOME / ".agents" / "skills"
    supports_mcp_toggle = True

    def config_file(self):
        base = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "opencode"
        for n in ("opencode.json", "opencode.jsonc"):
            if (base / n).exists():
                return base / n
        return base / "opencode.json"

    def detect(self):
        return bool(which("opencode"))

    def list_mcp(self):
        try:
            mcp = _load_json(self.config_file()).get("mcp", {})
            return dict(mcp.get("servers", {})) if isinstance(mcp.get("servers"), dict) else {}
        except HarnessError:
            return {}

    def plan_add(self, name, spec):
        if which("opencode"):
            if "url" in spec:
                cmd = ["opencode", "mcp", "add", name, "--global", "--url", spec["url"]]
            else:
                cmd = ["opencode", "mcp", "add", name, "--global"]
                for k, v in (spec.get("env") or {}).items():
                    cmd += ["--env", f"{k}={v}"]
                cmd += ["--", spec["command"], *spec.get("args", [])]
            return Change(self.id, "add", "cli", " ".join(cmd[:5]) + " …", cmd)
        return Change(self.id, "add", str(self.config_file()), f"tambah mcp.servers.{name}")

    def plan_remove(self, name):
        return Change(self.id, "remove", str(self.config_file()), f"hapus mcp.servers.{name} dari {self.config_file().name}")

    def apply(self, ch, name, spec):
        f = self.config_file()
        bk = backup_file(self.id, f)
        if ch.command:
            run(ch.command)
            return bk
        data = _load_json(f)  # refuses JSONC with comments
        servers = data.setdefault("mcp", {}).setdefault("servers", {})
        if ch.action == "add":
            servers[name] = ({"type": "remote", "url": spec["url"], **({"headers": spec["headers"]} if spec.get("headers") else {})}
                             if "url" in spec else
                             {"type": "local", "command": [spec["command"], *spec.get("args", [])],
                              **({"environment": spec["env"]} if spec.get("env") else {})})
        else:
            servers.pop(name, None)
        _save_json(f, data)
        return bk


# --------------------------------------------------------------------------- OpenBot (delegated)
class OpenBot(Adapter):
    """OpenBot keeps MCP servers in its app database; only an OpenBot agent can change them.
    Plans for OpenBot therefore return instructions for the agent (tools openbot.save_mcp_server,
    openbot.create_skill + openbot.install_local_skill) instead of editing files."""
    id, name = "openbot", "OpenBot"

    def config_file(self):
        if sys.platform == "darwin":
            return HOME / "Library" / "Application Support" / "OpenBot"
        if os.name == "nt":
            return Path(os.environ.get("APPDATA", HOME)) / "OpenBot"
        return Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "OpenBot"

    def detect(self):
        return self.config_file().exists()

    def plan_add(self, name, spec):
        return Change(self.id, "add", "agent", f"agen OpenBot memanggil openbot.save_mcp_server name={name}")

    def plan_remove(self, name):
        return Change(self.id, "remove", "agent", f"agen OpenBot memanggil openbot.remove_mcp_server name={name}")

    def apply(self, ch, name, spec):
        raise HarnessError("OpenBot dipasang oleh agen OpenBot: jalankan instruksi di rencana dengan tool openbot.*")

    def agent_instructions(self, ch, name, spec):
        if ch.action == "add":
            args = {"name": name, "transport": "http" if "url" in spec else "stdio"}
            args.update({"url": spec["url"]} if "url" in spec else {"command": spec["command"], "args": spec.get("args", [])})
            return {"tool": "openbot.save_mcp_server", "arguments": args,
                    "note": "env/API key: minta pengguna mengisinya di Server settings > MCP, jangan lewat chat"}
        return {"tool": "openbot.remove_mcp_server", "arguments": {"name": name}}


ADAPTERS: dict[str, Adapter] = {a.id: a for a in (ClaudeCode(), Codex(), Cursor(), Gemini(), OpenCode(), OpenBot())}


def detected() -> list[Adapter]:
    return [a for a in ADAPTERS.values() if a.detect()]


def get(harness_id: str) -> Adapter:
    if harness_id not in ADAPTERS:
        raise HarnessError(f"harness tidak dikenal: {harness_id} (pilihan: {', '.join(ADAPTERS)})")
    return ADAPTERS[harness_id]


# --------------------------------------------------------------------------- skills
SKILL_LOCATIONS = {
    "claude": {"dir": HOME / ".claude" / "skills", "read_by": ["Claude Code", "Cursor", "OpenCode"]},
    "agents": {"dir": HOME / ".agents" / "skills", "read_by": ["Codex", "Cursor", "Gemini CLI", "OpenCode"]},
}


def skill_locations_for(harness_ids) -> list[str]:
    locs = []
    for h in harness_ids:
        loc = "claude" if h == "claude-code" else ("agents" if h in ("codex", "cursor", "gemini", "opencode") else None)
        if loc and loc not in locs:
            locs.append(loc)
    return locs


def install_skill_folder(src: Path, slug: str, location: str) -> dict:
    """Copy a vetted skill folder into a user skills location. Existing folders are backed up first."""
    if not (src / "SKILL.md").exists():
        raise HarnessError(f"{src} tidak berisi SKILL.md")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        raise HarnessError(f"nama skill harus huruf kecil dan tanda hubung: {slug}")
    dest_root = SKILL_LOCATIONS[location]["dir"]
    dest = dest_root / slug
    backup = None
    if dest.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = paths.config_backups_dir() / "skills" / f"{stamp}-{location}-{slug}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(dest, backup)
        shutil.rmtree(dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
    return {"path": str(dest), "backup": str(backup) if backup else None}


def remove_skill_folder(slug: str, location: str) -> dict:
    dest = SKILL_LOCATIONS[location]["dir"] / slug
    if not dest.exists():
        return {"path": str(dest), "removed": False}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = paths.config_backups_dir() / "skills" / f"{stamp}-removed-{location}-{slug}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dest), backup)
    return {"path": str(dest), "removed": True, "backup": str(backup)}
