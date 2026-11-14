"""Install plans and human approval.

An agent may *create* a plan, but only a human can *approve* it (from the local web UI or the CLI
`agentloadout approve <id>`). apply() refuses any plan that is not approved, was approved more than
an hour ago, or changed after approval (content hash).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import subprocess
from pathlib import Path

from . import db, harness, paths

APPROVAL_TTL = dt.timedelta(hours=1)
MCP_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _file(plan_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]{6,16}", plan_id):
        raise ValueError("id rencana tidak valid")
    return paths.plans_dir() / f"{plan_id}.json"


def _hash(plan: dict) -> str:
    core = {k: plan[k] for k in ("item_id", "kind", "targets", "mcp_name", "spec", "skill", "changes")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


def load(plan_id: str) -> dict:
    f = _file(plan_id)
    if not f.exists():
        raise ValueError(f"rencana {plan_id} tidak ada")
    return json.loads(f.read_text())


def save(plan: dict):
    f = _file(plan["id"])
    f.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    f.chmod(0o600)


def list_plans(status: str | None = None) -> list[dict]:
    out = []
    for f in sorted(paths.plans_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        p = json.loads(f.read_text())
        if not status or p["status"] == status:
            out.append(p)
    return out


# --------------------------------------------------------------------------- spec from catalog
def mcp_spec_from_item(item: dict) -> dict | None:
    for s in json.loads(item.get("install") or "[]"):
        if s.get("client", "").startswith("JSON"):
            try:
                block = next(iter(json.loads(s["snippet"])["mcpServers"].values()))
            except (ValueError, KeyError, StopIteration):
                continue
            if "url" in block:
                return {"url": block["url"], **({"headers": block["headers"]} if block.get("headers") else {})}
            return {"command": block["command"], "args": list(block.get("args", [])), **({"env": block["env"]} if block.get("env") else {})}
    return None


def pin_versions(spec: dict) -> tuple[dict, str | None]:
    """Pin `npx -y pkg` / `uvx pkg` to the current registry version so installs are reproducible."""
    if "command" not in spec:
        return spec, None
    args = list(spec.get("args", []))
    cmd = Path(spec["command"]).name
    import requests
    for i, a in enumerate(args):
        if a.startswith("-"):
            continue
        try:
            if cmd == "npx" and not re.search(r"@\d|@latest$", a.split("/")[-1]):
                name = a
                r = requests.get(f"https://registry.npmjs.org/{name.replace('/', '%2F')}/latest", timeout=15)
                if r.ok and r.json().get("version"):
                    args[i] = f"{name}@{r.json()['version']}"
                    return {**spec, "args": args}, r.json()["version"]
            if cmd == "uvx" and "==" not in a:
                r = requests.get(f"https://pypi.org/pypi/{a}/json", timeout=15)
                if r.ok:
                    v = r.json()["info"]["version"]
                    args[i] = f"{a}=={v}"
                    return {**spec, "args": args}, v
        except requests.RequestException:
            return spec, None
        break
    m = re.search(r"@(\d[^\s/]*)$", next((a for a in args if not a.startswith("-")), ""))
    return spec, (m.group(1) if m else None)


# --------------------------------------------------------------------------- create
def create(item_id: str, targets: list[str], action: str = "install", skill_path: str | None = None,
           mcp_name: str | None = None, env: dict | None = None, created_by: str = "agent") -> dict:
    con = db.connect()
    row = con.execute("SELECT * FROM items WHERE id=?", (item_id.lower(),)).fetchone()
    if not row:
        raise ValueError(f"{item_id} tidak ada di katalog")
    item = dict(row)
    kind = item["kind"]
    if action not in ("install", "remove"):
        raise ValueError("aksi harus install atau remove")
    if not targets:
        raise ValueError("pilih minimal satu harness tujuan")
    for t in targets:
        harness.get(t)

    plan = {"id": secrets.token_hex(4), "created_at": _now().isoformat(timespec="seconds"), "created_by": created_by,
            "status": "pending", "item_id": item["id"], "item_name": item["name"], "repo_url": item["url"],
            "kind": kind, "action": action, "targets": targets, "mcp_name": None, "spec": None, "skill": None,
            "version": None, "changes": [], "risks": [r["label"] for r in json.loads(item.get("risks") or "[]")],
            "warnings": []}

    if kind == "mcp":
        name = (mcp_name or re.sub(r"[^a-z0-9_-]+", "-", item["name"].lower()).strip("-"))[:40]
        if not MCP_NAME.match(name):
            raise ValueError(f"nama server MCP tidak valid: {name}")
        plan["mcp_name"] = name
        if action == "install":
            spec = mcp_spec_from_item(item)
            if not spec:
                raise ValueError("katalog belum punya config otomatis untuk item ini — lihat README repo")
            spec, version = pin_versions(spec)
            if env:
                spec["env"] = {**spec.get("env", {}), **{k: str(v) for k, v in env.items()}}
            missing = [k for k, v in (spec.get("env") or {}).items() if v in ("", "...")]
            if missing:
                # never write placeholders into a harness config: they make the server start with a bogus key
                spec["env"] = {k: v for k, v in spec["env"].items() if k not in missing}
                if not spec["env"]:
                    spec.pop("env")
                plan["warnings"].append(f"variabel belum diisi: {', '.join(missing)} — tambahkan sendiri di config harness "
                                        "(atau sebagai environment variable) setelah dipasang; server mungkin butuh ini untuk jalan")
            plan["spec"], plan["version"] = spec, version
        for t in targets:
            a = harness.get(t)
            ch = a.plan_add(name, plan["spec"]) if action == "install" else a.plan_remove(name)
            entry = {"harness": t, "action": ch.action, "target": ch.target, "detail": ch.detail, "command": ch.command}
            if t == "openbot":
                entry["agent_instructions"] = harness.ADAPTERS["openbot"].agent_instructions(ch, name, plan["spec"] or {})
            plan["changes"].append(entry)

    elif kind == "skill":
        paths_ = json.loads(item.get("skill_paths") or "[]")
        sp = skill_path or (paths_[0] if len(paths_) == 1 else None)
        if not sp or sp not in paths_:
            raise ValueError(f"pilih skill_path dari: {', '.join(paths_[:30])}")
        slug = re.sub(r"[^a-z0-9]+", "-", (Path(sp).name if sp != "." else item["name"]).lower()).strip("-")
        plan["skill"] = {"path": sp, "slug": slug}
        for loc in harness.skill_locations_for(targets):
            d = harness.SKILL_LOCATIONS[loc]
            plan["changes"].append({"harness": loc, "action": "add" if action == "install" else "remove",
                                    "target": str(d["dir"] / slug),
                                    "detail": f"{'salin' if action == 'install' else 'pindahkan ke cadangan'} folder skill (dibaca: {', '.join(d['read_by'])})"})
        if "openbot" in targets:
            plan["changes"].append({"harness": "openbot", "action": "add" if action == "install" else "remove", "target": "agent",
                                    "detail": "agen OpenBot: openbot.create_skill dari folder skill, lalu install_local_skill ke semua agen",
                                    "agent_instructions": {"steps": ["salin folder skill ke workspace agen", "openbot.create_skill {sourcePath}",
                                                                     "openbot.list_agents → openbot.install_local_skill per agen"]}})
    else:
        raise ValueError("aplikasi belum bisa dipasang otomatis; gunakan perintah di katalog (mode salin)")

    plan["hash"] = _hash(plan)
    save(plan)
    return plan


# --------------------------------------------------------------------------- approve / reject
def approve(plan_id: str, by: str) -> dict:
    p = load(plan_id)
    if p["status"] != "pending":
        raise ValueError(f"rencana berstatus {p['status']}, tidak bisa disetujui")
    if _hash(p) != p["hash"]:
        raise ValueError("isi rencana berubah setelah dibuat; buat rencana baru")
    p.update(status="approved", approved_at=_now().isoformat(timespec="seconds"), approved_by=by)
    save(p)
    return p


def reject(plan_id: str, by: str) -> dict:
    p = load(plan_id)
    p.update(status="rejected", rejected_at=_now().isoformat(timespec="seconds"), rejected_by=by)
    save(p)
    return p


# --------------------------------------------------------------------------- apply
def apply(plan_id: str) -> dict:
    p = load(plan_id)
    if p["status"] != "approved":
        raise PermissionError("rencana belum disetujui manusia. Minta pengguna menyetujui lewat web "
                              f"Agent Loadout atau perintah: agentloadout approve {plan_id}")
    if _now() - dt.datetime.fromisoformat(p["approved_at"]) > APPROVAL_TTL:
        raise PermissionError("persetujuan sudah lebih dari 1 jam; buat dan setujui rencana baru")
    if _hash(p) != p["hash"]:
        raise PermissionError("isi rencana berubah setelah disetujui; dibatalkan")

    results, ok = [], True
    if p["kind"] == "skill":
        repo = Path(paths.work_dir() / p["item_id"].replace("/", "__"))
        if not repo.exists():
            from .inspect_repo import _clone
            _clone(p["item_id"], repo)
        src = repo / p["skill"]["path"] if p["skill"]["path"] != "." else repo
    for ch in p["changes"]:
        try:
            if ch["harness"] == "openbot":
                results.append({**ch, "result": "perlu agen OpenBot", "ok": None})
                continue
            if p["kind"] == "mcp":
                a = harness.get(ch["harness"])
                change = harness.Change(ch["harness"], ch["action"], ch["target"], ch["detail"], ch.get("command") or [])
                bk = a.apply(change, p["mcp_name"], p["spec"])
                results.append({**ch, "ok": True, "backup": bk})
            else:
                if ch["action"] == "add":
                    r = harness.install_skill_folder(src, p["skill"]["slug"], ch["harness"])
                else:
                    r = harness.remove_skill_folder(p["skill"]["slug"], ch["harness"])
                results.append({**ch, "ok": True, **r})
        except (harness.HarnessError, OSError, subprocess.SubprocessError) as e:
            ok = False
            results.append({**ch, "ok": False, "error": str(e)})
    p.update(status="applied" if ok else "failed", applied_at=_now().isoformat(timespec="seconds"), results=results)
    save(p)
    _record(p, ok)
    return p


def _record(p: dict, ok: bool):
    """Remember per-harness installs in the catalog DB (table targets)."""
    con = db.connect()
    con.execute("""CREATE TABLE IF NOT EXISTS targets (
        item_id TEXT, harness TEXT, kind TEXT, name TEXT, version TEXT, enabled INTEGER,
        installed_at TEXT, plan_id TEXT, PRIMARY KEY (item_id, harness))""")
    for r in p.get("results", []):
        if not r.get("ok"):
            continue
        if r["action"] == "add":
            con.execute("INSERT OR REPLACE INTO targets VALUES (?,?,?,?,?,?,?,?)",
                        (p["item_id"], r["harness"], p["kind"], p["mcp_name"] or (p["skill"] or {}).get("slug"),
                         p.get("version"), 1, p["applied_at"], p["id"]))
        else:
            con.execute("DELETE FROM targets WHERE item_id=? AND harness=?", (p["item_id"], r["harness"]))
    con.commit()


def installed_targets(item_id: str | None = None) -> list[dict]:
    con = db.connect()
    con.execute("""CREATE TABLE IF NOT EXISTS targets (
        item_id TEXT, harness TEXT, kind TEXT, name TEXT, version TEXT, enabled INTEGER,
        installed_at TEXT, plan_id TEXT, PRIMARY KEY (item_id, harness))""")
    q = "SELECT * FROM targets" + (" WHERE item_id=?" if item_id else "")
    return [dict(r) for r in con.execute(q, (item_id,) if item_id else ())]
