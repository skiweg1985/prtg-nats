#!/usr/bin/env bash

# Fast checks without Docker and without network access.
#
# Covers: shell syntax, the configuration template, rendering of the MPP
# configuration including value and structure validation. Runs in a few
# seconds and is therefore the first stage of the CI.

set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"

# The value checks use intervals like {0,63}. Bash 3.2, as macOS ships
# it, does not support them in [[ =~ ]] and would make every check fail
# falsely. The target platform is Linux with Bash 4 or newer.
if [[ "${BASH_VERSINFO[0]}" -lt 4 ]]; then
  printf 'These checks need Bash 4 or newer (found: %s).\n' \
    "${BASH_VERSION}" >&2
  printf 'On macOS, for example:\n' >&2
  printf '  docker run --rm -v "$PWD:/repo:ro" -w /repo debian:12 \\\n' >&2
  printf '    bash tests/check-static.sh\n' >&2
  exit 2
fi

passed=0
failed=0

check() {
  local description="$1"
  local actual="$2"
  local expected="$3"

  if [[ "${actual}" == "${expected}" ]]; then
    printf '  ok    %s\n' "${description}"
    passed=$((passed + 1))
  else
    printf '  FAIL  %s\n' "${description}" >&2
    printf '        expected: %s\n' "${expected}" >&2
    printf '        received: %s\n' "${actual}" >&2
    failed=$((failed + 1))
  fi
}

expect_failure() {
  local description="$1"
  shift

  if "$@" >/dev/null 2>&1; then
    printf '  FAIL  %s (was falsely accepted)\n' "${description}" >&2
    failed=$((failed + 1))
  else
    printf '  ok    %s\n' "${description}"
    passed=$((passed + 1))
  fi
}

printf '\n== Shell syntax ==\n'
while IFS= read -r script_path; do
  if bash -n "${script_path}" 2>/dev/null; then
    printf '  ok    %s\n' "${script_path}"
    passed=$((passed + 1))
  else
    printf '  FAIL  %s\n' "${script_path}" >&2
    bash -n "${script_path}" || true
    failed=$((failed + 1))
  fi
done < <(
  printf '%s\n' prtg-nats install-mpp.sh
  # sensors/ is included because a sensor can bring along shell scripts
  # that do not run on a probe - the endpoint setup script of
  # iperf-throughput, say. Unchecked, a typo in it only strikes whoever
  # runs it as root on a third-party machine.
  find libexec tests completions sensors web/updater -type f \
    \( -name '*.sh' -o -name '*.bash' -o -name 'prtg-nats-probe-helper' \) |
    sort
)

if command -v shellcheck >/dev/null 2>&1; then
  printf '\n== shellcheck ==\n'
  if shellcheck --severity=warning \
    prtg-nats install-mpp.sh libexec/*.sh libexec/prtg-nats-probe-helper \
    libexec/prtg-nats-iperf-helper \
    tests/*.sh completions/*.bash sensors/*/endpoint/*.sh \
    web/updater/*.sh; then
    printf '  ok    no warnings\n'
    passed=$((passed + 1))
  else
    printf '  FAIL  shellcheck reports warnings\n' >&2
    failed=$((failed + 1))
  fi
else
  printf '\n== shellcheck ==\n  skipped (not installed)\n'
fi

# The shell tooling names things that live in other files: helper scripts under
# libexec/ and services in compose.yaml. Nothing fails until the command is
# actually run, which is how a call to the deleted manage-users.sh and a start
# of the removed ca-download service both survived in here unnoticed.
printf '\n== Names the tooling refers to ==\n'

while IFS= read -r script_name; do
  [[ -n "${script_name}" ]] || continue
  check "libexec/${script_name} exists" \
    "$([[ -f "${PROJECT_DIR}/libexec/${script_name}" ]] && printf 'yes' ||
      printf 'no')" \
    "yes"
done < <(
  grep -hoE 'run_internal [a-z-]+\.sh' \
    "${PROJECT_DIR}/prtg-nats" "${PROJECT_DIR}"/libexec/*.sh 2>/dev/null |
    awk '{ print $2 }' | sort -u
)

compose_services="$(
  awk '
    /^services:/ { in_services = 1; next }
    /^[^ ]/ { in_services = 0 }
    in_services && /^  [a-z][a-z0-9_-]*:[[:space:]]*$/ {
      sub(/:.*/, ""); sub(/^ +/, ""); print
    }
  ' "${PROJECT_DIR}/compose.yaml"
)"
compose_containers="$(
  awk '$1 == "container_name:" { print $2 }' "${PROJECT_DIR}/compose.yaml"
)"

# "wait_until_healthy CONTAINER SERVICE" names one of each.
while read -r container_name service_name; do
  [[ -n "${container_name}" ]] || continue
  check "container ${container_name} is defined in compose.yaml" \
    "$(printf '%s\n' "${compose_containers}" | grep -Fxc "${container_name}" ||
      true)" \
    "1"
  check "service ${service_name} is defined in compose.yaml" \
    "$(printf '%s\n' "${compose_services}" | grep -Fxc "${service_name}" ||
      true)" \
    "1"
done < <(
  grep -hoE 'wait_until_healthy [a-z0-9-]+ [a-z0-9-]+' "${PROJECT_DIR}/prtg-nats" |
    sed 's/^wait_until_healthy //' | sort -u
)

# A service that is built and also carries a fixed image name is one compose
# will try to pull, because the name looks like a registry reference. There is
# no registry here, the pull fails, and it fails the whole command - which is
# how `prtg-nats update` stopped working on an installation that had just been
# told to update itself. pull_policy: build is what says "never pull this".
#
# Only services with both matter: the ones with build and no image are named
# after the project and compose never tries to fetch them.
while IFS= read -r service_name; do
  [[ -n "${service_name}" ]] || continue
  service_block="$(
    awk -v want="  ${service_name}:" '
      $0 == want { inside = 1; next }
      inside && /^  [a-z]/ { exit }
      inside { print }
    ' "${PROJECT_DIR}/compose.yaml"
  )"
  printf '%s\n' "${service_block}" | grep -q '^    image:' || continue
  printf '%s\n' "${service_block}" | grep -q '^    build:' ||
    printf '%s\n' "${service_block}" | grep -q '^      context:' || continue
  check "built service ${service_name} with a fixed image name says pull_policy: build" \
    "$(printf '%s\n' "${service_block}" | grep -c '^    pull_policy: build' ||
      true)" \
    "1"
done < <(printf '%s\n' "${compose_services}")

while IFS= read -r service_name; do
  [[ -n "${service_name}" ]] || continue
  check "restarted service ${service_name} is defined in compose.yaml" \
    "$(printf '%s\n' "${compose_services}" | grep -Fxc "${service_name}" ||
      true)" \
    "1"
done < <(
  grep -hoE '^[[:space:]]*compose restart .*$' "${PROJECT_DIR}/prtg-nats" |
    sed 's/^[[:space:]]*compose restart //' | tr ' ' '\n' | sort -u
)

check "no compose file other than compose.yaml is referenced" \
  "$(grep -c 'compose\.[a-z]*\.yaml' "${PROJECT_DIR}/prtg-nats" || true)" \
  "0"

printf '\n== Configuration template ==\n'
# shellcheck source=../libexec/mpp-config.sh
source "${PROJECT_DIR}/libexec/mpp-config.sh"
template="$(mpp_config_template_path)"
check "template found" "$(basename "${template}")" \
  "mpprobe-config.yaml.template"
for placeholder in "${MPP_CONFIG_PLACEHOLDERS[@]}"; do
  if grep -F "@@${placeholder}@@" "${template}" >/dev/null; then
    printf '  ok    placeholder @@%s@@ present\n' "${placeholder}"
    passed=$((passed + 1))
  else
    printf '  FAIL  placeholder @@%s@@ missing from the template\n' \
      "${placeholder}" >&2
    failed=$((failed + 1))
  fi
done

printf '\n== Rendering ==\n'
# shellcheck disable=SC2034  # read dynamically by mpp_render_config
set_valid_values() {
  MPP_PROBE_ID="$(mpp_generate_uuid)"
  MPP_ACCESS_KEY="$(mpp_default_access_key probe-test)"
  MPP_PROBE_NAME="$(mpp_default_probe_name probe-test)"
  MPP_NATS_HOST="nats.example.test"
  MPP_NATS_PORT="23561"
  MPP_NATS_USER="mpp-probe-test"
  MPP_NATS_PASSWORD="$(
    printf '0123456789abcdef%.0s' 1 2 3 4
  )"
  MPP_SERVER_CA="/etc/paessler/mpprobe/certs/nats-docker-ca.pem"
  MPP_CLIENT_NAME="prtgmpprobe"
}

rendered="$(mktemp)"
trap 'rm -f -- "${rendered}"' EXIT
set_valid_values
mpp_render_config > "${rendered}"

check "probe id carried over" \
  "$(mpp_read_config_field "${rendered}" id)" "${MPP_PROBE_ID}"
check "access key carried over" \
  "$(mpp_read_config_field "${rendered}" access_key)" "${MPP_ACCESS_KEY}"
check "probe name carried over" \
  "$(mpp_read_config_field "${rendered}" name)" "${MPP_PROBE_NAME}"
check "NATS user carried over" \
  "$(mpp_read_config_nats_user "${rendered}")" "${MPP_NATS_USER}"
check "NATS URL complete" \
  "$(awk '/^[[:space:]]+url:/ { print $2 }' "${rendered}")" \
  "tls://${MPP_NATS_HOST}:${MPP_NATS_PORT}"
check "no placeholders left" \
  "$(grep -c '@@' "${rendered}" || true)" "0"

if mpp_validate_rendered_config "${rendered}" 2>/dev/null; then
  printf '  ok    structure check accepts a valid configuration\n'
  passed=$((passed + 1))
else
  printf '  FAIL  structure check rejects a valid configuration\n' >&2
  failed=$((failed + 1))
fi

printf '\n== Value checks reject the invalid ==\n'
reject_value() {
  local description="$1"
  local variable="$2"
  local value="$3"

  (
    set_valid_values
    printf -v "${variable}" '%s' "${value}"
    mpp_validate_values
  ) >/dev/null 2>&1 &&
    {
      printf '  FAIL  %s\n' "${description}" >&2
      failed=$((failed + 1))
      return 0
    }
  printf '  ok    %s\n' "${description}"
  passed=$((passed + 1))
}

reject_value "probe id without UUID shape" MPP_PROBE_ID "not-a-uuid"
reject_value "password with spaces" MPP_NATS_PASSWORD "with spaces"
reject_value "probe name starting with @" MPP_PROBE_NAME "@invalid"
reject_value "relative CA path" MPP_SERVER_CA "relative/ca.pem"
reject_value "port out of range" MPP_NATS_PORT "99999"
reject_value "port not numeric" MPP_NATS_PORT "notaport"
reject_value "empty NATS user" MPP_NATS_USER ""

printf '\n== Structure check rejects the incomplete ==\n'
incomplete="$(mktemp)"
printf 'incomplete: true\n' > "${incomplete}"
expect_failure "configuration without required fields" \
  mpp_validate_rendered_config "${incomplete}"
printf 'id: x\na\001b\n' > "${incomplete}"
expect_failure "configuration with control characters" \
  mpp_is_free_of_control_characters "${incomplete}"
rm -f -- "${incomplete}"

printf '\n== Sensors ==\n'
# The individual messages come from tests/sensor-checks.py; only the
# overall result counts here.
if command -v python3 >/dev/null 2>&1; then
  if python3 "${PROJECT_DIR}/tests/sensor-checks.py"; then
    passed=$((passed + 1))
  else
    printf '  FAIL  sensor checks report failures\n' >&2
    failed=$((failed + 1))
  fi
else
  printf '  skipped (python3 not installed)\n'
fi

printf '\n== No internal identifiers ==\n'
# Site names and company identity may appear neither in code nor docs.
#
# The concrete names deliberately do NOT appear here. A deny list in a
# published repository publishes exactly what it is meant to hide - whoever
# reads the check learns the host scheme, the domain and the management
# network. Two layers replace it:
#
#   1. the structural rules below, which need no knowledge of any name;
#   2. an optional local pattern file for the real names of the environment
#      this grew out of. It is ignored by Git and therefore only present on
#      a machine that already knows them.
#
# If a name ever leaks, the fix belongs in the local file - not here.
internal_scan_excludes=(
  --exclude-dir=.git --exclude-dir=runtime --exclude-dir=backups
  --exclude-dir=.backup --exclude-dir=node_modules --exclude-dir=dist
  --exclude-dir=.venv --exclude-dir=__pycache__
  # Paessler ships these; "prtg.standardlookups.lan" is a file name, not a host.
  --exclude-dir=lookups
  # Local agent scratch, both Git-ignored and never published. A shared
  # .claude/settings.json stays in scope on purpose.
  --exclude-dir=worktrees --exclude=settings.local.json
  --exclude=internal-identifiers.patterns
)
internal_pattern_file="${PROJECT_DIR}/tests/internal-identifiers.patterns"

internal_hits="$(
  {
    # Address ranges typical of a corporate network. The documentation uses
    # 192.0.2.0/24 (RFC 5737). 10.x stays allowed on purpose: the sensor
    # READMEs use it for site examples such as "--internal-target HQ=10.0.0.10".
    grep -rn -E \
      '(172\.(1[6-9]|2[0-9]|3[01])|192\.168)\.[0-9]{1,3}\.[0-9]{1,3}' \
      "${internal_scan_excludes[@]}" . 2>/dev/null || true
    # Host suffixes that only resolve inside a network. "internal" is left out
    # deliberately - it collides with the i18n key errors.internal.unexpected.
    grep -rn -iE '[a-z0-9-]+\.(local|intern|corp|lan)([^a-z0-9-]|$)' \
      "${internal_scan_excludes[@]}" . 2>/dev/null || true
    # One pattern per line. Blank lines and comments are stripped first: to
    # grep -f an empty line is a pattern that matches everything, which would
    # turn the whole check into noise.
    if [[ -s "${internal_pattern_file}" ]]; then
      grep -rn -i -f <(
        grep -v -e '^[[:space:]]*$' -e '^[[:space:]]*#' \
          "${internal_pattern_file}"
      ) "${internal_scan_excludes[@]}" . 2>/dev/null || true
    fi
  } |
    # The check must not trip over its own patterns.
    grep -v '^./tests/check-static.sh:' |
    sort -u || true
)"
if [[ -z "${internal_hits}" ]]; then
  printf '  ok    no internal names or addresses in the repository\n'
  passed=$((passed + 1))
