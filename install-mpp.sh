#!/usr/bin/env bash

set -Eeuo pipefail

ORIGINAL_ARGS=("$@")
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NATS_HOST=""
NATS_PORT="23561"
NATS_USER="prtg-nats"
CA_SOURCE=""
CA_URL=""
EXPECTED_CA_SHA256=""
ACCEPT_CA="false"
CONFIG_MODE="generate"
DRY_RUN="false"
PROBE_NAME=""
PROBE_HOST=""
PROBE_ID=""
ACCESS_KEY=""
NATS_PASSWORD=""
CLIENT_NAME="prtgmpprobe"
NATS_PASSWORD_FILE=""
RENDER_ONLY="false"
CHECK_ONLY="false"
CA_DESTINATION="/etc/paessler/mpprobe/certs/nats-docker-ca.pem"
MPP_CONFIG="/etc/paessler/mpprobe/config.yaml"
MPP_SERVICE="prtg.mpprobe.service"

usage() {
  cat <<'EOF'
Install and configure a native PRTG Multi-Platform Probe.

Usage:
  sudo ./install-mpp.sh [options]

Connection:
  --nats-host FQDN        NATS server FQDN (prompted when omitted)
  --nats-port PORT        NATS port (default: 23561)
  --nats-user USER        NATS user for the probe (default: prtg-nats)
  --nats-password-file P  Read the NATS password from a protected file
                          instead of prompting for it

Certificate authority:
  --ca-file PATH          Read the public CA from a local PEM file
  --ca-url URL            Override http://FQDN/nats-ca.pem
  --ca-sha256 HEX         Expected SHA-256 digest of the CA certificate
  --accept-ca             Skip the prompt; requires --ca-sha256

Probe configuration:
  --probe-name NAME       Probe name (default: multi-platform-probe@HOST)
  --probe-id UUID         Probe id (default: reused or newly generated)
  --access-key KEY        PRTG access key (default: reused or newly generated)
  --client-name NAME      NATS client name (default: prtgmpprobe)
  --config-template PATH  Alternative configuration template
  --wizard                Use the official "prtgmpprobe config wizard"
                          instead of generating config.yaml
  --no-config             Install package and CA only, write no configuration
  --render-config         Print the rendered configuration and exit without
                          touching the host (no root required)

Other:
  --check-only            Only test the NATS endpoint the way the probe will
                          use it (DNS, TCP, NATS greeting, TLS upgrade) and
                          exit without changing the host
  --dry-run               Show the planned operations without changing the host
  -h, --help              Show this help

By default the installer generates /etc/paessler/mpprobe/config.yaml itself.
An existing probe id and access key are reused, so a repeated run keeps the
probe identity that PRTG already knows. The NATS password is never passed as
a command-line argument: it is either read from a protected file or entered
interactively without echo.

Without --ca-file, the installer downloads the public CA from the NATS host.
If that endpoint is unavailable, an interactive PEM copy/paste fallback is
offered.
EOF
}

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

run() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

require_command() {
  local command_name="$1"
  command -v "${command_name}" >/dev/null 2>&1 ||
    die "Required command not found: ${command_name}"
}

normalize_sha256() {
  tr '[:upper:]' '[:lower:]' | tr -d '[:space:]:'
}

ca_sha256() {
  openssl x509 -in "$1" -outform DER |
    openssl dgst -sha256 |
    awk '{print $NF}'
}

is_affirmative() {
  local normalized=""

  normalized="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "${normalized}" in
    y|yes)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# The render library lives in the repository under libexec/, and during the
