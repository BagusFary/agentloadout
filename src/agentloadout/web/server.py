"""Small HTTP server for the agent catalog. Listens on 127.0.0.1 only; Tailscale serve publishes it to the tailnet."""
import gzip
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from urllib.parse import parse_qs

from .. import config, db
from . import auth

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
        xff = self.headers.get("X-Forwarded-For", "")
        return xff.split(",")[-1].strip() if xff else self.client_address[0]

    def redirect(self, location, cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def via_tailnet(self):
        """True when the request came through `tailscale serve` from a tailnet device (not Funnel)."""
        if self.headers.get("Tailscale-Funnel-Request"):
            return False
        if self.headers.get("Tailscale-User-Login"):
            return True
        # direct local access (no proxy) is also trusted
        return not self.headers.get("X-Forwarded-For") and self.client_address[0] in ("127.0.0.1", "::1")

    def safe_origin(self):
        """Block cross-site requests (CSRF) and DNS rebinding.
        The web UI always sends X-Agentloadout; browsers cannot add it cross-origin without CORS."""
        if self.headers.get("X-Agentloadout") != "1":
            return False
        if not auth.enabled():  # local mode without password: only accept local host names
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if host not in ("127.0.0.1", "localhost", "::1"):
                return False
        return True

    def read_json(self, limit=20000):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("terlalu besar")
        return json.loads(self.rfile.read(length) or b"{}")

    def handle_plans(self, path):
        from .. import plans
        if not self.via_tailnet():
            return self.send_json(403, {"error": "Aksi hanya bisa dari komputer ini atau perangkat Tailscale."})
        try:
            body = self.read_json()
            parts = path.strip("/").split("/")  # api/plans[/<id>/<action>]
            if len(parts) == 2:
                p = plans.create(body.get("item_id", ""), list(body.get("targets") or []), body.get("action", "install"),
                                 body.get("skill_path") or None, body.get("mcp_name") or None, created_by="web")
                return self.send_json(201, p)
            if len(parts) == 4 and parts[3] in ("approve", "reject"):
                who = "web:" + (self.headers.get("Tailscale-User-Login") or self.client_ip())
                if parts[3] == "reject":
                    return self.send_json(200, plans.reject(parts[2], who))
                p = plans.approve(parts[2], who)
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

    def do_GET(self):
        import time as _t
        LAST_REQUEST[0] = _t.time()
        path = urlparse(self.path).path
        if path == "/login":
            return self.send_body(200, auth.login_page(), "text/html; charset=utf-8")
        if path == "/logout":
            return self.redirect("/login", auth.clear_cookie())
        if path == "/api/health":
            return self.send_json(200, {"ok": True})
        if not self.authed():
            if path.startswith("/api/"):
                return self.send_json(401, {"error": "perlu login"})
            return self.redirect("/login")
        if path == "/api/whoami":
            return self.send_json(200, {"tailnet": self.via_tailnet(),
                                        "login": self.headers.get("Tailscale-User-Login"),
                                        "funnel": bool(self.headers.get("Tailscale-Funnel-Request"))})
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
            return self.send_json(200, {"plans": plans.list_plans()[:50]})
        if path == "/api/items":
            items, meta = load_items()
            meta["actions_allowed"] = self.via_tailnet()          # install/remove via plans (any harness)
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
        path = urlparse(self.path).path
        if path == "/login":
            ip = self.client_ip()
            tailnet = self.via_tailnet()
            wait = max(auth.locked(ip), 0 if tailnet else auth.locked("*"))
            if wait:
                return self.send_body(429, auth.login_page(f"Terlalu banyak percobaan. Coba lagi {wait // 60 + 1} menit lagi."), "text/html; charset=utf-8")
            length = min(int(self.headers.get("Content-Length") or 0), 2000)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            given = (form.get("password") or [""])[0]
            if auth.check_password(ip, given, count_global=not tailnet):
                return self.redirect("/", auth.make_cookie())
            return self.send_body(401, auth.login_page("Password salah."), "text/html; charset=utf-8")
        if not self.authed():
            return self.send_json(401, {"error": "perlu login"})
        if path.startswith("/api/") and not self.safe_origin():
            return self.send_json(403, {"error": "permintaan lintas situs ditolak"})
        if path.startswith("/api/plans"):
            return self.handle_plans(path)
        if path != "/api/install":
            return self.send_json(404, {"error": "not found"})
        if not self.via_tailnet():
            return self.send_json(403, {"error": "Aksi hanya bisa dari perangkat Tailscale. Akses publik hanya untuk melihat."})
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


def main(host=None, port=None, on_demand=False):
    import threading
    import time
    w = config.web()
    host = host or w["host"]
    port = int(port or w["port"])
    srv = ThreadingHTTPServer((host, port), Handler)
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
    print(f"Agent Loadout di http://{host}:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
