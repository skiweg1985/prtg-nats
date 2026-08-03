#!/usr/bin/env bash
#
# Every path below is read by the scripts that source this file, never here,
# so the unused-variable warning would fire on all of them.
# shellcheck disable=SC2034

# Where the installation keeps its state, and every path derived from it.
#
# Separate from common.sh because the commands that only need a path -
# ca-path, ca-show, status - must keep working on a machine that has no .env
# yet. Sourcing common.sh would make them require site settings they never
# read.
#
# The installation lives in the prtg-nats-runtime volume, not beside this
# checkout: see the header of compose.yaml. The directory the containers
# mount is the volume's mountpoint, so that is what this tooling works on. A
# host path next to the repository would be a second, empty installation that
# nothing serves.
#
# Reading the path from Docker rather than hard-coding /var/lib/docker keeps
# this correct under a relocated data-root. It is a root-only path either way,
# which is what these scripts already require.

RUNTIME_DIR_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR_PROJECT_DIR="$(cd -- "${RUNTIME_DIR_SCRIPT_DIR}/.." && pwd)"
RUNTIME_VOLUME="${PRTG_NATS_RUNTIME_VOLUME:-prtg-nats-runtime}"

# PRTG_NATS_RUNTIME_DIR wins over the lookup. The end-to-end test needs it: it
# drives prtg-nats from inside a container that talks to the host's Docker
# socket, where the host's mountpoint does not resolve.
resolve_runtime_dir() {
  local mountpoint=""

  if [[ -n "${PRTG_NATS_RUNTIME_DIR:-}" ]]; then
    printf '%s' "${PRTG_NATS_RUNTIME_DIR}"
    return 0
  fi
  mountpoint="$(
    docker volume inspect --format '{{.Mountpoint}}' "${RUNTIME_VOLUME}" \
      2>/dev/null || true
  )"
  if [[ -n "${mountpoint}" && -d "${mountpoint}" ]]; then
    printf '%s' "${mountpoint}"
    return 0
  fi
  # Before the first "compose up" the volume does not exist. Falling back to
  # the old location keeps the help and "config" usable on a machine that has
  # not been set up yet, instead of failing on a path nobody created.
  printf '%s' "${RUNTIME_DIR_PROJECT_DIR}/runtime"
}

RUNTIME_DIR="$(resolve_runtime_dir)"
CERT_DIR="${RUNTIME_DIR}/certs"
PRIVATE_DIR="${RUNTIME_DIR}/private"
CREDENTIAL_DIR="${RUNTIME_DIR}/credentials"
ARCHIVE_DIR="${RUNTIME_DIR}/archive"
PUBLIC_DIR="${RUNTIME_DIR}/public"
AUTH_USER_DIR="${RUNTIME_DIR}/auth-users"
PROBE_DIR="${RUNTIME_DIR}/probes"
IPERF_DIR="${RUNTIME_DIR}/iperf"
SSH_PRIVATE_DIR="${PRIVATE_DIR}/ssh"
SSH_KEY_PATH="${SSH_PRIVATE_DIR}/prtg-nats-mpp-admin"
SSH_KNOWN_HOSTS="${SSH_PRIVATE_DIR}/known_hosts"

# State left behind by an installation from before the volume. Worth saying
# once: the keys are still there, they are simply not the installation any
# more, and a CA key nobody accounts for is its own problem.
warn_about_legacy_runtime() {
  local legacy="${RUNTIME_DIR_PROJECT_DIR}/runtime"

  [[ "${RUNTIME_DIR}" != "${legacy}" ]] || return 0
  [[ -f "${legacy}/certs/ca.pem" ]] || return 0
  printf 'Note: %s holds state from before the runtime moved into the\n' \
    "${legacy}" >&2
  printf '%s volume. It is no longer read. Keep it somewhere safe or\n' \
    "${RUNTIME_VOLUME}" >&2
  printf 'remove it - it still contains the old CA key.\n' >&2
}
