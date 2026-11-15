"""Password of the local web UI.

The human sets it in their own terminal with `agentloadout set-password`. It is stored as a scrypt hash
with a random salt in <home>/web_password.json (0600):

    {algo, n, r, p, salt, hash, set_at, set_by, reset_at, epoch}

`epoch` goes up on every change, reset and "log out everywhere"; the session key is derived from it, so
all existing sessions end at once.

Sources, in this order (the first that applies wins):
  hashed       the record above
  legacy_file  `[web] password_file` / AGENTLOADOUT_PASSWORD_FILE: plain text file (older installs)
  off          no password
A record that cannot be read counts as a password nobody knows (fail closed): logins fail until the
human runs `agentloadout set-password --reset`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

from . import config, filelock, paths

MIN_LEN = 10
MAX_LEN = 128
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

_threads = threading.Lock()


class PasswordError(Exception):
    pass


def record_file() -> Path:
    return paths.home() / "web_password.json"


def _lock_file() -> Path:
    return paths.home() / "web_password.lock"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def legacy_file() -> str:
    return config.web().get("password_file") or ""


def _legacy_value() -> str:
    return config.read_secret(legacy_file())


def read_record() -> dict | None:
    """The record; None when there is none; {"invalid": True} when it exists but is unusable."""
    f = record_file()
    if not f.exists():
        return None
    try:
        rec = json.loads(f.read_text())
        ok = (isinstance(rec, dict) and rec.get("algo") == "scrypt"
              and re.fullmatch(r"[0-9a-f]{32}", rec.get("salt", "")) and re.fullmatch(r"[0-9a-f]{64}", rec.get("hash", ""))
              and all(isinstance(rec.get(k), int) and rec[k] > 0 for k in ("n", "r", "p"))
              and isinstance(rec.get("epoch", 0), int))
    except (OSError, ValueError):
        ok = False
    return rec if ok else {"invalid": True}


def source() -> str:
    if record_file().exists():
        return "hashed"
    if _legacy_value():
        return "legacy_file"
    return "off"


def enabled() -> bool:
    return source() != "off"


def valid_format(pw: str) -> bool:
    return isinstance(pw, str) and MIN_LEN <= len(pw) <= MAX_LEN


def _derive(pw: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def verify(given: str) -> bool:
    """Constant-time check against the active source. Never logs the password."""
    given = given if isinstance(given, str) else ""
    src = source()
    if src == "hashed":
        rec = read_record()
        if not rec or rec.get("invalid") or len(given) > MAX_LEN:
            return False
        got = _derive(given, bytes.fromhex(rec["salt"]), rec["n"], rec["r"], rec["p"])
        return hmac.compare_digest(got.hex(), rec["hash"])
    if src == "legacy_file":
        return hmac.compare_digest(given.encode(), _legacy_value().encode())
    return False


def fingerprint() -> str:
    """Mixed into the session key: changes whenever the password, its epoch or its source changes."""
    src = source()
    if src == "hashed":
        rec = read_record() or {}
        return "h:" + hashlib.sha256(f"{rec.get('hash')}:{rec.get('epoch', 0)}:{rec.get('invalid')}".encode()).hexdigest()
    if src == "legacy_file":
        return "l:" + hashlib.sha256(_legacy_value().encode()).hexdigest()
    return "off"


def _write(rec: dict):
    paths.write_private(record_file(), json.dumps(rec))


def _new_record(pw: str, by: str, old: dict | None, reset: bool) -> dict:
    salt = secrets.token_bytes(16)
    usable = old if old and not old.get("invalid") else {}
    now = _now()
    return {"algo": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": _derive(pw, salt, **SCRYPT).hex(),
            "set_at": now, "set_by": by, "reset_at": now if reset else usable.get("reset_at"),
            # a broken record has no trustworthy epoch: jump far enough that no old session key repeats
            "epoch": usable.get("epoch", 0) + 1 if usable or not old else secrets.randbelow(2 ** 31) + 1000}


def set_password(new: str, old: str | None, by: str = "cli") -> dict:
    """Create or change the password. When one is already active (record or legacy file), `old` must match."""
    if not valid_format(new):
        raise PasswordError(f"Password harus {MIN_LEN}–{MAX_LEN} karakter.")
    with _threads, filelock.exclusive(_lock_file()):
        if source() != "off" and not verify(old or ""):
            raise PasswordError("Password lama salah.")
        rec = _new_record(new, by, read_record(), reset=False)
        _write(rec)
        return rec


def reset_password(new: str, by: str = "cli-reset") -> dict:
    """Forgotten password: replace it without the old one (human, own terminal). Ends every session."""
    if not valid_format(new):
        raise PasswordError(f"Password harus {MIN_LEN}–{MAX_LEN} karakter.")
    with _threads, filelock.exclusive(_lock_file()):
        rec = _new_record(new, by, read_record(), reset=True)
        _write(rec)
        return rec


def turn_off(old: str):
    """Remove the hashed password (the old one is required)."""
    with _threads, filelock.exclusive(_lock_file()):
        if source() != "hashed":
            raise PasswordError("Tidak ada password ber-hash untuk dimatikan.")
        if not verify(old or ""):
            raise PasswordError("Password lama salah.")
        record_file().unlink()


def bump_epoch() -> bool:
    """Log out every session of a hashed password. False when there is no usable record."""
    with _threads, filelock.exclusive(_lock_file()):
        rec = read_record()
        if not rec or rec.get("invalid"):
            return False
        rec["epoch"] = rec.get("epoch", 0) + 1
        _write(rec)
        return True


def status() -> dict:
    """Safe to show (no hash, no salt, no file content)."""
    src = source()
    out = {"source": src, "enabled": src != "off"}
    if src == "hashed":
        rec = read_record() or {}
        out.update(invalid=bool(rec.get("invalid")), set_at=rec.get("set_at"), set_by=rec.get("set_by"),
                   reset_at=rec.get("reset_at"))
    elif src == "legacy_file":
        out["note"] = "password dari file teks (password_file); jalankan `agentloadout set-password` untuk menyimpannya sebagai hash"
    return out
