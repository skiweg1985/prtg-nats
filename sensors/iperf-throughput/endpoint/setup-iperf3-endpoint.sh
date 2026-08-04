#!/usr/bin/env bash
#
# Sets up an iperf3 measurement endpoint for the iperf-throughput sensor on
# this machine: package, key pair, credentials, service.
#
# Runs on the endpoint, not on a probe and not on the prtg-nats server. It
# is deliberately self-contained: to set up a second endpoint, copy this one
# file there and run it.
#
# Running it twice does no harm. An existing key pair and existing
# credentials stay untouched as long as no --force stands next to it -
# otherwise an already deployed fleet would lose its access with one call.
set -euo pipefail

CONFIG_DIR=/etc/iperf3
DROP_IN_DIR=/etc/systemd/system/iperf3.service.d
DROP_IN="${DROP_IN_DIR}/auth.conf"
SERVICE=iperf3.service

user_name=prtg-probe
password_file=""
export_public_key=""
port=5201
force=no
force_credentials=no
remove=no

usage() {
  cat <<'USAGE'
Usage: setup-iperf3-endpoint.sh [OPTION]...

Sets up an authenticated iperf3 measurement endpoint for the
iperf-throughput sensor. Run it as root on the endpoint host, or as a user
who may sudo -- the script raises itself.

  --user NAME             user name the probes authenticate as
                          (default: prtg-probe)
  --password-file PATH    read the password from PATH instead of generating
                          one. Use this to give several endpoints the same
                          credentials, or to set a password of your choosing.
                          A password on the command line would end up in the
                          shell history, so there is no --password.
  --port PORT             port the endpoint listens on (default: 5201)
  --export-public-key PATH
                          write a copy of the public key to PATH, readable
                          by everyone. /etc/iperf3 is closed to all but root
                          and the service group, so whoever called this
                          script cannot pick the key up there.
  --force-credentials     replace the credentials but keep the key pair. This
                          is the password rotation: the probes need the new
                          password, but the key they already have stays valid.
  --force                 replace an existing key pair *and* the credentials.
                          Every probe already deployed loses access until it
                          gets both the new password and the new key.
  --remove                take the endpoint back off this host: stop the
                          service, drop the authentication and delete the key
                          pair and the credentials. The iperf3 package stays
                          installed -- something else may be using it.
  -h, --help              show this help

Without either --force option the script keeps whatever is already in place
and only reports it, so running it twice is safe.

"./prtg-nats iperf-server install ADMIN@HOST" on the NATS server runs this script
for you over SSH and keeps the password, so that it can be rolled out to the
probes afterwards. This file is the manual path.
USAGE
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '  %s\n' "$*"
}

