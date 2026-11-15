"""Small HTTP server for the Agent Loadout web catalog.

It listens on 127.0.0.1 by default. To open it from a phone, put an HTTPS proxy in front of it
(`tailscale serve` for the tailnet, or a public tunnel such as Tailscale Funnel, ngrok or Cloudflare);
see README "Open it from your phone".

Every request is classified by request_scope():
  local     peer is loopback, Host is 127.0.0.1 / localhost / [::1], and no proxy header is present
  external  anything else (fail closed): proxies always add X-Forwarded-* / Forwarded / Tailscale-* /
            Cf-* / Ngrok-* headers or a public Host name
External requests need a web password (`agentloadout set-password`): without one they only get a
"set a password first" page; /api/health is the only open path. Actions (plans, the OpenBot webhook,
security settings) are allowed for local requests and for logged-in sessions that are not Tailscale
Funnel requests; approving a plan always needs the approval PIN. Identity headers such as
Tailscale-User-Login grant nothing. A raw TCP tunnel (ssh -R, `tailscale serve --tcp`) passes bytes
unchanged and can look local: do not use one.
"""
import errno
import gzip
import ipaddress
import json
import mimetypes
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .. import config, db, paths, pin, web_password
from . import auth

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
PROXY_HEADERS = ("forwarded", "x-real-ip", "via", "true-client-ip", "x-client-ip", "x-original-forwarded-for")
PROXY_PREFIXES = ("x-forwarded-", "tailscale-", "cf-", "ngrok-")