else
  printf '  FAIL  internal identifiers found:\n' >&2
  printf '%s\n' "${internal_hits}" | sed 's/^/        /' >&2
  failed=$((failed + 1))
fi
if [[ -s "${internal_pattern_file}" ]]; then
  printf '  ok    local pattern file applied\n'
  passed=$((passed + 1))
else
  printf '  note  no local pattern file (%s); structural rules only\n' \
    "tests/internal-identifiers.patterns"
fi

printf '\n== Setup dialog ==\n'
if command -v expect >/dev/null 2>&1; then
  configure_dir="$(mktemp -d)"
  # Throwaway copy, so the own .env stays untouched.
  tar -C "${PROJECT_DIR}" --exclude=.git --exclude=runtime -cf - \
    prtg-nats libexec config .env.example 2>/dev/null |
    tar -C "${configure_dir}" -xf -
  cat > "${configure_dir}/dialog.exp" <<'EXPECT_SCRIPT'
set timeout 30
spawn ./prtg-nats config --edit
expect -re "FQDN.*: "             { send "nats.example.test\r" }
expect -re "container ports.*: "  { send "999.999.999.999\r" }
expect -re "container ports.*: "  { send "192.0.2.10\r" }
expect -re "PRTG core.*: "        { send "192.0.2.20\r" }
expect -re "NATS port.*: "        { send "\r" }
expect -re "CA download.*: "      { send "\r" }
expect -re "Organisation.*: "     { send "\r" }
expect -re "Apply.*: "            { send "y\r" }
expect eof
catch wait result
exit [lindex $result 3]
EXPECT_SCRIPT
  if (cd "${configure_dir}" && expect dialog.exp) >/dev/null 2>&1; then
    check "produces NATS_FQDN" \
      "$(grep '^NATS_FQDN=' "${configure_dir}/.env" || true)" \
      "NATS_FQDN=nats.example.test"
    check "rejects an invalid IP and asks again" \
      "$(grep '^NATS_HOST_IP=' "${configure_dir}/.env" || true)" \
      "NATS_HOST_IP=192.0.2.10"
    check "derives the SSH source address" \
      "$(grep '^MPP_SSH_SOURCE_CIDR=' "${configure_dir}/.env" || true)" \
      "MPP_SSH_SOURCE_CIDR=192.0.2.10/32"
    check "writes .env readable for the owner only" \
      "$(stat -c '%a' "${configure_dir}/.env" 2>/dev/null ||
        stat -f '%Lp' "${configure_dir}/.env")" "600"
  else
    printf '  FAIL  the setup dialog failed\n' >&2
    failed=$((failed + 1))
  fi
  expect_failure "aborts without a terminal" \
    bash -c "cd '${configure_dir}' && ./prtg-nats config --edit < /dev/null"
  rm -rf -- "${configure_dir}"
else
  printf '  skipped (expect not installed)\n'
fi

printf '\n== Completion ==\n'

# The suggestion lists are a second truth about the commands. So they do
# not go stale, they are checked against the dispatchers' actual branches:
# internal verbs and help aliases stay out.
dispatch_commands() {
  awk "/^case \"\\\$\{command_name/,/^esac/" "$1" |
    grep -oE '^  [a-z][a-z0-9|-]*\)' |
    tr -d ' )' |
    tr '|' '\n' |
    grep -vE '^(internal-|-h$|--help$)' |
    sort -u |
    tr '\n' ' '
}

completion_list() {
  # shellcheck disable=SC1091  # path is only known at run time
  source ./completions/prtg-nats.bash
  printf '%s' "${!1}" | tr -s ' \n' '\n' | sort -u | tr '\n' ' '
}

# The grace-period stubs for retired names are gone, so the completion list
# and the dispatcher have to agree exactly.
check "the command list matches the dispatcher" \
  "$(completion_list _prtg_nats_commands)" \
  "$(dispatch_commands ./prtg-nats)"
check "probe subcommands match" \
  "$(completion_list _prtg_nats_probe_commands)" \
  "$(dispatch_commands ./libexec/manage-probes.sh)"
check "sensor subcommands match" \
  "$(completion_list _prtg_nats_sensor_commands)" \
  "$(dispatch_commands ./libexec/manage-sensors.sh)"
check "iperf-server subcommands match" \
  "$(completion_list _prtg_nats_iperf_server_commands)" \
  "$(dispatch_commands ./libexec/manage-iperf-server.sh)"
check "overlay subcommands match" \
  "$(completion_list _prtg_nats_overlay_commands)" \
  "$(dispatch_commands ./libexec/manage-overlay.sh)"

check "the completion command delivers the file" \
  "$(./prtg-nats completion bash | cmp -s - completions/prtg-nats.bash &&
    printf 'same')" "same"
expect_failure "completion with an unknown shell" \
  ./prtg-nats completion fish
expect_failure "self without a known subcommand" \
  ./prtg-nats self nonsense
expect_failure "self install with an unknown option" \
  ./prtg-nats self install --something-else

# Help has to work without a configured .env - otherwise it is
# unreachable exactly when it is needed most.
check "self without an argument shows the help" \
  "$(./prtg-nats self | grep -c 'self install')" "1"
check "probe without an argument shows the help" \
  "$(./prtg-nats probe | grep -c '^Usage:')" "1"
check "user --help shows the help" \
  "$(./prtg-nats user --help | grep -c '^Usage:')" "1"
check "sensor without an argument shows the help" \
  "$(./prtg-nats sensor | grep -c '^Usage:')" "1"
check "overlay without an argument shows the help" \
  "$(./prtg-nats overlay | grep -c '^Usage:')" "1"
# The three modes are the feature. A help text that stops naming one of them
# is a mode nobody finds, and the enable path validates against the same set.
check "the overlay help names every mode" \
  "$(./prtg-nats overlay | grep -cE '^  (off|auto|on) ')" "3"
check "the entry point offers the overlay" \
  "$(./prtg-nats help | grep -c '^  overlay \.\.\.')" "1"

# Every reservation has to be revocable without tearing down the whole
# sensor - otherwise there is no way back from a wrong pick.
check "reserve has a counterpart" \
  "$(./prtg-nats sensor | grep -c 'sensor release NAME USER INTERFACE')" "1"
check "the probe knows the release verb" \
  "$(grep -c 'sensor-release-interface)' libexec/prtg-nats-probe-helper)" "1"
check "sensor deployment requires the helper version that is shipped" \
  "$(sed -n 's/^SENSOR_DEPLOYMENT_HELPER_VERSION=//p' \
    libexec/manage-sensors.sh)" \
  "$(sed -n 's/^HELPER_VERSION=//p' libexec/prtg-nats-probe-helper)"

# The version the shipped helper declares. The fixtures below derive their
# "before" and "after" from it, so a version bump changes one line in the
# helper and nothing here.
SHIPPED_HELPER_VERSION="$(
  sed -n 's/^HELPER_VERSION=//p' libexec/prtg-nats-probe-helper
)"

regular_sensor_helper_update_scenario() (
  local mode="$1"
  local fixture=""
  local sensor_args=(deploy dns-check mpp-test)

  fixture="$(mktemp -d)"
  trap 'rm -rf -- "${fixture}"' EXIT
  mkdir -p \
    "${fixture}/bin" \
    "${fixture}/libexec" \
    "${fixture}/runtime/private/ssh" \
    "${fixture}/runtime/probes" \
    "${fixture}/sensors/dns-check/script"
  cp \
    libexec/manage-sensors.sh \
    libexec/common.sh \
    libexec/mpp-config.sh \
    libexec/runtime-dir.sh \
    "${fixture}/libexec/"
  : > "${fixture}/runtime/private/ssh/prtg-nats-mpp-admin"
  : > "${fixture}/runtime/private/ssh/prtg-nats-mpp-admin.pub"
  cat > "${fixture}/.env" <<'EOF'
NATS_FQDN=nats.example.test
NATS_HOST_IP=192.0.2.10
EOF
  cat > "${fixture}/runtime/probes/mpp-test.env" <<'EOF'
SSH_HOST=probe.example.test
EOF
  cat > "${fixture}/sensors/dns-check/manifest.env" <<'EOF'
SENSOR_VERSION=1
SENSOR_DESCRIPTION=DNS check
SENSOR_SCRIPT=script/dns-check.py
EOF
  cat > "${fixture}/sensors/dns-check/script/dns-check.py" <<'EOF'
print('{"prtg": {"result": []}}')
EOF
  cat > "${fixture}/bin/ssh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
request=""
IFS= read -r request || true
cat >/dev/null || true
command_name="${request%%$'\t'*}"
printf '%s\n' "${command_name}" >> "${FAKE_HELPER_LOG}"
case "${command_name}" in
  probe-info)
    # Derived from the helper that is actually shipped, not written down: a
    # version bump used to break this fixture rather than the thing it tests.
    if [[ -f "${FAKE_HELPER_UPDATED}" ]]; then
      printf 'OK probe-info\nhelper_version=%s\n' "${FAKE_HELPER_VERSION}"
    else
      printf 'OK probe-info\nhelper_version=%s\n' \
        "$((FAKE_HELPER_VERSION - 1))"
    fi
    ;;
  sensor-activate)
    if [[ "${FAKE_BLOCK_ACTIVE:-}" == "yes" ]]; then
      printf 'ERROR: Sensor dns-check has an active transaction\n' >&2
      printf 'active_transaction=tx-old\n' >&2
      exit 1
    fi
    printf 'OK sensor-activate\n'
    ;;
  sensor-recover)
    printf 'OK sensor-recovered dns-check transaction=tx-old already-committed\n'
    ;;
  *)
    printf 'OK %s\n' "${command_name}"
    ;;
esac
EOF
  cat > "${fixture}/bin/ssh-keygen" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat > "${fixture}/libexec/manage-probes.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$1" == "helper-update" && "$2" == "mpp-test" ]]
printf 'helper-update\n' >> "${FAKE_HELPER_LOG}"
: > "${FAKE_HELPER_UPDATED}"
EOF
  chmod +x \
    "${fixture}/bin/ssh" \
    "${fixture}/bin/ssh-keygen" \
    "${fixture}/libexec/manage-probes.sh"

  case "${mode}" in
    dry-run) sensor_args+=(--dry-run) ;;
    blocked-active) : ;;
    recover)
      sensor_args=(recover dns-check mpp-test --transaction tx-old)
      ;;
    recover-without-transaction)
      sensor_args=(recover dns-check mpp-test)
      ;;
  esac
  if [[ "${mode}" == "recover-without-transaction" ]]; then
    if PRTG_NATS_RUNTIME_DIR="${fixture}/runtime" \
      FAKE_HELPER_LOG="${fixture}/helper.log" \
      FAKE_HELPER_UPDATED="${fixture}/helper.updated" \
      FAKE_HELPER_VERSION="${SHIPPED_HELPER_VERSION}" \
      PATH="${fixture}/bin:${PATH}" \
      bash "${fixture}/libexec/manage-sensors.sh" \
      "${sensor_args[@]}" >/dev/null 2>&1; then
      printf 'accepted\n'
    elif [[ ! -s "${fixture}/helper.log" ]]; then
      printf 'rejected-before-remote\n'
    else
      printf 'rejected-after-remote\n'
    fi
    return 0
  fi
  if [[ "${mode}" == "blocked-active" ]]; then
    local blocked_output=""
    local expected_recovery="sudo ./prtg-nats sensor recover dns-check mpp-test --transaction tx-old"
    if blocked_output="$(
      PRTG_NATS_RUNTIME_DIR="${fixture}/runtime" \
        FAKE_HELPER_LOG="${fixture}/helper.log" \
        FAKE_HELPER_UPDATED="${fixture}/helper.updated" \
        FAKE_HELPER_VERSION="${SHIPPED_HELPER_VERSION}" \
        FAKE_BLOCK_ACTIVE=yes \
        PATH="${fixture}/bin:${PATH}" \
        bash "${fixture}/libexec/manage-sensors.sh" \
        "${sensor_args[@]}" 2>&1
    )"; then
      printf 'accepted\n'
    elif [[ "${blocked_output}" == *"${expected_recovery}"* &&
            "${blocked_output}" != *"--transaction sensor-"* ]]; then
      printf 'blocked-with-active-transaction\n'
    else
      printf '%s\n' "${blocked_output}"
    fi
    return 0
  fi
  PRTG_NATS_RUNTIME_DIR="${fixture}/runtime" \
    FAKE_HELPER_LOG="${fixture}/helper.log" \
    FAKE_HELPER_UPDATED="${fixture}/helper.updated" \
    FAKE_HELPER_VERSION="${SHIPPED_HELPER_VERSION}" \
    PATH="${fixture}/bin:${PATH}" \
    bash "${fixture}/libexec/manage-sensors.sh" \
    "${sensor_args[@]}" >/dev/null

  if [[ "${mode}" == "dry-run" ]]; then
    [[ ! -s "${fixture}/helper.log" ]] && printf 'no-remote-command\n'
  elif [[ "${mode}" == "recover" ]]; then
    paste -s -d ',' "${fixture}/helper.log" | tr -d '\n'
    if grep -q -x 'dns-check' \
      "${fixture}/runtime/probes/mpp-test.sensors"; then
      printf ',assigned\n'
    else
      printf ',unassigned\n'
    fi
  else
    paste -s -d ',' "${fixture}/helper.log"
  fi
)

check "a regular sensor updates an old helper before its transaction" \
  "$(regular_sensor_helper_update_scenario deploy)" \
  "probe-info,helper-update,probe-info,sensor-stage,sensor-stage,sensor-activate,sensor-commit"
check "a regular sensor dry-run does not update or contact the helper" \
  "$(regular_sensor_helper_update_scenario dry-run)" "no-remote-command"
check "a blocked deploy reports the previous active transaction for recovery" \
  "$(regular_sensor_helper_update_scenario blocked-active)" \
  "blocked-with-active-transaction"
check "sensor recovery sends the sensor and required transaction together" \
  "$(regular_sensor_helper_update_scenario recover)" \
  "sensor-recover,assigned"
check "sensor recovery rejects a missing transaction before SSH" \
  "$(regular_sensor_helper_update_scenario recover-without-transaction)" \
  "rejected-before-remote"
check "sensor recovery help requires the expected transaction" \
  "$(./prtg-nats sensor --help | grep -c \
    'sensor recover NAME USER --transaction TRANSACTION')" "1"

# iPerf is release-owned, not whatever version the distribution happens to
# provide. The helper's fixed sensor-to-tool map is the privileged boundary;
# a manifest cannot name a package or a target path.
check "the iperf sensor declares its managed tool" \
  "$(grep -c '^SENSOR_TOOL=iperf3$' sensors/iperf-throughput/manifest.env)" "1"
