"""Login for the web UI: signed per-instance session cookie + brute-force limits.

The password comes from web_password (a scrypt record set with `agentloadout set-password`, or the older
plain-text `password_file`). It is never logged.

Session cookie: named per instance (al_sesi_<id>) so instances on other ports keep their own session,
signed with <home>/session.key mixed with the password fingerprint (hash + epoch). Changing or resetting
the password, or "log out everywhere", therefore ends every session at once. `Secure` is only set when
the request came over HTTPS (behind a TLS proxy such as `tailscale serve`), because browsers drop Secure
cookies on plain http to a LAN address.

Limits: 5 wrong passwords per client lock it for 15 minutes; for requests from outside this computer a
global limit (30) always applies too, so rotating X-Forwarded-For does not allow unlimited guessing.
"""
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time

from .. import paths, web_password

SESSION_DAYS = 30
MAX_FAILS = 5
GLOBAL_MAX_FAILS = 30
LOCK_SECONDS = 15 * 60

_lock = threading.Lock()
_fails = {}  # ip | "*" -> [count, first_fail_time, locked_until]


def session_key_file():
    return paths.home() / "session.key"


def _secret() -> str:
    f = session_key_file()
    if not f.exists():
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    return f.read_text().strip()


def _session_key() -> bytes:
    return (_secret() + web_password.fingerprint()).encode()


def rotate_session_key():
    """Invalidate every session, whatever the password source."""
    paths.write_private(session_key_file(), secrets.token_hex(32))


def logout_all():
    web_password.bump_epoch()
    rotate_session_key()


def enabled() -> bool:
    return web_password.enabled()


def cookie_name() -> str:
    return "al_sesi_" + paths.instance_id()[:8]


def make_cookie(secure: bool) -> str:
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(_session_key(), exp.encode(), hashlib.sha256).digest()
    token = exp + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return (f"{cookie_name()}={token}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Lax"
            + ("; Secure" if secure else ""))


def clear_cookie(secure: bool) -> str:
    return f"{cookie_name()}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")


