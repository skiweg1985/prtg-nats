#!/usr/bin/env bash

set -Eeuo pipefail

PUBLIC_KEY_FILE=""
HELPER_FILE=""
SOURCE_CIDR=""
MANAGEMENT_USER="prtg-nats-admin"
MANAGEMENT_HOME="/var/lib/prtg-nats-admin"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

validate_source_cidr() {
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