iperf_release_version="$(
  sed -n 's/^IPERF_VERSION=//p' tools/iperf3/versions.env
)"
iperf_system_min_version="$(
  sed -n 's/^IPERF_SYSTEM_MIN_VERSION=//p' tools/iperf3/versions.env
)"
check "the sensor manifest pins the built iperf release" \
  "$(sed -n 's/^SENSOR_TOOL_VERSION=//p' \
    sensors/iperf-throughput/manifest.env)" "${iperf_release_version}"
check "the sensor self-check pins the built iperf release" \
  "$(sed -n 's/^APPROVED_IPERF_VERSION = "\([^"]*\)"/\1/p' \
    sensors/iperf-throughput/script/iperf-throughput.py)" \
  "${iperf_release_version}"
check "the sensor manifest pins the system fallback minimum" \
  "$(sed -n 's/^SENSOR_TOOL_FALLBACK_MIN_VERSION=//p' \
    sensors/iperf-throughput/manifest.env)" "${iperf_system_min_version}"
check "the sensor self-check pins the system fallback minimum" \
  "$(sed -n 's/^SYSTEM_IPERF_MIN_VERSION = "\([^"]*\)"/\1/p' \
    sensors/iperf-throughput/script/iperf-throughput.py)" \
  "${iperf_system_min_version}"
check "the probe helper pins the system fallback minimum" \
  "$(sed -n 's/^SYSTEM_IPERF_MIN_VERSION="\([^"]*\)"/\1/p' \
    libexec/prtg-nats-probe-helper)" "${iperf_system_min_version}"
check "the probe helper fixes the system iperf path in its own code" \
  "$(sed -n 's/^SYSTEM_IPERF_PATH="\([^"]*\)"/\1/p' \
    libexec/prtg-nats-probe-helper)" "/usr/bin/iperf3"
check "the fixed production iperf path is not duplicated at call sites" \
  "$(grep -c '/usr/bin/iperf3' libexec/prtg-nats-probe-helper)" "1"
check "the probe maps the iperf sensor to one fixed tool" \
  "$(grep -c 'managed_tool_for_sensor' libexec/prtg-nats-probe-helper)" "5"
check "the sensor uses only the managed current link" \
  "$(grep -c '/opt/prtg-nats/tools/iperf3/current/iperf3' \
    sensors/iperf-throughput/script/iperf-throughput.py)" "1"
check "the helper never installs iperf from a distribution" \
  "$(grep -c "printf 'iperf3 iperf3" libexec/prtg-nats-probe-helper)" "0"
check "no manifest names a system package" \
  "$(grep -h 'SENSOR_PACKAGES' sensors/*/manifest.env 2>/dev/null | wc -l | tr -d ' ')" "0"

reported_userspace_architecture() {
  local package_architecture="$1"
  (
    # shellcheck disable=SC1090
    source <(sed -n '/^probe_userspace_architecture()/,/^}/p' \
      libexec/prtg-nats-probe-helper)
    dpkg() {
      [[ "$1" == '--print-architecture' ]] || return 1
      printf '%s\n' "${package_architecture}"
    }
    # This is intentionally a 64-bit kernel in every case. armhf must still
    # win when that is what the package database says the loader can run.
    uname() { printf 'aarch64\n'; }
    getconf() { printf '64\n'; }
    probe_userspace_architecture
  )
}

check "userspace detection recognises amd64" \
  "$(reported_userspace_architecture amd64)" "amd64"
check "userspace detection recognises arm64" \
  "$(reported_userspace_architecture arm64)" "arm64"
check "a 64-bit ARM kernel does not hide an armhf userspace" \
  "$(reported_userspace_architecture armhf)" "armhf"

forged_tool_stage_leftovers() {
  local sandbox=""
  local error_log=""
  local rejection="wrong-path"
  local leftovers=""
  sandbox="$(mktemp -d)"
  error_log="${sandbox}/error.log"
  mkdir -p \
    "${sandbox}/state/transactions" \
    "${sandbox}/state/sensor-transactions/committed"
  printf 'test public key\n' > "${sandbox}/public.pem"
  (
    # shellcheck disable=SC2034  # read by the dynamically sourced helper
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    SENSOR_COMMIT_TOMBSTONE_DIR="${SENSOR_TRANSACTION_STATE_DIR}/committed"
    # shellcheck disable=SC2034  # read by stage_sensor_tool after sourcing
    HELPER_SIGNING_KEY="${sandbox}/public.pem"
    # shellcheck disable=SC2034  # read by stage_sensor_tool after sourcing
    MAXIMUM_TOOL_ENVELOPE_BYTES=16777216
    # shellcheck disable=SC2034  # read by stage_sensor_tool after sourcing
    MAXIMUM_TOOL_BYTES=12582912
    SIGNATURE_CHECK_MARKER="${sandbox}/signature-checked"
    # shellcheck disable=SC1090
    source <(sed -n '/^die()/,/^}/p
      /^validate_token()/,/^}/p
      /^is_free_of_control_characters()/,/^}/p
      /^read_limited_input()/,/^}/p
      /^sensor_commit_tombstone_path()/,/^}/p
      /^committed_sensor_transaction_name()/,/^}/p
      /^reject_committed_sensor_transaction_id()/,/^}/p
      /^probe_userspace_architecture()/,/^}/p
      /^probe_libc()/,/^}/p
      /^probe_platform()/,/^}/p
      /^managed_tool_for_sensor()/,/^}/p
      /^validate_managed_tool_binary()/,/^}/p
      /^stage_sensor_tool()/,/^}/p' libexec/prtg-nats-probe-helper)
    openssl() {
      local output=""

      case "$1" in
        base64)
          while [[ "$#" -gt 0 ]]; do
            if [[ "$1" == "-out" ]]; then
              output="$2"
              break
            fi
            shift
          done
          [[ -n "${output}" ]] || return 1
          cat > "${output}"
          ;;
        dgst)
          : > "${SIGNATURE_CHECK_MARKER}"
          return 1
          ;;
        *)
          return 1
          ;;
      esac
    }
    printf 'forged envelope\n' |
      stage_sensor_tool forged iperf-throughput AAAA
  ) >/dev/null 2> "${error_log}" || true
  if [[ -f "${sandbox}/signature-checked" ]] && grep -q -x \
    'ERROR: sensor-tool-stage signature does not match this probe'\''s signing key' \
    "${error_log}"; then
    rejection="signature-rejected"
  else
    rejection="$(tail -n 1 "${error_log}")"
  fi
  leftovers="$({
    find "${sandbox}/state" -mindepth 1 -maxdepth 1 -print |
      grep -Ev '/(transactions|sensor-transactions)$' || true
    find "${sandbox}/state/transactions" -mindepth 1 -print || true
    find "${sandbox}/state/sensor-transactions" \
      -mindepth 1 -maxdepth 1 -print |
      grep -v '/committed$' || true
    find "${sandbox}/state/sensor-transactions/committed" \
      -mindepth 1 -print || true
  } | wc -l | tr -d '[:space:]')"
  printf '%s %s\n' "${rejection}" "${leftovers}"
  rm -rf -- "${sandbox}"
}

check "a forged tool reaches signature verification and leaves no payload" \
  "$(forged_tool_stage_leftovers)" "signature-rejected 0"

system_fallback_boundary_scenario() {
  local scenario="$1"
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    SENSOR_TOOL_ROOT="${sandbox}/tools"
    SENSOR_CONFIG_ROOT="${sandbox}/config"
    TEST_PLATFORM="linux-armhf-v6-glibc"
    local transaction="${sandbox}/transaction"
    local fake_root="${sandbox}/usr/bin"
    local fake_path="${fake_root}/iperf3"
    local version="3.18"
    local mode="0755"
    local authentication="yes"
    mkdir -p "${transaction}" "${fake_root}"

    # shellcheck disable=SC1090  # process substitution selects shipped code
    source <(sed -n '/^die()/,/^}/p
      /^managed_tool_for_sensor()/,/^}/p
      /^managed_tool_binary_name()/,/^}/p
      /^managed_tool_current_link()/,/^}/p
      /^managed_tool_metadata_path()/,/^}/p
      /^managed_tool_has_release_artifact()/,/^}/p
      /^version_at_least()/,/^}/p
      /^iperf_binary_version()/,/^}/p
      /^iperf_binary_has_authentication()/,/^}/p
      /^root_executable_path_is_trusted()/,/^}/p
      /^managed_tool_path_is_trusted()/,/^}/p
      /^system_tool_path_is_trusted()/,/^}/p
      /^validate_system_tool_binary()/,/^}/p
      /^prepare_sensor_tool_transaction()/,/^}/p
      /^install_system_tool()/,/^}/p
      /^managed_tool_metadata_value()/,/^}/p
      /^sensor_tool_fields()/,/^}/p' libexec/prtg-nats-probe-helper)

    # The complete helper overwrites any environment value with its literal
    # top-level assignment. Only this extracted-function harness redirects it
    # to a controlled root-owned fake instead of touching /usr/bin.
    # shellcheck disable=SC2034  # read by dynamically sourced helper functions
    SYSTEM_IPERF_PATH="${fake_path}"
    SYSTEM_IPERF_MIN_VERSION="3.18"
    probe_platform() { printf '%s\n' "${TEST_PLATFORM}"; }

    case "${scenario}" in
      old-version) version="3.17" ;;
      no-auth) authentication="no" ;;
      group-writable) mode="0775" ;;
      world-writable) mode="0757" ;;
      managed-platform) TEST_PLATFORM="linux-arm64-glibc" ;;
    esac
    install -o root -g root -m "${mode}" /dev/null "${fake_path}"
    {
      printf '#!/bin/sh\n'
      printf "printf 'iperf %s\\n'\n" "${version}"
      if [[ "${authentication}" == "yes" ]]; then
        printf "printf 'Linux test authentication\\n'\n"
      fi
    } > "${fake_path}"
    chmod "${mode}" "${fake_path}"

    if [[ "${scenario}" == "pass" ||
          "${scenario}" == "status-permission-drift" ]]; then
      prepare_sensor_tool_transaction \
        "${transaction}" iperf-throughput >/dev/null
      install_system_tool "${transaction}" iperf-throughput >/dev/null
      if [[ "${scenario}" == "status-permission-drift" ]]; then
        chmod 0775 "${fake_path}"
        if [[ "$(sensor_tool_fields iperf-throughput)" == \
              *$'tool_compatible=no' ]]; then
          printf 'incompatible'
        else
          printf 'falsely-compatible'
        fi
        return 0
      fi
      if [[ "$(stat -c '%U' "${fake_path}")" == "root" &&
            "$(readlink "${SENSOR_TOOL_ROOT}/iperf3/current")" == \
              "${fake_root}" ]] &&
        grep -q -x 'SOURCE=system' \
          "${SENSOR_CONFIG_ROOT}/iperf-throughput/tool.env" &&
        grep -q -x 'VERSION=3.18' \
          "${SENSOR_CONFIG_ROOT}/iperf-throughput/tool.env" &&
        grep -q -F -x "PATH=${fake_path}" \
          "${SENSOR_CONFIG_ROOT}/iperf-throughput/tool.env"; then
        printf 'accepted'
      else
        printf 'invalid-install'
      fi
      return 0
    fi

    if (
      prepare_sensor_tool_transaction \
        "${transaction}" iperf-throughput
      install_system_tool "${transaction}" iperf-throughput
    ) >/dev/null 2>&1; then
      printf 'accepted'
    else
      printf 'rejected'
    fi
  )
  rm -rf -- "${sandbox}"
}

check "a root-owned system iperf 3.18 with auth is accepted" \
  "$(system_fallback_boundary_scenario pass)" "accepted"
check "system iperf permission drift is reported incompatible" \
  "$(system_fallback_boundary_scenario status-permission-drift)" \
  "incompatible"
for rejected_system_fallback in \
  old-version no-auth group-writable world-writable managed-platform; do
  check "system fallback rejects ${rejected_system_fallback}" \
    "$(system_fallback_boundary_scenario "${rejected_system_fallback}")" \
    "rejected"
done

managed_tool_trust_scenario() {
  local scenario="$1"
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    SENSOR_TOOL_ROOT="${sandbox}/tools"
    SENSOR_CONFIG_ROOT="${sandbox}/config"
    MANAGED_EXEC_MARKER="${sandbox}/executed"
    export MANAGED_EXEC_MARKER
    local transaction="${sandbox}/transaction"
    local target="${SENSOR_TOOL_ROOT}/iperf3/3.21/linux-arm64-glibc/iperf3"
    local fields=""
    mkdir -p "${transaction}"
    cat > "${transaction}/tool-binary" <<'EOF'
#!/bin/sh
: > "${MANAGED_EXEC_MARKER}"
printf 'iperf 3.21\nLinux test authentication\n'
EOF
    chmod 0700 "${transaction}/tool-binary"
    printf 'iperf3\n' > "${transaction}/tool-name"
    printf '3.21\n' > "${transaction}/tool-version"
    printf 'linux-arm64-glibc\n' > "${transaction}/tool-platform"
    sha256sum "${transaction}/tool-binary" | awk '{print $1}' \
      > "${transaction}/tool-sha256"

    # shellcheck disable=SC1090  # process substitution selects shipped code
    source <(sed -n '/^die()/,/^}/p
      /^managed_tool_for_sensor()/,/^}/p
      /^managed_tool_binary_name()/,/^}/p
      /^managed_tool_current_link()/,/^}/p
      /^managed_tool_metadata_path()/,/^}/p
      /^managed_tool_has_release_artifact()/,/^}/p
      /^validate_managed_tool_binary()/,/^}/p
      /^version_at_least()/,/^}/p
      /^iperf_binary_version()/,/^}/p
      /^iperf_binary_has_authentication()/,/^}/p
      /^root_executable_path_is_trusted()/,/^}/p
      /^managed_tool_path_is_trusted()/,/^}/p
      /^system_tool_path_is_trusted()/,/^}/p
      /^install_managed_tool()/,/^}/p
      /^managed_tool_metadata_value()/,/^}/p
      /^sensor_tool_fields()/,/^}/p' libexec/prtg-nats-probe-helper)

    install_managed_tool "${transaction}" iperf-throughput
    rm -f -- "${MANAGED_EXEC_MARKER}"
    case "${scenario}" in
      chmod-status) chmod 0775 "${target}" ;;
      owner-status) chown 65534:65534 "${target}" ;;
      symlink-status)
        mv "${target}" "${target}.foreign"
        ln -s "${target}.foreign" "${target}"
        ;;
      content-status)
        cat > "${target}" <<'EOF'
