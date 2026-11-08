#!/usr/bin/env bash

# Installs the restricted management access on an iperf3 measurement endpoint.
# The counterpart to enroll-probe.sh, and deliberately its own file: the two
# hosts get different accounts, different vocabularies and different sudo
# rules, and a shared script with two modes would be one edit away from
# handing a measurement endpoint the probe's rights.
#
# Three differences to enroll-probe.sh are the whole of it:
#
#   1. No helper signing key. The iperf helper is not updated over its own
#      channel - four requests that do not change are not worth a signature
#      chain, and an update is a fresh invitation.
#   2. setup-iperf3-endpoint.sh is installed next to the helper, because the
#      helper calls it rather than reimplementing what it does.
#   3. The sudo rule keeps SSH_CLIENT. The platform cannot know which address
#      it appears under from here, and that address is what "from=" has to
#      name - so the endpoint reports it back rather than being guessed at.
#
# Optionally it also installs the release build of iperf3 the platform staged
# for this host. Distributions ship releases older than the 3.17 authenticated
# endpoints need, and an administrator who signed in to set the host up is the
# one moment this can happen without the restricted channel learning to install
# executables - which it must not.

set -Eeuo pipefail

PUBLIC_KEY_FILE=""
HELPER_FILE=""
SETUP_SCRIPT_FILE=""
SOURCE_CIDR=""
MANAGEMENT_USER="prtg-nats-iperf"
MANAGEMENT_HOME="/var/lib/prtg-nats-iperf"
HELPER_PATH="/usr/local/sbin/prtg-nats-iperf-helper"
SETUP_SCRIPT_PATH="/usr/local/sbin/setup-iperf3-endpoint.sh"
SUDOERS_PATH="/etc/sudoers.d/prtg-nats-iperf"
IPERF3_FILE=""
IPERF3_SHA256=""
IPERF3_VERSION=""
IPERF3_PLATFORM=""
# Kept apart from /opt/prtg-nats/tools, which belongs to the probe helper: a
# host can be a probe and an endpoint at once, and the two must not move each
# other's "current" link. setup-iperf3-endpoint.sh reads the same path.
ENDPOINT_TOOL_DIR="/opt/prtg-nats/endpoint/iperf3"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

validate_single_cidr() {
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

# A comma separated list, because "from=" takes one. It matters more here than
# on a probe: an endpoint is often reached from an internal address and from
# the outside, and both belong in the same rule. Every element is checked on
# its own - a list is only as good as its weakest entry.
validate_source_cidr() {
  local source_cidr="$1"
  local elements=()
  local element=""

  [[ -n "${source_cidr}" ]] || return 1
  [[ "${source_cidr}" != *,,* && "${source_cidr}" != ,* && \
     "${source_cidr}" != *, ]] || return 1
  IFS=',' read -r -a elements <<< "${source_cidr}"
  [[ "${#elements[@]}" -gt 0 ]] || return 1
  for element in "${elements[@]}"; do
    validate_single_cidr "${element}" || return 1
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --public-key)
      PUBLIC_KEY_FILE="${2:-}"
      shift 2
      ;;
    --helper)
      HELPER_FILE="${2:-}"
      shift 2
      ;;
    --setup-script)
      SETUP_SCRIPT_FILE="${2:-}"
      shift 2
      ;;
    --source-cidr)
      SOURCE_CIDR="${2:-}"
      shift 2
      ;;
    --iperf3)
      IPERF3_FILE="${2:-}"
      shift 2
      ;;
    --iperf3-sha256)
      IPERF3_SHA256="${2:-}"
      shift 2
      ;;
    --iperf3-version)
      IPERF3_VERSION="${2:-}"
      shift 2
      ;;
    --iperf3-platform)
      IPERF3_PLATFORM="${2:-}"
      shift 2
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || die "Enrollment must run as root"
[[ -f "${PUBLIC_KEY_FILE}" ]] || die "Public key file is missing"
[[ -f "${HELPER_FILE}" ]] || die "iperf helper file is missing"
# Required, not optional. The helper calls this script for every piece of work
# it does; without it the channel would install fine and refuse every request.
[[ -f "${SETUP_SCRIPT_FILE}" ]] || die "Endpoint setup script is missing"
validate_source_cidr "${SOURCE_CIDR}" ||
  die "Invalid SSH source CIDR"
command -v useradd >/dev/null 2>&1 || die "useradd is required"
command -v passwd >/dev/null 2>&1 || die "passwd is required"
command -v sudo >/dev/null 2>&1 || die "sudo is required"
grep -E '^ssh-ed25519 [A-Za-z0-9+/=]+ ' "${PUBLIC_KEY_FILE}" >/dev/null ||
  die "Expected an Ed25519 public key"
