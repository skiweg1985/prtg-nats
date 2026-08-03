# Sensor `link-quality` — reachability and connection quality

The sensor measures against several targets at once and reports packet
loss, round-trip time and jitter per target. It answers whether an uplink
is *usable* — not just whether it is *there*. A line with 3 % packet loss
passes every ping check and still makes telephony and terminal sessions
unusable.

The actual point compared to an ordinary ping sensor are the **two target
classes**:

| Parameter | Meant are | Own channel |
| --- | --- | --- |
| `--target` | targets on the internet | **Internet Reachable** |
| `--internal-target` | targets on your own network, say behind a VPN tunnel | **Internal Reachable** |

Only that makes "the internet is gone" distinguishable from "just the
tunnel is gone" without keeping two sensors side by side. Whoever wants to
know first where to start looking during a 3 a.m. alarm gets the answer
from a single sensor.

In addition, with `--source` the sensor measures from a specific local
address — deliberately through a specific path when the probe has several.

## Prerequisites

The sensor needs a **privileged helper**, specifically for ICMP: an echo
packet requires a raw socket, and `prtg.mpprobe.service` runs with
`NoNewPrivileges=yes`. The kernel therefore ignores both the setuid bit of
`sudo` and the file capability `cap_net_raw` of `/bin/ping`. Deployment
takes care of both — it installs the helper and sets up its socket.

> An unprivileged ICMP socket would be an alternative, but only on some
> probes: it depends on `net.ipv4.ping_group_range`, whose default differs
> per distribution and release. A sensor that measures on one probe and
> not on the next is worse than one that takes the same path everywhere.

No PyPI dependency, no virtual environment, no package index needed — the
sensor gets by with the standard library.

Probes enrolled before sensor management existed answer with
`Unsupported management request`. They need `./prtg-nats probe helper-update
USER` first, or "Update helper" on the probe page. One that reports no
`helper_version` at all needs a one-time
`./prtg-nats probe enroll USER ADMIN@HOST --reenroll` before that works.

## Set up

```bash
./prtg-nats sensor deploy link-quality mpp-probe-01
```

Deployment only counts as successful once the self-test has passed. It
checks not only that the helper is reachable, but also that it may open an
ICMP socket — otherwise a missing permission would only show up on the
first real run and look like a network failure there.

To every probe at once:

```bash
./prtg-nats sensor deploy link-quality --all
```

Removal is `./prtg-nats sensor remove link-quality mpp-probe-01`.

## Specifying targets

```text
[NAME=][tcp:]HOST[:PORT]
```

| Spec | Effect |
| --- | --- |
| `1.1.1.1` | ICMP echo to an address, channel name `1.1.1.1` |
| `Cloudflare=1.1.1.1` | the same, channel name `Cloudflare` |
| `HQ=10.0.0.10` | a name is what makes the channel list readable |
| `tcp:example.com:443` | connection setup instead of echo, for targets that drop ICMP |
| `[2001:db8::1]` | IPv6 goes in brackets |
| `Web=tcp:[2001:db8::1]:443` | both together |

**Assign names.** Without them, the channels are named after the address,
and a channel list of eight IP addresses is worthless in a 3 a.m. alarm.

**Choose TCP when ICMP does not get through.** Some operators and most
cloud services drop echo packets on principle. Entering such a target as
ICMP would produce a channel that permanently reports 100 % loss without
anything being broken. With `tcp:` the time to the completed handshake is
measured.

Up to **6 external** and **4 internal** targets are allowed.

## Parameters

For a Script v2 sensor they go into the **Parameters** field. The normal
case only needs `--target` and `--internal-target`.

| Parameter | Meaning |
| --- | --- |
| `--target SPEC` | target on the internet, repeatable. **At least one is required** |
| `--internal-target SPEC` | target on your own network, repeatable |
| `--packets N` | echo packets per target and run, default 20 |
| `--interval-ms N` | gap between two packets, default 200 |
| `--timeout-ms N` | wait for a single answer, default 1000 |
| `--payload-bytes N` | payload per echo, default 56 like `ping` |
| `--source IP` | local address to measure from |
| `--self-check` | check the ability to run, without measuring |

Targets times packets are capped at **400 per run**. The cap is no
convenience but the protection against the probe becoming a load tool;
whoever exceeds it gets a message with the line that has to stand there
instead.

