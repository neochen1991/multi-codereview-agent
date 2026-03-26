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
BACKEND_URL="http://127.0.0.1:8011/health"
FRONTEND_URL="http://127.0.0.1:5174"

cleanup_stale_pid() {
  local pid_file="$1"
  if [[ ! -f "${pid_file}" ]]; then
    return
  fi
  local pid
  pid="$(cat "${pid_file}")"
  if kill -0 "${pid}" 2>/dev/null; then
    return
  fi
  rm -f "${pid_file}"
}

wait_for_http() {
  local url="$1"
  local attempts="${2:-20}"
  local sleep_seconds="${3:-1}"
  local index=0
  while (( index < attempts )); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${sleep_seconds}"
    index=$((index + 1))
  done
  return 1
}

process_alive() {
  local pid_file="$1"
  if [[ ! -f "${pid_file}" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "${pid_file}")"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" 2>/dev/null
}

print_start_failure() {
  local service_name="$1"
  local pid_file="$2"
  local log_file="$3"
  echo "${service_name} failed to start, check ${log_file}"
  if [[ -f "${pid_file}" ]]; then
    echo "recorded pid: $(cat "${pid_file}")"
  else
    echo "pid file not created: ${pid_file}"
  fi
  if [[ -f "${log_file}" ]]; then
    tail -n 60 "${log_file}" || true
  fi
}

start_backend() {
  cleanup_stale_pid "${BACKEND_PID_FILE}"
  if [[ -f "${BACKEND_PID_FILE}" ]] && kill -0 "$(cat "${BACKEND_PID_FILE}")" 2>/dev/null; then
    echo "backend already running with pid $(cat "${BACKEND_PID_FILE}")"
    return
  fi
  if wait_for_http "${BACKEND_URL}" 1 0; then
    echo "backend already serving on ${BACKEND_URL}"
    return
  fi
  (
    cd "${ROOT_DIR}"
    nohup "${ROOT_DIR}/.venv/bin/uvicorn" app.main:app --app-dir "${ROOT_DIR}/backend" --port 8011 >"${BACKEND_LOG}" 2>&1 &
    echo $! >"${BACKEND_PID_FILE}"
  )
  local index=0
  while (( index < 20 )); do
    if wait_for_http "${BACKEND_URL}" 1 0; then
      echo "started backend on http://127.0.0.1:8011"
      return
    fi
    if ! process_alive "${BACKEND_PID_FILE}"; then
      print_start_failure "backend" "${BACKEND_PID_FILE}" "${BACKEND_LOG}"
      exit 1
    fi
    sleep 1
    index=$((index + 1))
  done
  print_start_failure "backend" "${BACKEND_PID_FILE}" "${BACKEND_LOG}"
  exit 1
}

start_frontend() {
  cleanup_stale_pid "${FRONTEND_PID_FILE}"
  if [[ -f "${FRONTEND_PID_FILE}" ]] && kill -0 "$(cat "${FRONTEND_PID_FILE}")" 2>/dev/null; then
    echo "frontend already running with pid $(cat "${FRONTEND_PID_FILE}")"
    return
  fi
  if wait_for_http "${FRONTEND_URL}" 1 0; then
    echo "frontend already serving on ${FRONTEND_URL}"
    return
  fi
  (
    cd "${ROOT_DIR}/frontend"
    nohup npm run dev -- --host 127.0.0.1 --port 5174 --strictPort >"${FRONTEND_LOG}" 2>&1 &
    echo $! >"${FRONTEND_PID_FILE}"
  )
  local index=0
  while (( index < 30 )); do
    if wait_for_http "${FRONTEND_URL}" 1 0; then
      echo "started frontend on http://127.0.0.1:5174"
      return
    fi
    if ! process_alive "${FRONTEND_PID_FILE}"; then
      print_start_failure "frontend" "${FRONTEND_PID_FILE}" "${FRONTEND_LOG}"
      exit 1
    fi
    sleep 1
    index=$((index + 1))
  done
  print_start_failure "frontend" "${FRONTEND_PID_FILE}" "${FRONTEND_LOG}"
  exit 1
}

start_backend
start_frontend

echo "logs:"
echo "  backend  ${BACKEND_LOG}"
echo "  frontend ${FRONTEND_LOG}"
