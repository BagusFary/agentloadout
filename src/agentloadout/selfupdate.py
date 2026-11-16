"""Upgrade Agent Loadout itself (`agentloadout upgrade`).

Rules (BRIEF-04 K7-K12):
- Only installs made with `uv tool install git+<repo>` can be upgraded here. A git checkout (`uv run`),
  a plain venv or uvx get a clear refusal with the right instructions.
- The source is the repository the tool was installed from, and it must be the official repo pinned in
  the code (REPO). AGENTLOADOUT_UPGRADE_REPO (tests/development) is honoured only when it is exactly the
  URL the current install came from, so it can never point an upgrade somewhere new.
- Only release tags vX.Y.Z of that repo (pre-releases only with --pre). The tag is resolved to a commit
  SHA with `git ls-remote` (no API quota) and installed *by SHA*; afterwards `--version` must equal the
  tag and direct_url.commit_id must equal the SHA. A tag that moved since the last check is refused.
- uv rebuilds the venv in place, so an interrupted or failed install can leave nothing working (H18).
  The old SHA is recorded first and every failure is rolled back by reinstalling that SHA.
- Downgrades need a human at a terminal; below MIN_SAFE also --force-insecure (H19).
- No automatic upgrades. Checks for a newer version are cached in <data>/update_check.json (at most one
  network check per 24 h); the web UI, `where` and MCP only read that cache.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__, paths

REPO = "https://github.com/BagusFary/agentloadout"
MIN_SAFE = (0, 3, 0)
CHECK_EVERY = dt.timedelta(hours=24)
LS_TIMEOUT = 15
NOTES_MAX = 2000
KEEP_BACKUPS = 3
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-?(rc|a|b|alpha|beta|pre)\.?(\d+))?$")


class UpgradeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- versions
def parse_tag(tag: str):
    """(major, minor, patch, pre) where pre is None for a release; None when not a version tag."""
    m = TAG_RE.match(tag or "")
    if not m:
        return None
    pre = (m.group(4), int(m.group(5) or 0)) if m.group(4) else None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)


def vkey(v):
    """Sort key: a pre-release sorts before its release."""
    return (v[0], v[1], v[2], 0 if v[3] else 1, v[3][1] if v[3] else 0)


def parse_version(text: str):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:(rc|a|b)(\d+))?", (text or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), (m.group(4), int(m.group(5))) if m.group(4) else None)


def tag_version(tag: str) -> str:
    v = parse_tag(tag)
    if not v:
        return tag
    s = f"{v[0]}.{v[1]}.{v[2]}"
    if v[3]:
        s += {"alpha": "a", "beta": "b", "pre": "rc"}.get(v[3][0], v[3][0]) + str(v[3][1])
    return s


def norm_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u.startswith("git+"):
        u = u[4:]
    return u[:-4] if u.endswith(".git") else u


# --------------------------------------------------------------------------- where we are installed
def _direct_url() -> dict:
    import importlib.metadata as md
    try:
        return json.loads(md.distribution("agentloadout").read_text("direct_url.json") or "{}")
    except (md.PackageNotFoundError, ValueError, TypeError):
        return {}


def uv_tool_dir() -> str | None:
    if os.environ.get("UV_TOOL_DIR"):
        return os.path.abspath(os.path.expanduser(os.environ["UV_TOOL_DIR"]))
    uv = shutil.which("uv")
    if uv:
        try:
            out = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return os.path.abspath(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "uv", "tools")


def install_kind() -> dict:
    """{kind, url, commit, prefix}. kind: uv-tool-git | uv-tool-other | uvx | checkout | venv | unknown."""
    d = _direct_url()
    prefix = os.path.abspath(sys.prefix)
    url, vcs = d.get("url", ""), (d.get("vcs_info") or {})
    tool_dir = uv_tool_dir()
    in_tool_dir = bool(tool_dir) and prefix.startswith(os.path.join(tool_dir, ""))
    if vcs.get("vcs") == "git":
        kind = "uv-tool-git" if in_tool_dir else "uvx"
    elif (d.get("dir_info") or {}).get("editable") or url.startswith("file://") and "dir_info" in d:
        kind = "checkout"
    elif in_tool_dir:
        kind = "uv-tool-other"
    elif d:
        kind = "venv"
    else:
        kind = "checkout" if (Path(__file__).resolve().parents[2] / "pyproject.toml").exists() else "venv"
    return {"kind": kind, "url": url, "commit": vcs.get("commit_id"), "prefix": prefix}


def source_repo(info: dict | None = None) -> str:
    """The repository upgrades come from: REPO, or (tests) an override that equals the install's own source."""
    info = info or install_kind()
    override = os.environ.get("AGENTLOADOUT_UPGRADE_REPO")
    if override and norm_url(override) == norm_url(info.get("url", "")):
        return info["url"]
    return REPO


