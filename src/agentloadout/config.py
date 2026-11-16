"""Settings from <home>/config.toml with environment overrides.

Example config.toml:

    [github]
    # Optional. Order: env GITHUB_TOKEN > env GITHUB_TOKEN_FILE > <data folder>/github_token (saved in the
    # web Settings menu or with `agentloadout set-token`) > token_file below.
    # token_file = "/path/to/github_token"

    [web]
    host = "127.0.0.1"
    port = 8787
    password_file = ""            # older installs only; use `agentloadout set-password` (stored as a hash)
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


TOKEN_SOURCES = (
    ("env", "variabel GITHUB_TOKEN"),
    ("env_file", "variabel GITHUB_TOKEN_FILE"),
    ("managed", "disimpan lewat Pengaturan / agentloadout set-token"),
    ("config", "token_file di config.toml"),
)


def _token_candidates() -> list[tuple[str, str]]:
    """(source id, token or "") in priority order: env > env file > managed file > config token_file."""
    from . import token_store
    return [("env", (os.environ.get("GITHUB_TOKEN") or "").strip()),
            ("env_file", read_secret(os.environ.get("GITHUB_TOKEN_FILE", ""))),
            ("managed", read_secret(str(token_store.token_file()))),
            ("config", read_secret(load()["github"]["token_file"]))]


def github_token() -> str:
    for _, tok in _token_candidates():
        if tok:
            return tok
    return ""


def github_token_info() -> dict:
    """Which token sources exist and which one is used. Never contains a token value (only its last 4)."""
    from . import token_store
    cands = _token_candidates()
    active = next((src for src, tok in cands if tok), None)
    sources = []
    for (src, tok), (_, label) in zip(cands, TOKEN_SOURCES):
        sources.append({"source": src, "label": label, "present": bool(tok), "active": src == active,
                        "shadowed": bool(tok) and active is not None and src != active})
    tok = next((t for s, t in cands if s == active), "")
    info = {"active": active, "sources": sources, "kind": token_store.kind(tok) if tok else None,
            "last4": tok[-4:] if len(tok) >= 12 else None}
    if active == "managed" or any(s["source"] == "managed" and s["present"] for s in sources):
        info["managed"] = token_store.read_meta()
    check = token_store.read_check()
    if check and tok and check.get("last4") == info["last4"]:
        info["last_check"] = check
    return info


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
