"""GitHub token: source order (K2), storage, CLI, web settings API, and the token never leaking (BRIEF-04 H4-H10)."""
import json
import stat

import pytest

from conftest import fake_token


GOOD = fake_token("DUMMYGOOD0123456789abcdefghijkWXYZ")           # accepted by the GitHub mock
GOOD_PAT = fake_token("DUMMYGOOD_0123456789abcdefPATQ", "fine-grained")    # fine-grained, reports an expiry
BAD = fake_token("DUMMYBAD00123456789abcdefghijklBADX")            # 401 from the mock
CFG = fake_token("DUMMYGOODCONFIG0123456789abcdefCFGA")           # CEO-style token_file in config.toml
ENVT = fake_token("DUMMYGOODENV0123456789abcdefghiENVB")
ALL_TOKENS = (GOOD, GOOD_PAT, BAD, CFG, ENVT)


def info(sb, extra_env=None):
    r = sb.py("import json\nfrom agentloadout import config\nprint(json.dumps(config.github_token_info()))", extra_env=extra_env)
    return json.loads(r.stdout.strip().splitlines()[-1])


def ceo_style(sb):
    """data folder whose config.toml points token_file at a file (like the owner's instance)."""
    sb.al.mkdir(parents=True, exist_ok=True)
    secret = sb.root / "vault" / "GITHUB_TOKEN"
    secret.parent.mkdir()
    secret.write_text(CFG + "\n")
    (sb.al / "config.toml").write_text(f'[github]\ntoken_file = "{secret}"\n')
    return secret


def no_token_text(sb, *texts):
    blob = "\n".join(texts)
    for f in sb.root.rglob("*"):
        if f.is_file() and f.suffix in (".log", ".json") and "vault" not in f.parts and f.name != "github_token":
            blob += f.read_text(errors="replace")
    for t in ALL_TOKENS:
        assert t not in blob


# ------------------------------------------------------------------ order (K2) and the owner's instance
def test_ceo_style_instance_keeps_using_the_same_token(sb):
    ceo_style(sb)
    i = info(sb)
    assert i["active"] == "config" and i["last4"] == CFG[-4:]
    r = sb.py("from agentloadout import collector\nprint(collector._tok[-4:], collector._tok_source)")
    assert r.stdout.split()[-2:] == [CFG[-4:], "config"]
    assert CFG not in json.dumps(i)


def test_source_order_and_shadowing(sb):
    ceo_style(sb)
    sb.py(f"from agentloadout import token_store; token_store.save({GOOD!r}, 'test')")
    i = info(sb)
    assert i["active"] == "managed" and i["last4"] == GOOD[-4:]
    by = {s["source"]: s for s in i["sources"]}
    assert by["config"]["present"] and by["config"]["shadowed"] and not by["managed"]["shadowed"]
    envfile = sb.root / "envfile"
    envfile.write_text(GOOD_PAT)
    assert info(sb, {"GITHUB_TOKEN_FILE": str(envfile)})["active"] == "env_file"
    i = info(sb, {"GITHUB_TOKEN_FILE": str(envfile), "GITHUB_TOKEN": ENVT})
    assert i["active"] == "env" and i["last4"] == ENVT[-4:] and i["kind"] == "classic"
    assert [s["source"] for s in i["sources"]] == ["env", "env_file", "managed", "config"]
    assert all(t not in json.dumps(i) for t in ALL_TOKENS)


# ------------------------------------------------------------------ storage
def test_store_is_atomic_0600_validated_and_clear_only_touches_managed(sb):
    secret = ceo_style(sb)
    sb.py(f"from agentloadout import token_store; token_store.save({GOOD!r}, 'test')")
    f = sb.al / "github_token"
    assert f.read_text().strip() == GOOD and stat.S_IMODE(f.stat().st_mode) == 0o600
    meta = json.loads((sb.al / "github_token.meta.json").read_text())
    assert meta["set_by"] == "test" and GOOD not in json.dumps(meta)
    assert not list(sb.al.glob("github_token*.tmp"))
    for bad in ("short", "ghp_has space 0123456789abcdef", "x" * 300, "ghp_line\nbreak0123456789abcdef"):
        r = sb.py(f"from agentloadout import token_store; token_store.save({bad!r}, 't')", check=False)
        assert r.returncode != 0 and "Format token" in r.stderr
    assert f.read_text().strip() == GOOD
    sb.py("from agentloadout import token_store; assert token_store.clear()")
    assert not f.exists() and secret.read_text().strip() == CFG and info(sb)["active"] == "config"


def test_expiry_parser_is_lenient():
    from agentloadout.github_check import parse_expiry
    assert parse_expiry("2027-02-14 10:00:00 UTC").startswith("2027-02-14T10:00:00")
    assert parse_expiry("2027-02-14 10:00:00 +0700").startswith("2027-02-14T10:00:00+07:00")
    assert parse_expiry("2027-02-14T10:00:00Z").startswith("2027-02-14T10:00:00")
    assert parse_expiry("kira-kira Februari") is None and parse_expiry(None) is None


