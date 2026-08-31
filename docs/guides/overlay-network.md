---
title: Connect probes over the overlay
role: operator
updated: 2026-08-31
---

# Connect probes over the overlay

The overlay is a WireGuard tunnel between this host and the probes. It is
optional, off by default, and it exists for two problems that are the same
problem seen from either end.

**A probe loses NATS when its site does.** `NATS_FQDN` is often an address
that branch offices reach through a site-to-site tunnel. When that tunnel is
down the probe is offline, even though the LTE modem next to it still has
perfectly good internet.

**The platform cannot reach a probe behind NAT.** The management channel runs
from here *to* the probe. A probe without a port forward is manageable only
for as long as somebody keeps one alive.

With the overlay, the probe dials out and holds the tunnel open. It gets an
address this platform can always reach, and a path to NATS that does not
depend on its site's WAN.

## Before you start

- A UDP port reachable from the probes - `51820` unless you change it. This
  is the only port the overlay needs open.
- An address the probes can dial that is *not* the internal NATS address.
  On a site whose NATS address is internal, this is the public one.
- WireGuard in the host kernel, and `wireguard-tools` available to the
  probes. Debian and Ubuntu carry it in the main archive. On RHEL 9 it comes
  from EPEL, and a probe without it is refused with that sentence rather than
  having a repository added behind your back.

## 1. Turn it on

```bash
sudo ./prtg-nats overlay enable --endpoint nats.example.com
```

This writes the settings to `.env`, generates the hub key and starts the hub
container. It refuses an endpoint that is `NATS_HOST_IP`: the tunnel would
have to carry its own endpoint, and a probe switching over would lose both
paths at once.

Check what it did:

```bash
sudo ./prtg-nats overlay status
```

## 2. Put a probe on it

```bash
sudo ./prtg-nats overlay add mpp-berlin-01
```

The probe generates its own key and reports back the public half - the
private one never leaves it. Its ordinary address stays exactly as it was,
and the management key gains the hub's address instead of losing the old one.
A probe whose tunnel breaks is reachable the way it always was.

In the interface the same thing is **Infrastructure → Overlay → Add probes**,
for one probe or for a whole site at once.

Probes enrolled from now on join during enrolment: the invitation carries the
hub key and an address reserved for that probe, and the bootstrap brings the
tunnel up before it reports in. That is the only order that works for a probe
behind NAT - it reports in once and is never reachable the other way.

## 3. Choose what the tunnel carries

Each probe has a mode, changeable at any time and without re-enrolling
anything:

| Mode | Tunnel | NATS traffic | Use it when |
| --- | --- | --- | --- |
| `off` | not built | always direct | the probe should not be on the overlay |
| `auto` | up | only while the direct path is down | the usual case |
| `on` | up | always | you want one predictable, encrypted path |

```bash
sudo ./prtg-nats overlay mode mpp-berlin-01 on
```

`auto` is the default and the one worth understanding. The probe keeps
measuring the direct path to NATS every minute, on the interface that path
actually uses, without disturbing whatever traffic is flowing. Three failures
in a row move NATS onto the tunnel; three successes move it back. It will not
move onto a tunnel that has no recent handshake - trading one broken path for
another achieves nothing.

`on` never falls back. A tunnel that stops handshaking in that mode is
reported as a problem rather than worked around, because `on` that quietly
fell back would be `auto` under a different name.

## 4. Read what it is doing

**Infrastructure → Overlay** lists every peer with two separate answers: the
mode it was given, and the path it is on right now.

The row worth noticing is a probe in `auto` that reports `tunnel`. It is
working - and it means that probe's ordinary route is down and the overlay is
the only reason you are still getting measurements. Nothing else in the
interface would have told you.

```bash
sudo ./prtg-nats overlay show mpp-berlin-01
```

## Taking a probe back off

```bash
sudo ./prtg-nats overlay mode mpp-berlin-01 off   # keep the address
sudo ./prtg-nats overlay remove mpp-berlin-01     # give it back
```

Both go over the probe's ordinary address on purpose: the request takes down
the tunnel that would otherwise carry it. A probe that does not answer there
is refused rather than left unreachable. `--force` overrides that when you
know what you are doing.

`./prtg-nats overlay disable` stops the hub without touching a single peer.
Re-enabling does not mean visiting every probe again - but put any probe in
mode `on` back to `auto` first, or it will be looking for a tunnel that is no
longer there.

## What to keep

`runtime/overlay/hub-key` is the only part of this that cannot be
regenerated. The peer list is rendered from the probe inventory, so it always
comes back; the hub key does not. A runtime restored without it means putting
every probe on the overlay again.

## Related pages

- [Configuration reference](../reference/configuration.md#overlay-network)
- [CLI reference](../reference/cli.md#the-overlay)
- [Troubleshooting](troubleshooting.md)
- [ADR 0009](../architecture/decisions/0009-a-wireguard-overlay-to-the-probes.md)
