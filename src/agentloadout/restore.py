"""Restore the catalog database from a backup.

  python3 restore.py --list                 # show available backups
  python3 restore.py <file.db>              # restore (stops/starts the web service)
  python3 restore.py <file.db> --target X   # restore into another path (for testing)

Before replacing, the current database is saved as katalog.db.before-restore-<time>.
"""
import argparse
import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys

from . import db, paths  # noqa: E402
from .backup import default_dir  # noqa: E402

SERVICE = "katalog-agen"


def check(path):
    con = sqlite3.connect(path)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("items", "star_snapshots", "installs")}
    con.close()
    return ok, counts


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("backup", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--target", default=str(paths.db_path()))
    a = ap.parse_args()

    if a.list or not a.backup:
        root = default_dir()
        for kind in ("daily", "weekly"):
            folder = os.path.join(root, kind)
            files = sorted(f for f in os.listdir(folder) if f.endswith(".db")) if os.path.isdir(folder) else []
            print(f"{kind}:", *(os.path.join(folder, f) for f in files) or ["(kosong)"], sep="\n  ")
        return

    ok, counts = check(a.backup)
    if ok != "ok":
        sys.exit(f"cadangan rusak ({ok}), dibatalkan")
    live = os.path.abspath(a.target) == str(paths.db_path())
    if live:
        systemctl("stop", SERVICE)
    try:
        if os.path.exists(a.target):
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(a.target, f"{a.target}.before-restore-{stamp}")
        for ext in ("-wal", "-shm"):
            if os.path.exists(a.target + ext):
                os.remove(a.target + ext)
        src = sqlite3.connect(a.backup)
        dst = sqlite3.connect(a.target)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    finally:
        if live:
            systemctl("start", SERVICE)
    ok2, counts2 = check(a.target)
    print(f"dipulihkan ke {a.target}: integrity {ok2}, {counts2}")


if __name__ == "__main__":
    main()