#!/bin/sh
: > "${MANAGED_EXEC_MARKER}"
printf 'iperf foreign\nLinux test authentication\n'
EOF
        chown root:root "${target}"
        chmod 0755 "${target}"
        ;;
      chmod-redeploy) chmod 0775 "${target}" ;;
      owner-redeploy) chown 65534:65534 "${target}" ;;
      symlink-redeploy)
        mv "${target}" "${target}.foreign"
        ln -s "${target}.foreign" "${target}"
        ;;
      content-redeploy)
        cat > "${target}" <<'EOF'
#!/bin/sh
: > "${MANAGED_EXEC_MARKER}"
printf 'iperf foreign\nLinux test authentication\n'
EOF
        chown root:root "${target}"
        chmod 0755 "${target}"
        ;;
    esac

    case "${scenario}" in
      *-status)
        fields="$(sensor_tool_fields iperf-throughput)"
        if [[ "${fields}" == *$'tool_compatible=no' &&
              "${fields}" == *$'tool_version=none'*
              && ! -e "${MANAGED_EXEC_MARKER}" ]]; then
          printf 'incompatible-not-executed'
        else
          printf 'unsafe-status'
        fi
        ;;
      *-redeploy)
        install_managed_tool "${transaction}" iperf-throughput
        if [[ -f "${target}" && ! -L "${target}" &&
              "$(stat -c '%u:%g:%a' "${target}")" == "0:0:755" &&
              "$(sha256sum "${target}" | awk '{print $1}')" == \
                "$(<"${transaction}/tool-sha256")" &&
              -e "${MANAGED_EXEC_MARKER}" ]]; then
          printf 'repaired-before-execution'
        else
          printf 'repair-failed'
        fi
        ;;
    esac
  )
  rm -rf -- "${sandbox}"
}

for managed_status_drift in chmod owner symlink content; do
  check "managed tool ${managed_status_drift} drift is not executed by status" \
    "$(managed_tool_trust_scenario "${managed_status_drift}-status")" \
    "incompatible-not-executed"
done
for managed_redeploy_drift in chmod owner symlink content; do
  check "managed tool redeploy repairs ${managed_redeploy_drift} drift" \
    "$(managed_tool_trust_scenario "${managed_redeploy_drift}-redeploy")" \
    "repaired-before-execution"
done

# Sensors that measure throughput have to share the same lock file.
# Otherwise one saturates the line while the other checks its target
# rate - and the alarm fires over a perfectly healthy line. Exactly the
# deployment both READMEs recommend.
#
# There is no shared library and there should not be one: sensors are
# deployed individually and have to stay runnable on their own. So this
# check keeps the spellings aligned.
check "the throughput sensors name the same lock file" \
  "$(grep -h '^THROUGHPUT_LOCK_PATH' sensors/*/script/*.py | sort -u | wc -l | tr -d ' ')" \
  "1"
check "and there are two naming it" \
  "$(grep -l '^THROUGHPUT_LOCK_PATH' sensors/*/script/*.py | wc -l | tr -d ' ')" "2"
# A lock named after the sensor would be private again.
check "no lock hangs on the own cache" \
  "$(grep -h '"%s.lock" % CACHE_PATH' sensors/*/script/*.py | wc -l | tr -d ' ')" "0"
check "sensor status knows the fleet view" \
  "$(./prtg-nats sensor | grep -c 'sensor status USER|--all')" "1"

# A sensor with dependencies is never installed byte for byte as the catalogue
# holds it: the helper points its shebang at the sensor's own virtual
# environment. The digest it answers with has to match the catalogue file all
# the same, or the platform reports the sensor as modified from the moment it
# is installed - and redeploying does not help, because the next install
# writes the same interpreter back in.
check "the sensor list asks for the normalised digest" \
  "$(grep -c 'sensor_script_checksum "${name}" "${script}"' \
    libexec/prtg-nats-probe-helper)" "1"
if command -v sha256sum >/dev/null 2>&1; then
  checksum_dir="$(mktemp -d)"
  catalogue_script=sensors/internet-speed/script/internet-speed.py
  installed_script="${checksum_dir}/internet-speed.py"
  catalogue_digest="$(sha256sum "${catalogue_script}" | awk '{print $1}')"
  mkdir -p "${checksum_dir}/config/internet-speed"

  # The two functions are lifted out of the helper and run on their own, so
  # the check exercises the code that ships rather than a copy of it.
  reported_digest() {
    # shellcheck disable=SC2034  # the two roots are read by the sourced code
    (
      SENSOR_CONFIG_ROOT="${checksum_dir}/config"
      SENSOR_VENV_ROOT="${checksum_dir}/venv"
      # shellcheck disable=SC1090  # a process substitution, not a fixed path
      source <(sed -n '/^sensor_shebang_path()/,/^}/p
        /^sensor_script_checksum()/,/^}/p' libexec/prtg-nats-probe-helper)
      sensor_script_checksum internet-speed "$1"
    )
  }
  differs_from_catalogue() {
    [[ "$(reported_digest "$1")" != "${catalogue_digest}" ]] &&
      printf 'changed'
  }

  # What install_sensor_files leaves behind: the rewritten script, and the
  # line the catalogue shipped recorded beside it.
  head -n 1 "${catalogue_script}" \
    > "${checksum_dir}/config/internet-speed/shebang"
  sed "1s|^#!.*|#!${checksum_dir}/venv/internet-speed/bin/python3|" \
    "${catalogue_script}" > "${installed_script}"
  check "the digest answers for the file the catalogue holds" \
    "$(reported_digest "${installed_script}")" "${catalogue_digest}"

  # Everything the check exists for has to survive the normalisation.
  printf '\n# edited on the probe\n' >> "${installed_script}"
  check "an edit on the probe still shows" \
    "$(differs_from_catalogue "${installed_script}")" "changed"
  # A shebang this helper did not write sends the sensor to another
  # interpreter, so it is a deviation rather than something to normalise away.
  sed '1s|^#!.*|#!/opt/elsewhere/bin/python3|' "${catalogue_script}" \
    > "${installed_script}"
  check "a shebang from elsewhere stays visible" \
    "$(differs_from_catalogue "${installed_script}")" "changed"

  # Without the recorded line there is nothing to put back, and guessing one
  # would be the wrong answer dressed as the right one.
  sed "1s|^#!.*|#!${checksum_dir}/venv/internet-speed/bin/python3|" \
    "${catalogue_script}" > "${installed_script}"
  rm -f -- "${checksum_dir}/config/internet-speed/shebang"
  check "a sensor without a recorded shebang is hashed as it lies" \
    "$(reported_digest "${installed_script}")" \
    "$(sha256sum "${installed_script}" | awk '{print $1}')"

  rm -rf -- "${checksum_dir}"
else
  printf '  skipped (sha256sum not installed)\n'
fi

# A rollout that fails while staging must not take the sensor that is already
# running with it. The rollback used to restore every staged slot, and a slot
# with no recorded "before" - which is every slot until activation records one
# - was restored by deleting the target. So a deploy that lost the connection
# between the second and the third file left the probe without the working
# version it had before anyone touched it.
#
# The function is lifted out of the helper and run on its own, so the check
# exercises the code that ships rather than a copy of it. It reports what is
# left of the installed sensor; the checks themselves stay in this shell,
# where the counter lives.
after_rollback() {
  local scenario="$1"
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    # shellcheck disable=SC2034  # the roots are read by the sourced code
    SENSOR_SCRIPT_DIR="${sandbox}/scripts"
    # shellcheck disable=SC2034
    SENSOR_WRAPPER_DIR="${sandbox}/sbin"
    # shellcheck disable=SC2034
    SENSOR_CONFIG_ROOT="${sandbox}/config"
    # shellcheck disable=SC2034
    SENSOR_SLOTS=(script wrapper requirements version)
    local transaction="${sandbox}/transaction"
    mkdir -p "${SENSOR_SCRIPT_DIR}" "${SENSOR_WRAPPER_DIR}" "${transaction}"

    # shellcheck disable=SC1090  # a process substitution, not a fixed path
    source <(sed -n '/^sensor_slot_target()/,/^}/p
      /^restore_sensor_files()/,/^}/p' libexec/prtg-nats-probe-helper)
    # The unit half needs systemd; the file half is what this is about.
    write_sensor_units() { :; }
    remove_sensor_units() { :; }
    restore_managed_tool() { :; }

    printf 'the running version\n' > "${SENSOR_SCRIPT_DIR}/demo.py"
    printf 'the new version\n' > "${transaction}/slot-script"
    case "${scenario}" in
      staged) ;;  # never activated, so nothing was recorded
      activated) printf 'the running version\n' > "${transaction}/original-script" ;;
      newly-installed) : > "${transaction}/original-script-absent" ;;
    esac

    restore_sensor_files "${transaction}" demo root

    if [[ -f "${SENSOR_SCRIPT_DIR}/demo.py" ]]; then
      cat "${SENSOR_SCRIPT_DIR}/demo.py"
    else
      printf 'gone\n'
    fi
  )
  rm -rf -- "${sandbox}"
}

check "a rollback before activation keeps the installed sensor" \
  "$(after_rollback staged)" "the running version"
check "a rollback after activation puts the previous file back" \
  "$(after_rollback activated)" "the running version"
check "a rollback removes a sensor that was newly installed" \
  "$(after_rollback newly-installed)" "gone"

after_tool_rollback() {
  local scenario="$1"
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    SENSOR_TOOL_ROOT="${sandbox}/tools"
    SENSOR_CONFIG_ROOT="${sandbox}/config"
    local transaction="${sandbox}/transaction"
    local tool_root="${SENSOR_TOOL_ROOT}/iperf3"
    mkdir -p \
      "${transaction}" \
      "${tool_root}/3.20/linux-arm64-glibc" \
      "${tool_root}/3.21/linux-arm64-glibc" \
      "${SENSOR_CONFIG_ROOT}/iperf-throughput"
    printf 'iperf3\n' > "${transaction}/tool-name"
    ln -s '3.21/linux-arm64-glibc' "${tool_root}/current"
    printf 'VERSION=3.21\n' \
      > "${SENSOR_CONFIG_ROOT}/iperf-throughput/tool.env"

    case "${scenario}" in
      previous)
        printf '3.20/linux-arm64-glibc\n' \
          > "${transaction}/original-tool-current"
        printf 'VERSION=3.20\n' \
          > "${transaction}/original-tool-metadata"
        ;;
      absent)
        : > "${transaction}/original-tool-current-absent"
        : > "${transaction}/original-tool-metadata-absent"
        ;;
    esac

    # shellcheck disable=SC1090
    source <(sed -n '/^managed_tool_current_link()/,/^}/p
      /^managed_tool_metadata_path()/,/^}/p
      /^restore_managed_tool()/,/^}/p' libexec/prtg-nats-probe-helper)
    restore_managed_tool "${transaction}" iperf-throughput
    if [[ -L "${tool_root}/current" ]]; then
      printf '%s ' "$(readlink "${tool_root}/current")"
    else
      printf 'absent '
    fi
    if [[ -f "${SENSOR_CONFIG_ROOT}/iperf-throughput/tool.env" ]]; then
      tr -d '\n' < "${SENSOR_CONFIG_ROOT}/iperf-throughput/tool.env"
    else
      printf 'absent'
    fi
  )
  rm -rf -- "${sandbox}"
}

check "managed tool rollback restores the previous current link" \
  "$(after_tool_rollback previous)" \
  "3.20/linux-arm64-glibc VERSION=3.20"
check "managed tool rollback restores an absent state" \
  "$(after_tool_rollback absent)" "absent absent"

# The marker is what tells the two apart, so the rollback has to look for it
# and the activation has to leave it behind.
check "activation marks the transaction as activated" \
  "$(grep -c "> \"\${transaction}/activated\"" libexec/prtg-nats-probe-helper)" "1"
check "sensor commits have their own transaction implementation" \
  "$(grep -c 'commit_sensor "${argument_one}"' \
    libexec/prtg-nats-probe-helper)" "1"

load_sensor_transaction_functions() {
  # shellcheck disable=SC1090  # process substitutions select shipped code
  source <(sed -n '/^die()/,/^}/p
    /^die_active_sensor_transaction()/,/^}/p
    /^validate_token()/,/^}/p
    /^sensor_transaction_lock_path()/,/^}/p
    /^sensor_active_transaction_path()/,/^}/p
    /^sensor_commit_tombstone_path()/,/^}/p
    /^committed_sensor_transaction_name()/,/^}/p
    /^reject_committed_sensor_transaction_id()/,/^}/p
    /^prune_sensor_commit_tombstones()/,/^}/p
    /^record_sensor_commit_tombstone()/,/^}/p
    /^acquire_sensor_transaction_lock()/,/^}/p
    /^release_sensor_transaction_lock()/,/^}/p
    /^read_active_sensor_transaction()/,/^}/p
    /^claim_sensor_activation()/,/^}/p
    /^require_active_sensor_transaction()/,/^}/p
    /^clear_active_sensor_transaction()/,/^}/p
    /^clear_incomplete_sensor_snapshot()/,/^}/p
    /^restore_failed_sensor_activation()/,/^}/p
    /^sensor_activation_failure_exit()/,/^}/p
    /^sensor_activation_signal_exit()/,/^}/p
    /^activate_sensor()/,/^}/p
    /^rollback_sensor()/,/^}/p
    /^recover_sensor_transaction()/,/^}/p
    /^commit_sensor()/,/^}/p
    /^remove_sensor()/,/^}/p' libexec/prtg-nats-probe-helper)
}

sensor_transaction_marker_scenario() {
  local sandbox=""
  local blocked_output=""
  sandbox="$(mktemp -d)"
  (
    # shellcheck disable=SC2034  # read by the dynamically sourced helper
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    SENSOR_SLOTS=(script wrapper requirements version)
    mkdir -p \
      "${TRANSACTION_DIR}/tx-one" \
      "${TRANSACTION_DIR}/tx-two" \
      "${SENSOR_TRANSACTION_STATE_DIR}"
    load_sensor_transaction_functions

    acquire_sensor_transaction_lock demo
    printf '%s ' "$(claim_sensor_activation \
      tx-one "${TRANSACTION_DIR}/tx-one" demo)"
    : > "${TRANSACTION_DIR}/tx-one/activation-complete"
    printf '%s ' "$(claim_sensor_activation \
      tx-one "${TRANSACTION_DIR}/tx-one" demo)"
    if blocked_output="$(
      claim_sensor_activation \
        tx-two "${TRANSACTION_DIR}/tx-two" demo 2>&1
    )"; then
      printf 'accepted-other '
    elif [[ "${blocked_output}" == *$'active_transaction=tx-one'* ]]; then
      printf 'blocked-other-structured '
    else
      printf 'blocked-other '
    fi
    if flock -n "$(sensor_transaction_lock_path demo)" -c true; then
      printf 'unlocked'
    else
      printf 'locked'
    fi
    release_sensor_transaction_lock
  )
  rm -rf -- "${sandbox}"
}

