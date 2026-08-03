#!/usr/bin/env bash

# End-to-end test of the MPP rollout against a real prtgmpprobe package.
#
# Builds two throwaway containers: a "probe" with systemd and SSH, plus an
# "admin" host that runs ./prtg-nats. In between runs this repository's
# real NATS stack. What is checked is the complete sequence: enrollment,
# package installation from the Paessler repository, rollout of the
# generated configuration, service start, NATS connection, a repeat run
# without bootstrap, and the password rotation.
#
# Prerequisites: Docker with the ability to start privileged containers
# (systemd), plus network access to packages.paessler.com and a wildcard
# DNS service. If anything is missing, the script aborts early with a
# clear reason.

set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="${PROJECT_DIR}/tests/e2e"
WORK_DIR=""
NETWORK_NAME="prtg-e2e-net"
PROBE_CONTAINER="prtg-e2e-probe"
ADMIN_CONTAINER="prtg-e2e-admin"
PROBE_HOSTNAME="oe-prtg-e2e"
NATS_USER="mpp-oe-prtg-e2e"
# Wildcard DNS: resolves to the NATS container over the alias on the
# Docker network, but to 127.0.0.1 on the host network stack. Both are
# needed because test_nats_credentials starts the client with
# --network host.
NATS_FQDN="127-0-0-1.sslip.io"
KEEP_ENVIRONMENT="${PRTG_E2E_KEEP:-false}"

passed=0
failed=0

log() {
  printf '\n=== %s ===\n' "$*"
}

check() {
  local description="$1"
  local actual="$2"
  local expected="$3"

  if [[ "${actual}" == "${expected}" ]]; then
    printf '  ok    %s\n' "${description}"
    passed=$((passed + 1))
  else
    printf '  FAIL  %s\n' "${description}" >&2
    printf '        erwartet: %s\n' "${expected}" >&2
    printf '        erhalten: %s\n' "${actual}" >&2
    failed=$((failed + 1))
  fi
}

check_contains() {
  local description="$1"
  local haystack="$2"
  local needle="$3"

  if [[ "${haystack}" == *"${needle}"* ]]; then
    printf '  ok    %s\n' "${description}"
    passed=$((passed + 1))
  else
    printf '  FAIL  %s (missing: %s)\n' "${description}" "${needle}" >&2
    failed=$((failed + 1))
  fi
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

skip() {
  printf '\nSKIPPED: %s\n' "$*" >&2
  exit "${PRTG_E2E_SKIP_CODE:-1}"
}

cleanup() {
  local exit_code="$?"

  if [[ "${KEEP_ENVIRONMENT}" == "true" ]]; then
    printf '\nThe test environment stays up (PRTG_E2E_KEEP=true).\n'
    return "${exit_code}"
  fi
  printf '\nCleaning up the test environment...\n'
  if [[ -n "${WORK_DIR}" ]]; then
    docker exec "${ADMIN_CONTAINER}" \
      docker compose --project-directory "${WORK_DIR}" down --volumes \
      >/dev/null 2>&1 || true
  fi
  docker rm -f "${PROBE_CONTAINER}" "${ADMIN_CONTAINER}" >/dev/null 2>&1 || true
  docker network rm "${NETWORK_NAME}" >/dev/null 2>&1 || true
  # The working directory lives on the Docker host and is not
  # necessarily visible from here; it is therefore removed through a
  # helper container.
  if [[ "${WORK_DIR}" == /tmp/prtg-e2e.* ]]; then
    docker run --rm -v /tmp:/hosttmp debian:12 \
      rm -rf -- "/hosttmp/${WORK_DIR#/tmp/}" >/dev/null 2>&1 || true
  fi
  return "${exit_code}"
}

log "Prerequisites"
command -v docker >/dev/null 2>&1 || skip "Docker is not available"
docker info >/dev/null 2>&1 || skip "the Docker daemon is not reachable"
docker run --rm --privileged debian:12 true >/dev/null 2>&1 ||
  skip "privileged containers are not allowed; systemd cannot be started"
docker run --rm --network host debian:12 getent hosts "${NATS_FQDN}" \
  >/dev/null 2>&1 ||
  skip "${NATS_FQDN} does not resolve; wildcard DNS is required"
docker run --rm debian:12 bash -c \
  'command -v curl >/dev/null || exit 0' >/dev/null 2>&1 || true
printf '  Docker, privileged containers and DNS are ready.\n'

host_architecture="$(docker version --format '{{.Server.Arch}}')"
case "${host_architecture}" in
  amd64) compose_architecture="x86_64" ;;
  arm64) compose_architecture="aarch64" ;;
  *) skip "unsupported architecture for this test: ${host_architecture}" ;;
