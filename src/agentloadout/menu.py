"""Interactive menu: `agentloadout` without arguments (or `agentloadout menu`) in a human's terminal.

Rules (BRIEF-05):
- Only when stdin AND stdout are terminals, no agent marker is set, TERM is not "dumb", CI is empty and
  AGENTLOADOUT_NO_MENU is empty. Otherwise the CLI behaves as before (usage + exit 2), so scripts and agents
  never wait at a prompt.
- Every choice runs an existing command through cli.run() with its normal checks (terminal, PIN, pin_gen,
  confirmations). The menu adds no shortcut: approving still needs the PIN, upgrades only go to the newest
  release, `--to`/`--pre`/downgrades and `mcp` are not in the menu.
- No global signal handlers: Ctrl-C at the main prompt (or "0", or EOF) leaves the menu; Ctrl-C inside a
  choice returns to the menu. After 15 minutes without input the menu closes itself (an agent driving a
  pseudo-terminal must not hang forever; AGENTLOADOUT_MENU_IDLE overrides the seconds, e.g. for tests).
- The status line never uses the network and never creates the catalog: the DB is opened read-only.
- Plain ASCII output (Windows code pages); color only for the banner, and only on a color-capable terminal.
- Importing this module prints nothing; the `mcp` path never imports it.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import __version__

IDLE_SECONDS = 15 * 60
EXIT = "exit"  # a handler's request to leave the menu (after a successful upgrade)

BANNER = [
    " ###   #### ##### #   # ##### #      ###   ###  ####   ###  #   # #####",
    "#   # #     #     ##  #   #   #     #   # #   # #   # #   # #   #   #  ",
    "##### #  ## ####  # # #   #   #     #   # ##### #   # #   # #   #   #  ",
    "#   # #   # #     #  ##   #   #     #   # #   # #   # #   # #   #   #  ",
    "#   #  ###  ##### #   #   #   #####  ###  #   # ####   ###   ###    #  ",
]
SMALL = "== AGENTLOADOUT =="


def _letters(start: int, count: int) -> list[str]:
    """The glyph columns of letters [start, start+count) of BANNER (each letter 5 wide, 1 space apart)."""
    a, b = start * 6, start * 6 + count * 6 - 1
    return [line[a:b].rstrip() for line in BANNER]


def banner_lines(width: int) -> list[str]:
    if width >= 72:
        return [line.rstrip() for line in BANNER]
    if width >= 42:
        return _letters(0, 5) + [""] + _letters(5, 7)  # AGENT / LOADOUT, 41 columns
    return [SMALL]


# --------------------------------------------------------------------------- when the menu may run
def menu_markers() -> tuple[str, ...]:
    from . import pin
    # AGENT is too generic to block set-pin, but enough to keep the menu away (OpenCode sets it)
    return tuple(pin.AGENT_MARKERS) + ("AGENT",)


def not_interactive_reason(env=None, stdin=None, stdout=None) -> str | None:
    """None when the menu may run, else a short reason."""
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        if not stdin.isatty():
            return "stdin bukan terminal"
        if not stdout.isatty():
            return "stdout bukan terminal"
    except (AttributeError, ValueError):
        return "tanpa terminal"
    for name in menu_markers():
        if env.get(name):
            return f"sesi agen ({name})"
    if env.get("TERM", "") == "dumb":
        return "TERM=dumb"
    if env.get("CI"):
        return "CI"
    if env.get("AGENTLOADOUT_NO_MENU"):
        return "AGENTLOADOUT_NO_MENU"
    return None


def interactive() -> bool:
    return not_interactive_reason() is None


def color_ok(env=None) -> bool:
    env = os.environ if env is None else env
    if env.get("NO_COLOR") or env.get("TERM", "") == "dumb":
        return False
    try:
        if not sys.stdout.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    return os.name != "nt" or bool(env.get("WT_SESSION"))


def width() -> int:
    return max(20, shutil.get_terminal_size((80, 24)).columns)


# --------------------------------------------------------------------------- input with an idle limit
class IdleTimeout(Exception):
    pass


def idle_seconds() -> float:
    try:
        return float(os.environ.get("AGENTLOADOUT_MENU_IDLE") or IDLE_SECONDS)
    except ValueError:
        return IDLE_SECONDS


def read_line(prompt: str) -> str:
    """input() with an idle limit (POSIX). Raises EOFError, KeyboardInterrupt or IdleTimeout."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    if os.name != "nt":
        import select
        ready, _, _ = select.select([sys.stdin], [], [], idle_seconds())
        if not ready:
            raise IdleTimeout()
        line = sys.stdin.readline()
        if line == "":
            raise EOFError()
        return line.rstrip("\n")
    return input()


