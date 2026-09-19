#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "FEHLER: 'uv' nicht gefunden." >&2
    echo "Installation: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

uv sync

echo "OK: .venv bereit unter $(pwd)/.venv"
echo "Versionen fixiert in uv.lock (siehe 'uv lock --upgrade' fuer Updates)."
