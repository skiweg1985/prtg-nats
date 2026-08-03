# Sensor `iperf-throughput` — check the uplink against your own endpoint

The sensor measures throughput, packet loss, jitter and latency between a
probe and a **self-operated iperf3 endpoint**. It answers whether a site
has the path to its own services at the required quality — VPN to
headquarters, telephony, terminal sessions.

That is deliberately **not** the same question as the
[`internet-speed`](../internet-speed/README.md) sensor's:

| Sensor | Measures against | Answers |
| --- | --- | --- |
| `internet-speed` | speedtest.net | Does the provider deliver the contracted line? |
| `iperf-throughput` | your own measurement endpoint | Can the site do its work? |

The first number you put in front of a provider. The second wakes you at
night. Both are useful; neither replaces the other.

The advantage of your own endpoint is not just the path but the
**stability**: it always sits in the same place. With speedtest.net the
server selection decides which path is measured, and it changes between
runs — a throughput curve that shows a server change is no longer readily
readable.

## Operational in two commands

```bash
sudo ./prtg-nats iperf-server install root@iperf.example.com
sudo ./prtg-nats sensor deploy iperf-throughput --all
```

The first sets up the endpoint over SSH and keeps its password; the second
brings sensor **and** credentials onto the probes. After that only the
sensor in PRTG remains, see
[create the sensor in PRTG](#create-the-sensor-in-prtg).

The details, the manual path and the password change are described in
[setting up the endpoint](#set-up-the-endpoint) and
[setting up the endpoint by hand](#set-up-the-endpoint-by-hand).

## Prerequisites

- **`iperf3` on the probe.** A Debian package, no virtual environment, no
  package index required — deployment pulls it in itself:

  ```bash
  apt-get install iperf3
  ```

- **A reachable iperf3 endpoint**, see below.
- **Credentials**, if the endpoint authenticates — recommended. Where the
  sensor takes them from: see [credentials](#credentials).

The sensor needs **no** root privileges and no privileged helper.

## Set up the endpoint

On the server every site can reach — usually where the site VPNs
terminate. From the NATS server it is **one command**:

```bash
sudo ./prtg-nats iperf-server install root@iperf.example.com
```

It signs in over SSH, installs `iperf3`, creates key pair and credentials,
switches the service to authentication, verifies the result — and keeps
password and public key here. That is exactly the point:

> **The password is created on the NATS server and sent to the endpoint,
> not the other way round.**

The endpoint itself stores only a SHA-256 of the credentials; a password
created there could not be read back after the run and would have to be
copied off the screen. Created centrally, it is still at hand — and can be
rolled out to the probes as a profile without an intermediate step.

After that only the sensor is missing, and it brings the credentials along
on its own:

```bash
sudo ./prtg-nats sensor deploy iperf-throughput --all
```

That is all. The endpoints can also be managed on the infrastructure pages
of the web interface.

### The commands

| Task | Command |
| --- | --- |
| set up an endpoint | `./prtg-nats iperf-server install ADMIN@HOST` |
| show configured endpoints | `./prtg-nats iperf-server list` |
| values of one endpoint | `./prtg-nats iperf-server show NAME` |
| credentials to one probe | `./prtg-nats iperf-server deploy NAME USER` |
| credentials to every probe | `./prtg-nats iperf-server deploy NAME --all` |
| revoke credentials | `./prtg-nats iperf-server revoke NAME USER` |
| change the password | `./prtg-nats iperf-server install ADMIN@HOST --name NAME --rotate` |
| forget an endpoint | `./prtg-nats iperf-server forget NAME` |

Options of `iperf-server install`:

| Option | What for |
| --- | --- |
| `--name NAME` | name the endpoint is kept under — and at the same time the profile name on the probe. Default: the short form of the host name |
| `--user NAME` | user name the probes authenticate as. Default `prtg-probe` |
| `--port PORT` | port of the endpoint. Default 5201 |
| `--rotate` | generate a **new** password and update every probe that already has this endpoint |
| `--dry-run` | only show what would happen |

**A second run does no harm.** Without `--rotate` it sets the endpoint to
exactly the password stored here — the already-served probes keep their
access. That also lets you bring an endpoint back in line that somebody
changed by hand.

**The endpoint gets no permanent access from here.** Unlike a probe it is
not managed, only measured; every intervention signs in anew. That is why
`--rotate` needs an SSH target again as well.

**Rollout happens as a profile**, over the same path as the credentials of
all other sensors — see [credentials](#credentials). As long as only one
endpoint is configured, its profile is additionally called `default`; the
sensor then finds it without `--profile`.

The Debian package's unit stays untouched — the authentication sits next
to it as a drop-in under `/etc/systemd/system/iperf3.service.d/auth.conf`.
Deleting that file and running `systemctl daemon-reload` takes the change
back completely.

**Three things easily overlooked here:**

- **Open UDP.** The sensor measures with UDP on the same port. Whoever
  opens only `tcp/5201` gets `unable to read from stream socket` — a
  message that does not sound like a firewall.
- **The clock.** The authentication checks a timestamp. If a probe's clock
  is off by more than ten seconds, the sign-in fails. NTP is a
  prerequisite, on both sides.
- **Authentication is not encryption.** The credentials are protected, the
  measurement data runs in cleartext. An endpoint reachable from the
  internet additionally belongs behind a firewall that lets only the sites
  through.

### A second endpoint

From the second site cluster on, a dedicated endpoint pays off — the path
being measured should be the one the services run on, after all. It is set
up the same way:

```bash
sudo ./prtg-nats iperf-server install root@iperf-south.example.com
sudo ./prtg-nats iperf-server deploy iperf-south --all
```

Every endpoint gets its **own** password and its own key, and both sit on
the probe in a profile under its name. Whoever compromises one of them
does not have the others.

From the second on, a sensor has to say which one it measures against —
the profile is named after the endpoint:

```text
--server iperf-south.example.com --username prtg-probe --profile iperf-south --download-mbit 30 --upload-mbit 10
```

As long as only one is configured, its profile is additionally called
`default`, and the sensor finds it without further ado. From the second on
that stops: which path is measured should not depend on which endpoint was
created first.

**One test at a time, per endpoint.** Several endpoints are also the
answer to a growing fleet: iperf3 accepts only one client at a time, and
two servers on two addresses simply double the capacity.

## Set up the endpoint by hand

The guided path above needs a NATS server that can reach the endpoint over
SSH. If that is not possible — an endpoint in a third-party network, a
customer operating it themselves — the same path also works by hand. It is
the same script.

### 1. On the endpoint

Copy [`endpoint/setup-iperf3-endpoint.sh`](endpoint/setup-iperf3-endpoint.sh)
there and run it — as root, or as somebody who may `sudo`; the script
elevates itself:

```bash
bash setup-iperf3-endpoint.sh
```

At the end it prints what the probes need:

```text
== Counter-check ==
  a client without credentials is rejected
  a client with credentials is accepted

== What the probes need ==
  user name:  prtg-probe
  password:   3f9a…                      <- store it now
  public key: /etc/iperf3/public.pem
```

**The password cannot be read back afterwards** — only its SHA-256 is on
disk. Store it away the moment it appears. To supply your own instead, put
it in a file; there is no command-line option for it, because there it
would land in the shell history:

```bash
printf '%s\n' 'THE-PASSWORD' > /root/iperf-password
bash setup-iperf3-endpoint.sh --password-file /root/iperf-password
shred -u /root/iperf-password
```

| Option | What for |
| --- | --- |
| `--user NAME` | different user name, default `prtg-probe` |
| `--password-file PATH` | your own password instead of a generated one |
| `--port PORT` | different port, default 5201 |
| `--force-credentials` | **password change**: new credentials, the key pair stays. The probes only need the new password |
| `--force` | replace key pair **and** credentials. Every probe needs both anew afterwards |

Without the two `--force` options everything existing stays untouched; the
script only verifies and reports. That also makes it usable to inspect an
endpoint somebody else set up.

### 2. As a profile onto the probes

Password and public key become a profile file. The key is base64-encoded
in it, because a PEM with its line breaks does not fit into a `KEY=VALUE`
file:

```bash
{
  printf 'IPERF3_PASSWORD=%s\n' 'THE-PASSWORD'
  printf 'IPERF3_PUBLIC_KEY_B64=%s\n' "$(base64 -w0 < public.pem)"
} > iperf-north.env
./prtg-nats sensor profile iperf-throughput mpp-probe-01 default \
  --from-file iperf-north.env
shred -u iperf-north.env
```

`iperf-server deploy` generates exactly this file — just from the password
it assigned itself.

## Deploy the sensor

```bash
./prtg-nats sensor deploy iperf-throughput mpp-probe-01
```

**Deployment pulls in `iperf3` itself** when it is missing — the same way
it takes care of `python3-venv`. The package name is fixed in the probe
helper, not in the sensor manifest: the management channel must not be
usable to pull in arbitrary packages. If the installation fails, the
sensor fails the self-test with `tool-missing` and the probe restores the
previous state.

**The credentials of all configured endpoints come along.** A sensor
without them would only report `credentials-unreadable`; they therefore
belong in the same operation, not in a second command somebody has to
remember. An endpoint that does not exist yet does not block deployment —
the sensor is then installed and says on its first run what it is
missing.

The server stores the profile files under `runtime/sensor-profiles/` — in
the same protected area as the NATS passwords and outside Git. A single
endpoint can be re-deployed at any time:

```bash
./prtg-nats iperf-server deploy iperf-north mpp-probe-02
```

## Credentials

`--username` decides everything else. Three cases:

| Parameters | Where the password comes from | What for |
| --- | --- | --- |
| *nothing* | — | **no authentication.** For an endpoint on your own network behind a firewall |
| `--username NAME` | from the deployed profile | **the regular case.** A change is a deployment, not a walk through thirty PRTG sensors |
| `--username NAME --password SECRET` | from the sensor parameter | for **probes managed by someone else**, where nothing can be placed |

A `--password` without `--username` is rejected as a configuration error —
otherwise the sensor would silently fall back to an unauthenticated
measurement, and exactly that is what nobody notices.

**The profile is the regular case, and for a reason:** a password in the
parameter field sits in cleartext in the PRTG sensor configuration and is
visible there to everyone allowed to open the sensor.

On the probe the profile lives under
`/etc/prtg-nats/sensors/iperf-throughput/profiles/`,
`root:paessler_mpprobe` with mode `0640`. The permissions are no
formality: the sensor **discards** a world-readable profile. A file
everyone may read is not a secret.

Unlike with `wlan-auth`, the file belongs to the service group — this
sensor has no privileged helper and reads it itself. The probe decides
that from what is installed, not from a claim by the caller.

**Several endpoints with different passwords** each get a profile, named
after the endpoint — `iperf-server deploy` creates it:

```text
--server iperf-south.example --username prtg-probe --profile iperf-south
```

As long as only one is configured, its profile is additionally called
`default`, and `--profile` can be dropped.

### Changing the password

```bash
sudo ./prtg-nats iperf-server install root@iperf.example.com --name iperf --rotate
```

The endpoint gets a new password, the key pair stays — and every probe
that already has it is updated in the same run. A probe that is
unreachable at that moment is named; it cannot measure until the next
`iperf-server deploy`.

Which path a sensor takes is told by the self-test:

```text
iperf 3.18 is ready. The configured parameters are valid.
Authenticating as prtg-probe with the password deployed on this probe.
```

## With or without a target rate

The sensor has two modes of operation, and the target rate selects
between them:

| Given | Question | Effect on the line |
| --- | --- | --- |
| **no target rate** | How fast is the path? | **saturates it** for 5 s per direction |
| `--download-mbit 30` | Does it hold 30 Mbit/s? | takes at most 30, for 5 s per direction |

**Without a target rate** the sensor measures both directions as fast as
the path yields. The **Target Met** channel is then absent — there is
nothing to pass — and **Download** and **Upload** are a real trend curve.
The alarm is then a lower limit on those channels, as usual.

**With a target rate** it becomes an assurance. The rate is then **the
business minimum** — the rate below which the site can no longer work. Not
the contracted rate, and not a fraction of it either. The **Download**
channel is capped at the target rate by construction and worthless as a
curve while the path holds it.

Saturation happens in both cases — up to the target rate or up to the
capacity. Target rate plus typical production load should therefore stay
below about 70 % of the capacity, and a sensor without a target rate
belongs in the off-hours or on a generous `--measure-every-minutes`.

The usual pattern is two sensors on the same device:

| Sensor | Parameters | Purpose |
| --- | --- | --- |
| assurance | `--udp --download-mbit 30 --upload-mbit 10` | alarm, hourly |
| capacity | `--measure-every-minutes 1440` | trend curve, once a day |

## TCP or UDP

The difference is not technical but substantive.

| | TCP (default) | UDP (`--udp`) |
| --- | --- | --- |
| behaviour on a tight line | backs off, rate drops | keeps sending, packets get lost |
| finding | achieved rate below the target | **packet loss** |
| additionally | retransmits, real round-trip time | jitter per direction |

**UDP is the stricter check.** It asks "does the line carry this rate",
and the answer is loss or no loss — without the detour through TCP's
adaptation. **TCP is the more useful trend curve**, because the achieved
rate is a number with a history and the round-trip time comes from the
kernel, not from a measurement with errors of its own.

**`--udp` therefore needs a target rate** and is rejected without one. A
UDP run without a rate measures nothing: iperf3 then sends at its own
default of **one megabit per second** and dutifully reports "no loss" —
measured on a probe. That would look like a healthy line and say nothing
about it.

Both are recommended, as two sensors on the same device:

| Sensor | Parameters | Purpose |
| --- | --- | --- |
| assurance | `--udp --download-mbit 30 --upload-mbit 10` | alarm on **Target Met** |
| trend | *without a target rate* | curve on **Download**, **Upload**, **Ping** |

A lock file makes sure the two never measure at the same time — and
**not together with `internet-speed`** either: all sensors that measure
throughput share the same lock on a probe. Otherwise one would saturate
the line while the other checks its target rate, and the alarm would fire
over a perfectly healthy line. If the lock is taken, the sensor serves its
last result (**Result Age** shows the age) and measures on the next scan.

## Data volume and load

Transfer runs **5 seconds per direction**. With a target rate the volume
is thereby exactly predictable: target rate × 5 seconds ÷ 8.

| Target rate | per run | hourly |
| --- | --- | --- |
| 10 Mbit/s | 6 MB | 6 MB |
| 30/10 Mbit/s | 25 MB | 25 MB |
| 100/40 Mbit/s | 87 MB | 87 MB |

**Why five and not ten seconds**, although iperf3 itself defaults to ten:
measured on a probe pairwise alternating, so congestion events hit both
variants alike — 828.8 / 830.7 / 778.9 / 785.4 Mbit/s over five seconds
against 564.9 / 689.6 / 653.7 / 870.2 over ten. The short measurement
reads higher **and** scatters six times less.

The per-second trace explains it: after TCP slow start in the first second
comes the peak, and from about second six the throughput drops — a burst
allowance or building congestion. Whoever measures longer averages that
drop in.

**The price:** a line that only throttles after several seconds goes
unnoticed. That is what `--seconds` is for:

```text
--server iperf.example.com --username prtg-probe --seconds 30
```

Allowed are 2 to 60 seconds. Below two it becomes unusable — the first
second belongs to TCP slow start, and with UDP the accounting tips over: a
three-second run on a probe reported 1000 Mbit/s sent, 17.7 received and
still 0.00 % loss.

The time budget grows along automatically; only if you set
`--timeout-seconds` yourself and it is too small does the sensor reject
the combination instead of aborting mid-run.

**Without a target rate the volume follows from the path** — the faster it
is, the more. Measured on a probe: about 800 Mbit/s down and 98 up come to
roughly **560 MB per run**. For this mode of operation,
`--measure-every-minutes` is the actual control; once a day in the
off-hours is the normal case.

**It adds up at the endpoint.** Thirty sites at 30/10 Mbit/s hourly are
about 1.5 GB per hour. That is rarely a problem, but it should surprise
nobody.

**One test at a time.** iperf3 accepts only one client; a second gets
`the server is busy running a test`. The sensor handles that cleanly: it
serves the last stored result and reports `busy` in the **Failure Code**
channel — no alarm, because a busy endpoint says nothing about the line.
For larger fleets it pays to run several instances on several ports.

**Minimum interval.** As with the `internet-speed` sensor,
`--measure-every-minutes` (default 60) serves the stored result again
instead of measuring anew. The **Result Age** channel shows how old it is.

## Parameters

| Parameter | Meaning |
| --- | --- |
| `--server HOST` | **Required.** The iperf3 endpoint |
| `--port PORT` | port of the endpoint, default 5201 |
| `--download-mbit MBIT` | download target in Mbit/s. Without any target rate the capacity is measured |
| `--upload-mbit MBIT` | upload target in Mbit/s |
| `--udp` | measure with UDP: loss and jitter instead of retransmits and latency. **Needs a target rate** |
| `--username NAME` | user name for the endpoint. Without it the sensor measures unauthenticated |
| `--password SECRET` | password in the sensor instead of on the probe, see [credentials](#credentials) |
| `--public-key PATH` | public key as a file, if it is not in the profile |
| `--profile NAME` | name of the deployed profile, default `default` |
| `--seconds SECONDS` | transfer duration per direction, default 5, allowed 2 to 60 |
| `--measure-every-minutes MINUTES` | minimum interval between real measurements, default 60, `0` disables it |
| `--timeout-seconds SECONDS` | time budget for the whole run, default 60 |
| `--self-check` | check the ability to run, without measuring |

At least one of the two target rates is needed for an assurance; the one
given selects its direction at the same time. Whoever sets only
`--download-mbit` measures only the download.

### Examples

The normal case at a site on a 100/40 line:

```text
--server iperf.example.com --username prtg-probe --download-mbit 30 --upload-mbit 10
```

The stricter check with loss measurement:

```text
--server iperf.example.com --username prtg-probe --udp --download-mbit 30 --upload-mbit 10
```

An endpoint without authentication, protected only by the firewall:

```text
--server 10.0.0.5 --download-mbit 30 --upload-mbit 10
```

## Channels

| ID | Channel | Unit | When |
| --- | --- | --- | --- |
| 10 | Test Result | 1 = measured, 2 = failed | always |
| 11 | Download | kbit/s | with `--download-mbit` |
| 12 | Upload | kbit/s | with `--upload-mbit` |
| 13 | Ping | ms | TCP only, upload direction only |
| 14 | Jitter Download | ms | UDP only |
| 15 | Jitter Upload | ms | UDP only |
| 16 | Result Age | s, 0 = just measured | always |
| 17 | Test Duration | ms | always |
| 18 | Failure Code | see below | always |
| 20 | Target Met | 1 = target held, 2 = missed | only with a target rate |
| 21 | Packet Loss Download | % | UDP only |
| 22 | Packet Loss Upload | % | UDP only |
| 23 | Retransmits Download | count | TCP only |
| 24 | Retransmits Upload | count | TCP only |

**The primary channel is Target Met** — provided a target rate is set. The
channel alarms on its own, no limit needs entering. To alarm per
direction, additionally set a lower limit on **Download** or **Upload**.

**Without a target rate this channel is absent**, and the alarm hangs on a
lower limit on **Download**. A "yes" without a target would carry no
statement, and a channel that always reads 1 would be worse than none.

**Ping exists only in the upload direction**, and that is no gap: the
round-trip time is known to the sending side. In the download direction
the endpoint sends, and its kernel view is not available to the sensor.
The value comes from `tcp_info` and is thereby a real round-trip time —
not the duration of an HTTP operation, as other methods report.

**Retransmits are the early indicator.** A line silting up shows
retransmissions long before the throughput falls below the target rate.

### Failure codes

| Code | Channel 18 | Meaning |
| --- | --- | --- |
| `ok` | 0 | measurement ran |
| `tool-missing` | 1 | iperf3 is missing on the probe |
| `credentials-unreadable` | 2 | password file or key missing, or permissions too wide |
| `server-unreachable` | 3 | the endpoint does not answer |
| `auth-failed` | 4 | user name, password or clock do not match |
| `busy` | 5 | the endpoint is running another test |
| `timeout` | 6 | time budget used up |
| `test-failed` | 7 | iperf3 aborted for another reason |

`tool-missing` and `credentials-unreadable` are reported as **sensor
errors**, not measurements: a missing tool says nothing about the line.
Everything else remains a successful output with a negative finding, so
the channel history stays readable across an outage.

**A missed target rate is not a failure code.** Channel 10 stays at 1,
channel 18 at 0, and only channel 20 reads 2.

## Create the sensor in PRTG

1. Add a **Script v2 sensor** on the probe's device.
2. Select `iperf-throughput.py` as the script.
3. Enter the parameters, see above. **Without `--server` the sensor does
   not run.**
4. Set the scanning interval to 5 minutes. Measurement still only happens
   every `--measure-every-minutes` minutes.
5. Set the timeout to at least `--timeout-seconds` plus 20 seconds, so 80
   seconds with the default.

**The parameter list is available inside PRTG itself.** Entering `--help`
in the parameter field returns it as the sensor message — including
defaults, an example line and the reference to this file. It is the only
place to find it without access to the probe. A sensor without parameters
points there as well.

## Check without PRTG

On the probe, as root. The invocation reproduces how the MPP service
starts the script — as the service user and with its hardening:

```bash
echo '--self-check --server iperf.example.com --username prtg-probe --download-mbit 30' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes -- /opt/paessler/share/scripts/iperf-throughput.py
```

Force a real measurement, past the minimum interval:

```bash
echo '--server iperf.example.com --username prtg-probe --download-mbit 30 --upload-mbit 10 --measure-every-minutes 0' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" -- /opt/paessler/share/scripts/iperf-throughput.py
```

If the sign-in fails, check the clock first — not the password.

## Limits

**The test saturates up to the target rate.** iperf3 takes the requested
rate without restraint. In return the volume is exactly predictable, and
the measurement comes from a tool proven over years rather than from
custom code.

**The endpoint is a dependency.** If it fails, every site reports
`server-unreachable` — and in PRTG that looks like thirty disrupted
lines. Monitor it.

**One test at a time**, see above.

**Your path is measured, not the internet.** For the question of whether
the provider delivers the contracted line, the
[`internet-speed`](../internet-speed/README.md) sensor stays responsible.