### Examples

The normal case at a site with a VPN to headquarters:

```text
--target Cloudflare=1.1.1.1 --target Google=8.8.8.8 --internal-target HQ=10.0.0.10
```

An operator that drops ICMP, and the own terminal server:

```text
--target Portal=tcp:portal.example.com:443 --internal-target TS=tcp:10.0.0.20:3389
```

Measure deliberately through a tunnel when the probe has several paths:

```text
--target 1.1.1.1 --internal-target HQ=10.0.0.10 --source 10.8.0.2
```

Resolve more finely where small losses matter — 50 packets resolve 2 %
instead of 5 %:

```text
--target VoIP-GW=tcp:sip.example.com:5060 --packets 50
```

### How finely the loss is resolved

The loss can only be reported in steps of `100 ÷ --packets` percent. That
is not rounding but the resolution of the measurement:

| `--packets` | Resolution | Duration per run |
| --- | --- | --- |
| 10 | 10 % | 2 s |
| 20 (default) | 5 % | 4 s |
| 50 | 2 % | 10 s |
| 100 | 1 % | 20 s |

To alarm on 1 % loss you need 100 packets — with 20, a single lost packet
is already 5 %. The default of 20 hits the usual case: it detects the
losses that hurt, without a run taking long.

## Data volume and load

Negligible, and that is the essential difference to
[`internet-speed`](../internet-speed/README.md): twenty packets of 56 bytes
are about **1.7 kB** per target and direction. A run with three targets
moves about 10 kB. The sensor may therefore run at a short interval; it
needs no minimum interval like the throughput sensor, and has none.

All targets are served **on the same beat**, not one after another. That is
not just faster: only that way do all targets see the same network
conditions, making their values comparable at all. Working through one
target after another would mean comparing measurements from different
minutes.

TCP targets are exempt: they get at most **10 attempts** with at least
**200 ms** spacing, regardless of `--packets`. A connection attempt leaves
a log entry on the far side, and twenty of them per minute would look like
a port scan.

If several sensors of this kind run on one probe, they measure one after
another: a lock caps the packet rate. Whoever would have to wait longer
than ten seconds reports `busy` instead of silently delivering delayed
values.

## Channels

| ID | Channel | Unit |
| --- | --- | --- |
| 10 | Test Result | 1 = measured, 2 = failed |
| 11 | Targets Reachable | count |
| 12 | Packet Loss | %, across all targets |
| 13 | Latency | ms, mean |
| 14 | Jitter | ms, mean |
| 15 | Worst Latency | ms, worst target |
| 16 | Worst Packet Loss | %, worst target |
| 17 | Test Duration | ms |
| 18 | Failure Code | see the table below |
| 19 | Quality Index | 0 to 93, see below |
| 20 | All Targets Reachable | 1 = all, 2 = at least one missing |
| 21 | Internet Reachable | 1 = at least one external target answers |
| 22 | Internal Reachable | 1 = at least one internal target answers |
| 30, 31, 32 | 1st external target: Loss, Latency, Jitter | %, ms, ms |
| 33 … 47 | 2nd to 6th external target | likewise |
| 50, 51, 52 | 1st internal target: Loss, Latency, Jitter | %, ms, ms |
| 53 … 61 | 2nd to 4th internal target | likewise |

**Primary channel.** For the normal case, **All Targets Reachable** — the
channel alarms on its own, no limit needs entering. Where quality counts
and not just reachability, **Quality Index** with a lower limit is the
better choice.

**Channels 21 and 22 are the actual diagnosis.** If 21 reads 1 and 22
reads 2, the uplink is fine and the tunnel is gone. If both read 2, the
line itself is affected. Both channels appear in the output even when the
measurement did not run at all — otherwise precisely the two channels
meant to name an outage would disappear.

**Round-trip time and jitter are float channels.** An internal target
answers in fractions of a millisecond; as an integer, a permanent 0 would
stand there — no statement at all exactly where the line is fastest.

An unreachable target reports **100 % loss and 0 ms**. The zero is not a
reading but the absence of one; the statement is in the loss channel.

### The quality index

Channel 19 condenses round-trip time, jitter and loss into one number: the
**R factor of the simplified E-model** per ITU-T G.107.

