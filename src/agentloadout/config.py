"""Settings from <home>/config.toml with environment overrides.

Example config.toml:

    [github]
    token_file = "~/.config/secrets/github_token"   # or env GITHUB_TOKEN / GITHUB_TOKEN_FILE

    [web]
    host = "127.0.0.1"
    port = 8787
    password_file = ""            # set to require a password (needed when exposed beyond localhost)
    idle_shutdown_minutes = 30    # on-demand UI stops after this idle time (0 = never)

    [catalog]
    refresh_hours = 24            # MCP server refreshes the catalog when older than this

    [integrations.openbot_webhook]   # optional: approvals through an OpenBot agent
    url = ""
    secret_file = ""
"""
import os
import tomllib
from functools import lru_cache
from pathlib import Path

from . import paths

DEFAULTS = {
    "github": {"token_file": ""},
    "web": {"host": "127.0.0.1", "port": 8787, "password_file": "", "idle_shutdown_minutes": 30},
    "catalog": {"refresh_hours": 24},
    "integrations": {"openbot_webhook": {"url": "", "secret_file": ""}},
}


def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@lru_cache(maxsize=1)
def load() -> dict:
    cfg = DEFAULTS
    f = paths.config_file()
    if f.exists():
        with open(f, "rb") as fh:
            cfg = _merge(DEFAULTS, tomllib.load(fh))
    return cfg


def read_secret(path_value: str) -> str:
    if not path_value:
        return ""
    p = Path(path_value).expanduser()
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def github_token() -> str:
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"].strip()
    for candidate in (os.environ.get("GITHUB_TOKEN_FILE", ""), load()["github"]["token_file"], str(paths.home() / "github_token")):
        t = read_secret(candidate)
        if t:
            return t
    return ""


def web() -> dict:
    w = dict(load()["web"])
    if os.environ.get("AGENTLOADOUT_PORT"):
        w["port"] = int(os.environ["AGENTLOADOUT_PORT"])
    if os.environ.get("AGENTLOADOUT_HOST"):
        w["host"] = os.environ["AGENTLOADOUT_HOST"]
    if os.environ.get("AGENTLOADOUT_PASSWORD_FILE"):
        w["password_file"] = os.environ["AGENTLOADOUT_PASSWORD_FILE"]
    return w


def openbot_webhook() -> dict:
    return load()["integrations"].get("openbot_webhook", {}) or {}


def write_default_config():
    f = paths.config_file()
    if not f.exists():
        f.write_text(__doc__.split("Example config.toml:\n\n", 1)[1].replace("\n    ", "\n").lstrip())
        if os.name != "nt":
            f.chmod(0o600)
    return f