def host_name(host_header: str | None) -> str:
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else ""
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def is_loopback(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def request_scope(peer: str, headers) -> str:
    """'local' or 'external' (see module docstring). Anything unusual is external."""
    if not is_loopback(peer):
        return "external"
    if host_name(headers.get("Host")) not in LOCAL_HOSTS:
        return "external"
    for name in headers.keys():
        n = name.lower()
        if n in PROXY_HEADERS or n.startswith(PROXY_PREFIXES):
            return "external"
    return "local"


def via_https(headers) -> bool:
    if (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower() == "https":
        return True
    fwd = (headers.get("Forwarded") or "").lower()
    return "proto=https" in fwd.replace(" ", "")


def host_warning(host: str) -> str | None:
    if host_name(host) in LOCAL_HOSTS or (host and is_loopback(host)):
        return None
    return (f"PERINGATAN: web dibuka di {host}, bukan hanya 127.0.0.1. Di jaringan lokal (LAN) password dan data "
            "terkirim tanpa enkripsi, dan setiap akses dianggap dari luar (wajib password). Untuk HP, lebih aman "
            "`tailscale serve` (HTTPS) ke 127.0.0.1.")

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
JSON_FIELDS = ("topics", "install", "env_vars", "risks", "skill_paths", "sources", "skills")

try:
    from ..integrations import openbot_webhook as installer  # optional approvals via an OpenBot agent
except Exception:  # noqa: BLE001
    installer = None


def load_progress(con):
    p = db.get_meta(con, "progress") or {}
    if p.get("running"):
        try:
            import datetime as _dt
            age = (_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(p["at"])).total_seconds()
            if age > 40 * 60:
                p["running"] = False
                p["label"] = "Berhenti (tidak ada kabar > 40 menit)"
        except (KeyError, ValueError):
            p["running"] = False
    return p


def load_items():
    con = db.connect()
    rows = con.execute("SELECT * FROM items ORDER BY stars DESC").fetchall()
    installs = {r["id"]: dict(r) for r in con.execute("SELECT * FROM installs")}
    from .. import plans as _plans
    targets = {}
    for t in _plans.installed_targets():
        targets.setdefault(t["item_id"], []).append(t["harness"])
    out = []
    for r in rows:
        d = dict(r)
        for f in JSON_FIELDS:
            d[f] = json.loads(d[f]) if d.get(f) else []
        d["install_state"] = installs.get(d["id"])
        d["targets"] = targets.get(d["id"], [])
        out.append(d)
    meta = {"last_run": db.get_meta(con, "last_run"), "progress": load_progress(con),
            "install_enabled": bool(installer and installer.enabled())}
    con.close()
    return out, meta


class Handler(BaseHTTPRequestHandler):
    server_version = "katalog-agen"

    def log_message(self, fmt, *args):  # quieter log
        sys.stderr.write("%s %s %s\n" % (self.client_ip(), self.command, self.path))

    def send_body(self, code, body: bytes, ctype: str):
        if "gzip" in self.headers.get("Accept-Encoding", "") and len(body) > 1024:
            body = gzip.compress(body)
            self.send_response(code)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, obj):
        self.send_body(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def client_ip(self):
        """For lockouts and logs. Behind a proxy, its last X-Forwarded-For entry is the one it added."""
        xff = self.headers.get("X-Forwarded-For", "")
        return (xff.split(",")[-1].strip() or self.client_address[0]) if xff else self.client_address[0]

    def scope(self) -> str:
        return request_scope(self.client_address[0], self.headers)

    def https(self) -> bool:
        return via_https(self.headers)

    def actions_allowed(self) -> bool:
        """Local, or a logged-in session that is not a Tailscale Funnel request (Funnel is view-only).
        The Funnel header can only take rights away, so forging it never helps."""
        if self.headers.get("Tailscale-Funnel-Request"):
            return False
        if self.scope() == "local":
            return True
        return auth.enabled() and self.authed()

    def gate(self, path) -> bool:
        """External access without a password: answer 'set a password first' and stop."""
        if self.scope() == "external" and not auth.enabled():
            if path.startswith("/api/"):
                self.send_json(403, {"error": "Agent Loadout dibuka dari luar komputer, tetapi belum punya password web. "
                                              "Jalankan `agentloadout set-password` di terminal laptop.", "need_password": True})
            else:
                self.send_body(403, auth.need_password_page(), "text/html; charset=utf-8")
            return False
        return True

    def redirect(self, location, cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def safe_origin(self):
        """Block cross-site requests (CSRF). The web UI always sends X-Agentloadout; browsers cannot add it
        cross-origin without CORS. DNS rebinding (a foreign Host) is external and needs the password."""
        return self.headers.get("X-Agentloadout") == "1"

    def read_json(self, limit=20000):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("terlalu besar")
        return json.loads(self.rfile.read(length) or b"{}")

    def handle_plans(self, path):
        from .. import plans
        if not self.actions_allowed():
            return self.send_json(403, {"error": "Aksi hanya bisa dari komputer ini atau dari sesi yang sudah login "
                                                 "(bukan lewat Funnel). Akses ini hanya untuk melihat."})
        try:
            body = self.read_json()
            parts = path.strip("/").split("/")  # api/plans[/<id>/<action>]
            if len(parts) == 2:
                p = plans.create(body.get("item_id", ""), list(body.get("targets") or []), body.get("action", "install"),
                                 body.get("skill_path") or None, body.get("mcp_name") or None, created_by="web")
                return self.send_json(201, p)
            if len(parts) == 4 and parts[3] in ("approve", "reject"):
                who = "web:" + self.scope()
                if parts[3] == "reject":
                    return self.send_json(200, plans.reject(parts[2], who))
                # approval needs the PIN in the body, also when the UI has no password (agents can reach 127.0.0.1)
                given = body.pop("pin", None)
                try:
                    gen = pin.verify(given if isinstance(given, str) else "")
                except pin.PinNotSet as e:
                    return self.send_json(409, {"error": str(e), "pin_set": False})
                except pin.PinLocked as e:
                    return self.send_json(429, {"error": str(e)})
                except pin.PinError as e:
                    return self.send_json(403, {"error": str(e)})
                plans.load(parts[2])  # 400 for an unknown id before anything else
                p = plans.approve(parts[2], "pin:web", pin_gen=gen)
                if body.get("apply"):
                    p = plans.apply(parts[2])
                    p["openbot"] = self.forward_openbot(p)
                return self.send_json(200, p)
        except (ValueError, PermissionError) as e:
            return self.send_json(400, {"error": str(e)})
        return self.send_json(404, {"error": "not found"})

    def forward_openbot(self, p):
        """OpenBot targets are installed by an OpenBot agent: hand them to the webhook if configured."""
        if "openbot" not in p.get("targets", []):
            return None
        if not installer or not installer.enabled():
            return "OpenBot: jalankan instruksi di rencana lewat agen OpenBot"
        code, res = installer.request({"id": p["item_id"], "action": "install" if p["action"] == "install" else "remove",
                                       "skill_path": (p.get("skill") or {}).get("path")})
        return res.get("error") or "permintaan dikirim ke agen OpenBot"

    def authed(self):
        return auth.valid_session(self.headers.get("Cookie", ""))

    def security_status(self) -> dict:
        return {"password": web_password.status(), "pin": pin.status(), "scope": self.scope(),
                "https": self.https(), "actions_allowed": self.actions_allowed(),
                "funnel": bool(self.headers.get("Tailscale-Funnel-Request"))}

    def handle_security(self, path):
        if not self.actions_allowed():
            return self.send_json(403, {"error": "Pengaturan keamanan hanya dari komputer ini atau sesi yang sudah login."})
        if path == "/api/security/logout-all":
            if not auth.enabled():
                return self.send_json(409, {"error": "Belum ada password web, jadi tidak ada sesi login."})
            auth.logout_all()
            return self.send_json_cookie(200, {"ok": True, "note": "Semua perangkat keluar. Perangkat ini tetap masuk."},
                                         auth.make_cookie(self.https()))
        if path == "/api/security/password":
            if web_password.source() != "hashed":
                return self.send_json(409, {"error": "Password ini belum disimpan sebagai hash (atau belum ada). "
                                                     "Ubah lewat terminal: agentloadout set-password"})
            ip, external = self.client_ip(), self.scope() == "external"
            try:
                body = self.read_json(4000)
            except ValueError:
                return self.send_json(400, {"error": "JSON tidak valid"})
            old, new = body.get("old"), body.get("new")
            if not isinstance(new, str) or not web_password.valid_format(new):
                return self.send_json(400, {"error": f"Password baru harus {web_password.MIN_LEN}–{web_password.MAX_LEN} karakter."})
            result, wait = auth.attempt(ip, old if isinstance(old, str) else "", count_global=external)
            if result == "locked":
                return self.send_json(429, {"error": f"Terlalu banyak percobaan. Coba lagi {wait // 60 + 1} menit lagi."})
            if result != "ok":
                return self.send_json(403, {"error": "Password lama salah."})
            try:
                web_password.set_password(new, old, by="web")
            except web_password.PasswordError as e:
                return self.send_json(400, {"error": str(e)})
            return self.send_json_cookie(200, {"ok": True, "note": "Password diganti. Perangkat lain keluar."},
                                         auth.make_cookie(self.https()))
        return self.send_json(404, {"error": "not found"})

    def send_json_cookie(self, code, obj, cookie):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        LAST_REQUEST[0] = time.time()
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "instance": INSTANCE[0] or paths.instance_id()})
        if not self.gate(path):
            return
        if path == "/login":
            return self.send_body(200, auth.login_page(), "text/html; charset=utf-8")
        if path == "/logout":
            return self.redirect("/login", auth.clear_cookie(self.https()))
        if not self.authed():
            if path.startswith("/api/"):
                return self.send_json(401, {"error": "perlu login"})
            return self.redirect("/login")
        if path == "/api/whoami":
            return self.send_json(200, {"scope": self.scope(), "actions_allowed": self.actions_allowed(),
                                        "funnel": bool(self.headers.get("Tailscale-Funnel-Request"))})
        if path == "/api/security":
            return self.send_json(200, self.security_status())
        if path == "/api/status":
            con = db.connect()
            res = {"progress": load_progress(con), "last_run": db.get_meta(con, "last_run")}
            con.close()
            return self.send_json(200, res)
        if path == "/api/harnesses":
            from .. import harness
            return self.send_json(200, {"harnesses": [h.info() for h in harness.ADAPTERS.values()]})
        if path == "/api/plans":
            from .. import plans
            st = pin.status()
            return self.send_json(200, {"plans": plans.list_plans()[:50], "pin_set": st["set"], "pin_reset_at": st.get("reset_at"),
                                        "pin_reset_recent": st.get("reset_recent", False)})
        if path == "/api/items":
            items, meta = load_items()
            meta["actions_allowed"] = self.actions_allowed()      # install/remove via plans (any harness)
            meta["pin_reset_recent"] = pin.status().get("reset_recent", False)
            meta["openbot_webhook"] = meta["install_enabled"]      # legacy OpenBot buttons need the webhook
            return self.send_json(200, {"items": items, "meta": meta})
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.abspath(os.path.join(WEB, rel))
        if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
            return self.send_body(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self.send_body(200, fh.read(), ctype)

    def do_POST(self):
        LAST_REQUEST[0] = time.time()
        path = urlparse(self.path).path
        if not self.gate(path):
            return
        if path == "/login":
            ip = self.client_ip()
            external = self.scope() == "external"
            length = min(int(self.headers.get("Content-Length") or 0), 2000)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            given = (form.get("password") or [""])[0]
            result, wait = auth.attempt(ip, given, count_global=external)
            if result == "locked":
                return self.send_body(429, auth.login_page(f"Terlalu banyak percobaan. Coba lagi {wait // 60 + 1} menit lagi."), "text/html; charset=utf-8")
            if result == "ok":
                return self.redirect("/", auth.make_cookie(self.https()))
            return self.send_body(401, auth.login_page("Password salah."), "text/html; charset=utf-8")
        if not self.authed():
            return self.send_json(401, {"error": "perlu login"})
        if path.startswith("/api/") and not self.safe_origin():
            return self.send_json(403, {"error": "permintaan lintas situs ditolak"})
        if path.startswith("/api/plans"):
            return self.handle_plans(path)
        if path.startswith("/api/security/"):
            return self.handle_security(path)
        if path != "/api/install":
            return self.send_json(404, {"error": "not found"})
        if not self.actions_allowed():
            return self.send_json(403, {"error": "Aksi hanya bisa dari komputer ini atau dari sesi yang sudah login "
                                                 "(bukan lewat Funnel). Akses ini hanya untuk melihat."})
        if not installer or not installer.enabled():
            return self.send_json(503, {"error": "Tombol pasang belum aktif"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > 10000:
            return self.send_json(413, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_json(400, {"error": "JSON tidak valid"})
        code, res = installer.request(body)
        return self.send_json(code, res)


LAST_REQUEST = [0.0]
INSTANCE = [""]
PORT_TRIES = 10


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64  # phones open several connections at once; the default 5 drops some
    # Windows SO_REUSEADDR lets a second process bind the same port silently: refuse instead
    allow_reuse_address = os.name != "nt"


class PortBusy(OSError):
    pass


def instance_at(host, port) -> str | None:
    """The instance id answering /api/health on host:port; '' for something else; None when nothing answers."""
    import urllib.request
    h = "127.0.0.1" if host in ("", "0.0.0.0") else ("[::1]" if host == "::" else host)
    try:
        with urllib.request.urlopen(f"http://{h}:{port}/api/health", timeout=1.5) as r:
            data = json.loads(r.read() or b"{}")
            return str(data.get("instance") or "")
    except (OSError, ValueError):
        return None


def ui_file():
    return paths.home() / "ui.json"


def read_ui() -> dict | None:
    try:
        return json.loads(ui_file().read_text())
    except (OSError, ValueError):
        return None


def bind(host, port, explicit) -> tuple[Server | None, int, str]:
    """Bind host:port; when it is busy and the port was not chosen explicitly, try the next ones.
    Returns (server, port, note). server is None when this instance already runs there."""
    me = paths.instance_id()
    last = port + (0 if explicit else PORT_TRIES)
    for p in range(port, last + 1):
        try:
            return Server((host, p), Handler), p, ("" if p == port else f"port {port} dipakai proses lain, pindah ke {p}")
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1), errno.EACCES):
                raise
            who = instance_at(host, p)
            if who == me:
                return None, p, "sudah berjalan"
            if explicit:
                what = "instance Agent Loadout lain (folder data lain)" if who else "program lain"
                raise PortBusy(f"Port {p} sedang dipakai {what}. Pilih port lain dengan --port, "
                               "atau jalankan tanpa --port/AGENTLOADOUT_PORT agar Agent Loadout memilih port kosong.") from e
    raise PortBusy(f"Port {port}–{last} semuanya terpakai. Pilih port lain dengan --port.")


def main(host=None, port=None, on_demand=False):
    import threading
    w = config.web()
    host = host or w["host"]
    explicit = bool(port) or bool(os.environ.get("AGENTLOADOUT_PORT"))
    port = int(port or w["port"])
    warn = host_warning(host)
    if warn:
        print(warn, file=sys.stderr, flush=True)
    INSTANCE[0] = paths.instance_id()
    try:
        srv, port, note = bind(host, port, explicit)
    except PortBusy as e:
        print(f"agentloadout ui: {e}", file=sys.stderr, flush=True)
        sys.exit(2)
    shown = "127.0.0.1" if host in ("", "0.0.0.0") else host
    if srv is None:
        print(f"Agent Loadout sudah berjalan di http://{shown}:{port}", flush=True)
        return
    if note:
        print(note, file=sys.stderr, flush=True)
    paths.write_private(ui_file(), json.dumps({"host": shown, "port": port, "pid": os.getpid(), "instance": INSTANCE[0],
                                               "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))
    LAST_REQUEST[0] = time.time()
    idle = int(w.get("idle_shutdown_minutes") or 0) * 60
    if on_demand and idle:
        def watchdog():
            while True:
                time.sleep(30)
                if time.time() - LAST_REQUEST[0] > idle:
                    srv.shutdown()
                    return
        threading.Thread(target=watchdog, daemon=True).start()
    print(f"Agent Loadout di http://{shown}:{port}", flush=True)
    if threading.current_thread() is threading.main_thread():
        import signal
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # systemd stop: still remove ui.json
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        if (read_ui() or {}).get("pid") == os.getpid():
            ui_file().unlink(missing_ok=True)


if __name__ == "__main__":
    main()
