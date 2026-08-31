#!/usr/bin/env bash
#
# Brings the hub interface up and keeps it in step with the rendered peer
# list. Two things it deliberately does not do:
#
#   - restart on a change. Adding one probe would drop every tunnel that is
#     already up, so a new peer arrives through "wg syncconf" instead.
#   - fail when there is no configuration yet. The profile can be active
#     before "prtg-nats overlay enable" has generated the key, and a
#     crash-looping container is a worse answer than a waiting one.
#
# The interface lives in the host's network namespace, which is the whole
# point - the API opens its SSH connections from there. It also means the
# interface outlives this container unless it is taken down on the way out,
# hence the trap.

set -Eeuo pipefail

INTERFACE="${OVERLAY_INTERFACE:-prtgnats0}"
CONFIG="/etc/wireguard/${INTERFACE}.conf"
POLL_SECONDS="${OVERLAY_POLL_SECONDS:-5}"

say() { printf '%s overlay: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

shut_down() {
  trap - EXIT INT TERM
  if ip link show "${INTERFACE}" >/dev/null 2>&1; then
    say "taking ${INTERFACE} down"
    wg-quick down "${INTERFACE}" || true
  fi
  exit 0
}
trap shut_down EXIT INT TERM

config_fingerprint() {
  [[ -f "${CONFIG}" ]] || return 0
  sha256sum "${CONFIG}" | awk '{ print $1 }'
}

if ! ip link add name "prtg-nats-probe" type wireguard 2>/dev/null; then
  say "the host kernel does not provide WireGuard."
  say "Install the module (Debian and Ubuntu: wireguard, RHEL 9: kmod-wireguard)"
  say "or run a kernel from 5.6 onwards, then start the stack again."
  exit 1
fi
ip link delete "prtg-nats-probe" 2>/dev/null || true

while [[ ! -f "${CONFIG}" ]]; do
  say "waiting for ${CONFIG} - run \"prtg-nats overlay enable\""
  sleep "${POLL_SECONDS}"
done

say "bringing ${INTERFACE} up"
wg-quick up "${INTERFACE}"
fingerprint="$(config_fingerprint)"
say "up with $(wg show "${INTERFACE}" peers | grep -c . || true) peers"

while sleep "${POLL_SECONDS}"; do
  current="$(config_fingerprint)"
  [[ "${current}" != "${fingerprint}" ]] || continue
  # An empty fingerprint means the file is gone; that is an operator removing
  # the installation, not a peer change, and taking the interface down here
  # would race the removal. Waiting is the safe reading.
  [[ -n "${current}" ]] || continue
  say "peer list changed, syncing without touching existing handshakes"
  if wg syncconf "${INTERFACE}" <(wg-quick strip "${INTERFACE}"); then
    fingerprint="${current}"
  else
    say "syncconf refused the new configuration; keeping the running one"
  fi
done