def valid_session(cookie_header: str) -> bool:
    if not enabled():
        return True
    name = cookie_name()
    for part in (cookie_header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key != name or "." not in value:
            continue
        exp, _, sig = value.partition(".")
        if not exp.isdigit() or int(exp) < time.time():
            return False
        good = base64.urlsafe_b64encode(hmac.new(_session_key(), exp.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        return hmac.compare_digest(good, sig)
    return False


def locked(ip: str) -> int:
    """Seconds left on the lock for this key, 0 when not locked."""
    with _lock:
        rec = _fails.get(ip)
        if rec and rec[2] > time.time():
            return int(rec[2] - time.time())
    return 0


def _keys(ip, count_global):
    return ((ip, MAX_FAILS), ("*", GLOBAL_MAX_FAILS)) if count_global else ((ip, MAX_FAILS),)


def _begin(ip: str, count_global: bool) -> int:
    """Count the attempt *before* checking the password, so parallel guesses cannot all pass the lock check.
    Returns the seconds left when locked (attempt not counted), else 0."""
    now = time.time()
    with _lock:
        for key, _ in _keys(ip, count_global):
            rec = _fails.get(key)
            if rec and rec[2] > now:
                return int(rec[2] - now) or 1
        for key, limit in _keys(ip, count_global):
            rec = _fails.setdefault(key, [0, now, 0])
            if now - rec[1] > LOCK_SECONDS and rec[2] <= now:
                rec[:] = [0, now, 0]
            rec[0] += 1
            if rec[0] >= limit:
                rec[2] = now + LOCK_SECONDS
    return 0


def _success(ip: str, count_global: bool):
    with _lock:
        _fails.pop(ip, None)
        if count_global and "*" in _fails:
            rec = _fails["*"]
            rec[0] = max(rec[0] - 1, 0)  # a correct password does not use up the global budget
            if rec[0] < GLOBAL_MAX_FAILS:
                rec[2] = 0


def attempt(ip: str, given: str, count_global: bool) -> tuple[str, int]:
    """('ok', 0) | ('wrong', 0) | ('locked', seconds). Wrong answers are slowed down."""
    wait = _begin(ip, count_global)
    if wait:
        return "locked", wait
    if enabled() and web_password.verify(given):
        _success(ip, count_global)
        return "ok", 0
    time.sleep(1)  # slow down guessing
    return "wrong", 0


LOGIN_PAGE = """<!DOCTYPE html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>Masuk – Agent Loadout</title>
<script>document.documentElement.dataset.theme=localStorage.getItem("katalog-theme")||"auto";</script>
<style>
:root{--bg:#eef2f7;--ink:#0b0f19;--muted:#6e7480;--glass:rgba(255,255,255,.58);--line:rgba(255,255,255,.8);--fill:rgba(118,118,128,.12);--b1:#93c5fd;--b2:#c4b5fd;--b3:#99f6e4;--accent:#007aff;color-scheme:light}
:root[data-theme=dark]{--bg:#05070d;--ink:#f5f5f7;--muted:#8e8e93;--glass:rgba(22,24,32,.58);--line:rgba(255,255,255,.1);--fill:rgba(118,118,128,.24);--b1:#1d4ed8;--b2:#6d28d9;--b3:#0f766e;--accent:#0a84ff;color-scheme:dark}
@media (prefers-color-scheme:dark){:root[data-theme=auto]{--bg:#05070d;--ink:#f5f5f7;--muted:#8e8e93;--glass:rgba(22,24,32,.58);--line:rgba(255,255,255,.1);--fill:rgba(118,118,128,.24);--b1:#1d4ed8;--b2:#6d28d9;--b3:#0f766e;--accent:#0a84ff;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased;overflow:hidden}
.bg{position:fixed;inset:0;z-index:-1;pointer-events:none}.bg i{position:absolute;width:60vmax;height:60vmax;border-radius:50%;filter:blur(80px);opacity:.55}
.bg i:nth-child(1){background:var(--b1);top:-25vmax;left:-15vmax}.bg i:nth-child(2){background:var(--b2);top:-10vmax;right:-25vmax}.bg i:nth-child(3){background:var(--b3);bottom:-30vmax;left:20vmax;opacity:.4}
form{width:min(90vw,360px);padding:30px 24px 24px;border-radius:28px;background:var(--glass);border:1px solid var(--line);-webkit-backdrop-filter:saturate(180%) blur(24px);backdrop-filter:saturate(180%) blur(24px);box-shadow:0 20px 50px rgba(0,0,0,.18);text-align:center}
.icon{width:58px;height:58px;margin:0 auto 14px;border-radius:16px;display:grid;place-items:center;color:#fff;background:linear-gradient(135deg,#0a84ff,#af52de);box-shadow:0 8px 20px rgba(10,132,255,.35)}
h1{margin:0;font-size:24px;font-weight:800;letter-spacing:-.03em}p{margin:6px 0 20px;color:var(--muted);font-size:14.5px}
input{width:100%;padding:13px 14px;border:0;border-radius:13px;background:var(--fill);color:var(--ink);font-size:16px;outline:none}
input:focus{box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 35%,transparent)}
button{width:100%;margin-top:10px;padding:13px;border:0;border-radius:13px;background:var(--accent);color:#fff;font-size:16px;font-weight:600;cursor:pointer}
button:active{transform:scale(.98)}.err{color:#ff3b30;font-size:14px;margin:12px 0 0}
</style></head><body><div class="bg"><i></i><i></i><i></i></div>
<form method="post" action="/login"><div class="icon"><svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg></div>
<h1>Agent Loadout</h1><p>Masukkan password untuk melanjutkan</p>{notice}
<input type="password" name="password" autocomplete="current-password" placeholder="Password" autofocus required>
<button type="submit">Masuk</button>{error}</form></body></html>"""


def login_page(error: str = "", notice: str = "") -> bytes:
    msg = f'<p class="err">{error}</p>' if error else ""
    return LOGIN_PAGE.replace("{error}", msg).replace("{notice}", notice).encode()


NEED_PASSWORD_PAGE = """<!DOCTYPE html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pasang password dulu – Agent Loadout</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:system-ui,sans-serif;
background:#eef2f7;color:#0b0f19}@media (prefers-color-scheme:dark){body{background:#05070d;color:#f5f5f7}}
main{max-width:440px;padding:28px;text-align:center}code{background:rgba(127,127,127,.18);padding:2px 6px;border-radius:6px}
p{line-height:1.5}</style></head><body><main><h1>Pasang password dulu</h1>
<p>Agent Loadout dibuka dari luar komputer ini, tetapi belum punya password web.</p>
<p>Di laptop yang menjalankan Agent Loadout, buka terminal sendiri (bukan lewat agen) lalu jalankan:</p>
<p><code>agentloadout set-password</code></p><p>Setelah itu muat ulang halaman ini.</p></main></body></html>"""


def need_password_page() -> bytes:
    return NEED_PASSWORD_PAGE.encode()
