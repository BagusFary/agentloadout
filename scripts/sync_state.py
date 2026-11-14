#!/usr/bin/env python3
"""Pembungkus untuk instance pribadi: meneruskan ke modul agentloadout.integrations.openbot_sync."""
import os
import runpy
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("AGENTLOADOUT_HOME", os.path.join(ROOT, "data"))
os.environ.setdefault("AGENTLOADOUT_DB", os.path.join(ROOT, "data", "katalog.db"))
os.environ.setdefault("AGENTLOADOUT_BACKUP_DIR", os.path.expanduser("~/Backups/agentloadout"))
runpy.run_module("agentloadout.integrations.openbot_sync", run_name="__main__", alter_sys=True)
