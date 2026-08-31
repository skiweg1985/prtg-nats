#!/usr/bin/env bash
#
# The overlay: a WireGuard tunnel between this host and the probes.
#
# Every verb here delegates to app.ops, including turning the overlay on. The
# settings live in the runtime and the hub is created through the Docker
# socket, so the interface and this script do the same thing rather than two
# things - and an administrator never has to edit a file on the host for it.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LIBEXEC_DIR="${SCRIPT_DIR}"
# PROJECT_DIR comes from common.sh below, which ops.sh then reads - so the
# order of the two source lines is not arbitrary.

usage() {
  cat <<'USAGE'
Usage: prtg-nats overlay COMMAND [ARGUMENTS]

  status                    the hub, its peers and what each one is doing
  enable HOST [OPTIONS]     turn the overlay on; HOST is what probes dial
                            Options: --port N --subnet CIDR
                                     --default-mode off|auto|on
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

command_name="${1:-}"
shift || true

case "${command_name}" in
  status)
    run_ops overlay status
    ;;
  enable)
    [[ -n "${1:-}" ]] ||
      die "This command needs the address probes dial to reach this host.
It has to be reachable when the ordinary path is not, so on a site with an
internal NATS address this is the public one."
    run_ops overlay enable "$@"
    ;;
  disable)
    run_ops overlay disable
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