check "a completed activation retry is idempotent and another tx is blocked" \
  "$(sensor_transaction_marker_scenario)" \
  "pending complete blocked-other-structured locked"

sensor_activation_success_scenario() {
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    # shellcheck disable=SC2034  # read by the dynamically sourced helper
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    SENSOR_COMMIT_TOMBSTONE_DIR="${SENSOR_TRANSACTION_STATE_DIR}/committed"
    # shellcheck disable=SC2034  # read by dynamically sourced helper functions
    MAXIMUM_SENSOR_COMMIT_TOMBSTONES=1024
    SENSOR_SCRIPT_DIR="${sandbox}/scripts"
    SENSOR_SLOTS=(script wrapper requirements version)
    # shellcheck disable=SC2034  # read by activation traps after sourcing
    SENSOR_ACTIVATION_TRANSACTION=""
    # shellcheck disable=SC2034  # read by activation traps after sourcing
    SENSOR_ACTIVATION_NAME=""
    # shellcheck disable=SC2034  # read by activation traps after sourcing
    SENSOR_ACTIVATION_SERVICE_USER=""
    mkdir -p \
      "${TRANSACTION_DIR}/tx-one" \
      "${SENSOR_COMMIT_TOMBSTONE_DIR}" \
      "${SENSOR_SCRIPT_DIR}"
    printf 'demo\n' > "${TRANSACTION_DIR}/tx-one/sensor-name"
    printf 'new\n' > "${TRANSACTION_DIR}/tx-one/slot-script"
    printf 'old\n' > "${SENSOR_SCRIPT_DIR}/demo.py"
    load_sensor_transaction_functions

    sensor_slot_target() { printf '%s/%s.py\n' "${SENSOR_SCRIPT_DIR}" "$1"; }
    sensor_service_user() { printf 'probe\n'; }
    prepare_sensor_tool_transaction() { :; }
    record_managed_tool_original() { :; }
    install_sensor_files() {
      cp "${TRANSACTION_DIR}/tx-one/slot-script" \
        "${SENSOR_SCRIPT_DIR}/demo.py"
      printf 'installed\n' >> "${sandbox}/installations"
    }
    sensor_self_check() { return 0; }
    restore_sensor_files() {
      cp "$1/original-script" "${SENSOR_SCRIPT_DIR}/demo.py"
    }

    activate_sensor tx-one >/dev/null
    activate_sensor tx-one >/dev/null
    printf '%s %s %s %s ' \
      "$(<"${SENSOR_SCRIPT_DIR}/demo.py")" \
      "$(<"${TRANSACTION_DIR}/tx-one/original-script")" \
      "$(wc -l < "${sandbox}/installations" | tr -d '[:space:]')" \
      "$(read_active_sensor_transaction demo)"
    commit_sensor tx-one >/dev/null
    if [[ ! -e "${TRANSACTION_DIR}/tx-one" &&
          ! -e "$(sensor_active_transaction_path demo)" ]]; then
      printf 'clean '
    else
      printf 'leftovers '
    fi
    printf '%s ' "$(committed_sensor_transaction_name tx-one)"
    printf 'current\n' > "$(sensor_active_transaction_path demo)"
    if [[ "$(commit_sensor tx-one)" == "OK sensor-committed demo" ]]; then
      printf 'retry-ok '
    else
      printf 'retry-failed '
    fi
    printf '%s' "$(read_active_sensor_transaction demo)"
  )
  rm -rf -- "${sandbox}"
}

check "activation and response-loss commit retries preserve newer state" \
  "$(sensor_activation_success_scenario)" \
  "new old 1 tx-one clean demo retry-ok current"

sensor_commit_retry_lock_order() {
  local body=""
  local lock_line=""
  local inspect_line=""

  body="$(sed -n '/^commit_sensor()/,/^}/p' \
    libexec/prtg-nats-probe-helper)"
  lock_line="$(printf '%s\n' "${body}" |
    grep -n -m 1 'acquire_sensor_transaction_lock' | cut -d: -f1)"
  inspect_line="$(printf '%s\n' "${body}" |
    grep -n -m 1 '\[\[ -e "${transaction}"' | cut -d: -f1)"
  if [[ -n "${lock_line}" && -n "${inspect_line}" &&
        "${lock_line}" -lt "${inspect_line}" ]]; then
    printf 'lock-first'
  else
    printf 'inspection-first'
  fi
}

check "a tombstoned commit locks before inspecting its transaction" \
  "$(sensor_commit_retry_lock_order)" "lock-first"

commit_response_loss_wait_scenario() {
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    SENSOR_COMMIT_TOMBSTONE_DIR="${SENSOR_TRANSACTION_STATE_DIR}/committed"
    MAXIMUM_SENSOR_COMMIT_TOMBSTONES=1024
    local transaction="${TRANSACTION_DIR}/race"
    local lock_path="${SENSOR_TRANSACTION_STATE_DIR}/demo.lock"
    local waiting="${sandbox}/waiting"
    local result="${sandbox}/result"
    local retry_pid=""
    local attempt=0
    mkdir -p "${transaction}" "${SENSOR_COMMIT_TOMBSTONE_DIR}"
    printf 'demo\n' > "${transaction}/sensor-name"
    load_sensor_transaction_functions
    record_sensor_commit_tombstone race demo

    eval "$(declare -f acquire_sensor_transaction_lock |
      sed '1s/acquire_sensor_transaction_lock/original_acquire_sensor_transaction_lock/')"
    acquire_sensor_transaction_lock() {
      : > "${waiting}"
      original_acquire_sensor_transaction_lock "$@"
    }

    exec 7> "${lock_path}"
    flock -x 7
    (
      exec 7>&-
      commit_sensor race > "${result}"
    ) &
    retry_pid="$!"
    for ((attempt = 0; attempt < 100; attempt++)); do
      [[ ! -e "${waiting}" ]] || break
      sleep 0.01
    done
    if [[ ! -e "${waiting}" ]]; then
      flock -u 7
      exec 7>&-
      wait "${retry_pid}" || true
      printf 'did-not-wait'
      return 0
    fi

    rm -rf -- "${transaction}"
    flock -u 7
    exec 7>&-
    if wait "${retry_pid}" &&
      [[ "$(<"${result}")" == "OK sensor-committed demo" &&
         ! -e "${transaction}" ]]; then
      printf 'retry-ok-after-wait'
    else
      printf 'retry-failed-after-wait'
    fi
  )
  rm -rf -- "${sandbox}"
}

check "a waiting response-loss retry accepts tx cleanup by the first commit" \
  "$(commit_response_loss_wait_scenario)" "retry-ok-after-wait"

stale_sensor_commit_scenario() {
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    SENSOR_COMMIT_TOMBSTONE_DIR="${SENSOR_TRANSACTION_STATE_DIR}/committed"
    MAXIMUM_SENSOR_COMMIT_TOMBSTONES=1024
    mkdir -p \
      "${TRANSACTION_DIR}/stale" \
      "${SENSOR_COMMIT_TOMBSTONE_DIR}"
    printf 'demo\n' > "${TRANSACTION_DIR}/stale/sensor-name"
    : > "${TRANSACTION_DIR}/stale/activation-complete"
    printf 'current\n' > "${SENSOR_TRANSACTION_STATE_DIR}/demo.active"
    load_sensor_transaction_functions

    if (commit_sensor stale) >/dev/null 2>&1; then
      printf 'accepted '
    else
      printf 'blocked '
    fi
    printf '%s ' "$(read_active_sensor_transaction demo)"
    if [[ -d "${TRANSACTION_DIR}/stale" &&
          ! -e "$(sensor_commit_tombstone_path stale)" ]]; then
      printf 'preserved'
    else
      printf 'mutated'
    fi
  )
  rm -rf -- "${sandbox}"
}

check "a stale sensor commit cannot acknowledge or touch a newer tx" \
  "$(stale_sensor_commit_scenario)" "blocked current preserved"

bounded_sensor_commit_tombstone_scenario() {
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    SENSOR_TRANSACTION_STATE_DIR="${sandbox}/sensor-transactions"
    SENSOR_COMMIT_TOMBSTONE_DIR="${SENSOR_TRANSACTION_STATE_DIR}/committed"
    # shellcheck disable=SC2034  # read by pruning after dynamic sourcing
    MAXIMUM_SENSOR_COMMIT_TOMBSTONES=2
    mkdir -p "${SENSOR_COMMIT_TOMBSTONE_DIR}"
    load_sensor_transaction_functions

    record_sensor_commit_tombstone old-one demo
    touch -d '@1' "$(sensor_commit_tombstone_path old-one)"
    record_sensor_commit_tombstone old-two demo
    touch -d '@2' "$(sensor_commit_tombstone_path old-two)"
    record_sensor_commit_tombstone current demo
    find "${SENSOR_COMMIT_TOMBSTONE_DIR}" \
      -mindepth 1 -maxdepth 1 -type f ! -name '.*' -printf '%f\n' |
      sort | paste -s -d ' ' -
    if (reject_committed_sensor_transaction_id current) \
      >/dev/null 2>&1; then
      printf 'accepted-reuse\n'
    else
      printf 'blocked-reuse\n'
    fi
  )
  rm -rf -- "${sandbox}"
}

check "sensor commit tombstones retain only the configured bound" \
  "$(bounded_sensor_commit_tombstone_scenario)" \
  $'current old-two\nblocked-reuse'

sensor_activation_failure_scenario() {
  local failure_mode="$1"
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    # shellcheck disable=SC2034  # read by the dynamically sourced helper
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    SENSOR_SCRIPT_DIR="${sandbox}/scripts"
    SENSOR_SLOTS=(script wrapper requirements version)
    # shellcheck disable=SC2034  # read by activation traps after sourcing
    SENSOR_ACTIVATION_TRANSACTION=""
    # shellcheck disable=SC2034  # read by activation traps after sourcing
    SENSOR_ACTIVATION_NAME=""
    # shellcheck disable=SC2034  # read by activation traps after sourcing
    SENSOR_ACTIVATION_SERVICE_USER=""
    FAILURE_MODE="${failure_mode}"
    mkdir -p \
      "${TRANSACTION_DIR}/tx-one" \
      "${SENSOR_TRANSACTION_STATE_DIR}" \
      "${SENSOR_SCRIPT_DIR}"
    printf 'demo\n' > "${TRANSACTION_DIR}/tx-one/sensor-name"
    printf 'new\n' > "${TRANSACTION_DIR}/tx-one/slot-script"
    printf 'old\n' > "${SENSOR_SCRIPT_DIR}/demo.py"
    load_sensor_transaction_functions

    sensor_slot_target() { printf '%s/%s.py\n' "${SENSOR_SCRIPT_DIR}" "$1"; }
    sensor_service_user() { printf 'probe\n'; }
    prepare_sensor_tool_transaction() { :; }
    record_managed_tool_original() { :; }
    install_sensor_files() {
      printf 'partial\n' > "${SENSOR_SCRIPT_DIR}/demo.py"
      case "${FAILURE_MODE}" in
        err) return 1 ;;
        term) kill -TERM "${BASHPID}" ;;
      esac
    }
    sensor_self_check() { [[ "${FAILURE_MODE}" != "exit" ]]; }
    restore_sensor_files() {
      cp "$1/original-script" "${SENSOR_SCRIPT_DIR}/demo.py"
      printf 'restored\n' >> "${sandbox}/restorations"
    }

    # Do not put activate_sensor on the left side of `||`: Bash suppresses
    # ERR traps throughout a function called from such a conditional.
    set +e
    (activate_sensor tx-one) >/dev/null 2>&1
    set -e
    printf '%s %s %s %s ' \
      "$(<"${SENSOR_SCRIPT_DIR}/demo.py")" \
      "$(wc -l < "${sandbox}/restorations" | tr -d '[:space:]')" \
      "$(read_active_sensor_transaction demo)" \
      "$(test -f "${TRANSACTION_DIR}/tx-one/activation-restored" &&
        printf marked)"
    rollback_sensor tx-one >/dev/null
    if [[ ! -e "${TRANSACTION_DIR}/tx-one" &&
          ! -e "$(sensor_active_transaction_path demo)" ]]; then
      printf 'clean'
    else
      printf 'leftovers'
    fi
  )
  rm -rf -- "${sandbox}"
}

for activation_failure_mode in err exit term; do
  check "${activation_failure_mode} during activation restores under the lock" \
    "$(sensor_activation_failure_scenario "${activation_failure_mode}")" \
    "old 1 tx-one marked clean"
done

stale_sensor_rollback_scenario() {
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    # shellcheck disable=SC2034  # read by the dynamically sourced helper
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    # shellcheck disable=SC2034  # read by rollback_sensor after sourcing
    SENSOR_SLOTS=(script wrapper requirements version)
    mkdir -p \
      "${TRANSACTION_DIR}/stale" \
      "${SENSOR_TRANSACTION_STATE_DIR}"
    printf 'demo\n' > "${TRANSACTION_DIR}/stale/sensor-name"
    : > "${TRANSACTION_DIR}/stale/activated"
    printf 'newer\n' > "${sandbox}/installed"
    printf 'current\n' > "${SENSOR_TRANSACTION_STATE_DIR}/demo.active"
    load_sensor_transaction_functions
    sensor_service_user() { printf 'probe\n'; }
    restore_sensor_files() { printf 'stale\n' > "${sandbox}/installed"; }

    if (rollback_sensor stale) >/dev/null 2>&1; then
      printf 'accepted '
    else
      printf 'blocked '
    fi
    printf '%s %s' \
      "$(<"${sandbox}/installed")" \
      "$(<"${SENSOR_TRANSACTION_STATE_DIR}/demo.active")"
  )
  rm -rf -- "${sandbox}"
}

check "a stale rollback cannot replace a newer active deployment" \
  "$(stale_sensor_rollback_scenario)" "blocked newer current"

