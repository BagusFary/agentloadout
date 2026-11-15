"""Web password, outside-access gate, cookies and sessions (BRIEF-03 H1-H6, H12, H14)."""
import json
import stat
import threading

import pytest

from conftest import AGENT_MARKERS

PW = "kata-sandi-uji-12"
TS_USER = {"Tailscale-User-Login": "someone@example.com"}


class Headers(dict):
    """Case-insensitive enough for request_scope (it reads .get('Host') and .keys())."""


# ------------------------------------------------------------------ request_scope (K1)
@pytest.mark.parametrize("peer,host,extra,expected", [
    ("127.0.0.1", "127.0.0.1:8790", {}, "local"),
    ("127.0.0.1", "localhost:8790", {}, "local"),
    ("::1", "[::1]:8790", {}, "local"),
    ("::ffff:127.0.0.1", "127.0.0.1", {}, "local"),
    ("127.0.0.1", "127.0.0.1", {"X-Forwarded-For": "1.2.3.4"}, "external"),
    ("127.0.0.1", "127.0.0.1", {"X-Forwarded-Proto": "https"}, "external"),
    ("127.0.0.1", "127.0.0.1", {"Forwarded": "for=1.2.3.4"}, "external"),
    ("127.0.0.1", "127.0.0.1", {"X-Real-IP": "1.2.3.4"}, "external"),
    ("127.0.0.1", "127.0.0.1", TS_USER, "external"),
    ("127.0.0.1", "127.0.0.1", {"Cf-Ray": "abc"}, "external"),
    ("127.0.0.1", "127.0.0.1", {"Ngrok-Skip-Browser-Warning": "1"}, "external"),
    ("127.0.0.1", "laptop.tailnet.invalid", {}, "external"),
    ("127.0.0.1", "evil.example", {}, "external"),       # DNS rebinding
    ("127.0.0.1", None, {}, "external"),                 # no Host: fail closed
    ("192.168.1.5", "127.0.0.1", {}, "external"),         # LAN peer
    ("100.64.0.9", "127.0.0.1:8790", {}, "external"),
])
def test_request_scope(peer, host, extra, expected):
    from agentloadout.web.server import request_scope
    h = Headers(extra)
    if host is not None:
        h["Host"] = host
    assert request_scope(peer, h) == expected


def test_https_detection_and_host_warning():
    from agentloadout.web.server import host_warning, via_https
    assert via_https({"X-Forwarded-Proto": "https"}) and via_https({"Forwarded": "for=1.2.3.4; proto=https"})
    assert not via_https({}) and not via_https({"X-Forwarded-Proto": "http"})
    assert host_warning("127.0.0.1") is None and host_warning("localhost") is None and host_warning("::1") is None
    assert "PERINGATAN" in host_warning("0.0.0.0") and "PERINGATAN" in host_warning("192.168.1.5")


# ------------------------------------------------------------------ gate without a password (H3, H14)
@pytest.fixture
def web(sb):
    sb.catalog()
    return sb.ui()


def test_outside_without_password_gets_set_password_page(sb, web):
    assert web.request("GET", "/")["status"] == 200  # local: open as before
    for hdrs, host in (({"X-Forwarded-For": "1.2.3.4"}, None), ({}, "evil.example"), (TS_USER, None)):
        page = web.request("GET", "/", headers=hdrs, host=host)
        assert page["status"] == 403 and "agentloadout set-password" in page["text"]
        api = web.request("GET", "/api/items", headers=hdrs, host=host)
        assert api["status"] == 403 and api["json"]["need_password"] is True
        assert web.request("GET", "/login", headers=hdrs, host=host)["status"] == 403
    health = web.request("GET", "/api/health", headers={"X-Forwarded-For": "1.2.3.4"})
    assert health["status"] == 200 and health["json"]["instance"] == (sb.al / "instance_id").read_text().strip()


def test_api_install_uses_the_new_gate(sb, web):
    out = web.request("POST", "/api/install", headers={"X-Forwarded-For": "1.2.3.4", **TS_USER}, body={"id": "x"})
    assert out["status"] == 403 and out["json"].get("need_password")
    local = web.request("POST", "/api/install", body={"id": "x"})
    assert local["status"] == 503  # gate passed; the OpenBot webhook is not configured here


# ------------------------------------------------------------------ with a password (H1, H2, K2, K3)
@pytest.fixture
def pweb(sb):
    sb.catalog()
    sb.set_password(PW)
    return sb.ui()


def test_password_is_hashed_on_disk(sb, pweb):
    rec_file = sb.al / "web_password.json"
    rec = json.loads(rec_file.read_text())
    assert rec["algo"] == "scrypt" and len(rec["salt"]) == 32 and rec["set_by"] == "cli" and rec["epoch"] >= 1
    assert stat.S_IMODE(rec_file.stat().st_mode) == 0o600
    for f in sb.al.rglob("*"):
        if f.is_file():
            assert PW.encode() not in f.read_bytes(), f


