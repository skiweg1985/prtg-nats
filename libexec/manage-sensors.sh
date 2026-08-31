#!/usr/bin/env bash

# Central management of the sensor scripts for the multi-platform probes.
#
# A sensor lives versioned under sensors/NAME/ and is rolled out over the same
# restricted channel as the MPP configuration: stage, then activate with a
# self-test, and roll back automatically on failure. Sensor files are text;
# release-owned executables travel in a separately signed text envelope.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage:
  ./prtg-nats sensor list
  ./prtg-nats sensor show NAME
  ./prtg-nats sensor deploy NAME USER [--dry-run]
  ./prtg-nats sensor deploy NAME --all [--dry-run]
  ./prtg-nats sensor prepare USER|--all
  ./prtg-nats sensor status USER|--all
  ./prtg-nats sensor recover NAME USER --transaction TRANSACTION
  ./prtg-nats sensor remove NAME USER
  ./prtg-nats sensor reserve NAME USER INTERFACE
  ./prtg-nats sensor release NAME USER INTERFACE
  ./prtg-nats sensor profile NAME USER PROFILE [--from-file FILE]
  ./prtg-nats sensor profile NAME USER PROFILE --remove
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
SENSOR_PROFILE_DIR="${RUNTIME_DIR}/sensor-profiles"
DRY_RUN="false"
SENSOR_DEPLOYMENT_HELPER_VERSION=9

require_username() {
  local username="${1:-}"
  [[ -n "${username}" ]] || die "A NATS username is required"
  validate_nats_username "${username}" || die "Invalid NATS username"
}

validate_sensor_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

active_sensor_transaction_from_error() {
  local helper_output="$1"
  local active_transaction=""

  active_transaction="$(
    printf '%s\n' "${helper_output}" |
      sed -n 's/^active_transaction=\([A-Za-z0-9][A-Za-z0-9._-]*\)$/\1/p' |
      head -n 1
  )"
  [[ -n "${active_transaction}" ]] || return 1
  validate_sensor_name "${active_transaction}" || return 1
  printf '%s\n' "${active_transaction}"
}

sensor_directory() {
  local name="$1"

  validate_sensor_name "${name}" || die "Invalid sensor name: ${name}"
  [[ -d "${SENSOR_SOURCE_DIR}/${name}" ]] ||
    die "Unknown sensor: ${name}"
  printf '%s/%s\n' "${SENSOR_SOURCE_DIR}" "${name}"
}

sensor_manifest_value() {
  local directory="$1"
  local key="$2"

  read_optional_env_value "${directory}/manifest.env" "${key}"
}

