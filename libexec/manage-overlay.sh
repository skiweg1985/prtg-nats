#!/usr/bin/env bash
#
# The overlay: a WireGuard tunnel between this host and the probes.
#
# Two halves, deliberately split. Enabling and disabling write .env and drive
# the compose profile, which only works here - the API container has the
# runtime volume and the Docker socket but no checkout to write .env into.
# Everything about a single probe is app.ops, so the interface and this script
# put a probe on the overlay the same way rather than two ways.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
LIBEXEC_DIR="${SCRIPT_DIR}"
ENVIRONMENT_FILE="${PROJECT_DIR}/.env"

usage() {
  cat <<'USAGE'
Usage: prtg-nats overlay COMMAND [ARGUMENTS]

  status                    the hub, its peers and what each one is doing
  enable [--endpoint HOST]  turn the overlay on for this installation
  disable                   turn it off; the peers keep their addresses
  add USER [--mode MODE]    put a probe on the overlay
  remove USER [--force]     take it off again
  mode USER MODE [--force]  change when its NATS traffic takes the tunnel
  show USER                 one probe's overlay state

MODE is one of:

  off    no tunnel
  auto   tunnel up; NATS takes it only while the direct path is down
  on     tunnel up; NATS always takes it

The management channel uses the overlay address in auto and on, falling back
to the probe's ordinary address when the tunnel does not answer.
USAGE
}

case "${1:-}" in
  -h | --help | help | '')
    usage
    exit 0
    ;;
esac

# shellcheck source=common.sh
source "${LIBEXEC_DIR}/common.sh"
# shellcheck source=ops.sh
source "${LIBEXEC_DIR}/ops.sh"

require_probe_name() {
  local username="${1:-}"

  [[ -n "${username}" ]] || die "This command needs a probe account name"
  validate_nats_username "${username}" ||
    die "Invalid probe account name: ${username}"
  [[ -f "$(probe_path "${username}")" ]] ||
    die "No probe is enrolled as ${username}"
}

# Rewrites one key in .env, or appends it. The file is the operator's, so the
# comments and the order stay as they are - only the value moves.
set_environment_value() {
  local key="$1"
  local value="$2"
  local temporary=""

  [[ -f "${ENVIRONMENT_FILE}" ]] ||
    die "There is no .env yet. Run \"./prtg-nats config --edit\" first."
  temporary="$(mktemp "${ENVIRONMENT_FILE}.XXXXXX")"
  if grep -qE "^#?${key}=" "${ENVIRONMENT_FILE}"; then
    sed -E "s|^#?${key}=.*|${key}=${value}|" "${ENVIRONMENT_FILE}" > "${temporary}"
  else
    cat "${ENVIRONMENT_FILE}" > "${temporary}"
    printf '%s=%s\n' "${key}" "${value}" >> "${temporary}"
  fi
  chmod 600 "${temporary}"
  mv -f "${temporary}" "${ENVIRONMENT_FILE}"
}

remove_environment_value() {
  local key="$1"
  local temporary=""

  [[ -f "${ENVIRONMENT_FILE}" ]] || return 0
  temporary="$(mktemp "${ENVIRONMENT_FILE}.XXXXXX")"
  sed -E "s|^${key}=.*|#${key}=|" "${ENVIRONMENT_FILE}" > "${temporary}"
  chmod 600 "${temporary}"
  mv -f "${temporary}" "${ENVIRONMENT_FILE}"
}

enable_overlay() {
  local endpoint="${OVERLAY_ENDPOINT_HOST:-}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --endpoint)
        endpoint="${2:-}"
        shift 2
        ;;
      *) die "Unknown option: $1" ;;
    esac
  done

  if [[ -z "${endpoint}" ]]; then
    [[ -t 0 ]] ||
      die "The overlay needs an endpoint. Pass --endpoint HOST."
    printf 'The address probes dial to reach this host.\n'
    printf 'It has to be reachable when the ordinary path is not, so on a\n'
    printf 'site with an internal NATS address this is the public one.\n\n'
    read -r -p "Overlay endpoint [${NATS_FQDN}]: " endpoint
    endpoint="${endpoint:-${NATS_FQDN}}"
  fi
  mpp_validate_nats_host "${endpoint}" ||
    die "Not a usable endpoint address: ${endpoint}"
  # The one mistake that cannot be recovered from the far side: routing the
  # NATS address through a tunnel whose own endpoint is that address.
  [[ "${endpoint}" != "${NATS_HOST_IP}" ]] ||
    die "The endpoint is NATS_HOST_IP. The tunnel would have to carry its own
endpoint, and a probe switching over would lose both paths at once. Use the
address this host answers on from outside."
  validate_overlay_subnet "${OVERLAY_SUBNET}" ||
    die "Invalid OVERLAY_SUBNET: ${OVERLAY_SUBNET}"
  validate_overlay_mode "${OVERLAY_DEFAULT_MODE}" ||
    die "Invalid OVERLAY_DEFAULT_MODE: ${OVERLAY_DEFAULT_MODE}"

  set_environment_value OVERLAY_ENDPOINT_HOST "${endpoint}"
  set_environment_value COMPOSE_PROFILES overlay
  run_ops overlay init
  require_command docker
  docker compose --project-directory "${PROJECT_DIR}" up -d overlay

  printf '\nThe overlay is on. The hub answers on %s:%s/udp.\n' \
    "${endpoint}" "${OVERLAY_PORT}"
  printf 'Open that port, then put a probe on it:\n'
  printf '  sudo ./prtg-nats overlay add USER\n'
}

disable_overlay() {
  require_command docker
  # The peers keep their addresses and their keys. Taking the hub down is not
  # the same as retiring every probe from the overlay, and re-enabling should
  # not mean visiting each one again.
  docker compose --project-directory "${PROJECT_DIR}" stop overlay >/dev/null 2>&1 ||
    true
  docker compose --project-directory "${PROJECT_DIR}" rm -f overlay >/dev/null 2>&1 ||
    true
  remove_environment_value COMPOSE_PROFILES
  printf 'The overlay hub is stopped. Every probe keeps its address and key,\n'
  printf 'and still reaches this host the ordinary way.\n'
  printf 'Probes left in mode "on" reach NATS only through the tunnel - put\n'
  printf 'them back with "overlay mode USER auto" before you need them.\n'
}

command_name="${1:-}"
shift || true

case "${command_name}" in
  status)
    run_ops overlay status
    ;;
  enable)
    enable_overlay "$@"
    ;;
  disable)
    disable_overlay
    ;;
  add)
    require_probe_name "${1:-}"
    run_ops overlay add "$@"
    ;;
  remove)
    require_probe_name "${1:-}"
    run_ops overlay remove "$@"
    ;;
  mode)
    require_probe_name "${1:-}"
    [[ -n "${2:-}" ]] || die "This command needs a mode: off, auto or on"
    validate_overlay_mode "$2" || die "Unknown mode: $2"
    run_ops overlay mode "$@"
    ;;
  show)
    require_probe_name "${1:-}"
    run_ops overlay show "$@"
    ;;
  *)
    printf 'Unknown command: %s\n\n' "${command_name}" >&2
    usage >&2
    exit 2
    ;;
esac
