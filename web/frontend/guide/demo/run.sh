#!/usr/bin/env bash
# Start the screenshot demo: the real backend from this checkout with fakes
# for every host it would talk to, a NATS container of its own, and three
# weeks of seeded history. Local development only - see README.md.
#
#   run.sh [STATE_DIR]
#
# STATE_DIR defaults to /tmp/prtg-nats-guide-demo. Running it again while the
# demo is up only prints where it is.
set -Eeuo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${DEMO_DIR}/../../../.." && pwd)"
STATE_DIR="${1:-${PRTG_NATS_DEMO_STATE:-/tmp/prtg-nats-guide-demo}}"
NATS_CONTAINER="prtg-nats-guide-demo-nats"
# The image compose.yaml pins, so the demo runs the server version the stack
# ships.
NATS_IMAGE="nats:2.10.29-alpine@sha256:b83efabe3e7def1e0a4a31ec6e078999bb17c80363f881df35edc70fcb6bb927"

say() { printf '%s\n' "$*"; }
die() { printf 'run.sh: %s\n' "$*" >&2; exit 1; }

port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# The first free port at or above the given one. Other sessions on this
# machine run their own servers; the demo steps around them rather than
# assuming a fixed port.
free_port() {
  local port="$1"
  while ! port_free "${port}"; do port=$((port + 1)); done
  printf '%s' "${port}"
}

json_value() {
  sed -n "s/.*\"$2\": *\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" "$1" | head -n 1
}

command -v docker >/dev/null || die "docker is needed for the NATS container"
command -v lsof >/dev/null || die "lsof is needed to find free ports"

mkdir -p "${STATE_DIR}"
STATE_DIR="$(cd "${STATE_DIR}" && pwd)"
# Marks the directory as the demo's, so stop.sh --purge removes nothing else.
touch "${STATE_DIR}/.prtg-nats-guide-demo"

PORTS="${STATE_DIR}/ports.json"
if [[ -f "${STATE_DIR}/backend.pid" ]] && kill -0 "$(cat "${STATE_DIR}/backend.pid")" 2>/dev/null; then
  say "The demo is already running."
else
  if [[ ! -f "${PORTS}" ]]; then
    api="$(free_port 18100)"
    nats="$(free_port 24561)"
    monitor="$(free_port 18222)"
    printf '{"api": %s, "nats": %s, "nats_monitor": %s, "nats_image": "%s"}\n' \
      "${api}" "${nats}" "${monitor}" "${NATS_IMAGE}" > "${PORTS}"
  fi
  api="$(json_value "${PORTS}" api)"
  nats="$(json_value "${PORTS}" nats)"
  monitor="$(json_value "${PORTS}" nats_monitor)"
  port_free "${api}" || die "port ${api} is taken; remove ${PORTS} to pick new ports"

  VENV="${STATE_DIR}/venv"
  if [[ ! -x "${VENV}/bin/python" ]]; then
    say "Creating the Python environment in ${VENV} ..."
    if command -v uv >/dev/null; then
      uv venv --quiet --python 3.11 "${VENV}"
      # The locked dependencies, without installing the backend itself: it
      # runs from this checkout, so a change shows on restart.
      uv pip sync --quiet --python "${VENV}/bin/python" \
        "${REPO_DIR}/web/backend/requirements.lock"
    else
      python3.11 -m venv "${VENV}"
      "${VENV}/bin/pip" install --quiet --require-hashes \
        -r "${REPO_DIR}/web/backend/requirements.lock"
    fi
  fi

  rm -f "${STATE_DIR}/ready"
  say "Starting the backend on 127.0.0.1:${api} ..."
  (
    cd "${REPO_DIR}/web/backend"
    PYTHONDONTWRITEBYTECODE=1 \
    DEMO_STATE_DIR="${STATE_DIR}" \
    DEMO_API_PORT="${api}" \
    DEMO_NATS_PORT="${nats}" \
    DEMO_NATS_MONITOR_PORT="${monitor}" \
    DEMO_NATS_CONTAINER="${NATS_CONTAINER}" \
      nohup "${VENV}/bin/python" "${DEMO_DIR}/server.py" \
      >>"${STATE_DIR}/backend.log" 2>&1 &
    printf '%s\n' "$!" > "${STATE_DIR}/backend.pid"
  )

  # The first start seeds three weeks of history through the API, which
  # takes a few minutes; later starts are ready in seconds.
  for _ in $(seq 1 600); do
    [[ -f "${STATE_DIR}/ready" ]] && break
    kill -0 "$(cat "${STATE_DIR}/backend.pid")" 2>/dev/null ||
      die "the backend stopped; see ${STATE_DIR}/backend.log"
    sleep 1
  done
  [[ -f "${STATE_DIR}/ready" ]] || die "not ready after 10 minutes; see ${STATE_DIR}/backend.log"
  if grep -q "seeding failed" "${STATE_DIR}/backend.log"; then
    say "WARNING: seeding reported an error; see ${STATE_DIR}/backend.log"
  fi
fi

api="$(json_value "${PORTS}" api)"
monitor="$(json_value "${PORTS}" nats_monitor)"
say ""
say "Backend:        http://127.0.0.1:${api}  (API docs at /api/docs)"
say "NATS monitor:   http://127.0.0.1:${monitor}  (container ${NATS_CONTAINER})"
say "State:          ${STATE_DIR}"
say "Interface:      ${DEMO_DIR}/frontend.sh ${STATE_DIR}"
say "Accounts:       admin / demo-admin-2026!   olga / demo-olga-2026!   viktor / demo-viktor-2026!"
say "Stop:           ${DEMO_DIR}/stop.sh ${STATE_DIR}  (--purge also deletes the state)"
