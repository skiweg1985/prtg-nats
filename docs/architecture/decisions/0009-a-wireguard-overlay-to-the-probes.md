---
title: The probes reach the platform over a WireGuard overlay
role: developer
updated: 2026-08-31
status: accepted
---

# 9. The probes reach the platform over a WireGuard overlay

## Context

Two things break when the path between a probe and this installation does,
and they break in opposite directions.

A probe at a branch office reaches NATS over whatever the site gives it.
`NATS_FQDN` is commonly an address those sites reach through a site-to-site
tunnel, so when that tunnel is down the probe is offline - even while it
still has an uplink of its own, an LTE modem for instance, that could carry
the same traffic.

The management channel runs the other way. `web-api` opens SSH *to* the
probe, from the host address the probe's `from="…"` clause names.
`app/infrastructure/ssh_provisioning.py` has said the problem out loud since
the beginning: *"it often sits behind NAT where we cannot reach it"* - which
is why enrolment is a callback and not a connection. A probe like that is
manageable only for as long as somebody keeps a port forward alive for it.

Both are the same missing thing: a stable, private path between the two
that does not depend on the customer's WAN.

## Decision

A WireGuard hub-and-spoke overlay. The NATS host is the hub with a public
endpoint; every probe is a spoke that dials out and holds the tunnel open
with `PersistentKeepalive`.

**WireGuard directly, not Headscale or NetBird.** No NAT traversal is needed
here - the hub is the public side, and the spokes only ever dial out - so
what those products add is a control plane, and a control plane is a second
register of which probes exist. That is the thing
[ADR 0002](0002-runtime-stays-the-source-of-truth.md) exists to prevent.
They would also mean a third-party package repository on every probe host.

**The peer list is a rendering of the probe inventory.**
`runtime/probes/USER.env` gains the address, the public key and the mode;
`runtime/overlay/prtgnats0.conf` is generated from them, the same
relationship `auth-users/*.auth` has to `nats-server.conf`. There is nothing
to reconcile, because there is only one register.

**The hub key lives in `runtime/overlay/`, not `runtime/private/`.** The hub
runs in a container of its own, and that container has no business being
able to read the CA key - the same reason the interface certificate is in
`web-certs/` and not in `certs/`.

**The probe generates its own key.** The platform learns the public half
from the answer and never holds the private one.

**Three modes, per probe, changeable at any time.** `off` builds no tunnel;
`auto` keeps the tunnel up and routes NATS traffic through it only while the
direct path is down; `on` always routes it through. The management channel
uses the overlay address in `auto` and `on` and falls back to the ordinary
address - a standing tunnel the platform refuses to dial over would be a
strange thing to build.

**What makes `auto` honest is where the route lives.** `wg-quick` installs
none (`Table = off`). The overlay subnet goes into the main table once, and
the route to the NATS server sits in a table of its own that a single policy
rule switches on and off. The direct path can therefore be measured at any
moment - a bound socket on the interface the main table names - without
moving the route that is carrying traffic. Pulling a route to find out
whether it is still needed interrupts exactly what it is measuring.

**NATS itself is untouched.** `NATS_HOST_IP/32` is in the probe's
`AllowedIPs`, the probe keeps dialling `tls://NATS_FQDN:23561`, and only the
route decides which way the packets go. No second listener, no DNAT, and the
existing server certificate stays valid because neither the name nor the
destination address changes.

**Off by default and opt-in per installation**, behind a Compose profile.
Enabling it writes `.env` and starts the hub, so it happens on the host with
`prtg-nats overlay enable` and not from the interface.

## Consequences

**Good.** A probe behind NAT becomes reachable without anybody maintaining a
port forward for it, and a branch office whose site-to-site tunnel drops
keeps delivering measurements over whatever uplink it has left.

**Good.** The overlay answers a question nothing answered before: a probe in
`auto` that is on the tunnel means somebody's ordinary route is down. That
used to be invisible - the probe looked healthy, because it was.

**Good.** Nothing about it is load-bearing until it is used. An installation
that never enables it runs no extra container, and a probe in `off` is
exactly the probe it was.

**Cost.** One container with `NET_ADMIN` in the host network namespace,
which is a real privilege and the reason it is behind a profile. It
manipulates host routes because it shares the namespace - that is the point,
and it is why its entrypoint takes the interface down on the way out.

**Cost.** A second address per probe, and with it a second `known_hosts`
entry and a longer `from=` list. Both are written from what is already
pinned rather than scanned again; re-scanning would turn pinning into trust
on first use.

**Cost.** A watcher running as root on every probe in `auto`. It is
generated by the helper rather than shipped as a second file, so it travels
under the signature that already protects the helper
([ADR 0006](0006-signed-helper-updates.md)) - but it is code on the probe
that has to be right, and a bug in it moves traffic.

**Cost.** `wireguard-tools` comes from EPEL on RHEL 9 while Debian and
Ubuntu carry it in the main archive. A probe without it is refused with that
sentence rather than having a repository added behind the operator's back.

**Cost.** An endpoint that is `NATS_HOST_IP` would route the tunnel's own
endpoint into the tunnel and lose both paths at once. It is refused when the
overlay is enabled and again per probe, because there is no recovering from
it on the far side.