# central SSH push flat next to this script in the temporary staging
# directory.
load_configuration_library() {
  local library_candidate=""

  for library_candidate in \
    "${SCRIPT_DIR}/libexec/mpp-config.sh" \
    "${SCRIPT_DIR}/mpp-config.sh"; do
    if [[ -f "${library_candidate}" ]]; then
      # shellcheck source=libexec/mpp-config.sh
      source "${library_candidate}"
      return 0
    fi
  done
  die "Configuration library mpp-config.sh not found"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ca-file)
      [[ $# -ge 2 ]] || die "--ca-file requires a path"
      CA_SOURCE="$2"
      shift 2
      ;;
    --nats-host)
      [[ $# -ge 2 ]] || die "--nats-host requires an FQDN"
      NATS_HOST="$2"
      shift 2
      ;;
    --nats-port)
      [[ $# -ge 2 ]] || die "--nats-port requires a port"
      NATS_PORT="$2"
      shift 2
      ;;
    --nats-user)
      [[ $# -ge 2 ]] || die "--nats-user requires a user name"
      NATS_USER="$2"
      shift 2
      ;;
    --ca-url)
      [[ $# -ge 2 ]] || die "--ca-url requires an URL"
      CA_URL="$2"
      shift 2
      ;;
    --ca-sha256)
      [[ $# -ge 2 ]] || die "--ca-sha256 requires a digest"
      EXPECTED_CA_SHA256="$2"
      shift 2
      ;;
    --accept-ca)
      ACCEPT_CA="true"
      shift
      ;;
    --nats-password-file)
      [[ $# -ge 2 ]] || die "--nats-password-file requires a path"
      NATS_PASSWORD_FILE="$2"
      shift 2
      ;;
    --probe-name)
      [[ $# -ge 2 ]] || die "--probe-name requires a name"
      PROBE_NAME="$2"
      shift 2
      ;;
    --probe-host)
      [[ $# -ge 2 ]] || die "--probe-host requires a host name"
      PROBE_HOST="$2"
      shift 2
      ;;
    --probe-id)
      [[ $# -ge 2 ]] || die "--probe-id requires a UUID"
      PROBE_ID="$2"
      shift 2
      ;;
    --access-key)
      [[ $# -ge 2 ]] || die "--access-key requires a key"
      ACCESS_KEY="$2"
      shift 2
      ;;
    --client-name)
      [[ $# -ge 2 ]] || die "--client-name requires a name"
      CLIENT_NAME="$2"
      shift 2
      ;;
    --config-template)
      [[ $# -ge 2 ]] || die "--config-template requires a path"
      MPP_CONFIG_TEMPLATE="$2"
      shift 2
      ;;
    --wizard)
      CONFIG_MODE="wizard"
      shift
      ;;
    --no-config|--no-wizard)
      CONFIG_MODE="none"
      shift
      ;;
    --render-config)
      RENDER_ONLY="true"
      shift
      ;;
    --check-only)
      CHECK_ONLY="true"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

# The MPP establishes the NATS connection in two steps: the server first
# sends an INFO line in cleartext, and only then the session upgrades to TLS.
# A plain TCP test therefore misses exactly the disruptions that strike in
# the second step. The most common case is a firewall with application
# detection: it sees a session on an unusual port that does not start like
# TLS, classifies it as "unknown-tcp" and resets it at the upgrade - while
# the bare connection setup stays permitted. This test therefore walks the
# same path as the MPP and names the phase in which it fails.
nats_endpoint_tls_stage() {
  python3 - "$1" "$2" "$3" <<'PYTHON'
import socket
import ssl
import sys

host, port, ca_path = sys.argv[1], int(sys.argv[2]), sys.argv[3]

try:
    sock = socket.create_connection((host, port), timeout=10)
except OSError as error:
    print(error)
    sys.exit(21)

try:
    greeting = sock.recv(4096)
except OSError as error:
    print(error)
    sys.exit(22)

if not greeting.startswith(b"INFO "):
    print(repr(greeting[:60]))
    sys.exit(23)

context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.check_hostname = True
context.verify_mode = ssl.CERT_REQUIRED
try:
    context.load_verify_locations(ca_path)
except OSError as error:
    print(error)
    sys.exit(24)

try:
    tls = context.wrap_socket(sock, server_hostname=host)
except ssl.SSLCertVerificationError as error:
    print(error.verify_message or error)
    sys.exit(12)
except ssl.SSLError as error:
    print(error)
    sys.exit(13)
except ConnectionResetError as error:
    print(error)
    sys.exit(10)
except TimeoutError as error:
    print(error)
    sys.exit(11)
except OSError as error:
    print(error)
    sys.exit(13)

print("%s %s" % (tls.version(), tls.cipher()[0]))
tls.close()
PYTHON
}

check_nats_endpoint() {
  local host="${NATS_HOST}"
  local port="${NATS_PORT}"
  local ca_path="${1:-${CA_DESTINATION}}"
  local resolved="" info_line="" server_name="" detail="" stage_status=0

  log "Checking the NATS endpoint"

  if [[ "${DRY_RUN}" == "true" ]]; then
    run getent hosts "${host}"
    printf '+ NATS handshake test against %s:%s\n' "${host}" "${port}"
    return 0
  fi

  resolved="$(getent hosts "${host}" | awk '{ print $1; exit }')" || resolved=""
  [[ -n "${resolved}" ]] || die "DNS lookup failed for ${host}"
  printf 'DNS   %s resolves to %s\n' "${host}" "${resolved}"

  timeout 5 bash -c "</dev/tcp/${host}/${port}" 2>/dev/null ||
    die "Cannot open a TCP connection to ${host}:${port}.
The port is closed, filtered, or dropped on the way. Check routing and any
firewall between this probe and ${resolved}."
  printf 'TCP   connection to %s:%s established\n' "${resolved}" "${port}"

  # The INFO line proves that NATS really answers on the port, not a proxy
  # or some other service. It arrives in cleartext, so it is readable without
  # further tools.
  # Deliberately in a subshell: "exec 3<>..." in the main script would apply
  # an appended error redirection to the shell permanently and swallow every
  # later message.
  info_line="$(
    timeout 5 bash -c '
      exec 3<>"/dev/tcp/$1/$2" || exit 1
      IFS= read -r -u 3 greeting || exit 1
      printf "%s" "${greeting}"
    ' _ "${host}" "${port}" 2>/dev/null || true
  )"
  case "${info_line}" in
    'INFO '*)
      server_name="$(
        printf '%s' "${info_line}" |
          sed -n 's/.*"server_name":"\([^"]*\)".*/\1/p'
      )"
      printf 'NATS  greeted as %s\n' "${server_name:-unnamed server}"
      ;;
    '')
      die "No NATS greeting from ${host}:${port} within 5 seconds.
The TCP connection opens but stays silent. That is what an intercepting
firewall or a TCP proxy looks like; a NATS server answers immediately with an
INFO line."
      ;;
    *)
      die "Unexpected greeting from ${host}:${port}.
Something other than a NATS server is listening on this port."
      ;;
  esac

  if ! command -v python3 >/dev/null 2>&1; then
    printf 'TLS   skipped: python3 is not available for the handshake test\n'
    printf '\nEndpoint reachable; the TLS phase could not be verified here.\n'
    return 0
  fi

  [[ -f "${ca_path}" ]] ||
    die "CA certificate not found for the handshake test: ${ca_path}"

  detail="$(nats_endpoint_tls_stage "${host}" "${port}" "${ca_path}")" ||
    stage_status=$?
  case "${stage_status}" in
    0)
      printf 'TLS   handshake completed (%s)\n' "${detail}"
      printf '\nEndpoint check passed; the probe can reach NATS the way it will at runtime.\n'
      return 0
      ;;
    10)
      die "The connection was reset while upgrading to TLS (${detail}).
TCP works and NATS answers, but the switch to TLS is cut off. NATS starts in
plaintext and only then upgrades, so a firewall with application inspection
classifies this session as \"unknown-tcp\" on port ${port} and resets it,
while a plain connection test still succeeds.
Check the firewall between ${resolved} and this probe for a deny rule that
matches port ${port}, and allow the session or add an application override
for NATS."
      ;;
    11)
      die "The TLS upgrade to ${host}:${port} timed out (${detail}).
The handshake starts but no answer arrives. Look for a device that holds the
session open without passing it on, or for an MTU problem on the path."
      ;;
    12)
      die "The NATS server certificate was rejected: ${detail}.
The CA in ${ca_path} does not match the certificate the server presents, or
the certificate is not valid for ${host}. Compare the fingerprint with
\"./prtg-nats ca-info\" on the NATS server."
      ;;
    23)
      die "Unexpected greeting during the handshake test: ${detail}."
      ;;
    24)
      die "The CA certificate could not be loaded: ${detail}."
      ;;
    *)
      die "The TLS handshake with ${host}:${port} failed: ${detail}."
      ;;
  esac
}