def test_local_access_needs_the_password_too(sb, pweb):
    r = pweb.request("GET", "/")
    assert r["status"] == 303 and r["headers"]["location"] == "/login"
    assert pweb.request("GET", "/api/items")["status"] == 401
    bad, cookie = pweb.login("wrong-password-x")
    assert bad["status"] == 401 and cookie is None
    ok, cookie = pweb.login(PW)
    assert ok["status"] == 303 and cookie
    assert pweb.request("GET", "/api/items", cookie=cookie)["status"] == 200


def test_forged_tailscale_identity_grants_nothing(sb, pweb):
    hdrs = {"X-Forwarded-For": "1.2.3.4", **TS_USER}
    assert pweb.request("GET", "/api/whoami", headers=hdrs)["status"] == 401
    assert pweb.request("POST", "/api/plans", headers=hdrs, body={"item_id": "acme/demo-mcp", "targets": ["cursor"]})["status"] == 401
    _, cookie = pweb.login(PW, headers=hdrs)
    who = pweb.request("GET", "/api/whoami", headers=hdrs, cookie=cookie)["json"]
    assert who["scope"] == "external" and "login" not in who
    # a logged-in session over a Tailscale Funnel request stays view-only
    funnel = {**hdrs, "Tailscale-Funnel-Request": "?1"}
    assert pweb.request("GET", "/api/items", headers=funnel, cookie=cookie)["json"]["meta"]["actions_allowed"] is False
    assert pweb.request("POST", "/api/plans", headers=funnel, cookie=cookie,
                        body={"item_id": "acme/demo-mcp", "targets": ["cursor"]})["status"] == 403
    assert pweb.request("POST", "/api/install", headers=funnel, cookie=cookie, body={"id": "x"})["status"] == 403
    # without Funnel: a logged-in session may act (approval still needs the PIN)
    assert pweb.request("GET", "/api/items", headers=hdrs, cookie=cookie)["json"]["meta"]["actions_allowed"] is True
    made = pweb.request("POST", "/api/plans", headers=hdrs, cookie=cookie, body={"item_id": "acme/demo-mcp", "targets": ["cursor"]})
    assert made["status"] == 201
    nopin = pweb.request("POST", f"/api/plans/{made['json']['id']}/approve", headers=hdrs, cookie=cookie, body={"apply": True})
    assert nopin["status"] == 409 and sb.plan_json(made["json"]["id"])["status"] == "pending"


