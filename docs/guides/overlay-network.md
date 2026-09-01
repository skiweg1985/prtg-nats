---
title: Connect probes over the overlay
role: operator
updated: 2026-09-01
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
- An administrator account. Turning the overlay on creates a container with
  network-admin rights on this host, so it needs the same kind of account a
  stack update does.
- WireGuard in the host kernel, and `wireguard-tools` available to the
  probes. Debian and Ubuntu carry it in the main archive. On RHEL 9 it comes
  from EPEL, and a probe without it is refused with that sentence rather than
  having a repository added behind your back.

## 1. Turn it on

**Infrastructure → Overlay → Turn the overlay on**, and give it the address
probes will dial. That generates the hub key, renders its configuration and
starts the hub. Nothing runs for the overlay before this - the container with
network privileges is created when you ask for it and not a moment earlier,
which is also why the button needs an administrator.

It refuses an endpoint that is `NATS_HOST_IP`: the tunnel would have to carry
its own endpoint, and a probe switching over would lose both paths at once.

The same thing from the command line, for automation and recovery:

```bash
sudo ./prtg-nats overlay enable nats.example.com
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

A site with no site-to-site tunnel at all needs one step more, because the
probe cannot reach this platform to fetch the script in the first place. See
[A site with no way in](#a-site-with-no-way-in) below.

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

## A site with no way in

An outpost with no site-to-site tunnel cannot enrol the ordinary way. The
interface answers on `NATS_HOST_IP`, so the probe would need the very path it
does not have before it could fetch anything - the overlay would fix that, and
the overlay is what it cannot reach.

For that case, tick **This probe cannot reach the platform directly** when
creating the invitation. You then get three commands instead of one, to run in
order on a console of the probe - Raspberry Pi Connect, SSH from inside that
site, a keyboard in front of it:

1. **Provide WireGuard** - installs `wireguard-tools`
2. **Build the tunnel to the platform** - brings `prtgnats0` up. This is the
   one that carries the probe's private key; treat it like a password
3. **Start the enrolment** - the ordinary one-liner, which now has a path

The order is the whole point: the one-liner downloads a script, and that
download is the first request. A script cannot be fetched over a tunnel it has
not built yet, so the tunnel comes first and by hand.

After the second command, `sudo wg show` should report a time under
`latest handshake`. If it does not, the endpoint is not reachable over UDP and
the third command would fail with a timeout that says nothing.

What changes, and it is worth knowing before you tick it: the platform
generates the probe's WireGuard key instead of the probe generating its own,
and **the script carries the private half**. It has to - a probe that cannot
reach the platform cannot report a key it made. That turns the script into a
credential. Hand it over the way you would hand over a password, not in a
ticket or a chat.

Such a probe is always mode `on`, not the site default. `auto` measures the
direct path once a minute to decide whether to fall back, and here there is no
direct path to measure - it would also leave NATS off the tunnel for the first
two minutes, which is exactly when the probe is reporting in.

Such a probe is addressed by IP throughout - `BASE_URL`, the package
installer and its own NATS configuration. Its site has no name server that
knows this platform, so `NATS_FQDN` would be a dead end even with the tunnel
up. The server certificate names `NATS_HOST_IP` as well, so nothing is
verified less strictly; it is only less readable in a config file.

The probe needs to reach two things for this to work:

- the overlay endpoint over UDP - the public address, not the NATS one
- its distribution's own mirrors, for `wireguard-tools` and for the
  `prtgmpprobe` package. Neither comes from this platform.

And one thing has to be true on this side: **the NATS server certificate has
to carry `NATS_HOST_IP` as a SAN.** The probe is configured with the address,
so a certificate that only names the FQDN is refused - `tls: bad certificate`
in the NATS log, while tunnel and enrolment both look perfectly healthy. An
installation whose certificate predates this is fixed with
`./prtg-nats renew-certificate`.

The reservation lives as long as the invitation. Revoke it, or let it expire,
and the key stops working - so a command that was never used does not leave a
way onto the overlay behind. Reissue it and you get a new key; the old script
is then worth nothing.

Everything after that is the same as any other probe: the peer becomes an
ordinary one the moment the probe reports in, and `overlay show` treats it
like the rest.

## Taking a probe back off

```bash
sudo ./prtg-nats overlay mode mpp-berlin-01 off   # keep the address
sudo ./prtg-nats overlay remove mpp-berlin-01     # give it back
```

Both go over the probe's ordinary address on purpose: the request takes down
the tunnel that would otherwise carry it. A probe that does not answer there
is refused rather than left unreachable. `--force` overrides that when you
know what you are doing.

**Retiring a probe takes its peer too.** `./prtg-nats probe unenroll` and
**Retire** in the interface remove the inventory entry, and the hub is
re-rendered without it - so a retired probe loses its way onto the overlay
and its route to the NATS address at the same moment. The probe keeps running
and keeps its own configuration; it simply has no path any more. That is the
point: retiring a host has to take its access, not only our ability to manage
it.

**Turn off** on the same page - or `./prtg-nats overlay disable` - stops the
hub without touching a single peer. Re-enabling does not mean visiting every
probe again. Put any probe in mode `on` back to `auto` first, though, or it
will be looking for a tunnel that is no longer there.

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
- [ADR 0010](../architecture/decisions/0010-enrolling-a-probe-over-the-tunnel.md)
