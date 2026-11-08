---
title: Probes report back over a channel of their own
role: developer
updated: 2026-11-08
status: accepted
---

# 13. Probes report back over a channel of their own

## Context

What a sensor sees on the probe stays on the probe. `wlan-auth` is the
sharpest case: its helper reads every line `wpa_supplicant` and the DHCP
client write, decides on a result from them, and keeps them in memory
until the run is over. The probe writes no sensor output to the journal
(see the `snapshot` docstring in `sensors/wlan-auth/privileged`), so the
reason a sign-in failed - a rejected certificate, an EAP timeout, a DHCP
client killed before it sent a packet - exists for a few seconds and is
then gone. PRTG gets one error code. Everything else so far has meant SSH
to the probe and a test run by hand.

The paths that exist today do not fit:

- **The SSH helper** (forced command, [ADR 0006](0006-signed-helper-updates.md))
  is a path from the platform to the probe. It needs the platform to reach
  the probe at the moment it asks, and a probe that was unreachable while
  it measured has nothing to hand over afterwards unless it kept it.
  Polling every probe on a timer to find out whether anything happened
  turns a management path into a telemetry path.
- **NATS** carries the probe protocol for PRTG
  ([ADR 0001](0001-speak-the-probe-protocol-directly.md)). The credentials
  on the probe belong to `prtgmpprobe`, and the server's subjects and
  permissions are shaped by what PRTG expects. Putting a second producer on
  that connection mixes our traffic into PRTG's.
- **The WireGuard overlay** ([ADR 0009](0009-a-wireguard-overlay-to-the-probes.md))
  is optional. A channel that only works through it leaves every probe with
  a direct route without logs.

Measured on 2026-11-07 with a temporary listener on `8443/tcp` of the
platform host: three site probes reached it, two directly and through the
tunnel, one with the tunnel as its only route. No firewall in between had
to change.

## Decision

**The probe opens the connection.** A small service on the probe,
installed and kept up by the helper, sends what sensors record to the
platform over HTTPS. It connects outbound, so it works behind NAT, needs no
listening port on the probe, and reconnects on its own after an outage.
Whether the route runs through the overlay or directly is the operating
system's routing decision, not the service's.

**The probe proves who it is with a client certificate.** At enrolment the
helper generates a key on the probe and the platform signs a certificate
for it from a CA of its own under `runtime/private/`, separate from the
NATS CA so that trusting one never implies the other. The certificate
names the probe's identity
([ADR 0011](0011-a-probe-is-reached-by-identity-not-by-address.md)), not
its address. The service renews it before it expires over the same
channel. A probe that misses the window is repaired over SSH, see below.

**A dedicated port, the same process.** The platform accepts these
connections on a port of their own (default `8443/tcp`, configurable), with
client certificates required, apart from the port the web interface uses.
A firewall rule for probes then opens nothing of the interface. The
requests are handled by the API process like everything else
([ADR 0004](0004-one-process-no-broker.md)).

**What arrives is stored in the runtime volume.** Records are JSON Lines,
one file per probe, sensor and day under `runtime/sensor-logs/`, kept for
14 days. They are measurement output, not management state, and the
runtime is where the shell tooling and backups already look
([ADR 0002](0002-runtime-stays-the-source-of-truth.md)). SQLite at most
keeps the last-seen timestamp of each probe's channel, as a cache.

**The probe buffers, within a hard limit.** Records wait on disk until the
platform has acknowledged them, up to 20 MB per probe. Beyond that the
oldest go first, and the gap is recorded and delivered as a record of its
own, so the platform can say that something is missing instead of
silently showing less.

**Secrets never leave the probe.** Passphrases, passwords and key
passphrases are removed on the probe before a record is written to the
buffer. Identities, MAC addresses, BSSIDs and certificate subjects stay:
they are what lines a failed sign-in up with the RADIUS server and the
gateway.

**SSH stays the management path and the repair path.** Rollouts,
reservations and helper updates keep going through the helper. When a
probe's channel stays silent or its certificate is rejected, the platform
reinstalls the service and issues a new certificate over SSH.

**One direction for now.** The channel carries records from the probe to
the platform. Commands from the platform to the probe are not part of this
decision.

**Finished runs, not a tail.** A record is sent when a run ends. An open
page shows it through the existing event stream; following the raw output
of a run while it is still going is not part of this decision.

The first implementation is specified in
[issue 168](https://github.com/skiweg1985/prtg-nats/issues/168).

## Consequences

**Good.** The reason a sensor went red is visible on the platform within
seconds of the run, and stays there for two weeks, without SSH to a
probe.

**Good.** The record format is generic. A sensor that wants its output on
the platform writes records; it does not get a transport of its own.

**Good.** A probe without the overlay gets the same channel as one with
it.

**Cost.** A second CA to keep and back up, and a certificate lifecycle on
every probe. A runtime restored without the CA key means every probe gets
a new certificate over SSH.

**Cost.** A long-running service on every probe, and one more port to
open from the sites to the platform.

**Cost.** Up to 20 MB of writes per probe on what is often an SD card.
The buffer is written in appends and only grows while the platform is
unreachable.

**Cost.** Searching 14 days of files is a scan. At the volumes one
platform sees that is fine; if it stops being fine, an index is a cache
next to the files, not a replacement for them.

**Cost.** The probe channel's listener runs with
`strict_sni_host insecure_off`. A probe that reaches the platform by
address sends no server name, and with client auth on, Caddy would then
answer every request with 421 because the Host header cannot match an
absent name. The check exists to keep a certificate admitted for one site
from being used on another behind the same listener; this listener carries
only the probe channel, with one client-auth policy for both its names, so
there is nothing to cross over to. A future site on the same listener
would have to bring the check back.

**Cost.** A record the platform refuses with `422` is dropped on the probe
and replaced by a gap record with `code=refused`. Keeping it would hold up
every record behind it for good, since the platform will refuse it again
on every attempt; the gap record keeps the loss visible on the platform,
the probe's journal names the record and the platform's answer, and a
refusal at all points at a service and a backend whose limits disagree -
the case the backend's contract test for the duplicated limits is there to
catch.

**Note.** "Silent" needs a sign of life that does not depend on sensors
writing to the log. The service sends an empty batch as a heartbeat at
least every five minutes, and the platform counts silence from the later
of the last delivery and the certificate's issue time, with a default
threshold of three missed heartbeats.

## Alternatives considered

**Pull over the SSH helper.** A `sensor-log` helper command, called by the
platform. No new port and no new credential, and the obvious first step.
Rejected as the transport because the platform has to reach the probe and
has to poll to learn anything happened. It stays the repair path.

**Push over NATS.** The connection exists already. Rejected because the
credentials and the server's permissions belong to PRTG's probe protocol.

**A bearer token per probe.** Simpler than a CA. Rejected because the
token would sit in clear on the probe and rotating it needs the same
lifecycle a certificate has, without the expiry that forces it to happen.

**The overlay identity alone.** No extra secret at all. Rejected because
the overlay is optional.

**SQLite for the records.** Filtering and full-text search come for free.
Rejected for the reason in ADR 0002: the records are output, not
something the platform owns, and the shell tooling would lose sight of
them.