```text
effective delay = round-trip time + 2 × jitter + 10 ms
R = 93.2 − delay/40        (up to 160 ms)
R = 93.2 − (delay−120)/10  (above)
R = R − 2.5 × loss in percent
```

| R | Meaning |
| --- | --- |
| 90 and above | very good |
| 80 to 90 | good, the usual expectation of a business uplink |
| 70 to 80 | usable, telephony audibly worse |
| 50 to 70 | poor |
| below 50 | unusable |

The index is meant for voice, and exactly that makes it usable here: voice
is the most sensitive common application, so the value trips before anyone
calls. Jitter counts double because a receiver has to compensate for it,
and that compensation acts as additional delay.

> **The index never reaches 100.** The model's optimum is 93.2, and even
> the shortest path costs a remainder. A warning threshold at 95 would
> alarm permanently; sensible are 80 as warning and 70 as error.

**The index rates only the answering targets.** A dead target belongs in
reachability, not in quality — otherwise a single target that drops ICMP
on principle would pull the index to a permanent 0, and a channel that
always shows 0 no longer says anything about the line. Channel 12 can
therefore well report 50 % loss while channel 19 reads 93: half the
targets are gone, and the path that is still there is impeccable. How many
targets answer at all is in channels 11, 20, 21 and 22.

**Round-trip time, jitter and index do not average across both measurement
kinds.** A TCP handshake contains the far end's reaction and sits
systematically above an echo; averaging both would produce a number no
path ever had. If there are echo targets, they decide; otherwise the TCP
targets take their place. The loss is exempt and counts across all
targets — a lost packet is a lost packet.

### Failure codes

| Code | Channel 18 | Meaning |
| --- | --- | --- |
| `ok` | 0 | measurement ran |
| `bad-request` | 1 | the task was faulty — sensor error |
| `no-privileges` | 2 | the helper does not run as root — sensor error |
| `socket-failed` | 3 | no ICMP socket to be had — sensor error |
| `bind-failed` | 4 | the address from `--source` no longer exists |
| `busy` | 5 | another run is measuring right now |
| `internal-error` | 6 | unexpected failure in the helper |

The first three are reported as **sensor errors**, not measurements: they
say nothing about the uplink. `bind-failed`, by contrast, does — a
vanished tunnel address is exactly the finding this is about.

Per target there is additionally a reason that appears in the sensor
message:

| Reason | Meaning |
| --- | --- |
| `no-reply` | no answer within the wait |
| `unreachable` | the far end or a router reports "unreachable" |
| `expired` | the packet's lifetime ran out — routing loop |
| `refused` | with `tcp:`: the port is closed |
| `resolve-failed` | the name could not be resolved |
| `bad-address` | the name points at a multicast or broadcast address |

**An unresolvable target does not discard the others' measurement.** A
typo in one of five targets would otherwise take the other four with it —
and a failed resolver is more of a finding about the uplink than an
operating error anyway.

## Create the sensor in PRTG

1. Add a **Script v2 sensor** on the probe's device.
2. Select `link-quality.py` as the script.
3. Enter the parameters, see above. **Without `--target` the sensor does
   not run.**
4. Set the scanning interval to 1 to 5 minutes. The sensor produces hardly
   any load; a short interval is harmless here.
5. Set the timeout to at least `--packets × --interval-ms` plus 30
   seconds, so 35 seconds with the defaults.

**Only append targets, never insert in between.** The channel numbers
follow the parameter order. Whoever inserts a target in second place
shifts all following ones — the history in PRTG then hangs on the wrong
channel. Appending is harmless, and the two classes have separate number
ranges: an additional external target leaves the internal ones untouched.

## Check without PRTG

On the probe, as root. The invocation reproduces how the MPP service
starts the script: as the service user and with its hardening. That is
essential — from a root shell the call succeeds even when it fails at
exactly those limits in the real service context.

Check only the ability to run, without any network traffic:

```bash
echo '--self-check' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" -- /opt/paessler/share/scripts/link-quality.py
```

If parameters come along, the self-test checks them too — likewise without
network traffic. That way a configuration can be checked **before** it is
entered in PRTG, where nobody checks it any more:

```bash
echo '--self-check --target Cloudflare=1.1.1.1 --internal-target HQ=10.0.0.10' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" -- /opt/paessler/share/scripts/link-quality.py
```

