#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage:
  ./prtg-nats probe enroll [USER] ADMIN@HOST [--reenroll]
  ./prtg-nats probe list
  ./prtg-nats probe show USER
  ./prtg-nats probe status USER
  ./prtg-nats probe status --all [--format text|json]
  ./prtg-nats probe info USER
  ./prtg-nats probe configure USER [--probe-name NAME]
  ./prtg-nats probe install-ca USER
  ./prtg-nats probe helper-update USER|--all
  ./prtg-nats probe adopt USER
  ./prtg-nats probe apply USER
  ./prtg-nats probe unenroll USER [--remove-access] [--remove-sensors]
                                  [--uninstall-mpp]
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
# shellcheck source=ops.sh
source "${SCRIPT_DIR}/ops.sh"

require_command ssh
require_command scp
require_command ssh-keygen

BOOTSTRAP_CONTROL_PATH="${PRTG_NATS_BOOTSTRAP_CONTROL_PATH:-}"
# A probe name explicitly requested for this run; empty means derive one.
REQUESTED_PROBE_NAME="${PRTG_NATS_PROBE_NAME:-}"

require_username() {
  local username="${1:-}"
  [[ -n "${username}" ]] || die "A NATS username is required"
  validate_nats_username "${username}" || die "Invalid NATS username"
}

credential_exists() {
  [[ -f "$(credential_path "$1")" && -f "$(auth_user_path "$1")" ]]
}

bootstrap_ssh() {
  local target="$1"
  local ssh_options=(
    -o BatchMode=yes
    -o StrictHostKeyChecking=yes
    -o UserKnownHostsFile="${SSH_KNOWN_HOSTS}"
    -o ConnectTimeout=10
  )
  shift
  if [[ -n "${BOOTSTRAP_CONTROL_PATH}" ]]; then
    [[ -S "${BOOTSTRAP_CONTROL_PATH}" ]] ||
      die "Bootstrap SSH control session is unavailable"
    ssh_options+=(-o "ControlPath=${BOOTSTRAP_CONTROL_PATH}")
  fi
  ssh "${ssh_options[@]}" -- "${target}" "$@"
}

# Rewrites the inventory while preserving the probe identity, so a rotation or
# transaction step cannot lose it.
write_inventory() {
  local username="$1"
  local host="$2"
  local pending_transaction="${3:-}"
  local destination=""
  local temporary=""
  local probe_id=""
  local access_key=""
  local probe_name=""

  destination="$(probe_path "${username}")"
  probe_id="${PROBE_ID_OVERRIDE:-$(read_optional_env_value "${destination}" PROBE_ID)}"
  access_key="${ACCESS_KEY_OVERRIDE:-$(read_optional_env_value "${destination}" ACCESS_KEY)}"
  probe_name="${PROBE_NAME_OVERRIDE:-$(read_optional_env_value "${destination}" PROBE_NAME)}"
  temporary="$(mktemp "${PROBE_DIR}/${username}.env.XXXXXX")"
  {
    printf 'NATS_USERNAME=%s\n' "${username}"
    printf 'SSH_HOST=%s\n' "${host}"
    printf 'SSH_PORT=22\n'
    printf 'PENDING_TRANSACTION=%s\n' "${pending_transaction}"
    printf 'PROBE_ID=%s\n' "${probe_id}"
    printf 'ACCESS_KEY=%s\n' "${access_key}"
    printf 'PROBE_NAME=%s\n' "${probe_name}"
  } > "${temporary}"
  chmod 600 "${temporary}"
  mv "${temporary}" "${destination}"
}

inventory_host() {
  local inventory=""

  inventory="$(probe_path "$1")"
  [[ -f "${inventory}" ]] || die "Probe is not enrolled: $1"
  read_env_value "${inventory}" SSH_HOST
}

# Queries the restricted management access without failing when the package or
# the configuration on the probe is still missing.
probe_info() {
  printf 'probe-info\n' | managed_ssh "$1"
}

probe_info_value() {
  local information="$1"
  local field="$2"

  printf '%s\n' "${information}" |
    awk -F= -v field="${field}" '
      $1 == field {
        sub("^" field "=", "", $0)
        print $0
        found = 1
        exit
      }
      END { if (!found) exit 42 }
    ' 2>/dev/null || true
}

# Reachability test for the key-based access. No die() on failure.
probe_reachable() {
  local username="$1"

  [[ -f "$(probe_path "${username}")" ]] || return 1
  probe_info "${username}" >/dev/null 2>&1
}