if [[ -n "${CA_SOURCE}" && -n "${CA_URL}" ]]; then
  die "--ca-file and --ca-url cannot be used together"
fi
if [[ "${ACCEPT_CA}" == "true" && -z "${EXPECTED_CA_SHA256}" ]]; then
  die "--accept-ca requires --ca-sha256 from a trusted source"
fi
if [[ "${CONFIG_MODE}" == "wizard" && "${RENDER_ONLY}" == "true" ]]; then
  die "--render-config and --wizard cannot be used together"
fi

# Pure diagnosis: checks the endpoint and touches nothing. Usable on a probe
# whose installation was aborted, and for the counter-check after a firewall
# change.
if [[ "${CHECK_ONLY}" == "true" ]]; then
  [[ -n "${NATS_HOST}" ]] || die "--check-only requires --nats-host"
  check_nats_endpoint "${CA_SOURCE:-${CA_DESTINATION}}"
  exit 0
fi

# Adopts the existing probe identity, so a repeated run does not replace the
# probe PRTG already knows with a new one.
resolve_probe_identity() {
  local probe_host="${PROBE_HOST:-$(hostname -f 2>/dev/null || hostname)}"
  local existing_value=""

  if [[ -z "${PROBE_ID}" && -f "${MPP_CONFIG}" ]]; then
    existing_value="$(mpp_read_config_field "${MPP_CONFIG}" id || true)"
    if mpp_validate_probe_id "${existing_value}"; then
      PROBE_ID="${existing_value}"
    fi
  fi
  if [[ -z "${ACCESS_KEY}" && -f "${MPP_CONFIG}" ]]; then
    existing_value="$(mpp_read_config_field "${MPP_CONFIG}" access_key || true)"
    if mpp_validate_access_key "${existing_value}"; then
      ACCESS_KEY="${existing_value}"
    fi
  fi
  if [[ -z "${PROBE_NAME}" && -f "${MPP_CONFIG}" ]]; then
    existing_value="$(mpp_read_config_field "${MPP_CONFIG}" name || true)"
    if mpp_validate_probe_name "${existing_value}"; then
      PROBE_NAME="${existing_value}"
    fi
  fi

  [[ -n "${PROBE_ID}" ]] || PROBE_ID="$(mpp_generate_uuid)"
  [[ -n "${PROBE_NAME}" ]] ||
    PROBE_NAME="$(mpp_default_probe_name "${probe_host}")"
  # Name first, then the key: its readable part is built from the name, so
  # both can be attributed to the same probe in PRTG.
  [[ -n "${ACCESS_KEY}" ]] ||
    ACCESS_KEY="$(mpp_default_access_key "${PROBE_NAME}")"
}

