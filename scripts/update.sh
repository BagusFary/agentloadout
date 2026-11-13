#!/usr/bin/env bash
# Perbarui data katalog. Dipanggil oleh routine harian OpenBot.
set -euo pipefail
cd "$(dirname "$0")/.."
export GITHUB_TOKEN_FILE="${GITHUB_TOKEN_FILE:-/home/bagus/.config/OpenBot/agent-api-keys/agent-4b18fa4a-ba4c-4376-93c7-209e5bbc3d4a/GITHUB_TOKEN}"
python3 collector/collect.py > data/collect.log 2>&1
tail -1 data/collect.log
