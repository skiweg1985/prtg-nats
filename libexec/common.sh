#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
MPP_CA_PATH="/etc/paessler/mpprobe/certs/nats-docker-ca.pem"
MPP_DEFAULT_CLIENT_NAME="prtgmpprobe"

# RUNTIME_DIR and everything derived from it. Its own file so that the
# path-only commands can source it without requiring the site settings below.
# shellcheck source=runtime-dir.sh
source "${SCRIPT_DIR}/runtime-dir.sh"

# shellcheck source=mpp-config.sh
source "${SCRIPT_DIR}/mpp-config.sh"

if [[ -f "${PROJECT_DIR}/.env" ]]; then
  while IFS='=' read -r env_key env_value; do
    case "${env_key}" in
      NATS_FQDN|NATS_PORT|NATS_HOST_IP|CA_HTTP_PORT|PRTG_CORE_IP|MPP_SSH_SOURCE_CIDR|CA_ORGANIZATION)
        if [[ -z "${!env_key+x}" ]]; then
          printf -v "${env_key}" '%s' "${env_value}"
        fi
        ;;
    esac
  done < "${PROJECT_DIR}/.env"
fi

# Site-specific values deliberately have no default: a built-in hostname or
# address would be wrong for every other installation and would only show up
# late. "./prtg-nats config --edit" writes them.
NATS_PORT="${NATS_PORT:-23561}"
CA_HTTP_PORT="${CA_HTTP_PORT:-80}"
CA_ORGANIZATION="${CA_ORGANIZATION:-PRTG NATS}"
if ! [[ "${NATS_PORT}" =~ ^[0-9]{1,5}$ ]] ||
  ((NATS_PORT < 1 || NATS_PORT > 65535)); then
  printf 'Invalid NATS_PORT: %s\n' "${NATS_PORT}" >&2
  exit 1
fi

require_configured_value() {
  local variable_name="$1"
  local description="$2"

  if [[ -z "${!variable_name:-}" ]]; then
    printf 'Required value is missing: %s (%s)\n' \
      "${variable_name}" "${description}" >&2
    printf 'Run "sudo ./prtg-nats setup".\n' >&2
    exit 1
  fi
}

require_configured_value NATS_FQDN 'FQDN of the NATS server'
require_configured_value NATS_HOST_IP 'IP address for the container ports'

MPP_SSH_SOURCE_CIDR="${MPP_SSH_SOURCE_CIDR:-${NATS_HOST_IP}/32}"
NATS_USERNAME="${NATS_USERNAME:-prtg-nats}"
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "${command_name}" >&2
    exit 1
  fi
}

create_runtime_directories() {
  umask 077
  mkdir -p \
    "${CERT_DIR}" \
    "${PRIVATE_DIR}" \
    "${CREDENTIAL_DIR}" \
    "${ARCHIVE_DIR}" \
    "${PUBLIC_DIR}" \
    "${AUTH_USER_DIR}" \
    "${PROBE_DIR}" \
    "${IPERF_DIR}" \
    "${SSH_PRIVATE_DIR}"
  chmod 700 \
    "${RUNTIME_DIR}" \
    "${CERT_DIR}" \
    "${PRIVATE_DIR}" \
    "${CREDENTIAL_DIR}" \
    "${ARCHIVE_DIR}" \
    "${AUTH_USER_DIR}" \
    "${PROBE_DIR}" \
    "${IPERF_DIR}" \
    "${SSH_PRIVATE_DIR}"
  chmod 755 "${PUBLIC_DIR}"
}

