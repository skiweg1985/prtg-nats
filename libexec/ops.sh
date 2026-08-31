#!/usr/bin/env bash
#
# Reaching the Python operations module.
#
# Its own file because both the entry point and the subcommand groups under
# libexec/ delegate to it, and a second copy of "how do we run app.ops here"
# is how the two would end up disagreeing about which runtime they act on.
#
# Sourcing this needs PROJECT_DIR and LIBEXEC_DIR to be set.

API_CONTAINER="prtg-nats-web-api"

api_container_running() {
  [[ "$(
    docker inspect --format '{{.State.Running}}' "${API_CONTAINER}" 2>/dev/null ||
      true
  )" == "true" ]]
}

# --- Recovery commands delegate to the Python operations module -------------
#
# One implementation for certificates, accounts and verification: the same
# services the web platform runs.
#
# The API container is the first choice, because it is the one place the
# backend is guaranteed to be installed and already mounts the runtime volume.
# Nothing has to be installed on the host for "setup" to work - and it has to
# work there, because the interface that would otherwise finish the setup sits
# behind a proxy that cannot start until the runtime exists.
#
# A local interpreter is the fallback for a checkout that has the backend in a
# venv, which is what the end-to-end test uses.
ops_python() {
  local candidate=""

  for candidate in \
    "${PROJECT_DIR}/web/backend/.venv/bin/python" \
    python3; do
    # Importing app.ops proves nothing: its heavy imports are deferred into
    # the subcommands, so an interpreter without the dependencies passes and
    # then fails inside the command with a bare "No module named httpx".
    # Importing what a subcommand actually reaches for is the honest check.
    if PRTG_NATS_WEB_PROJECT_DIR="${PROJECT_DIR}" \
      PYTHONPATH="${PROJECT_DIR}/web/backend" \
      "${candidate}" -c 'import app.ops, app.infrastructure.docker' \
      >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

run_ops() {
  local interpreter=""

  if api_container_running; then
    docker exec "${API_CONTAINER}" python -m app.ops "$@"
    return
  fi

  interpreter="$(ops_python)" ||
    {
      printf 'The management API is not running and this machine has no backend.\n' >&2
      printf 'Start the stack, which is where these commands run:\n' >&2
      printf '  sudo ./prtg-nats start\n' >&2
      printf 'For a checkout without the stack, install the backend once:\n' >&2
      printf '  python3 -m venv web/backend/.venv\n' >&2
      printf '  web/backend/.venv/bin/pip install -e web/backend\n' >&2
      return 1
    }
  # The runtime path has to travel along. Without it the backend falls back to
  # PROJECT_DIR/runtime and writes the CA, the credentials and the database
  # beside the checkout, while this script keeps reading the volume - two
  # installations, one of which nothing serves. The API container carries the
  # same value in its image; here it is whatever runtime-dir.sh resolved.
  # shellcheck source=libexec/runtime-dir.sh
  source "${LIBEXEC_DIR}/runtime-dir.sh"
  PRTG_NATS_WEB_PROJECT_DIR="${PROJECT_DIR}" \
    PRTG_NATS_WEB_RUNTIME_DIR="${RUNTIME_DIR}" \
    PYTHONPATH="${PROJECT_DIR}/web/backend" \
    "${interpreter}" -m app.ops "$@"
}
