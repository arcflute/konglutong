#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/python -c 'import sys; assert sys.prefix != sys.base_prefix, "independent environment required"'
.venv/bin/python -m pip install --no-cache-dir -r requirements-build.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
