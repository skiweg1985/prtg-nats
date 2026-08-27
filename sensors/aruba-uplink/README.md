# Sensor `aruba-uplink` — which uplink the site is actually on

The sensor asks an Aruba gateway about its uplinks and reports which of
them stands, which one carries the traffic and how well each of them
performs. It answers the question the other sensors of this repository
cannot: not *how good* the line is, but *which* line it is.

At a site with an LTE backup that is the expensive gap. The gateway
switches over quietly, `link-quality` and `internet-speed` keep reporting
green — the line does carry, after all — and the site runs on mobile data
until the bill arrives. The other direction is just as quiet: the backup
deregisters, nobody notices, and the next real outage finds nothing to fail
over to. Channel *Backup Uplink Up* exists for exactly that second case.

The gateway measures the path quality of every uplink by itself, against
its own health-check targets. The sensor takes those numbers instead of
producing traffic of its own — including the R value of the E-model per
ITU-T G.107, the same measure that
[link-quality](../link-quality/README.md) computes from the probe. Only
here it comes separately per uplink, which from the probe is impossible in
principle: a packet leaving the site takes whichever path the gateway
picks.

## Prerequisites

- An **Aruba gateway with AOS-10** whose management interface answers on
  port 443. Developed and verified against a 9004-LTE with 10.7.1.0.
- A **read-only account** on the gateway. The sensor never writes, and an
  account that cannot write is the one thing that limits the damage if the
  credentials ever leak — all the more since the same account usually opens
  every gateway of the fleet.
- The probe reaches the gateway over HTTPS.

No PyPI dependency, no virtual environment, no credential profile on the
probe, no privileged helper — the sensor gets by with the standard library
and runs as the service user.

> Deliberately over HTTPS and not over SSH: Python ships no SSH client, so
> that route would mean `paramiko` in a virtual environment on every probe,
> or `sshpass` with the password in the process list. The management API
> needs neither and answers in JSON instead of screen output.

## Set up

Roll the sensor out to a probe:

```bash
./prtg-nats sensor deploy aruba-uplink USER
```

That is all the probe needs. Everything site-specific lives in PRTG.

## Credentials

Gateway and credentials are sensor parameters, the way the Script v2
examples bundled with the probe do it. In PRTG they are not typed out but
written as **placeholders**, so nothing site-specific has to be entered
anywhere:

```text
--host %host --user %scriptplaceholder1 --password %scriptplaceholder2
```

| Placeholder | Where it comes from |
| --- | --- |
| `%host` | address of the device the sensor sits on |
| `%scriptplaceholder1` … `5` | *Credentials for Script Sensors* in the device settings |

Set the account once on the group that holds the gateways; every device
below inherits it, and every new site works without touching the
credentials again. PRTG keeps the values out of the sensor log and the
sensor settings.

For that promise to hold, a script must never print such a value. This one
masks the password in every message it produces, including the ones
argparse writes about a mistyped parameter — that is the single place where
a secret could otherwise escape into a notification.

The parameters can of course be written out literally as well, which is
useful for a test from the command line. Then the password does stand in
the sensor configuration.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `--host` | — | required, address of the gateway |
| `--user` | — | required, the read-only account |
| `--password` | — | required, its password |
| `--primary` | `wired` | which uplink kind is the main path: `wired` or `cellular` |
| `--backup` | `cellular` | the alternative path: `cellular`, `wired` or `none` |
| `--backup-share` | `25` | percentage of traffic on the backup from which it counts as moved over |
| `--timeout-ms` | `10000` | milliseconds to wait for a single answer |
| `--self-check` | — | only verify the ability to run |

Which uplink is the primary one comes from **this configuration, not from
the device**. A gateway that load-balances reports no uplink as a backup at
all — it counts `g_numBkpUplinks: 0` while happily using both — so only the
parameters can say what the main path is meant to be.

### Examples

A site on a fixed line with LTE backup needs nothing beyond the
credentials:

```text
--host %host --user %scriptplaceholder1 --password %scriptplaceholder2
```

A site without an alternative path. Without this the sensor would report a
permanent alarm for a backup the site does not have:

```text
--host %host --user %scriptplaceholder1 --password %scriptplaceholder2 --backup none
```

A site that has only mobile data:

```text
--host %host --user %scriptplaceholder1 --password %scriptplaceholder2 --primary cellular --backup none
```

A site where LTE is the main path and the fixed line the fallback:

```text
--host %host --user %scriptplaceholder1 --password %scriptplaceholder2 --primary cellular --backup wired
```

## Channels

| Channel | Meaning |
| --- | --- |
| Test Result | the gateway answered and delivered usable data |
| Uplinks Connected | how many uplinks stand |
| Primary Uplink Up | the kind named by `--primary` is `Connected` and `Reachable` |
| Backup Uplink Up | the alternative path is ready for use |
| Traffic on Backup | share of the backup in the overall traffic |
| Primary Bandwidth Utilisation | utilisation of the main path |
| Uplink Changes 24h | changeovers within the last 24 hours |
| Test Duration | how long the run took |
| Failure Code | see below |
| LTE RSRP | received power of the mobile link |
| On Primary Uplink | the main path carries the traffic |
| LTE SINR | signal-to-noise ratio; says more about usability than the level |
| LTE Data Usage | volume in the billing period |
| Primary Latency / Jitter / Packet Loss / Quality | path measurement of the main path |
| Backup Latency / Jitter / Packet Loss / Quality | the same for the alternative path |