validate_nats_username() {
  local username="$1"
  [[ "${username}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

is_affirmative() {
  local normalized=""

  normalized="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "${normalized}" in
    y|yes|j|ja)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

validate_ssh_source_cidr() {
  local source_cidr="$1"
  local address="${source_cidr%/*}"
  local prefix="${source_cidr##*/}"

  [[ "${source_cidr}" =~ ^[A-Fa-f0-9:.]+/[0-9]{1,3}$ ]] || return 1
  if [[ "${address}" == *:* ]]; then
    ((prefix >= 0 && prefix <= 128))
  else
    [[ "${address}" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] &&
      ((prefix >= 0 && prefix <= 32))
  fi
}

credential_path() {
  local username="$1"
  printf '%s/%s.env\n' "${CREDENTIAL_DIR}" "${username}"
}

auth_user_path() {
  local username="$1"
  printf '%s/%s.auth\n' "${AUTH_USER_DIR}" "${username}"
}

probe_path() {
  local username="$1"
  printf '%s/%s.env\n' "${PROBE_DIR}" "${username}"
}

# The sensors assigned to a probe. Deliberately beside the inventory rather
# than inside it: the inventory is rewritten at every transaction step, and the
# assignment must not be affected by that.
probe_sensors_path() {
  local username="$1"
  printf '%s/%s.sensors\n' "${PROBE_DIR}" "${username}"
}

# Which measurement endpoints a probe knows. Beside the inventory for the same
# reason as the sensor assignment - and because a password rotation can read
# from it who needs new credentials, without asking every probe in turn.
probe_iperf_path() {
  local username="$1"
  printf '%s/%s.iperf\n' "${PROBE_DIR}" "${username}"
}

# Measurement endpoints carry a name of their own rather than a NATS account:
# they are not a probe but a counterpart to measure against. The character
# class is the same as for sensor and profile names - the name becomes a
# directory on every probe and has to pass the same check in the helper.
validate_iperf_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

# The credentials of a measurement endpoint. They live in the same protected
# area as the NATS passwords: the password is created here and handed out from
# here, because the endpoint itself only keeps its SHA-256.
iperf_path() {
  local name="$1"
  printf '%s/%s.env\n' "${IPERF_DIR}" "${name}"
}

# The public key of the endpoint. It is not a secret but belongs to the same
# record: without it a probe cannot encrypt the credentials, and iperf3 then
# refuses the login.
iperf_key_path() {
  local name="$1"
  printf '%s/%s.pem\n' "${IPERF_DIR}" "${name}"
}

registered_iperf_servers() {
  local inventory=""

  shopt -s nullglob
  for inventory in "${IPERF_DIR}"/*.env; do
    basename -- "${inventory}" .env
  done
  shopt -u nullglob
}

# One interactive SSH session that every later step of a rollout borrows. It
# exists because the bootstrap login is the one place where a password may be
# asked for: without the shared master every scp and every ssh would ask
# again, and an operator typing the same password five times stops reading
# what it is for.
#
# BatchMode=no is the whole point here - the callers all run with
# BatchMode=yes and would never get a prompt of their own.
BOOTSTRAP_CONTROL_DIR="${BOOTSTRAP_CONTROL_DIR:-}"
BOOTSTRAP_CONTROL_PATH="${BOOTSTRAP_CONTROL_PATH:-}"

open_bootstrap_control_session() {
  local ssh_target="$1"

  BOOTSTRAP_CONTROL_DIR="$(mktemp -d /tmp/prtg-nats-ssh.XXXXXX)"
  chmod 0700 "${BOOTSTRAP_CONTROL_DIR}"
  BOOTSTRAP_CONTROL_PATH="${BOOTSTRAP_CONTROL_DIR}/master"

  printf 'Establishing one bootstrap SSH session to %s...\n' "${ssh_target}"
  printf 'Authenticate with an existing SSH key or the bootstrap user password.\n'
  ssh \
    -M \
    -S "${BOOTSTRAP_CONTROL_PATH}" \
    -o ControlMaster=yes \
    -o ControlPersist=no \
    -o BatchMode=no \
    -N \
    -f \
    -- "${ssh_target}"
  [[ -S "${BOOTSTRAP_CONTROL_PATH}" ]] ||
    {
      printf 'Bootstrap SSH control session was not created.\n' >&2
      return 1
    }
}

# Fault tolerant on purpose: this runs from EXIT traps, where a failure would
# replace the error the operator actually needs to see.
close_bootstrap_control_session() {
  local ssh_target="$1"

  if [[ -n "${BOOTSTRAP_CONTROL_PATH}" && -S "${BOOTSTRAP_CONTROL_PATH}" ]]; then
    ssh \
      -S "${BOOTSTRAP_CONTROL_PATH}" \
      -O exit \
      -- "${ssh_target}" >/dev/null 2>&1 || true
  fi
  if [[ "${BOOTSTRAP_CONTROL_DIR}" =~ ^/tmp/prtg-nats-ssh\.[A-Za-z0-9]+$ ]]; then
    rm -rf -- "${BOOTSTRAP_CONTROL_DIR}"
  fi
  BOOTSTRAP_CONTROL_DIR=""
  BOOTSTRAP_CONTROL_PATH=""
}

# The restricted management channel to an enrolled probe. On the far side sits
# a forced command that only accepts the known requests.
#
# -T: the forced command never needs a terminal. Without the option ssh asks
# for one and warns on every call about the pipe on stdin.
#
# ServerAlive*: ConnectTimeout only covers establishing the connection. If a
# host stops answering afterwards the call would hang indefinitely, and one
# dead probe would block an overview of the whole fleet.
managed_ssh() {
  local username="$1"
  local inventory=""
  local host=""

  inventory="$(probe_path "${username}")"
  [[ -f "${inventory}" ]] || die "Probe is not enrolled for user: ${username}"
  host="$(read_env_value "${inventory}" SSH_HOST)"
  [[ "${host}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]] ||
    die "Invalid enrolled SSH host"
  ssh \
    -T \
    -i "${SSH_KEY_PATH}" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="${SSH_KNOWN_HOSTS}" \
    -o ConnectTimeout=10 \
    -o ServerAliveInterval=5 \
    -o ServerAliveCountMax=3 \
    -- "prtg-nats-admin@${host}"
}

# Every enrolled probe, one NATS user name per line. The inventory is the
# list: a probe without a file under PROBE_DIR is not enrolled.
enrolled_probes() {
  local inventory=""

  shopt -s nullglob
  for inventory in "${PROBE_DIR}"/*.env; do
    basename -- "${inventory}" .env
  done
  shopt -u nullglob
}

# Every probe whose inventory names this host, one NATS user name per line.
# For the commands that take ADMIN@HOST instead of the user name: once a probe
# is enrolled, the host already says which account belongs to it.
#
# No answer and several answers are both left to the caller. A host with two
# inventories is a rollout that went sideways - picking one of them here would
# bury that in a command that appears to have worked.
enrolled_users_for_host() {
  local wanted_host="$1"
  local username=""

  while read -r username; do
    [[ "$(read_optional_env_value "$(probe_path "${username}")" SSH_HOST)" \
      == "${wanted_host}" ]] || continue
    printf '%s\n' "${username}"
  done < <(enrolled_probes)
}

read_env_value() {
  local input_file="$1"
  local wanted_key="$2"
  local key=""
  local value=""

  while IFS='=' read -r key value; do
    if [[ "${key}" == "${wanted_key}" ]]; then
      printf '%s\n' "${value}"
      return 0
    fi
  done < "${input_file}"
  return 1
}

# Like read_env_value, but returns an empty string instead of an error when the
# entry is missing. For optional inventory fields.
read_optional_env_value() {
  local input_file="$1"
  local wanted_key="$2"

  [[ -f "${input_file}" ]] || return 0
  read_env_value "${input_file}" "${wanted_key}" 2>/dev/null || true
}

# Renders the MPP configuration of one account to stdout. Probe identity and
# password come from the protected runtime state.
#
# mpp_render_config reads the MPP_* values through dynamic scope, which is why
# they look unused to a static check.
# shellcheck disable=SC2034
render_probe_config() {
  local username="$1"
  local probe_id="$2"
  local access_key="$3"
  local probe_name="$4"
  local credentials=""

  credentials="$(credential_path "${username}")"
  [[ -f "${credentials}" ]] ||
    {
      printf 'Unknown NATS user: %s\n' "${username}" >&2
      return 1
    }

  local MPP_PROBE_ID="${probe_id}"
  local MPP_ACCESS_KEY="${access_key}"
  local MPP_PROBE_NAME="${probe_name}"
  local MPP_NATS_HOST="${NATS_FQDN}"
  # The port deliberately comes from the active environment rather than from
  # the credential file, so that a port change in .env takes effect for older
  # accounts as well on the next rollout.
  local MPP_NATS_PORT="${NATS_PORT}"
  local MPP_NATS_USER="${username}"
  local MPP_NATS_PASSWORD=""
  local MPP_SERVER_CA=""
  local MPP_CLIENT_NAME="${MPP_DEFAULT_CLIENT_NAME}"

  MPP_NATS_PASSWORD="$(read_env_value "${credentials}" NATS_PASSWORD)"
  MPP_SERVER_CA="$(read_optional_env_value "${credentials}" NATS_CA_PATH)"
  [[ -n "${MPP_SERVER_CA}" ]] || MPP_SERVER_CA="${MPP_CA_PATH}"

  mpp_render_config
}

write_auth_user_file() {
  local output_path="$1"
  local username="$2"
  local password_bcrypt="$3"

  printf '%s\t%s\n' "${username}" "${password_bcrypt}" > "${output_path}"
  chmod 600 "${output_path}"
}

bootstrap_auth_registry() {
  local auth_files=()
  local credentials="${CREDENTIAL_DIR}/prtg-nats.env"
  local username=""
  local password_bcrypt=""

  create_runtime_directories
  shopt -s nullglob
  auth_files=("${AUTH_USER_DIR}"/*.auth)
  shopt -u nullglob
  [[ "${#auth_files[@]}" -eq 0 ]] || return 0

  [[ -f "${credentials}" && -f "${RUNTIME_DIR}/conf/nats-server.conf" ]] ||
    {
      printf 'Legacy NATS runtime is incomplete; cannot initialize user registry.\n' >&2
      return 1
    }
  username="$(read_env_value "${credentials}" NATS_USERNAME)"
  validate_nats_username "${username}" ||
    {
      printf 'Invalid legacy NATS username: %s\n' "${username}" >&2
      return 1
    }
  password_bcrypt="$(
    # Single quotes intentionally preserve the literal bcrypt dollar signs.
    # shellcheck disable=SC2016
    sed -n \
      's/^[[:space:]]*password:[[:space:]]*"\(\$2[abxy]\$[0-9][0-9]\$[.\/A-Za-z0-9]\{53\}\)"[[:space:]]*$/\1/p' \
      "${RUNTIME_DIR}/conf/nats-server.conf"
  )"
  [[ "$(printf '%s\n' "${password_bcrypt}" | sed '/^$/d' | wc -l | tr -d ' ')" == "1" ]] ||
    {
      printf 'Could not identify exactly one legacy bcrypt hash.\n' >&2
      return 1
    }
  grep -F "user: \"${username}\"" "${RUNTIME_DIR}/conf/nats-server.conf" >/dev/null ||
    {
      printf 'Legacy credentials do not match the NATS configuration.\n' >&2
      return 1
    }
  write_auth_user_file \
    "${AUTH_USER_DIR}/${username}.auth" \
    "${username}" \
    "${password_bcrypt}"
}

ensure_management_ssh_key() {
  create_runtime_directories
  require_command ssh-keygen

  if [[ -f "${SSH_KEY_PATH}" || -f "${SSH_KEY_PATH}.pub" ]]; then
    [[ -f "${SSH_KEY_PATH}" && -f "${SSH_KEY_PATH}.pub" ]] ||
      {
        printf 'Incomplete management SSH key pair under %s.\n' \
          "${SSH_PRIVATE_DIR}" >&2
        return 1
      }
  else
    ssh-keygen \
      -q \
      -t ed25519 \
      -N '' \
      -C "prtg-nats-mpp-admin@${NATS_FQDN}" \
      -f "${SSH_KEY_PATH}"
  fi
  chmod 600 "${SSH_KEY_PATH}"
  chmod 644 "${SSH_KEY_PATH}.pub"
  touch "${SSH_KNOWN_HOSTS}"
  chmod 600 "${SSH_KNOWN_HOSTS}"
}

# The key that signs the probe helper. Whoever holds the management SSH key
# can open the channel; only whoever holds this key can put a new helper - and
# therefore new root code - through it.
#
# P-256 with SHA-256 rather than ed25519: the probe verifies with
# "openssl dgst -verify", which speaks this everywhere, while raw ed25519
# verification needs an OpenSSL 3 flag that Debian 11 does not have.
ensure_helper_signing_key() {
  create_runtime_directories
  require_command openssl

  if [[ -f "${HELPER_SIGNING_KEY_PATH}" ]]; then
    # The public half is derived, so a missing one is regenerated rather than
    # treated as an error - unlike the SSH pair, where the private half alone
    # cannot produce the exact file the probes were given.
    [[ -f "${HELPER_SIGNING_PUBLIC_PATH}" ]] ||
      openssl pkey \
        -in "${HELPER_SIGNING_KEY_PATH}" \
        -pubout \
        -out "${HELPER_SIGNING_PUBLIC_PATH}"
  else
    openssl ecparam \
      -name prime256v1 \
      -genkey \
      -noout \
      -out "${HELPER_SIGNING_KEY_PATH}"
    openssl pkey \
      -in "${HELPER_SIGNING_KEY_PATH}" \
      -pubout \
      -out "${HELPER_SIGNING_PUBLIC_PATH}"
  fi
  chmod 600 "${HELPER_SIGNING_KEY_PATH}"
  chmod 644 "${HELPER_SIGNING_PUBLIC_PATH}"
}

# The signature the probe checks before it replaces its own helper, base64 on
# a single line so it fits into one protocol argument.
sign_helper_file() {
  local file="$1"

  ensure_helper_signing_key
  openssl dgst -sha256 -sign "${HELPER_SIGNING_KEY_PATH}" "${file}" |
    openssl base64 -A
}

nats_container_running() {
  [[ "$(docker inspect --format '{{.State.Running}}' prtg-nats 2>/dev/null || true)" == "true" ]]
}

# SHA-256 of the active runtime CA, in the same format the probe reports.
runtime_ca_fingerprint() {
  [[ -f "${CERT_DIR}/ca.pem" ]] || return 1
  openssl x509 -in "${CERT_DIR}/ca.pem" -outform DER |
    openssl dgst -sha256 |
    awk '{print $NF}'
}

# Every NATS account currently connected, one per line. An overview of the
# whole fleet queries monitoring once instead of once per probe. If the
# endpoint is unreachable the output stays empty.
nats_connected_users() {
  local response=""

  command -v curl >/dev/null 2>&1 || return 0
  response="$(
    curl --fail --silent --max-time 5 \
      'http://127.0.0.1:8222/connz?auth=1&state=open' || true
  )"
  [[ -n "${response}" ]] || return 0
  printf '%s' "${response}" |
    awk '
      {
        while (match($0, /"authorized_user"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
          entry = substr($0, RSTART, RLENGTH)
          sub(/^"authorized_user"[[:space:]]*:[[:space:]]*"/, "", entry)
          sub(/"$/, "", entry)
          print entry
          $0 = substr($0, RSTART + RLENGTH)
        }
      }
    '
}

wait_for_nats_user_connection() {
  local username="$1"
  local response=""

  require_command curl
  for _ in $(seq 1 30); do
    response="$(
      curl --fail --silent \
        'http://127.0.0.1:8222/connz?auth=1&state=open' || true
    )"
    # Monitoring reports the connected name as "authorized_user"; there is no
    # "user" field in /connz. Both spellings are covered so the check works
    # regardless of the JSON formatting.
    if printf '%s' "${response}" |
      grep -F "\"authorized_user\": \"${username}\"" >/dev/null ||
      printf '%s' "${response}" |
        grep -F "\"authorized_user\":\"${username}\"" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  printf 'No active NATS connection found for user %s.\n' "${username}" >&2
  return 1
}

