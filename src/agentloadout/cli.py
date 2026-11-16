"""agentloadout command line.

  agentloadout setup [--to claude-code,codex,...] [--yes] [--token-from-env]
                                                   register the Agent Loadout MCP server in your harnesses
  agentloadout uninstall [--yes]                   remove it again (your config backups stay)
  agentloadout set-pin [--reset]                   set, change or reset the approval PIN (human, own terminal)
  agentloadout set-password [--reset | --off]      web UI password, needed to open it from a phone (human)
  agentloadout set-token [--from-env|--clear|--test]  GitHub token (raises the API limit; never in chat)
  agentloadout upgrade [--check] [--to vX.Y.Z] [--yes] [--pre]   upgrade Agent Loadout itself (uv tool installs)
  agentloadout ui [--on-demand] [--port N]         local web catalog (http://127.0.0.1:8787)
  agentloadout plans | approve <id> | reject <id>  review install plans made by agents
  agentloadout refresh | backup | restore [file] | check-updates | where | harnesses
  agentloadout mcp                                 MCP server over stdio (harnesses run this)

Install from GitHub (there is no PyPI release; the PyPI package named `agentloadout` is not ours):
  uv tool install "git+https://github.com/BagusFary/agentloadout@v0.4.0"
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


def _store_token(token: str, by: str, test: bool = True) -> int:
    """Test (when possible) and save a token. A token GitHub rejects (401) is not saved. Never prints it."""
    from . import github_check, token_store
    if not token_store.valid_format((token or "").strip()):
        print("  ✗ format token tidak dikenali (20–255 karakter huruf/angka/_); tidak disimpan.", file=sys.stderr)
        return 2
    token = token.strip()
    res = github_check.test(token) if test else {"ok": None, "error": "tidak diuji"}
    if res.get("ok") is False:
        print(f"  ✗ {res['error']}. Token tidak disimpan.", file=sys.stderr)
        return 2
    token_store.save(token, by)
    if res.get("ok"):
        token_store.save_check({**res, "kind": token_store.kind(token), "last4": token[-4:]})
    kind = token_store.kind(token)
    print(f"  ✓ token GitHub ({kind}, …{token[-4:]}) disimpan di {token_store.token_file()} (0600)")
    if res.get("ok"):
        core = res.get("core") or {}
        print(f"    diuji: batas API {core.get('limit')}/jam, sisa {core.get('remaining')}"
              + (f", kedaluwarsa {res.get('expires') or res.get('expires_raw')}" if res.get("expires_raw") else ""))
        if res.get("scopes"):
            print(f"    ! token classic ini punya scope {', '.join(res['scopes'])}; Agent Loadout tidak butuh scope apa pun.")
    else:
        print(f"    ! belum diuji: {res.get('error')}. Disimpan; uji lagi nanti: agentloadout set-token --test")
    _warn_shadowed()
    return 0


def _warn_shadowed():
    from . import config
    info = config.github_token_info()
    if info["active"] in ("env", "env_file"):
        print(f"    ! token ini tidak dipakai selama {dict((s['source'], s['label']) for s in info['sources'])[info['active']]} "
              "terisi di proses yang menjalankan Agent Loadout.")


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
    found = harness.detected()
    print("Harness terdeteksi:", ", ".join(h.name for h in found) or "(tidak ada)")
    in_openbot = bool(os.environ.get("OPENBOT_AGENT_PROCESS"))
    try:
        if a.to:
            chosen = [harness.get(x.strip()) for x in a.to.split(",")]
        elif in_openbot:
            # run by an OpenBot agent: register in OpenBot only, never in other harnesses on this computer
            chosen = [harness.get("openbot")]
            print("Dijalankan oleh agen OpenBot: hanya OpenBot (pakai --to untuk harness lain).")
        else:
            chosen = found
    except harness.HarnessError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not chosen:
        print("Tidak ada harness yang didukung. Pasang Claude Code, Codex, Cursor, Gemini CLI, OpenCode, atau OpenBot dulu.")
        return 1
    print(f"\nMCP '{MCP_NAME}' akan didaftarkan ke: {', '.join(h.name for h in chosen)}")
    print(f"  perintah: {spec['command']} {' '.join(spec['args'])}")
    if not _ask("Lanjutkan?", a.yes):
        print("Dibatalkan.")
        return 1
    for h in chosen:
        if h.id == "openbot":
            _openbot_setup(h, spec)
            continue
        try:
            ch = h.plan_add(MCP_NAME, spec)
            bk = h.apply(ch, MCP_NAME, spec)
            print(f"  ✓ {h.name}" + (f"  (cadangan: {bk})" if bk else ""))
        except harness.HarnessError as e:
            print(f"  ✗ {h.name}: {e}")
    _install_guide_skill(chosen, a.yes)
    config.write_default_config()
    if a.token_from_env:
        if os.environ.get("GITHUB_TOKEN", "").strip():
            _store_token(os.environ["GITHUB_TOKEN"], "cli-env")
        else:
            print("  ! --token-from-env: GITHUB_TOKEN kosong, token tidak disimpan.", file=sys.stderr)
    elif not config.github_token() and not a.yes and sys.stdin.isatty():
        print("\nToken GitHub (opsional, read-only) menaikkan batas API dari 60 ke 5.000 request/jam.")
        tok = getpass.getpass("Tempel token, atau Enter untuk lewati: ").strip()
        if tok:
            _store_token(tok, "cli")
    _offer_password(a)
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


def _guide_src():
    from pathlib import Path
    return Path(__file__).parent / "skill" / "agentloadout"


def _openbot_setup(h, spec):
    """OpenBot keeps MCP servers in its own app: an OpenBot agent registers them with its tools."""
    from . import harness
    step = h.agent_instructions(harness.Change("openbot", "add", "agent", ""), MCP_NAME, spec)
    step.pop("note", None)
    print("  ℹ OpenBot: MCP didaftarkan oleh agen OpenBot, bukan oleh perintah ini. Agen OpenBot memanggil:")
    print("    " + json.dumps(step, ensure_ascii=False))
    print(f"    skill panduan: openbot.create_skill dengan sourcePath {_guide_src()}, lalu openbot.install_local_skill")
    print("    lalu periksa dengan openbot.list_mcp_servers. Env/API key: minta pengguna mengisinya di pengaturan "
          "MCP OpenBot, jangan lewat chat.")


def _offer_password(a):
    from . import web_password
    if web_password.enabled():
        return
    if not a.yes and sys.stdin.isatty() and not pin_markers():
        if _ask("\nPasang password web sekarang? (wajib untuk membuka web dari HP atau lewat tunnel)", False):
            cmd_set_password(argparse.Namespace(reset=False, off=False))
        return
    print("\nWeb hanya bisa dibuka di komputer ini sampai PENGGUNA memasang password di terminalnya sendiri:\n"
          "  agentloadout set-password")


def pin_markers():
    from . import pin
    return pin.agent_markers()


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
    from . import harness
    src = _guide_src()
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
    in_openbot = bool(os.environ.get("OPENBOT_AGENT_PROCESS"))
    openbot = in_openbot or harness.get("openbot").detect()
    targets = [] if in_openbot else [h for h in harness.detected() if h.id != "openbot" and MCP_NAME in h.list_mcp()]
    guides = [] if in_openbot else [loc for loc, d in harness.SKILL_LOCATIONS.items() if _is_our_guide(d["dir"] / MCP_NAME)]
    if openbot:
        print("ℹ OpenBot: MCP dilepas oleh agen OpenBot. Agen OpenBot memanggil:\n    "
              + json.dumps({"tool": "openbot.remove_mcp_server", "arguments": {"name": MCP_NAME}})
              + "\n    dan melepas skill lokal 'agentloadout' bila dipasang, lalu periksa dengan openbot.list_mcp_servers.")
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
        gen = pin.verify(getpass.getpass("\nPIN persetujuan (Enter kosong = batal): "))
    except pin.PinError as e:
        print(f"{e} Ditolak.", file=sys.stderr)
        return 2
    p = plans.approve(a.plan_id, "pin:cli", pin_gen=gen)
    print(f"Rencana {p['id']}: {p['status']}. Agen sekarang boleh menjalankan apply_plan (berlaku 1 jam).")
    if a.apply:
        p = plans.apply(a.plan_id)
        print(json.dumps(p.get("results", []), indent=2, ensure_ascii=False))
    return 0


def cmd_set_token(a):
    """GitHub token: not a secret that guards approvals, so agents may run --from-env; never paste it in chat."""
    from . import config, github_check, token_store
    if a.clear:
        removed = token_store.clear()
        print("Token yang dikelola Agent Loadout dihapus." if removed else "Tidak ada token yang dikelola Agent Loadout.")
        info = config.github_token_info()
        if info["active"]:
            label = dict((s["source"], s["label"]) for s in info["sources"])[info["active"]]
            print(f"Masih ada token aktif dari {label}; ubah di sana kalau ingin menghapusnya.")
        return 0
    if a.test:
        tok = config.github_token()
        info = config.github_token_info()
        res = github_check.test(tok or None)
        src = info["active"] or "tanpa token"
        if res.get("ok"):
            core = res.get("core") or {}
            if tok:
                token_store.save_check({**res, "kind": info["kind"], "last4": info["last4"]})
            print(f"OK ({src}{', ' + info['kind'] + ' …' + info['last4'] if tok else ''}): batas API {core.get('limit')}/jam, "
                  f"sisa {core.get('remaining')}" + (f", kedaluwarsa {res.get('expires') or res.get('expires_raw')}" if res.get("expires_raw") else ""))
            if res.get("scopes"):
                print(f"! token classic ini punya scope {', '.join(res['scopes'])}; tidak dibutuhkan.")
            return 0
        print(f"Gagal ({src}): {res.get('error')}", file=sys.stderr)
        return 2 if res.get("ok") is False else 1
    if a.from_env:
        if not os.environ.get("GITHUB_TOKEN", "").strip():
            print("set-token --from-env: GITHUB_TOKEN kosong.", file=sys.stderr)
            return 2
        return _store_token(os.environ["GITHUB_TOKEN"], "cli-env")
    if not sys.stdin.isatty():
        print("set-token: tidak ada terminal untuk input tersembunyi. Pakai `GITHUB_TOKEN=... agentloadout set-token --from-env`,\n"
              "atau menu Pengaturan di web. Jangan menempel token di chat.", file=sys.stderr)
        return 2
    try:
        tok = getpass.getpass("Token GitHub (input tersembunyi): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nDibatalkan.", file=sys.stderr)
        return 1
    if not tok:
        print("Dibatalkan.", file=sys.stderr)
        return 1
    return _store_token(tok, "cli")


def _human_terminal(what: str) -> int:
    """Secrets are set by the human in their own terminal: refuse inside an agent or without a terminal."""
    from . import pin
    found = pin.agent_markers()
    if found:
        print(f"{what} ditolak: perintah ini berjalan di dalam agen (" + ", ".join(found) + ").\n"
              "Hanya pengguna yang boleh menjalankannya, di terminalnya sendiri di luar agen.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print(f"{what} harus dijalankan di terminal interaktif.", file=sys.stderr)
        return 2
    return 0


def cmd_set_pin(a):
    from . import pin, plans
    rc = _human_terminal("set-pin")
    if rc:
        return rc
    changed = False
    try:
        if a.reset:
            print("Reset PIN: dipakai kalau PIN lupa atau terkunci. Rencana yang sudah disetujui tapi belum dipasang\n"
                  "akan dicabut, dan waktu reset tercatat (tampil di web dan `agentloadout where`).")
            if input("Ketik RESET untuk lanjut: ").strip() != "RESET":
                print("Dibatalkan.", file=sys.stderr)
                return 1
            old = None
        else:
            old = getpass.getpass("PIN lama: ") if pin.is_set() else None
        new = getpass.getpass(f"PIN baru ({pin.MIN_DIGITS}+ digit): ")
        if not pin.valid_format(new):
            print(f"PIN harus {pin.MIN_DIGITS}–{pin.MAX_DIGITS} digit angka. Tidak diubah.", file=sys.stderr)
            return 2
        if getpass.getpass("Ulangi PIN baru: ") != new:
            print("PIN tidak sama. Tidak diubah.", file=sys.stderr)
            return 2
        if a.reset:
            pin.reset_pin(new)
        else:
            changed = pin.is_set()
            pin.set_pin(new, old)
    except pin.PinError as e:
        hint = " Lupa PIN? Jalankan: agentloadout set-pin --reset" if not a.reset else ""
        print(f"{e} Tidak diubah.{hint}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\nDibatalkan.", file=sys.stderr)
        return 1
    revoked = plans.revoke_approved("PIN direset" if a.reset else "PIN diganti") if (a.reset or changed) else []
    print(f"PIN persetujuan {'direset' if a.reset else 'disimpan'} (hash scrypt, {pin.pin_file()}). Jangan bagikan PIN ini ke agen.")
    if revoked:
        print(f"Rencana yang dicabut (perlu disetujui ulang): {', '.join(revoked)}")
    return 0


def cmd_set_password(a):
    from . import web_password as wp
    rc = _human_terminal("set-password")
    if rc:
        return rc
    src = wp.source()
    try:
        if a.off:
            if src != "hashed":
                print("Tidak ada password ber-hash." + (f" Password sekarang dari file teks {wp.legacy_file()}: hapus baris "
                                                      "password_file di config.toml untuk mematikannya." if src == "legacy_file" else ""),
                      file=sys.stderr)
                return 2
            wp.turn_off(getpass.getpass("Password lama: "))
            print("Password web dimatikan. Akses dari luar komputer ini sekarang diblokir sampai password dipasang lagi.")
            return 0
        if a.reset:
            print("Reset password web: dipakai kalau password lupa. Semua perangkat akan keluar.")
            old = None
        else:
            old = getpass.getpass("Password lama: ") if src != "off" else None
        new = getpass.getpass(f"Password baru ({wp.MIN_LEN}–{wp.MAX_LEN} karakter): ")
        if not wp.valid_format(new):
            print(f"Password harus {wp.MIN_LEN}–{wp.MAX_LEN} karakter. Tidak diubah.", file=sys.stderr)
            return 2
        if getpass.getpass("Ulangi password baru: ") != new:
            print("Password tidak sama. Tidak diubah.", file=sys.stderr)
            return 2
        if a.reset:
            wp.reset_password(new)
        else:
            wp.set_password(new, old)
    except wp.PasswordError as e:
        hint = " Lupa password? Jalankan: agentloadout set-password --reset" if not a.reset else ""
        print(f"{e} Tidak diubah.{hint}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\nDibatalkan.", file=sys.stderr)
        return 1
    print(f"Password web {'direset' if a.reset else 'disimpan'} sebagai hash scrypt ({wp.record_file()}). "
          "Semua sesi lama keluar; login ulang di browser.")
    if wp.legacy_file():
        print(f"Catatan: password_file di config ({wp.legacy_file()}) sekarang diabaikan. Hapus file teks polos itu "
              "dan baris password_file di config.toml.")
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
    p = sub.add_parser("set-pin", help="set or change the approval PIN (in your own terminal, not through an agent)")
    p.add_argument("--reset", action="store_true", help="forgot the PIN: set a new one without the old (type RESET)")
    p = sub.add_parser("set-password", help="set the web UI password (needed to open the web UI from a phone)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--reset", action="store_true", help="forgot the password: set a new one, log out every device")
    g.add_argument("--off", action="store_true", help="remove the password (only local access works afterwards)")
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
    p = sub.add_parser("upgrade", help="upgrade Agent Loadout itself to the newest release (uv tool installs)")
    p.add_argument("--check", action="store_true", help="only check for a newer release (no changes)")
    p.add_argument("--to", metavar="vX.Y.Z", help="a specific release tag (terminal only; downgrades need a human)")
    p.add_argument("--yes", "-y", action="store_true", help="no confirmation (agents: only to the newest release)")
    p.add_argument("--pre", action="store_true", help="include pre-releases")
    p.add_argument("--force-insecure", action="store_true", help=argparse.SUPPRESS)
    p = sub.add_parser("update", help="did you mean `upgrade`? (catalog items: check-updates)")
    p.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    p = sub.add_parser("set-token", help="save, test or remove the GitHub token (raises the API limit)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--from-env", action="store_true", help="save $GITHUB_TOKEN (for agents; never paste a token in chat)")
    g.add_argument("--clear", action="store_true", help="remove the token Agent Loadout manages")
    g.add_argument("--test", action="store_true", help="test the active token against GitHub /rate_limit")
    a = ap.parse_args(argv)

    if a.cmd == "setup":
        sys.exit(cmd_setup(a))
    if a.cmd == "uninstall":
        sys.exit(cmd_uninstall(a))
    if a.cmd == "set-pin":
        sys.exit(cmd_set_pin(a))
    if a.cmd == "upgrade":
        from . import selfupdate
        sys.exit(selfupdate.upgrade(to=a.to, pre=a.pre, yes=a.yes, force_insecure=a.force_insecure, check_only=a.check))
    if a.cmd == "update":
        print("Maksud Anda `agentloadout upgrade`? (memperbarui Agent Loadout sendiri)\n"
              "Untuk versi baru MCP/skill yang terpasang: `agentloadout check-updates`.", file=sys.stderr)
        sys.exit(2)
    if a.cmd == "set-token":
        sys.exit(cmd_set_token(a))
    if a.cmd == "set-password":
        sys.exit(cmd_set_password(a))
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
        from . import config, paths, pin, web_password
        from .web import server as web
        print(f"data    : {paths.home()}\ncatalog : {paths.db_path()}\nbackups : {paths.backup_dir()}\nconfig  : {paths.config_file()}")
        ti = config.github_token_info()
        if ti["active"]:
            label = dict((x["source"], x["label"]) for x in ti["sources"])[ti["active"]]
            shadow = [x["label"] for x in ti["sources"] if x["shadowed"]]
            print(f"github token: set ({ti['kind']}, …{ti['last4'] or '?'}) dari {label}"
                  + (f"; tertimpa: {', '.join(shadow)}" if shadow else ""))
        else:
            print("github token: not set (optional, raises API limit: `agentloadout set-token` or web Settings)")
        st = pin.status()
        if not st["set"]:
            print("approval PIN: not set (run `agentloadout set-pin` yourself)")
        elif st.get("invalid"):
            print("approval PIN: record invalid, approvals locked (run `agentloadout set-pin --reset`)")
        else:
            print(f"approval PIN: set {st.get('set_at')} by {st.get('set_by') or '?'}" + (" (locked)" if st.get("locked") else ""))
            if st.get("reset_at"):
                print(f"              PIN direset pada {st['reset_at']}")
        pw = web_password.status()
        print("web password: " + {"hashed": f"set (hash) {pw.get('set_at')}" + (f", direset {pw['reset_at']}" if pw.get("reset_at") else ""),
                                  "legacy_file": "set (plain-text password_file; run `agentloadout set-password` to hash it)",
                                  "off": "not set (web only on this computer; `agentloadout set-password`)"}[pw["source"]])
        ui = web.read_ui()
        print(f"web UI  : http://{ui['host']}:{ui['port']}/ (pid {ui['pid']})" if ui else "web UI  : not running (`agentloadout ui`)")
        if ui and ui.get("version") and ui["version"] != __version__:
            print(f"          ! web UI ini masih versi {ui['version']} (terpasang {__version__}): mulai ulang web UI")
        from . import selfupdate
        c = selfupdate.read_cache()
        kind = selfupdate.install_kind()["kind"]
        line = f"version : {__version__} ({kind})"
        if c and c.get("latest"):
            line += f"; terbaru {c['latest']}, dicek {c.get('checked_at')}" + (" -> `agentloadout upgrade`" if c.get("newer") and kind == "uv-tool-git" else "")
        elif c and c.get("error"):
            line += f"; cek versi terakhir gagal ({c['error'][:60]})"
        print(line)


if __name__ == "__main__":
    main()
