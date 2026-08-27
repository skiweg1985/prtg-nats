#!/usr/bin/env bash
#
# Does a container without compose labels actually survive the stack being
# recreated around it?
#
# The whole update design rests on that one claim. `docker compose up` replaces
# prtg-nats-web-api, so the thing driving the update cannot live inside it -
# and the reason we believe an unlabelled container is safe is that
# --remove-orphans collects candidates by the project label. That is a claim
# about how somebody else's software behaves, made in
# docs/architecture/decisions/0007-update-the-stack-from-the-interface.md, and
# a design resting on an unverified claim about Docker rests on nothing.
#
# So this proves it against a real daemon, on a throwaway project rather than
# the real stack: building the real images twice takes twenty minutes and
# proves nothing more about the part in question. What it does not cover is
# the full round trip through the API and the interface - that is the live
# update on a real installation, and it is still owed.
#
# Five things are checked, and each one is a sentence from that ADR:
#
#   1. compose records the checkout on every container it creates
#   2. the updater is still there after the recreate, with its exit code
#   3. its log still holds what it wrote *after* the recreate
#   4. --remove-orphans did not collect it
#   5. the labelled service really was replaced, so 2-4 are not vacuous
#
# Needs Docker and the updater image. Runs in seconds.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

PROJECT_NAME="prtg-nats-e2e-update-$$"
UPDATER_NAME="${PROJECT_NAME}-updater"
WORK_DIR=""

# The image the real updater runs from. Using it rather than a bare alpine is
# the point: it is the one that has to carry a working compose client, and a
# check that quietly substituted a different image would stop covering that.
UPDATER_IMAGE="prtg-nats-updater:current"
# What the throwaway stack is made of. Pinned like everything in compose.yaml.
STACK_IMAGE="alpine:3.22"

passed=0
failed=0

check() {
  local name="$1"
  shift
  if "$@"; then
    printf '  ok    %s\n' "${name}"
    passed=$((passed + 1))
  else
    printf '  FAIL  %s\n' "${name}" >&2
    failed=$((failed + 1))
  fi
}

contains() {
  [[ "$1" == *"$2"* ]]
}