sensor_recovery_scenario() {
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    SENSOR_COMMIT_TOMBSTONE_DIR="${SENSOR_TRANSACTION_STATE_DIR}/committed"
    # shellcheck disable=SC2034  # read by dynamically sourced helper functions
    MAXIMUM_SENSOR_COMMIT_TOMBSTONES=1024
    SENSOR_SCRIPT_DIR="${sandbox}/scripts"
    SENSOR_WRAPPER_DIR="${sandbox}/wrappers"
    SENSOR_CONFIG_ROOT="${sandbox}/config"
    SENSOR_TOOL_ROOT="${sandbox}/tools"
    # shellcheck disable=SC2034  # read by dynamically sourced helper functions
    SENSOR_SLOTS=(script wrapper requirements version)
    local old_transaction="${TRANSACTION_DIR}/tx-old"
    local new_transaction="${TRANSACTION_DIR}/tx-new"
    local tool_root="${SENSOR_TOOL_ROOT}/iperf3"
    mkdir -p \
      "${old_transaction}" \
      "${new_transaction}" \
      "${SENSOR_COMMIT_TOMBSTONE_DIR}" \
      "${SENSOR_SCRIPT_DIR}" \
      "${SENSOR_WRAPPER_DIR}" \
      "${SENSOR_CONFIG_ROOT}/demo" \
      "${tool_root}/3.20/linux-arm64-glibc" \
      "${tool_root}/3.21/linux-arm64-glibc"
    printf 'demo\n' > "${old_transaction}/sensor-name"
    printf 'demo\n' > "${new_transaction}/sensor-name"
    : > "${old_transaction}/activated"
    : > "${old_transaction}/activation-complete"
    printf 'new sensor\n' > "${old_transaction}/slot-script"
    printf 'old sensor\n' > "${old_transaction}/original-script"
    printf 'iperf3\n' > "${old_transaction}/tool-name"
    printf '3.20/linux-arm64-glibc\n' \
      > "${old_transaction}/original-tool-current"
    printf 'VERSION=3.20\n' \
      > "${old_transaction}/original-tool-metadata"
    printf 'new sensor\n' > "${SENSOR_SCRIPT_DIR}/demo.py"
    ln -s '3.21/linux-arm64-glibc' "${tool_root}/current"
    printf 'VERSION=3.21\n' > "${SENSOR_CONFIG_ROOT}/demo/tool.env"
    printf 'tx-old\n' > "${SENSOR_TRANSACTION_STATE_DIR}/demo.active"
    load_sensor_transaction_functions
    # shellcheck disable=SC1090  # process substitution selects shipped code
    source <(sed -n '/^managed_tool_current_link()/,/^}/p
      /^managed_tool_metadata_path()/,/^}/p
      /^sensor_slot_target()/,/^}/p
      /^restore_managed_tool()/,/^}/p
      /^restore_sensor_files()/,/^}/p' libexec/prtg-nats-probe-helper)
    sensor_service_user() { printf 'probe\n'; }
    write_sensor_units() { :; }
    remove_sensor_units() { :; }

    recover_sensor_transaction other tx-old >/dev/null
    if [[ "$(read_active_sensor_transaction demo)" == "tx-old" &&
          "$(<"${SENSOR_SCRIPT_DIR}/demo.py")" == "new sensor" ]]; then
      printf 'wrong-safe '
    else
      printf 'wrong-mutated '
    fi
    if (recover_sensor_transaction demo tx-new) >/dev/null 2>&1; then
      printf 'stale-accepted '
    else
      printf 'stale-blocked '
    fi
    if (
      acquire_sensor_transaction_lock demo
      claim_sensor_activation tx-new "${new_transaction}" demo
    ) >/dev/null 2>&1; then
      printf 'deploy-accepted '
    else
      printf 'deploy-blocked '
    fi

    recover_sensor_transaction demo tx-old >/dev/null
    if [[ "$(<"${SENSOR_SCRIPT_DIR}/demo.py")" == "old sensor" &&
          "$(readlink "${tool_root}/current")" == \
            "3.20/linux-arm64-glibc" &&
          "$(<"${SENSOR_CONFIG_ROOT}/demo/tool.env")" == \
            "VERSION=3.20" &&
          ! -e "${old_transaction}" &&
          ! -e "${SENSOR_TRANSACTION_STATE_DIR}/demo.active" ]]; then
      printf 'restored '
    else
      printf 'restore-failed '
    fi
    if [[ "$(recover_sensor_transaction demo tx-old)" == \
          "OK sensor-recovered demo transaction=tx-old no-active" ]]; then
      printf 'retry-no-active '
    else
      printf 'retry-failed '
    fi
    acquire_sensor_transaction_lock demo
    printf '%s ' "$(claim_sensor_activation \
      tx-new "${new_transaction}" demo)"
    clear_active_sensor_transaction tx-new demo
    release_sensor_transaction_lock
    record_sensor_commit_tombstone tx-done committed
    if [[ "$(recover_sensor_transaction committed tx-done)" == \
          "OK sensor-recovered committed transaction=tx-done already-committed" ]]; then
      printf 'tombstone-retry'
    else
      printf 'tombstone-lost'
    fi
  )
  rm -rf -- "${sandbox}"
}

check "explicit recovery restores only the named active transaction" \
  "$(sensor_recovery_scenario)" \
  "wrong-safe stale-blocked deploy-blocked restored retry-no-active pending tombstone-retry"

sensor_remove_serialization_scenario() {
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    STATE_DIR="${sandbox}/state"
    TRANSACTION_DIR="${STATE_DIR}/transactions"
    SENSOR_TRANSACTION_STATE_DIR="${STATE_DIR}/sensor-transactions"
    SENSOR_SCRIPT_DIR="${sandbox}/scripts"
    SENSOR_WRAPPER_DIR="${sandbox}/wrappers"
    SENSOR_CONFIG_ROOT="${sandbox}/config"
    SENSOR_VENV_ROOT="${sandbox}/venv"
    mkdir -p \
      "${SENSOR_TRANSACTION_STATE_DIR}" \
      "${SENSOR_SCRIPT_DIR}" \
      "${SENSOR_WRAPPER_DIR}" \
      "${SENSOR_CONFIG_ROOT}/demo" \
      "${SENSOR_VENV_ROOT}/demo"
    printf 'current\n' > "${SENSOR_TRANSACTION_STATE_DIR}/demo.active"
    printf 'sensor\n' > "${SENSOR_SCRIPT_DIR}/demo.py"
    load_sensor_transaction_functions
    release_sensor_interfaces() { :; }
    remove_sensor_units() { :; }
    sensor_slot_target() {
      case "$2" in
        script) printf '%s/%s.py\n' "${SENSOR_SCRIPT_DIR}" "$1" ;;
        wrapper) printf '%s/prtg-sensor-%s\n' "${SENSOR_WRAPPER_DIR}" "$1" ;;
      esac
    }

    if (remove_sensor demo) >/dev/null 2>&1; then
      printf 'accepted '
    else
      printf 'blocked '
    fi
    if [[ -f "${SENSOR_SCRIPT_DIR}/demo.py" ]]; then
      printf 'preserved '
    else
      printf 'removed-early '
    fi
    rm -f -- "${SENSOR_TRANSACTION_STATE_DIR}/demo.active"
    remove_sensor demo >/dev/null
    if [[ ! -e "${SENSOR_SCRIPT_DIR}/demo.py" &&
          ! -e "${SENSOR_CONFIG_ROOT}/demo" ]]; then
      printf 'removed'
    else
      printf 'leftovers'
    fi
  )
  rm -rf -- "${sandbox}"
}

check "sensor removal cannot cross an active deployment" \
  "$(sensor_remove_serialization_scenario)" "blocked preserved removed"

non_runnable_tool_status_scenario() {
  local sandbox=""
  sandbox="$(mktemp -d)"
  (
    # shellcheck disable=SC2034  # read by the dynamically sourced helper
    SENSOR_TOOL_ROOT="${sandbox}/tools"
    SENSOR_CONFIG_ROOT="${sandbox}/config"
    # shellcheck disable=SC2034  # read by sensor_tool_fields after sourcing
    SYSTEM_IPERF_MIN_VERSION="3.18"
    local release="${SENSOR_TOOL_ROOT}/iperf3/3.21/linux-amd64-glibc"
    local metadata="${SENSOR_CONFIG_ROOT}/iperf-throughput/tool.env"
    local fields=""
    mkdir -p "${release}" "$(dirname -- "${metadata}")"
    printf 'not an executable format\n' > "${release}/iperf3"
    chmod 0755 "${release}/iperf3"
    ln -s '3.21/linux-amd64-glibc' "${SENSOR_TOOL_ROOT}/iperf3/current"
    {
      printf 'SOURCE=managed\n'
      printf 'VERSION=3.21\n'
      printf 'PLATFORM=linux-amd64-glibc\n'
      printf 'SHA256=%s\n' "$(sha256sum "${release}/iperf3" | awk '{print $1}')"
    } > "${metadata}"

    # shellcheck disable=SC1090  # process substitution selects shipped code
    source <(sed -n '/^managed_tool_for_sensor()/,/^}/p
      /^managed_tool_binary_name()/,/^}/p
      /^managed_tool_current_link()/,/^}/p
      /^managed_tool_metadata_path()/,/^}/p
      /^managed_tool_has_release_artifact()/,/^}/p
      /^version_at_least()/,/^}/p
      /^iperf_binary_version()/,/^}/p
      /^iperf_binary_has_authentication()/,/^}/p
      /^root_executable_path_is_trusted()/,/^}/p
      /^managed_tool_path_is_trusted()/,/^}/p
      /^system_tool_path_is_trusted()/,/^}/p
      /^managed_tool_metadata_value()/,/^}/p
      /^sensor_tool_fields()/,/^}/p' libexec/prtg-nats-probe-helper)
    fields="$(sensor_tool_fields iperf-throughput)"
    if [[ "${fields}" == *$'tool_version=none\t'* &&
          "${fields}" == *$'tool_source=managed\t'* &&
          "${fields}" == *$'tool_compatible=no'* ]]; then
      printf 'reported-incompatible'
    else
      printf '%s' "${fields}"
    fi
  )
  rm -rf -- "${sandbox}"
}

check "a wrong-ELF managed tool is reported instead of aborting sensor-list" \
  "$(non_runnable_tool_status_scenario)" "reported-incompatible"

# The endpoint is the second machine this tool sets up over SSH. What
# holds for the probe holds for it too: help without a configured
# environment, and every deploy has a counterpart.
check "iperf-server without an argument shows the help" \
  "$(./prtg-nats iperf-server | grep -c '^Usage:')" "1"
check "deploy has a counterpart" \
  "$(./prtg-nats iperf-server | grep -c 'iperf-server revoke NAME USER|--all')" "1"
# The password is created on this server and sent there, not the other
# way round. Otherwise it could not be read back after the run and would
# have to be copied off the screen - exactly the manual work this
# removes.
check "the help names the direction of the password" \
  "$(./prtg-nats iperf-server --help |
    grep -c 'generated here and sent there')" "1"
# The setup script lives with the sensor and is copied from there. A
# moved path would otherwise only show up during a real rollout.
check "the setup script sits where it is expected" \
  "$(test -f sensors/iperf-throughput/endpoint/setup-iperf3-endpoint.sh &&
    printf 'yes')" "yes"
check "and masters the password change without a key swap" \
  "$(bash sensors/iperf-throughput/endpoint/setup-iperf3-endpoint.sh --help |
    grep -c -- '--force-credentials')" "1"
# /etc/iperf3 is closed to everyone but root and the service group, so the
# administrator the session belongs to cannot read the public key there.
# The setup run hands it over while it still has root; without that the
# install ends after the endpoint is already set up.
check "the setup script can hand out the public key" \
  "$(bash sensors/iperf-throughput/endpoint/setup-iperf3-endpoint.sh --help |
    grep -c -- '--export-public-key')" "1"
check "and the install asks it to" \
  "$(grep -c -- '--export-public-key %q' libexec/manage-iperf-server.sh)" "1"
check "and reads the copy, not the closed directory" \
  "$(grep -c 'cat .\${REMOTE_STAGE}/public.pem' \
    libexec/manage-iperf-server.sh)" "1"
check "endpoint setup rejects pre-OAEP iperf versions" \
  "$(grep -c 'authenticated endpoints require 3.17 or newer' \
    sensors/iperf-throughput/endpoint/setup-iperf3-endpoint.sh)" "1"
check "endpoint setup requires the authentication feature" \
  "$(grep -c "grep -i 'authentication'" \
    sensors/iperf-throughput/endpoint/setup-iperf3-endpoint.sh)" "1"
check "endpoint service uses the exact executable that was checked" \
  "$(grep -c '^ExecStart=\${IPERF_BIN} ' \
    sensors/iperf-throughput/endpoint/setup-iperf3-endpoint.sh)" "1"
check "no component enables legacy PKCS1 padding" \
  "$(grep -R --exclude='*.md' -c -- '--use-pkcs1-padding' \
    sensors libexec web/backend 2>/dev/null | awk -F: '{ total += $2 } END { print total + 0 }')" \
  "0"
# The register dialog hands the same steps to somebody we never reach, so
# the two describe one endpoint or the record here describes a host the
# probes cannot authenticate against. Compared by the parts that decide
# that: where the material lives, and what the service is started with.
iperf_help=web/frontend/src/features/infrastructure/IperfPage.tsx
for expected in \
  '/etc/iperf3' \
  'private.pem' \
  'credentials.csv' \
  '/etc/systemd/system/iperf3.service.d' \
  '--rsa-private-key-path' \
  '--authorized-users-path'; do
  check "the register dialog names ${expected}" \
    "$(grep -c -- "${expected}" "${iperf_help}" | awk '$1 > 0 {print "yes"}')" \
    "yes"
done
# iperf3 hashes "{user}password" and nothing else. A dialog handing out a
# different recipe produces an endpoint that rejects every probe, and the
# refusal names neither the password nor the hash.
check "the register dialog hashes the way iperf3 reads it" \
  "$(grep -c '{$IPERF_USER}$PASSWORD' "${iperf_help}")" "1"
# A drop-in that only appends leaves the packaged start command in place,
# and systemd refuses a service with two of them.
check "the register dialog clears ExecStart before setting it" \
  "$(grep -c '^ExecStart=$' "${iperf_help}")" "1"
# Unconditionally: the install records the password it generated, so the
# endpoint has to take it over even on the first run against a host that
# was set up by hand. Anything else records a password nobody can use.
check "the install always enforces its own password" \
  "$(grep -c 'setup_options+=(--force-credentials)' \
    libexec/manage-iperf-server.sh)" "1"
