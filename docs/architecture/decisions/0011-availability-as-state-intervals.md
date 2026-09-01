---
title: Availability is stored as state intervals, not as a time series
role: developer
updated: 2026-09-01
status: accepted
---

# 11. Availability is stored as state intervals, not as a time series

## Context

An IT support desk wants one question answered for a few hundred devices:
is it switched on? A printer at a branch office, a card terminal at a till.
Not throughput, not response time distributions - power and network, yes or
no, and how often it was not.

PRTG answers that too, but every device that gets an answer there is a
sensor that has to be licensed, placed in a device tree and looked after.
For a question this small that is the wrong unit of accounting, and the
people who need the answer - a shop manager, a field technician - are not
the people who get a PRTG login.

The obvious build is the one everybody draws on a whiteboard: probes ping,
results go into InfluxDB, Grafana draws it. That is three moving parts for
a boolean. It also stores the wrong thing. A ping every 60 seconds against
300 devices is 158 million data points a year, virtually all of them
repeating the previous one, and answering "how available was this printer
in August" then means aggregating those points back into exactly the
information that was thrown away when they were written: the times the
state changed.

## Decision

The database stores state intervals. One row per device per uninterrupted
stretch in one state:

    device, state, started_at, ended_at, samples, failures

A measurement that agrees with the open interval extends it - one `UPDATE`,
no new row. A measurement that disagrees closes it and opens the next one.
The same 300 devices produce a few thousand rows a year, in the SQLite that
is already there, backed up by the backup that is already there.

Availability over any window is then arithmetic on interval overlaps, and
it is exact rather than sampled: no bucket boundary, no interpolation, no
"it was down for four minutes but the 5-minute bucket says three". The
outage list, the thing support actually reads, is a `SELECT` over the down
intervals instead of a query language.

Latency keeps its own, deliberately coarse table: one row per device per
five minutes with minimum, average and maximum. That is enough to see a
printer answering slower than it used to, and it is two orders of
magnitude less data than the samples it summarises.

The collector is not a new daemon. It is a Script v2 sensor like every
other sensor in `sensors/`, so it inherits the whole rollout machinery -
staging, activation, rollback, version and checksum drift, the signed
privileged helper. It fetches its target list from the platform over NATS
at the start of every run, measures, and publishes the results back over
NATS. Nothing new is installed on a probe that the platform cannot already
install, update and remove.

Transport is the NATS server that this repository exists for, over the
account the probe already has. The sensor reads URL, user, password and CA
out of `/etc/paessler/mpprobe/config.yaml`, which its service user can read
because MPP itself must read it. No second account, no second credential
to rotate, no second port to open in a firewall.

## Consequences

**Good.** No new container, no new database, no new backup, no new daemon,
no new credential. The feature is a table, a worker and a sensor.

**Good.** The measuring interval is PRTG's scanning interval, so the thing
that already decides how often a probe does work keeps deciding it.

**Good.** Every answer the dashboard gives is exact. "Available 99.2% in
August, three outages, the longest 41 minutes" is computed from the
intervals themselves.

**Cost.** No high-resolution history. Whoever wants to see the round-trip
time of one device second by second over a week will not find it here -
`link-quality` is the sensor for that question, against a handful of
targets rather than hundreds.

**Cost.** The measurement stops when the sensor's probe stops. A device is
then not down, it is unmeasured, and the two must not be confused: an
interval whose probe has gone silent is closed as `unknown`, and the
dashboard says so rather than reporting a fleet-wide outage every time a
branch office loses its uplink.

**Cost.** Results travel on subjects any probe account may publish to. The
authorization block in `nats-server.conf` grants no subject permissions
today - it is user and password, and every account may use every subject.
A probe that is taken over can therefore report for devices that belong to
another probe. The ingest guards what it can: a report is only accepted for
devices assigned to the reporting account, and everything else is dropped
and counted. Narrowing the accounts themselves is a change to the PRTG
traffic's own permissions and belongs in its own decision.

**The seam.** The ingest writes intervals through one service. Whoever
wants Influx or VictoriaMetrics later adds a second sink there and keeps
the intervals as the authoritative answer.
