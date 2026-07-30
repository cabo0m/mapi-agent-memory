#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WHEELHOUSE="${2:-}"
unset PYTHONPATH

CLEAN_ROOT="$(mktemp -d /tmp/mapi-public-clean-linux-XXXXXX)"
echo "CLEAN_ROOT=${CLEAN_ROOT}"

if python3 -m venv "${CLEAN_ROOT}/.venv" 2>/dev/null; then
  :
elif [[ -n "${WHEELHOUSE}" && -f "${WHEELHOUSE}/virtualenv.pyz" ]]; then
  python3 "${WHEELHOUSE}/virtualenv.pyz" "${CLEAN_ROOT}/.venv" --quiet
else
  echo "Unable to create a virtual environment." >&2
  exit 1
fi
PYTHON="${CLEAN_ROOT}/.venv/bin/python"
PIP="${CLEAN_ROOT}/.venv/bin/pip"

if [[ -n "${WHEELHOUSE}" ]]; then
  "${PIP}" install \
    --no-index \
    --find-links "${WHEELHOUSE}" \
    "mapi-agent-memory[dev]" \
    --quiet
else
  "${PIP}" install "${SOURCE_ROOT}[dev]" --quiet
fi

export MAPI_ROOT="${CLEAN_ROOT}"
export MAPI_DATA_DIR="${CLEAN_ROOT}/data"
export MAPI_DB_PATH="${CLEAN_ROOT}/data/mapi.db"
export MCP_SURFACE_PROFILE="agent"
export MAPI_ADMIN_TOOLS_ENABLED="false"
export MAPI_GEMINI_ENABLED="false"
export MAPI_SEMANTIC_ENABLED="false"
export MAPI_RUNTIME_HOST="127.0.0.1"
export MAPI_RUNTIME_PORT="8128"

"${CLEAN_ROOT}/.venv/bin/mapi-migrate"
"${CLEAN_ROOT}/.venv/bin/mapi-seed-demo"
"${CLEAN_ROOT}/.venv/bin/mapi-seed-demo"
"${CLEAN_ROOT}/.venv/bin/mapi-doctor"

"${CLEAN_ROOT}/.venv/bin/mapi-server" \
  >"${CLEAN_ROOT}/server.stdout.log" \
  2>"${CLEAN_ROOT}/server.stderr.log" &
SERVER_PID=$!
cleanup() {
  kill "${SERVER_PID}" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 60); do
  if (echo >/dev/tcp/127.0.0.1/8128) >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    cat "${CLEAN_ROOT}/server.stderr.log"
    exit 1
  fi
  sleep 0.5
done

"${PYTHON}" "${SOURCE_ROOT}/scripts/smoke_mcp.py" \
  --url "http://127.0.0.1:8128/mcp/"

"${PYTHON}" -m pytest \
  "${SOURCE_ROOT}/tests/test_public_surface.py" \
  "${SOURCE_ROOT}/tests/test_public_packaging.py" \
  "${SOURCE_ROOT}/tests/test_public_memory_workflow.py" \
  -q -p no:cacheprovider --basetemp "${CLEAN_ROOT}/pytest-tmp"

kill "${SERVER_PID}"
wait "${SERVER_PID}" || true
trap - EXIT

python3 --version
grep -E '^(NAME|VERSION)=' /etc/os-release
