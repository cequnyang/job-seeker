#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

VENV_DIR="${VENV_DIR:-.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PROFILE_DIR="${LINKEDIN_PROFILE_DIR:-.linkedin_profile}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Local virtual environment was not found. Preparing it first..." >&2
  chmod +x ./run/setup_linux_env.sh
  VENV_DIR="$VENV_DIR" PYTHON_BIN="$PYTHON_BIN" ./run/setup_linux_env.sh
fi

PYTHON_BIN="$VENV_DIR/bin/python"

if ! "$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

missing = [
    name
    for name in ("playwright", "yaml")
    if importlib.util.find_spec(name) is None
]
if missing:
    print("Missing Python dependencies: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
PY
then
  cat >&2 <<'EOF'

Login dependencies are missing. Refresh the isolated environment:

  ./run/setup_linux_env.sh

Then rerun:

  ./run/linkedin_login.sh
EOF
  exit 2
fi

cat <<EOF
Starting LinkedIn login.

Complete login / 2FA / captcha in the opened browser window, then return to this terminal and press Enter.
Profile directory: $PROFILE_DIR

EOF

exec "$PYTHON_BIN" ./scripts/pipeline/linkedin_jobs.py login --profile-dir "$PROFILE_DIR" "$@"
