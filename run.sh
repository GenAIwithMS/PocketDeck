#!/usr/bin/env bash
# PocketDeck launcher for Linux. Usage: ./run.sh [--port 8765] [--pin 123456]
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is not installed."
    echo "  Fedora:        sudo dnf install python3"
    echo "  Ubuntu/Debian: sudo apt install python3"
    exit 1
fi

exec python3 pocketdeck.py "$@"
