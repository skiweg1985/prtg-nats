#!/usr/bin/env bash

set -Eeuo pipefail

PUBLIC_KEY_FILE=""
HELPER_FILE=""
SIGNING_KEY_FILE=""
SOURCE_CIDR=""
MANAGEMENT_USER="prtg-nats-admin"
MANAGEMENT_HOME="/var/lib/prtg-nats-admin"
CONFIG_DIR="/etc/prtg-nats"
SIGNING_KEY_PATH="${CONFIG_DIR}/helper-signing.pub"

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

# A comma separated list, because "from=" takes one and a server is not always
# reached from a single network - two uplinks, or a host that answers on an
# internal address and a public one. Every element is checked on its own: a
# list is only as good as its weakest entry, and one typo that widens the rule
# to everything is exactly what this validation exists to prevent.
validate_source_cidr() {
  local source_cidr="$1"
  local elements=()
  local element=""

  [[ -n "${source_cidr}" ]] || return 1
  # A leading, trailing or doubled comma would produce an empty pattern, and
  # sshd's reading of an empty pattern is not something to find out by
  # experiment on a host we are about to leave alone.
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
    --signing-key)
      SIGNING_KEY_FILE="${2:-}"
      shift 2
      ;;
    --source-cidr)
      SOURCE_CIDR="${2:-}"
      shift 2
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || die "Enrollment must run as root"
[[ -f "${PUBLIC_KEY_FILE}" ]] || die "Public key file is missing"
[[ -f "${HELPER_FILE}" ]] || die "Probe helper file is missing"
# Required, not optional. This is the only path the signing key travels on -
# accepting an enrolment without it would leave a probe whose helper can never
# be renewed except by walking to a console again.
[[ -f "${SIGNING_KEY_FILE}" ]] || die "Helper signing key file is missing"
grep -E '^-----BEGIN PUBLIC KEY-----$' "${SIGNING_KEY_FILE}" >/dev/null ||
  die "Expected a PEM public key for helper signatures"
validate_source_cidr "${SOURCE_CIDR}" ||
  die "Invalid SSH source CIDR"
command -v useradd >/dev/null 2>&1 || die "useradd is required"
command -v passwd >/dev/null 2>&1 || die "passwd is required"
command -v sudo >/dev/null 2>&1 || die "sudo is required"
grep -E '^ssh-ed25519 [A-Za-z0-9+/=]+ ' "${PUBLIC_KEY_FILE}" >/dev/null ||
  die "Expected an Ed25519 public key"

if ! id "${MANAGEMENT_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --create-home \
    --home-dir "${MANAGEMENT_HOME}" \
    --shell /bin/sh \
    "${MANAGEMENT_USER}"
fi
passwd -l "${MANAGEMENT_USER}" >/dev/null 2>&1 || true

install -o root -g root -m 0755 \
  "${HELPER_FILE}" /usr/local/sbin/prtg-nats-probe-helper
install -d -o root -g root -m 0755 "${CONFIG_DIR}"
# World-readable on purpose: it is a public key, and the helper reads it as
# root. What matters is that only root can write it.
install -o root -g root -m 0644 "${SIGNING_KEY_FILE}" "${SIGNING_KEY_PATH}"
install -d -o root -g root -m 0700 /var/lib/prtg-nats-probe-state
install -d -o "${MANAGEMENT_USER}" -g "${MANAGEMENT_USER}" -m 0700 \
  "${MANAGEMENT_HOME}/.ssh"

public_key="$(<"${PUBLIC_KEY_FILE}")"
{
  printf 'from="%s",restrict,' "${SOURCE_CIDR}"
  printf 'command="sudo -n /usr/local/sbin/prtg-nats-probe-helper" '
  printf '%s\n' "${public_key}"
} > "${MANAGEMENT_HOME}/.ssh/authorized_keys"
chown "${MANAGEMENT_USER}:${MANAGEMENT_USER}" \
  "${MANAGEMENT_HOME}/.ssh/authorized_keys"
chmod 0600 "${MANAGEMENT_HOME}/.ssh/authorized_keys"

cat > /etc/sudoers.d/prtg-nats-admin <<'EOF'
prtg-nats-admin ALL=(root) NOPASSWD: /usr/local/sbin/prtg-nats-probe-helper
EOF
chmod 0440 /etc/sudoers.d/prtg-nats-admin
if command -v visudo >/dev/null 2>&1; then
  visudo -cf /etc/sudoers.d/prtg-nats-admin >/dev/null
fi

printf 'Restricted PRTG NATS management access installed.\n'