confirm_and_pin_host_key() {
  local host="$1"
  local scanned=""

  if ssh-keygen -F "${host}" -f "${SSH_KNOWN_HOSTS}" >/dev/null 2>&1; then
    printf 'SSH host key for %s is already pinned; keeping it.\n' "${host}"
    return 0
  fi
  scanned="$(mktemp "${SSH_PRIVATE_DIR}/host-key.XXXXXX")"
  ssh-keyscan -T 10 -t ed25519,rsa -H -- "${host}" 2>/dev/null > "${scanned}"
  [[ -s "${scanned}" ]] || die "Could not obtain an SSH host key from ${host}"
  printf 'SSH host-key fingerprints for %s:\n' "${host}"
  ssh-keygen -lf "${scanned}"
  if [[ -t 0 ]]; then
    read -r -p 'Verify the fingerprints through a trusted channel. Continue? [y/N]: ' confirmation
    is_affirmative "${confirmation}" ||
      die "SSH host-key confirmation declined"
  else
    die "SSH host-key enrollment requires an interactive terminal"
  fi
  cat "${scanned}" >> "${SSH_KNOWN_HOSTS}"
  chmod 600 "${SSH_KNOWN_HOSTS}"
  rm -f -- "${scanned}"
}

enroll_probe() {
  local username="$1"
  local bootstrap_target="$2"
  local allow_existing="${3:-false}"
  local host="${bootstrap_target##*@}"
  local own_bootstrap_session="false"
  local remote_stage=""
  local remote_command=""
  local bootstrap_options=(
    -o BatchMode=yes
    -o StrictHostKeyChecking=yes
    -o UserKnownHostsFile="${SSH_KNOWN_HOSTS}"
  )

  credential_exists "${username}" || die "Unknown NATS user: ${username}"
  [[ "${username}" != "${NATS_USERNAME}" ]] ||
    die "The shared Core user cannot be enrolled as an individual probe"
  [[ "${bootstrap_target}" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]] ||
    die "Bootstrap target must use ADMIN@HOST"
  [[ ! -f "$(probe_path "${username}")" || "${allow_existing}" == "true" ]] ||
    die "Probe is already enrolled for user: ${username}"
  validate_ssh_source_cidr "${MPP_SSH_SOURCE_CIDR}" ||
    die "Invalid MPP_SSH_SOURCE_CIDR: ${MPP_SSH_SOURCE_CIDR}"

  ensure_management_ssh_key
  ensure_helper_signing_key
  confirm_and_pin_host_key "${host}"
  # Every call below runs with BatchMode=yes, so none of them can ask for a
  # password. Called from install-mpp the session is already open and inherited
  # through the environment; called directly this is the only place that can
  # open one, and without it the enrollment fails at the first connection with
  # nothing but "Permission denied".
  #
  # The session is pointed at the key that was pinned a moment ago. It settles
  # the host-key question for everything that follows, because the calls below
  # are multiplexed over its ControlPath and their own UserKnownHostsFile never
  # comes into it: without this the master would ask a second time, against
  # whatever known_hosts the invoking account happens to have.
  if [[ -z "${BOOTSTRAP_CONTROL_PATH}" ]]; then
    open_bootstrap_control_session "${bootstrap_target}" \
      -o StrictHostKeyChecking=yes \
      -o UserKnownHostsFile="${SSH_KNOWN_HOSTS}" ||
      die "Could not open a bootstrap session as ${bootstrap_target}; it needs the password of that account or an SSH key for it"
    own_bootstrap_session="true"
  fi
  [[ -S "${BOOTSTRAP_CONTROL_PATH}" ]] ||
    die "Bootstrap SSH control session is unavailable"
  bootstrap_options+=(-o "ControlPath=${BOOTSTRAP_CONTROL_PATH}")

  # Armed before the first remote call, because from here on a failure would
  # otherwise leave the ssh master running. Only a session opened here is
  # closed here: one inherited from install-mpp is still needed after the
  # enrollment returns and is cleaned up there.
  cleanup_remote() {
    if [[ "${remote_stage}" =~ ^/tmp/prtg-nats-enroll\.[A-Za-z0-9]+$ ]]; then
      bootstrap_ssh "${bootstrap_target}" \
        "rm -rf -- '${remote_stage}'" >/dev/null 2>&1 || true
    fi
    [[ "${own_bootstrap_session}" != "true" ]] ||
      close_bootstrap_control_session "${bootstrap_target}"
  }
  trap cleanup_remote EXIT

  remote_stage="$(
    bootstrap_ssh "${bootstrap_target}" \
      'umask 077; mktemp -d /tmp/prtg-nats-enroll.XXXXXX'
  )"
  [[ "${remote_stage}" =~ ^/tmp/prtg-nats-enroll\.[A-Za-z0-9]+$ ]] ||
    die "Unexpected remote enrollment path"

  scp \
    -q \
    "${bootstrap_options[@]}" \
    -- \
    "${SCRIPT_DIR}/enroll-probe.sh" \
    "${SCRIPT_DIR}/prtg-nats-probe-helper" \
    "${SSH_KEY_PATH}.pub" \
    "${HELPER_SIGNING_PUBLIC_PATH}" \
    "${bootstrap_target}:${remote_stage}/"
  printf -v remote_command \
    'sudo bash %q --public-key %q --helper %q --signing-key %q --source-cidr %q' \
    "${remote_stage}/enroll-probe.sh" \
    "${remote_stage}/prtg-nats-mpp-admin.pub" \
    "${remote_stage}/prtg-nats-probe-helper" \
    "${remote_stage}/helper-signing.pub" \
    "${MPP_SSH_SOURCE_CIDR}"
  ssh \
    -t \
    "${bootstrap_options[@]}" \
    -- "${bootstrap_target}" "${remote_command}"

  write_inventory "${username}" "${host}"
  # probe-info rather than status: right after enrollment the package may not
  # be there yet, but the restricted access has to answer already.
  if ! probe_info "${username}"; then
    rm -f -- "$(probe_path "${username}")"
    die "Restricted management connection failed after enrollment"
  fi
  cleanup_remote
  remote_stage=""
  trap - EXIT
  printf 'Enrolled %s at prtg-nats-admin@%s.\n' "${username}" "${host}"
}