esac
printf '  Architecture: %s\n' "${host_architecture}"

trap cleanup EXIT

log "Building images"
docker build -q -t prtg-e2e-probe -f "${TEST_DIR}/Dockerfile.probe" \
  "${TEST_DIR}" >/dev/null
docker build -q -t prtg-e2e-admin \
  --build-arg "COMPOSE_ARCH=${compose_architecture}" \
  -f "${TEST_DIR}/Dockerfile.admin" "${TEST_DIR}" >/dev/null ||
  die "The admin image could not be built (network access to github.com?)"
printf '  Images are ready.\n'

log "Creating the repository copy"
# The compose stack bind-mounts ./runtime. Such paths are always
# resolved by the Docker host, not the caller. If this script itself runs
# in a container - in Gitea Actions, say - a directory created here would
# be invisible to the host and the admin container would get an empty
# one. The working directory is therefore created and filled directly on
# the host through helper containers. That works in both cases.
WORK_DIR="/tmp/prtg-e2e.$(
  head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n'
)"
docker run --rm -v /tmp:/hosttmp debian:12 \
  mkdir -p "/hosttmp/${WORK_DIR#/tmp/}"
# Local build artefacts must not travel along: a macOS .venv, say,
# would poison the backend installation in the Linux admin container.
tar -C "${PROJECT_DIR}" --exclude=.git --exclude=runtime \
  --exclude='*/.venv' --exclude='*/node_modules' --exclude='*/dist' \
  --exclude='*/__pycache__' --exclude='*/.pytest_cache' \
  --exclude='*/.mypy_cache' --exclude='*/.ruff_cache' -cf - . |
  docker run -i --rm -v "${WORK_DIR}:/dst" debian:12 tar -C /dst -xf -
docker run --rm -v "${WORK_DIR}:/dst" debian:12 test -x /dst/prtg-nats ||
  die "The repository copy on the Docker host is incomplete"
printf '  %s (on the Docker host)\n' "${WORK_DIR}"

log "Starting containers"
docker network rm "${NETWORK_NAME}" >/dev/null 2>&1 || true
docker network create "${NETWORK_NAME}" >/dev/null
docker run -d --name "${PROBE_CONTAINER}" --hostname "${PROBE_HOSTNAME}" \
  --network "${NETWORK_NAME}" --network-alias "${PROBE_HOSTNAME}" \
  --privileged --cgroupns=host \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  prtg-e2e-probe >/dev/null
docker run -d --name "${ADMIN_CONTAINER}" --hostname prtg-e2e-admin \
  --network "${NETWORK_NAME}" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${WORK_DIR}:${WORK_DIR}" -w "${WORK_DIR}" \
  prtg-e2e-admin >/dev/null

for _ in $(seq 1 30); do
  [[ "$(
    docker exec "${PROBE_CONTAINER}" systemctl is-system-running 2>/dev/null ||
      true
  )" =~ ^(running|degraded)$ ]] && break
  sleep 2
done
check "systemd runs on the probe" \
  "$(docker exec "${PROBE_CONTAINER}" systemctl is-active ssh 2>&1)" "active"

log "Preparing the SSH bootstrap"
docker exec "${ADMIN_CONTAINER}" bash -c '
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  ssh-keygen -q -t ed25519 -N "" -f /root/.ssh/id_ed25519
'
bootstrap_key="$(docker exec "${ADMIN_CONTAINER}" cat /root/.ssh/id_ed25519.pub)"
docker exec "${PROBE_CONTAINER}" bash -c "
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  printf '%s\n' '${bootstrap_key}' > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
"
printf '  Bootstrap key deposited.\n'

log "Setting up the NATS server"
admin_address="$(
  docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
    "${ADMIN_CONTAINER}"
)"

