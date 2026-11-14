"""Record install status of a catalog item so the web shows it.

Usage:
  python3 set_status.py <owner/repo> <status> "<detail>" [--ref mcp:rea] [--package npm:rea-agents]
                        [--version 6.9.0] [--enabled 1|0] [--skill-path skills/foo]

status: requested | review | installed | failed | removed
  --ref         what it is inside OpenBot: mcp:<server name> | skill:<local-skill id> | app:<command>
  --package     where newer versions are checked: npm:<pkg> | pypi:<pkg> | git:<owner/repo>
  --version     installed version (npm/pypi version or git commit sha)
  --enabled     1 when on, 0 when switched off (MCP)
  --skill-path  folder inside the repo when one skill of a multi-skill repo was installed
"""
import argparse
import datetime as dt
import os
import sys

from . import db, paths  # noqa: E402

VALID = {"requested", "review", "installed", "failed", "removed"}


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("id")
    ap.add_argument("status", choices=sorted(VALID))
    ap.add_argument("detail", nargs="?", default="")
    ap.add_argument("--ref")
    ap.add_argument("--package")
    ap.add_argument("--version")
    ap.add_argument("--enabled", type=int, choices=[0, 1])
    ap.add_argument("--skill-path")
    a = ap.parse_args()

    item_id = a.id.strip().lower()
    con = db.connect()
    if not con.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
        print(f"peringatan: {item_id} tidak ada di katalog, status tetap dicatat")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO installs(id, status, detail, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at",
        (item_id, a.status, a.detail[:500], now),
    )
    for col, val in (("ref", a.ref), ("package", a.package), ("installed_version", a.version),
                     ("enabled", a.enabled), ("skill_path", a.skill_path)):
        if val is not None:
            con.execute(f"UPDATE installs SET {col}=? WHERE id=?", (val, item_id))
    if a.status == "installed" and a.enabled is None:
        con.execute("UPDATE installs SET enabled=COALESCE(enabled, 1) WHERE id=?", (item_id,))
    if a.status == "removed":
        con.execute("UPDATE installs SET enabled=NULL, latest_version=NULL WHERE id=?", (item_id,))
    con.commit()
    row = dict(con.execute("SELECT * FROM installs WHERE id=?", (item_id,)).fetchone())
    print({k: v for k, v in row.items() if v is not None})


if __name__ == "__main__":
    main()
