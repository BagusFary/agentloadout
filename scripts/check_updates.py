"""Check newer versions for installed catalog items (npm, PyPI, or latest git commit).

Usage: GITHUB_TOKEN_FILE=... python3 check_updates.py
"""
import datetime as dt
import os
import sys
from urllib.parse import quote

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
import db  # noqa: E402

UA = {"User-Agent": "agentloadout/1.0"}


def gh_headers():
    h = dict(UA, Accept="application/vnd.github+json")
    path = os.environ.get("GITHUB_TOKEN_FILE")
    if path and os.path.exists(path):
        h["Authorization"] = "Bearer " + open(path).read().strip()
    return h


def latest(package: str):
    kind, _, name = package.partition(":")
    if kind == "npm":
        r = requests.get(f"https://registry.npmjs.org/{quote(name, safe='@')}/latest", headers=UA, timeout=20)
        return r.json().get("version") if r.ok else None
    if kind == "pypi":
        r = requests.get(f"https://pypi.org/pypi/{name}/json", headers=UA, timeout=20)
        return r.json()["info"]["version"] if r.ok else None
    if kind == "git":
        repo, _, branch = name.partition("@")
        r = requests.get(f"https://api.github.com/repos/{repo}/commits/{branch or 'HEAD'}", headers=gh_headers(), timeout=20)
        return r.json().get("sha", "")[:7] if r.ok else None
    return None


def main():
    con = db.connect()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    found = []
    for r in con.execute("SELECT id, package, installed_version FROM installs WHERE status='installed' AND package IS NOT NULL").fetchall():
        try:
            v = latest(r["package"])
        except requests.RequestException as e:
            print(f"  {r['id']}: gagal cek ({e.__class__.__name__})")
            continue
        if v:
            con.execute("UPDATE installs SET latest_version=? WHERE id=?", (v, r["id"]))
            if r["installed_version"] and v != r["installed_version"] and not v.startswith(r["installed_version"]):
                found.append(f"{r['id']} {r['installed_version']} -> {v}")
    db.set_meta(con, "last_update_check", {"at": now, "updates": found})
    con.commit()
    print("update tersedia:", found or "tidak ada")


if __name__ == "__main__":
    main()