# Exactly one sensor declares itself responsible for endpoints; through
# this field "sensor deploy" learns the credentials belong along.
check "the iperf sensor declares its endpoints" \
  "$(grep -h 'SENSOR_IPERF=iperf3' sensors/*/manifest.env |
    wc -l | tr -d ' ')" "1"
# The credentials take the path the repository already has. A second
# channel for the same thing would be a second place to maintain - and
# the one overlooked at the next permission bug.
#
# The five: deploy writes the endpoint's own profile, then either writes or
# removes "default" depending on whether the endpoint is still alone; revoke
# removes both again.
check "the rollout uses the profile mechanism" \
  "$(grep -c 'manage-sensors.sh" profile' libexec/manage-iperf-server.sh)" "5"
check "and invents no channel of its own" \
  "$(grep -c 'sensor-write-iperf' libexec/prtg-nats-probe-helper)" "0"
# The sensor reads the profile under the names "iperf-server deploy"
# writes. If they drift apart, it no longer finds the credentials.
check "profile keys agree between sensor and rollout" \
  "$(grep -c -E 'IPERF3_(PASSWORD|PUBLIC_KEY_B64)=' \
    libexec/manage-iperf-server.sh)" "2"

check "install-mpp --help shows the help" \
  "$(./prtg-nats install-mpp --help | grep -c '^Usage:')" "1"
check "config --help shows the help" \
  "$(./prtg-nats config --help | grep -c 'config --edit')" "1"

# The grace period is over: a retired name is an unknown command again.
expect_failure "configure is no longer dispatched" ./prtg-nats configure
check "config without .env names the way" \
  "$(cd "$(mktemp -d)" && "${PROJECT_DIR}/prtg-nats" config 2>&1 |
    grep -c 'Not configured yet')" "1"
expect_failure "verify with an unknown option" ./prtg-nats verify --nonsense

# Install and remove against a throwaway startup file: the second run
# must not append twice, and afterwards the file has to look as before.
completion_home="$(mktemp -d)"
printf '# own settings\n' > "${completion_home}/.zshrc"
completion_before="$(cat "${completion_home}/.zshrc")"
HOME="${completion_home}" SUDO_USER="" USER="" \
  ./prtg-nats self install --completion-only zsh >/dev/null
check "install writes one block" \
  "$(grep -c '>>> prtg-nats completion >>>' "${completion_home}/.zshrc")" "1"
HOME="${completion_home}" SUDO_USER="" USER="" \
  ./prtg-nats self install --completion-only zsh >/dev/null
check "a second install does not append twice" \
  "$(grep -c '>>> prtg-nats completion >>>' "${completion_home}/.zshrc")" "1"
HOME="${completion_home}" SUDO_USER="" USER="" \
  ./prtg-nats self uninstall --completion-only >/dev/null
check "uninstall restores the file" \
  "$(cat "${completion_home}/.zshrc")" "${completion_before}"
rm -rf -- "${completion_home}"

printf '\n== Invocation over PATH ==\n'

# A symlink in PATH must not shift the project directory: the script
# would otherwise look for libexec/ next to the link.
#
# The runtime directory is pinned for this check. Unpinned it comes from the
# prtg-nats-runtime volume, so the answer would depend on whether the machine
# running the checks happens to have an installation on it.
link_dir="$(mktemp -d)"
ln -s "${PROJECT_DIR}/prtg-nats" "${link_dir}/prtg-nats"
check "a symlink finds the repository" \
  "$(PRTG_NATS_RUNTIME_DIR=/srv/runtime "${link_dir}/prtg-nats" ca-path)" \
  "/srv/runtime/certs/ca.pem"
check "an absent volume falls back to the checkout" \
  "$(
    PRTG_NATS_RUNTIME_DIR='' \
      PRTG_NATS_RUNTIME_VOLUME=prtg-nats-no-such-volume \
      "${PROJECT_DIR}/prtg-nats" ca-path
  )" \
  "${PROJECT_DIR}/runtime/certs/ca.pem"
check "the completion finds it over PATH" \
  "$(
    PATH="${link_dir}:${PATH}"
    # shellcheck disable=SC1091  # path is only known at run time
    source ./completions/prtg-nats.bash
    cd /
    _prtg_nats_project_dir prtg-nats
  )" "${PROJECT_DIR}"
rm -rf -- "${link_dir}"

expect_failure "self install with an unknown argument" \
  ./prtg-nats self install toomuch

# install sets up both: link and completion. Both targets point into
# throwaway directories - a test run may touch neither /usr/local/bin
# nor the setup of the machine it runs on.
integration_home="$(mktemp -d)"
integration_bin="$(mktemp -d)"
integration_completion="$(mktemp -d)"
printf '# own settings\n' > "${integration_home}/.zshrc"
self_install() {
  HOME="${integration_home}" SUDO_USER="" USER="" \
    PRTG_NATS_LINK_DIR="${integration_bin}" \
    PRTG_NATS_BASH_COMPLETION_DIR="${integration_completion}" \
    ./prtg-nats "$@"
}

self_install self install zsh >/dev/null
check "install creates the link" \
  "$(readlink "${integration_bin}/prtg-nats")" "${PROJECT_DIR}/prtg-nats"
check "install sets up the completion along" \
  "$(grep -c '>>> prtg-nats completion >>>' "${integration_home}/.zshrc")" "1"

# The system-wide bash path only applies when the directory exists -
# here one exists, so it has to be taken, not the startup file.
self_install self install --completion-only bash >/dev/null
check "bash uses the directory when it exists" \
  "$(grep -c 'completions/prtg-nats.bash' \
    "${integration_completion}/prtg-nats")" "1"

self_install self uninstall >/dev/null
check "uninstall takes the completion out" \
  "$(grep -c '>>> prtg-nats completion >>>' "${integration_home}/.zshrc")" "0"
check "uninstall removes the link" \
  "$(ls "${integration_bin}" | wc -l | tr -d ' ')" "0"
check "uninstall cleans up the directory too" \
  "$(ls "${integration_completion}" | wc -l | tr -d ' ')" "0"
rm -rf -- "${integration_home}" "${integration_bin}" "${integration_completion}"

printf '\n== Name derivation ==\n'

check "the host name is shortened to its short form" \
  "$(mpp_host_label probe-01.example.com)" "probe-01"
# An address must not be cut at the first dot, or all probes of one
# network would carry the same name.
check "IPv4 keeps all octets" \
  "$(mpp_host_label 192.0.2.18)" "192-0-2-18"
check "a probe name from IPv4 stays distinguishable" \
  "$(mpp_default_probe_name 192.0.2.18)" \
  "multi-platform-probe@192-0-2-18"
check "the derived name is valid" \
  "$(mpp_validate_probe_name "$(mpp_default_probe_name 192.0.2.18)" &&
    printf 'yes')" "yes"
# The readable part comes first, so the key list in PRTG can be
# attributed at a glance.
check "the access key starts with the readable part" \
  "$(mpp_default_access_key 192.0.2.18 | cut -c1-10)" "192-0-2-18"
check "the access key follows the probe name" \
  "$(mpp_default_access_key 'multi-platform-probe@standort-nord' |
    cut -c1-13)" "standort-nord"
check "the access key keeps the random part" \
  "$(mpp_default_access_key 'multi-platform-probe@standort-nord' |
    sed 's/^standort-nord-//' |
    grep -cE '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')" \
  "1"
check "an access key from IPv4 is valid" \
  "$(mpp_validate_access_key "$(mpp_default_access_key 192.0.2.18)" &&
    printf 'yes')" "yes"
check "an access key from a long name is valid" \
  "$(mpp_validate_access_key \
    "$(mpp_default_access_key "probe@$(printf 'x%.0s' $(seq 1 80))")" &&
    printf 'yes')" "yes"

printf '\n== Endpoint check ==\n'

expect_failure "--check-only without --nats-host" \
  bash ./install-mpp.sh --check-only

check "the help text names --check-only" \
  "$(./install-mpp.sh --help | grep -c -- '--check-only')" "1"

# A closed port has to be recognised as a TCP problem, not surface as
# a connection error later.
endpoint_output="$(
  bash ./install-mpp.sh --nats-host 127.0.0.1 --nats-port 24897 \
    --check-only 2>&1 || true
)"
check "a closed port is named" \
  "$(printf '%s' "${endpoint_output}" |
    grep -c 'Cannot open a TCP connection')" "1"

# The two cases a plain port scan cannot distinguish: a silent service
# and a session that is only reset at the TLS upgrade. The second is the
# pattern of a firewall with application inspection.
if command -v python3 >/dev/null 2>&1; then
  endpoint_dir="$(mktemp -d)"
  cat > "${endpoint_dir}/listener.py" <<'PYTHON'
import socket
import struct
import sys
import threading

mode = sys.argv[1]
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", 0))
listener.listen(5)
print(listener.getsockname()[1], flush=True)


def handle(client):
    if mode == "silent":
        # Accepts the connection and stays silent: a TCP proxy looks
        # like this.
        import time
        time.sleep(30)
        return
    client.sendall(b'INFO {"server_name":"static-test"}\r\n')
    client.recv(4096)
    client.setsockopt(
        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
    )
    client.close()


while True:
    connection, _ = listener.accept()
    threading.Thread(target=handle, args=(connection,), daemon=True).start()
PYTHON
  # The TLS stage loads the CA before the handshake, so the reset case
  # needs a formally valid file too. EC keeps generation short.
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout "${endpoint_dir}/key.pem" -out "${endpoint_dir}/ca.pem" \
    -days 1 -nodes -subj '/CN=static-test' >/dev/null 2>&1

  for endpoint_mode in silent reset; do
    python3 "${endpoint_dir}/listener.py" "${endpoint_mode}" \
      > "${endpoint_dir}/port.${endpoint_mode}" 2>/dev/null &
    endpoint_pid=$!
    endpoint_port=""
    for _ in $(seq 1 50); do
      endpoint_port="$(cat "${endpoint_dir}/port.${endpoint_mode}" 2>/dev/null)"
      [[ -z "${endpoint_port}" ]] || break
      sleep 0.1
    done
    endpoint_output="$(
      bash ./install-mpp.sh --nats-host 127.0.0.1 \
        --nats-port "${endpoint_port}" \
        --ca-file "${endpoint_dir}/ca.pem" --check-only 2>&1 || true
    )"
    kill "${endpoint_pid}" 2>/dev/null || true
    wait "${endpoint_pid}" 2>/dev/null || true
    if [[ "${endpoint_mode}" == "silent" ]]; then
      check "a silent service is detected" \
        "$(printf '%s' "${endpoint_output}" |
          grep -c 'No NATS greeting')" "1"
    else
      check "a reset at the TLS upgrade is detected" \
        "$(printf '%s' "${endpoint_output}" |
          grep -c 'reset while upgrading to TLS')" "1"
      check "the reset message names the firewall as the cause" \
        "$(printf '%s' "${endpoint_output}" |
          grep -c 'application inspection')" "1"
    fi
  done
  rm -rf -- "${endpoint_dir}"
else
  printf '  skipped (python3 not installed)\n'
fi

printf '\n== Helper update ==\n'

# The one request that installs root code on a probe. What keeps it safe is
# the signature, so the check is that the probe's verification actually says
# no - to a file signed by a different key, and to a file that was changed
# after it was signed.
if command -v openssl >/dev/null 2>&1; then
  signing_dir="$(mktemp -d)"
  openssl ecparam -name prime256v1 -genkey -noout \
    -out "${signing_dir}/key.pem" 2>/dev/null
  openssl pkey -in "${signing_dir}/key.pem" -pubout \
    -out "${signing_dir}/key.pub" 2>/dev/null
  openssl ecparam -name prime256v1 -genkey -noout \
    -out "${signing_dir}/other.pem" 2>/dev/null
  cp libexec/prtg-nats-probe-helper "${signing_dir}/payload"

  # The same two commands the helper runs, with the same flags.
  sign_with() {
    openssl dgst -sha256 -sign "$1" "${signing_dir}/payload" | openssl base64 -A
  }
  verify_signature() {
    printf '%s' "$1" |
      openssl base64 -d -A -out "${signing_dir}/sig" &&
      openssl dgst -sha256 -verify "${signing_dir}/key.pub" \
        -signature "${signing_dir}/sig" "$2" >/dev/null 2>&1
  }

  own_signature="$(sign_with "${signing_dir}/key.pem")"
  check "a signature is one line of base64" \
    "$(printf '%s' "${own_signature}" | grep -cE '^[A-Za-z0-9+/=]+$')" "1"
  check "the probe accepts what its own key signed" \
    "$(verify_signature "${own_signature}" "${signing_dir}/payload" &&
      printf 'yes')" "yes"

  other_signature="$(sign_with "${signing_dir}/other.pem")"
  check "a signature from another key is refused" \
    "$(verify_signature "${other_signature}" "${signing_dir}/payload" ||
      printf 'refused')" "refused"

  cp "${signing_dir}/payload" "${signing_dir}/tampered"
  printf '\nrm -rf /\n' >> "${signing_dir}/tampered"
  check "a payload changed after signing is refused" \
    "$(verify_signature "${own_signature}" "${signing_dir}/tampered" ||
      printf 'refused')" "refused"

  # The declared version is what the platform compares against, and what the
  # helper reads back out of the file it just installed.
  check "the helper declares exactly one version" \
    "$(grep -cE '^HELPER_VERSION=[0-9]+$' libexec/prtg-nats-probe-helper)" "1"
  check "probe-info reports the version" \
    "$(grep -c "printf 'helper_version=%s" libexec/prtg-nats-probe-helper)" "1"
  # A copy onto the running file would cut the script off mid-execution.
  check "the new helper is moved into place, not copied over" \
    "$(grep -c 'mv -f -- "${incoming}" "${HELPER_PATH}"' \
      libexec/prtg-nats-probe-helper)" "1"

  rm -rf -- "${signing_dir}"
else
  printf '  skipped (openssl not installed)\n'
fi

printf '\n== Probe enrollment ==\n'

# The inventory is what lets a command take ADMIN@HOST instead of the account
# name. The lookup runs against real inventory files in a throwaway runtime,
# because that is the only thing it reads.
enrollment_runtime="$(mktemp -d)"
mkdir -p "${enrollment_runtime}/probes"
write_test_inventory() {
  printf 'NATS_USERNAME=%s\nSSH_HOST=%s\nSSH_PORT=22\n' "$1" "$2" \
    > "${enrollment_runtime}/probes/$1.env"
}
users_for_host() {
  (
    export PRTG_NATS_RUNTIME_DIR="${enrollment_runtime}"
    export NATS_FQDN=nats.example.test
    export NATS_HOST_IP=192.0.2.10
    # shellcheck disable=SC1091  # sourced for the helper, not for its output
    source ./libexec/common.sh
    enrolled_users_for_host "$1" | tr '\n' ' '
  )
}

