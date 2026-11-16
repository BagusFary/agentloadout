"""The GitHub token that Agent Loadout manages itself: <data>/github_token (0600).

Written by the web Settings menu, `agentloadout set-token` and `setup --token-from-env`. Writes are
atomic (temp file, fsync, replace) so an interrupted write never leaves an empty token behind.
Metadata (when and by what it was set; never the token) goes to github_token.meta.json.
The token value is never printed, logged or returned by any API.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import paths

TOKEN_RE = re.compile(r"[A-Za-z0-9_]{20,255}")


class TokenError(ValueError):
    pass


def token_file() -> Path:
    return paths.home() / "github_token"


def meta_file() -> Path:
    return paths.home() / "github_token.meta.json"


def check_file() -> Path:
    """Last test result of the active token (limits, kind, expiry; no token)."""
    return paths.home() / "github_token.check.json"


def valid_format(token: str) -> bool:
    return isinstance(token, str) and bool(TOKEN_RE.fullmatch(token))


def kind(token: str) -> str:
    if token.startswith("github_pat_"):
        return "fine-grained"
    if token.startswith("ghp_"):
        return "classic"
    if token.startswith(("gho_", "ghu_", "ghs_", "ghr_")):
        return "app/oauth"
    return "lain"


def read_meta() -> dict:
    try:
        m = json.loads(meta_file().read_text())
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def save(token: str, by: str) -> dict:
    token = (token or "").strip()
    if not valid_format(token):
        raise TokenError("Format token tidak dikenali: 20–255 karakter huruf, angka, atau _ (tanpa spasi).")
    paths.write_private(token_file(), token + "\n")
    meta = {"set_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "set_by": by}
    paths.write_private(meta_file(), json.dumps(meta))
    check_file().unlink(missing_ok=True)
    return meta


def save_from_env(by: str = "cli-env") -> dict:
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        raise TokenError("GITHUB_TOKEN kosong.")
    return save(token, by)


def clear() -> bool:
    """Remove only the token this program manages. Env variables and config token_file are not touched."""
    existed = token_file().exists()
    for f in (token_file(), meta_file(), check_file()):
        f.unlink(missing_ok=True)
    return existed


def save_check(result: dict):
    safe = {k: result.get(k) for k in ("ok", "status", "core", "search", "scopes", "expires", "expires_raw", "kind", "last4")}
    safe["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    paths.write_private(check_file(), json.dumps(safe))


def read_check() -> dict | None:
    try:
        return json.loads(check_file().read_text())
    except (OSError, ValueError):
        return None
