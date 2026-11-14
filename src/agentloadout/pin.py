"""Approval PIN: a human sets it once in their own terminal (`agentloadout set-pin`); every approval in
the CLI or the web UI must present it. Agents never learn it.

The PIN is stored as a scrypt hash with a random salt in <home>/approval_pin.json (0600). Wrong PINs are
counted in <home>/approval_pin.fails (shared by CLI and web): after MAX_FAILS the PIN is locked for
LOCK_SECONDS. The PIN itself is never written or logged anywhere.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from . import paths

MIN_DIGITS = 6
MAX_DIGITS = 32
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

# environment variables that agent harnesses set for the shell commands they run
AGENT_MARKERS = ("CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID")

_lock = threading.Lock()


class PinError(Exception):
    pass


class PinNotSet(PinError):
    def __init__(self):
        super().__init__("PIN persetujuan belum diset. Minta pengguna menjalankan `agentloadout set-pin` "
                         "di terminalnya sendiri.")


class PinLocked(PinError):
    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"Terlalu banyak PIN salah. Persetujuan dikunci {seconds // 60 + 1} menit lagi.")


class WrongPin(PinError):
    def __init__(self, left: int):
        super().__init__("PIN salah." + (f" Sisa {left} percobaan sebelum dikunci." if left > 0 else ""))


def pin_file() -> Path:
    return paths.home() / "approval_pin.json"


def fails_file() -> Path:
    return paths.home() / "approval_pin.fails"


def agent_markers(env=None) -> list[str]:
    env = os.environ if env is None else env
    return [k for k in AGENT_MARKERS if env.get(k)]


def is_set() -> bool:
    try:
        rec = json.loads(pin_file().read_text())
        return bool(rec.get("hash") and rec.get("salt"))
    except (OSError, ValueError):
        return False


def valid_format(pin: str) -> bool:
    return bool(re.fullmatch(rf"\d{{{MIN_DIGITS},{MAX_DIGITS}}}", pin or ""))


def _derive(pin: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def _write_private(f: Path, text: str):
    tmp = f.with_name(f.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, f)


def _matches(pin: str) -> bool:
    rec = json.loads(pin_file().read_text())
    got = _derive(pin, bytes.fromhex(rec["salt"]), rec.get("n", SCRYPT["n"]), rec.get("r", SCRYPT["r"]), rec.get("p", SCRYPT["p"]))
    return hmac.compare_digest(got.hex(), rec["hash"])


def set_pin(new: str, old: str | None = None):
    """Create or change the PIN. Changing it requires the current PIN (counted like any approval)."""
    if not valid_format(new):
        raise PinError(f"PIN harus {MIN_DIGITS}–{MAX_DIGITS} digit angka.")
    if is_set():
        verify(old or "")
    salt = secrets.token_bytes(16)
    rec = {"algo": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": _derive(new, salt, **SCRYPT).hex(),
           "set_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    _write_private(pin_file(), json.dumps(rec))
    fails_file().unlink(missing_ok=True)


def _read_fails() -> dict:
    try:
        return json.loads(fails_file().read_text())
    except (OSError, ValueError):
        return {"count": 0, "locked_until": 0}


def locked_seconds() -> int:
    left = int(_read_fails().get("locked_until", 0) - time.time())
    return max(left, 0)


def verify(pin: str):
    """Raise PinNotSet / PinLocked / WrongPin unless `pin` is correct. Never logs the PIN."""
    if not is_set():
        raise PinNotSet()
    with _lock:
        fails = _read_fails()
        if fails.get("locked_until", 0) > time.time():
            raise PinLocked(int(fails["locked_until"] - time.time()))
        if valid_format(pin) and _matches(pin):
            fails_file().unlink(missing_ok=True)
            return
        if fails.get("locked_until") and fails["locked_until"] <= time.time():
            fails = {"count": 0, "locked_until": 0}  # an expired lock starts a fresh round
        fails["count"] = int(fails.get("count", 0)) + 1
        if fails["count"] >= MAX_FAILS:
            fails["locked_until"] = time.time() + LOCK_SECONDS
        _write_private(fails_file(), json.dumps(fails))
    time.sleep(0.5)  # slow down guessing
    if fails["count"] >= MAX_FAILS:
        raise PinLocked(LOCK_SECONDS)
    raise WrongPin(MAX_FAILS - fails["count"])
