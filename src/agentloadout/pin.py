"""Approval PIN: a human sets it once in their own terminal (`agentloadout set-pin`); every approval in
the CLI or the web UI must present it. Agents never learn it.

<home>/approval_pin.json (0600) holds one record: the scrypt hash of the PIN with its random salt, the
count of wrong PINs and the lock time. Keeping the counter in the same record means it cannot be reset
on its own: deleting the file deletes the PIN, and then every approval is refused until a human runs
`set-pin` again. A record that cannot be read or is invalid counts as locked (fail closed). CLI and web
are separate processes, so every read-modify-write of the record holds an inter-process file lock.
The PIN itself is never written or logged anywhere.

The record also keeps `set_by`, `set_at`, `reset_at` and `gen`. `gen` goes up whenever the PIN is set,
changed or reset; an approval stores the gen it was made with and apply_plan refuses a plan whose gen is
no longer current. A forgotten PIN is replaced with `agentloadout set-pin --reset` (human, own terminal,
type RESET): it works on a locked or broken record, records reset_at, and revokes approved plans.
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
AGENT_MARKERS = ("CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                 "OPENBOT_AGENT_PROCESS",
                 # Gemini CLI runs agent commands in a pty (node-pty); OpenCode and Cursor set these in agent shells
                 "GEMINI_CLI", "GEMINI_CLI_IN_SHELL", "OPENCODE", "CURSOR_AGENT")
RESET_BANNER_DAYS = 7

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
    paths.write_private(f, text)


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
              and isinstance(rec.get("locked_until", 0), (int, float))
              and isinstance(rec.get("gen", 0), int))
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


def verify(pin: str) -> int:
    """Raise PinNotSet / PinLocked / WrongPin unless `pin` is correct; return the PIN gen it matched.
    Never logs the PIN."""
    def run():
        with _threads, filelock.exclusive(lock_file()):
            rec = _read()
            before = (rec or {}).get("fails", 0), (rec or {}).get("locked_until", 0)
            rec = _check(rec, pin)
            if (rec["fails"], rec["locked_until"]) != before:
                _write_private(pin_file(), json.dumps(rec))
            return rec.get("gen", 0)
    return _slow(run)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _record(new: str, by: str, gen: int, reset_at: str | None) -> dict:
    salt = secrets.token_bytes(16)
    return {"algo": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": _derive(new, salt, **SCRYPT).hex(),
            "fails": 0, "locked_until": 0, "set_at": _now(), "set_by": by, "reset_at": reset_at, "gen": gen}


def set_pin(new: str, old: str | None = None, by: str = "cli") -> int:
    """Create or change the PIN. Changing it requires the current PIN (counted like any approval).
    Returns the new gen; approvals made with an older gen stop working."""
    if not valid_format(new):
        raise PinError(f"PIN harus {MIN_DIGITS}–{MAX_DIGITS} digit angka.")

    def run():
        with _threads, filelock.exclusive(lock_file()):
            rec = _read()
            if rec is not None:
                _check(rec, old or "")
            gen = rec.get("gen", 0) + 1 if rec else _fresh_gen()
            _write_private(pin_file(), json.dumps(_record(new, by, gen, (rec or {}).get("reset_at"))))
            return gen
    gen = _slow(run)
    (paths.home() / "approval_pin.fails").unlink(missing_ok=True)  # counter file of the first 0.2.0 draft
    return gen


def reset_pin(new: str, by: str = "cli-reset") -> int:
    """Forgotten PIN: replace it without the old one. Works on a missing, locked or broken record.
    The caller (CLI) checks the terminal and agent markers; plans.revoke_approved() follows."""
    if not valid_format(new):
        raise PinError(f"PIN harus {MIN_DIGITS}–{MAX_DIGITS} digit angka.")
    with _threads, filelock.exclusive(lock_file()):
        try:
            rec = _read()
        except PinRecordInvalid:
            rec = None
        gen = rec.get("gen", 0) + 1 if rec else _fresh_gen()
        _write_private(pin_file(), json.dumps(_record(new, by, gen, _now())))
        return gen


def _fresh_gen() -> int:
    """Gen for a PIN set without a usable earlier record (first time, deleted or broken file). Random, so it
    never repeats a gen that old approvals may still carry."""
    return secrets.randbelow(2 ** 40) + 1


def current_gen() -> int | None:
    """The gen approvals must match; None when no usable PIN record exists (then nothing may be applied)."""
    try:
        rec = _read()
    except PinRecordInvalid:
        return None
    return None if rec is None else rec.get("gen", 0)


def _recent(stamp: str | None) -> bool:
    if not stamp:
        return False
    try:
        t = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return False
    return time.time() - t < RESET_BANNER_DAYS * 86400


def status() -> dict:
    """Safe to show: no hash, no salt."""
    try:
        rec = _read()
    except PinRecordInvalid:
        return {"set": True, "invalid": True, "locked": True}
    if rec is None:
        return {"set": False}
    return {"set": True, "set_at": rec.get("set_at"), "set_by": rec.get("set_by"), "reset_at": rec.get("reset_at"),
            "reset_recent": _recent(rec.get("reset_at")), "gen": rec.get("gen", 0),
            "locked": rec.get("locked_until", 0) > time.time()}


def locked_seconds() -> int:
    try:
        rec = _read()
    except PinRecordInvalid:
        return LOCK_SECONDS
    return max(int((rec or {}).get("locked_until", 0) - time.time()), 0)
