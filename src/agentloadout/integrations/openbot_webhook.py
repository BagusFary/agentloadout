"""Send signed install/remove requests from the catalog to Noah's OpenBot webhook routine.

Configure in config.toml under [integrations.openbot_webhook] (url, secret_file). The signing
secret is read from a file; it is never sent to the browser or logged.
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

from .. import config, db
EVENT_TYPE = "katalog.install"


def _config():
    return config.openbot_webhook()


def _secret():
    return config.read_secret(_config().get("secret_file", ""))


def enabled() -> bool:
    return bool(_config().get("url")) and bool(_secret())


ACTIONS = ("install", "remove", "update", "enable", "disable")


def request(body: dict):
    item_id = str(body.get("id", "")).strip().lower()
    action = body.get("action", "install")
    if action not in ACTIONS:
        return 400, {"error": "aksi tidak dikenal"}
    con = db.connect()
    row = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return 404, {"error": "item tidak ada di katalog"}
    state = con.execute("SELECT * FROM installs WHERE id=?", (item_id,)).fetchone()
    status = state["status"] if state else None
    installed = status == "installed"
    if status in ("requested", "review"):
        age = time.time() - calendar.timegm(time.strptime(state["updated_at"][:19], "%Y-%m-%dT%H:%M:%S"))
        if age < 3600:
            return 409, {"error": "permintaan untuk item ini sudah dikirim, cek chat Noah"}
    if action == "install" and installed:
        return 409, {"error": "item ini sudah terpasang"}
    if action in ("remove", "update", "enable", "disable") and not installed:
        return 409, {"error": "item ini belum tercatat terpasang"}
    if action == "update" and not (state["latest_version"] and state["latest_version"] != state["installed_version"]):
        return 409, {"error": "belum ada versi baru"}
    if action in ("enable", "disable") and not (state["ref"] or "").startswith(("mcp:", "skill:")):
        return 409, {"error": "nyala/mati hanya untuk server MCP dan skill"}
    skill_path = str(body.get("skill_path") or "").strip()
    if skill_path and skill_path not in json.loads(row["skill_paths"] or "[]"):
        return 400, {"error": "folder skill tidak dikenal"}

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
        "skill_path": skill_path or None,
        "installed": {k: state[k] for k in ("ref", "package", "installed_version", "latest_version", "enabled", "skill_path")}
                     if state else None,
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
        (item_id, "requested" if action in ("install",) else status, f"{action} dikirim ke Noah", now),
    )
    con.commit()
    return 202, {"ok": True, "status": "requested"}
