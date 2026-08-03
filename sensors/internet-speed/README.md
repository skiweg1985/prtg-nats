# Sensor `internet-speed` — measure the throughput of the uplink

The sensor measures download, upload and latency of a probe against
speedtest.net and reports the values to PRTG. It answers how well a site is
actually connected — not just whether it is reachable. A line that drops
from 100 to 8 Mbit/s counts as available to every ping check and is
practically unusable.

The sensor answers **one** question: does the provider deliver the
contracted line? That is the number you hold up to a provider. It
**saturates the line doing so**, for about twenty seconds per run.

The other question — does the uplink hold what the site needs for its
work — is answered by the
[`iperf-throughput`](../iperf-throughput/README.md) sensor:

| Sensor | Measures against | Answers |
| --- | --- | --- |
| `internet-speed` | speedtest.net | Does the provider deliver the contracted line? |
| `iperf-throughput` | your own measurement endpoint | Can the site do its work? |

`--mode maximum` is still **required**, although it is the only value: a
sensor someone creates without parameters would otherwise flood a site's
line every hour, and nobody would notice until the site calls.

Measurement uses [speedtest-cli](https://pypi.org/project/speedtest-cli/).
Deployment installs it itself: since the sensor ships a `requirements.txt`,
the probe creates a dedicated virtual environment under
`/var/lib/prtg-nats-sensors/venv/internet-speed` and points the installed
script's shebang at it.

## Prerequisites

- **Access to a package index** from the probe. Without it, deployment
  fails at `pip install`. In isolated networks, point `/etc/pip.conf` on
  the probe at the internal mirror.
- **`python3-venv`** on the probe. Deployment takes care of that itself: if
  the package is missing, the probe installs it. To run that step up front
  and check it in isolation:

  ```bash
  ./prtg-nats sensor prepare mpp-probe-01
  ```

- Unrestricted access to speedtest.net over HTTPS. A forced proxy on the
  network is not supported.

Probes enrolled before sensor management existed answer both with
`Unsupported management request`. They need a one-time
`./prtg-nats probe enroll USER ADMIN@HOST --reenroll`.

The sensor needs **no** root privileges and no privileged helper. It runs
entirely as the probe's service user.

## Set up

```bash
./prtg-nats sensor deploy internet-speed mpp-probe-01
```

Deployment only counts as successful once the probe has created the virtual
environment and the self-test has passed. If either fails, the probe
restores the previous state.

To every probe at once:

```bash
./prtg-nats sensor deploy internet-speed --all
```

Removal is `./prtg-nats sensor remove internet-speed mpp-probe-01`; that
also removes the virtual environment.

## Mode `minimum` has moved out

It checked whether the line holds a target rate without saturating it — and
measured against speedtest.net for that. There, the server selection decides
which path is measured, and it changes between runs. The pacing had to be
built by the script itself, because a third-party test server cannot be
throttled: about four hundred lines, which most recently hid a silently
capped socket buffer.

[`iperf-throughput`](../iperf-throughput/README.md) asks the same question
against a self-operated endpoint. It always sits in the same place, and
`iperf3` does the pacing with one switch.

**An existing sensor in mode `minimum` no longer measures.** It reports a
configuration error that points at the successor. The same holds for
`--min-download-mbit` and `--min-upload-mbit`. Create an
`iperf-throughput` sensor and remove the old one — deliberately as a new
sensor, so the history stays cleanly separated.

## Data volume and load

The volume follows from the throughput — the faster the line, the more:

| Uplink | per direction | per run |
| --- | --- | --- |
| 50 Mbit/s | 60 MB | 120 MB |
| 100 Mbit/s | 125 MB | 250 MB |
| 500 Mbit/s | 600 MB | 1.2 GB |

**Minimum interval.** The script stores every result and serves it again
within `--measure-every-minutes` instead of measuring anew. The default is
60 minutes. The **Result Age** channel shows how old the served value is.
Because every run saturates the line, this sensor belongs on a large
interval and in the off-hours — once a day is the normal case.

**It never measures at the same time as `iperf-throughput`.** All sensors
that measure throughput share the same lock file on a probe. Otherwise this
one would saturate the line while the other checks its target rate, and the
alarm would fire over a perfectly healthy line.

**Time budget.** `--timeout-seconds` is a hard ceiling, default 120
seconds. When it runs out, the sensor aborts through an alarm signal and
reports `timeout`. That works even when speedtest-cli is stuck in one of
its worker threads — without this backstop the sensor would hang until PRTG
cuts it off, and deliver no result at all.

## Parameters

For a Script v2 sensor they go into the **Parameters** field. The normal
case needs one to three of them.

| Parameter | Meaning |
| --- | --- |
| `--mode maximum` | **Required.** The only value; without it the sensor does not run |
| `--measure-every-minutes MINUTES` | minimum interval between real measurements, default 60, `0` disables it |
| `--timeout-seconds SECONDS` | time budget for the whole run, default 120 |
| `--server ID` | pin the test server instead of choosing automatically |
| `--source IP` | source address, if the probe has several ways out |
| `--no-secure` | HTTP instead of HTTPS towards speedtest.net |
| `--self-check` | check the ability to run, without measuring |

The parser still knows `--min-download-mbit` and `--min-upload-mbit`, but
only to point an existing sensor at
[`iperf-throughput`](../iperf-throughput/README.md). Nothing is measured
with them here any more.

### Examples

The normal case — once a day, in the off-hours:

```text
--mode maximum --measure-every-minutes 1440
```

For an acceptance test or troubleshooting, measuring on every scan:

```text
--mode maximum --measure-every-minutes 0
```

A fixed test server, so the readings stay comparable over time:

```text
--mode maximum --measure-every-minutes 1440 --server 12345
```

An automatic server selection can switch between two runs; the values are
then no longer readily comparable.

**Take the ID from the sensor message**, not from a list. The sensor writes
it into every message — "… via NovoServe (Amsterdam), server 63250" — and
that is the server it chose itself. One run is therefore enough to pin the
one it takes anyway.

That is not a convenience but the reliable way. speedtest.net maintains
**two different directories** of ten servers each: one over HTTP, one over
HTTPS — measured without a single shared ID. The sensor asks over HTTPS,
`speedtest-cli --list` over HTTP — and the two are not equally good.
Measured simultaneously on a probe in Amsterdam, stable over eight rounds:

| Directory | the five nearest servers |
| --- | --- |
| HTTPS, which the sensor uses | five times 15 km, 12–15 ms |
| HTTP, which `--list` shows | 378 to 487 km, 23–255 ms |

Whoever reads an ID off `--list` therefore quite likely pins a server the
sensor would never have taken on its own. A badly chosen test server is a
particularly unpleasant source of error: it looks like a slow line.

This is compounded by the sensor remembering the address of a server it
once found and still reaching it when speedtest.net no longer offers it.
That keeps a pinned server measurable for weeks — but it equally keeps a
bad choice alive. A pin therefore wants to be set right once.

If you need the list anyway, say to hit a specific operator, it is
available on the probe:

```bash
/var/lib/prtg-nats-sensors/venv/internet-speed/bin/speedtest-cli --list
```

The full path is necessary: speedtest-cli lives in the sensor's virtual
environment and is not on the PATH. The output is sorted by distance; the
number before the bracket is the ID for `--server`:

```text
59168) TIM SpA (Bergamo, Italy) [343.26 km]
23925) Planetel (Bergamo, Italy) [343.26 km]
```

An ID found that way is usable — if it is not in the HTTPS directory, the
sensor checks the HTTP directory. **But check the distance**, and then the
first sensor message: it says what the pin actually did.

The offered set also changes over time. Yesterday's ID can be in neither of
the two directories today although the server runs flawlessly; that is what
the memory just described is for.

### Recommended pattern: two sensors

This sensor alone answers only half the question. The usual pair on one
device:

| Sensor | Parameters | Purpose |
| --- | --- | --- |
| capacity | `internet-speed --mode maximum --measure-every-minutes 1440` | trend curve against speedtest.net, once a day |
| assurance | `iperf-throughput --server … --download-mbit 30 --upload-mbit 10` | alarm against your own endpoint, hourly |

The capacity sensor gets a PRTG schedule for the off-hours — it saturates
the line. The two never measure at the same time: all sensors that measure
throughput share the same lock file on a probe.

## Channels

| ID | Channel | Unit |
| --- | --- | --- |
| 10 | Test Result | 1 = measured, 2 = failed |
| 11 | Download | kbit/s |
| 12 | Upload | kbit/s |
| 13 | Ping | ms, at idle |
| 14 | Jitter | ms |
| 15 | Same Server | 1 = same as before, 0 = switched |
| 16 | Result Age | s, 0 = just measured |
| 17 | Test Duration | ms |
| 18 | Failure Code | see the table below |

**The primary channel is Download**, with a lower limit. To alarm per
direction, additionally set one on **Upload**.

**Same Server** explains jumps in the throughput curve. When the channel
reads 0, the automatic server selection switched and the values are no
longer readily comparable with the ones before. With `--server` set it
stays at 1.

It deliberately uses a different lookup than the alarm channels: in the
usual `…yesno.stateyesok`, "No" is an *Error*, and the sensor would go red
just because speedtest.net offered a different server. A server change is
context, though, not a failure. It therefore uses
`…exchangedag.yesno.allstatesok`, where both values are *OK* — hence also
the counting of 0 and 1 instead of 1 and 2 here.

**Result Age** deserves its own look: if the value stays above
`--measure-every-minutes` permanently, the sensor no longer measures but
keeps serving values from the cache.

Channels for which no value exists are absent from the output.

### Failure codes

| Code | Channel 18 | Meaning |
| --- | --- | --- |
| `ok` | 0 | measurement ran |
| `module-missing` | 1 | speedtest-cli is missing — deploy the sensor again |
| `config-unreachable` | 2 | speedtest.net does not answer |
| `no-servers` | 3 | no usable test server found |
| `download-failed` | 4 | download aborted |
| `upload-failed` | 5 | upload aborted |
| `timeout` | 6 | time budget used up |
| `busy` | 7 | another run is measuring right now |
| `bind-failed` | 8 | the address from `--source` no longer exists |

The values 1 and 2 instead of 1 and 0 are no typo: the PRTG standard lookup
comes from the SNMP world, where 1 means true and 2 false. It does not know
a 0 — that would appear as an "undefined lookup value" and trigger only a
warning where an error has to stand.

`module-missing` is reported as a sensor error, not a measurement: a
missing tool says nothing about the line. The same holds for unreadable
parameters.

## Create the sensor in PRTG

1. Add a **Script v2 sensor** on the probe's device.
2. Select `internet-speed.py` as the script.
3. Enter the parameters, see above. **Without `--mode` the sensor does not
   run.**
4. Set the scanning interval to 5 minutes. Measurement still only happens
   every `--measure-every-minutes` minutes; short intervals merely keep the
   sensor status current.
5. Set the timeout to at least `--timeout-seconds` plus 20 seconds, so 140
   seconds with the default.

**The parameter list is available inside PRTG itself.** Entering `--help`
in the parameter field returns it as the sensor message — including
defaults, an example line and the reference to this file. It is the only
place to find it without access to the probe. A sensor without `--mode`
points there as well.

If several sensors on one probe measure throughput, they measure one after
another — **across sensor kinds too**, so together with
[`iperf-throughput`](../iperf-throughput/README.md). A shared lock file
prevents two runs from slowing each other down and both reading too low.
The waiting run serves the last stored result.

**Do not switch the mode of an existing sensor.** Channel limits lose their
meaning in the process — an "alarm below 50 Mbit/s" set for `maximum` fires
permanently after switching to a target rate of 30. In doubt, create a new
sensor.

## Check without PRTG

On the probe, as root. The invocation reproduces how the MPP service starts
the script: as the service user and with its hardening.

Check only the ability to run — the same thing deployment does after
installation, and without any network traffic:

```bash
echo '--self-check' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes -- /opt/paessler/share/scripts/internet-speed.py
```

If parameters come along, the self-test checks them too — likewise without
network traffic. That way a configuration can be checked **before** it is
entered in PRTG, where nobody checks it any more:

```bash
echo '--self-check --mode maximum --server 12345' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes -- /opt/paessler/share/scripts/internet-speed.py
```

Force a real measurement, past the minimum interval:

```bash
echo '--mode maximum --measure-every-minutes 0' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" -- /opt/paessler/share/scripts/internet-speed.py
```

A second call without `--measure-every-minutes 0` has to serve the stored
result: **Result Age** then reads above 0 and no new measurement runs.

Is the virtual environment present?

```bash
ls -l /var/lib/prtg-nats-sensors/venv/internet-speed/bin/python3
```

## How the measurement runs

1. The parameters are checked before a single byte flows. A mistake leads
   to a sensor error with the line that has to stand there instead.
2. If a result younger than `--measure-every-minutes` exists, it is
   served. No measurement takes place.
3. Otherwise a lock is taken. If another run is already measuring, this run
   serves the last stored result instead of waiting.
4. speedtest-cli fetches its configuration and chooses the test server —
   the nearest, or the one pinned with `--server`.
5. Download and upload measure the peak. Five short latency samples for the
   jitter follow.
6. The result is stored and printed.

The cache lives under `/tmp`, with the mode in its name: while a fleet is
being migrated, results from both states sit there for a while; without the
marker a sensor would be slipped one that no longer exists in that form.
Through `PrivateTmp` the directory belongs to the MPP service alone; the
script checks owner and permissions anyway and discards a file that
deviates. A service restart discards the cache — the consequence is one
extra measurement, nothing more.

## Limits

speedtest-cli has been unchanged since 2021. Beyond roughly 500 Mbit/s it
tends to measure low, because it uses the older speedtest.net interface.
For the question of whether an uplink degrades, that is irrelevant — for an
acceptance measurement against an assured value it is not suitable.

**The sensor measures the latency itself.** speedtest-cli no longer can: it
fetches `latency.txt` with raw `http.client` and treats everything but a
200 answer as a failure. The test servers meanwhile answer with a redirect
to their own host name, which `http.client` does not follow — out come
three placeholders and, from them, the number **1,800,000 ms**. If you see
it in a direct `speedtest-cli` run, you do not have a line problem:
download and upload run flawlessly there, because they go through urllib
and follow the redirect. The sensor discards such values and measures
itself instead.

The speedtest-cli version is pinned in [requirements.txt](requirements.txt).