read_nats_password() {
  local password_line=""

  [[ -z "${NATS_PASSWORD}" ]] || return 0
  if [[ -n "${NATS_PASSWORD_FILE}" ]]; then
    [[ -f "${NATS_PASSWORD_FILE}" ]] ||
      die "Password file not found: ${NATS_PASSWORD_FILE}"
    password_line="$(
      awk -F= '
        $1 == "NATS_PASSWORD" {
          sub(/^NATS_PASSWORD=/, "", $0)
          print $0
          found = 1
          exit
        }
        END { if (!found) exit 42 }
      ' "${NATS_PASSWORD_FILE}" 2>/dev/null || true
    )"
    if [[ -z "${password_line}" ]]; then
      password_line="$(
        awk 'NF { print; exit }' "${NATS_PASSWORD_FILE}"
      )"
    fi
    NATS_PASSWORD="${password_line}"
  else
    [[ -t 0 ]] ||
      die "Without --nats-password-file the password requires a terminal"
    read -r -s -p "NATS password for ${NATS_USER}: " NATS_PASSWORD
    printf '\n'
  fi
  [[ -n "${NATS_PASSWORD}" ]] || die "No NATS password was provided"
}

export_configuration_values() {
  MPP_PROBE_ID="${PROBE_ID}"
  MPP_ACCESS_KEY="${ACCESS_KEY}"
  MPP_PROBE_NAME="${PROBE_NAME}"
  MPP_NATS_HOST="${NATS_HOST}"
  MPP_NATS_PORT="${NATS_PORT}"
  MPP_NATS_USER="${NATS_USER}"
  MPP_NATS_PASSWORD="${NATS_PASSWORD}"
  MPP_SERVER_CA="${CA_DESTINATION}"
  MPP_CLIENT_NAME="${CLIENT_NAME}"
}

if [[ "${RENDER_ONLY}" == "true" ]]; then
  load_configuration_library
  [[ -n "${NATS_HOST}" ]] || die "--render-config requires --nats-host"
  resolve_probe_identity
  read_nats_password
  export_configuration_values
  mpp_render_config || die "Could not render the MPP configuration"
  exit 0
fi

if [[ "${DRY_RUN}" != "true" && "${EUID}" -ne 0 ]]; then
  require_command sudo
  exec sudo -- "$0" "${ORIGINAL_ARGS[@]}"
fi

if [[ "${CONFIG_MODE}" == "generate" ]]; then
  load_configuration_library
fi

