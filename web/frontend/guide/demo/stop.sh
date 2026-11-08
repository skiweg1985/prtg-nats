#!/usr/bin/env bash
# Stop the screenshot demo: the Vite dev server, the backend and its NATS
# container. The state stays for the next run.sh unless --purge is given.
#
#   stop.sh [STATE_DIR] [--purge]
set -Eeuo pipefail

STATE_DIR="${PRTG_NATS_DEMO_STATE:-/tmp/prtg-nats-guide-demo}"
PURGE=false
for argument in "$@"; do
  case "${argument}" in
    --purge) PURGE=true ;;
    *) STATE_DIR="${argument}" ;;
  esac
done
NATS_CONTAINER="prtg-nats-guide-demo-nats"

stop_pid() {
  local file="$1"
  [[ -f "${file}" ]] || return 0
  local pid
  pid="$(cat "${file}")"
  if kill -0 "${pid}" 2>/dev/null; then
    # npx starts vite as a child; the whole group goes.
    pkill -TERM -P "${pid}" 2>/dev/null || true
    kill "${pid}" 2>/dev/null || true
  fi
  rm -f "${file}"
}

stop_pid "${STATE_DIR}/vite.pid"
stop_pid "${STATE_DIR}/backend.pid"
rm -f "${STATE_DIR}/ready" "${STATE_DIR}/vite.port"
# Only the demo's own container, by its exact name.
docker rm -f "${NATS_CONTAINER}" >/dev/null 2>&1 || true

if [[ "${PURGE}" == true ]]; then
  if [[ -f "${STATE_DIR}/.prtg-nats-guide-demo" ]]; then
    rm -rf "${STATE_DIR}"
    printf 'Stopped and removed %s\n' "${STATE_DIR}"
  else
    printf 'stop.sh: %s is not a demo state directory; left alone\n' "${STATE_DIR}" >&2
    exit 1
  fi
else
  printf 'Stopped. The state in %s is kept; run.sh starts it again.\n' "${STATE_DIR}"
fi
