#!/usr/bin/env bash
# Perbarui data katalog. Dipanggil oleh routine harian OpenBot.
set -euo pipefail
cd "$(dirname "$0")/.."
# token, paths and backup folder come from data/config.toml via the wrapper scripts
# back up BEFORE writing new data, so a buggy run never overwrites the last good copy
python3 scripts/backup.py > data/collect.log 2>&1 || { echo "CADANGAN GAGAL, fetch dibatalkan" | tee -a data/collect.log; exit 1; }
python3 scripts/collect.py >> data/collect.log 2>&1
python3 scripts/translate.py --apply >> data/collect.log 2>&1 || echo "terjemahan gagal diterapkan (lanjut)" >> data/collect.log
python3 scripts/translate.py --export data/to_translate.json >> data/collect.log 2>&1 || true
python3 scripts/check_updates.py >> data/collect.log 2>&1 || echo "cek update gagal (lanjut)" >> data/collect.log
# summary lines; a missing line must not fail the routine (e.g. the collection was skipped because another
# refresh held the lock: collect.py then prints "...dilewati" and exits 0)
grep "cadangan OK" data/collect.log | tail -1 || true
grep -E "selesai dalam|dilewati" data/collect.log | tail -1 || true
grep "diekspor" data/collect.log | tail -1 || true
grep "update tersedia" data/collect.log | tail -1 || true
