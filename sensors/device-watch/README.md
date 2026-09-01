# Sensor `device-watch` — is every device on this site switched on

The collector half of the availability monitoring. It asks the platform
which devices this probe should watch, measures them, reports the result
back over NATS, and hands PRTG a summary: how many devices, how many
reachable, how many not.

It exists for a question PRTG answers badly at this scale. A support desk
that wants to know whether forty printers and twenty card terminals are
switched on does not want sixty sensors in a device tree, each licensed
and looked after — and the people who need the answer, a shop manager or a
technician on the road, are not the people who get a PRTG login. They get
a viewer account on this platform and the dashboard under **Availability**.

## What it measures, and what it does not

One echo request per device, a couple of attempts, per run. Reachable or
not, plus the round-trip time as a by-product. That is deliberately all:

- **not** how good the line is — that is `link-quality`, against a handful
  of targets rather than hundreds;
- **not** what the device is doing — a printer that is on but out of paper
  answers here, and rightly so.

Devices that answer ICMP badly can be checked over TCP instead, per device,
in the interface: the sensor connects and hangs up. That path needs no
privileges at all.

## Where the device list comes from

Nowhere on the probe. The list is fetched over NATS at the start of every
run, so a printer added in the interface is measured on the next scan and
nobody rolls anything out. A probe measures only the devices assigned to
it, and the platform enforces that on the way back in as well: a report
naming somebody else's device is dropped and counted.

The sensor needs no credentials of its own either. NATS address, account,
password and CA come out of `/etc/paessler/mpprobe/config.yaml`, which the
sensor's service user can read because `prtg.mpprobe.service` has to read
it too.

Nothing is kept between runs. A report that cannot be delivered is lost,
and the platform records the gap as *unmeasured* rather than as an outage —
which is the honest answer and cheaper than a spool directory this service
user may not be able to create in the first place.

## Prerequisites

The sensor needs a **privileged helper**, specifically for ICMP: an echo
packet requires a raw socket, and `prtg.mpprobe.service` runs with
`NoNewPrivileges=yes`. The kernel therefore ignores both the setuid bit of
`sudo` and the file capability `cap_net_raw` of `/bin/ping`. Deployment
installs it as a socket-activated service, exactly as for `link-quality`:

```bash
./prtg-nats sensor deploy device-watch PROBE
```

The helper measures the whole list in one pass — every echo request goes
out first, then the answers are collected as they arrive. A rack of
switched-off printers therefore costs one timeout, not one per printer.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `--timeout-ms` | 1500 | milliseconds to wait for a single answer |
| `--packets` | 2 | attempts per device before it counts as unreachable in this run |

A device does not become *down* in the history because one run found it
unreachable. The platform holds a per-device threshold — three consecutive
failed runs by default — because a card terminal drops the odd echo request
without anybody noticing at the till.

## Channels

| Channel | Meaning |
| --- | --- |
| **Devices** | how many devices this probe watches |
| **Reachable** | how many answered in this run |
| **Unreachable** | how many did not — the channel to set an alert on |
| **Failure Code** | 0 while the sensor works; a number when it cannot |
| **Duration** | how long the run took |

The alert threshold on **Unreachable** stays with whoever operates PRTG:
one printer off overnight is normal in some shops and a call-out in others.

`Failure Code` is a cause the sensor can state without anybody parsing
message text — 3 for a probe configuration it cannot read, 4 for a NATS
server it cannot reach, 2 for a missing privileged helper.

## The interval

The scanning interval in PRTG is the measuring interval. A minute is a good
starting point: it is fast enough for a support desk and slow enough that
three hundred devices are nowhere near the run's own budget.

## Documentation

[Watching devices](../../docs/guides/watching-devices.md) has the whole
picture, from adding the first printer to reading the dashboard.
[ADR 0011](../../docs/architecture/decisions/0011-availability-as-state-intervals.md)
explains why the history is state intervals rather than a time series
database.
