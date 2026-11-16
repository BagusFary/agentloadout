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


def home_dir() -> Path:
    """The data folder's path, without creating it (for read-only callers such as the menu's status line)."""
    env = os.environ.get("AGENTLOADOUT_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP


def home() -> Path:
    p = home_dir()
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


def instance_id() -> str:
    """Random id of this data folder (one Agent Loadout instance). Tells instances on different ports apart.
    Created atomically (write a temp file, then link it into place) so concurrent first starts agree."""
    import secrets
    f = home() / "instance_id"
    if not f.exists():
        tmp = home() / f"instance_id.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(8) + "\n")
        try:
            os.link(tmp, f)
        except FileExistsError:
            pass
        except OSError:  # file systems without hard links
            if not f.exists():
                os.replace(tmp, f)
        finally:
            tmp.unlink(missing_ok=True)
    return f.read_text().strip()


def write_private(f: Path, text: str):
    """Write `text` to `f` atomically with mode 0600 (temp file, fsync, replace)."""
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, f)