# Fixed ports collide on shared machines, on a CI runner say ("port is
# already allocated"). The ports are therefore found at run time. Side
# effect: the test runs on a port other than 23561 and thereby also
# proves that NATS_PORT really takes effect everywhere.
find_free_port() {
  local candidate=""

  for _ in $(seq 1 40); do
    candidate=$(( 20000 + (RANDOM % 20000) ))
    if ! docker run --rm --network host debian:12 \
      timeout 1 bash -c "</dev/tcp/127.0.0.1/${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

nats_port="$(find_free_port)" || die "No free port found for NATS"
ca_port="$(find_free_port)" || die "No free port found for the CA endpoint"
[[ "${nats_port}" != "${ca_port}" ]] ||
  die "The port search returned the same port twice"
printf '  NATS port %s, CA port %s\n' "${nats_port}" "${ca_port}"

docker exec "${ADMIN_CONTAINER}" bash -c "cat > ${WORK_DIR}/.env <<EOF
NATS_FQDN=${NATS_FQDN}
NATS_PORT=${nats_port}
NATS_HOST_IP=127.0.0.1
CA_HTTP_PORT=${ca_port}
PRTG_CORE_IP=127.0.0.1
MPP_SSH_SOURCE_CIDR=${admin_address}/32
EOF
chmod 600 ${WORK_DIR}/.env"
# The recovery commands (init, user rotate) run the same Python services the
# web platform uses; install the backend once in the admin container.
docker exec "${ADMIN_CONTAINER}" bash -c "
  python3 -m venv ${WORK_DIR}/web/backend/.venv &&
  ${WORK_DIR}/web/backend/.venv/bin/pip install --quiet ${WORK_DIR}/web/backend
" || die "The backend installation in the admin container failed"
docker exec "${ADMIN_CONTAINER}" ./prtg-nats init >/dev/null
docker exec "${ADMIN_CONTAINER}" \
  docker compose --project-directory "${WORK_DIR}" up -d nats ca-download \
  >/dev/null
for _ in $(seq 1 30); do
  [[ "$(
    docker inspect -f '{{.State.Health.Status}}' prtg-nats 2>/dev/null || true
  )" == "healthy" ]] && break
  sleep 2
done
check "NATS is healthy" \
  "$(docker inspect -f '{{.State.Health.Status}}' prtg-nats 2>/dev/null)" \
  "healthy"
docker network connect --alias "${NATS_FQDN}" "${NETWORK_NAME}" prtg-nats
# The monitoring listens on the Docker host; in production prtg-nats
# runs there as well. For the test the port is forwarded.
docker exec -d "${ADMIN_CONTAINER}" \
  socat TCP-LISTEN:8222,fork,reuseaddr "TCP:${NATS_FQDN}:8222"
sleep 3

log "First run: enrollment, package, configuration"
docker exec "${ADMIN_CONTAINER}" \
  expect ./tests/e2e/install.exp "${NATS_USER}" "root@${PROBE_HOSTNAME}" \
  > /tmp/first-run.log 2>&1 ||
  {
    tail -40 /tmp/first-run.log >&2
    die "The first run failed"
  }
first_run="$(cat /tmp/first-run.log)"
check_contains "enrollment performed" "${first_run}" \
  "Enrolled ${NATS_USER}"
check_contains "package installed" "${first_run}" "Setting up prtgmpprobe"
check_contains "configuration rolled out" "${first_run}" \
  "Configuration applied"
check_contains "access key printed" "${first_run}" "Probe Access Key"
check "no terminal warning" \
  "$(grep -c 'Pseudo-terminal' /tmp/first-run.log || true)" "0"

log "State on the probe"
check "the service is active" \
  "$(docker exec "${PROBE_CONTAINER}" \
    systemctl is-active prtg.mpprobe.service 2>&1)" "active"
# The service runs as its own user and has to be able to read the
# configuration; 0600 root:root would make it fail.
config_group="$(
  docker exec "${PROBE_CONTAINER}" \
    stat -c '%G' /etc/paessler/mpprobe/config.yaml 2>&1
)"
service_group="$(
  docker exec "${PROBE_CONTAINER}" \
    awk -F= '/^Group=/ { print $2 }' /lib/systemd/system/prtg.mpprobe.service
)"
check "the configuration belongs to the service group" \
  "${config_group}" "${service_group}"
check "the configuration is readable for the group" \
  "$(docker exec "${PROBE_CONTAINER}" \
    stat -c '%a' /etc/paessler/mpprobe/config.yaml 2>&1)" "640"
check "the configuration is not world-readable" \
  "$(docker exec "${PROBE_CONTAINER}" bash -c \
    'stat -c "%a" /etc/paessler/mpprobe/config.yaml | cut -c3')" "0"

log "NATS connection"
connection_user=""
for _ in $(seq 1 30); do
  connection_user="$(
    docker exec "${ADMIN_CONTAINER}" bash -c '
      curl --fail --silent "http://127.0.0.1:8222/connz?auth=1&state=open" |
        python3 -c "
import sys, json
data = json.load(sys.stdin)
for connection in data[\"connections\"]:
    print(connection.get(\"authorized_user\", \"\"))
"' 2>/dev/null | grep -F "${NATS_USER}" || true
  )"
  [[ -n "${connection_user}" ]] && break
  sleep 2
done
check "the probe is signed in to NATS" "${connection_user}" "${NATS_USER}"

log "Probe identity"
first_key="$(
  docker exec "${ADMIN_CONTAINER}" bash -c \
    "grep '^ACCESS_KEY=' runtime/probes/${NATS_USER}.env | cut -d= -f2-"
)"
first_id="$(
  docker exec "${ADMIN_CONTAINER}" bash -c \
    "grep '^PROBE_ID=' runtime/probes/${NATS_USER}.env | cut -d= -f2-"
)"
[[ -n "${first_key}" ]] || die "No access key in the inventory"
printf '  Access Key: %s\n' "${first_key}"

