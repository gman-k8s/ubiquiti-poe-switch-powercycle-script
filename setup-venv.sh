#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "FEHLER: '$PYTHON_BIN' nicht gefunden." >&2
    exit 1
fi

"$PYTHON_BIN" -m venv .venv
.venv/bin/python3 -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "OK: .venv bereit unter $(pwd)/.venv"
echo "Versionen fixiert in requirements.txt (manuell aktualisieren + neu ausfuehren)."