# ------------------------------------------------------------------ CLI
def test_cli_set_token(sb):
    r = sb.cli("set-token")
    assert r.returncode == 2 and "--from-env" in r.stderr  # no terminal: hint, no prompt
    r = sb.cli("set-token", "--from-env", extra_env={"GITHUB_TOKEN": BAD})
    assert r.returncode == 2 and "401" in r.stderr and not (sb.al / "github_token").exists()
    r = sb.cli("set-token", "--from-env", extra_env={"GITHUB_TOKEN": GOOD})
    assert r.returncode == 0 and GOOD[-4:] in r.stdout and "classic" in r.stdout
    r2 = sb.cli("where")
    assert "dari disimpan lewat Pengaturan" in r2.stdout and GOOD[-4:] in r2.stdout
    r3 = sb.cli("set-token", "--test")
    assert r3.returncode == 0 and "5000" in r3.stdout
    rc, out = sb.pty("set-token", answers=[("Token GitHub", GOOD_PAT)])
    assert rc == 0 and "fine-grained" in out and "2027-02-14" in out
    r4 = sb.cli("set-token", "--clear")
    assert r4.returncode == 0 and not (sb.al / "github_token").exists()
    no_token_text(sb, r.stdout, r.stderr, r2.stdout, r3.stdout, r4.stdout, out)


def test_setup_token_from_env_uses_the_store(sb):
    (sb.home / ".cursor").mkdir()
    r = sb.cli("setup", "--yes", "--to", "cursor", "--token-from-env", extra_env={"GITHUB_TOKEN": GOOD})
    assert r.returncode == 0 and (sb.al / "github_token").read_text().strip() == GOOD
    assert json.loads((sb.al / "github_token.meta.json").read_text())["set_by"] == "cli-env"
    no_token_text(sb, r.stdout, r.stderr)


# ------------------------------------------------------------------ web settings API (K1)
@pytest.fixture
def web(sb):
    sb.catalog()
    return sb.ui()


def test_web_save_test_clear(sb, web):
    st = web.request("GET", "/api/settings/token")
    assert st["status"] == 200 and st["json"]["active"] is None and st["headers"]["cache-control"] == "no-store"
    bad = web.request("POST", "/api/settings/token", body={"token": BAD})
    assert bad["status"] == 400 and "tidak disimpan" in bad["json"]["error"] and not (sb.al / "github_token").exists()
    fmt = web.request("POST", "/api/settings/token", body={"token": "nope"})
    assert fmt["status"] == 400
    ok = web.request("POST", "/api/settings/token", body={"token": GOOD_PAT})
    assert ok["status"] == 200 and ok["json"]["saved"] and ok["json"]["status"]["active"] == "managed"
    assert ok["json"]["test"]["expires"].startswith("2027-02-14") and ok["json"]["status"]["kind"] == "fine-grained"
    assert json.loads((sb.al / "github_token.meta.json").read_text())["set_by"] == "web-local"
    active = web.request("POST", "/api/settings/token/test", body={})
    assert active["status"] == 200 and active["json"]["test"]["core"]["limit"] == 5000
    st = web.request("GET", "/api/settings/token")["json"]
    assert st["last_check"]["expires"].startswith("2027-02-14")
    scoped = web.request("POST", "/api/settings/token/test", body={"token": fake_token("DUMMYGOODSCOPE0123456789abcdefSCOP")})
    assert "scope" in scoped["json"]["test"]["warning"]
    clr = web.request("POST", "/api/settings/token/clear", body={})
    assert clr["status"] == 200 and not (sb.al / "github_token").exists()
    assert web.request("POST", "/api/settings/token/clear", body={})["status"] == 409
    big = web.request("POST", "/api/settings/token", body={"token": "a" * 2000})
    assert big["status"] == 413
    no_token_text(sb, json.dumps([st, ok, bad, active, scoped, clr]), web.output())


def test_web_token_access_rules(sb, tmp_path):
    sb.catalog()
    sb.set_password("kata-sandi-uji-12")
    web = sb.ui()
    _, cookie = web.login("kata-sandi-uji-12")
    ext = {"X-Forwarded-For": "100.64.0.5"}
    https = {**ext, "X-Forwarded-Proto": "https"}
    assert web.request("POST", "/api/settings/token", headers=ext, cookie=cookie, body={"token": GOOD})["status"] == 403  # LAN http
    assert web.request("GET", "/api/settings/token", headers=ext, cookie=cookie)["status"] == 403
    funnel = {**https, "Tailscale-Funnel-Request": "?1"}
    assert web.request("POST", "/api/settings/token", headers=funnel, cookie=cookie, body={"token": GOOD})["status"] == 403
    assert web.request("POST", "/api/settings/token", headers=https, body={"token": GOOD})["status"] == 401  # no session
    csrf = web.request("POST", "/api/settings/token", headers={**https, "X-Agentloadout": "0"}, cookie=cookie, body={"token": GOOD})
    assert csrf["status"] == 403
    ok = web.request("POST", "/api/settings/token", headers=https, cookie=cookie, body={"token": GOOD})
    assert ok["status"] == 200 and json.loads((sb.al / "github_token.meta.json").read_text())["set_by"] == "web-luar"


def test_token_test_is_rate_limited(sb, web):
    codes = [web.request("POST", "/api/settings/token/test", body={})["status"] for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]


def test_query_string_is_never_logged(sb, web):
    web.request("GET", f"/api/status?token={GOOD}")
    web.request("GET", f"/?x={ENVT}")
    no_token_text(sb, web.output())
    assert "/api/status" in web.output()


def test_saved_token_shadowed_by_env_is_reported(sb):
    sb.catalog()
    web = sb.ui(extra_env={"GITHUB_TOKEN": ENVT})
    r = web.request("POST", "/api/settings/token", body={"token": GOOD})
    assert r["status"] == 200 and "tidak dipakai" in r["json"]["test"]["warning"]
    st = r["json"]["status"]
    assert st["active"] == "env" and [s["shadowed"] for s in st["sources"] if s["source"] == "managed"] == [True]
    no_token_text(sb, json.dumps(r["json"]), web.output())