A real measurement:

```bash
echo '--target Cloudflare=1.1.1.1 --packets 10' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" -- /opt/paessler/share/scripts/link-quality.py
```

Is the helper listening?

```bash
./prtg-nats sensor status mpp-probe-01
```

The state `helper=listening` is the answer. On the probe itself,
`systemctl status prtg-sensor-link-quality.socket` shows the same.

The helper remains an ordinary filter program and can be called by hand as
root — useful when the question is whether the problem sits with the
sensor or the helper:

```bash
echo '{"targets":[{"host":"1.1.1.1"}],"packets":5}' | /usr/local/sbin/prtg-sensor-link-quality
```

## How the measurement runs

1. The parameters are checked before a single packet flows. A mistake
   leads to a sensor error with the line that has to stand there instead.
2. The task goes as JSON over the Unix socket to the privileged helper.
3. It takes the lock, waiting up to ten seconds for it. If another sensor
   is already measuring, this run reports `busy` instead of silently
   delivering delayed values.
4. The target names are resolved and the addresses checked. An
   unresolvable name makes **only that one target** unreachable; the rest
   are measured.
5. The raw sockets are opened, bound to the given address with `--source`.
6. Then the paced rounds, see below. TCP targets run in their own threads
   alongside.
7. The helper returns loss, round-trip times and jitter per target; the
   sensor script builds the aggregates, the quality index and the channels
   from them.

With the defaults a run takes about five seconds.

### The paced rounds

In every round, an echo request goes to **every** ICMP target, then the
helper listens for answers until the end of the interval, then the next
round follows. All targets are thus served on the same beat, not one after
another — only that way do they see the same network conditions, making
their values comparable at all. Working through one target after another
would mean comparing measurements from different minutes.

Every packet carries a run identifier, a running sequence number and a
fixed marker string in the payload. The sequence number attributes every
answer exactly to its packet and thereby yields the round-trip time. The
marker is the second barrier: a raw socket sees **every** ICMP answer of
the machine, including that of a concurrently running `ping` — without it,
foreign traffic would flow into the measurement.

After the last round the helper waits another `--timeout-ms` for
outstanding answers. Whatever is then missing counts as lost. An answer
arriving even later is **not** credited retroactively: for the application
the packet was too late, and a late round-trip time would distort the
mean.

### Why the sensor can tell "refused" from "silent"

An ICMP error message — *Destination Unreachable*, *Time Exceeded* —
carries the start of the packet that triggered it. The helper reads the
identifier and sequence number out of it and thereby attributes the
message to the same target.

Hence the separate reasons in the sensor message: `unreachable` means
somebody on the path explicitly refuses — a firewall or a router without a
route. `no-reply` means simply nothing came back. For troubleshooting that
is the difference between "somebody says no" and "nobody is there".

### TCP targets

Instead of an echo, a connection is set up and the time to the completed
handshake is measured. A failed attempt counts as loss; an actively
refused one reports `refused`, which means the target is alive and only
the port is closed.

These targets deliberately run more restrained: at most ten attempts with
at least 200 ms spacing, regardless of `--packets`.

## Limits

**ICMP is not payload traffic.** Routers often deprioritise echo packets
and rate-limit them. A loss of a few percent against a single target can
therefore be that router's rate limiting rather than the line. That is
exactly why the sensor measures against several targets: what appears on
all at once is your own uplink; what only one target shows is its path.

**The sensor measures the outbound and return path together.** A
round-trip time cannot be split into its directions. A loss affecting only
the upload cannot be told here from one affecting only the download.

**`--source` binds the address, not the route.** Whether the packet really
goes through the intended tunnel is decided by the probe's routing table.
With cleanly set routes the source address is the right lever; with
policy routing by interface it is not.

**A target that drops ICMP only intermittently** produces loss values that
are none. Such targets should be entered as `tcp:`.

The privileged helper's limits — at most 10 targets, 100 packets per
target, 400 packets per run, at least 50 ms pacing — are fixed in the
program and are not parameters. They are the protection against a
compromised service user using the probe as a load tool against an
arbitrary target. The helper refuses multicast and broadcast addresses as
targets: an echo there makes many devices answer at once, which is
pointless as a measurement and dangerous as amplification.