def check_repo() -> str:
    """Repository for the read-only version check (banner): the same rule as installing (source_repo), so an
    environment variable can never point the check, its SHAs or its change notes at another repository."""
    return source_repo(install_kind())


# --------------------------------------------------------------------------- remote tags
def ls_remote(repo: str, timeout: int = LS_TIMEOUT) -> dict:
    """{tag: sha}, peeled (^{}) SHAs win for annotated tags. Raises UpgradeError when git fails."""
    git = shutil.which("git")
    if not git:
        raise UpgradeError("git tidak ditemukan; cek versi butuh git")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        out = subprocess.run([git, "ls-remote", "--tags", repo], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        raise UpgradeError("git ls-remote terlalu lama") from e
    if out.returncode != 0:
        raise UpgradeError("git ls-remote gagal: " + (out.stderr.strip().splitlines() or ["?"])[-1][:200])
    tags = {}
    for line in out.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            tags[name[:-3]] = sha
        else:
            tags.setdefault(name, sha)
    return tags


def releases(repo: str, pre: bool = False) -> list[tuple]:
    """[(version tuple, tag, sha)] sorted from newest."""
    out = []
    for tag, sha in ls_remote(repo).items():
        v = parse_tag(tag)
        if v and (pre or not v[3]):
            out.append((v, tag, sha))
    return sorted(out, key=lambda r: vkey(r[0]), reverse=True)


def changelog_section(text: str, version: str) -> str:
    """The `## [X.Y.Z]` section of a Keep-a-Changelog file, plain text, at most NOTES_MAX characters."""
    lines, out, on = (text or "").splitlines(), [], False
    for line in lines:
        if line.startswith("## "):
            if on:
                break
            on = f"[{version}]" in line or line[3:].strip().startswith(version)
            continue
        if on:
            out.append(line)
    s = "\n".join(out).strip()
    return s[:NOTES_MAX] + ("…" if len(s) > NOTES_MAX else "")


def fetch_changelog(repo: str, sha: str) -> str:
    """CHANGELOG.md at a *commit SHA* (raw by tag name can be up to 5 minutes stale, and tags can move)."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        return ""
    if repo.startswith("file://"):
        git = shutil.which("git")
        try:
            out = subprocess.run([git, "--git-dir", repo[len("file://"):], "show", f"{sha}:CHANGELOG.md"],
                                 capture_output=True, text=True, timeout=10)
            return out.stdout if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError, TypeError):
            return ""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)/?$", norm_url(repo))
    if not m:
        return ""
    import requests
    try:
        r = requests.get(f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{sha}/CHANGELOG.md", timeout=10)
        return r.text if r.status_code == 200 else ""
    except requests.RequestException:
        return ""


# --------------------------------------------------------------------------- cache (read by web / where / MCP)
def cache_file() -> Path:
    return paths.home() / "update_check.json"


def read_cache() -> dict | None:
    """No network. Adds `newer` relative to the version of *this* process."""
    try:
        c = json.loads(cache_file().read_text())
    except (OSError, ValueError):
        return None
    cur, lat = parse_version(__version__), parse_version(c.get("latest") or "")
    c["current"] = __version__
    c["newer"] = bool(cur and lat and vkey(lat) > vkey(cur))
    return c


def check(force: bool = False, pre: bool = False) -> dict:
    """Ask the repo for the newest release (at most once per 24 h unless force). Never raises."""
    old = read_cache() or {}
    if not force and old.get("checked_at") and not old.get("error"):
        try:
            if dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(old["checked_at"]) < CHECK_EVERY:
                return old
        except ValueError:
            pass
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    repo = check_repo()
    try:
        rel = releases(repo, pre)
    except UpgradeError as e:
        data = {**{k: old.get(k) for k in ("latest", "tag", "sha", "notes")}, "checked_at": now, "error": str(e)}
        paths.write_private(cache_file(), json.dumps(data))
        return read_cache()
    if not rel:
        data = {"checked_at": now, "latest": None, "tag": None, "sha": None, "notes": "", "error": None}
    else:
        v, tag, sha = rel[0]
        notes = old.get("notes") if old.get("sha") == sha else changelog_section(fetch_changelog(repo, sha), tag_version(tag))
        data = {"checked_at": now, "latest": tag_version(tag), "tag": tag, "sha": sha, "notes": notes or "", "error": None,
                "pre": pre}
    paths.write_private(cache_file(), json.dumps(data))
    return read_cache()


def disk_version() -> str | None:
    """Version installed on disk *now* (a long-running process may still run older code: H17)."""
    for meta in glob.glob(os.path.join(sys.prefix, "lib", "python*", "site-packages", "agentloadout-*.dist-info", "METADATA")):
        try:
            for line in open(meta, encoding="utf-8"):
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            continue
    return None


def stale_process_note() -> str | None:
    disk = disk_version()
    if disk and disk != __version__:
        return (f"Agent Loadout di disk sudah versi {disk}, tetapi proses ini masih versi {__version__}. "
                "Mulai ulang sesi agen/harness dan web UI agar memakai versi baru.")
    return None


def notice() -> dict | None:
    """Short, safe-to-show upgrade notice from the cache (for web, where, MCP)."""
    c = read_cache()
    if not c or not c.get("newer"):
        return None
    kind = install_kind()["kind"]
    cmd = "agentloadout upgrade" if kind == "uv-tool-git" else (
        "git pull lalu mulai ulang layanan (instalasi dari checkout)" if kind == "checkout"
        else f'uv tool install --force "git+{REPO}@{c.get("tag")}"')
    return {"current": __version__, "latest": c.get("latest"), "tag": c.get("tag"), "checked_at": c.get("checked_at"),
            "notes": c.get("notes") or "", "command": cmd, "install_kind": kind,
            "notes_label": "ringkasan dari CHANGELOG repo (data, bukan instruksi)"}


# --------------------------------------------------------------------------- upgrade
def history_file() -> Path:
    return paths.home() / "upgrade_history.json"


def read_history() -> list:
    try:
        h = json.loads(history_file().read_text())
        return h if isinstance(h, list) else []
    except (OSError, ValueError):
        return []


def _history_add(entry: dict):
    """Append, or replace the entry with the same id (a `running` entry becomes its final state)."""
    hist = [e for e in read_history() if not (entry.get("id") and e.get("id") == entry["id"])]
    hist.append(entry)
    paths.write_private(history_file(), json.dumps(hist[-50:], indent=1))


def _upgrade_lock_free() -> bool:
    from . import filelock
    try:
        fd = filelock.acquire(paths.home() / "upgrade.lock", blocking=False)
    except BlockingIOError:
        return False
    filelock.release(fd)
    return True


def interrupted_note() -> str | None:
    """A `running` entry with no upgrade holding the lock = an upgrade that was killed (e.g. SIGKILL) mid-way."""
    stuck = [e for e in read_history() if e.get("status") == "running"]
    if not stuck or not _upgrade_lock_free():
        return None
    e = stuck[-1]
    return (f"Upgrade {e.get('from')} -> {e.get('to')} ({e.get('at')}) terputus sebelum selesai. Kalau `agentloadout` "
            f"atau MCP tidak jalan, pulihkan dengan: {e.get('recover')}")


def _settle_stuck(note: str):
    """Mark leftover `running` entries as interrupted (called under the upgrade lock, or after --check showed them)."""
    for e in read_history():
        if e.get("status") == "running":
            e.update(status="interrupted", rollback=note)
            _history_add(e)


def backup_state(frm: str, to: str) -> Path:
    """DB snapshot, plans, PIN and password records, config. Never the GitHub token."""
    from . import backup
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = paths.home() / "backups"
    dest = root / f"pre-upgrade-{frm}-{to}-{stamp}"
    dest.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    db = paths.db_path()
    if db.exists():
        backup.snapshot(str(db), str(dest / "catalog.db"))
    for name in ("approval_pin.json", "web_password.json", "config.toml", "instance_id"):
        src = paths.home() / name
        if src.exists():
            shutil.copy2(src, dest / name)
            os.chmod(dest / name, 0o600)
    if (paths.home() / "plans").is_dir():
        shutil.copytree(paths.home() / "plans", dest / "plans")
    olds = sorted(p for p in root.glob("pre-upgrade-*") if p.is_dir())
    for p in olds[:-KEEP_BACKUPS]:
        shutil.rmtree(p, ignore_errors=True)
    return dest


def _bin() -> str:
    return os.path.join(os.path.dirname(sys.executable), "agentloadout" + (".exe" if os.name == "nt" else ""))


def _installed() -> tuple[str | None, str | None]:
    """(version from `agentloadout --version`, direct_url.commit_id) of the files on disk, from a new process."""
    ver = None
    try:
        out = subprocess.run([_bin(), "--version"], capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            ver = out.stdout.strip().split()[-1]
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    commit = None
    for f in glob.glob(os.path.join(sys.prefix, "lib", "python*", "site-packages", "agentloadout-*.dist-info", "direct_url.json")):
        try:
            commit = (json.loads(open(f).read()).get("vcs_info") or {}).get("commit_id")
        except (OSError, ValueError):
            pass
    return ver, commit


def _uv_install(repo: str, sha: str, python: str) -> tuple[bool, str]:
    uv = shutil.which("uv")
    if not uv:
        return False, "uv tidak ditemukan di PATH"
    # the base interpreter, not the tool venv's own python (that venv is about to be rebuilt)
    cmd = [uv, "tool", "install", "--force", "--python", python, f"git+{repo}@{sha}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "uv tool install terlalu lama"
    tail = (out.stderr or out.stdout).strip().splitlines()[-3:]
    return out.returncode == 0, " | ".join(tail)[:400]


def stop_ondemand_ui() -> str:
    """Stop this instance's on-demand web UI (started by open_dashboard) after an upgrade; report others."""
    from .web import server as web
    ui = web.read_ui()
    if not ui:
        return "web UI tidak berjalan"
    pid = ui.get("pid")
    try:
        cmdline = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    except OSError:
        return "web UI tidak berjalan"
    args = [a.decode(errors="replace") for a in cmdline]
    ours = (web.instance_at(ui.get("host", "127.0.0.1"), ui.get("port")) == paths.instance_id()
            and "ui" in args and any("agentloadout" in a for a in args))
    if ours and "--on-demand" in args:
        os.kill(pid, signal.SIGTERM)
        return f"web UI on-demand (pid {pid}) dihentikan; buka lagi lewat agen atau `agentloadout ui`"
    return f"web UI di port {ui.get('port')} masih versi lama: mulai ulang (misalnya `systemctl --user restart …` atau `agentloadout ui`)"


def upgrade(to: str | None = None, pre: bool = False, yes: bool = False, force_insecure: bool = False,
            check_only: bool = False, out=print, ask=None) -> int:
    from . import refresh
    info = install_kind()
    if check_only:
        stuck = interrupted_note()
        if stuck:
            out("PERINGATAN: " + stuck)
            if info.get("commit") and _installed()[1] == info["commit"]:
                _settle_stuck(f"tidak selesai; instalasi ternyata utuh ({__version__}), dicatat oleh upgrade --check")
        c = check(force=True, pre=pre)
        if c.get("error"):
            out(f"Cek versi gagal: {c['error']}")
            return 1
        if c.get("newer"):
            out(f"Versi baru tersedia: {c['latest']} (sekarang {__version__}).")
            if c.get("notes"):
                out("Perubahan (dari CHANGELOG repo):\n" + c["notes"])
            out("Perbarui dengan: agentloadout upgrade")
        else:
            out(f"Sudah versi terbaru ({__version__}).")
        return 0

    kind = info["kind"]
    if kind != "uv-tool-git":
        hint = {"checkout": "Ini instalasi dari checkout git (misalnya `uv run`). Perbarui dengan `git pull` di folder itu, "
                            "lalu mulai ulang layanan/web UI.",
                "uvx": f"Ini dijalankan lewat uvx (sementara). Pasang dulu sebagai tool: uv tool install \"git+{REPO}@<tag>\".",
                "venv": f"Ini instalasi di venv biasa. Perbarui dengan pip di venv itu, atau pasang sebagai tool: "
                        f"uv tool install \"git+{REPO}@<tag>\".",
                "uv-tool-other": f"Tool ini tidak dipasang dari git. Pasang ulang: uv tool install --force \"git+{REPO}@<tag>\"."}
        out("upgrade hanya untuk instalasi `uv tool install git+…`. " + hint.get(kind, "Jenis instalasi tidak dikenali."))
        return 2
    repo = source_repo(info)
    if norm_url(info["url"]) != norm_url(repo):
        out(f"upgrade ditolak: tool ini dipasang dari {info['url']}, bukan dari repo resmi {REPO}.")
        return 2
    from . import filelock
    try:  # one upgrade at a time
        up_fd = filelock.acquire(paths.home() / "upgrade.lock", blocking=False)
    except BlockingIOError:
        out("upgrade ditolak: upgrade lain sedang berjalan.")
        return 2
    ref_fd = refresh.try_lock()  # held until verified: no catalog refresh may start while the venv is rebuilt
    if ref_fd is None:
        filelock.release(up_fd)
        out("upgrade ditolak: pembaruan katalog sedang berjalan. Tunggu sampai selesai.")
        return 2
    in_main = threading.current_thread() is threading.main_thread() and os.name != "nt"
    old_int = signal.getsignal(signal.SIGINT) if in_main else None
    old_term = signal.signal(signal.SIGTERM, _on_term) if in_main else None  # a harness kill takes the same path as Ctrl-C
    marked = _mark_upgrading()
    try:
        _settle_stuck("terputus (proses mati tanpa sempat mencatat; misalnya SIGKILL)")
        return _upgrade_locked(info, repo, to, pre, yes, force_insecure, out, ask)
    finally:
        if in_main:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
        _unmark_upgrading(marked)
        filelock.release(ref_fd)
        filelock.release(up_fd)


def _on_term(signum, frame):
    raise SystemExit(f"dihentikan oleh sinyal {signum}")


def _ignore_signals():
    if threading.current_thread() is threading.main_thread() and os.name != "nt":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _mark_upgrading():
    """While the upgrade holds the refresh lock, the web UI says so instead of "Memulai pembaruan" (S19)."""
    if not paths.db_path().exists():
        return None
    try:
        from . import db
        con = db.connect()
        prev = db.get_meta(con, "progress") or {}
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        db.set_meta(con, "progress", {"running": True, "step": 0, "steps": 4, "label": "Agent Loadout sedang di-upgrade",
                                      "source": "upgrade", "started_at": now, "at": now})
        con.commit()
        con.close()
        return prev
    except Exception:  # noqa: BLE001
        return None


def _unmark_upgrading(prev):
    if prev is None:
        return
    try:
        from . import db
        con = db.connect()
        db.set_meta(con, "progress", prev)
        con.commit()
        con.close()
    except Exception:  # noqa: BLE001
        pass


def _upgrade_locked(info, repo, to, pre, yes, force_insecure, out, ask) -> int:
    from . import pin, plans
    approved = [p["id"] for p in plans.list_plans("approved")]
    if approved:
        out(f"upgrade ditolak: ada rencana yang sudah disetujui tetapi belum dipasang ({', '.join(approved)}). "
            "Minta agen menjalankan apply_plan, atau tolak rencananya dulu.")
        return 2

    human = sys.stdin.isatty() and not pin.agent_markers()
    try:
        rel = releases(repo, pre=pre or bool(to and parse_tag(to) and parse_tag(to)[3]))
    except UpgradeError as e:
        out(f"Cek versi gagal: {e}")
        return 1
    if not rel:
        out("Repo tidak punya tag rilis.")
        return 1
    if to:
        hit = [r for r in rel if r[1] == to]
        if not hit:
            out(f"Tag {to} tidak ada di repo resmi. Pilihan: {', '.join(r[1] for r in rel[:8])}")
            return 2
        target = hit[0]
    else:
        target = rel[0]
    tv, tag, sha = target
    cur = parse_version(__version__)
    old_sha = info["commit"]
    if tag_version(tag) == __version__ and sha == old_sha:
        out(f"Sudah versi {__version__} ({sha[:7]}).")
        return 0
    cached = read_cache() or {}
    if cached.get("tag") == tag and cached.get("sha") and cached["sha"] != sha:
        out(f"upgrade ditolak: tag {tag} berpindah dari {cached['sha'][:7]} ke {sha[:7]} sejak dicek. Kalau itu disengaja "
            "oleh pemilik repo, jalankan `agentloadout upgrade --check` lalu ulangi.")
        return 2
    downgrade = cur and vkey(tv) < vkey(cur)
    if (to or downgrade or pre) and not human:
        out("upgrade ditolak: --to, --pre (pre-release) dan downgrade hanya boleh dijalankan pengguna di terminalnya "
            "sendiri (bukan agen).")
        return 2
    if vkey(tv) < vkey(MIN_SAFE + (None,)):
        if not force_insecure:
            out(f"upgrade ditolak: {tag} lebih lama dari {'.'.join(map(str, MIN_SAFE))}. Versi itu tidak mengenal password ber-hash "
                "dan gerbang akses luar, jadi web bisa terbuka tanpa password. Kalau benar-benar perlu: --force-insecure.")
            return 2
        out(f"PERINGATAN: {tag} tidak aman (web tanpa password ber-hash dan tanpa gerbang akses luar, tanpa pin_gen).")
        if (ask or input)("Ketik INSECURE untuk tetap memasang: ").strip() != "INSECURE":
            out("Dibatalkan.")
            return 1
    action = "downgrade" if downgrade else "upgrade"
    out(f"{action}: {__version__} ({(old_sha or '?')[:7]}) -> {tag_version(tag)} ({sha[:7]}) dari {repo}")
    if not yes:
        if not sys.stdin.isatty():
            out("Tidak ada terminal untuk konfirmasi. Jalankan dengan --yes.")
            return 2
        if (ask or input)("Lanjutkan? [y/N] ").strip().lower() not in ("y", "ya", "yes"):
            out("Dibatalkan.")
            return 1
    from .web import server as _web  # noqa: F401  (load now: after uv rebuilds the venv, lazy imports would read new files)
    python = os.path.realpath(sys.executable)
    entry = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "from": __version__, "from_sha": old_sha,
             "to": tag_version(tag), "to_sha": sha, "tag": tag, "by": "terminal" if human else "agent/non-tty"}
    try:
        bk = backup_state(__version__, tag_version(tag))
        out(f"  cadangan state: {bk}")
        entry["backup"] = str(bk)
    except Exception as e:  # noqa: BLE001
        out(f"upgrade dibatalkan: cadangan gagal ({e}). Tidak ada yang diubah.")
        return 1

    recover = f'uv tool install --force "git+{repo}@{old_sha or "v" + __version__}"'
    entry.update(id=f"{entry['at']}-{os.getpid()}", status="running", repo=repo, recover=recover)
    try:
        _history_add(entry)  # written before uv touches anything: even SIGKILL leaves a trace and the recovery command
        out(f"  Kalau terputus, pulihkan dengan: {recover}")
        sys.stdout.flush()
        ok, detail = _uv_install(repo, sha, python)
        ver, commit = _installed()
    except (KeyboardInterrupt, SystemExit) as e:
        _ignore_signals()  # the restore below must not be interrupted as well
        why = "dihentikan (Ctrl-C)" if isinstance(e, KeyboardInterrupt) else (str(e) or "dihentikan")
        out(f"\n  ✗ upgrade terputus: {why}. Memulihkan versi lama, jangan hentikan lagi…")
        entry.update(status="interrupted", error=why)
        _restore_old(repo, old_sha, python, entry, out)
        return 130
    if ok and ver == tag_version(tag) and commit == sha:
        entry["status"] = "ok"
        _history_add(entry)
        out(f"  ✓ terpasang {ver} ({commit[:7]}), path MCP tetap: {_bin()} mcp")
        out("  " + stop_ondemand_ui())
        out("Mulai sesi baru di harness/agen agar MCP memakai versi baru (tidak perlu daftar ulang).")
        return 0
    why = detail if not ok else f"verifikasi gagal: --version={ver!r}, commit={commit and commit[:7]!r}, harusnya {tag_version(tag)}/{sha[:7]}"
    out(f"  ✗ {why}")
    entry.update(status="failed", error=why)
    _ignore_signals()  # a rollback must not be interrupted either
    _restore_old(repo, old_sha, python, entry, out)
    return 1