# No built-in default: a fixed FQDN would be wrong for every other
# installation and would only show up at the TLS handshake.
if [[ -z "${NATS_HOST}" ]]; then
  if [[ -t 0 ]]; then
    while [[ -z "${NATS_HOST}" ]]; do
      read -r -p "NATS server FQDN: " NATS_HOST
      if [[ -n "${NATS_HOST}" ]] &&
        ! [[ "${NATS_HOST}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]]; then
        printf 'Not a valid FQDN: %s\n' "${NATS_HOST}" >&2
        NATS_HOST=""
      fi
    done
  elif [[ "${DRY_RUN}" == "true" ]]; then
    # Reine Anzeige; ohne Terminal gibt es nichts zu fragen.
    NATS_HOST="nats.example.com"
  else
    die "--nats-host is required without an interactive terminal"
  fi
fi

[[ "${NATS_HOST}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]] ||
  die "Invalid NATS FQDN: ${NATS_HOST}"
[[ "${NATS_PORT}" =~ ^[0-9]+$ ]] ||
  die "Invalid NATS port: ${NATS_PORT}"
((NATS_PORT >= 1 && NATS_PORT <= 65535)) ||
  die "NATS port must be between 1 and 65535"
[[ "${NATS_USER}" =~ ^[A-Za-z0-9._-]+$ ]] ||
  die "Invalid NATS user name"
if [[ -n "${CA_URL}" && ! "${CA_URL}" =~ ^https?://[^[:space:]]+$ ]]; then
  die "CA URL must use HTTP or HTTPS"
fi
if [[ "${DRY_RUN}" != "true" &&
      "${ACCEPT_CA}" != "true" &&
      ! -t 0 ]]; then
  die "Interactive CA confirmation requires a terminal"
fi

if [[ ! -r /etc/os-release ]]; then
  die "This installer requires a supported Linux distribution"
fi

# shellcheck disable=SC1091
source /etc/os-release
distribution_id="${ID:-unknown}"
distribution_like="${ID_LIKE:-}"
distribution_version="${VERSION_ID:-unknown}"

package_family=""
case " ${distribution_id} ${distribution_like} " in
  *" debian "*|*" ubuntu "*)
    package_family="debian"
    ;;
  *" rhel "*|*" fedora "*)
    package_family="rhel"
    ;;
  *)
    die "Unsupported distribution: ${distribution_id} ${distribution_version}"
    ;;
esac

log "Detected ${PRETTY_NAME:-${distribution_id}} (${package_family})"

check_package_manager_state() {
  local dpkg_audit=""
  local pending_packages="0"

  [[ "${package_family}" == "debian" ]] || return 0
  require_command dpkg
  dpkg_audit="$(dpkg --audit)"
  if [[ -n "${dpkg_audit}" ]]; then
    pending_packages="$(
      printf '%s\n' "${dpkg_audit}" |
        awk '/^ [^[:space:]]+[[:space:]]/ { count++ } END { print count + 0 }'
    )"
    printf '\nERROR: Debian has unfinished package operations' >&2
    if ((pending_packages > 0)); then
      printf ' affecting approximately %s packages' \
        "${pending_packages}" >&2
    fi
    printf '.\n' >&2
    printf 'The MPP installer stopped before making package changes.\n\n' >&2
    printf 'Repair the probe, then rerun install-mpp:\n' >&2
    printf '  sudo dpkg --configure -a\n' >&2
    printf '  sudo apt-get -f install\n' >&2
    printf '  sudo dpkg --audit\n' >&2
    printf '\nRun "sudo dpkg --audit" separately only if full details are needed.\n' >&2
    return 1
  fi
}

ensure_prerequisites() {
  local ca_bundle_present="false"

  if [[ "${package_family}" == "debian" &&
        -s /etc/ssl/certs/ca-certificates.crt ]]; then
    ca_bundle_present="true"
  elif [[ "${package_family}" == "rhel" &&
          -s /etc/pki/tls/certs/ca-bundle.crt ]]; then
    ca_bundle_present="true"
  fi

  if command -v curl >/dev/null 2>&1 &&
    command -v openssl >/dev/null 2>&1 &&
    [[ "${ca_bundle_present}" == "true" ]]; then
    return
  fi

  log "Installing certificate and download prerequisites"
  if [[ "${package_family}" == "debian" ]]; then
    run apt-get update
    run env DEBIAN_FRONTEND=noninteractive \
      apt-get install -y ca-certificates curl openssl
  else
    run dnf install -y ca-certificates curl openssl
  fi
}

# Sensors with dependencies create their own virtual environment during
# deployment. Debian splits ensurepip into a separate package; without it,
# "python3 -m venv" fails and the probe only reports that it could not create
# the environment. On RHEL, python3 brings both along.
ensure_python_venv() {
  if python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
    return
  fi

  log "Installing Python virtual environment support for sensors"
  if [[ "${package_family}" == "debian" ]]; then
    run apt-get update
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv
  else
    run dnf install -y python3
  fi
}

check_package_manager_state
ensure_prerequisites
ensure_python_venv

temporary_ca=""
RENDERED_CONFIG=""
PREVIOUS_CONFIG=""
cleanup_temporary_ca() {
  if [[ -n "${temporary_ca}" &&
        "${temporary_ca}" =~ ^/tmp/prtg-nats-ca\.[A-Za-z0-9]+$ ]]; then
    rm -f -- "${temporary_ca}"
  fi
}
cleanup_generated_configuration() {
  local leftover=""

  for leftover in "${RENDERED_CONFIG}" "${PREVIOUS_CONFIG}"; do
    if [[ "${leftover}" =~ ^/tmp/prtg-mpp-config\.[A-Za-z0-9]+$ ]]; then
      rm -f -- "${leftover}"
    fi
  done
}
trap 'cleanup_temporary_ca; cleanup_generated_configuration' EXIT

new_temporary_ca() {
  temporary_ca="$(mktemp /tmp/prtg-nats-ca.XXXXXX)"
  chmod 0600 "${temporary_ca}"
}

paste_ca_interactively() {
  local certificate_started="false"
  local certificate_complete="false"
  local certificate_line=""

  new_temporary_ca
  : > "${temporary_ca}"

  printf '\nThe CA download endpoint is unavailable.\n'
  printf 'On the NATS server, run: sudo ./prtg-nats ca-show\n'
  printf 'Paste the complete PEM certificate below. Input ends automatically\n'
  printf 'after the -----END CERTIFICATE----- line.\n\n'

  while IFS= read -r certificate_line; do
    if [[ "${certificate_line}" == "-----BEGIN CERTIFICATE-----" ]]; then
      certificate_started="true"
    fi
    if [[ "${certificate_started}" == "true" ]]; then
      printf '%s\n' "${certificate_line}" >> "${temporary_ca}"
    fi
    if [[ "${certificate_line}" == "-----END CERTIFICATE-----" &&
          "${certificate_started}" == "true" ]]; then
      certificate_complete="true"
      break
    fi
  done

  [[ "${certificate_complete}" == "true" ]] ||
    die "No complete PEM certificate was pasted"
  CA_SOURCE="${temporary_ca}"
  ca_origin="interactive copy/paste"
}

ca_origin=""
actual_ca_sha256=""
if [[ "${DRY_RUN}" == "true" ]]; then
  if [[ -n "${CA_SOURCE}" ]]; then
    ca_origin="${CA_SOURCE}"
  else
    CA_URL="${CA_URL:-http://${NATS_HOST}/nats-ca.pem}"
    ca_origin="${CA_URL}, with interactive PEM fallback"
    CA_SOURCE="/tmp/prtg-nats-ca.DOWNLOADED-OR-PASTED"
  fi
  log "Obtaining and validating the public NATS CA"
  printf '+ CA source: %s\n' "${ca_origin}"
  printf '+ validate one current self-signed CA certificate\n'
  if [[ "${ACCEPT_CA}" != "true" ]]; then
    printf '+ display subject, validity, SHA-256, and request trust confirmation\n'
  fi
else
  if [[ -n "${CA_SOURCE}" ]]; then
    ca_origin="local file ${CA_SOURCE}"
  else
    CA_URL="${CA_URL:-http://${NATS_HOST}/nats-ca.pem}"
    new_temporary_ca
    log "Downloading the public NATS CA from ${CA_URL}"
    if curl --fail --silent --show-error --location \
      --connect-timeout 5 \
      --max-time 15 \
      --max-filesize 1048576 \
      --output "${temporary_ca}" \
      "${CA_URL}"; then
      CA_SOURCE="${temporary_ca}"
      ca_origin="${CA_URL}"
    elif [[ -t 0 ]]; then
      cleanup_temporary_ca
      temporary_ca=""
      paste_ca_interactively
    else
      die "CA download failed; use --ca-file or run interactively to paste it"
    fi
  fi

  require_command openssl
  [[ -f "${CA_SOURCE}" ]] || die "CA certificate not found: ${CA_SOURCE}"
  certificate_count="$(grep -c 'BEGIN CERTIFICATE' "${CA_SOURCE}" || true)"
  [[ "${certificate_count}" == "1" ]] ||
    die "CA input must contain exactly one PEM certificate"
  openssl x509 -in "${CA_SOURCE}" -noout >/dev/null 2>&1 ||
    die "CA file is not a valid PEM certificate"
  openssl x509 -in "${CA_SOURCE}" -noout -text |
    grep -F 'CA:TRUE' >/dev/null ||
    die "Certificate is not a certificate authority"
  openssl verify -CAfile "${CA_SOURCE}" "${CA_SOURCE}" >/dev/null ||
    die "CA certificate is not self-consistent"
  openssl x509 -in "${CA_SOURCE}" -noout -checkend 0 >/dev/null ||
    die "CA certificate has expired"

  actual_ca_sha256="$(ca_sha256 "${CA_SOURCE}" | normalize_sha256)"
  if [[ -n "${EXPECTED_CA_SHA256}" ]]; then
    expected_ca_sha256="$(
      printf '%s' "${EXPECTED_CA_SHA256}" | normalize_sha256
    )"
    [[ "${actual_ca_sha256}" == "${expected_ca_sha256}" ]] ||
      die "CA SHA-256 mismatch"
  fi

  log "Public NATS CA awaiting approval"
  printf 'Source=%s\n' "${ca_origin}"
  openssl x509 -in "${CA_SOURCE}" \
    -noout -subject -issuer -dates
  printf 'SHA-256 Fingerprint=%s\n' "${actual_ca_sha256}"
  printf 'NATS endpoint=%s:%s\n' "${NATS_HOST}" "${NATS_PORT}"

  if [[ "${ACCEPT_CA}" != "true" ]]; then
    printf '\nCompare the fingerprint with "sudo ./prtg-nats ca-info" on the\n'
    printf 'NATS server or another trusted administrator channel.\n'
    read -r -p 'Install and trust this CA? [y/N]: ' ca_confirmation
    is_affirmative "${ca_confirmation}" ||
      die "CA confirmation declined"
  fi
fi

package_installed="false"
# "dpkg-query -W" alone is not enough: after "apt-get remove" without
# "purge", the package stays registered with status "config-files" and would
# still count as installed although the program files are gone. The
# installation would then be skipped and the service could not start.
if [[ "${package_family}" == "debian" ]] &&
  [[ "$(
    dpkg-query -W -f '${db:Status-Status}' prtgmpprobe 2>/dev/null || true
  )" == "installed" ]]; then
  package_installed="true"
elif [[ "${package_family}" == "rhel" ]] &&
  rpm -q prtgmpprobe >/dev/null 2>&1; then
  package_installed="true"
fi

install_debian_package() {
  local repository_key="/usr/share/keyrings/paessler-archive-keyring.asc"
  local repository_file="/etc/apt/sources.list.d/paessler.sources"
  local repository_url=""
  local download_dir=""

  [[ -n "${VERSION_CODENAME:-}" ]] ||
    die "VERSION_CODENAME is missing in /etc/os-release"
  repository_url="https://packages.paessler.com/docs/apt-sources/${VERSION_CODENAME}.sources"

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '+ download and validate %s\n' \
      'https://packages.paessler.com/keys/paessler.asc'
    printf '+ install Paessler signing key as %s\n' "${repository_key}"
    printf '+ download and validate %s\n' "${repository_url}"
    printf '+ install repository definition as %s\n' "${repository_file}"
    run apt-get update
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y prtgmpprobe
    return
  fi

  download_dir="$(mktemp -d /tmp/prtg-mpp-repository.XXXXXX)"
  trap '
    if [[ "${download_dir}" =~ ^/tmp/prtg-mpp-repository\.[A-Za-z0-9]+$ ]]; then
      rm -rf -- "${download_dir}"
    fi
  ' RETURN

  curl --fail --silent --show-error --location \
    https://packages.paessler.com/keys/paessler.asc \
    --output "${download_dir}/paessler.asc"
  grep -F 'BEGIN PGP PUBLIC KEY BLOCK' "${download_dir}/paessler.asc" >/dev/null ||
    die "Downloaded Paessler signing key is not an ASCII public key"
  install -o root -g root -m 0644 \
    "${download_dir}/paessler.asc" "${repository_key}"

  curl --fail --silent --show-error --location \
    "${repository_url}" \
    --output "${download_dir}/paessler.sources"
  grep -F 'packages.paessler.com' "${download_dir}/paessler.sources" >/dev/null ||
    die "Downloaded Paessler repository definition is invalid"
  install -o root -g root -m 0644 \
    "${download_dir}/paessler.sources" "${repository_file}"

  apt-get update
  env DEBIAN_FRONTEND=noninteractive apt-get install -y prtgmpprobe
}

install_rhel_package() {
  local major_version="${distribution_version%%.*}"
  local repository_file="/etc/yum.repos.d/paessler-prtg.repo"
  local repository_url=""
  local download_dir=""

  [[ "${major_version}" == "9" ]] ||
    die "The automated RHEL path currently supports major version 9 only"
  repository_url="https://packages.paessler.com/docs/rpm-sources/rhel-9.repo"

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '+ download and validate %s\n' "${repository_url}"
    printf '+ install repository definition as %s\n' "${repository_file}"
    run dnf install -y prtgmpprobe
    return
  fi

  download_dir="$(mktemp -d /tmp/prtg-mpp-repository.XXXXXX)"
  trap '
    if [[ "${download_dir}" =~ ^/tmp/prtg-mpp-repository\.[A-Za-z0-9]+$ ]]; then
      rm -rf -- "${download_dir}"
    fi
  ' RETURN

  curl --fail --silent --show-error --location \
    "${repository_url}" \
    --output "${download_dir}/paessler.repo"
  grep -F 'packages.paessler.com' "${download_dir}/paessler.repo" >/dev/null ||
    die "Downloaded Paessler repository definition is invalid"
  install -o root -g root -m 0644 \
    "${download_dir}/paessler.repo" "${repository_file}"
  dnf install -y prtgmpprobe
}

if [[ "${package_installed}" == "true" ]]; then
  log "prtgmpprobe is already installed; package installation skipped"
else
  log "Installing the official Paessler package"
  if [[ "${package_family}" == "debian" ]]; then
    install_debian_package
  else
    install_rhel_package
  fi
fi

log "Installing the public NATS CA"
run install -d -o root -g root -m 0755 "$(dirname -- "${CA_DESTINATION}")"
run install -o root -g root -m 0644 "${CA_SOURCE}" "${CA_DESTINATION}"
if [[ "${DRY_RUN}" != "true" ]]; then
  printf 'Installed CA SHA-256: %s\n' "${actual_ca_sha256}"
fi

check_nats_endpoint

if [[ "${CONFIG_MODE}" == "none" ]]; then
  printf '\nPackage and CA preparation complete; no configuration was written.\n'
  exit 0
fi

if [[ "${CONFIG_MODE}" == "wizard" ]]; then
  cat <<EOF

Use these values in the official configuration wizard:

  Probe name:                    unique host/location name
  Probe access key:              keep the generated unique value
  NATS connection security:      Yes (TLS)
  Use system CA certificates:    No
  Server CA PEM:                 ${CA_DESTINATION}
  NATS server FQDN:              ${NATS_HOST}
  NATS server port:              ${NATS_PORT}
  Authentication:                Username/Password
  NATS username:                 ${NATS_USER}
  NATS password:                 protected administrator handoff

The password and access key are deliberately not passed to this script.
EOF
fi

if [[ -f "${MPP_CONFIG}" ]]; then
  backup_path="${MPP_CONFIG}.before-install.$(date -u +%Y%m%dT%H%M%SZ)"
  log "Backing up the existing MPP configuration"
  run cp -a "${MPP_CONFIG}" "${backup_path}"
  run chown root:root "${backup_path}"
  run chmod 0600 "${backup_path}"
  printf 'Backup: %s\n' "${backup_path}"
fi

# Writes the rendered configuration and restores the previous state on a
# start failure.
# prtg.mpprobe.service runs under its own service user
# (User=/Group=paessler_mpprobe on MPP 3.10.0), not as root. A configuration
# with 0600 root:root is unreadable for it and the service aborts with
# "Failed to read config file: Permission denied". A newly created
# configuration therefore gets the group of the configuration directory and
# mode 0640: root writes, the service user reads, nobody else.
apply_service_group() {
  local target_path="$1"
  local directory_group=""

  directory_group="$(stat -c '%G' "$(dirname -- "${MPP_CONFIG}")" 2>/dev/null || true)"
  if [[ -n "${directory_group}" && "${directory_group}" != "root" ]]; then
    chown "root:${directory_group}" "${target_path}"
    chmod 0640 "${target_path}"
  fi
}

write_generated_configuration() {
  local rendered=""
  local previous=""
  local restore_previous="false"

  require_command systemctl
  RENDERED_CONFIG="$(mktemp /tmp/prtg-mpp-config.XXXXXX)"
  PREVIOUS_CONFIG="$(mktemp /tmp/prtg-mpp-config.XXXXXX)"
  rendered="${RENDERED_CONFIG}"
  previous="${PREVIOUS_CONFIG}"
  chmod 0600 "${rendered}" "${previous}"

  mpp_render_config > "${rendered}" ||
    die "Could not render the MPP configuration"
  mpp_validate_rendered_config "${rendered}" ||
    die "The rendered MPP configuration failed its structure check"

  if [[ -f "${MPP_CONFIG}" ]]; then
    cp -p "${MPP_CONFIG}" "${previous}"
    restore_previous="true"
  fi

  install -d -o root -g root -m 0755 "$(dirname -- "${MPP_CONFIG}")"
  install -o root -g root -m 0600 "${rendered}" "${MPP_CONFIG}.new"
  if [[ "${restore_previous}" == "true" ]]; then
    chown --reference="${MPP_CONFIG}" "${MPP_CONFIG}.new"
    chmod --reference="${MPP_CONFIG}" "${MPP_CONFIG}.new"
  else
    apply_service_group "${MPP_CONFIG}.new"
  fi
  mv "${MPP_CONFIG}.new" "${MPP_CONFIG}"

  systemctl enable "${MPP_SERVICE}" >/dev/null 2>&1 || true
  if ! systemctl restart "${MPP_SERVICE}" ||
    ! systemctl is-active --quiet "${MPP_SERVICE}"; then
    if [[ "${restore_previous}" == "true" ]]; then
      install -o root -g root -m 0600 "${previous}" "${MPP_CONFIG}.new"
      chown --reference="${MPP_CONFIG}" "${MPP_CONFIG}.new"
      chmod --reference="${MPP_CONFIG}" "${MPP_CONFIG}.new"
      mv "${MPP_CONFIG}.new" "${MPP_CONFIG}"
      systemctl restart "${MPP_SERVICE}" || true
      die "${MPP_SERVICE} did not start; the previous configuration was restored"
    fi
    rm -f -- "${MPP_CONFIG}"
    systemctl stop "${MPP_SERVICE}" || true
    die "${MPP_SERVICE} did not start with the generated configuration"
  fi
  cleanup_generated_configuration
}

probe_access_key=""
if [[ "${CONFIG_MODE}" == "wizard" ]]; then
  log "Starting the official MPP configuration wizard"
  if [[ "${DRY_RUN}" == "true" ]]; then
    run prtgmpprobe config wizard
    run systemctl enable "${MPP_SERVICE}"
    run systemctl restart "${MPP_SERVICE}"
  else
    require_command prtgmpprobe
    require_command systemctl
    prtgmpprobe config wizard
    [[ -f "${MPP_CONFIG}" ]] ||
      die "Wizard did not create ${MPP_CONFIG}"
    systemctl enable "${MPP_SERVICE}"
    systemctl restart "${MPP_SERVICE}"
    systemctl is-active --quiet "${MPP_SERVICE}" ||
      die "${MPP_SERVICE} is not active after configuration"
  fi
else
  log "Generating the MPP configuration"
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '+ resolve probe identity (reuse existing id and access key)\n'
    printf '+ read the NATS password without echo or from --nats-password-file\n'
    printf '+ render %s from the configuration template\n' "${MPP_CONFIG}"
    run systemctl enable "${MPP_SERVICE}"
    run systemctl restart "${MPP_SERVICE}"
  else
    resolve_probe_identity
    read_nats_password
    export_configuration_values
    printf 'Probe name: %s\n' "${PROBE_NAME}"
    printf 'Probe id:   %s\n' "${PROBE_ID}"
    write_generated_configuration
  fi
fi

if [[ "${DRY_RUN}" != "true" ]]; then
  grep -F "url: tls://${NATS_HOST}:${NATS_PORT}" "${MPP_CONFIG}" >/dev/null ||
    die "MPP configuration does not contain the expected TLS NATS URL"
  grep -F "user: ${NATS_USER}" "${MPP_CONFIG}" >/dev/null ||
    die "MPP configuration does not contain the expected NATS user"
  grep -F "server_ca: ${CA_DESTINATION}" "${MPP_CONFIG}" >/dev/null ||
    die "MPP configuration does not contain the expected CA path"
  load_configuration_library
  probe_access_key="$(mpp_read_config_field "${MPP_CONFIG}" access_key)" ||
    die "Could not read a unique probe access key from ${MPP_CONFIG}"
fi

printf '\nMPP installation completed.\n'

if [[ -n "${probe_access_key}" ]]; then
  cat <<EOF

Probe Access Key for PRTG:

  ${probe_access_key}

Copy this value as a new line into the PRTG access-key list.
EOF
else
  printf '\nDry run: no Probe Access Key was created or displayed.\n'
fi

cat <<EOF

Next:
  1. Add the Probe Access Key shown above as a new line in PRTG.
  2. Approve the probe (do not use "Approve and auto-discover").
  3. Check:
       systemctl status ${MPP_SERVICE} --no-pager
       journalctl -u ${MPP_SERVICE} -n 200 --no-pager
EOF