write_test_inventory mpp-probe-01 probe-01.example.test
check "an enrolled host names its account" \
  "$(users_for_host probe-01.example.test)" "mpp-probe-01 "
check "an unknown host names none" \
  "$(users_for_host probe-99.example.test)" ""
# Two inventories on one host: the state a reenroll under a different account
# leaves behind. It has to stay visible, not be resolved to whichever file the
# glob returns first.
write_test_inventory mpp-probe-02 probe-01.example.test
check "an ambiguous host reports both accounts" \
  "$(users_for_host probe-01.example.test)" "mpp-probe-01 mpp-probe-02 "
rm -rf -- "${enrollment_runtime}"

check "the usage names the optional account" \
  "$(./prtg-nats probe --help | grep -c 'probe enroll \[USER\] ADMIN@HOST')" "1"

# The dispatcher decides what a single argument means before anything touches
# the network, so its refusals are checkable here - but the command requires
# an SSH client before it gets that far.
if command -v ssh >/dev/null 2>&1 && command -v ssh-keygen >/dev/null 2>&1; then
  enroll_refusal() {
    PRTG_NATS_RUNTIME_DIR="$(mktemp -d)" \
      NATS_FQDN=nats.example.test \
      NATS_HOST_IP=192.0.2.10 \
      ./prtg-nats probe enroll "$@" 2>&1 || true
  }
  check "a single argument without a host is refused" \
    "$(enroll_refusal mpp-probe-01 |
      grep -c 'probe enroll \[USER\] ADMIN@HOST')" "1"
  check "a host with no inventory asks for the account" \
    "$(enroll_refusal admin@probe-01.example.test |
      grep -c 'No probe is enrolled at probe-01.example.test')" "1"
else
  printf '  skipped (no SSH client installed)\n'
fi

# The master settles the host-key check for the whole run: every later call is
# multiplexed over its ControlPath, so its own UserKnownHostsFile is never
# looked at. The enrollment pins the key first and has to point the session at
# that file, or the pinning decides nothing and the operator is asked twice.
check "the enrollment session uses the pinned host key" \
  "$(grep -A2 'open_bootstrap_control_session "${bootstrap_target}"' \
    libexec/manage-probes.sh |
    grep -c 'UserKnownHostsFile="${SSH_KNOWN_HOSTS}"')" "1"
# install-mpp opens its session before anything is pinned, so it must not be
# pointed at the runtime file - there would be nothing in it to match.
check "the installer session is not" \
  "$(grep -A2 'open_bootstrap_control_session "${ssh_target}"' prtg-nats |
    grep -c 'UserKnownHostsFile' || true)" "0"

printf '\n== Bootstrap report ==\n'

# The template is not valid shell until it is rendered - the placeholders sit
# where values belong - so the syntax check above skips it and this one fills
# it in first. It runs as root on somebody else's machine, which is the worst
# place to find a typo.
bootstrap_dir="$(mktemp -d)"
# Rendered twice: an ordinary enrolment and one over the tunnel. The two differ
# in the order they do things, so a syntax error in either branch is a syntax
# error on somebody's console - and checking a single rendering would only ever
# catch one of them.
#
# The private key is rendered the way the platform renders it: filled in for a
# tunnel enrolment, empty for every other one.
render_bootstrap() {
  sed \
    -e 's|@@BASE_URL@@|https://nats.example.test:8443/api/v1|' \
    -e 's|@@TOKEN@@|token|' \
    -e 's|@@CA_PEM@@|-----BEGIN CERTIFICATE-----|' \
    -e 's|@@CA_SHA256@@|0000|' \
    -e 's|@@CA_FINGERPRINT@@|1111|' \
    -e 's|@@SSH_SOURCE_CIDR@@|192.0.2.0/24|' \
    -e 's|@@NATS_HOST@@|nats.example.test|' \
    -e 's|@@NATS_PORT@@|23561|' \
    -e 's|@@MANAGEMENT_PUBLIC_KEY@@|ssh-ed25519 AAAA|' \
    -e 's|@@HELPER_SIGNING_KEY@@|-----BEGIN PUBLIC KEY-----|' \
    -e 's|@@INSTALL_PACKAGE@@|true|' \
    -e 's|@@OVERLAY_ENABLED@@|true|' \
    -e 's|@@OVERLAY_MODE@@|auto|' \
    -e 's|@@OVERLAY_ADDRESS@@|10.83.1.0|' \
    -e 's|@@OVERLAY_SUBNET@@|10.83.0.0/16|' \
    -e 's|@@OVERLAY_ENDPOINT@@|nats.example.test:51820|' \
    -e 's|@@OVERLAY_HUB_KEY@@|AAAA|' \
    -e 's|@@OVERLAY_NATS_HOST_IP@@|192.0.2.10|' \
    -e "s|@@OVERLAY_FIRST@@|$1|" \
    -e "s|@@OVERLAY_PRIVATE_KEY@@|$2|" \
    bootstrap/probe-bootstrap.sh.template
}

render_bootstrap false "" > "${bootstrap_dir}/bootstrap.sh"
render_bootstrap true BBBB > "${bootstrap_dir}/bootstrap-tunnel.sh"

for rendered in "${bootstrap_dir}/bootstrap.sh" \
  "${bootstrap_dir}/bootstrap-tunnel.sh"; do
  if sh -n "${rendered}" 2>/dev/null; then
    printf '  ok    %s is valid POSIX shell\n' "$(basename "${rendered}")"
    passed=$((passed + 1))
  else
    printf '  FAIL  %s is valid POSIX shell\n' "$(basename "${rendered}")" >&2
    sh -n "${rendered}" || true
    failed=$((failed + 1))
  fi
  check "${rendered##*/} leaves no placeholder behind" \
    "$(grep -c '@@' "${rendered}" || true)" "0"
done

# The throwaway tunnel runs before the first fetch and nowhere else. If it
# ever slid below the management access again the script would be back to
# needing the platform before it can reach it - the exact deadlock this whole
# path exists to break.
tunnel_line="$(
  grep -n 'Building the overlay tunnel before anything else' \
    "${bootstrap_dir}/bootstrap-tunnel.sh" | cut -d: -f1
)"
fetch_line="$(
  grep -n '^fetch enroll-probe.sh' "${bootstrap_dir}/bootstrap-tunnel.sh" |
    cut -d: -f1
)"
if [ -n "${tunnel_line}" ] && [ -n "${fetch_line}" ] &&
  [ "${tunnel_line}" -lt "${fetch_line}" ]; then
  printf '  ok    the tunnel is built before the first fetch\n'
  passed=$((passed + 1))
else
  printf '  FAIL  the tunnel is built before the first fetch\n' >&2
  failed=$((failed + 1))
fi

# install-mpp.sh has no default for the NATS endpoint and asks for it at a
# terminal. The bootstrap arrives through a pipe and has none, so every option
# the installer insists on without one has to be on that command line. Omitting
# --nats-host cost an afternoon: the installer refused before it touched a
# package manager, the bootstrap reported back without the package, and the
# enrolment died four steps later over a missing systemd unit.
bootstrap_install_call="$(
  sed -n '/install-mpp.sh" \\/,/--no-config/p' "${bootstrap_dir}/bootstrap.sh"
)"
for option in --nats-host --nats-port --ca-file --ca-sha256 --accept-ca; do
  check "the installer is called with ${option}" \
    "$(printf '%s' "${bootstrap_install_call}" | grep -c -- "${option}")" "1"
done

# --ca-sha256 means the fingerprint of the certificate, not the hash of the
# file the certificate sits in. The bootstrap holds both and verifies its own
# copy with the file hash, so passing that one on is the easy mistake - and it
# mismatches on every certificate there is, right after the same file was
# checked successfully a few lines earlier.
check "the installer gets the fingerprint, not the file hash" \
  "$(printf '%s' "${bootstrap_install_call}" |
    grep -c -- '--ca-sha256 "${CA_FINGERPRINT}"')" "1"

# The bootstrap hands over the CA it has already written to the destination,
# so the installer is asked to copy a file onto itself. "install" refuses
# that, which turned a run that had installed the package and placed the CA
# into a reported failure.
check "the installer survives being handed the CA already in place" \
  "$(grep -c '"${CA_SOURCE}" -ef "${CA_DESTINATION}"' install-mpp.sh)" "1"
# The overlay is configured through the helper the previous step installed,
# not reimplemented in the bootstrap. Two implementations of "configure the
# tunnel" would be two ways for a probe to end up different from what the hub
# believes about it.
check "the bootstrap configures the overlay through the helper" \
  "$(grep -c "overlay-configure" "${bootstrap_dir}/bootstrap.sh")" "1"
check "the bootstrap reports the key the probe generated" \
  "$(grep -c 'overlay_public_key' "${bootstrap_dir}/bootstrap.sh")" "2"
# A private key travels in exactly one case, and it is the one that cannot work
# any other way: a probe whose only route to this platform is the tunnel itself
# cannot report a key it generated, because reporting needs that tunnel
# (ADR 0010). So the rule is not "never" any more - it is "only there".
#
# An ordinary enrolment still renders the field empty. That is what these two
# check: the placeholder exists once, and nothing fills it in unless the
# invitation asked for a tunnel enrolment.
check "the bootstrap holds one private key placeholder" \
  "$(grep -c '@@OVERLAY_PRIVATE_KEY@@' ./bootstrap/probe-bootstrap.sh.template)" "1"
check "an ordinary enrolment renders no private key" \
  "$(grep -c '^OVERLAY_PRIVATE_KEY=""$' "${bootstrap_dir}/bootstrap.sh")" "1"
check "a tunnel enrolment renders one" \
  "$(grep -c '^OVERLAY_PRIVATE_KEY="BBBB"$' \
    "${bootstrap_dir}/bootstrap-tunnel.sh")" "1"
check "the bootstrap hands it the destination path" \
  "$(printf '%s' "${bootstrap_install_call}" |
    grep -c -- '--ca-file "${CA_PATH}"')" "1"

# What the installer says when it fails is quoted straight into the report, so
# it has to survive the trip: an unescaped quote or backslash in that text
# would leave the platform with a document it cannot parse, and the reason for
# the failure would be lost exactly when it is needed.
cat > "${bootstrap_dir}/failure.log" <<'INSTALLER_OUTPUT'
E: Unable to locate package "prtgmpprobe"
E: path C:\temp is not a directory
	indented	with	tabs
INSTALLER_OUTPUT
# The function is lifted out of the script that was just rendered and sourced
# on its own, so the check exercises the code that ships rather than a copy of
# it kept in step by hand.
escaped="$(
  sh -c '
    . "$1"
    json_escape_tail "$2"
  ' _ <(sed -n '/^json_escape_tail()/,/^}/p' "${bootstrap_dir}/bootstrap.sh") \
    "${bootstrap_dir}/failure.log"
)"
if command -v python3 >/dev/null 2>&1; then
  parsed="$(
    printf '{"package_error":"%s"}' "${escaped}" |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["package_error"])'
  )"
  check "the installer output survives as one JSON string" \
    "$(printf '%s' "${parsed}" | grep -c 'Unable to locate package')" "1"
  check "a backslash in it does not break the document" \
    "$(printf '%s' "${parsed}" | grep -c 'C:\\temp')" "1"
  check "tabs are folded rather than left raw" \
    "$(printf '%s' "${escaped}" | grep -c "$(printf '\t')" || true)" "0"
else
  printf '  skipped (python3 not installed)\n'
fi

# The iperf endpoint bootstrap, the same way. It runs as root on a host that
# usually stands on a public address, so a typo here is worse than on a probe.
sed \
  -e 's|@@BASE_URL@@|https://nats.example.test:8443/api/v1|' \
  -e 's|@@TOKEN@@|token|' \
  -e 's|@@CA_PEM@@|-----BEGIN CERTIFICATE-----|' \
  -e 's|@@CA_SHA256@@|0000|' \
  -e 's|@@SSH_SOURCE_CIDR@@|192.0.2.0/24,203.0.113.7/32|' \
  -e 's|@@MANAGEMENT_PUBLIC_KEY@@|ssh-ed25519 AAAA|' \
  bootstrap/iperf-bootstrap.sh.template > "${bootstrap_dir}/iperf-bootstrap.sh"
if sh -n "${bootstrap_dir}/iperf-bootstrap.sh" 2>/dev/null; then
  printf '  ok    the rendered iperf bootstrap is valid POSIX shell\n'
  passed=$((passed + 1))
else
  printf '  FAIL  the rendered iperf bootstrap is valid POSIX shell\n' >&2
  sh -n "${bootstrap_dir}/iperf-bootstrap.sh" || true
  failed=$((failed + 1))
fi

check "the rendered iperf bootstrap leaves no placeholder behind" \
  "$(grep -c '@@' "${bootstrap_dir}/iperf-bootstrap.sh" || true)" "0"

# Without the source CIDR the enrolment script refuses, and it refuses on a
# console the operator has already left. Worse, an empty one would be a
# management key valid from anywhere.
check "the iperf enrolment is called with --source-cidr" \
  "$(grep -c -- '--source-cidr "${SSH_SOURCE_CIDR}"' \
    "${bootstrap_dir}/iperf-bootstrap.sh")" "1"

# Nothing secret may sit in this file: fetching it does not spend the
# invitation, so it stays readable for as long as the token lives. The
# endpoint's password arrives over the management channel instead, and no
# placeholder here may ever be filled with one.
check "the iperf bootstrap has no placeholder for a secret" \
  "$(grep -cE '@@[A-Z_]*(PASSWORD|SECRET|CREDENTIAL|PRIVATE)[A-Z_]*@@' \
    bootstrap/iperf-bootstrap.sh.template || true)" "0"

# Every asset the bootstrap fetches has to be one the platform is willing to
# serve. The two lists live in different languages and drift silently: the
# bootstrap would fail halfway through, as root, on somebody else's machine.
for asset in $(
  grep -oE '^fetch [a-z0-9.-]+' bootstrap/iperf-bootstrap.sh.template |
    awk '{print $2}'
); do
  check "the platform serves ${asset}" \
    "$(grep -q "\"${asset}\"" web/backend/app/services/enrollment.py &&
      printf 'yes' || printf 'no')" "yes"
done

rm -rf -- "${bootstrap_dir}"

printf '\n== Result ==\n'
printf '  passed: %s\n' "${passed}"
printf '  failed: %s\n' "${failed}"
[[ "${failed}" -eq 0 ]]
