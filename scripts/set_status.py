"""Record install status of a catalog item so the web shows it.

Usage: python3 set_status.py <owner/repo> <requested|review|installed|failed|removed> "<detail>"
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
import db  # noqa: E402

VALID = {"requested", "review", "installed", "failed", "removed"}


def main():
    if len(sys.argv) < 3 or sys.argv[2] not in VALID:
        print(__doc__)
        sys.exit(2)
    item_id, status = sys.argv[1].strip().lower(), sys.argv[2]
    detail = sys.argv[3] if len(sys.argv) > 3 else ""
    con = db.connect()
    if not con.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
        print(f"peringatan: {item_id} tidak ada di katalog, status tetap dicatat")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO installs(id, status, detail, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at",
        (item_id, status, detail[:500], now),
    )
    con.commit()
    print(f"{item_id}: {status}")


if __name__ == "__main__":
    main()
