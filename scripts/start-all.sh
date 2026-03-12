#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${TMPDIR:-/tmp}/multi-codereview-agent"
LOG_DIR="${ROOT_DIR}/logs"
mkdir -p "${RUN_DIR}"
mkdir -p "${LOG_DIR}"

BACKEND_LOG="${LOG_DIR}/backend.log"
FRONTEND_LOG="${LOG_DIR}/frontend.log"
BACKEND_PID_FILE="${RUN_DIR}/backend.pid"
FRONTEND_PID_FILE="${RUN_DIR}/frontend.pid"

if [[ -f "${BACKEND_PID_FILE}" ]] && kill -0 "$(cat "${BACKEND_PID_FILE}")" 2>/dev/null; then
  echo "backend already running with pid $(cat "${BACKEND_PID_FILE}")"
else
  nohup "${ROOT_DIR}/.venv/bin/uvicorn" app.main:app --app-dir "${ROOT_DIR}/backend" --reload --port 8000 >"${BACKEND_LOG}" 2>&1 &
  echo $! >"${BACKEND_PID_FILE}"
  echo "started backend on http://127.0.0.1:8000"
fi

if [[ -f "${FRONTEND_PID_FILE}" ]] && kill -0 "$(cat "${FRONTEND_PID_FILE}")" 2>/dev/null; then
  echo "frontend already running with pid $(cat "${FRONTEND_PID_FILE}")"
else
  (
    cd "${ROOT_DIR}/frontend"
    nohup npm run dev -- --host 127.0.0.1 --port 5173 >"${FRONTEND_LOG}" 2>&1 &
    echo $! >"${FRONTEND_PID_FILE}"
  )
  echo "started frontend on http://127.0.0.1:5173"
fi

echo "logs:"
echo "  backend  ${BACKEND_LOG}"
echo "  frontend ${FRONTEND_LOG}"
