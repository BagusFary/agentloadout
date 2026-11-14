"""Sync catalog install status with what is really installed in OpenBot.

Noah writes a snapshot JSON from OpenBot tools, then runs this script:
  {
    "mcp":    [{"name": "rea", "enabled": true}, ...],          # openbot.list_mcp_servers
    "skills": [{"id": "local-skill-...", "slug": "..."}, ...]   # openbot.list_local_skills
  }
Usage: python3 sync_state.py /path/to/snapshot.json
"""
import datetime as dt
import json
import os
import sys

from .. import db  # noqa: E402


def main():
    snap = json.load(open(sys.argv[1]))
    mcp = {m["name"]: bool(m.get("enabled", True)) for m in snap.get("mcp", []) if m.get("name")}
    skills = {s["id"] for s in snap.get("skills", []) if s.get("id")}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = db.connect()
    changes = []
    for r in con.execute("SELECT * FROM installs WHERE status='installed' AND ref IS NOT NULL").fetchall():
        kind, _, name = r["ref"].partition(":")
        if kind == "mcp":
            if name not in mcp:
                con.execute("UPDATE installs SET status='removed', enabled=NULL, detail=?, updated_at=?, synced_at=? WHERE id=?",
                            (f"Server MCP '{name}' tidak ada lagi di OpenBot", now, now, r["id"]))
                changes.append(f"{r['id']}: dihapus dari OpenBot")
            else:
                on = 1 if mcp[name] else 0
                if r["enabled"] != on:
                    changes.append(f"{r['id']}: {'nyala' if on else 'mati'}")
                con.execute("UPDATE installs SET enabled=?, synced_at=? WHERE id=?", (on, now, r["id"]))
        elif kind == "skill":
            if name not in skills:
                con.execute("UPDATE installs SET status='removed', enabled=NULL, detail=?, updated_at=?, synced_at=? WHERE id=?",
                            (f"Skill lokal '{name}' tidak ada lagi di OpenBot", now, now, r["id"]))
                changes.append(f"{r['id']}: skill dihapus dari OpenBot")
            else:
                con.execute("UPDATE installs SET synced_at=? WHERE id=?", (now, r["id"]))
        else:
            con.execute("UPDATE installs SET synced_at=? WHERE id=?", (now, r["id"]))
    db.set_meta(con, "last_sync", {"at": now, "changes": changes})
    con.commit()
    print("perubahan:", changes or "tidak ada")


if __name__ == "__main__":
    main()
