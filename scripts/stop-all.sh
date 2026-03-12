#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${TMPDIR:-/tmp}/multi-codereview-agent"
BACKEND_PID_FILE="${RUN_DIR}/backend.pid"
FRONTEND_PID_FILE="${RUN_DIR}/frontend.pid"

stop_pid_file() {
  local pid_file="$1"
  local label="$2"
  if [[ ! -f "${pid_file}" ]]; then
    echo "${label} not running"
    return
  fi
  local pid
  pid="$(cat "${pid_file}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}"
    echo "stopped ${label} (${pid})"
  else
    echo "${label} pid ${pid} already exited"
  fi
  rm -f "${pid_file}"
}

stop_pid_file "${BACKEND_PID_FILE}" "backend"
stop_pid_file "${FRONTEND_PID_FILE}" "frontend"