list_probes() {
  local inventories=()
  local inventory=""
  local username=""
  local host=""
  local pending=""
  local probe_name=""

  shopt -s nullglob
  inventories=("${PROBE_DIR}"/*.env)
  shopt -u nullglob
  printf '%-24s %-32s %-32s %s\n' \
    "NATS USER" "SSH TARGET" "PROBE NAME" "PENDING"
  for inventory in "${inventories[@]}"; do
    username="$(read_env_value "${inventory}" NATS_USERNAME)"
    host="$(read_env_value "${inventory}" SSH_HOST)"
    pending="$(read_env_value "${inventory}" PENDING_TRANSACTION || true)"
    probe_name="$(read_optional_env_value "${inventory}" PROBE_NAME)"
    printf '%-24s %-32s %-32s %s\n' \
      "${username}" \
      "prtg-nats-admin@${host}" \
      "${probe_name:--}" \
      "${pending:--}"
  done
}

status_probe() {
  local username="$1"
  printf 'status\n' | managed_ssh "${username}"
}

# The state of one probe for the fleet overview. Deliberately never fails: a
# dead host becomes a row with a reason, not an abort. The result is left in
# the OVERVIEW_* variables.
collect_probe_state() {
  local username="$1"
  local connected_users="$2"
  local expected_ca="$3"
  local inventory=""
  local information=""

  inventory="$(probe_path "${username}")"
  OVERVIEW_HOST="$(read_env_value "${inventory}" SSH_HOST)"
  OVERVIEW_SERVICE="-"
  OVERVIEW_PACKAGE="-"
  OVERVIEW_CA="-"
  OVERVIEW_NATS="-"
  OVERVIEW_REACHABLE="false"
  OVERVIEW_NOTE=""

  if ! information="$(probe_info "${username}" 2>/dev/null)"; then
    OVERVIEW_NOTE="unreachable"
    return 0
  fi

  OVERVIEW_REACHABLE="true"
  OVERVIEW_SERVICE="$(probe_info_value "${information}" service)"
  OVERVIEW_PACKAGE="$(probe_info_value "${information}" package)"
  [[ -n "${OVERVIEW_SERVICE}" ]] || OVERVIEW_SERVICE="unknown"
  [[ -n "${OVERVIEW_PACKAGE}" ]] || OVERVIEW_PACKAGE="unknown"

  local reported_ca=""
  reported_ca="$(probe_info_value "${information}" ca_sha256)"
  if [[ -z "${reported_ca}" || "${reported_ca}" == "none" ]]; then
    OVERVIEW_CA="missing"
  elif [[ -n "${expected_ca}" && "${reported_ca}" == "${expected_ca}" ]]; then
    OVERVIEW_CA="ok"
  else
    OVERVIEW_CA="mismatched"
  fi

  if printf '%s\n' "${connected_users}" |
    grep -Fxq "${username}"; then
    OVERVIEW_NATS="connected"
  else
    OVERVIEW_NATS="disconnected"
  fi
}

json_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# An overview of every registered probe. The exit code is 0 only when every
# probe is reachable and active, carries the expected CA and is connected to
# NATS - which makes the call usable from cron and from monitoring.
overview_probes() {
  local output_format="$1"
  local inventories=()
  local inventory=""
  local username=""
  local connected_users=""
  local expected_ca=""
  local problems=0
  local first_entry="true"

  shopt -s nullglob
  inventories=("${PROBE_DIR}"/*.env)
  shopt -u nullglob
  connected_users="$(nats_connected_users)"
  expected_ca="$(runtime_ca_fingerprint || true)"

  if [[ "${output_format}" == "json" ]]; then
    printf '{\n  "probes": [\n'
  else
    printf '%-24s %-30s %-10s %-10s %-11s %s\n' \
      "NATS USER" "HOST" "SERVICE" "PACKAGE" "CA" "NATS"
  fi

  for inventory in "${inventories[@]}"; do
    username="$(read_env_value "${inventory}" NATS_USERNAME)"
    collect_probe_state "${username}" "${connected_users}" "${expected_ca}"
    if [[ "${OVERVIEW_REACHABLE}" != "true" ||
          "${OVERVIEW_SERVICE}" != "active" ||
          "${OVERVIEW_CA}" != "ok" ||
          "${OVERVIEW_NATS}" != "connected" ]]; then
      problems=$((problems + 1))
    fi

    if [[ "${output_format}" == "json" ]]; then
      [[ "${first_entry}" == "true" ]] || printf ',\n'
      first_entry="false"
      printf '    {"nats_user": "%s", "host": "%s", "reachable": %s, ' \
        "$(json_escape "${username}")" \
        "$(json_escape "${OVERVIEW_HOST}")" \
        "${OVERVIEW_REACHABLE}"
      printf '"service": "%s", "package": "%s", "ca": "%s", "nats": "%s"' \
        "$(json_escape "${OVERVIEW_SERVICE}")" \
        "$(json_escape "${OVERVIEW_PACKAGE}")" \
        "$(json_escape "${OVERVIEW_CA}")" \
        "$(json_escape "${OVERVIEW_NATS}")"
      if [[ -n "${OVERVIEW_NOTE}" ]]; then
        printf ', "note": "%s"' "$(json_escape "${OVERVIEW_NOTE}")"
      fi
      printf '}'
    else
      printf '%-24s %-30s %-10s %-10s %-11s %s\n' \
        "${username}" \
        "${OVERVIEW_HOST}" \
        "${OVERVIEW_SERVICE}" \
        "${OVERVIEW_PACKAGE}" \
        "${OVERVIEW_CA}" \
        "${OVERVIEW_NATS}${OVERVIEW_NOTE:+ (${OVERVIEW_NOTE})}"
    fi
  done

  if [[ "${output_format}" == "json" ]]; then
    [[ "${first_entry}" == "true" ]] || printf '\n'
    printf '  ],\n  "total": %s,\n  "problems": %s\n}\n' \
      "${#inventories[@]}" "${problems}"
  else
    printf '\n%s of %s probes without findings.\n' \
      "$(( ${#inventories[@]} - problems ))" "${#inventories[@]}"
    if [[ -z "${connected_users}" && "${#inventories[@]}" -gt 0 ]]; then
      printf 'Note: NATS monitoring was unreachable; the NATS column is unusable.\n' >&2
    fi
  fi
  [[ "${problems}" -eq 0 ]]
}

# Determines the probe identity from the inventory, failing that from a
# configuration already present on the probe, and otherwise creates one. The
# result is left in RESOLVED_PROBE_ID, RESOLVED_ACCESS_KEY and
# RESOLVED_PROBE_NAME.
resolve_probe_identity() {
  local username="$1"
  local host="$2"
  local remote_information="${3:-}"
  local inventory=""
  local candidate=""
  local label_host=""

  inventory="$(probe_path "${username}")"
  RESOLVED_PROBE_ID="$(read_optional_env_value "${inventory}" PROBE_ID)"
  RESOLVED_ACCESS_KEY="$(read_optional_env_value "${inventory}" ACCESS_KEY)"
  RESOLVED_PROBE_NAME="$(read_optional_env_value "${inventory}" PROBE_NAME)"

  if [[ -n "${remote_information}" ]]; then
    if [[ -z "${RESOLVED_PROBE_ID}" ]]; then
      candidate="$(probe_info_value "${remote_information}" id)"
      mpp_validate_probe_id "${candidate}" && RESOLVED_PROBE_ID="${candidate}"
    fi
    if [[ -z "${RESOLVED_ACCESS_KEY}" ]]; then
      candidate="$(probe_info_value "${remote_information}" access_key)"
      mpp_validate_access_key "${candidate}" &&
        RESOLVED_ACCESS_KEY="${candidate}"
    fi
    if [[ -z "${RESOLVED_PROBE_NAME}" ]]; then
      candidate="$(probe_info_value "${remote_information}" name)"
      mpp_validate_probe_name "${candidate}" && RESOLVED_PROBE_NAME="${candidate}"
    fi
  fi

  # An explicitly requested name beats both inventory and probe: it is the
  # only value the operator deliberately supplies for this run.
  if [[ -n "${REQUESTED_PROBE_NAME}" ]]; then
    mpp_validate_probe_name "${REQUESTED_PROBE_NAME}" ||
      die "Invalid probe name: ${REQUESTED_PROBE_NAME}"
    RESOLVED_PROBE_NAME="${REQUESTED_PROBE_NAME}"
  fi

  [[ -n "${RESOLVED_PROBE_ID}" ]] ||
    RESOLVED_PROBE_ID="$(mpp_generate_uuid)"

  # If the probe was enrolled through its IP, the address makes a poor name.
  # The hostname the probe reports is the better source; older probe helpers do
  # not report it, and then the address stands.
  label_host="${host}"
  if [[ "${host}" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    if [[ -n "${remote_information}" ]]; then
      candidate="$(probe_info_value "${remote_information}" hostname)"
      if [[ -n "${candidate}" && "${candidate}" != "none" ]] &&
        mpp_validate_nats_host "${candidate}"; then
        label_host="${candidate}"
      fi
    fi
  fi

  [[ -n "${RESOLVED_PROBE_NAME}" ]] ||
    RESOLVED_PROBE_NAME="$(ask_probe_name "${label_host}" "${host}")"
  # The readable part of the access key follows the probe name, so that name
  # and key line up the same way in PRTG. A key that has already been issued is
  # left alone: changing it would mean updating it in the core as well.
  [[ -n "${RESOLVED_ACCESS_KEY}" ]] ||
    RESOLVED_ACCESS_KEY="$(mpp_default_access_key "${RESOLVED_PROBE_NAME}")"
}

# Asks for the probe name when one has to be issued and the probe is only
# known by its address. Without a terminal the suggestion stands, so
# automation does not hang.
ask_probe_name() {
  local label_host="$1"
  local ssh_host="$2"
  local suggestion=""
  local answer=""

  suggestion="$(mpp_default_probe_name "${label_host}")"
  if [[ "${label_host}" != "${ssh_host}" ]] || [[ ! -t 0 ]] ||
    [[ ! "${ssh_host}" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    printf '%s\n' "${suggestion}"
    return 0
  fi

  printf '\nThe probe is only known by its address %s and reports no usable\n' \
    "${ssh_host}" >&2
  printf 'host name, so the derived name is hard to tell apart in PRTG.\n' >&2
  while true; do
    read -r -p "Probe name [${suggestion}]: " answer
    [[ -n "${answer}" ]] || answer="${suggestion}"
    if mpp_validate_probe_name "${answer}"; then
      printf '%s\n' "${answer}"
      return 0
    fi
    printf 'Use letters, digits and . _ @ - and start with a letter or digit.\n' >&2
  done
}

remember_probe_identity() {
  local username="$1"
  local host="$2"

  PROBE_ID_OVERRIDE="${RESOLVED_PROBE_ID}" \
  ACCESS_KEY_OVERRIDE="${RESOLVED_ACCESS_KEY}" \
  PROBE_NAME_OVERRIDE="${RESOLVED_PROBE_NAME}" \
    write_inventory "${username}" "${host}"
}

RENDERED_PROBE_CONFIG=""
cleanup_rendered_config() {
  if [[ "${RENDERED_PROBE_CONFIG}" == "${RUNTIME_DIR}"/mpp-config.* ]]; then
    rm -f -- "${RENDERED_PROBE_CONFIG}"
  fi
}

install_ca_on_probe() {
  local username="$1"

  [[ -f "${CERT_DIR}/ca.pem" ]] ||
    die "Public CA not found: ${CERT_DIR}/ca.pem"
  {
    printf 'install-ca\n'
    cat "${CERT_DIR}/ca.pem"
  } | managed_ssh "${username}"
}

# Hands the probe the helper this checkout ships. The probe verifies the
# signature against the key it was given during enrollment before it replaces
# anything, so a probe from before signed updates refuses this and has to be
# enrolled again - see the message it sends back.
update_probe_helper() {
  local username="$1"
  local helper="${SCRIPT_DIR}/prtg-nats-probe-helper"
  local signature=""

  [[ -f "${helper}" ]] || die "Probe helper not found: ${helper}"
  signature="$(sign_helper_file "${helper}")"
  {
    printf 'helper-update\t%s\n' "${signature}"
    cat "${helper}"
  } | managed_ssh "${username}"
}

# One failure does not stop the fleet: a probe that is down should not keep
# every other one on an old helper.
update_helper_on_all_probes() {
  local username=""
  local failures=0

  for username in $(enrolled_probes); do
    printf '%s: ' "${username}"
    if ! update_probe_helper "${username}"; then
      failures=$((failures + 1))
    fi
  done
  [[ "${failures}" -eq 0 ]] ||
    die "The helper update failed on ${failures} probe(s)"
}

# Renders the configuration centrally and rolls it out transactionally.
configure_probe() {
  local username="$1"
  local host=""
  local transaction_id=""
  local remote_information=""
  local rendered=""

  credential_exists "${username}" || die "Unknown NATS user: ${username}"
  host="$(inventory_host "${username}")"
  remote_information="$(probe_info "${username}")" ||
    die "The restricted management connection to ${host} failed"
  [[ "$(probe_info_value "${remote_information}" package)" != "none" ]] ||
    die "prtgmpprobe is not installed on ${host}; run install-mpp with a bootstrap target first"

  resolve_probe_identity "${username}" "${host}" "${remote_information}"
  RENDERED_PROBE_CONFIG="$(mktemp "${RUNTIME_DIR}/mpp-config.XXXXXX")"
  rendered="${RENDERED_PROBE_CONFIG}"
  chmod 600 "${rendered}"
  trap cleanup_rendered_config EXIT

  render_probe_config \
    "${username}" \
    "${RESOLVED_PROBE_ID}" \
    "${RESOLVED_ACCESS_KEY}" \
    "${RESOLVED_PROBE_NAME}" > "${rendered}" ||
    die "Could not render the MPP configuration for ${username}"
  mpp_validate_rendered_config "${rendered}" ||
    die "The rendered MPP configuration failed its structure check"

  transaction_id="configure-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  {
    printf 'write-config\t%s\n' "${transaction_id}"
    cat "${rendered}"
  } | managed_ssh "${username}" ||
    die "The probe rejected the generated configuration"
  cleanup_rendered_config
  trap - EXIT

  PROBE_ID_OVERRIDE="${RESOLVED_PROBE_ID}" \
  ACCESS_KEY_OVERRIDE="${RESOLVED_ACCESS_KEY}" \
  PROBE_NAME_OVERRIDE="${RESOLVED_PROBE_NAME}" \
    write_inventory "${username}" "${host}" "${transaction_id}"

  if ! finish_probe_transaction activate "${username}"; then
    finish_probe_transaction rollback "${username}" || true
    die "The probe could not start with the generated configuration"
  fi
  if ! status_probe "${username}" ||
    { nats_container_running &&
      ! wait_for_nats_user_connection "${username}"; }; then
    finish_probe_transaction rollback "${username}" || true
    # The cause is almost always on the path between probe and NATS, not in
    # the configuration. The endpoint test asks exactly that and names the
    # phase it fails in.
    printf '\nThe configuration was applied but no NATS connection appeared.\n' >&2
    printf 'It has been rolled back. Diagnose the path from the probe with:\n' >&2
    printf '  sudo ./install-mpp.sh --nats-host %s --nats-port %s --check-only\n' \
      "${NATS_FQDN}" "${NATS_PORT}" >&2
    printf 'A reset during the TLS upgrade means a firewall is cutting the\n' >&2
    printf 'session; NATS starts in plaintext, so a plain port test still passes.\n' >&2
    die "The probe did not connect to NATS with the generated configuration"
  fi
  finish_probe_transaction commit "${username}"
  remember_probe_identity "${username}" "${host}"
  printf '\nConfiguration applied on %s.\n' "${host}"
}

# Adopts an identity that already exists on the probe into the inventory. Used
# after a wizard run or after a manual installation.
adopt_probe_identity() {
  local username="$1"
  local host=""
  local remote_information=""

  host="$(inventory_host "${username}")"
  remote_information="$(probe_info "${username}")" ||
    die "The restricted management connection to ${host} failed"
  resolve_probe_identity "${username}" "${host}" "${remote_information}"
  remember_probe_identity "${username}" "${host}"
  printf 'Recorded probe identity for %s.\n' "${username}"
  printf '  Probe name: %s\n' "${RESOLVED_PROBE_NAME}"
  printf '  Probe id:   %s\n' "${RESOLVED_PROBE_ID}"
}

show_probe() {
  local username="$1"
  local inventory=""
  local remote_information=""

  inventory="$(probe_path "${username}")"
  [[ -f "${inventory}" ]] || die "Probe is not enrolled: ${username}"
  printf 'NATS user:    %s\n' "${username}"
  printf 'SSH target:   prtg-nats-admin@%s\n' \
    "$(read_env_value "${inventory}" SSH_HOST)"
  printf 'Probe name:   %s\n' \
    "$(read_optional_env_value "${inventory}" PROBE_NAME)"
  printf 'Probe id:     %s\n' \
    "$(read_optional_env_value "${inventory}" PROBE_ID)"
  printf 'Access key:   %s\n' \
    "$(read_optional_env_value "${inventory}" ACCESS_KEY)"
  printf 'Inventory:    %s\n' "${inventory}"
  if remote_information="$(probe_info "${username}" 2>/dev/null)"; then
    printf '\nReported by the probe:\n'
    printf '%s\n' "${remote_information}" | sed 's/^/  /'
  else
    printf '\nThe restricted management connection is currently unavailable.\n'
  fi
}

stage_probe() {
  local username="$1"
  local password="$2"
  local inventory=""
  local host=""
  local transaction_id=""

  inventory="$(probe_path "${username}")"
  [[ -f "${inventory}" ]] || die "Probe is not enrolled: ${username}"
  host="$(read_env_value "${inventory}" SSH_HOST)"
  transaction_id="rotate-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  printf 'stage\t%s\t%s\n%s\n' \
    "${transaction_id}" "${username}" "${password}" |
    managed_ssh "${username}"
  write_inventory "${username}" "${host}" "${transaction_id}"
}

pending_transaction() {
  local username="$1"
  local transaction=""

  transaction="$(
    read_env_value "$(probe_path "${username}")" PENDING_TRANSACTION
  )"
  [[ -n "${transaction}" ]] || die "No pending probe transaction for ${username}"
  printf '%s\n' "${transaction}"
}

finish_probe_transaction() {
  local operation="$1"
  local username="$2"
  local transaction=""
  local inventory=""
  local host=""

  inventory="$(probe_path "${username}")"
  transaction="$(pending_transaction "${username}")"
  host="$(read_env_value "${inventory}" SSH_HOST)"
  printf '%s\t%s\n' "${operation}" "${transaction}" |
    managed_ssh "${username}"
  if [[ "${operation}" == "commit" || "${operation}" == "rollback" ]]; then
    write_inventory "${username}" "${host}"
  fi
}

apply_probe() {
  local username="$1"
  local credentials=""
  local password=""

  credentials="$(credential_path "${username}")"
  credential_exists "${username}" || die "Unknown NATS user: ${username}"
  password="$(read_env_value "${credentials}" NATS_PASSWORD)"
  stage_probe "${username}" "${password}"
  if ! finish_probe_transaction activate "${username}"; then
    finish_probe_transaction rollback "${username}" || true
    return 1
  fi
  if ! status_probe "${username}" ||
    { nats_container_running &&
      ! wait_for_nats_user_connection "${username}"; }; then
    finish_probe_transaction rollback "${username}" || true
    return 1
  fi
  finish_probe_transaction commit "${username}"
  printf 'Applied current NATS credentials to the probe.\n'
}

# Every sensor the probe carries, from both sources that know about them.
#
# The two can disagree after an interrupted rollout: the inventory knows what
# we deployed, the probe knows what it actually has. Cleaning up from the
# bookkeeping alone would leave behind precisely the sensors something already
# went wrong with.
carried_sensors() {
  local username="$1"
  local assignment=""
  local listed=""

  assignment="$(probe_sensors_path "${username}")"
  {
    [[ ! -f "${assignment}" ]] ||
      grep -E -v '^[[:space:]]*(#.*)?$' "${assignment}" || true
    listed="$(printf 'sensor-list\n' | managed_ssh "${username}" || true)"
    [[ -z "${listed}" ]] ||
      printf '%s\n' "${listed}" | sed -n 's/^\([A-Za-z0-9][A-Za-z0-9._-]*\)\t.*/\1/p'
  } | sort -u
}