if [[ -n "${IPERF3_FILE}${IPERF3_SHA256}${IPERF3_VERSION}${IPERF3_PLATFORM}" ]]; then
  # All four or none: a build without its digest is a build nobody checked.
  [[ -f "${IPERF3_FILE}" ]] || die "The staged iperf3 build is missing"
  [[ "${IPERF3_SHA256}" =~ ^[0-9a-f]{64}$ ]] || die "Invalid iperf3 digest"
  [[ "${IPERF3_VERSION}" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "Invalid iperf3 version"
  [[ "${IPERF3_PLATFORM}" =~ ^linux-[a-z0-9][a-z0-9_-]*-glibc$ ]] ||
    die "Invalid iperf3 platform"
  command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"
fi

# Checks the staged build against the digest the platform sent and against the
# version it claims, then switches "current" over in one rename. A build that
# fails either check is not installed at all; the endpoint then keeps whatever
# it had, and the setup's own version check decides whether that is enough.
install_release_iperf3() {
  local target_dir="${ENDPOINT_TOOL_DIR}/${IPERF3_VERSION}/${IPERF3_PLATFORM}"
  local candidate=""
  local reported=""

  [[ "$(sha256sum "${IPERF3_FILE}" | awk '{print $1}')" == "${IPERF3_SHA256}" ]] ||
    die "The staged iperf3 build does not match its digest"
  install -d -o root -g root -m 0755 "${target_dir}"
  candidate="$(mktemp "${target_dir}/.iperf3.XXXXXX")"
  install -o root -g root -m 0755 "${IPERF3_FILE}" "${candidate}"
  reported="$("${candidate}" --version 2>&1 | awk 'NR == 1 { print $2 }' || true)"
  if [[ "${reported}" != "${IPERF3_VERSION}" ]]; then
    rm -f -- "${candidate}"
    die "The staged iperf3 build reports ${reported:-nothing} instead of ${IPERF3_VERSION}"
  fi
  if ! "${candidate}" --version 2>&1 | grep -qi 'authentication'; then
    rm -f -- "${candidate}"
    die "The staged iperf3 build lacks authentication support"
  fi
  mv -f -- "${candidate}" "${target_dir}/iperf3"
  ln -sfn -- "${IPERF3_VERSION}/${IPERF3_PLATFORM}" "${ENDPOINT_TOOL_DIR}/.current.new"
  mv -fT -- "${ENDPOINT_TOOL_DIR}/.current.new" "${ENDPOINT_TOOL_DIR}/current"
  # Earlier releases go once nothing points at them any more. The running
  # service keeps its open file until the setup restarts it on the new one.
  find "${ENDPOINT_TOOL_DIR}" -mindepth 1 -maxdepth 1 -type d \
    ! -name "${IPERF3_VERSION}" -exec rm -rf -- {} +
  printf 'Installed the release build of iperf3 %s (%s).\n' \
    "${IPERF3_VERSION}" "${IPERF3_PLATFORM}"
}

if ! id "${MANAGEMENT_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --create-home \
    --home-dir "${MANAGEMENT_HOME}" \
    --shell /bin/sh \
    "${MANAGEMENT_USER}"
fi
passwd -l "${MANAGEMENT_USER}" >/dev/null 2>&1 || true

install -o root -g root -m 0755 "${HELPER_FILE}" "${HELPER_PATH}"
install -o root -g root -m 0755 "${SETUP_SCRIPT_FILE}" "${SETUP_SCRIPT_PATH}"
if [[ -n "${IPERF3_FILE}" ]]; then
  install_release_iperf3
fi
install -d -o "${MANAGEMENT_USER}" -g "${MANAGEMENT_USER}" -m 0700 \
  "${MANAGEMENT_HOME}/.ssh"

public_key="$(<"${PUBLIC_KEY_FILE}")"
{
  printf 'from="%s",restrict,' "${SOURCE_CIDR}"
  printf 'command="sudo -n %s" ' "${HELPER_PATH}"
  printf '%s\n' "${public_key}"
} > "${MANAGEMENT_HOME}/.ssh/authorized_keys"
chown "${MANAGEMENT_USER}:${MANAGEMENT_USER}" \
  "${MANAGEMENT_HOME}/.ssh/authorized_keys"
chmod 0600 "${MANAGEMENT_HOME}/.ssh/authorized_keys"

# SSH_CLIENT survives the sudo call so that "endpoint-info" can report which
# address this host sees the platform arrive from. sshd sets it after
# authentication, so it is not something a client can claim, and it is the
# only way the platform learns whether the "from=" rule above is the right
# one - it cannot see its own outgoing address from behind NAT.
cat > "${SUDOERS_PATH}" <<EOF
Defaults:${MANAGEMENT_USER} env_keep += "SSH_CLIENT SSH_CONNECTION"
${MANAGEMENT_USER} ALL=(root) NOPASSWD: ${HELPER_PATH}
EOF
chmod 0440 "${SUDOERS_PATH}"
if command -v visudo >/dev/null 2>&1; then
  visudo -cf "${SUDOERS_PATH}" >/dev/null
fi

printf 'Restricted PRTG NATS iperf management access installed.\n'
# The one repair that cannot be done from the platform: if the rule above
# names the wrong network, the channel never opens and there is nothing to
# report the mistake through. So it is said here, on the console where
# somebody is still watching.
printf 'Accepting the platform from: %s\n' "${SOURCE_CIDR}"
printf 'If the platform cannot reach this host afterwards, correct that\n'
printf 'from= rule in %s\n' "${MANAGEMENT_HOME}/.ssh/authorized_keys"
