"""agentloadout command line.

  agentloadout setup [--to claude-code,codex,...] [--yes]   register the Agent Loadout MCP server in your harnesses
  agentloadout uninstall [--yes]                             remove it again (your config backups stay)
  agentloadout ui [--on-demand]                              local web catalog (http://127.0.0.1:8787)
  agentloadout plans | approve <id> | reject <id>            review install plans made by agents
  agentloadout refresh | backup | restore [file] | check-updates | where | harnesses
  agentloadout mcp                                           MCP server over stdio (harnesses run this)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys

MCP_NAME = "agentloadout"


def mcp_spec() -> dict:
    """How harnesses should start our MCP server.
    - run through `uvx agentloadout ...` (ephemeral uv cache): register `uvx agentloadout mcp`, always latest;
    - installed as a tool / in a venv: register the absolute path of that installed script;
    - AGENTLOADOUT_MCP_COMMAND overrides everything (e.g. for development)."""
    if os.environ.get("AGENTLOADOUT_MCP_COMMAND"):
        parts = os.environ["AGENTLOADOUT_MCP_COMMAND"].split()
        return {"command": parts[0], "args": parts[1:]}
    exe = sys.executable.replace("\\", "/")
    ephemeral = "/uv/archive" in exe or "/.cache/uv/" in exe or "/uv/cache/" in exe
    if ephemeral and shutil.which("uvx"):
        return {"command": shutil.which("uvx"), "args": ["agentloadout", "mcp"]}
    script = os.path.join(os.path.dirname(sys.executable), "agentloadout" + (".exe" if os.name == "nt" else ""))
    if os.path.exists(script):
        return {"command": script, "args": ["mcp"]}
    return {"command": sys.executable, "args": ["-m", "agentloadout.cli", "mcp"]}


def _ask(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "ya", "yes")
    except EOFError:
        return False


def cmd_setup(a):
    from . import config, harness, paths
    found = [h for h in harness.detected() if h.id != "openbot"]
    print("Harness terdeteksi:", ", ".join(h.name for h in found) or "(tidak ada)")
    chosen = [harness.get(x) for x in a.to.split(",")] if a.to else found
    if not chosen:
        print("Tidak ada harness yang didukung. Pasang Claude Code, Codex, Cursor, Gemini CLI, atau OpenCode dulu.")
        return 1
    spec = mcp_spec()
    print(f"\nMCP '{MCP_NAME}' akan didaftarkan ke: {', '.join(h.name for h in chosen)}")
    print(f"  perintah: {spec['command']} {' '.join(spec['args'])}")
    if not _ask("Lanjutkan?", a.yes):
        print("Dibatalkan.")
        return 1
    for h in chosen:
        try:
            ch = h.plan_add(MCP_NAME, spec)
            bk = h.apply(ch, MCP_NAME, spec)
            print(f"  ✓ {h.name}" + (f"  (cadangan: {bk})" if bk else ""))
        except harness.HarnessError as e:
            print(f"  ✗ {h.name}: {e}")
    _install_guide_skill(chosen, a.yes)
    config.write_default_config()
    if not config.github_token() and not a.yes and sys.stdin.isatty():
        print("\nToken GitHub (opsional, tanpa scope) menaikkan batas API dari 60 ke 5.000 request/jam.")
        tok = getpass.getpass("Tempel token, atau Enter untuk lewati: ").strip()
        if tok:
            f = paths.home() / "github_token"
            f.write_text(tok)
            if os.name != "nt":
                f.chmod(0o600)
            print(f"  disimpan di {f}")
    if any(h.id == "openbot" for h in harness.detected()):
        print("\nOpenBot terdeteksi: tambahkan MCP lewat Server settings > MCP dengan perintah di atas.")
    print("\nSelesai. Buka ulang harness-mu, lalu minta agen: \"cari MCP untuk postgres di Agent Loadout\".")
    print("Katalog diambil otomatis saat pertama dipakai (±5 menit). Web lokal: agentloadout ui")
    return 0


def _install_guide_skill(chosen, yes):
    """Install the bundled `agentloadout` skill so agents know the safe workflow."""
    from pathlib import Path
    from . import harness
    src = Path(__file__).parent / "skill" / "agentloadout"
    locs = harness.skill_locations_for([h.id for h in chosen])
    if not locs or not _ask("Pasang juga skill panduan 'agentloadout' untuk agen?", yes):
        return
    for loc in locs:
        try:
            harness.install_skill_folder(src, "agentloadout", loc)
            print(f"  ✓ skill panduan -> {harness.SKILL_LOCATIONS[loc]['dir']}/agentloadout")
        except harness.HarnessError as e:
            print(f"  ✗ skill panduan: {e}")


def cmd_uninstall(a):
    from . import harness
    targets = [h for h in harness.detected() if h.id != "openbot" and MCP_NAME in h.list_mcp()]
    if not targets:
        print("MCP agentloadout tidak terdaftar di harness mana pun.")
        return 0
    print("Akan dihapus dari:", ", ".join(h.name for h in targets))
    if not _ask("Lanjutkan?", a.yes):
        return 1
    for h in targets:
        try:
            h.apply(h.plan_remove(MCP_NAME), MCP_NAME, None)
            print(f"  ✓ {h.name}")
        except harness.HarnessError as e:
            print(f"  ✗ {h.name}: {e}")
    for loc in harness.SKILL_LOCATIONS:
        if (harness.SKILL_LOCATIONS[loc]["dir"] / "agentloadout").exists():
            harness.remove_skill_folder("agentloadout", loc)
            print(f"  ✓ skill panduan dilepas dari {harness.SKILL_LOCATIONS[loc]['dir']}")
    print("Data katalog tetap ada di `agentloadout where`; hapus foldernya bila tidak dipakai lagi.")
    return 0


def _print_plan(p):
    print(f"\nRencana {p['id']}  [{p['status']}]  dibuat {p['created_at']} oleh {p['created_by']}")
    print(f"  {p['action']} {p['kind']} {p['item_id']}  ({p['repo_url']})")
    if p.get("spec"):
        s = p["spec"]
        print("  config:", s.get("url") or f"{s['command']} {' '.join(s.get('args', []))}")
    if p.get("skill"):
        print(f"  skill: {p['skill']['path']} -> {p['skill']['slug']}")
    for c in p["changes"]:
        print(f"   - {c['harness']}: {c['detail']}")
    for w in p.get("warnings", []):
        print("  ! " + w)
    if p.get("risks"):
        print("  risiko:", ", ".join(p["risks"]))


def cmd_plans(a):
    from . import plans
    items = plans.list_plans(None if a.all else "pending")
    if not items:
        print("Tidak ada rencana menunggu persetujuan." if not a.all else "Belum ada rencana.")
    for p in items[:20]:
        _print_plan(p)


def cmd_approve(a, approve=True):
    from . import plans
    p = plans.load(a.plan_id)
    _print_plan(p)
    if approve and not sys.stdin.isatty() and os.environ.get("AGENTLOADOUT_ALLOW_NONINTERACTIVE_APPROVAL") != "1":
        # agents usually run shell commands without a terminal: approval must come from a human at a terminal
        print("\nPersetujuan harus dari terminal interaktif (atau lewat web Agent Loadout). Ditolak.")
        return 2
    if approve and not _ask("\nSetujui rencana ini?", a.yes and sys.stdin.isatty() or os.environ.get("AGENTLOADOUT_ALLOW_NONINTERACTIVE_APPROVAL") == "1"):
        print("Tidak disetujui.")
        return 1
    p = plans.approve(a.plan_id, f"cli:{getpass.getuser()}") if approve else plans.reject(a.plan_id, f"cli:{getpass.getuser()}")
    print(f"Rencana {p['id']}: {p['status']}. " + ("Agen sekarang boleh menjalankan apply_plan (berlaku 1 jam)." if approve else ""))
    if approve and a.apply:
        p = plans.apply(a.plan_id)
        print(json.dumps(p.get("results", []), indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentloadout", description="Agent Loadout: MCP servers, skills and apps for any agent harness.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup", help="register the MCP server in your harnesses")
    p.add_argument("--to", help="comma-separated harness ids (default: all detected)")
    p.add_argument("--yes", "-y", action="store_true")
    p = sub.add_parser("uninstall", help="remove the MCP server from your harnesses")
    p.add_argument("--yes", "-y", action="store_true")
    p = sub.add_parser("ui", help="start the local web catalog")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--on-demand", action="store_true", help="stop after the configured idle time")
    p = sub.add_parser("plans", help="list plans waiting for approval")
    p.add_argument("--all", action="store_true")
    for name in ("approve", "reject"):
        p = sub.add_parser(name, help=f"{name} a plan")
        p.add_argument("plan_id")
        p.add_argument("--yes", "-y", action="store_true")
        if name == "approve":
            p.add_argument("--apply", action="store_true", help="apply right after approving")
    sub.add_parser("harnesses", help="show detected harnesses")
    sub.add_parser("refresh", help="back up and fetch the catalog now")
    sub.add_parser("mcp", help="run the MCP server over stdio")
    sub.add_parser("backup", help="back up the catalog database")
    p = sub.add_parser("restore", help="restore the catalog from a backup")
    p.add_argument("file", nargs="?")
    sub.add_parser("check-updates", help="check newer versions of installed items")
    sub.add_parser("where", help="show data, config and backup folders")
    a = ap.parse_args(argv)

    if a.cmd == "setup":
        sys.exit(cmd_setup(a))
    if a.cmd == "uninstall":
        sys.exit(cmd_uninstall(a))
    if a.cmd == "ui":
        from .web import server
        server.main(a.host, a.port, on_demand=a.on_demand)
    elif a.cmd == "plans":
        cmd_plans(a)
    elif a.cmd in ("approve", "reject"):
        try:
            sys.exit(cmd_approve(a, a.cmd == "approve"))
        except (ValueError, PermissionError) as e:
            sys.exit(f"Gagal: {e}")
    elif a.cmd == "harnesses":
        from . import harness
        for h in harness.ADAPTERS.values():
            i = h.info()
            print(f"{'✓' if i['detected'] else '·'} {i['name']:12} config: {i['config']}")
    elif a.cmd == "refresh":
        from . import backup, collector
        backup.main_args([])
        collector.main()
    elif a.cmd == "mcp":
        from . import mcp_server
        mcp_server.main()
    elif a.cmd == "backup":
        from . import backup
        backup.main_args([])
    elif a.cmd == "restore":
        from . import restore
        sys.argv = ["restore"] + ([a.file] if a.file else ["--list"])
        restore.main()
    elif a.cmd == "check-updates":
        from . import updates
        updates.main()
    elif a.cmd == "where":
        from . import config, paths
        print(f"data    : {paths.home()}\ncatalog : {paths.db_path()}\nbackups : {paths.backup_dir()}\nconfig  : {paths.config_file()}")
        print(f"github token: {'set' if config.github_token() else 'not set (optional, raises API limit)'}")


if __name__ == "__main__":
    main()