# Captured before parsing: the self-invocation through sudo needs the
# arguments unchanged.
ORIGINAL_ARGS=("$@")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      [[ $# -ge 2 ]] || die "--user needs a value"
      user_name="$2"
      shift 2
      ;;
    --password-file)
      [[ $# -ge 2 ]] || die "--password-file needs a path"
      password_file="$2"
      shift 2
      ;;
    --export-public-key)
      [[ $# -ge 2 ]] || die "--export-public-key needs a path"
      export_public_key="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || die "--port needs a value"
      port="$2"
      shift 2
      ;;
    --force-credentials)
      force_credentials=yes
      shift
      ;;
    --force)
      force=yes
      force_credentials=yes
      shift
      ;;
    --remove)
      remove=yes
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "Unknown option: $1"
      ;;
  esac
done

[[ "${user_name}" =~ ^[A-Za-z0-9_-]+$ ]] ||
  die "The user name may only contain letters, digits, hyphen and underscore"
[[ "${port}" =~ ^[0-9]+$ && "${port}" -ge 1 && "${port}" -le 65535 ]] ||
  die "The port must be a number between 1 and 65535"
if [[ -n "${export_public_key}" ]]; then
  [[ "${export_public_key}" == /* ]] ||
    die "--export-public-key needs an absolute path"
  [[ -d "$(dirname -- "${export_public_key}")" ]] ||
    die "The directory for ${export_public_key} does not exist"
fi
# Whoever signs in over SSH as an ordinary administrator should not have
# to re-run the script with sudo by hand. It elevates itself, the way
# install-mpp.sh does on a probe.
if [[ "${EUID}" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 ||
    die "This script must run as root on the endpoint host and sudo is missing"
  exec sudo -- "$0" "${ORIGINAL_ARGS[@]}"
fi
command -v systemctl >/dev/null 2>&1 ||
  die "This script needs systemd; the iperf3 service is managed as a unit"

# The counterpart to everything below, and deliberately in this file rather
# than in a second script: what is taken back has to be exactly what was put
# there, and that stays true only as long as both live next to each other.
if [[ "${remove}" == "yes" ]]; then
  printf '== Removing the endpoint ==\n'
  systemctl disable --now "${SERVICE}" >/dev/null 2>&1 || true
  # The drop-in is the whole of the authentication. Without it the packaged
  # unit is back, which is an endpoint anybody may measure against - so the
  # service is stopped above before this file disappears, not after.
  rm -f -- "${DROP_IN}"
  rmdir -- "${DROP_IN_DIR}" 2>/dev/null || true
  systemctl daemon-reload
  # The private key decrypts what the probes send; the credentials file is
  # the hash they authenticate against. Neither has any use here afterwards.
  rm -rf -- "${CONFIG_DIR}"
  note "service stopped and disabled"
  note "authentication drop-in, key pair and credentials removed"
  note "the iperf3 package is still installed; remove it yourself if nothing"
  note "else on this host needs it"
  exit 0
fi

printf '== iperf3 ==\n'
if command -v iperf3 >/dev/null 2>&1; then
  note "$(iperf3 --version | head -1) is already installed"
else
  command -v apt-get >/dev/null 2>&1 ||
    die "iperf3 is missing and this script only knows apt-get; install it yourself"
  DEBIAN_FRONTEND=noninteractive apt-get update >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y iperf3 >/dev/null 2>&1 ||
    die "Could not install iperf3"
  note "$(iperf3 --version | head -1) installed"
fi

# The package creates the iperf3 user and group; the service runs under
# them. Without the group the service could not get at its private key.
service_group=iperf3
getent group "${service_group}" >/dev/null 2>&1 ||
  die "The group ${service_group} does not exist; the iperf3 package creates it"

printf '\n== Key pair ==\n'
install -d -o root -g "${service_group}" -m 0750 "${CONFIG_DIR}"
if [[ -f "${CONFIG_DIR}/private.pem" && "${force}" == "no" ]]; then
  note "kept the existing key pair (use --force to replace it)"
else
  # The private key decrypts the probes' credentials. It never leaves
  # this machine; the probes only get the public one.
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -outform PEM \
    -out "${CONFIG_DIR}/private.pem" 2>/dev/null ||
    die "Could not generate the private key"
  openssl rsa -in "${CONFIG_DIR}/private.pem" -pubout \
    -out "${CONFIG_DIR}/public.pem" 2>/dev/null ||
    die "Could not derive the public key"
  note "generated a new 2048 bit key pair"
fi
chown root:"${service_group}" "${CONFIG_DIR}/private.pem" "${CONFIG_DIR}/public.pem"
chmod 0640 "${CONFIG_DIR}/private.pem"
chmod 0644 "${CONFIG_DIR}/public.pem"
# The public key is the one thing here that has to leave the machine, and
# the directory around it is closed. Handing it out while we still have root
# saves the caller a second elevation - they came in as an administrator,
# not as a member of the service group.
if [[ -n "${export_public_key}" ]]; then
  install -m 0644 "${CONFIG_DIR}/public.pem" "${export_public_key}"
  note "exported the public key to ${export_public_key}"
fi

printf '\n== Credentials ==\n'
password=""
password_was_given=no
if [[ -f "${CONFIG_DIR}/credentials.csv" && "${force_credentials}" == "no" ]]; then
  note "kept the existing credentials (use --force-credentials to replace them)"
  note "the password is not stored here in clear text and cannot be shown"
else
  if [[ -n "${password_file}" ]]; then
    [[ -r "${password_file}" ]] || die "Cannot read ${password_file}"
    password="$(head -n 1 "${password_file}" | tr -d '\r\n')"
    [[ -n "${password}" ]] || die "${password_file} is empty"
    password_was_given=yes
    note "took the password from ${password_file}"
  else
    password="$(openssl rand -hex 24)"
    note "generated a new password"
  fi
  # Only the hash over "{user}password" is stored - iperf3 demands it
  # like that, and that way the password is nowhere on disk.
  hash="$(printf '%s' "{${user_name}}${password}" | sha256sum | awk '{print $1}')"
  printf '%s,%s\n' "${user_name}" "${hash}" > "${CONFIG_DIR}/credentials.csv"
fi
chown root:"${service_group}" "${CONFIG_DIR}/credentials.csv"
chmod 0640 "${CONFIG_DIR}/credentials.csv"

printf '\n== Service ==\n'
# The package's unit stays untouched; the authentication sits next to it
# as a drop-in. Deleting this file and running daemon-reload takes the
# change back completely.
install -d -m 0755 "${DROP_IN_DIR}"
cat > "${DROP_IN}" <<UNIT
# Created by setup-iperf3-endpoint.sh. The endpoint now only accepts
# authenticated clients. Deleting this file and running "systemctl
# daemon-reload" takes the change back completely.
[Service]
ExecStart=
ExecStart=/usr/bin/iperf3 --server --interval 0 --port ${port} \\
  --rsa-private-key-path ${CONFIG_DIR}/private.pem \\
  --authorized-users-path ${CONFIG_DIR}/credentials.csv
UNIT
systemctl daemon-reload
systemctl enable --now "${SERVICE}" >/dev/null 2>&1 || true
systemctl restart "${SERVICE}"
sleep 2
[[ "$(systemctl is-active "${SERVICE}")" == "active" ]] ||
  die "The ${SERVICE} did not come up; see: systemctl status ${SERVICE}"
note "listening on port ${port}, running as $(systemctl show "${SERVICE}" -p User --value)"

printf '\n== Counter-check ==\n'
# An endpoint that accepts unauthenticated clients looks exactly like a
# working one until the day somebody else finds it.
if iperf3 --client 127.0.0.1 --port "${port}" --time 1 >/dev/null 2>&1; then
  die "The endpoint accepted a client without credentials; authentication is not in effect"
fi
note "a client without credentials is rejected"
if [[ -n "${password}" ]]; then
  if IPERF3_PASSWORD="${password}" iperf3 --client 127.0.0.1 --port "${port}" \
    --time 1 --bitrate 10M --username "${user_name}" \
    --rsa-public-key-path "${CONFIG_DIR}/public.pem" >/dev/null 2>&1; then
    note "a client with credentials is accepted"
  else
    die "The endpoint rejected the credentials it was just given; check the clock (NTP) on this host"
  fi
fi

printf '\n== What the probes need ==\n'
note "user name:  ${user_name}"
# A password that was supplied is already known to the caller - repeating
# it here would only put it additionally into terminal captures and logs.
# Only one generated here has to go on screen, because afterwards it is
# gone.
if [[ -n "${password}" && "${password_was_given}" == "no" ]]; then
  note "password:   ${password}"
  note "Store it now; it is not recoverable from this host."
elif [[ "${password_was_given}" == "yes" ]]; then
  note "password:   the one you supplied; it is not repeated here"
else
  note "password:   unchanged, take it from where you kept it"
fi
note "public key: ${CONFIG_DIR}/public.pem"
printf '\nOn every probe, as root:\n\n'
cat <<'PROBE'
  install -d -m 0755 /etc/prtg-nats/sensors/iperf-throughput
  install -m 0644 public.pem /etc/prtg-nats/sensors/iperf-throughput/public.pem
  printf '%s\n' 'THE-PASSWORD' > /etc/prtg-nats/sensors/iperf-throughput/credentials
  chown root:paessler_mpprobe /etc/prtg-nats/sensors/iperf-throughput/credentials
  chmod 0640 /etc/prtg-nats/sensors/iperf-throughput/credentials
PROBE
printf '\nOn the NATS server this is one command instead:\n'
printf '  ./prtg-nats iperf-server deploy NAME --all\n'

# The own host name only works as an example when it is fully qualified.
# A short name like "ubuntu" would mislead: the probes have to reach the
# endpoint from outside, and for that it is rarely the right one.
endpoint_name="$(hostname -f 2>/dev/null || hostname)"
if [[ "${endpoint_name}" != *.* ]]; then
  endpoint_name="ENDPOINT-HOST"
fi
printf '\nSensor parameters in PRTG:\n\n'
printf '  --server %s --port %s --username %s --download-mbit 30 --upload-mbit 10\n' \
  "${endpoint_name}" "${port}" "${user_name}"
if [[ "${endpoint_name}" == "ENDPOINT-HOST" ]]; then
  printf '\nThis host has no fully qualified name; put in the name or address\nthe probes reach it by.\n'
fi
printf '\nRemember to open %s for TCP *and* UDP; the sensor measures with both.\n' \
  "${port}"
