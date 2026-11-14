"""Approval PIN: a human sets it once in their own terminal (`agentloadout set-pin`); every approval in
the CLI or the web UI must present it. Agents never learn it.

<home>/approval_pin.json (0600) holds one record: the scrypt hash of the PIN with its random salt, the
count of wrong PINs and the lock time. Keeping the counter in the same record means it cannot be reset
on its own: deleting the file deletes the PIN, and then every approval is refused until a human runs
`set-pin` again. A record that cannot be read or is invalid counts as locked (fail closed). CLI and web
are separate processes, so every read-modify-write of the record holds an inter-process file lock.
The PIN itself is never written or logged anywhere.
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

from . import filelock, paths

MIN_DIGITS = 8
MAX_DIGITS = 32
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

# environment variables that agent harnesses set for the shell commands they run
AGENT_MARKERS = ("CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID")

_threads = threading.Lock()  # threads of the web server; processes use the file lock


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


class PinRecordInvalid(PinLocked):
    def __init__(self):
        PinError.__init__(self, "Catatan PIN tidak terbaca atau tidak valid, jadi persetujuan dikunci. Pengguna bisa "
                                f"menghapus {pin_file()} lalu menjalankan `agentloadout set-pin` lagi.")
        self.seconds = LOCK_SECONDS


class WrongPin(PinError):
    def __init__(self, left: int):
        super().__init__("PIN salah." + (f" Sisa {left} percobaan sebelum dikunci." if left > 0 else ""))


def pin_file() -> Path:
    return paths.home() / "approval_pin.json"


def lock_file() -> Path:
    return paths.home() / "approval_pin.lock"


def agent_markers(env=None) -> list[str]:
    env = os.environ if env is None else env
    return [k for k in AGENT_MARKERS if env.get(k)]


def is_set() -> bool:
    """True when a PIN record exists, valid or not (an invalid record must lock, not look 'not set')."""
    return pin_file().exists()


def valid_format(pin: str) -> bool:
    return bool(re.fullmatch(rf"\d{{{MIN_DIGITS},{MAX_DIGITS}}}", pin or ""))


def _derive(pin: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def _write_private(f: Path, text: str):
    tmp = f.with_name(f.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, f)


def _read() -> dict | None:
    """The record, None when there is no PIN. Raises PinRecordInvalid for anything unusable."""
    f = pin_file()
    if not f.exists():
        return None
    try:
        rec = json.loads(f.read_text())
        ok = (isinstance(rec, dict) and rec.get("algo") == "scrypt"
              and re.fullmatch(r"[0-9a-f]{32}", rec.get("salt", "")) and re.fullmatch(r"[0-9a-f]{64}", rec.get("hash", ""))
              and all(isinstance(rec.get(k), int) and rec[k] > 0 for k in ("n", "r", "p"))
              and isinstance(rec.get("fails", 0), int) and rec.get("fails", 0) >= 0
              and isinstance(rec.get("locked_until", 0), (int, float)))
    except (OSError, ValueError):
        ok = False
    if not ok:
        raise PinRecordInvalid()
    return rec


def _matches(rec: dict, pin: str) -> bool:
    got = _derive(pin, bytes.fromhex(rec["salt"]), rec["n"], rec["r"], rec["p"])
    return hmac.compare_digest(got.hex(), rec["hash"])


def _check(rec: dict | None, pin: str) -> dict:
    """Verify under the lock. Returns the record to write back; raises after recording a failure."""
    if rec is None:
        raise PinNotSet()
    now = time.time()
    if rec.get("locked_until", 0) > now:
        raise PinLocked(int(rec["locked_until"] - now))
    if rec.get("locked_until"):
        rec.update(fails=0, locked_until=0)  # an expired lock starts a fresh round
    if valid_format(pin) and _matches(rec, pin):
        rec.update(fails=0, locked_until=0)
        return rec
    rec["fails"] = rec.get("fails", 0) + 1
    if rec["fails"] >= MAX_FAILS:
        rec["locked_until"] = now + LOCK_SECONDS
    _write_private(pin_file(), json.dumps(rec))
    if rec["fails"] >= MAX_FAILS:
        raise PinLocked(LOCK_SECONDS)
    raise WrongPin(MAX_FAILS - rec["fails"])


def _slow(fn, *args):
    try:
        return fn(*args)
    except (WrongPin, PinLocked):
        time.sleep(0.5)  # slow down guessing (outside the lock)
        raise


def verify(pin: str):
    """Raise PinNotSet / PinLocked / WrongPin unless `pin` is correct. Never logs the PIN."""
    def run():
        with _threads, filelock.exclusive(lock_file()):
            rec = _read()
            before = (rec or {}).get("fails", 0), (rec or {}).get("locked_until", 0)
            rec = _check(rec, pin)
            if (rec["fails"], rec["locked_until"]) != before:
                _write_private(pin_file(), json.dumps(rec))
    _slow(run)


def set_pin(new: str, old: str | None = None):
    """Create or change the PIN. Changing it requires the current PIN (counted like any approval)."""
    if not valid_format(new):
        raise PinError(f"PIN harus {MIN_DIGITS}–{MAX_DIGITS} digit angka.")

    def run():
        with _threads, filelock.exclusive(lock_file()):
            rec = _read()
            if rec is not None:
                _check(rec, old or "")
            salt = secrets.token_bytes(16)
            _write_private(pin_file(), json.dumps({
                "algo": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": _derive(new, salt, **SCRYPT).hex(),
                "fails": 0, "locked_until": 0, "set_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))
    _slow(run)
    (paths.home() / "approval_pin.fails").unlink(missing_ok=True)  # counter file of the first 0.2.0 draft


def locked_seconds() -> int:
    try:
        rec = _read()
    except PinRecordInvalid:
        return LOCK_SECONDS
    return max(int((rec or {}).get("locked_until", 0) - time.time()), 0)
