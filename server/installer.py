"""Send signed install/remove requests from the catalog to Noah's OpenBot webhook routine.

The webhook URL lives in data/webhook.json. The signing secret is read from a file outside the
project (saved through OpenBot's secure key form); it is never sent to the browser or logged.
"""
import calendar
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "collector"))
import db  # noqa: E402

CONFIG = os.path.join(ROOT, "data", "webhook.json")
SECRET_FILE = os.environ.get(
    "KATALOG_WEBHOOK_SECRET_FILE",
    "/home/bagus/.config/OpenBot/agent-api-keys/agent-4b18fa4a-ba4c-4376-93c7-209e5bbc3d4a/KATALOG_WEBHOOK_SECRET",
)
EVENT_TYPE = "katalog.install"


def _config():
    try:
        return json.load(open(CONFIG))
    except (OSError, ValueError):
        return {}


def _secret():
    try:
        return open(SECRET_FILE).read().strip()
    except OSError:
        return ""


def enabled() -> bool:
    return bool(_config().get("url")) and bool(_secret())


def request(body: dict):
    item_id = str(body.get("id", "")).strip().lower()
    action = body.get("action", "install")
    if action not in ("install", "remove"):
        return 400, {"error": "aksi tidak dikenal"}
    con = db.connect()
    row = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return 404, {"error": "item tidak ada di katalog"}
    state = con.execute("SELECT status, updated_at FROM installs WHERE id=?", (item_id,)).fetchone()
    if state and state["status"] in ("requested", "review"):
        age = time.time() - calendar.timegm(time.strptime(state["updated_at"][:19], "%Y-%m-%dT%H:%M:%S"))
        if age < 3600:
            return 409, {"error": "permintaan untuk item ini sudah dikirim, cek chat Noah"}
    if state and state["status"] == "installed" and action == "install":
        return 409, {"error": "item ini sudah terpasang"}
    if action == "remove" and not (state and state["status"] == "installed"):
        return 409, {"error": "item ini belum tercatat terpasang"}

    data = {
        "action": action,
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "url": row["url"],
        "description": (row["description"] or "")[:500],
        "install": json.loads(row["install"] or "[]"),
        "env_vars": json.loads(row["env_vars"] or "[]"),
        "skill_paths": json.loads(row["skill_paths"] or "[]")[:40],
        "risks": [r["label"] for r in json.loads(row["risks"] or "[]")],
    }
    payload = json.dumps({"type": EVENT_TYPE, "occurredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "data": data},
                         separators=(",", ":")).encode()
    ts = str(int(time.time()))
    delivery = f"katalog-{uuid.uuid4().hex}"
    sig = hmac.new(_secret().encode(), f"{ts}.{delivery}.".encode() + payload, hashlib.sha256).hexdigest()
    try:
        r = requests.post(_config()["url"], data=payload, timeout=20, headers={
            "Content-Type": "application/json",
            "x-openbot-timestamp": ts,
            "x-openbot-delivery-id": delivery,
            "x-openbot-signature": f"sha256={sig}",
        })
    except requests.RequestException as e:
        return 502, {"error": f"webhook tidak bisa dihubungi: {e.__class__.__name__}"}
    if r.status_code not in (200, 202):
        msg = {401: "tanda tangan ditolak (cek secret)", 404: "routine webhook sudah dihapus",
               503: "OpenBot sedang offline/sibuk, coba lagi"}.get(r.status_code, f"HTTP {r.status_code}")
        return 502, {"error": msg}
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    con.execute(
        "INSERT INTO installs(id, status, detail, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at",
        (item_id, "requested", f"{action} dikirim ke Noah", now),
    )
    con.commit()
    return 202, {"ok": True, "status": "requested"}