def test_global_login_lock_holds_with_rotating_forwarded_for(sb, pweb):
    codes = []

    def guess(i):
        r, _ = pweb.login(f"wrong-guess-{i:04d}", headers={"X-Forwarded-For": f"10.0.{i // 250}.{i % 250}", **TS_USER})
        codes.append(r["status"])

    threads = [threading.Thread(target=guess, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert codes.count(401) <= 30 and 429 in codes, codes
    r, cookie = pweb.login(PW, headers={"X-Forwarded-For": "172.16.99.99", **TS_USER})
    assert r["status"] == 429 and cookie is None  # correct password, new IP, forged identity: still locked
    local, cookie = pweb.login(PW)
    assert local["status"] == 303 and cookie  # the computer itself is not locked out by outside guessing


def test_legacy_password_file_keeps_working_until_hashed(sb):
    sb.catalog()
    legacy = sb.root / "legacy_pw"
    legacy.write_text("legacy-password-77\n")
    web = sb.ui(extra_env={"AGENTLOADOUT_PASSWORD_FILE": str(legacy)})
    r, cookie = web.login("legacy-password-77")
    assert r["status"] == 303 and cookie
    sec = web.request("GET", "/api/security", cookie=cookie)["json"]
    assert sec["password"]["source"] == "legacy_file"
    assert web.request("POST", "/api/security/password", cookie=cookie, body={"old": "legacy-password-77", "new": "x" * 12})["status"] == 409
    # the hashed record wins over password_file (K4)
    sb.py("from agentloadout import web_password; web_password.set_password('hashed-password-1', 'legacy-password-77')",
          extra_env={"AGENTLOADOUT_PASSWORD_FILE": str(legacy)})
    assert web.request("GET", "/api/items", cookie=cookie)["status"] == 401  # old session ended
    assert web.login("legacy-password-77")[0]["status"] == 401
    assert web.login("hashed-password-1")[0]["status"] == 303


# ------------------------------------------------------------------ cookies (H5, H6)
def test_cookie_name_is_per_instance_and_secure_only_over_https(sb, pweb, tmp_path):
    from conftest import Sandbox
    _, plain = pweb.login(PW)
    iid = (sb.al / "instance_id").read_text().strip()
    assert plain.startswith(f"al_sesi_{iid[:8]}=")
    r, _ = pweb.login(PW)
    assert "Secure" not in r["cookies"][0]
    r, _ = pweb.login(PW, headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "100.64.0.2"})
    assert r["status"] == 303 and "; Secure" in r["cookies"][0]
    other = Sandbox(tmp_path / "other")
    other.catalog()
    other.set_password(PW)
    web2 = other.ui()
    _, c2 = web2.login(PW)
    assert c2.split("=")[0] != plain.split("=")[0]
    assert pweb.request("GET", "/api/items", cookie=f"{plain}; {c2}")["status"] == 200
    assert web2.request("GET", "/api/items", cookie=f"{plain}; {c2}")["status"] == 200


# ------------------------------------------------------------------ sessions (H12) and password changes
def test_logout_all_ends_other_sessions(sb, pweb):
    _, a = pweb.login(PW)
    _, b = pweb.login(PW)
    r = pweb.request("POST", "/api/security/logout-all", cookie=a, body={})
    assert r["status"] == 200
    fresh = r["cookies"][0].split(";")[0]
    assert pweb.request("GET", "/api/items", cookie=b)["status"] == 401
    assert pweb.request("GET", "/api/items", cookie=a)["status"] == 401
    assert pweb.request("GET", "/api/items", cookie=fresh)["status"] == 200


def test_web_password_change_needs_the_old_password(sb, pweb):
    _, a = pweb.login(PW)
    _, b = pweb.login(PW)
    bad = pweb.request("POST", "/api/security/password", cookie=a, body={"old": "not-the-password", "new": "new-password-99"})
    assert bad["status"] == 403
    short = pweb.request("POST", "/api/security/password", cookie=a, body={"old": PW, "new": "short"})
    assert short["status"] == 400
    ok = pweb.request("POST", "/api/security/password", cookie=a, body={"old": PW, "new": "new-password-99"})
    assert ok["status"] == 200
    assert pweb.request("GET", "/api/items", cookie=b)["status"] == 401
    assert pweb.request("GET", "/api/items", cookie=ok["cookies"][0].split(";")[0])["status"] == 200
    assert json.loads((sb.al / "web_password.json").read_text())["set_by"] == "web"
    assert pweb.login("new-password-99")[0]["status"] == 303


def test_no_web_endpoint_creates_passwords_or_touches_the_pin(sb, web):
    assert web.request("POST", "/api/security/password", body={"old": "", "new": "abcdefghijkl"})["status"] == 409
    for path in ("/api/security/pin", "/api/security/reset", "/api/security/password/reset", "/api/pin"):
        assert web.request("POST", path, body={"pin": "12345678"})["status"] == 404
    assert not (sb.al / "web_password.json").exists() and not (sb.al / "approval_pin.json").exists()


def test_cross_site_post_is_refused(sb, web):
    r = web.request("POST", "/api/plans", headers={"Content-Type": "application/json", "X-Agentloadout": "0"},
                    body={"item_id": "acme/demo-mcp", "targets": ["cursor"]})
    assert r["status"] == 403


# ------------------------------------------------------------------ CLI set-password
def test_set_password_needs_terminal(sb):
    r = sb.cli("set-password")
    assert r.returncode == 2 and not (sb.al / "web_password.json").exists()


@pytest.mark.parametrize("marker", AGENT_MARKERS)
def test_set_password_refused_inside_agent(sb, marker):
    rc, out = sb.pty("set-password", extra_env={marker: "1"},
                     answers=[("Password baru", PW), ("Ulangi", PW)])
    assert rc == 2 and "ditolak" in out
    assert not (sb.al / "web_password.json").exists()


def test_set_password_flow_and_reset_ends_sessions(sb):
    sb.catalog()
    rc, out = sb.pty("set-password", answers=[("Password baru", "short"), ("Ulangi", "short")])
    assert rc == 2 and "10" in out
    rc, out = sb.pty("set-password", answers=[("Password baru", PW), ("Ulangi", PW)])
    assert rc == 0, out
    assert PW not in out
    rc, out = sb.pty("set-password", answers=[("Password lama", "wrong-old-pass"), ("Password baru", "x" * 12), ("Ulangi", "x" * 12)])
    assert rc == 2 and "set-password --reset" in out
    web = sb.ui()
    _, cookie = web.login(PW)
    assert web.request("GET", "/api/items", cookie=cookie)["status"] == 200
    rc, out = sb.pty("set-password", "--reset", answers=[("Password baru", "fresh-password-1"), ("Ulangi", "fresh-password-1")])
    assert rc == 0, out
    rec = json.loads((sb.al / "web_password.json").read_text())
    assert rec["set_by"] == "cli-reset" and rec["reset_at"]
    assert web.request("GET", "/api/items", cookie=cookie)["status"] == 401
    assert web.login("fresh-password-1")[0]["status"] == 303
    rc, out = sb.pty("set-password", "--off", answers=[("Password lama", "wrong-old-pass")])
    assert rc == 2 and (sb.al / "web_password.json").exists()
    rc, out = sb.pty("set-password", "--off", answers=[("Password lama", "fresh-password-1")])
    assert rc == 0 and not (sb.al / "web_password.json").exists()
    w = sb.cli("where")
    assert "web password: not set" in w.stdout
