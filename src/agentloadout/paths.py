"""Where Agent Loadout keeps its data on each OS.

Everything lives in one folder (override with AGENTLOADOUT_HOME):
  Linux   $XDG_DATA_HOME/agentloadout  (default ~/.local/share/agentloadout)
  macOS   ~/Library/Application Support/agentloadout
  Windows %APPDATA%\\agentloadout
"""
import os
import sys
from pathlib import Path

APP = "agentloadout"


def home() -> Path:
    env = os.environ.get("AGENTLOADOUT_HOME")
    if env:
        p = Path(env).expanduser()
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP
    elif os.name == "nt":
        p = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP
    else:
        p = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP
    p.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            p.chmod(0o700)
        except OSError:
            pass
    return p


def db_path() -> Path:
    return Path(os.environ["AGENTLOADOUT_DB"]).expanduser() if os.environ.get("AGENTLOADOUT_DB") else home() / "catalog.db"


def backup_dir() -> Path:
    env = os.environ.get("AGENTLOADOUT_BACKUP_DIR")
    return Path(env).expanduser() if env else home() / "backups"


def config_file() -> Path:
    return home() / "config.toml"


def cache_file(name: str) -> Path:
    return home() / name


def plans_dir() -> Path:
    p = home() / "plans"
    p.mkdir(exist_ok=True)
    return p


def config_backups_dir() -> Path:
    p = home() / "config-backups"
    p.mkdir(exist_ok=True)
    return p


def work_dir() -> Path:
    """Scratch space for repo inspections (cloned, never executed)."""
    p = home() / "work"
    p.mkdir(exist_ok=True)
    return p
