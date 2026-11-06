# Sensor `wlan-auth` — check Wi-Fi sign-in

The sensor signs a probe in to a Wi-Fi network and measures how long the
individual stages take. It answers whether a device at a site can actually
sign in — not just whether an access point answers.

Supported are **WPA2-PSK**, **PEAP/MSCHAPv2** and **EAP-TLS**.

After every run the sensor disconnects again and powers the test interface
down. The DHCP stage runs in test mode: the complete DHCP exchange is
measured, but the offered address is not put on the interface.

## Prerequisites

- A **dedicated Wi-Fi interface** for tests. The probe has to be on the
  network by another route, usually Ethernet.
- `wpa_supplicant` on the probe, and a DHCP client for the DHCP stage.
  The rollout asks for `busybox`, whose `udhcpc` carries it out; it is on
  every Raspberry Pi OS, so normally nothing is installed. `dhcpcd` still
  works where it is present and is used when busybox is not — but not on
  a 32-bit userland under a 64-bit kernel, the ordinary shape of Raspberry
  Pi OS armhf on 64-bit hardware. There its seccomp filter does not match
  the processes it guards, and the kernel kills the client with SIGSYS
  before it sends a packet. The sensor reports that as `dhcp-client-broken`
  rather than blaming the DHCP server.
- The radio must not be blocked. A probe that has never used Wi-Fi ships
  with it soft-blocked; the sensor lifts that itself, because the
  interface was reserved for exactly this. A block set in hardware stays,
  and is reported as `iface-blocked`.
- The test interface is reserved (see below). Without a reservation the
  sensor refuses every run.

Without a running helper service the sensor reports a sensor error pointing
at `prtg-sensor-wlan-auth.socket`.

The sensor refuses the test on an interface that carries a default route or
is not a radio interface. A mistyped parameter therefore cannot cut the
probe off from its NATS server.

## Set up

```bash
./prtg-nats sensor deploy wlan-auth mpp-probe-01
./prtg-nats sensor reserve wlan-auth mpp-probe-01 wlan0
```

Or from the web interface: the sensor tab of a probe lists its radio
interfaces with what each one is doing, and reserves the chosen one with a
button. That way the decision is made against what the probe reports rather
than against a name typed from memory.

Reserving takes `wlan0` out of NetworkManager permanently, removes its
address and route and powers the interface down. **An existing Wi-Fi
connection over this interface is lost in the process** — that is intended:
a test interface should do nothing outside the tests, and it is why
reserving is a separate decision rather than a side effect of naming the
interface in the sensor parameters.

`./prtg-nats sensor remove wlan-auth mpp-probe-01` undoes it.

## Parameters

For a Script v2 sensor they go into the **Parameters** field.

| Parameter | Meaning |
| --- | --- |
| `--interface NAME` | test interface, default `wlan0` |
| `--ssid NAME` | network to check |
| `--auth psk\|peap\|eap-tls` | method |
| `--profile NAME` | credentials from a profile on the probe |
| `--psk PASSPHRASE` | WPA2 passphrase |
| `--identity NAME` | user name for enterprise methods |
| `--password PASSWORD` | password for PEAP |
| `--anonymous-identity NAME` | outer identity for the tunnel |
| `--ca-cert FILE` | CA that issued the RADIUS server certificate |
| `--domain-suffix-match SUFFIX` | required name suffix of the server certificate |
| `--no-verify-server` | do not verify the server certificate |
| `--client-cert FILE` | client certificate for EAP-TLS |
| `--private-key FILE` | private key for EAP-TLS |
| `--private-key-passwd PASSWORD` | passphrase of the key |
| `--bssid AA:BB:CC:DD:EE:FF` | test against a specific access point |
| `--hidden` | the SSID is not broadcast |
| `--stage assoc\|dhcp` | stop after sign-in, or measure through DHCP |
| `--timeout SECONDS` | time budget for the whole test, default 45 |
| `--max-age SECONDS` | oldest measurement this sensor accepts, default 300 |
| `--measure-now` | measure on the spot instead of collecting (diagnosis) |

### Examples

WPA2-PSK:

