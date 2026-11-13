"""Password login for the catalog: signed session cookie + simple brute-force limit.

The password is read from a file saved through OpenBot's secure key form; it is never logged.
"""
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time

PASSWORD_FILE = os.environ.get(
    "KATALOG_PASSWORD_FILE",
    "/home/bagus/.config/OpenBot/agent-api-keys/agent-4b18fa4a-ba4c-4376-93c7-209e5bbc3d4a/KATALOG_WEB_PASSWORD",
)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SESSION_KEY_FILE = os.path.join(ROOT, "data", "session.key")
COOKIE = "katalog_sesi"
SESSION_DAYS = 30
MAX_FAILS = 5
GLOBAL_MAX_FAILS = 30
LOCK_SECONDS = 15 * 60

_lock = threading.Lock()
_fails = {}  # ip -> [count, first_fail_time, locked_until]


def _password() -> str:
    try:
        return open(PASSWORD_FILE).read().strip()
    except OSError:
        return ""


def _session_key() -> bytes:
    if not os.path.exists(SESSION_KEY_FILE):
        fd = os.open(SESSION_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32))
    # the password fingerprint is mixed in so changing the password logs everyone out
    pw_fp = hashlib.sha256(_password().encode()).hexdigest()
    return (open(SESSION_KEY_FILE).read().strip() + pw_fp).encode()


def enabled() -> bool:
    return bool(_password())


def make_cookie() -> str:
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(_session_key(), exp.encode(), hashlib.sha256).digest()
    token = exp + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return (f"{COOKIE}={token}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; Secure; SameSite=Lax")


def clear_cookie() -> str:
    return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"


def valid_session(cookie_header: str) -> bool:
    if not enabled():
        return True
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name != COOKIE or "." not in value:
            continue
        exp, _, sig = value.partition(".")
        if not exp.isdigit() or int(exp) < time.time():
            return False
        good = base64.urlsafe_b64encode(hmac.new(_session_key(), exp.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        return hmac.compare_digest(good, sig)
    return False


def locked(ip: str) -> int:
    """Seconds left on the lock for this IP, 0 when not locked."""
    with _lock:
        rec = _fails.get(ip)
        if rec and rec[2] > time.time():
            return int(rec[2] - time.time())
    return 0


def check_password(ip: str, given: str) -> bool:
    ok = enabled() and hmac.compare_digest(given.encode(), _password().encode())
    with _lock:
        if ok:
            _fails.pop(ip, None)
            return True
        for key, limit in ((ip, MAX_FAILS), ("*", GLOBAL_MAX_FAILS)):
            rec = _fails.setdefault(key, [0, time.time(), 0])
            if time.time() - rec[1] > LOCK_SECONDS:
                rec[:] = [0, time.time(), 0]
            rec[0] += 1
            if rec[0] >= limit:
                rec[2] = time.time() + LOCK_SECONDS
    time.sleep(1)  # slow down guessing
    return False


LOGIN_PAGE = """<!DOCTYPE html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>Masuk – Katalog Agen</title>
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
<h1>Katalog Agen</h1><p>Masukkan password untuk melanjutkan</p>
<input type="password" name="password" autocomplete="current-password" placeholder="Password" autofocus required>
<button type="submit">Masuk</button>{error}</form></body></html>"""


def login_page(error: str = "") -> bytes:
    msg = f'<p class="err">{error}</p>' if error else ""
    return LOGIN_PAGE.replace("{error}", msg).encode()