def ask(prompt: str) -> str:
    return read_line(prompt).strip()


def confirm_typed(word: str, what: str) -> bool:
    return ask(f"Ketik {word} untuk {what} (Enter = batal): ") == word


# --------------------------------------------------------------------------- status line (no network, no DB creation)
def catalog_count() -> str:
    import sqlite3
    from . import paths
    db = Path(os.environ["AGENTLOADOUT_DB"]).expanduser() if os.environ.get("AGENTLOADOUT_DB") else paths.home_dir() / "catalog.db"
    if not db.exists():
        return "belum ada"
    try:
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            n = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        finally:
            con.close()
        return f"{n} item"
    except sqlite3.Error:
        return "katalog tidak terbaca"


def _fresh() -> bool:
    """No data folder yet: show the status without creating anything (most modules create it on first use)."""
    from . import paths
    return not paths.home_dir().exists()


def status_parts() -> list[str]:
    if _fresh():
        tok = "env" if os.environ.get("GITHUB_TOKEN") else "env file" if os.environ.get("GITHUB_TOKEN_FILE") else "[ ] belum"
        return [f"v{__version__}", "PIN [ ] belum", "password web [ ]", f"token {tok}", "katalog " + catalog_count(),
                "refresh diam", "rencana menunggu 0", "belum ada folder data (agentloadout setup)"]
    from . import config, pin, refresh, selfupdate, web_password
    parts = []
    ver = f"v{__version__}"
    c = selfupdate.read_cache() or {}
    if c.get("newer"):
        ver += f" (v{c['latest']} tersedia)"
    parts.append(ver)
    st = pin.status()
    parts.append("PIN " + ("[!] rusak" if st.get("invalid") else "[x]" if st.get("set") else "[ ] belum"))
    pw = web_password.status()
    parts.append("password web " + {"hashed": "[x]", "legacy_file": "[x] file teks", "off": "[ ]"}[pw["source"]])
    ti = config.github_token_info()
    parts.append("token " + ({"env": "env", "env_file": "env file", "managed": "tersimpan", "config": "config"}
                             .get(ti["active"], "[ ] belum") if ti["active"] else "[ ] belum"))
    parts.append("katalog " + catalog_count())
    parts.append("refresh " + ("berjalan" if refresh.running() else "diam"))
    parts.append(f"rencana menunggu {pending_count()}")
    return parts


def pending_count() -> int:
    from . import paths, plans
    if not (paths.home_dir() / "plans").is_dir():  # do not create plans/ just to count nothing
        return 0
    try:
        return len(plans.list_plans("pending"))
    except (OSError, ValueError):
        return 0


# --------------------------------------------------------------------------- running existing commands
def run_cmd(*argv: str) -> int:
    """An existing CLI command with all its own checks (the same parser and dispatch as the terminal)."""
    from . import cli
    ap = cli.build_parser()
    return cli.run(ap.parse_args(list(argv)), ap)


def pause():
    """After a choice: wait for Enter. EOF leaves the menu, Ctrl-C just returns."""
    try:
        read_line("\nEnter untuk kembali ke menu... ")
    except KeyboardInterrupt:
        print()


def h_plans():
    from . import cli, plans
    items = plans.list_plans("pending")
    if not items:
        print("Tidak ada rencana menunggu persetujuan.")
        return None
    for i, p in enumerate(items, 1):
        print(f"\n[{i}]", end="")
        cli._print_plan(p)
    pick = ask(f"\nNomor rencana [1-{len(items)}] (Enter = kembali): ")
    if not pick.isdigit() or not 1 <= int(pick) <= len(items):
        return None
    p = items[int(pick) - 1]
    act = ask(f"Rencana {p['id']}: [s]etujui dengan PIN, [t]olak, Enter = kembali: ").lower()
    if act in ("s", "setujui"):
        run_cmd("approve", p["id"])
    elif act in ("t", "tolak"):
        if ask(f"Tolak rencana {p['id']}? [y/N] ").lower() in ("y", "ya", "yes"):
            run_cmd("reject", p["id"])
    return None