```text
--interface wlan0 --ssid Guestnet --auth psk --psk secretpassphrase
```

PEAP/MSCHAPv2 with a verified server certificate:

```text
--interface wlan0 --ssid Corporate --auth peap --identity monitor
--password secret --ca-cert /etc/prtg-nats/sensors/wlan-auth/certs/radius-ca.pem
--domain-suffix-match example.com
```

EAP-TLS with a client certificate:

```text
--interface wlan0 --ssid Corporate --auth eap-tls --identity probe-01
--client-cert /etc/prtg-nats/sensors/wlan-auth/certs/probe.pem
--private-key /etc/prtg-nats/sensors/wlan-auth/certs/probe-key.pem
--ca-cert /etc/prtg-nats/sensors/wlan-auth/certs/radius-ca.pem
```

With a profile — no password appears in PRTG:

```text
--interface wlan0 --profile office
```

## Credentials as a profile

A profile is a file of `KEY=VALUE` lines. Use
[example.env.template](profiles/example.env.template) as the template.
Allowed are `AUTH`, `SSID`, `PSK`, `IDENTITY`, `PASSWORD`,
`ANONYMOUS_IDENTITY`, `CA_CERT`, `DOMAIN_SUFFIX_MATCH`, `CLIENT_CERT`,
`PRIVATE_KEY`, `PRIVATE_KEY_PASSWD` and `BSSID`.

```bash
cp sensors/wlan-auth/profiles/example.env.template office.env
# enter the values
./prtg-nats sensor profile wlan-auth mpp-probe-01 office --from-file office.env
```

Parameters given directly on the sensor take precedence over the profile's
values.

This repository does **not** deploy client certificates or the RADIUS CA;
they are placed on the probe, sensibly under
`/etc/prtg-nats/sensors/wlan-auth/certs/`. The sensor only names their
paths.

## Channels

| ID | Channel | Unit |
| --- | --- | --- |
| 10 | Auth Result | 1 = succeeded, 2 = failed, 3 = nothing measured |
| 11 | Total Time | ms |
| 12 | Association Time | ms |
| 13 | Auth Time | ms |
| 14 | DHCP Time | ms, only with `--stage dhcp` |
| 15 | Signal | dBm |
| 16 | Frequency | MHz |
| 17 | Measurement Age | s |
| 18 | Failure Code | see the table below |

**Auth Result** works well as the primary channel; it can be set in the
sensor settings. A failed sign-in does not set the sensor to *Down*; it sets
this channel — that keeps the history of the timing channels readable. The
alarm is set up through a limit on the channel.

The third value is neither: `3` means nothing was measured, either because
the job was only just registered or because the last measurement is older
than `--max-age`. It uses the standard lookup
`prtg.standardlookups.netapp.nichealth`, whose `3` alarms on nothing — a
value that does not exist must not read as a failed sign-in, and must not
read as a successful one either.

> **A sensor that existed before this change needs its lookup switched by
> hand.** PRTG keeps the lookup a channel was created with, so an existing
> `Auth Result` stays on `prtg.standardlookups.yesno.stateyesok` and
> answers the new value with *"undefined lookup value (3)"* and a warning.
> In the channel settings of `Auth Result`, set the lookup to
> `prtg.standardlookups.netapp.nichealth`. The history survives it: `1` and
> `2` mean the same in both lookups. Sensors created afterwards get it
> right by themselves.

**Measurement Age** says how old the reported measurement is. It is the
channel that shows whether the radio keeps up with what the sensors ask of
it, and a limit on it is the alarm for that. It exists from the first scan,
reading zero until something has been measured.

If the sign-in fails, the association time is still kept. That makes it
possible to tell whether the radio link stood and only the authentication
was rejected.

### Failure codes