cleanup() {
  docker rm -f "${UPDATER_NAME}" >/dev/null 2>&1 || true
  if [[ -n "${WORK_DIR}" && -f "${WORK_DIR}/compose.yaml" ]]; then
    docker compose --project-directory "${WORK_DIR}" -p "${PROJECT_NAME}" \
      down --remove-orphans --timeout 1 >/dev/null 2>&1 || true
  fi
  [[ -z "${WORK_DIR}" ]] || rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

if ! docker info >/dev/null 2>&1; then
  printf 'Docker is not available; this check needs it.\n' >&2
  exit 1
fi

if ! docker image inspect "${UPDATER_IMAGE}" >/dev/null 2>&1; then
  printf 'The updater image is missing. Build it first:\n' >&2
  printf '  docker build -f web/updater/Dockerfile -t %s %s\n' \
    "${UPDATER_IMAGE}" "${PROJECT_DIR}" >&2
  exit 1
fi

printf '== A stack to recreate ==\n'

WORK_DIR="$(mktemp -d)"
cat > "${WORK_DIR}/compose.yaml" <<COMPOSE
name: ${PROJECT_NAME}
services:
  victim:
    image: ${STACK_IMAGE}
    command: ["sleep", "600"]
    restart: "no"
COMPOSE

docker compose --project-directory "${WORK_DIR}" -p "${PROJECT_NAME}" up -d \
  >/dev/null 2>&1
before="$(docker compose --project-directory "${WORK_DIR}" -p "${PROJECT_NAME}" \
  ps -q victim)"
check "the stack is up" test -n "${before}"

# The label the backend finds the checkout by. Read here exactly the way
# DockerAdapter.compose_project() reads it.
working_dir="$(docker inspect "${before}" \
  --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' \
  2>/dev/null || printf '')"
check "compose records the project directory on the container" \
  test "${working_dir}" = "${WORK_DIR}"

printf '\n== The updater, without compose labels ==\n'

# Created rather than run, so nothing is missed between start and attach - and
# created with no project label at all, exactly as create_updater does. It
# recreates the stack it is not part of, then keeps writing: those later lines
# are the proof that it outlived the recreate rather than being restarted.
#
# The checkout is mounted at its own path here too, for the same reason the
# real one does it: compose hands the daemon host paths.
docker create --name "${UPDATER_NAME}" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${WORK_DIR}:${WORK_DIR}" \
  -w "${WORK_DIR}" \
  --entrypoint sh \
  "${UPDATER_IMAGE}" -c "
    echo before-recreate
    docker compose -p ${PROJECT_NAME} up -d --force-recreate --remove-orphans \
      >/dev/null 2>&1 || echo compose-failed
    echo after-recreate
  " >/dev/null

docker start "${UPDATER_NAME}" >/dev/null
exit_code="$(docker wait "${UPDATER_NAME}")"

printf '\n== What survived ==\n'

check "the updater ran to completion" test "${exit_code}" = "0"

# Still there, with its log. Had --remove-orphans collected it, both would be
# gone - and with them any way for the next process to learn how it went.
state="$(docker inspect "${UPDATER_NAME}" --format '{{.State.Status}}' \
  2>/dev/null || printf 'gone')"
check "the updater still exists after the recreate" test "${state}" = "exited"

log="$(docker logs "${UPDATER_NAME}" 2>&1 || printf '')"
check "its log survived the recreate" contains "${log}" "before-recreate"
check "it kept running past the recreate" contains "${log}" "after-recreate"
check "the recreate itself worked" bash -c '[[ "$1" != *compose-failed* ]]' _ "${log}"

# The same filter compose uses internally to find orphans.
remaining="$(docker ps -aq --filter "name=${UPDATER_NAME}" | wc -l | tr -d ' ')"
check "--remove-orphans left the unlabelled container alone" \
  test "${remaining}" = "1"

# And the control, without which the check above proves nothing.
#
# A real orphan, not a hand-labelled impostor: a service compose started
# itself, then removed from the file. Labelling a foreign container by hand
# does not produce one - tried, and compose leaves it alone - so a control
# built that way would pass while proving nothing about the sweep.
cat > "${WORK_DIR}/compose.yaml" <<COMPOSE
name: ${PROJECT_NAME}
services:
  victim:
    image: ${STACK_IMAGE}
    command: ["sleep", "600"]
    restart: "no"
  departing:
    image: ${STACK_IMAGE}
    command: ["sleep", "600"]
    restart: "no"
COMPOSE
docker compose --project-directory "${WORK_DIR}" -p "${PROJECT_NAME}" up -d \
  >/dev/null 2>&1
departing="$(docker compose --project-directory "${WORK_DIR}" \
  -p "${PROJECT_NAME}" ps -q departing 2>/dev/null || printf '')"
check "the second service started" test -n "${departing}"

# Out of the file, and now it is an orphan.
cat > "${WORK_DIR}/compose.yaml" <<COMPOSE
name: ${PROJECT_NAME}
services:
  victim:
    image: ${STACK_IMAGE}
    command: ["sleep", "600"]
    restart: "no"
COMPOSE
docker compose --project-directory "${WORK_DIR}" -p "${PROJECT_NAME}" up -d \
  --remove-orphans >/dev/null 2>&1 || true

swept="$(docker ps -aq --filter "id=${departing}" | wc -l | tr -d ' ')"
check "--remove-orphans does collect a real orphan, so the sweep is real" \
  test "${swept}" = "0"

# And the labelled service really was replaced, so none of the above is
# vacuous: a recreate that did nothing would pass every check up to here.
after="$(docker compose --project-directory "${WORK_DIR}" -p "${PROJECT_NAME}" \
  ps -q victim 2>/dev/null || printf '')"
check "the labelled service was in fact replaced" \
  bash -c '[[ -n "$1" && "$1" != "$2" ]]' _ "${after}" "${before}"

printf '\n== Result ==\n'
printf '  passed: %d\n' "${passed}"
printf '  failed: %d\n' "${failed}"
[[ ${failed} -eq 0 ]]
