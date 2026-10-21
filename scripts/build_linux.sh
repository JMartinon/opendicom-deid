#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${PYTHON:-python3}"
# Bootstrap uses only the standard library; the selected build Python is checked
# before pip or tests run. A working system Python can replace a Tk-less venv.
exec "$python_bin" scripts/build_linux.py