| Code | Channel 18 | Meaning |
| --- | --- | --- |
| `ok` | 0 | sign-in succeeded |
| `assoc-timeout` | 1 | no association within the time budget |
| `auth-failed-psk` | 2 | passphrase rejected |
| `auth-failed-eap` | 3 | the RADIUS server rejected the credentials |
| `server-cert-rejected` | 4 | server certificate not accepted |
| `auth-rejected` | 5 | the access point rejected the sign-in |
| `assoc-rejected` | 6 | the access point rejected the association |
| `ssid-not-found` | 7 | SSID not found in the scan |
| `dhcp-timeout` | 8 | no DHCP answer |
| `auth-timeout` | 9 | key negotiation not completed |
| `assoc-lost` | 10 | the link changed while DHCP was running |
| `not-measured` | 11 | registered, not measured yet |
| `result-stale` | 12 | the last measurement is older than `--max-age` |

The message line names the network, the access point behind it and the
address that was offered — in a list of sensors that is everything visible
without opening one:

```text
Authentication succeeded on "Office" via 1c:30:03:12:46:f3, DHCP offered 192.0.2.75 from 192.0.2.1
The access point rejected the association on "Office" (assoc-rejected (status 17))
```

The DHCP server is named alongside the address because the address alone
does not say which network answered. Two SSIDs that belong in different
VLANs are told apart by their server, and a lease from the wrong one
otherwise reads like a healthy result. Where a rejection carries an
802.11 status code, it is carried through as well: `17` means the access
point had no capacity left, `30` that it turned the station away for the
moment, `12` that it refused for a reason outside the standard — a MAC
filter or a client block, for instance.

The SSID comes from the link the probe actually joined, so it also names
the network behind a `--profile`, where the PRTG configuration holds only
the profile name. A run that never associated falls back to the SSID that
was asked for, and names none where a profile carried it.

If the test could not take place at all — missing interface, missing
privileges, invalid parameters — the sensor reports a sensor error instead,
with the cause in the message text. That separation is deliberate: such a
state says nothing about the Wi-Fi.

## Create the sensor in PRTG

1. Add a **Script v2 sensor** on the probe's device.
2. Select `wlan-auth.py` as the script.
3. Enter the parameters, see above.
4. Set the scanning interval to one minute. The sensor collects a stored
   result, so asking often costs the radio nothing.
5. Leave the timeout at its default. Collecting needs no patience — the
   script gives up after 30 seconds of its own.
6. Set `--max-age` to the freshness this network needs, and put a limit on
   the **Measurement Age** channel a little above it.