def h_web():
    from .web import server as web
    print("Menyalakan web katalog di latar (berhenti sendiri kalau lama tidak dipakai)...")
    ui = web.ensure_ui()
    if not ui:
        print("Web tidak bisa dijalankan (port terpakai?). Coba `agentloadout ui` untuk melihat pesannya.")
        return None
    url = f"http://{ui['host']}:{ui['port']}/"
    print(f"Web katalog: {url}")
    if ask("Buka di browser? [y/N] ").lower() in ("y", "ya", "yes"):
        import webbrowser
        webbrowser.open(url)
    return None


def h_refresh():
    from . import refresh
    if refresh.running():
        print("Pembaruan katalog sedang berjalan. Lihat progresnya di web (pilihan 2) atau di Status (4).")
        return None
    if refresh.start_background(source="menu"):
        print("Pembaruan katalog dimulai di latar. Menu tetap bisa dipakai; lihat progres di web atau Status (4).")
    else:
        print("Pembaruan katalog sedang berjalan.")
    return None


def h_status():
    run_cmd("where")
    return None


def _submenu(title: str, options: list[tuple[str, tuple[str, ...]]]):
    print(title)
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}  {label}")
    pick = ask(f"Pilih [1-{len(options)}] (Enter = kembali): ")
    if pick.isdigit() and 1 <= int(pick) <= len(options):
        run_cmd(*options[int(pick) - 1][1])
    return None


def h_token():
    return _submenu("Token GitHub (menaikkan batas API GitHub; jangan pernah ditempel di chat agen):",
                    [("Simpan token baru (input tersembunyi, diuji ke GitHub)", ("set-token",)),
                     ("Uji token aktif", ("set-token", "--test")),
                     ("Hapus token yang disimpan Agent Loadout", ("set-token", "--clear"))])


def h_pin():
    return _submenu("PIN persetujuan (wajib untuk menyetujui rencana pasang/lepas):",
                    [("Atur atau ganti PIN", ("set-pin",)),
                     ("Lupa PIN: reset (mencabut persetujuan lama)", ("set-pin", "--reset"))])


def h_password():
    return _submenu("Password web (wajib untuk membuka web dari HP atau dari luar laptop):",
                    [("Atur atau ganti password", ("set-password",)),
                     ("Lupa password: reset (semua perangkat keluar)", ("set-password", "--reset")),
                     ("Matikan password (web hanya dari laptop ini)", ("set-password", "--off"))])


def h_setup():
    from . import harness
    found = harness.detected()
    print("Harness terdeteksi:")
    for h in harness.ADAPTERS.values():
        print(f"  [{'x' if h in found else ' '}] {h.id:12} {h.name}")
    ids = ask("Daftarkan ke (pisahkan dengan koma; Enter = semua yang terdeteksi; - = batal): ")
    if ids == "-":
        return None
    run_cmd("setup", *(["--to", ids] if ids else []))  # setup asks "Lanjutkan?" itself
    return None


def h_upgrade():
    from . import selfupdate
    run_cmd("upgrade", "--check")
    c = selfupdate.read_cache() or {}
    if not c.get("newer"):
        return None
    if ask(f"Upgrade ke v{c['latest']} sekarang? [y/N] ").lower() not in ("y", "ya", "yes"):
        return None
    code = run_cmd("upgrade", "--yes")  # newest release only; refuses checkouts/uvx with instructions
    disk = selfupdate.disk_version()
    if code == 0 and disk and disk != __version__:
        # the venv was replaced in place: this process still runs the old code (H5)
        print(f"\nAgent Loadout sekarang v{disk}. Menu ditutup; jalankan `agentloadout` lagi untuk versi baru.")
        return EXIT
    return None


def h_backup():
    from . import backup
    print("Cadangan & pulihkan:\n  1  Cadangkan katalog sekarang\n  2  Pulihkan katalog dari cadangan")
    pick = ask("Pilih [1-2] (Enter = kembali): ")
    if pick == "1":
        run_cmd("backup")
    elif pick == "2":
        files = []
        for kind in ("daily", "weekly"):
            folder = Path(backup.default_dir()) / kind
            if folder.is_dir():
                files += sorted(folder.glob("*.db"), key=lambda f: f.stat().st_mtime)
        if not files:
            print("Belum ada cadangan.")
            return None
        for i, f in enumerate(files, 1):
            print(f"  {i:2}  {f.parent.name}/{f.name}")
        n = ask(f"Nomor cadangan [1-{len(files)}] (Enter = batal): ")
        if n.isdigit() and 1 <= int(n) <= len(files):
            chosen = files[int(n) - 1]
            if confirm_typed("PULIHKAN", f"mengganti katalog sekarang dengan {chosen.name}"):
                run_cmd("restore", str(chosen))
            else:
                print("Dibatalkan.")
    return None


