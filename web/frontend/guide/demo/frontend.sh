#!/usr/bin/env bash
# Start the Vite dev server against the demo backend that run.sh started.
#
#   frontend.sh [STATE_DIR]
#
# Runs in the background and prints the address. The port is the first free
# one from 15173 and is kept in STATE_DIR/ports.json beside the others.
set -Eeuo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "${DEMO_DIR}/../.." && pwd)"
STATE_DIR="${1:-${PRTG_NATS_DEMO_STATE:-/tmp/prtg-nats-guide-demo}}"
PORTS="${STATE_DIR}/ports.json"

die() { printf 'frontend.sh: %s\n' "$*" >&2; exit 1; }
[[ -f "${PORTS}" ]] || die "no demo in ${STATE_DIR}; start it with run.sh first"

api="$(sed -n 's/.*"api": *\([0-9]*\).*/\1/p' "${PORTS}")"
if [[ -f "${STATE_DIR}/vite.pid" ]] && kill -0 "$(cat "${STATE_DIR}/vite.pid")" 2>/dev/null; then
  port="$(cat "${STATE_DIR}/vite.port")"
else
  port=15173
  while lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; do port=$((port + 1)); done
  [[ -d "${FRONTEND_DIR}/node_modules" ]] || (cd "${FRONTEND_DIR}" && npm ci --no-audit --no-fund)
  (
    cd "${FRONTEND_DIR}"
    # vite.config.ts proxies /api to this address; the default is 8100.
    VITE_API_PROXY_TARGET="http://127.0.0.1:${api}" \
      nohup npx vite --host 127.0.0.1 --port "${port}" --strictPort \
      >>"${STATE_DIR}/vite.log" 2>&1 &
    printf '%s\n' "$!" > "${STATE_DIR}/vite.pid"
  )
  printf '%s\n' "${port}" > "${STATE_DIR}/vite.port"
  for _ in $(seq 1 60); do
    curl -fsS -o /dev/null "http://127.0.0.1:${port}/" 2>/dev/null && break
    sleep 1
  done
fi
printf 'Interface: http://127.0.0.1:%s  (API proxied to 127.0.0.1:%s)\n' "${port}" "${api}"