remove_every_sensor() {
  local username="$1"
  local sensor=""
  local removed=0

  while IFS= read -r sensor; do
    [[ -n "${sensor}" ]] || continue
    "${SCRIPT_DIR}/manage-sensors.sh" remove "${sensor}" "${username}" ||
      die "Could not remove sensor ${sensor} from ${username}"
    removed=$((removed + 1))
  done < <(carried_sensors "${username}")
  printf 'Removed %s sensor(s) from %s.\n' "${removed}" "${username}"
}

uninstall_probe_software() {
  local username="$1"
  local answer=""

  answer="$(printf 'mpp-uninstall\n' | managed_ssh "${username}")" ||
    die "The probe could not uninstall its software"
  printf 'Uninstalled the probe software on %s (%s).\n' \
    "${username}" "${answer#OK mpp-uninstalled }"
}

unenroll_probe() {
  local username="$1"
  local remove_access="$2"
  local remove_sensors="$3"
  local uninstall_mpp="$4"
  local inventory=""

  inventory="$(probe_path "${username}")"
  [[ -f "${inventory}" ]] || die "Probe is not enrolled: ${username}"
  # Both of these need the management channel, so they happen before the step
  # that closes it. A failure here stops the unenrollment: a probe that could
  # not be cleaned up has to stay reachable, or nobody reaches it again.
  [[ "${remove_sensors}" != "true" ]] || remove_every_sensor "${username}"
  [[ "${uninstall_mpp}" != "true" ]] || uninstall_probe_software "${username}"
  if [[ "${remove_access}" == "true" ]]; then
    printf 'unenroll\n' | managed_ssh "${username}"
  fi
  rm -f -- "${inventory}"
  # The overlay peer goes with the entry it was rendered from. Without this the
  # hub keeps a peer for a probe nobody manages any more, and that probe keeps
  # a route to the NATS address - retiring it would take our access to it and
  # leave its access to us.
  run_ops overlay refresh >/dev/null 2>&1 || true
  printf 'Removed probe enrollment for %s.\n' "${username}"
  if [[ "${remove_access}" != "true" ]]; then
    printf 'The restricted remote key remains installed; use --remove-access to revoke it.\n'
  fi
  # The account goes with the probe. It was created by the enrolment, so the
  # retirement takes it back - the platform does the same. The one refusal
  # worth surviving is the last remaining account: NATS needs at least one,
  # so that case is reported instead of failing a retirement that has
  # already revoked the probe's access.
  if run_ops user delete "${username}" >/dev/null 2>&1; then
    printf 'Removed the NATS account %s.\n' "${username}"
  else
    printf 'The NATS account %s was kept - remove it with "user delete %s" once
another account exists.\n' "${username}" "${username}"
  fi
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
  enroll)
    enroll_usage="Usage: ./prtg-nats probe enroll [USER] ADMIN@HOST [--reenroll]"
    enroll_allow_existing="false"
    enroll_arguments=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --reenroll)
          enroll_allow_existing="true"
          shift
          ;;
        -*)
          die "${enroll_usage}"
          ;;
        *)
          enroll_arguments+=("$1")
          shift
          ;;
      esac
    done
    case "${#enroll_arguments[@]}" in
      2)
        enroll_username="${enroll_arguments[0]}"
        enroll_target="${enroll_arguments[1]}"
        ;;
      1)
        # An enrolled host already names its account: the inventory was
        # written with it. Only the bootstrap target is left to type, and that
        # one cannot be derived - the admin account is not ours.
        enroll_target="${enroll_arguments[0]}"
        [[ "${enroll_target}" == *@* ]] || die "${enroll_usage}"
        enroll_host="${enroll_target##*@}"
        mapfile -t enroll_candidates < \
          <(enrolled_users_for_host "${enroll_host}")
        case "${#enroll_candidates[@]}" in
          1)
            enroll_username="${enroll_candidates[0]}"
            printf 'Reenrolling %s, the probe enrolled at %s.\n' \
              "${enroll_username}" "${enroll_host}"
            ;;
          0)
            die "No probe is enrolled at ${enroll_host}; name the NATS user: ./prtg-nats probe enroll USER ${enroll_target}"
            ;;
          *)
            die "Several probes are enrolled at ${enroll_host} (${enroll_candidates[*]}); name the NATS user: ./prtg-nats probe enroll USER ${enroll_target}"
            ;;
        esac
        ;;
      *)
        die "${enroll_usage}"
        ;;
    esac
    require_username "${enroll_username}"
    enroll_probe \
      "${enroll_username}" "${enroll_target}" "${enroll_allow_existing}"
    ;;
  list)
    [[ $# -eq 0 ]] || die "Usage: ./prtg-nats probe list"
    list_probes
    ;;
  show)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats probe show USER"
    show_probe "$1"
    ;;
  status)
    if [[ "${1:-}" == "--all" ]]; then
      shift
      overview_format="text"
      if [[ "${1:-}" == "--format" ]]; then
        [[ $# -ge 2 ]] || die "--format requires text or json"
        case "$2" in
          text|json)
            overview_format="$2"
            ;;
          *)
            die "Unsupported format: $2 (expected text or json)"
            ;;
        esac
        shift 2
      fi
      [[ $# -eq 0 ]] ||
        die "Usage: ./prtg-nats probe status --all [--format text|json]"
      overview_probes "${overview_format}"
      exit "$?"
    fi
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats probe status USER"
    status_probe "$1"
    ;;
  info)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats probe info USER"
    probe_info "$1"
    ;;
  configure)
    require_username "${1:-}"
    username="$1"
    shift
    if [[ "${1:-}" == "--probe-name" ]]; then
      [[ $# -ge 2 ]] ||
        die "--probe-name requires a name"
      REQUESTED_PROBE_NAME="$2"
      shift 2
    fi
    [[ $# -eq 0 ]] ||
      die "Usage: ./prtg-nats probe configure USER [--probe-name NAME]"
    configure_probe "${username}"
    ;;
  install-ca)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats probe install-ca USER"
    install_ca_on_probe "$1"
    ;;
  helper-update)
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats probe helper-update USER|--all"
    if [[ "$1" == "--all" ]]; then
      update_helper_on_all_probes
    else
      require_username "$1"
      update_probe_helper "$1"
    fi
    ;;
  adopt)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats probe adopt USER"
    adopt_probe_identity "$1"
    ;;
  internal-reachable)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Internal reachability check requires USER"
    probe_reachable "$1"
    ;;
  apply)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats probe apply USER"
    apply_probe "$1"
    ;;
  unenroll)
    require_username "${1:-}"
    username="$1"
    shift
    remove_access="false"
    remove_sensors="false"
    uninstall_mpp="false"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --remove-access)
          remove_access="true"
          shift
          ;;
        --remove-sensors)
          remove_sensors="true"
          shift
          ;;
        --uninstall-mpp)
          uninstall_mpp="true"
          shift
          ;;
        *)
          die "Usage: ./prtg-nats probe unenroll USER [--remove-access] [--remove-sensors] [--uninstall-mpp]"
          ;;
      esac
    done
    unenroll_probe \
      "${username}" "${remove_access}" "${remove_sensors}" "${uninstall_mpp}"
    ;;
  internal-stage)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Internal stage requires USER"
    IFS= read -r internal_password ||
      die "Internal stage requires a password on stdin"
    stage_probe "$1" "${internal_password}"
    ;;
  internal-activate)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Internal activate requires USER"
    finish_probe_transaction activate "$1"
    status_probe "$1"
    ;;
  internal-rollback)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Internal rollback requires USER"
    finish_probe_transaction rollback "$1"
    ;;
  internal-commit)
    require_username "${1:-}"
    [[ $# -eq 1 ]] || die "Internal commit requires USER"
    finish_probe_transaction commit "$1"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