def h_check_updates():
    run_cmd("check-updates")
    return None


def h_uninstall():
    print("Ini melepas MCP agentloadout dan skill panduannya dari harness. Data dan cadangan tetap ada.")
    if confirm_typed("LEPAS", "melepas Agent Loadout dari harness"):
        run_cmd("uninstall", "--yes")
    else:
        print("Dibatalkan.")
    return None


GROUPS = [
    ("Sehari-hari", [("1", "Rencana menunggu", h_plans), ("2", "Buka web katalog", h_web),
                     ("3", "Perbarui katalog sekarang", h_refresh), ("4", "Status lengkap", h_status)]),
    ("Pengaturan", [("5", "Token GitHub", h_token), ("6", "PIN persetujuan", h_pin), ("7", "Password web", h_password)]),
    ("Instalasi & perawatan", [("8", "Pasang ke harness", h_setup), ("9", "Cek & upgrade Agent Loadout", h_upgrade),
                               ("10", "Cadangan & pulihkan", h_backup), ("11", "Cek versi baru item terpasang", h_check_updates),
                               ("12", "Lepas Agent Loadout", h_uninstall)]),
]


def handlers() -> dict:
    return {key: fn for _, items in GROUPS for key, _, fn in items}


def wrap_parts(parts: list[str], w: int) -> list[str]:
    """Join status parts with " | " and break lines only between parts (a part longer than w is cut)."""
    lines, cur = [], ""
    for part in parts:
        part = part[:w]
        cand = f"{cur} | {part}" if cur else part
        if len(cand) <= w:
            cur = cand
        else:
            lines.append(cur)
            cur = part
    return lines + ([cur] if cur else [])


def render(w: int) -> str:
    out = []
    lines = banner_lines(w)
    if color_ok():
        lines = [f"\x1b[1;36m{x}\x1b[0m" if x else x for x in lines]
    out += lines
    out.append("")
    out += wrap_parts(status_parts(), w)
    pending = pending_count()
    entries = []
    for title, items in GROUPS:
        block = [title]
        for key, label, _ in items:
            if key == "1":
                label = f"{label} ({pending})"
            block.append(f" {key:>2}  {label}")
        entries.append(block)
    entries[-1].append("  0  Keluar")
    out.append("")
    if w >= 72:  # two columns: daily | settings, then maintenance
        left, right = entries[0], entries[1]
        for i in range(max(len(left), len(right))):
            a = left[i] if i < len(left) else ""
            b = right[i] if i < len(right) else ""
            out.append(f"{a:<36}{b}".rstrip())
        out.append("")
        out += entries[2]
    else:
        for block in entries:
            out += [x[:w] for x in block] + [""]
    return "\n".join(x[:w] if not x.startswith("\x1b") else x for x in out).rstrip() + "\n"


def run_menu() -> int:
    table = handlers()
    while True:
        if color_ok():
            sys.stdout.write("\x1b[H\x1b[2J")
        try:
            sys.stdout.write(render(width()))
            choice = ask(f"\nPilih [0-{len(table)}]: ").lower()
        except (KeyboardInterrupt, EOFError):
            print("\nKeluar.")
            return 0
        except IdleTimeout:
            print(f"\nTidak ada input selama {int(idle_seconds())} detik; menu ditutup.")
            return 0
        if choice in ("0", "q", "keluar", "exit"):
            print("Keluar.")
            return 0
        fn = table.get(choice)
        if not fn:
            print(f"Pilihan tidak dikenal: {choice!r}")
            try:
                pause()
            except (EOFError, IdleTimeout):
                return 0
            continue
        print()
        try:
            result = fn()
        except KeyboardInterrupt:  # Ctrl-C inside a choice: straight back to the menu
            print("\nDibatalkan, kembali ke menu.")
            continue
        except EOFError:
            print("\nKeluar.")
            return 0
        except IdleTimeout:
            print("\nTidak ada input; menu ditutup.")
            return 0
        if result == EXIT:
            return 0
        try:
            pause()
        except (EOFError, IdleTimeout):
            print("\nKeluar.")
            return 0