**On Primary Uplink** is the channel that carries the actual alarm. It
decides on a threshold, not on equality: a gateway that load-balances puts
a little traffic on the backup permanently, and comparing against zero
would alarm forever at such a site. Without a backup the channel simply
reports whether the primary stands and carries.

Which channels exist follows the **configuration**, never the measurement.
The backup channels are absent with `--backup none`, the radio channels
when neither role is `cellular`. Everything else is always there, a dead
uplink included — it then reports 0 and an alarm. A channel that disappears
during an outage and returns afterwards tears its history apart in PRTG.

### Failure codes

| Code | Value | Meaning |
| --- | --- | --- |
| `ok` | 0 | the measurement ran |
| `bad-request` | 1 | the parameters do not match the device |
| `login-failed` | 2 | the gateway refused the credentials |
| `gateway-unreachable` | 3 | no answer within the budget |
| `bad-answer` | 4 | the answer was not usable |
| `no-quality-data` | 5 | no path measurement; the quality channels stay at 0 |
| `internal-error` | 6 | unexpected failure |

Only `bad-request` becomes a **sensor error**: it says something about how
the sensor was set up. Everything else stays a valid measurement with the
alarm on the *Test Result* channel — a gateway that stopped answering is
not a broken sensor, it is the incident this sensor exists for.

`no-quality-data` is the one exception that keeps *Test Result* green. The
path measurement is a secondary one; losing it leaves every uplink channel
intact and saying what it says.

## Create the sensor in PRTG

Create a device for the gateway with its address, then add a **Script v2**
sensor on it, pick `aruba-uplink.py` as the script and enter the parameters
from above. `%host` then resolves to that device's address.

An interval of five minutes fits: the values the sensor reads are averages
the gateway keeps updating anyway, and every run costs the device a login.

## Check without PRTG

On the probe, as root. The invocation reproduces how the MPP service starts
the script: as the service user and with its hardening. That is essential —
from a root shell the call succeeds even when it fails at exactly those
limits in the real service context. Placeholders do not work here, so the
credentials are written out:

Check only the ability to run, without any network traffic:

```bash
echo '--host 192.0.2.1 --user monitoring --password SECRET --self-check' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" -- /opt/paessler/share/scripts/aruba-uplink.py
```

The self-test checks the parameters but does not reach out to the gateway.
Activating a sensor must not depend on a device on the other side of the
site; whether the credentials work shows on the first real run. Without
parameters it passes as well - that is the form the deployment uses, long
before a gateway is entered in PRTG.

A real measurement:

```bash
echo '--host 192.0.2.1 --user monitoring --password SECRET' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" -- /opt/paessler/share/scripts/aruba-uplink.py
```

## How the measurement runs

One run opens a single TLS connection and sends five requests over it:

| Step | Command | What comes back |
| --- | --- | --- |
| 1 | `POST /v1/api/login` | the session token |
| 2 | `show uplink` | state, reachability and utilisation per uplink |
| 3 | `show uplink stats` | byte rates per uplink |
| 4 | `show uplink cellular details` | RSRP, SINR, data usage |
| 5 | `show uplink debug` | the gateway's own path measurement |
| 6 | `GET /v1/api/logout` | the session is handed back |

Step 4 runs only when a `cellular` uplink is configured. Of that answer the
sensor reads **only** RSRP, SINR and data usage. The same block carries
IMEI, IMSI, cell ID and GPS position; those are device and location
identifiers and are deliberately not read, so they cannot end up in a
channel, a message or a log.

**The logout is not optional.** A gateway holds a limited number of
sessions. A sensor running every few minutes and leaving them behind locks
everyone out of the device within a day — including the people who would
have to fix it. It therefore happens in a `finally`, even when the
measurement failed.

The path measurement in step 5 comes from `show uplink debug`, where the
gateway keeps the result of its health check. A `link:` line opens a block
and names the kind, the `probe ip:` lines below belong to it. The gateway
probes several targets per uplink; the sensor averages over them, because a
single target says as much about a path as a single ping does.

Between runs the sensor keeps the three yes/no statements in
`/tmp/prtg-sensor-aruba-uplink-<uid>.json` and counts how often they
changed. A single changeover is an event and needs no alarm; twenty of them
are a defect nobody would spot in the individual channels.

## Limits

- **The gateway certificate is not verified.** It is self-signed, so there
  is no authority to check it against, and pinning a fingerprint would mean
  carrying one per site — which is exactly the per-site configuration this
  sensor is meant to avoid. The connection is encrypted, but a device that
  answers on that address instead of the gateway would be handed the
  credentials. That is the trade; a read-only account is what keeps its
  cost bounded.
- **`show uplink debug` is not documented API surface.** Should a future
  AOS release change its wording, the path channels stay at 0 and the
  sensor reports `no-quality-data`. Everything else keeps working — a
  sensor failing entirely over a secondary measurement would be the worse
  answer.
- The path measurement is the gateway's, against **its own** targets. It
  says how the uplinks compare, not what a specific service at the far end
  experiences. For that, `link-quality` measures against targets you pick.
- One sensor addresses one gateway. A site with two gateways needs two
  sensors.
- The state file lives in `/tmp` and does not survive a reboot of the
  probe. The changeover count then starts at 0 again — measured values are
  unaffected, they all come from the gateway.
- `Data usage` is what the gateway counts, not what the provider bills.
  Without `Data limit` and `Data billing date` set on the device, the
  number keeps running without a reference period.
