"""agentloadout command line.

  agentloadout setup [--to claude-code,codex,...] [--yes] [--token-from-env]
                                                   register the Agent Loadout MCP server in your harnesses
  agentloadout uninstall [--yes]                   remove it again (your config backups stay)
  agentloadout set-pin                             set or change the approval PIN (human, own terminal)
  agentloadout ui [--on-demand]                    local web catalog (http://127.0.0.1:8787)
  agentloadout plans | approve <id> | reject <id>  review install plans made by agents
  agentloadout refresh | backup | restore [file] | check-updates | where | harnesses
  agentloadout mcp                                 MCP server over stdio (harnesses run this)

Install from GitHub (there is no PyPI release; the PyPI package named `agentloadout` is not ours):
  uv tool install "git+https://github.com/BagusFary/agentloadout@v0.2.0"
  "$(uv tool dir --bin)/agentloadout" setup --yes
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import sys

from . import __version__

MCP_NAME = "agentloadout"
REPO = "https://github.com/BagusFary/agentloadout"
INSTALL_CMD = f'uv tool install "git+{REPO}@v{__version__}"'
SETUP_CMD = '"$(uv tool dir --bin)/agentloadout" setup --yes'
GUIDE_MARKER = ".installed-by-agentloadout"  # marks the guide skill folder that setup installed
# SKILL.md of guide skills installed by versions without the marker (v0.1.0)
LEGACY_GUIDE_SHA256 = {"2f839b5caa8938b3b65d46001b66733f760192174e47704ed1b466b89307d7e7"}


class SetupError(RuntimeError):
    pass


def _ephemeral_env() -> bool:
    """True when we run from a throwaway uv environment (uvx / `uv tool run`), which uv may delete at any time."""
    if os.environ.get("UV_TOOL_DIR") and os.path.abspath(sys.prefix).startswith(os.path.abspath(os.environ["UV_TOOL_DIR"]) + os.sep):
        return False  # a real `uv tool install`
    markers = ("/archive-v", "/builds-v", "/environments-v", "/.cache/uv/", "/uv/cache/", "/uv/archive")
    cache = os.environ.get("UV_CACHE_DIR")
    for p in {sys.executable, sys.prefix}:
        p = p.replace("\\", "/")
        if any(m in p for m in markers):
            return True
        if cache and os.path.abspath(p).startswith(os.path.abspath(cache) + os.sep):
            return True
    return False


def mcp_spec() -> dict:
    """How harnesses should start our MCP server.
    - installed with `uv tool install` / in a venv: the absolute path of that installed script;
    - run through uvx (throwaway uv cache env): refused — `uvx agentloadout` would fetch the unrelated PyPI
      package of the same name, and the cache path disappears on `uv cache clean`;
    - AGENTLOADOUT_MCP_COMMAND overrides everything (development), but never a bare `uvx agentloadout`."""
    if os.environ.get("AGENTLOADOUT_MCP_COMMAND"):
        parts = shlex.split(os.environ["AGENTLOADOUT_MCP_COMMAND"], posix=os.name != "nt")
        exe = os.path.basename(parts[0]).lower().removesuffix(".exe")
        via_pypi = exe == "uvx" or (exe == "uv" and parts[1:3] == ["tool", "run"]) or (exe == "uv" and parts[1:2] == ["x"])
        if via_pypi and not any(p == "--from" or p.startswith("--from=") for p in parts):
            raise SetupError("AGENTLOADOUT_MCP_COMMAND memakai `uvx`/`uv tool run` tanpa `--from`; itu mengambil paket "
                             "PyPI lain yang bernama sama. Pakai path absolut hasil `uv tool install`.")
        return {"command": parts[0], "args": parts[1:]}
    if _ephemeral_env():
        raise SetupError("agentloadout sedang berjalan dari environment sementara uv (uvx), yang bisa hilang kapan saja.\n"
                         f"Pasang dulu sebagai tool, lalu jalankan setup dari sana:\n  {INSTALL_CMD}\n  {SETUP_CMD}")
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


def _need_yes(a, what: str) -> int:
    """Agents run commands without a terminal: tell them how to proceed instead of silently cancelling."""
    if not a.yes and not sys.stdin.isatty():
        print(f"agentloadout {what}: tidak ada terminal interaktif untuk konfirmasi. Jalankan dengan --yes:\n"
              f"  agentloadout {what} --yes", file=sys.stderr)
        return 2
    return 0


def _save_token_from_env() -> str | None:
    from . import paths
    tok = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not tok:
        print("  ! --token-from-env: GITHUB_TOKEN kosong, token tidak disimpan.", file=sys.stderr)
        return None
    f = paths.home() / "github_token"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok + "\n")
    if os.name != "nt":
        f.chmod(0o600)
    return str(f)


def cmd_setup(a):
    from . import config, harness, paths, pin
    rc = _need_yes(a, "setup")
    if rc:
        return rc
    try:
        spec = mcp_spec()
    except SetupError as e:
        print(str(e), file=sys.stderr)
        return 1
    found = [h for h in harness.detected() if h.id != "openbot"]
    print("Harness terdeteksi:", ", ".join(h.name for h in found) or "(tidak ada)")
    try:
        chosen = [harness.get(x.strip()) for x in a.to.split(",")] if a.to else found
    except harness.HarnessError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not chosen:
        print("Tidak ada harness yang didukung. Pasang Claude Code, Codex, Cursor, Gemini CLI, atau OpenCode dulu.")
        return 1
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
    if a.token_from_env:
        f = _save_token_from_env()
        if f:
            print(f"  ✓ token GitHub disimpan di {f} (0600)")
    elif not config.github_token() and not a.yes and sys.stdin.isatty():
        print("\nToken GitHub (opsional, tanpa scope) menaikkan batas API dari 60 ke 5.000 request/jam.")
        tok = getpass.getpass("Tempel token, atau Enter untuk lewati: ").strip()
        if tok:
            os.environ["GITHUB_TOKEN"] = tok
            print(f"  disimpan di {_save_token_from_env()}")
    if any(h.id == "openbot" for h in harness.detected()):
        print("\nOpenBot terdeteksi: tambahkan MCP lewat Server settings > MCP dengan perintah di atas.")
    print("\nSelesai. Buka ulang harness-mu, lalu minta agen: \"cari MCP untuk postgres di Agent Loadout\".")
    if not pin.is_set():
        print("Sebelum persetujuan pertama, PENGGUNA (bukan agen) menjalankan di terminalnya sendiri:\n"
              "  agentloadout set-pin")
    if config.github_token():
        print("Katalog diambil otomatis saat pertama dipakai (dengan token: sekitar 5–10 menit).")
    else:
        print("Katalog diambil otomatis saat pertama dipakai. Tanpa token GitHub, data awal muncul dalam beberapa menit,\n"
              "lalu item yang belum diperiksa dilengkapi bertahap (batas 60 request/jam). Lihat README.")
    print(f"Data: {paths.home()}   Web lokal: agentloadout ui")
    return 0


def _is_our_guide(folder) -> bool:
    import hashlib
    if (folder / GUIDE_MARKER).exists():
        return True
    try:
        return hashlib.sha256((folder / "SKILL.md").read_bytes()).hexdigest() in LEGACY_GUIDE_SHA256
    except OSError:
        return False


def _install_guide_skill(chosen, yes):
    """Install the bundled `agentloadout` skill so agents know the safe workflow.
    A folder of the same name that setup did not install (no marker) belongs to the user and is left alone."""
    from pathlib import Path
    from . import harness
    src = Path(__file__).parent / "skill" / "agentloadout"
    locs = harness.skill_locations_for([h.id for h in chosen])
    if not locs or not _ask("Pasang juga skill panduan 'agentloadout' untuk agen?", yes):
        return
    for loc in locs:
        dest = harness.SKILL_LOCATIONS[loc]["dir"] / "agentloadout"
        if dest.exists() and not _is_our_guide(dest):
            print(f"  · skill panduan dilewati: {dest} sudah ada dan bukan dipasang oleh agentloadout")
            continue
        try:
            harness.install_skill_folder(src, "agentloadout", loc)
            (dest / GUIDE_MARKER).write_text(f"agentloadout {__version__} guide skill; removed by `agentloadout uninstall`\n")
            print(f"  ✓ skill panduan -> {dest}")
        except harness.HarnessError as e:
            print(f"  ✗ skill panduan: {e}")


def cmd_uninstall(a):
    from . import harness, paths
    rc = _need_yes(a, "uninstall")
    if rc:
        return rc
    targets = [h for h in harness.detected() if h.id != "openbot" and MCP_NAME in h.list_mcp()]
    guides = [loc for loc, d in harness.SKILL_LOCATIONS.items() if _is_our_guide(d["dir"] / MCP_NAME)]
    if not targets and not guides:
        print("MCP agentloadout tidak terdaftar di harness mana pun, dan skill panduan tidak terpasang.")
    else:
        print("Akan dihapus dari:", ", ".join([h.name for h in targets] +
                                              [f"skill panduan di {harness.SKILL_LOCATIONS[l]['dir']}" for l in guides]))
        if not _ask("Lanjutkan?", a.yes):
            print("Dibatalkan.")
            return 1
        for h in targets:
            try:
                bk = h.apply(h.plan_remove(MCP_NAME), MCP_NAME, None)
                print(f"  ✓ {h.name}" + (f"  (cadangan config: {bk})" if bk else ""))
            except harness.HarnessError as e:
                print(f"  ✗ {h.name}: {e}")
        for loc in guides:
            r = harness.remove_skill_folder(MCP_NAME, loc)
            print(f"  ✓ skill panduan dilepas dari {harness.SKILL_LOCATIONS[loc]['dir']}  (cadangan: {r.get('backup')})")
    print(f"\nCadangan config & skill: {paths.config_backups_dir()}")
    print(f"Data katalog, rencana dan PIN tetap di: {paths.home()} — hapus folder itu bila tidak dipakai lagi.")
    print("Langkah terakhir untuk melepas programnya:\n  uv tool uninstall agentloadout")
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
    from . import pin, plans
    p = plans.load(a.plan_id)
    _print_plan(p)
    if not approve:
        p = plans.reject(a.plan_id, f"cli:{getpass.getuser()}")
        print(f"Rencana {p['id']}: {p['status']}.")
        return 0
    if not sys.stdin.isatty():
        # agents usually run shell commands without a terminal: approval must come from a human
        print("\nPersetujuan harus dari terminal interaktif dengan PIN (atau lewat web Agent Loadout). Ditolak.", file=sys.stderr)
        return 2
    if not pin.is_set():
        print("\nPIN persetujuan belum diset. Ditolak.\nMinta pengguna menjalankan `agentloadout set-pin` "
              "di terminalnya sendiri, lalu setujui lagi.", file=sys.stderr)
        return 2
    try:
        pin.verify(getpass.getpass("\nPIN persetujuan (Enter kosong = batal): "))
    except pin.PinError as e:
        print(f"{e} Ditolak.", file=sys.stderr)
        return 2
    p = plans.approve(a.plan_id, "pin:cli")
    print(f"Rencana {p['id']}: {p['status']}. Agen sekarang boleh menjalankan apply_plan (berlaku 1 jam).")
    if a.apply:
        p = plans.apply(a.plan_id)
        print(json.dumps(p.get("results", []), indent=2, ensure_ascii=False))
    return 0


def cmd_set_pin(a):
    from . import pin
    found = pin.agent_markers()
    if found:
        print("set-pin ditolak: perintah ini berjalan di dalam agen (" + ", ".join(found) + ").\n"
              "PIN hanya boleh dibuat oleh pengguna di terminalnya sendiri, di luar agen.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("set-pin harus dijalankan di terminal interaktif.", file=sys.stderr)
        return 2
    try:
        old = getpass.getpass("PIN lama: ") if pin.is_set() else None
        new = getpass.getpass(f"PIN baru ({pin.MIN_DIGITS}+ digit): ")
        if not pin.valid_format(new):
            print(f"PIN harus {pin.MIN_DIGITS}–{pin.MAX_DIGITS} digit angka. Tidak diubah.", file=sys.stderr)
            return 2
        if getpass.getpass("Ulangi PIN baru: ") != new:
            print("PIN tidak sama. Tidak diubah.", file=sys.stderr)
            return 2
        pin.set_pin(new, old)
    except pin.PinError as e:
        print(f"{e} Tidak diubah.", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\nDibatalkan.", file=sys.stderr)
        return 1
    print(f"PIN persetujuan disimpan (hash scrypt, {pin.pin_file()}). Jangan bagikan PIN ini ke agen.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentloadout", description="Agent Loadout: MCP servers, skills and apps for any agent harness.")
    ap.add_argument("--version", action="version", version=f"agentloadout {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup", help="register the MCP server in your harnesses")
    p.add_argument("--to", help="comma-separated harness ids (default: all detected)")
    p.add_argument("--yes", "-y", action="store_true", help="no questions (needed when run by an agent)")
    p.add_argument("--token-from-env", action="store_true", help="save $GITHUB_TOKEN to the data folder (0600)")
    p = sub.add_parser("uninstall", help="remove the MCP server and guide skill from your harnesses")
    p.add_argument("--yes", "-y", action="store_true")
    sub.add_parser("set-pin", help="set or change the approval PIN (in your own terminal, not through an agent)")
    p = sub.add_parser("ui", help="start the local web catalog")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--on-demand", action="store_true", help="stop after the configured idle time")
    p = sub.add_parser("plans", help="list plans waiting for approval")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("approve", help="approve a plan (interactive terminal + approval PIN)")
    p.add_argument("plan_id")
    p.add_argument("--apply", action="store_true", help="apply right after approving")
    p = sub.add_parser("reject", help="reject a plan")
    p.add_argument("plan_id")
    p.add_argument("--yes", "-y", action="store_true", help=argparse.SUPPRESS)
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
    if a.cmd == "set-pin":
        sys.exit(cmd_set_pin(a))
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
        from . import refresh
        sys.exit(refresh.run_locked())  # same lock as the MCP background refresh
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
        from . import config, paths, pin
        print(f"data    : {paths.home()}\ncatalog : {paths.db_path()}\nbackups : {paths.backup_dir()}\nconfig  : {paths.config_file()}")
        print(f"github token: {'set' if config.github_token() else 'not set (optional, raises API limit)'}")
        print(f"approval PIN: {'set' if pin.is_set() else 'not set (run `agentloadout set-pin` yourself)'}")


if __name__ == "__main__":
    main()
