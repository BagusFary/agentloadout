"""Test a GitHub token with GET /rate_limit (does not use API quota).

Returns limits, the classic-token scopes (X-OAuth-Scopes) and the expiry header
(GitHub-Authentication-Token-Expiration; its format is not documented, so it is parsed leniently and the
raw value is kept). Errors never contain the token or request headers, and nothing is logged.
"""
from __future__ import annotations

import datetime as dt
import os

import requests

TIMEOUT = 10
UA = "agentloadout-token-check"


def api() -> str:
    return os.environ.get("AGENTLOADOUT_GITHUB_API", "https://api.github.com").rstrip("/")


def parse_expiry(raw: str | None) -> str | None:
    """ISO date-time of the expiry header, or None when the format is unknown (show the raw value then)."""
    if not raw:
        return None
    text = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            t = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t.isoformat()
    try:
        t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).isoformat()
    except ValueError:
        return None


def _limits(body: dict, name: str) -> dict | None:
    r = ((body or {}).get("resources") or {}).get(name)
    if not isinstance(r, dict):
        return None
    return {k: r.get(k) for k in ("limit", "remaining", "reset", "used")}


def test(token: str | None) -> dict:
    """{ok: True|False|None, status, error, core, search, scopes, expires, expires_raw}.
    ok None = GitHub could not be reached (unknown)."""
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(api() + "/rate_limit", headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        return {"ok": None, "status": None, "error": f"tidak bisa menghubungi GitHub ({type(e).__name__})"}
    out = {"status": r.status_code, "scopes": None, "expires": None, "expires_raw": None}
    if r.status_code == 401:
        return {**out, "ok": False, "error": "token ditolak GitHub (401: salah, dicabut, atau kedaluwarsa)"}
    if r.status_code != 200:
        return {**out, "ok": None, "error": f"GitHub menjawab HTTP {r.status_code}"}
    try:
        body = r.json()
    except ValueError:
        body = {}
    scopes = r.headers.get("X-OAuth-Scopes")
    raw = r.headers.get("GitHub-Authentication-Token-Expiration")
    return {**out, "ok": True, "error": None, "core": _limits(body, "core"), "search": _limits(body, "search"),
            "scopes": [s.strip() for s in scopes.split(",") if s.strip()] if scopes is not None else None,
            "expires_raw": raw, "expires": parse_expiry(raw)}
