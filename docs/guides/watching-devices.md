---
title: Watching devices
role: operator
updated: 2026-09-01
---

# Watching devices

A support desk usually needs one thing answered about a few hundred devices:
is it switched on. A printer at a branch office, a card terminal at a till.
This is the part of the platform that answers it, and it is deliberately
small - a sensor on the probe, a table here, one page in the interface.

It is not a second PRTG. There are no thresholds, no notification chains and
no device tree; PRTG stays the monitoring system, and the probes stay the
probes. What this adds is the question PRTG answers expensively at this
scale: sixty devices are sixty sensors to license and look after, and the
people who want the answer - a shop manager, a technician on the road - do
not have a PRTG login.

## How it works

```text
probe                              platform
  device-watch sensor  ──ask──▶  which devices do I watch?
                       ◀─list──
  ping / connect
                       ──report─▶  fold into the history
                                   (up since 08:12, down 14:03-14:41, …)
                                        │
                                   the dashboard
```

Everything travels over the NATS server this installation already runs, on
the account the probe already has. Nothing new is installed, no port is
opened, no credential is issued.

The history is stored as state intervals rather than as measurements: one row
per device per uninterrupted stretch in one state. A printer that has been on
since March is one row, availability over any window is exact rather than
sampled, and there is no time series database to run or back up.
[ADR 0011](../architecture/decisions/0011-availability-as-state-intervals.md)
has the reasoning, including what it would cost to add Influx later.

## Set it up

**1. Roll the sensor out to every probe that should measure.**

```bash
./prtg-nats sensor deploy device-watch mpp-hamburg-01
```

Or in the interface, under **Sensors**, like any other sensor. It brings a
privileged helper for ICMP, which the rollout installs as a socket-activated
service - the same shape `link-quality` uses, and for the same reason.

**2. Create the sensor object in PRTG.** A Script v2 sensor on the probe,
with the scanning interval you want to measure at. A minute is a good
starting point. That interval is the measuring interval: nothing here
schedules itself.

**3. Add the devices.** In the interface under **Availability**, or over the
API:

```http
POST /api/v1/watch/devices
{"display_name": "Kassendrucker 1", "address": "10.10.0.31",
 "probe_id": "01J…", "labels": {"team": "support", "site": "hamburg"}}
```

The probe measures on the next scan. No rollout, no restart - the sensor
fetches its list at the start of every run.

## Devices, and how they are checked

| Field | Meaning |
| --- | --- |
| `address` | hostname or IP, resolved on the probe |
| `method` | `icmp` (default) or `tcp` |
| `port` | for `tcp` only, and required there |
| `probe_id` | which probe measures it - the vantage point |
| `failure_threshold` | consecutive failed runs before it counts as down |
| `labels` | free key/value pairs, see below |

ICMP is the default because it needs no open port and says what this feature
asks: is the thing powered and on the network. For a device that answers
ICMP badly or not at all, `tcp` connects to a port and hangs up - 9100 for a
printer, whatever a terminal listens on.

The threshold is why a lost packet is not an outage. A card terminal drops
the odd echo request without anybody at the till noticing; three failed runs
in a row is a different statement. The failures are still recorded inside the
interval, so "up, but answering badly" stays visible.

A device belongs to exactly one probe. A printer in Hamburg is not reachable
from the Berlin site, and moving a device between sites is moving the
assignment - the history stays with the device.

## Labels, and who sees what

Labels are free key/value pairs, and they are how a team sees its own
devices:

```text
team=support   site=hamburg   room=kasse-2
```

The dashboard filters on them, and the filter is in the URL, so a shop gets a
bookmark that shows its own devices and nothing else:

```text
/availability?label=site:hamburg
```

Reading the dashboard is its own permission, `watch.read`, which every role
carries - including **viewer**, which grants nothing else that matters. That
is the account to hand out. Editing the list needs `watch.manage`, which the
operator and the administrator have. See
[Roles and permissions](../web/roles.md).

> Labels filter what is shown, not what may be read. A viewer who removes the
> filter sees every device. Handing one site an account that *cannot* see
> another site's devices is not something this version does.

## Three states, and why the third one matters

| State | Means |
| --- | --- |
| **up** | it answered |
| **down** | it did not, for as many runs in a row as its threshold |
| **unknown** | nobody measured |

`unknown` is not a third kind of broken. A device added a minute ago is
unknown; so is every device behind a probe that stopped reporting. That
distinction is the difference between "the Hamburg branch lost its uplink"
and "every printer in Hamburg was switched off", and the platform will not
confuse the two: when a probe goes quiet, its devices' intervals are closed
as unknown, backdated to the last measurement.

The dashboard also says whether the platform is receiving reports at all. A
page full of unknown devices has two causes, and that is the one an operator
can do something about.

## Reading the history

**Availability** over a window is computed from the intervals, so it is
exact:

```http
GET /api/v1/watch/devices/{id}/availability?days=30
```

```json
{"up_seconds": 2588400.0, "down_seconds": 2460.0, "unknown_seconds": 1140.0,
 "outages": 3, "longest_outage_seconds": 1680.0, "ratio": 0.99905}
```

Time nobody measured is its own number and is left out of the ratio. `ratio`
is null when nothing was measured at all - not zero, which would read as a
month-long outage.

**Outages** are the list support actually reads: what was down, since when,
how long, newest first.

```http
GET /api/v1/watch/outages?days=7&label=team:support
```

Round-trip times are kept summarised per five minutes - minimum, average,
maximum. Enough to see a device answering slower than it used to, and
deliberately not enough to plot a line. Whoever needs that resolution wants
`link-quality`, against a handful of targets rather than hundreds.

## What PRTG sees

The sensor hands PRTG a summary of its own site: how many devices, how many
reachable, how many not, and how long the run took. The channel worth an
alert is **Unreachable** - and the threshold belongs to whoever operates
PRTG, because one printer off overnight is routine in some shops and a
call-out in others.

That keeps the two roles apart: PRTG raises the alarm at three in the
morning, and this dashboard answers which device it was.

## Limits worth knowing

- **500 devices per probe and run.** The privileged helper refuses more; it
  is a measurement service, not a load generator.
- **A run that cannot report is lost.** The sensor keeps nothing between
  runs, and the platform records the gap as unmeasured rather than inventing
  an outage.
- **The measurement stops when the probe stops.** See `unknown` above.
- **Reports travel on subjects any probe account may publish to.** The
  platform only accepts a report for devices assigned to the reporting
  account, and drops the rest. Narrowing the NATS accounts themselves is
  its own piece of work - the third cost in ADR 0011 says so plainly.

## Troubleshooting

**Every device is unknown, and the dashboard says it is not receiving.** The
platform cannot reach its own NATS server. `docker compose ps`, then
`./prtg-nats status` - and check that `runtime/credentials/prtg-nats.env`
exists, which is the account the platform connects as.

**One probe's devices are unknown, the others are fine.** That probe is not
reporting. Its sensor may not be deployed, its PRTG sensor object may not
exist or be paused, or the site is offline - which is exactly what unknown is
there to say.

**The sensor reports failure code 2.** The privileged helper is missing or
not running:

```bash
systemctl status prtg-sensor-device-watch.socket
```

Redeploying the sensor reinstalls it.

**A device is down that is demonstrably on.** It is answering the check, not
the question - try `tcp` against a port it does serve. Printers in particular
are often configured not to answer ICMP at all.
