#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${PYTHON:-python3}"
"$python_bin" scripts/build_environment.py
"$python_bin" -m pip install '.[build,dev]'
"$python_bin" -m pytest
"$python_bin" scripts/build.py
