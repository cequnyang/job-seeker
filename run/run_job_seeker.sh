#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-}"
VENV_DIR="${VENV_DIR:-.venv}"

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  elif [[ -x "$VENV_DIR/bin/python" ]]; then
    PYTHON_BIN="$VENV_DIR/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

if ! "$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

missing = [
    name
    for name in ("dotenv", "pypdf", "playwright", "yaml")
    if importlib.util.find_spec(name) is None
]
if missing:
    print("Missing Python dependencies: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
PY
then
  cat >&2 <<EOF

Install the Linux runtime dependencies first:

  chmod +x ./run/setup_linux_env.sh
  ./run/setup_linux_env.sh

This creates .venv, installs requirements.txt there, and installs Playwright's Chromium browser.

Active Python was:

  $PYTHON_BIN

Then rerun:

  ./run/run_job_seeker.sh ...
EOF
  exit 2
fi

exec "$PYTHON_BIN" ./scripts/job_seeker_launcher.py "$@"