log "Second run without a bootstrap target"
second_run="$(
  docker exec "${ADMIN_CONTAINER}" \
    ./prtg-nats install-mpp --nats-user "${NATS_USER}" 2>&1
)"
check_contains "key access is checked first" "${second_run}" \
  "Checking the restricted management access"
check_contains "no bootstrap needed" "${second_run}" \
  "no bootstrap login is required"
check_contains "configuration rolled out again" "${second_run}" \
  "Configuration applied"
check "access key unchanged" \
  "$(docker exec "${ADMIN_CONTAINER}" bash -c \
    "grep '^ACCESS_KEY=' runtime/probes/${NATS_USER}.env | cut -d= -f2-")" \
  "${first_key}"
check "probe id unchanged" \
  "$(docker exec "${ADMIN_CONTAINER}" bash -c \
    "grep '^PROBE_ID=' runtime/probes/${NATS_USER}.env | cut -d= -f2-")" \
  "${first_id}"

log "Fleet overview"
# The overview reports findings through the exit code. Under "set -e" a
# plain assignment would end the test right here, hence if/else.
if overview_output="$(
  docker exec "${ADMIN_CONTAINER}" ./prtg-nats probe status --all 2>&1
)"; then
  overview_status=0
else
  overview_status="$?"
fi
printf '%s\n' "${overview_output}" | sed 's/^/  /'
check "the overview reports no findings" "${overview_status}" "0"
check_contains "the probe is in the overview" "${overview_output}" \
  "${NATS_USER}"
check_contains "the service is reported active" "${overview_output}" "active"
check_contains "the CA matches the runtime CA" "${overview_output}" "ok"
check_contains "the NATS connection is detected" "${overview_output}" "connected"

overview_json="$(
  docker exec "${ADMIN_CONTAINER}" \
    ./prtg-nats probe status --all --format json 2>/dev/null
)"
check "the JSON is valid and reports no problems" \
  "$(printf '%s' "${overview_json}" |
    docker exec -i "${ADMIN_CONTAINER}" python3 -c '
import sys, json
data = json.load(sys.stdin)
print(data["problems"], len(data["probes"]))
' 2>/dev/null)" "0 1"

# An unreachable probe must not block the overview; it has to appear
# as a finding.
docker stop "${PROBE_CONTAINER}" >/dev/null
if offline_output="$(
  docker exec "${ADMIN_CONTAINER}" ./prtg-nats probe status --all 2>&1
)"; then
  offline_status=0
else
  offline_status="$?"
fi
check "a powered-off probe reports a finding" "${offline_status}" "1"
check_contains "the powered-off probe is named" "${offline_output}" \
  "unreachable"
docker start "${PROBE_CONTAINER}" >/dev/null
for _ in $(seq 1 30); do
  docker exec "${PROBE_CONTAINER}" \
    systemctl is-active prtg.mpprobe.service >/dev/null 2>&1 && break
  sleep 2
done

log "Password rotation"
# A failed rotation should appear as FAIL with its output, not abort
# the script through set -e and swallow the error message.
if rotation_output="$(
  docker exec "${ADMIN_CONTAINER}" \
    ./prtg-nats user rotate "${NATS_USER}" 2>&1
)"; then
  :
else
  printf '  Rotation output:\n%s\n' "${rotation_output}" >&2
fi
check_contains "rotation completed" "${rotation_output}" "OK committed"
reconnected=""
for _ in $(seq 1 30); do
  reconnected="$(
    docker exec "${ADMIN_CONTAINER}" bash -c '
      curl --fail --silent "http://127.0.0.1:8222/connz?auth=1&state=open" |
        python3 -c "
import sys, json
data = json.load(sys.stdin)
for connection in data[\"connections\"]:
    print(connection.get(\"authorized_user\", \"\"))
"' 2>/dev/null | grep -F "${NATS_USER}" || true
  )"
  [[ -n "${reconnected}" ]] && break
  sleep 2
done
check "the probe reconnected after the rotation" "${reconnected}" "${NATS_USER}"

log "Result"
printf '  passed: %s\n' "${passed}"
printf '  failed: %s\n' "${failed}"
[[ "${failed}" -eq 0 ]]