One sensor checks exactly one network. For several SSIDs, create several
sensors, on the same interface too: they no longer compete for the radio,
because one service measures for all of them. What they do compete for is
time — see [what the radio can deliver](#what-the-radio-can-deliver).

## Check without PRTG

On the probe, as root. The invocation reproduces how the MPP service starts
the script: as the service user and with its hardening. A plain
`sudo -u paessler_mpprobe` is **not** enough — it would have more privileges
than the service and mask failures.

The script collects what the service measured, so a hand-run invocation
needs `--measure-now` to use the radio. That switch is for diagnosis and
does not belong in a PRTG sensor, where it would bring back exactly the
competition for the radio that the service exists to prevent.

```bash
echo '--interface wlan0 --ssid Guestnet --auth psk --psk secretpassphrase --measure-now' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" --property=ProtectSystem=full -- /opt/paessler/share/scripts/wlan-auth.py
```

Check only the ability to run, without radio traffic — the same thing
deployment does after installation:

```bash
echo '--self-check' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes -- /opt/paessler/share/scripts/wlan-auth.py
```

Is the privileged service reachable, and is it running?

```bash
systemctl status prtg-sensor-wlan-auth.socket prtg-sensor-wlan-auth.service
```

The service runs permanently, unlike the helpers of the other sensors: it
schedules the radio, so it has to outlive the request that asked. A restart
empties its memory — the jobs and the credentials with them — and the next
scan of each sensor registers it again.

## How the test runs

The sensor script itself must not configure any Wi-Fi — and it does not
measure either. It asks for the last measurement of its network over
`/run/prtg-sensor-wlan-auth.sock`, where a systemd service with root
privileges answers and schedules the measurements itself. Passwords
therefore never appear in the process list. Why no sudo: see
[the sensor guide](../../docs/guides/deploy-sensors.md#how-a-sensor-gets-root-privileges).

Why the service decides when to measure and not the sensor: a probe has one
radio, and PRTG knows one scanning interval per sensor and nothing about the
other sensors on the same device. Two sensors would take the link from each
other, and the one that lost would report a failure about a network that is
perfectly healthy. The service is the only place that sees every job, so it
is the place where the order is decided. See
[ADR 0011](../../docs/architecture/decisions/0011-the-helper-schedules-the-radio.md).

A sensor registers by asking, and nothing else:

1. The first request registers the job and is answered with "not measured
   yet" — `Auth Result` reads 3, which alarms on nothing.
2. The service measures it as soon as the radio is free.
3. Every further request is answered with that measurement and its age. The
   service renews it before it reaches `--max-age`.
4. A job nobody asks for any more expires, after three times `--max-age` and
   at least five minutes. Deleting or pausing the sensor in PRTG therefore
   ends the measuring, and leaves nothing to clean up on the probe.

The credentials live in the memory of that service and nowhere else. They
are part of what identifies a job, so a changed password is a different job:
the old one stops being asked for and expires, instead of the radio going on
measuring with credentials nobody uses any more.

One measurement then runs like this:

1. The target interface is checked: present, a radio interface, reserved,
   without a default route. A lock file keeps parallel runs apart. Two
   sensors no longer meet there — the service measures for both — but a
   hand-run `--measure-now` does, and it waits for the radio for half of
   its own time budget, at most 15 seconds.
2. Behind the lock the interface is taken back: a supplicant or a `dhcpcd`
   left by a run that never finished is ended, stale work directories go,
   address and route are flushed. NetworkManager's own supplicant is left
   alone — it carries no configuration below `/run`, which is what tells
   the two apart.
3. A short-lived `wpa_supplicant` instance starts with a generated
   configuration under `/run` (mode `0600`).
4. Association and completed key negotiation are read off the event stream
   with timestamps, plus signal and frequency.
5. With `--stage dhcp`, a complete DHCP cycle follows in test mode.
6. Before the address is believed, the link is checked against the one
   that was measured — same cell, still connected. A lease collected after
   the link changed belongs to another association, so it is reported as
   `assoc-lost` rather than as an address.
7. Cleanup always happens: end the instance, power the interface down,
   delete generated files — even on timeout or abort.

### What the radio can deliver

The interval in PRTG no longer decides when the radio is used. It decides
how often a sensor asks, and asking is free. What a sensor needs is
`--max-age`, and whether the radio can hold that is arithmetic: a failing
test uses its budget in full — `--timeout` seconds, 45 by default — so two
networks need 90 seconds of radio time per round and three need 135.

Where the jobs do not fit into their `--max-age`, the service shares the
radio out evenly and all of them age together, instead of one network
starving while another stays fresh. That shows on the **Measurement Age**
channel of every affected sensor at once, which is why the limit belongs on
that channel. Past `--max-age` the sensor stops offering the old value as a
result at all: `Auth Result` goes to 3 and the failure code to 12.

So a sensor asking for a 45-second check to be no older than a minute, next
to a second one of its kind, cannot be satisfied — and says that, instead of
reporting a failure about the Wi-Fi.

The renewal starts a whole round before the limit, not at some fraction of
it: a result renewed at `--max-age` would already be too old by the time
the round is over. With the default `--max-age 300` and two networks of 45
seconds, the radio is used every 210 seconds — not every minute, however
often PRTG asks. **A scanning interval of one minute no longer means a
measurement every minute.** Whoever wants that has to say so, with a
`--max-age` just above what a round costs.

The state on a probe is readable where the measuring happens, which is the
only place it exists:

```bash
python3 - <<'PY'
import json, socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect("/run/prtg-sensor-wlan-auth.sock")
s.sendall(json.dumps({"action": "status"}).encode())
s.shutdown(socket.SHUT_WR)
print(s.recv(65536).decode())
PY
```

It names every registered job with the freshness it asked for, the age it
holds, when it is renewed and when it expires unasked — and never the
credentials it signs in with.

All variable values go into the `wpa_supplicant` configuration as hex
strings. Quotation marks or line breaks in an SSID or a password therefore
cannot produce an additional directive. The WPA2 passphrase is converted to
the key up front and thus never sits in a file at any point.
