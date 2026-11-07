---
title: One client address per network
role: developer
updated: 2026-11-07
status: accepted
---

# 12. One client address per network

## Context

A probe with one radio that checks several networks signs in to all of
them one after the other, a few seconds apart, as the scheduler of
[ADR 0011](0011-the-helper-schedules-the-radio.md) runs them. Until now
every sign-in used the radio's built-in address.

On Aruba gateways under AOS-10 that is not neutral. Measured on three sites
with the same pair of networks - an internal one over 802.1X, a guest one
over WPA2-PSK - two of which were affected:

- A station that signs in to the 802.1X network and then, within the user
  idle timeout of 300 seconds, to the PSK network under the same address
  is placed in the VLAN of its previous session. The gateway logs it at
  association as `assg-vlan-req … new vlan: Opensystem set VLAN for
  wireless`, with the old VLAN; the access point forwards accordingly.
- With the same address the guest network was measured correctly in 5 of
  79 runs across two affected sites and three gateway versions (10.4.1.2,
  10.7.2.2, 10.7.2.6). With a second address it was measured correctly in
  all 40 runs.
- A pause of 60 seconds between the sessions, an explicit disconnect
  before leaving the first network, moving the access points into their
  intended management VLAN and a gateway upgrade each changed nothing.
- One site was not affected. Its gateway configuration is identical; it
  differs in access point model and firmware. Which of the two decides is
  open and with the vendor.

The sensor measured the guest network in the internal VLAN and reported a
valid lease - green, on a network that was not the one under test. Real
clients rarely show the fault, because phones use a private address per
network.

## Decision

The helper signs in to every network under an address of its own, unless
told otherwise.

- **Derived, not random:** `02` followed by the first five bytes of
  SHA-256 over the radio's built-in address and the SSID. The same radio
  gets the same address on the same network every time, so it keeps one
  DHCP lease and one entry in the gateway, and stays findable. A random
  address per run would leave a lease and a gateway entry behind on every
  measurement.
- **Locally administered and unicast,** as phones do with their private
  address per network.
- **Set on every run, the built-in address included,** because the
  interface keeps whatever the previous run left. The built-in address is
  read from the driver (`ETHTOOL_GPERMADDR`), since `/sys` only knows the
  address currently set, and put back after the run.
- **No fallback:** a radio that refuses a new address does not sign in
  under its own one instead. That would bring the fault back without
  anyone noticing. The run is blocked with `mac-not-settable` and the
  sensor reports a sensor error that names the address.
- **`--mac` selects it:** `auto` (default), `permanent` for the built-in
  address, or a fixed address for networks that only admit a known one.
- **The address is part of the job's identity** and appears in the sensor
  message, so the client can be found in the gateway's user table.

## Consequences

- Existing sensors sign in under a new address after the update. A DHCP
  reservation, a MAC allowlist or a rule tied to the radio's address no
  longer matches; such a sensor needs `--mac permanent` or the reserved
  address.
- The infrastructure sees one client per network instead of one per probe.
  That is closer to how a phone behaves and makes each network's entry
  readable on its own, at the cost of no longer finding the probe under one
  address everywhere. The message names the address of each run for that
  reason.
- An address used for one network only has consecutive sessions on that
  network, which the radio's single address never had. On the affected
  gateways that surfaced a guest policy that let DHCP answers to a known
  client address fall under its private-networks deny: the first run got
  a lease, the later ones none. It needed a rule permitting DHCP in both
  directions, which real guests renewing a lease need as well.
- Radios whose driver cannot change the address - some USB adapters - can
  only run with `--mac permanent`, and then share the fault on gateways
  that have it.
- The underlying gateway behaviour is not fixed by this. It still affects
  real devices that move between the two networks under one address, and
  it is reported to the vendor separately.
