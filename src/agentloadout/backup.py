"""Consistent daily backup of the catalog database with rotation.

- Uses SQLite's online backup API (safe while the web server reads the DB).
- Verifies each copy with PRAGMA integrity_check.
- Keeps the last 7 daily and 4 weekly copies.
- Also writes a small JSON export of the data GitHub cannot give back
  (star history, install status, translations).

Usage: python3 backup.py [--dir ~/Backups/agentloadout]
Env:   KATALOG_BACKUP_DIR overrides the default folder.
"""
import argparse
import datetime as dt
import gzip
import json
import os
import sqlite3
import sys

from . import db, paths  # noqa: E402

KEEP_DAILY = 7
KEEP_WEEKLY = 4
IRREPLACEABLE = ("star_snapshots", "installs", "translations", "meta")


def default_dir():
    if os.environ.get("KATALOG_BACKUP_DIR"):
        return os.path.expanduser(os.environ["KATALOG_BACKUP_DIR"])
    return str(paths.backup_dir())


def snapshot(src_path, dst_path):
    tmp = dst_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect(src_path, timeout=30)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    src.close()
    if ok != "ok":
        os.remove(tmp)
        raise RuntimeError(f"integrity_check gagal: {ok}")
    os.chmod(tmp, 0o600)
    os.replace(tmp, dst_path)


def export_state(db_path, out_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    data = {t: [dict(r) for r in con.execute(f"SELECT * FROM {t}")] for t in IRREPLACEABLE}
    con.close()
    with gzip.open(out_path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.chmod(out_path, 0o600)
    return {t: len(v) for t, v in data.items()}


def rotate(folder, keep):
    files = sorted(f for f in os.listdir(folder) if f.startswith("katalog-") and f.endswith((".db", ".state.json.gz")))
    days = sorted({f.split(".")[0] for f in files}, reverse=True)
    removed = 0
    for old in days[keep:]:
        for f in files:
            if f.startswith(old + "."):
                os.remove(os.path.join(folder, f))
                removed += 1
    return removed


def main():
    main_args(None)


def main_args(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=default_dir())
    a = ap.parse_args(argv)
    daily = os.path.join(a.dir, "daily")
    weekly = os.path.join(a.dir, "weekly")
    os.makedirs(daily, exist_ok=True)
    os.makedirs(weekly, exist_ok=True)
    for d in (a.dir, daily, weekly):
        os.chmod(d, 0o700)

    src = str(paths.db_path())
    if not os.path.exists(src):
        print("database belum ada, tidak ada yang dicadangkan")
        return
    stamp = dt.date.today().isoformat()
    base = os.path.join(daily, f"katalog-{stamp}")
    snapshot(src, base + ".db")
    counts = export_state(base + ".db", base + ".state.json.gz")

    # weekly copy: when the newest weekly copy is 7+ days old (or none exists)
    weeklies = sorted(f for f in os.listdir(weekly) if f.endswith(".db"))
    last = dt.date.fromisoformat(weeklies[-1][8:18]) if weeklies else None
    made_weekly = False
    if last is None or (dt.date.today() - last).days >= 7:
        wbase = os.path.join(weekly, f"katalog-{stamp}")
        snapshot(base + ".db", wbase + ".db")
        export_state(wbase + ".db", wbase + ".state.json.gz")
        made_weekly = True

    r1 = rotate(daily, KEEP_DAILY)
    r2 = rotate(weekly, KEEP_WEEKLY)
    size = os.path.getsize(base + ".db") / 1e6
    print(f"cadangan OK: {base}.db ({size:.1f} MB), state {counts}"
          f"{', + salinan mingguan' if made_weekly else ''}; dirotasi {r1 + r2} file lama")


if __name__ == "__main__":
    main()