list_sensors() {
  local directory=""
  local name=""

  printf '%-16s %-8s %s\n' 'SENSOR' 'VERSION' 'DESCRIPTION'
  shopt -s nullglob
  for directory in "${SENSOR_SOURCE_DIR}"/*/; do
    [[ -f "${directory}manifest.env" ]] || continue
    name="$(basename -- "${directory}")"
    printf '%-16s %-8s %s\n' \
      "${name}" \
      "$(sensor_manifest_value "${directory%/}" SENSOR_VERSION)" \
      "$(sensor_manifest_value "${directory%/}" SENSOR_DESCRIPTION)"
  done
  shopt -u nullglob
}

show_sensor() {
  local name="$1"
  local directory=""
  local relative=""
  local slot=""

  directory="$(sensor_directory "${name}")"
  printf 'Sensor:       %s\n' "${name}"
  printf 'Version:      %s\n' "$(sensor_manifest_value "${directory}" SENSOR_VERSION)"
  printf 'Description:  %s\n' "$(sensor_manifest_value "${directory}" SENSOR_DESCRIPTION)"
  printf 'Source:       %s\n' "${directory}"
  printf '\nFiles that would be transferred:\n'
  for slot in script wrapper requirements; do
    relative="$(sensor_slot_source "${directory}" "${slot}")"
    [[ -n "${relative}" ]] || continue
    printf '  %-13s %s -> %s\n' \
      "${slot}" "${relative#"${PROJECT_DIR}"/}" "$(remote_slot_path "${name}" "${slot}")"
  done
  if [[ -n "$(sensor_manifest_value "${directory}" SENSOR_IPERF)" ]]; then
    printf '\nMeasures against endpoints managed with "./prtg-nats iperf-server";\n'
    printf 'their credentials are rolled out together with this sensor:\n'
    registered_iperf_servers | sed 's/^/  /'
    [[ -n "$(registered_iperf_servers)" ]] ||
      printf '  none yet — "./prtg-nats iperf-server install ADMIN@HOST"\n'
  fi
  if [[ -n "$(sensor_manifest_value "${directory}" SENSOR_TOOL)" ]]; then
    printf '\nManaged tool: %s (selected by probe userspace platform)\n' \
      "$(sensor_manifest_value "${directory}" SENSOR_TOOL)"
  fi
  printf '\nAssigned to probes:\n'
  probes_with_sensor "${name}" | sed 's/^/  /'
}

# The path of a slot file in the repository, empty if the sensor has none.
sensor_slot_source() {
  local directory="$1"
  local slot="$2"
  local relative=""

  case "${slot}" in
    script)
      relative="$(sensor_manifest_value "${directory}" SENSOR_SCRIPT)"
      ;;
    wrapper)
      relative="$(sensor_manifest_value "${directory}" SENSOR_PRIVILEGED)"
      ;;
    requirements)
      relative="$(sensor_manifest_value "${directory}" SENSOR_REQUIREMENTS)"
      ;;
  esac
  [[ -n "${relative}" ]] || return 0
  [[ -f "${directory}/${relative}" ]] ||
    die "Sensor file declared in the manifest is missing: ${relative}"
  printf '%s/%s\n' "${directory}" "${relative}"
}

# For display only; the authoritative paths are the ones in the probe helper.
remote_slot_path() {
  local name="$1"

  case "$2" in
    script)
      printf '/opt/paessler/share/scripts/%s.py\n' "${name}"
      ;;
    wrapper)
      printf '/usr/local/sbin/prtg-sensor-%s\n' "${name}"
      ;;
    requirements)
      printf '/etc/prtg-nats/sensors/%s/requirements.txt\n' "${name}"
      ;;
  esac
}

assigned_sensors() {
  local username="$1"
  local assignment=""

  assignment="$(probe_sensors_path "${username}")"
  [[ -f "${assignment}" ]] || return 0
  grep -E -v '^[[:space:]]*(#.*)?$' "${assignment}" || true
}

remember_assignment() {
  local username="$1"
  local name="$2"
  local assignment=""

  assignment="$(probe_sensors_path "${username}")"
  touch "${assignment}"
  chmod 600 "${assignment}"
  grep -q -x -- "${name}" "${assignment}" ||
    printf '%s\n' "${name}" >> "${assignment}"
}

forget_assignment() {
  local username="$1"
  local name="$2"
  local assignment=""
  local remaining=""

  assignment="$(probe_sensors_path "${username}")"
  [[ -f "${assignment}" ]] || return 0
  remaining="$(grep -v -x -- "${name}" "${assignment}" || true)"
  if [[ -n "${remaining}" ]]; then
    printf '%s\n' "${remaining}" > "${assignment}"
    chmod 600 "${assignment}"
  else
    rm -f -- "${assignment}"
  fi
}

probes_with_sensor() {
  local name="$1"
  local inventory=""
  local username=""
  local found="false"

  shopt -s nullglob
  for inventory in "${PROBE_DIR}"/*.env; do
    username="$(basename -- "${inventory}" .env)"
    if assigned_sensors "${username}" | grep -q -x -- "${name}"; then
      printf '%s\n' "${username}"
      found="true"
    fi
  done
  shopt -u nullglob
  [[ "${found}" == "true" ]] || printf 'none\n'
}

# Rolls a sensor out transactionally: stage every file first, then activate.
# The self-test on the probe decides whether it worked; if it fails, the helper
# restores the previous state.
deploy_sensor() {
  local name="$1"
  local username="$2"
  local directory=""
  local transaction_id=""
  local slot=""
  local source_path=""
  local version=""
  local first_rollout="false"
  local managed_tool=""
  local managed_tool_version=""
  local managed_tool_fallback_min_version=""
  local helper_version=""
  local platform=""
  local tool_policy=""
  local probe_details=""
  local exported_tool=""
  local tool_envelope=""
  local signature=""
  local tool_preview=""
  local activation_answer=""
  local recovery_transaction=""

  directory="$(sensor_directory "${name}")"
  [[ -f "$(probe_path "${username}")" ]] ||
    die "Probe is not enrolled: ${username}"
  # Read before remember_assignment below records the sensor: that is what
  # tells a first rollout from a repeat, and the endpoint credentials are
  # handled differently in the two cases.
  assigned_sensors "${username}" | grep -q -x -- "${name}" || first_rollout="true"
  version="$(sensor_manifest_value "${directory}" SENSOR_VERSION)"
  managed_tool="$(sensor_manifest_value "${directory}" SENSOR_TOOL)"
  managed_tool_version="$(
    sensor_manifest_value "${directory}" SENSOR_TOOL_VERSION
  )"
  managed_tool_fallback_min_version="$(
    sensor_manifest_value "${directory}" SENSOR_TOOL_FALLBACK_MIN_VERSION
  )"
  [[ -n "${version}" ]] || die "The manifest of ${name} declares no version"
  if [[ -n "${managed_tool}" && -z "${managed_tool_version}" ]]; then
    die "The manifest of ${name} declares no managed tool version"
  fi
  if [[ -n "${managed_tool}" && -z "${managed_tool_fallback_min_version}" ]]; then
    die "The manifest of ${name} declares no system tool minimum version"
  fi

  if [[ "${DRY_RUN}" == "true" ]]; then
    if [[ -n "${managed_tool}" ]]; then
      probe_details="$(printf 'probe-info\n' | managed_ssh "${username}")" ||
        die "The probe did not answer the managed-tool preflight"
      helper_version="$(
        printf '%s\n' "${probe_details}" |
          sed -n 's/^helper_version=//p' | head -n 1
      )"
      if [[ "${helper_version}" =~ ^[0-9]+$ ]] &&
        ((helper_version >= SENSOR_DEPLOYMENT_HELPER_VERSION)); then
        platform="$(
          printf '%s\n' "${probe_details}" |
            sed -n 's/^platform=//p' | head -n 1
        )"
        [[ -n "${platform}" && "${platform}" != *unknown* ]] ||
          die "The probe reported no supported userspace platform"
        require_command docker
        tool_policy="$(docker compose --project-directory "${PROJECT_DIR}" run \
          --rm --no-deps -T web-api \
          python -m app.ops tool-policy \
          "${managed_tool}" "${platform}" "${managed_tool_version}" \
          "${managed_tool_fallback_min_version}")" ||
          die "No approved ${managed_tool} source is available for ${platform}"
        if [[ "${tool_policy}" == "managed" ]]; then
          tool_preview="${managed_tool} ${managed_tool_version} for ${platform} (verified release artifact)"
        elif [[ "${tool_policy}" == "system" ]]; then
          tool_preview="/usr/bin/${managed_tool} >=${managed_tool_fallback_min_version} for ${platform} (validated during activation)"
        else
          die "The tool policy returned an invalid source: ${tool_policy}"
        fi
      else
        tool_preview="${managed_tool} ${managed_tool_version}; platform selection follows the signed helper update"
      fi
    fi
    printf 'Would deploy sensor %s (version %s) to %s:\n' \
      "${name}" "${version}" "${username}"
    for slot in script wrapper requirements; do
      source_path="$(sensor_slot_source "${directory}" "${slot}")"
      [[ -n "${source_path}" ]] || continue
      printf '  %-13s %s\n' "${slot}" "$(remote_slot_path "${name}" "${slot}")"
    done
    printf '  %-13s %s\n' 'self-check' 'runs as the MPP service user'
    if [[ -n "${managed_tool}" ]]; then
      printf '  %-13s %s\n' 'managed-tool' \
        "${tool_preview}"
    fi
    if [[ -n "$(sensor_manifest_value "${directory}" SENSOR_IPERF)" ]]; then
      printf '  %-13s %s\n' 'iperf' \
        "$(registered_iperf_servers | paste -s -d ',' - || true)"
    fi
    return 0
  fi

  # Commit retries depend on the current helper's durable tombstone. Upgrade
  # before any transaction, including sensors that carry no managed tool: an
  # old helper can commit successfully and still turn a lost SSH answer into
  # an unresolvable retry.
  probe_details="$(printf 'probe-info\n' | managed_ssh "${username}")" ||
    die "The probe did not report its helper version"
  helper_version="$(
    printf '%s\n' "${probe_details}" |
      sed -n 's/^helper_version=//p' | head -n 1
  )"
  if [[ ! "${helper_version}" =~ ^[0-9]+$ ]] ||
    ((helper_version < SENSOR_DEPLOYMENT_HELPER_VERSION)); then
    printf 'Updating the signed probe helper before the sensor rollout.\n'
    "${SCRIPT_DIR}/manage-probes.sh" helper-update "${username}" >/dev/null ||
      die "The probe helper could not be updated on ${username}"
    probe_details="$(printf 'probe-info\n' | managed_ssh "${username}")" ||
      die "The updated probe helper did not answer on ${username}"
    helper_version="$(
      printf '%s\n' "${probe_details}" |
        sed -n 's/^helper_version=//p' | head -n 1
    )"
    if [[ ! "${helper_version}" =~ ^[0-9]+$ ]] ||
      ((helper_version < SENSOR_DEPLOYMENT_HELPER_VERSION)); then
      die "The probe still reports an older helper after its signed update"
    fi
  fi

  if [[ -n "${managed_tool}" ]]; then
    platform="$(
      printf '%s\n' "${probe_details}" |
        sed -n 's/^platform=//p' | head -n 1
    )"
    [[ -n "${platform}" && "${platform}" != *unknown* ]] ||
      die "The probe reported no supported userspace platform"
    require_command docker
    tool_policy="$(docker compose --project-directory "${PROJECT_DIR}" run \
      --rm --no-deps -T web-api \
      python -m app.ops tool-policy \
      "${managed_tool}" "${platform}" "${managed_tool_version}" \
      "${managed_tool_fallback_min_version}")" ||
      die "No approved ${managed_tool} source is available for ${platform}"
    [[ "${tool_policy}" == "managed" || "${tool_policy}" == "system" ]] ||
      die "The tool policy returned an invalid source: ${tool_policy}"
  fi

  transaction_id="sensor-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  if [[ "${tool_policy}" == "managed" ]]; then
    exported_tool="$(mktemp)"
    tool_envelope="$(mktemp)"
    require_command docker
    # The release image owns the approved bytes, but its long-running web
    # service is optional. A one-off container reads the same runtime signing
    # key and exits after exporting the signed envelope.
    docker compose --project-directory "${PROJECT_DIR}" run \
      --rm --no-deps -T web-api \
      python -m app.ops export-tool \
      "${managed_tool}" "${platform}" "${managed_tool_version}" \
      > "${exported_tool}" || {
        rm -f -- "${exported_tool}" "${tool_envelope}"
        die "No approved ${managed_tool} artifact is available for ${platform}; update the stack image first"
      }
    signature="$(head -n 1 "${exported_tool}")"
    tail -n +2 "${exported_tool}" > "${tool_envelope}"
    {
      printf 'sensor-tool-stage\t%s\t%s\t%s\n' \
        "${transaction_id}" "${name}" "${signature}"
      cat "${tool_envelope}"
    } | managed_ssh "${username}" || {
      rm -f -- "${exported_tool}" "${tool_envelope}"
      printf 'sensor-rollback\t%s\n' "${transaction_id}" |
        managed_ssh "${username}" >/dev/null 2>&1 || true
      die "The probe rejected the managed tool for ${name}"
    }
    rm -f -- "${exported_tool}" "${tool_envelope}"
  fi
  for slot in script wrapper requirements; do
    source_path="$(sensor_slot_source "${directory}" "${slot}")"
    [[ -n "${source_path}" ]] || continue
    {
      printf 'sensor-stage\t%s\t%s\t%s\n' "${transaction_id}" "${name}" "${slot}"
      cat "${source_path}"
    } | managed_ssh "${username}" || {
      printf 'sensor-rollback\t%s\n' "${transaction_id}" |
        managed_ssh "${username}" >/dev/null 2>&1 || true
      die "The probe rejected the ${slot} of ${name}"
    }
  done
  {
    printf 'sensor-stage\t%s\t%s\tversion\n' "${transaction_id}" "${name}"
    printf '%s\n' "${version}"
  } | managed_ssh "${username}" || {
    printf 'sensor-rollback\t%s\n' "${transaction_id}" |
      managed_ssh "${username}" >/dev/null 2>&1 || true
    die "The probe rejected the version of ${name}"
  }

  if ! activation_answer="$(
    printf 'sensor-activate\t%s\n' "${transaction_id}" |
      managed_ssh "${username}" 2>&1
  )"; then
    printf 'sensor-rollback\t%s\n' "${transaction_id}" |
      managed_ssh "${username}" || true
    recovery_transaction="$(
      active_sensor_transaction_from_error "${activation_answer}" || true
    )"
    [[ -n "${recovery_transaction}" ]] ||
      recovery_transaction="${transaction_id}"
    [[ -z "${activation_answer}" ]] ||
      printf '%s\n' "${activation_answer}" >&2
    die "Sensor ${name} could not be activated on ${username}. If transaction ${recovery_transaction} remains active, run: sudo ./prtg-nats sensor recover ${name} ${username} --transaction ${recovery_transaction}"
  fi
  [[ -z "${activation_answer}" ]] || printf '%s\n' "${activation_answer}"
  if ! printf 'sensor-commit\t%s\n' "${transaction_id}" |
    managed_ssh "${username}" >/dev/null; then
    printf 'The first commit answer was lost; confirming transaction %s.\n' \
      "${transaction_id}" >&2
    if ! printf 'sensor-commit\t%s\n' "${transaction_id}" |
      managed_ssh "${username}" >/dev/null; then
      printf 'sensor-rollback\t%s\n' "${transaction_id}" |
        managed_ssh "${username}" >/dev/null 2>&1 || true
      die "Could not confirm sensor commit ${transaction_id} on ${username}; inspect sensor status before retrying"
    fi
  fi
  remember_assignment "${username}" "${name}"
  printf 'Deployed sensor %s (version %s) to %s.\n' \
    "${name}" "${version}" "${username}"
  deploy_sensor_iperf_servers "${directory}" "${username}" "${first_rollout}"
}

# A sensor that measures against an endpoint of its own is not a finished
# sensor without that endpoint's credentials -- it would only report
# "credentials-unreadable". They therefore belong in the same operation, not in
# a second command somebody has to remember.
#
# The rollout is deliberately afterwards and not part of the transaction: the
# sensor's self-test checks whether it can run, not whether it may measure. An
# endpoint that does not exist yet must not roll the sensor back.
#
# Which endpoints those are is the probe's own assignment, not the whole
# registry. A rollout that deployed everything would silently undo a revoke --
# the one operation whose entire point is that a probe stops holding
# credentials -- and spread every endpoint's password across every probe that
# happens to run the sensor.
#
# Only the first rollout to a probe seeds that assignment with every registered
# endpoint. Afterwards an empty assignment means what it says, rather than
# "nothing has happened here yet"; the two cannot be told apart from the
# sidecar alone, because it is deleted with its last entry.
deploy_sensor_iperf_servers() {
  local directory="$1"
  local username="$2"
  local first_rollout="$3"
  local kind=""
  local endpoints=()
  local assigned_servers=()
  local endpoint=""

  kind="$(sensor_manifest_value "${directory}" SENSOR_IPERF)"
  [[ -n "${kind}" ]] || return 0
  mapfile -t endpoints < <(registered_iperf_servers)
  if [[ "${#endpoints[@]}" -eq 0 ]]; then
    printf 'No %s endpoint is registered yet; the sensor cannot authenticate\n' \
      "${kind}"
    printf 'until one is set up:\n'
    printf '  ./prtg-nats iperf-server install ADMIN@HOST\n'
    return 0
  fi
  if [[ "${first_rollout}" != "true" ]]; then
    mapfile -t assigned_servers < <(assigned_iperf_servers "${username}")
    endpoints=()
    for endpoint in "${assigned_servers[@]}"; do
      # An assignment can outlive the endpoint it names - somebody forgot one
      # while a probe was unreachable. Deploying it would only fail.
      if registered_iperf_servers | grep -q -x -- "${endpoint}"; then
        endpoints+=("${endpoint}")
      fi
    done
    [[ "${#endpoints[@]}" -gt 0 ]] || return 0
  fi
  for endpoint in "${endpoints[@]}"; do
    "${SCRIPT_DIR}/manage-iperf-server.sh" deploy "${endpoint}" "${username}" ||
      printf 'WARNING: the credentials of %s did not reach %s; deploy them with\n         ./prtg-nats iperf-server deploy %s %s\n' \
        "${endpoint}" "${username}" "${endpoint}" "${username}" >&2
  done
}

deploy_to_all() {
  local name="$1"
  local username=""
  local failures=0

  for username in $(enrolled_probes); do
    if ! deploy_sensor "${name}" "${username}"; then
      failures=$((failures + 1))
    fi
  done
  [[ "${failures}" -eq 0 ]] ||
    die "Deployment failed on ${failures} probe(s)"
}

# Brings a probe to the state that sensors with dependencies need. Deployment
# does this by itself now; as a separate command the step can be done ahead of
# time and checked on its own, rather than being noticed inside a running
# transaction.
prepare_probe() {
  local username="$1"
  local answer=""

  [[ -f "$(probe_path "${username}")" ]] ||
    die "Probe is not enrolled: ${username}"
  if ! answer="$(printf 'sensor-prepare\n' | managed_ssh "${username}")"; then
    printf '%s: could not be prepared\n' "${username}" >&2
    return 1
  fi
  printf '%s: %s\n' "${username}" "${answer#OK sensor-prepared }"
}

prepare_all() {
  local username=""
  local failures=0

  for username in $(enrolled_probes); do
    if ! prepare_probe "${username}"; then
      failures=$((failures + 1))
    fi
  done
  [[ "${failures}" -eq 0 ]] ||
    die "Preparation failed on ${failures} probe(s)"
}

status_sensors() {
  local username="$1"

  printf 'Assigned in the inventory:\n'
  assigned_sensors "${username}" | sed 's/^/  /' || true
  printf '\nReported by the probe:\n'
  if ! printf 'sensor-list\n' | managed_ssh "${username}" | sed 's/^/  /'; then
    printf '  The restricted management connection is currently unavailable.\n'
  fi
}

# The fleet view, like "probe status --all". An unreachable probe is a line
# with a note, not an abort - otherwise one dead host hides all the rest.
status_all_sensors() {
  local username=""
  local assigned=""
  local probes=()

  mapfile -t probes < <(enrolled_probes)
  [[ "${#probes[@]}" -gt 0 ]] ||
    {
      printf 'No probes are enrolled.\n'
      return 0
    }
  printf '%-28s %s\n' "NATS USER" "ASSIGNED SENSORS"
  for username in "${probes[@]}"; do
    assigned="$(assigned_sensors "${username}" | paste -s -d ',' - || true)"
    printf '%-28s %s\n' "${username}" "${assigned:-—}"
  done
}

recover_sensor() {
  local name="$1"
  local username="$2"
  local transaction_id="$3"
  local answer=""

  validate_sensor_name "${name}" || die "Invalid sensor name: ${name}"
  validate_sensor_name "${transaction_id}" ||
    die "Invalid sensor transaction: ${transaction_id}"
  answer="$(
    printf 'sensor-recover\t%s\t%s\n' "${name}" "${transaction_id}" |
      managed_ssh "${username}"
  )" ||
    die "Could not recover sensor ${name} transaction ${transaction_id} on ${username}"
  printf '%s\n' "${answer}"
  if [[ "${answer}" == \
        "OK sensor-recovered ${name} transaction=${transaction_id} already-committed" ]]; then
    # The probe committed before its SSH answer was lost, so the normal deploy
    # path never reached its local bookkeeping. Recovery completes that half
    # too; a rolled-back or no-active transaction must not create an assignment.
    remember_assignment "${username}" "${name}"
  fi
}

remove_sensor() {
  local name="$1"
  local username="$2"
  local directory=""

  validate_sensor_name "${name}" || die "Invalid sensor name: ${name}"
  directory="$(sensor_directory "${name}")"
  printf 'sensor-remove\t%s\n' "${name}" | managed_ssh "${username}" ||
    die "The probe could not remove ${name}"
  forget_assignment "${username}" "${name}"
  # Along with the configuration directory the helper also clears the
  # endpoint credentials. If the bookkeeping here stayed behind, "endpoint
  # list" would report probes that have held nothing for a long time.
  [[ -z "$(sensor_manifest_value "${directory}" SENSOR_IPERF)" ]] ||
    rm -f -- "$(probe_iperf_path "${username}")"
  printf 'Removed sensor %s from %s.\n' "${name}" "${username}"
}

release_interface() {
  local name="$1"
  local username="$2"
  local interface="$3"

  validate_sensor_name "${name}" || die "Invalid sensor name: ${name}"
  [[ "${interface}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$ ]] ||
    die "Invalid interface name: ${interface}"
  printf 'sensor-release-interface\t%s\t%s\n' "${name}" "${interface}" |
    managed_ssh "${username}" ||
    die "The probe did not release ${interface}"
  printf 'Interface %s on %s is back under normal management.\n' \
    "${interface}" "${username}"
}

reserve_interface() {
  local name="$1"
  local username="$2"
  local interface="$3"

  validate_sensor_name "${name}" || die "Invalid sensor name: ${name}"
  [[ "${interface}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$ ]] ||
    die "Invalid interface name: ${interface}"
  printf 'sensor-reserve-interface\t%s\t%s\n' "${name}" "${interface}" |
    managed_ssh "${username}" ||
    die "The probe did not reserve ${interface}"
  printf 'Interface %s on %s is now reserved for sensor tests.\n' \
    "${interface}" "${username}"
}

# Credentials live centrally under runtime/sensor-profiles/ - in the same
# protected area as the NATS passwords and outside Git.
profile_source_path() {
  local name="$1"
  local profile="$2"

  printf '%s/%s/%s.env\n' "${SENSOR_PROFILE_DIR}" "${name}" "${profile}"
}

write_profile() {
  local name="$1"
  local username="$2"
  local profile="$3"
  local from_file="$4"
  local source_path=""

  validate_sensor_name "${name}" || die "Invalid sensor name: ${name}"
  validate_sensor_name "${profile}" || die "Invalid profile name: ${profile}"
  source_path="$(profile_source_path "${name}" "${profile}")"

  if [[ -n "${from_file}" ]]; then
    [[ -f "${from_file}" ]] || die "Profile file not found: ${from_file}"
    mkdir -p "$(dirname -- "${source_path}")"
    chmod 700 "${SENSOR_PROFILE_DIR}" "$(dirname -- "${source_path}")"
    install -m 600 "${from_file}" "${source_path}"
  fi
  [[ -f "${source_path}" ]] ||
    die "No stored profile ${profile} for ${name}; pass --from-file"

  printf 'Profile %s is stored under %s.\n' "${profile}" "${source_path}"
  {
    printf 'sensor-write-profile\t%s\t%s\n' "${name}" "${profile}"
    cat "${source_path}"
  } | managed_ssh "${username}" ||
    die "The probe rejected the profile ${profile}"
  printf 'Deployed profile %s of %s to %s.\n' "${profile}" "${name}" "${username}"
}

remove_profile() {
  local name="$1"
  local username="$2"
  local profile="$3"

  validate_sensor_name "${name}" || die "Invalid sensor name: ${name}"
  validate_sensor_name "${profile}" || die "Invalid profile name: ${profile}"
  printf 'sensor-remove-profile\t%s\t%s\n' "${name}" "${profile}" |
    managed_ssh "${username}" ||
    die "The probe could not remove the profile"
  printf 'Removed profile %s of %s from %s.\n' \
    "${profile}" "${name}" "${username}"
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
  list)
    [[ $# -eq 0 ]] || die "Usage: ./prtg-nats sensor list"
    list_sensors
    ;;
  show)
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats sensor show NAME"
    show_sensor "$1"
    ;;
  deploy)
    [[ $# -ge 2 ]] ||
      die "Usage: ./prtg-nats sensor deploy NAME USER|--all [--dry-run]"
    sensor_name="$1"
    target="$2"
    shift 2
    if [[ "${1:-}" == "--dry-run" ]]; then
      DRY_RUN="true"
      shift
    fi
    [[ $# -eq 0 ]] ||
      die "Usage: ./prtg-nats sensor deploy NAME USER|--all [--dry-run]"
    if [[ "${target}" == "--all" ]]; then
      deploy_to_all "${sensor_name}"
    else
      require_username "${target}"
      deploy_sensor "${sensor_name}" "${target}"
    fi
    ;;
  prepare)
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats sensor prepare USER|--all"
    if [[ "$1" == "--all" ]]; then
      prepare_all
    else
      require_username "$1"
      prepare_probe "$1"
    fi
    ;;
  status)
    [[ $# -eq 1 ]] || die "Usage: ./prtg-nats sensor status USER|--all"
    if [[ "$1" == "--all" ]]; then
      status_all_sensors
    else
      require_username "$1"
      status_sensors "$1"
    fi
    ;;
  recover)
    [[ $# -eq 4 && "$3" == "--transaction" ]] ||
      die "Usage: ./prtg-nats sensor recover NAME USER --transaction TRANSACTION"
    require_username "$2"
    recover_sensor "$1" "$2" "$4"
    ;;
  remove)
    [[ $# -eq 2 ]] || die "Usage: ./prtg-nats sensor remove NAME USER"
    require_username "$2"
    remove_sensor "$1" "$2"
    ;;
  reserve)
    [[ $# -eq 3 ]] ||
      die "Usage: ./prtg-nats sensor reserve NAME USER INTERFACE"
    require_username "$2"
    reserve_interface "$1" "$2" "$3"
    ;;
  release)
    [[ $# -eq 3 ]] ||
      die "Usage: ./prtg-nats sensor release NAME USER INTERFACE"
    require_username "$2"
    release_interface "$1" "$2" "$3"
    ;;
  profile)
    [[ $# -ge 3 ]] ||
      die "Usage: ./prtg-nats sensor profile NAME USER PROFILE [--from-file FILE]"
    sensor_name="$1"
    require_username "$2"
    probe_user="$2"
    profile_name="$3"
    shift 3
    profile_file=""
    case "${1:-}" in
      --from-file)
        [[ $# -ge 2 ]] || die "--from-file requires a path"
        profile_file="$2"
        shift 2
        ;;
      --remove)
        shift
        [[ $# -eq 0 ]] || die "--remove takes no further arguments"
        remove_profile "${sensor_name}" "${probe_user}" "${profile_name}"
        exit 0
        ;;
    esac
    [[ $# -eq 0 ]] ||
      die "Usage: ./prtg-nats sensor profile NAME USER PROFILE [--from-file FILE]"
    write_profile \
      "${sensor_name}" "${probe_user}" "${profile_name}" "${profile_file}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
