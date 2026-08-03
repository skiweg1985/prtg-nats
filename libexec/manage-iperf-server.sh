#!/usr/bin/env bash

# Central management of the iperf3 measurement endpoints for the
# iperf-throughput sensor.
#
# The endpoint is set up over SSH, like a probe through "install-mpp" - with
# one difference, and it is the core of this script:
#
#   The password is created here and sent there, not the other way round.
#
# The endpoint stores only a SHA-256 of the credentials; a password created
# there could not be read back after the run and would have to be copied off
# the screen. Created centrally, it is still at hand afterwards - and can be
# rolled out to the probes without an intermediate step.
#
# An endpoint deliberately gets no permanent access from here: it only
# measures, it is not managed. Every writing run signs in anew.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage:
  ./prtg-nats iperf-server install ADMIN@HOST [--name NAME] [--user NAME]
                               [--port PORT] [--rotate] [--dry-run]
  ./prtg-nats iperf-server list
  ./prtg-nats iperf-server show NAME
  ./prtg-nats iperf-server deploy NAME USER|--all [--dry-run]
  ./prtg-nats iperf-server revoke NAME USER|--all
  ./prtg-nats iperf-server forget NAME [--yes]

install sets up an authenticated iperf3 endpoint over SSH. The password is
generated here and sent there, so that "deploy" can hand it to the probes
afterwards -- on the endpoint itself only its SHA-256 is kept.
EOF
}

# Help before the environment is loaded: otherwise it is unreachable without
# a configured .env - which is exactly when it is needed most.
case "${1:-}" in
  ''|-h|--help|help)
    usage
    exit 0
    ;;
esac
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require_command ssh

SENSOR_SOURCE_DIR="${PROJECT_DIR}/sensors"
IPERF_KIND="iperf3"
# The script that runs on the endpoint. It lives with the sensor, not in
# libexec/, because it can also be fetched from there by hand - the manual
# path in sensors/iperf-throughput/README.md uses the same file.
SETUP_SCRIPT="${SENSOR_SOURCE_DIR}/iperf-throughput/endpoint/setup-iperf3-endpoint.sh"
# Where the endpoint stores its public key. The same path is in the setup
# script; here it is only read.
REMOTE_PUBLIC_KEY="/etc/iperf3/public.pem"

DRY_RUN="false"
SSH_CONTROL_DIR=""
SSH_CONTROL_PATH=""
SSH_TARGET=""
REMOTE_STAGE=""

require_username() {
  local username="${1:-}"
  [[ -n "${username}" ]] || die "A NATS username is required"
  validate_nats_username "${username}" || die "Invalid NATS username"
}

require_iperf_name() {
  local name="${1:-}"
  [[ -n "${name}" ]] || die "An iperf endpoint name is required"
  validate_iperf_name "${name}" || die "Invalid iperf endpoint name: ${name}"
}

require_registered_iperf() {
  local name="$1"

  require_iperf_name "${name}"
  [[ -f "$(iperf_path "${name}")" ]] ||
    die "Unknown iperf endpoint: ${name}. Set one up with \"./prtg-nats iperf-server install ADMIN@HOST\""
}

# Which sensors work with endpoints of this kind. The answer lives in the
# manifest, not here: a second sensor against the same kind of far end should
# be served without changing this script.
iperf_sensors() {
  local manifest=""
  local name=""

  shopt -s nullglob
  for manifest in "${SENSOR_SOURCE_DIR}"/*/manifest.env; do
    [[ "$(read_optional_env_value "${manifest}" SENSOR_IPERF)" == \
       "${IPERF_KIND}" ]] || continue
    name="${manifest%/manifest.env}"
    printf '%s\n' "${name##*/}"
  done
  shopt -u nullglob
}

# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------

assigned_iperf_servers() {
  local username="$1"
  local assignment=""

  assignment="$(probe_iperf_path "${username}")"
  [[ -f "${assignment}" ]] || return 0
  grep -E -v '^[[:space:]]*(#.*)?$' "${assignment}" || true
}

remember_iperf() {
  local username="$1"
  local name="$2"
  local assignment=""

  assignment="$(probe_iperf_path "${username}")"
  touch "${assignment}"
  chmod 600 "${assignment}"
  grep -q -x -- "${name}" "${assignment}" ||
    printf '%s\n' "${name}" >> "${assignment}"
}