def _restore_old(repo, old_sha, python, entry, out) -> bool:
    """Bring back the version that was installed before. Judged by what is installed (version + commit), never by
    uv's exit code; when the old install is still complete nothing is reinstalled (S15)."""
    ver, commit = _installed()
    if old_sha and commit == old_sha and ver == __version__:
        entry["rollback"] = "tidak perlu: instalasi lama masih utuh"
        _history_add(entry)
        out(f"  instalasi lama masih utuh: {ver} ({old_sha[:7]}). Tidak ada yang berubah.")
        return True
    if not old_sha:
        entry["rollback"] = "tidak bisa: SHA lama tidak diketahui"
        _history_add(entry)
        out(f"Rollback tidak bisa (SHA lama tidak diketahui). Pasang ulang manual: {entry.get('recover')}")
        return False
    out(f"  rollback ke {__version__} ({old_sha[:7]})…")
    rok, rdetail = _uv_install(repo, old_sha, python)
    rver, rcommit = _installed()
    if rcommit == old_sha and rver == __version__:
        entry["rollback"] = "ok"
        _history_add(entry)
        out(f"  ✓ kembali ke {rver} ({old_sha[:7]}). Tidak ada yang berubah.")
        return True
    entry["rollback"] = f"gagal: {rdetail}"
    _history_add(entry)
    out("  ✗ rollback juga gagal. Pasang ulang manual:\n"
        f"    {entry.get('recover')}\n"
        f"  Data dan cadangan tetap aman di {paths.home()} ({entry.get('backup')}).")
    return False
