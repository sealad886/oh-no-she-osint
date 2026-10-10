#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
    command -v python3 >/dev/null 2>&1 || { echo 'Install Python 3.11 or newer first.' >&2; exit 1; }
    python3 -m venv .venv
fi
for argument do
    if [ "$argument" = "--direct" ]; then
        exec .venv/bin/python -m local_osint serve --open --allow-network "$@"
    fi
done
exec .venv/bin/python -m local_osint serve --open --allow-network --nginx "$@"
