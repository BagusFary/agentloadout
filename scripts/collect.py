#!/usr/bin/env python3
"""Pembungkus untuk instance pribadi: menjalankan kolektor agentloadout di bawah kunci refresh yang sama
dengan refresh dari MCP, web, dan `agentloadout refresh`.

Kalau refresh lain sedang berjalan, koleksi ini dilewati (exit 0, routine tidak dianggap gagal).
Cadangan dibuat oleh scripts/backup.py di update.sh, jadi di sini tidak dicadangkan lagi.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("AGENTLOADOUT_HOME", os.path.join(ROOT, "data"))
os.environ.setdefault("AGENTLOADOUT_DB", os.path.join(ROOT, "data", "katalog.db"))
os.environ.setdefault("AGENTLOADOUT_BACKUP_DIR", os.path.expanduser("~/Backups/agentloadout"))

from agentloadout import refresh  # noqa: E402

fd = refresh.try_lock()
if fd is None:
    print("refresh lain sedang berjalan (MCP/web/CLI); koleksi harian ini dilewati.", flush=True)
    sys.exit(0)
sys.exit(refresh.run_locked(fd, source=os.environ.get("AGENTLOADOUT_REFRESH_SOURCE", "routine"), backup=False))