forget_iperf_assignment() {
  local username="$1"
  local name="$2"
  local assignment=""
  local remaining=""

  assignment="$(probe_iperf_path "${username}")"
  [[ -f "${assignment}" ]] || return 0
  remaining="$(grep -v -x -- "${name}" "${assignment}" || true)"
  if [[ -n "${remaining}" ]]; then
    printf '%s\n' "${remaining}" > "${assignment}"
    chmod 600 "${assignment}"
  else
    rm -f -- "${assignment}"
  fi
}

probes_with_iperf() {
  local name="$1"
  local inventory=""
  local username=""

  shopt -s nullglob
  for inventory in "${PROBE_DIR}"/*.env; do
    username="$(basename -- "${inventory}" .env)"
    assigned_iperf_servers "${username}" | grep -q -x -- "${name}" &&
      printf '%s\n' "${username}"
  done
  shopt -u nullglob
  return 0
}

enrolled_probes() {
  local inventory=""

  shopt -s nullglob
  for inventory in "${PROBE_DIR}"/*.env; do
    basename -- "${inventory}" .env
  done
  shopt -u nullglob
}

# ---------------------------------------------------------------------------
# SSH to the endpoint
# ---------------------------------------------------------------------------

cleanup_session() {
  if [[ "${REMOTE_STAGE}" =~ ^/tmp/prtg-iperf-setup\.[A-Za-z0-9]+$ &&
        -S "${SSH_CONTROL_PATH}" ]]; then
    # The password file lives in the staging directory. It disappears with
    # it, even if the run aborts halfway.
    ssh \
      -S "${SSH_CONTROL_PATH}" \
      -o BatchMode=yes \
      -- "${SSH_TARGET}" \
      "rm -rf -- '${REMOTE_STAGE}'" >/dev/null 2>&1 || true
    REMOTE_STAGE=""
  fi
  if [[ -n "${SSH_CONTROL_PATH}" && -S "${SSH_CONTROL_PATH}" ]]; then
    ssh \
      -S "${SSH_CONTROL_PATH}" \
      -O exit \
      -- "${SSH_TARGET}" >/dev/null 2>&1 || true
  fi
  if [[ "${SSH_CONTROL_DIR}" =~ ^/tmp/prtg-nats-ssh\.[A-Za-z0-9]+$ ]]; then
    rm -rf -- "${SSH_CONTROL_DIR}"
    SSH_CONTROL_DIR=""
    SSH_CONTROL_PATH=""
  fi
}

# One single session for the whole run: password or passphrase are prompted
# for at most once, and copying and executing both travel over it.
open_session() {
  local ssh_target="$1"

  SSH_CONTROL_DIR="$(mktemp -d /tmp/prtg-nats-ssh.XXXXXX)"
  chmod 0700 "${SSH_CONTROL_DIR}"
  SSH_CONTROL_PATH="${SSH_CONTROL_DIR}/master"

  printf 'Opening one SSH session to %s...\n' "${ssh_target}"
  printf 'Authenticate with an existing SSH key or the account password.\n'
  ssh \
    -M \
    -S "${SSH_CONTROL_PATH}" \
    -o ControlMaster=yes \
    -o ControlPersist=no \
    -o BatchMode=no \
    -N \
    -f \
    -- "${ssh_target}"
  [[ -S "${SSH_CONTROL_PATH}" ]] ||
    die "The SSH control session to ${ssh_target} was not created"
}

session_run() {
  ssh \
    -S "${SSH_CONTROL_PATH}" \
    -o BatchMode=yes \
    -- "${SSH_TARGET}" \
    "$@"
}

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

# The default name of an endpoint is the short form of its host name. That
# is the name its credentials later carry on every probe, so it should be
# recognisable, not numbered through.
default_iperf_name() {
  local ssh_target="$1"
  local host="${ssh_target#*@}"

  mpp_host_label "${host}"
}

install_iperf() {
  local ssh_target=""
  local name=""
  local iperf_user="prtg-probe"
  local port="5201"
  local rotate="false"
  local password=""
  local existing=""
  local public_key=""
  local setup_options=()
  local remote_command=""
  local option=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name)
        [[ $# -ge 2 ]] || die "--name requires a value"
        name="$2"
        shift 2
        ;;
      --user)
        [[ $# -ge 2 ]] || die "--user requires a value"
        iperf_user="$2"
        shift 2
        ;;
      --port)
        [[ $# -ge 2 ]] || die "--port requires a value"
        port="$2"
        shift 2
        ;;
      --rotate)
        rotate="true"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      -*)
        die "Unsupported option: $1"
        ;;
      *)
        [[ -z "${ssh_target}" ]] ||
          die "Only one SSH target is supported: $1"
        ssh_target="$1"
        shift
        ;;
    esac
  done

  [[ -n "${ssh_target}" ]] ||
    die "Usage: ./prtg-nats iperf-server install ADMIN@HOST [options]"
  [[ "${ssh_target}" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9][A-Za-z0-9.-]*$ ]] ||
    die "Invalid SSH target: ${ssh_target}. Expected ADMIN@HOST"
  [[ "${iperf_user}" =~ ^[A-Za-z0-9_-]+$ ]] ||
    die "Invalid iperf user name: ${iperf_user}"
  [[ "${port}" =~ ^[0-9]{1,5}$ ]] && ((port >= 1 && port <= 65535)) ||
    die "Invalid port: ${port}"
  [[ -f "${SETUP_SCRIPT}" ]] ||
    die "The endpoint setup script is missing: ${SETUP_SCRIPT}"
  [[ -n "${name}" ]] || name="$(default_iperf_name "${ssh_target}")"
  require_iperf_name "${name}"

  existing="$(iperf_path "${name}")"
  if [[ -f "${existing}" && "${rotate}" != "true" ]]; then
    # A second run sets the endpoint to the state recorded here instead of
    # inventing a new password. Otherwise every already-served probe would
    # lose its access on every update.
    password="$(read_env_value "${existing}" IPERF_PASSWORD)" ||
      die "The stored record of ${name} has no password: ${existing}"
    printf 'Reusing the password stored for %s.\n' "${name}"
  elif [[ -f "${existing}" ]]; then
    password="$(openssl rand -hex 24)"
    printf 'Generating a new password for %s.\n' "${name}"
  else
    password="$(openssl rand -hex 24)"
    printf 'Generating a password for the new endpoint %s.\n' "${name}"
  fi

  setup_options=(--user "${iperf_user}" --port "${port}")
  # Afterwards the endpoint has to hold exactly the password recorded here.
  # Without this switch it would keep an existing one and the two sides would
  # drift apart without anyone noticing. The key pair stays untouched -
  # replacing it costs every probe its access.
  [[ ! -f "${existing}" ]] || setup_options+=(--force-credentials)
  [[ "${rotate}" != "true" ]] || setup_options+=(--force-credentials)

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '\nDry run, nothing was changed:\n'
    printf '  iperf endpoint    %s\n' "${name}"
    printf '  SSH target        %s\n' "${ssh_target}"
    printf '  listening on      %s:%s\n' "${ssh_target#*@}" "${port}"
    printf '  authenticates as  %s\n' "${iperf_user}"
    printf '  password          %s\n' \
      "$([[ -f "${existing}" && "${rotate}" != "true" ]] &&
        printf 'the stored one' || printf 'a newly generated one')"
    printf '  record            %s\n' "$(iperf_path "${name}")"
    return 0
  fi

  require_command scp
  require_command openssl
  require_command mktemp
  SSH_TARGET="${ssh_target}"
  trap cleanup_session EXIT

  open_session "${ssh_target}"

  REMOTE_STAGE="$(
    session_run 'umask 077; mktemp -d /tmp/prtg-iperf-setup.XXXXXX'
  )"
  [[ "${REMOTE_STAGE}" =~ ^/tmp/prtg-iperf-setup\.[A-Za-z0-9]+$ ]] ||
    die "Unexpected remote staging path: ${REMOTE_STAGE}"

  printf 'Copying the setup script to %s...\n' "${ssh_target}"
  scp \
    -q \
    -o "ControlPath=${SSH_CONTROL_PATH}" \
    -- \
    "${SETUP_SCRIPT}" \
    "${ssh_target}:${REMOTE_STAGE}/setup-iperf3-endpoint.sh"
  # The password travels as a file over the existing session, not as an
  # argument: an argument would appear in the endpoint's process list and in
  # the history of every shell involved.
  printf '%s\n' "${password}" |
    session_run "umask 077; cat > '${REMOTE_STAGE}/password'"

  printf -v remote_command \
    'bash %q --password-file %q' \
    "${REMOTE_STAGE}/setup-iperf3-endpoint.sh" \
    "${REMOTE_STAGE}/password"
  for option in "${setup_options[@]}"; do
    printf -v remote_command '%s %q' "${remote_command}" "${option}"
  done

  printf 'Setting up the iperf3 endpoint on %s...\n\n' "${ssh_target}"
  # With a terminal: the script elevates itself through sudo when needed,
  # and sudo then prompts for the password.
  ssh \
    -S "${SSH_CONTROL_PATH}" \
    -t \
    -- "${ssh_target}" \
    "${remote_command}" ||
    die "The endpoint setup on ${ssh_target} failed; nothing was recorded here"

  public_key="$(session_run "cat '${REMOTE_PUBLIC_KEY}'")" ||
    die "Could not read the public key ${REMOTE_PUBLIC_KEY} from ${ssh_target}"
  printf '%s\n' "${public_key}" | grep -q -- '-----BEGIN PUBLIC KEY-----' ||
    die "The endpoint did not return a usable public key"

  write_iperf_record \
    "${name}" "${ssh_target#*@}" "${port}" "${iperf_user}" "${password}"
  printf '%s\n' "${public_key}" > "$(iperf_key_path "${name}")"
  chmod 600 "$(iperf_key_path "${name}")"

  cleanup_session
  trap - EXIT

  printf '\nEndpoint %s is set up and its credentials are stored here.\n' \
    "${name}"
  report_next_steps "${name}" "${ssh_target#*@}" "${port}" "${iperf_user}"

  # After a password change, every already-served probe is locked out.
  # Updating them right away is not a convenience but the repair of the state
  # the change just created.
  [[ "${rotate}" == "true" ]] || return 0
  redeploy_after_rotation "${name}"
}

write_iperf_record() {
  local name="$1"
  local host="$2"
  local port="$3"
  local iperf_user="$4"
  local password="$5"
  local record=""

  record="$(iperf_path "${name}")"
  {
    printf 'IPERF_NAME=%s\n' "${name}"
    printf 'IPERF_KIND=%s\n' "${IPERF_KIND}"
    printf 'IPERF_HOST=%s\n' "${host}"
    printf 'IPERF_PORT=%s\n' "${port}"
    printf 'IPERF_USERNAME=%s\n' "${iperf_user}"
    printf 'IPERF_PASSWORD=%s\n' "${password}"
    printf 'IPERF_UPDATED=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "${record}"
  chmod 600 "${record}"
}

report_next_steps() {
  local name="$1"
  local host="$2"
  local port="$3"
  local iperf_user="$4"
  local sensor=""

  sensor="$(iperf_sensors | head -1)"
  cat <<EOF

Next:
  1. Open ${port}/tcp *and* ${port}/udp on the way to ${host}.
     The sensor measures with both; only TCP looks like a broken line.
  2. Roll the credentials out to the probes:
       ./prtg-nats iperf-server deploy ${name} --all
     "./prtg-nats sensor deploy ${sensor:-iperf-throughput} USER" does that
     by itself, so a probe that still needs the sensor needs nothing else.
  3. Sensor parameters in PRTG. Without a target rate the sensor measures
     what the path carries -- start there, then set a rate once you know:
       --server ${host} --port ${port} --username ${iperf_user}

The password is stored under $(iperf_path "${name}") and is not printed.
EOF
}

redeploy_after_rotation() {
  local name="$1"
  local probes=()
  local username=""

  mapfile -t probes < <(probes_with_iperf "${name}")
  [[ "${#probes[@]}" -gt 0 ]] || return 0
  printf '\nThe password changed; refreshing the probes that already use %s.\n' \
    "${name}"
  for username in "${probes[@]}"; do
    (deploy_iperf "${name}" "${username}") ||
      printf 'WARNING: %s still has the old password and cannot measure until\n         "./prtg-nats iperf-server deploy %s %s" succeeds.\n' \
        "${username}" "${name}" "${username}" >&2
  done
}

# ---------------------------------------------------------------------------
# deploy / revoke
# ---------------------------------------------------------------------------

# Which of the endpoint sensors are really installed on this probe. The
# probe itself is asked, not the assignment here: depositing credentials for
# a sensor that is not there would leave behind a file nobody reads.
installed_iperf_sensors() {
  local username="$1"
  local reported=""
  local sensor=""

  reported="$(printf 'sensor-list\n' | managed_ssh "${username}")" || return 1
  while read -r sensor; do
    printf '%s\n' "${reported}" | grep -q -E "^${sensor}"$'\t' &&
      printf '%s\n' "${sensor}"
  done < <(iperf_sensors)
  return 0
}

# An endpoint's record becomes a sensor profile.
#
# The way onto the probe is the one the repository already has: "sensor
# profile" stores it centrally under runtime/sensor-profiles/ and writes it
# over the restricted channel. Building a second path for the same thing
# would mean maintaining two places where credentials reach a probe.
#
# The sensor expects the format like this: KEY=VALUE, the key base64 on one
# line, because a PEM with its line breaks does not fit into such a file.
render_iperf_profile() {
  local name="$1"
  local record=""

  record="$(iperf_path "${name}")"
  printf '# Generated by "./prtg-nats iperf-server deploy". Do not edit by hand.\n'
  printf '# Endpoint %s on %s:%s\n' \
    "${name}" \
    "$(read_optional_env_value "${record}" IPERF_HOST)" \
    "$(read_optional_env_value "${record}" IPERF_PORT)"
  printf 'IPERF3_PASSWORD=%s\n' "$(read_env_value "${record}" IPERF_PASSWORD)"
  printf 'IPERF3_PUBLIC_KEY_B64=%s\n' \
    "$(base64 < "$(iperf_key_path "${name}")" | tr -d '\n')"
}

deploy_iperf() {
  local name="$1"
  local username="$2"
  local sensor=""
  local sensors=()
  local installed=()
  local profile=""
  local only_one="false"

  [[ -f "$(probe_path "${username}")" ]] ||
    die "Probe is not enrolled: ${username}"
  [[ -f "$(iperf_key_path "${name}")" ]] ||
    die "The public key of ${name} is missing: $(iperf_key_path "${name}")"
  require_command base64

  mapfile -t sensors < <(iperf_sensors)
  [[ "${#sensors[@]}" -gt 0 ]] ||
    die "No sensor in sensors/ declares SENSOR_IPERF=${IPERF_KIND}"
  # As long as there is exactly one endpoint, its profile is additionally
  # called "default" - the name the sensor looks for without --profile.
  # Nobody should have to enter a name while there is nothing to tell apart.
  [[ "$(registered_iperf_servers | wc -l | tr -d ' ')" != "1" ]] ||
    only_one="true"

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf 'Would deploy the credentials of %s to %s as profile %s' \
      "${name}" "${username}" "${name}"
    [[ "${only_one}" != "true" ]] || printf ' (and as "default")'
    printf ' for: %s\n' "${sensors[*]}"
    return 0
  fi

  mapfile -t installed < <(installed_iperf_sensors "${username}") ||
    die "The restricted management connection to ${username} is unavailable"
  [[ "${#installed[@]}" -gt 0 ]] ||
    die "None of the sensors ${sensors[*]} is installed on ${username}; run \"./prtg-nats sensor deploy ${sensors[0]} ${username}\" first"

  profile="$(mktemp)"
  # The password is in this file; it disappears again in any case.
  trap 'rm -f -- "${profile}"' RETURN
  chmod 600 "${profile}"
  render_iperf_profile "${name}" > "${profile}"

  for sensor in "${installed[@]}"; do
    "${SCRIPT_DIR}/manage-sensors.sh" profile \
      "${sensor}" "${username}" "${name}" --from-file "${profile}" >/dev/null ||
      die "The probe rejected the credentials of ${name} for ${sensor}"
    if [[ "${only_one}" == "true" ]]; then
      "${SCRIPT_DIR}/manage-sensors.sh" profile \
        "${sensor}" "${username}" default --from-file "${profile}" >/dev/null ||
        die "The probe rejected the default profile of ${name} for ${sensor}"
    fi
    printf 'Deployed the credentials of %s to %s for sensor %s' \
      "${name}" "${username}" "${sensor}"
    if [[ "${only_one}" == "true" ]]; then
      printf ' (profile %s, and as "default" so no --profile is needed).\n' \
        "${name}"
    else
      printf ' (profile %s; the sensor needs "--profile %s").\n' \
        "${name}" "${name}"
    fi
  done
  remember_iperf "${username}" "${name}"
}

# The subshell is load-bearing here: deploy_iperf aborts with "die", and
# without it a single probe without the sensor would end the run across the
# whole fleet. Such a probe is not a failure, though - it simply does not
# need this endpoint.
deploy_to_all() {
  local name="$1"
  local username=""
  local probes=()
  local failures=0

  mapfile -t probes < <(enrolled_probes)
  [[ "${#probes[@]}" -gt 0 ]] ||
    {
      printf 'No probes are enrolled.\n'
      return 0
    }
  for username in "${probes[@]}"; do
    if ! (deploy_iperf "${name}" "${username}"); then
      failures=$((failures + 1))
    fi
  done
  [[ "${failures}" -eq 0 ]] ||
    printf '\n%s probe(s) were skipped; see the messages above.\n' \
      "${failures}" >&2
}

# Counterpart to deploy, over the same path. The "default" profile goes
# along if it was this one - otherwise a sensor without --profile would be
# left with credentials nobody can attribute any more.
revoke_iperf() {
  local name="$1"
  local username="$2"
  local sensor=""

  [[ -f "$(probe_path "${username}")" ]] ||
    die "Probe is not enrolled: ${username}"
  while read -r sensor; do
    "${SCRIPT_DIR}/manage-sensors.sh" profile \
      "${sensor}" "${username}" "${name}" --remove >/dev/null 2>&1 || true
    [[ "$(registered_iperf_servers | wc -l | tr -d ' ')" != "1" ]] ||
      "${SCRIPT_DIR}/manage-sensors.sh" profile \
        "${sensor}" "${username}" default --remove >/dev/null 2>&1 || true
  done < <(iperf_sensors)
  forget_iperf_assignment "${username}" "${name}"
  printf 'Removed the credentials of %s from %s.\n' "${name}" "${username}"
}

revoke_from_all() {
  local name="$1"
  local username=""

  for username in $(enrolled_probes); do
    (revoke_iperf "${name}" "${username}") || true
  done
}

# ---------------------------------------------------------------------------
# list / show / forget
# ---------------------------------------------------------------------------

list_iperf_servers() {
  local record=""
  local name=""
  local deployed_to=""

  shopt -s nullglob
  set -- "${IPERF_DIR}"/*.env
  shopt -u nullglob
  [[ $# -gt 0 ]] ||
    {
      printf 'No iperf endpoint is registered.\n\n'
      printf 'Set one up with:\n'
      printf '  ./prtg-nats iperf-server install ADMIN@HOST\n'
      return 0
    }

  printf '%-20s %-30s %-14s %s\n' 'IPERF' 'HOST:PORT' 'USER' 'PROBES'
  for record in "$@"; do
    name="$(basename -- "${record}" .env)"
    deployed_to="$(probes_with_iperf "${name}" | paste -s -d ',' - || true)"
    printf '%-20s %-30s %-14s %s\n' \
      "${name}" \
      "$(read_optional_env_value "${record}" IPERF_HOST):$(
        read_optional_env_value "${record}" IPERF_PORT)" \
      "$(read_optional_env_value "${record}" IPERF_USERNAME)" \
      "${deployed_to:-—}"
  done
}

show_iperf() {
  local name="$1"
  local record=""
  local deployed_to=""

  record="$(iperf_path "${name}")"
  printf 'iperf:        %s\n' "${name}"
  printf 'Kind:         %s\n' \
    "$(read_optional_env_value "${record}" IPERF_KIND)"
  printf 'Host:         %s\n' \
    "$(read_optional_env_value "${record}" IPERF_HOST)"
  printf 'Port:         %s (open it for TCP and UDP)\n' \
    "$(read_optional_env_value "${record}" IPERF_PORT)"
  printf 'User:         %s\n' \
    "$(read_optional_env_value "${record}" IPERF_USERNAME)"
  printf 'Updated:      %s\n' \
    "$(read_optional_env_value "${record}" IPERF_UPDATED)"
  printf 'Record:       %s\n' "${record}"
  printf 'Public key:   %s\n' "$(iperf_key_path "${name}")"
  printf '\nSensor parameters in PRTG (add --download-mbit/--upload-mbit to\n'
  printf 'turn the measurement into an assurance):\n'
  printf '  --server %s --port %s --username %s\n' \
    "$(read_optional_env_value "${record}" IPERF_HOST)" \
    "$(read_optional_env_value "${record}" IPERF_PORT)" \
    "$(read_optional_env_value "${record}" IPERF_USERNAME)"
  printf '\nDeployed to:\n'
  deployed_to="$(probes_with_iperf "${name}")"
  if [[ -n "${deployed_to}" ]]; then
    printf '%s\n' "${deployed_to}" | sed 's/^/  /'
  else
    printf '  none yet — "./prtg-nats iperf-server deploy %s --all"\n' "${name}"
  fi
  printf '\nThe password is deliberately not printed.\n'
}

forget_iperf_record() {
  local name="$1"
  local confirmed="$2"
  local deployed_to=""
  local answer=""

  deployed_to="$(probes_with_iperf "${name}")"
  if [[ -n "${deployed_to}" && "${confirmed}" != "true" ]]; then
    printf 'These probes still carry the credentials of %s:\n' "${name}" >&2
    printf '%s\n' "${deployed_to}" | sed 's/^/  /' >&2
    printf '\nRemove them there first:\n' >&2
    printf '  ./prtg-nats iperf-server revoke %s --all\n' "${name}" >&2
    printf 'Or repeat with --yes to drop the record anyway.\n' >&2
    return 1
  fi
  if [[ "${confirmed}" != "true" ]]; then
    [[ -t 0 ]] ||
      die "No terminal for the confirmation; repeat with --yes"
    read -r -p "Forget the iperf endpoint ${name}? [y/N]: " answer
    is_affirmative "${answer}" ||
      {
        printf 'Nothing was changed.\n'
        return 0
      }
  fi
  rm -f -- "$(iperf_path "${name}")" "$(iperf_key_path "${name}")"
  printf 'Forgot the iperf endpoint %s.\n' "${name}"
  printf 'The iperf3 service on that host keeps running; stop it there if it\n'
  printf 'is no longer wanted:  systemctl disable --now iperf3.service\n'
}

create_runtime_directories
ensure_management_ssh_key

command_name="${1:-}"
[[ -n "${command_name}" ]] || {
  usage
  exit 2
}
shift

case "${command_name}" in
  install)
    install_iperf "$@"
    ;;
  list)
    [[ $# -eq 0 ]] || die "Usage: ./prtg-nats iperf-server list"
    list_iperf_servers
    ;;
  show)
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats iperf-server show NAME"
    require_registered_iperf "$1"
    show_iperf "$1"
    ;;
  deploy)
    [[ $# -ge 2 ]] ||
      die "Usage: ./prtg-nats iperf-server deploy NAME USER|--all [--dry-run]"
    iperf_name="$1"
    target="$2"
    shift 2
    if [[ "${1:-}" == "--dry-run" ]]; then
      DRY_RUN="true"
      shift
    fi
    [[ $# -eq 0 ]] ||
      die "Usage: ./prtg-nats iperf-server deploy NAME USER|--all [--dry-run]"
    require_registered_iperf "${iperf_name}"
    if [[ "${target}" == "--all" ]]; then
      deploy_to_all "${iperf_name}"
    else
      require_username "${target}"
      deploy_iperf "${iperf_name}" "${target}"
    fi
    ;;
  revoke)
    [[ $# -eq 2 ]] ||
      die "Usage: ./prtg-nats iperf-server revoke NAME USER|--all"
    require_iperf_name "$1"
    if [[ "$2" == "--all" ]]; then
      revoke_from_all "$1"
    else
      require_username "$2"
      revoke_iperf "$1" "$2"
    fi
    ;;
  forget)
    [[ $# -ge 1 && $# -le 2 ]] ||
      die "Usage: ./prtg-nats iperf-server forget NAME [--yes]"
    require_registered_iperf "$1"
    confirmed="false"
    [[ "${2:-}" != "--yes" ]] || confirmed="true"
    [[ -z "${2:-}" || "${2}" == "--yes" ]] ||
      die "Usage: ./prtg-nats iperf-server forget NAME [--yes]"
    forget_iperf_record "$1" "${confirmed}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
