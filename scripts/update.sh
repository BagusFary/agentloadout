#!/usr/bin/env bash
# Perbarui data katalog. Dipanggil oleh routine harian OpenBot.
set -euo pipefail
cd "$(dirname "$0")/.."
export GITHUB_TOKEN_FILE="${GITHUB_TOKEN_FILE:-/home/bagus/.config/OpenBot/agent-api-keys/agent-4b18fa4a-ba4c-4376-93c7-209e5bbc3d4a/GITHUB_TOKEN}"
# back up BEFORE writing new data, so a buggy run never overwrites the last good copy
python3 scripts/backup.py > data/collect.log 2>&1 || { echo "CADANGAN GAGAL, fetch dibatalkan" | tee -a data/collect.log; exit 1; }
python3 collector/collect.py >> data/collect.log 2>&1
python3 scripts/translate.py --apply >> data/collect.log 2>&1 || echo "terjemahan gagal diterapkan (lanjut)" >> data/collect.log
python3 scripts/translate.py --export data/to_translate.json >> data/collect.log 2>&1 || true
python3 scripts/check_updates.py >> data/collect.log 2>&1 || echo "cek update gagal (lanjut)" >> data/collect.log
grep "cadangan OK" data/collect.log | tail -1
grep "selesai dalam" data/collect.log | tail -1
grep "diekspor" data/collect.log | tail -1
grep "update tersedia" data/collect.log | tail -1
