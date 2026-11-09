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
| `--gateway ADDRESS\|none` | which address to ping after DHCP; empty means the one from the lease |
| `--mac auto\|permanent\|ADDRESS` | client address: one per network (default), the radio's own, or a fixed one |
| `--timeout SECONDS` | time budget for the whole test, default 45 |
| `--sensor-id %sensorid` | the PRTG sensor ID; a changed configuration of the sensor then replaces the old one on the probe at once |
| `--max-age SECONDS` | optional upper bound for the measurement age; unset (default), the probe plans by itself |
| `--measure-now` | measure on the spot instead of collecting (diagnosis) |

Every parameter name starts with two plain hyphens. A line typed on a
phone or pasted out of a document easily carries an en or em dash instead,
which looks the same on screen and leaves the sensor down with *"Could not
parse input parameter"* — so the message names the character when it finds
one.

A parameter PRTG refuses for any other reason reads the same way, so the
message says what is wrong with this one: which parameter it was, and
either the values it accepts, the kind of value it needs, or the known
name closest to a misspelt one. What was configured is never repeated
back, because a parameter line carries passphrases.

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

**What each method needs.** `psk` takes `PSK`. Both enterprise methods take
`IDENTITY` and `CA_CERT`: the user name because the RADIUS server is asked
about a user either way, the authority because the sensor refuses to run
against an unverified server. `NO_VERIFY_SERVER=true` is the decision not
to verify it, and then `CA_CERT` may stay empty — for a first test, or
where the authority is not at hand. `peap` adds `PASSWORD`, `eap-tls` adds
`CLIENT_CERT` and `PRIVATE_KEY`, plus `PRIVATE_KEY_PASSWD` where the key
file is encrypted.

**`CA_CERT` takes a bundle.** Several certificates may share the file, so
an intermediate and its root travel together — which is what a two-tier
Windows PKI needs, and what works whether or not the RADIUS server sends
its intermediate along. The authority alone accepts every certificate it
ever signed, though: `DOMAIN_SUFFIX_MATCH` is what pins the server itself,
and without it another host of the same PKI can stand in for the RADIUS
server.

`PRIVATE_KEY_PASSWD` is the passphrase of the key file, not a user
password - EAP-TLS proves the identity with the certificate alone. A key
exported without a passphrase leaves the field empty.

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
| 19 | Gateway Reachable | lookup, absent with `--gateway none` |
| 20 | Gateway Time | ms, absent with `--gateway none` |
| 21 | Round Time | s, one pass over every job on the radio |
| 22 | Jobs on Radio | count |

**Auth Result** works well as the primary channel; it can be set in the
sensor settings. A failed sign-in does not set the sensor to *Down*; it sets
this channel — that keeps the history of the timing channels readable. The
alarm is set up through a limit on the channel.

The third value is neither: `3` means nothing was measured, either because
the job was only just registered or because the last measurement is
stale. It uses the standard lookup
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

**Gateway Reachable** separates a lease from a connection, and it needs
no configuration: with `--stage dhcp` the sensor pings the gateway as part
of the test. An address proves only that a DHCP server answered. A client
placed in the wrong network holds a perfectly valid lease and reaches
nothing — seen on a probe whose guest network handed out addresses of the
internal one, where the sensor reported *ok* for minutes while the client
could not reach its own gateway.

Asked is the router the lease names, or the server that offered it where
the lease names none. `--gateway ADDRESS` replaces that choice, for a
network whose router stays silent or one with fixed addressing. The
leased address is put on the interface as a `/32` with a host route for
the duration of the ping and removed afterwards, so nothing of it
outlives the run.

> **A network that drops ICMP needs `--gateway none`.** The check runs by
> default, so a sensor that was green before this change and sits in such
> a network reports failure code 14 from the next scan on. `none` takes
> the question away, and channels 19 and 20 with it.

### One address per network

By default the probe signs in to every network under an address of its
own, derived from the radio's built-in address and the SSID: locally
administered (`02:…`), the same every time for the same network, different
for every other network and every other radio. Phones do the same with
their private address per Wi-Fi network.

The reason is a fault on Aruba gateways under AOS-10, found on two of
three sites measured: a station that signs in to an 802.1X network and
then, within the user idle timeout, to a PSK network under the same
address is given the VLAN of its previous session. A probe that checks
an internal and a guest network on one radio, a few seconds apart, then
measures the guest network in the internal VLAN — with a valid lease and a
green sensor. Under a second address the guest network is measured as
itself. The background is in
[ADR 0012](../../docs/architecture/decisions/0012-one-client-address-per-network.md).

| `--mac` | Signs in under |
| --- | --- |
| `auto` (default) | the network's own address |
| `permanent` | the radio's built-in address, as before this change |
| `02:00:5e:10:00:01` | exactly this address — for a DHCP reservation or an allowlist |

The address is set before every run and the radio's own is put back
afterwards. It appears in the message, so the client can be found in the
gateway's user table and in Central. A radio whose driver refuses a new
address does not fall back to its own, which would bring the fault back
unnoticed: the run does not take place and the sensor reports a sensor
error that names the address and what the kernel said.

A network address that is reused also reuses what the gateway remembers
about it, and a guest role that blocks private networks can trip over
that. Measured on Aruba AOS-10 with a guest policy of *permit ICMP to the
gateway, deny private-networks, permit any*: the first run under a new
address got its lease, every later one timed out on DHCP. Once the gateway
knows the client's address, its DHCP answer goes out as unicast to that
private address and the deny catches it - a rule that permits DHCP only
*towards* the gateway does not help, because the answer travels the other
way. What fixed it was `any → any → svc-dhcp → permit` ahead of the deny.
Real guests need the same rule anyway: a phone renews its lease by unicast
to the server.

> **Existing sensors sign in under a new address after this update.**
> Where a DHCP reservation, a MAC allowlist or a rule in Central is tied to
> the radio's address, set `--mac permanent` or the reserved address with
> `--mac ADDRESS`.

**Measurement Age** says how old the reported measurement is. With a
healthy schedule it stays below the scanning interval plus a round.
**Round Time** is how long one pass over every job on the radio takes, and
**Jobs on Radio** how many share it - a round that grows is a network that
started failing or one network too many. The three exist from the first
scan, reading zero until something has been measured. See
[what the radio can deliver](#what-the-radio-can-deliver).

If the sign-in fails, the association time is still kept. That makes it
possible to tell whether the radio link stood and only the authentication
was rejected.

### Failure codes

| Code | Channel 18 | Meaning |
| --- | --- | --- |
| `ok` | 0 | sign-in succeeded |
| `assoc-timeout` | 1 | no association within the time budget |
| `auth-failed-psk` | 2 | passphrase rejected |
| `auth-failed-eap` | 3 | the RADIUS server rejected the sign-in |
| `server-cert-rejected` | 4 | server certificate not accepted |
| `auth-rejected` | 5 | the access point rejected the sign-in |
| `assoc-rejected` | 6 | the access point rejected the association |
| `ssid-not-found` | 7 | SSID not found in the scan |
| `dhcp-timeout` | 8 | no DHCP answer |
| `auth-timeout` | 9 | key negotiation not completed |
| `assoc-lost` | 10 | the link changed while DHCP was running |
| `not-measured` | 11 | registered, not measured yet |
| `result-stale` | 12 | the last measurement is older than about two rounds, or than `--max-age` where that is set |
| `max-age-too-low` | 13 | retired: a `--max-age` the radio cannot keep is a warning in the message now |
| `gateway-unreachable` | 14 | the lease is valid, but the gateway does not answer |

The message line is a row of short parts, the network and its verdict
first — in a list of sensors it is everything visible without opening one.
After that, in a fixed order: the identity that signed in, the client
address, the access point, the leased address and its DHCP server, the
gateway ping, on failure the code, and the age of the measurement. A part
without a value is left out.

The identity follows the verdict on success and on failure alike, because
it is the name a sign-in is looked up by in the RADIUS log. PEAP and EAP-TLS
have one, a pre-shared key has none. With `--profile` the identity lives
only in the variant on the probe, so the privileged service returns it with
the measurement. The passwords, the key passphrase and the anonymous
identity never come back.

```text
corp-wifi: OK; identity probe01@example.test; client 02:5e:9a:31:44:07; AP 00:00:5e:00:53:c2; IP 192.0.2.80 from 192.0.2.1; ping 5 ms; age 8 s
corp-wifi: credentials rejected by RADIUS; identity probe01@example.test; client 02:5e:9a:31:44:07; code auth-failed-eap; age 14 s
Office: OK; client 02:3f:a1:9c:44:e7; AP 00:00:5e:00:53:c2; IP 192.0.2.75 from 192.0.2.1; ping 7 ms; age 12 s
Office: OK; client 02:91:0b:7e:12:c4; AP 00:00:5e:00:53:c0; IP 198.51.100.26 from 198.51.100.1; router 198.51.100.254 ping 4 ms; age 31 s
Office: no DHCP answer; client 02:3f:a1:9c:44:e7; AP 00:00:5e:00:53:c2; code dhcp-timeout; age 20 s
Office: association rejected by AP (status 17); client 02:3f:a1:9c:44:e7; code assoc-rejected; age 9 s
Office: waiting for first measurement
Office: measurement 9 min old, round currently 4 min, 4 jobs on wlan0
--max-age 60 s cannot be kept, the radio needs 120 s for 2 jobs on wlan0; Office: OK; client 02:3f:a1:9c:44:e7; age 61 s
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

### EAP-TLS against a Windows NPS

Two messages under code 3 mean different things, and the difference is
where to look.

*credentials rejected by RADIUS* is the plain case: the EAP exchange ran
and ended in a rejection.

*RADIUS broke off the TLS handshake* means the server answered the client
certificate with a TLS alert that `wpa_supplicant` could not read. NPS
sends such an alert with record version `0x0301` although the session is
TLS 1.2, and OpenSSL 3 drops the record over the version — the raw lines
then end on `SSL_connect error … wrong version number` and the alert
itself is lost. Measured against NPS the alert was `access_denied`.

What it is in practice: the certificate is cryptographically fine and the
server refuses the authorisation. NPS maps an EAP-TLS client certificate
to an account through the UPN in its `subjectAltName`, and since
[KB5014754](https://support.microsoft.com/kb/5014754) it wants either the
SID extension `1.3.6.1.4.1.311.25.2` in the certificate or a strong
`altSecurityIdentities` mapping on the account. A certificate carrying
only a `CN` has neither, and NPS logs event 6273 with reason code 16,
*user credentials mismatch* — which reads like a wrong password and is
not one.

So before suspecting the probe: check what the certificate carries.

```bash
openssl x509 -in client.pem -noout -subject \
  -ext subjectAltName,extendedKeyUsage
```

A certificate for this is issued from a template that writes the account's
UPN into the `subjectAltName` and needs the extended key usage *TLS Web
Client Authentication*. A template set to *supply in the request* produces
exactly the unusable certificate above when nothing is supplied.

## Create the sensor in PRTG

1. Add a **Script v2 sensor** on the probe's device.
2. Select `wlan-auth.py` as the script.
3. Enter the parameters, see above.
4. Set the scanning interval to how often this network should be
   measured — five minutes is a sound default. The probe measures the
   network about that often, never more than once a minute.
5. Leave the timeout at its default. Collecting needs little patience —
   the script gives up after 40 seconds of its own, 20 of which are the
   wait for a new job's first measurement.
6. Add `--sensor-id %sensorid` to the parameters, so a changed
   configuration replaces the old one on the probe at once.
7. Leave `--max-age` unset. A result that is too old already turns the
   failure code to 12; a limit on **Round Time** warns earlier, when the
   radio is getting crowded.

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

## Sensor logs

Every run leaves one record in the platform's sensor log: a scheduled run
and a hand-run `--measure-now` alike, successful or not. A run that never
got the radio because another test held it leaves none, since nothing was
measured. Open the records under **Sensor log** in the web interface, or on
the probe's page.

The privileged service writes the record. It hands the record to the return
channel service on `/run/prtg-nats-return-channel.sock`, which keeps it on
disk until the platform has it
([ADR 0013](../../docs/architecture/decisions/0013-probes-report-back-over-a-channel-of-their-own.md)).
Without that service the sensor measures and reports to PRTG as before; only
the record is missing. The handover gives up after two seconds, so a service
that hangs cannot hold up the radio.

A record carries:

- the result (`ok` or `error`), the failure code and the message PRTG got,
  a 12-character job label, and the time. The label is a hash over the
  job without its credentials, so a record never carries anything derived
  from a password
- the network, the authentication method, the interface, the stage, the
  identity and the variant (`profile`, the name given with `--profile`). The
  platform draws two configurations of one SSID as two rows of the timeline
  by it
- what the run saw: access point, client address, leased address, DHCP
  server, gateway, frequency, signal, and the stage timings
- the raw lines of `wpa_supplicant`
- the raw output of the DHCP client, including its error output
- the service's own account of the run: the lock, the stages it reached,
  the cause it ended on

The passphrase, the enterprise password and the private key passphrase are
removed before the record leaves the service — from every field and from
every raw line, in plain text, in the hex form the supplicant configuration
uses, and for a passphrase also as the key derived from it. A secret
shorter than four characters cannot be blanked without making the line
unreadable, so a line holding one is dropped entirely. The identity, the
addresses and the certificate subjects stay: they are what a failed sign-in
is looked up by in the RADIUS and gateway logs. Very long output is
shortened to the first and last lines so that a record stays below the
service's 64 KB limit.

The PRTG message of a failed run ends with the link to its record:

```text
Guest: no DHCP answer; client 02:3f:a1:9c:44:e7; code dhcp-timeout; age 20 s; log https://nats.example.test/sensor-logs/3f0c3d1e-58a4-4c39-9a3b-7e0f2b6f4d10
```

The platform writes its interface address (`WEB_URL`) into the return
channel's `channel.env` whenever it installs the channel or refreshes its
service. That directory is readable by root only, so the privileged helper
builds the link and hands it to the sensor script with the result. A probe
whose channel has no interface address yet — an older helper, or a platform
without `WEB_FQDN` and `NATS_FQDN` — gets the path alone
(`log /sensor-logs/<id>`); put it after the address you open the platform
with. The message stays within PRTG's 2000 characters. When it has to be
shortened, the parts after the identity give way first, the last one first;
the verdict, the identity and the link to the run stay.

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

1. The first request registers the job, and the service measures it at
   once. The request waits up to 20 seconds for that first measurement, so
   a new or changed sensor shows values on its first scan. Only when the
   radio is busy with another run does it answer "not measured yet" —
   `Auth Result` reads 3, which alarms on nothing.
2. Every further request is answered with the latest measurement and its
   age. The service reads off the requests how often PRTG asks, and
   measures the job about that often.
3. A job nobody asks for any more pauses after three of its intervals: it
   is no longer measured and takes no part in the round. If its sensor asks
   again, it is measured at once.
4. A paused job expires after three of its intervals and at least an hour -
   long enough that a sensor polled every half hour, or one whose interval
   was just raised, never loses its job between two scans. Deleting or
   pausing the sensor in PRTG therefore ends the measuring, and leaves
   nothing to clean up on the probe.

The credentials live in the memory of that service and nowhere else. They
are part of what identifies a job, so any changed parameter makes a
different job. Without more to go on, the old one pauses once it has missed
three scans - a few minutes at the one-minute interval. With
`--sensor-id %sensorid` in the parameters, the service knows which sensor in
PRTG asks, and a new configuration of the same sensor replaces the old job
at once. A placeholder PRTG did not replace arrives as written; the sensor
then says `--sensor-id ignored` in its message and works as without it.

The sensor page of a probe in the web interface lists the configurations
the service holds, which of them are paused, and removes one on request.

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

The scanning interval in PRTG is the one setting. The service reads it off
the requests and measures each network about that often: a sensor polled
every five minutes is measured about every five minutes and leaves the
airtime in between to the others. A network is never measured more than
once a minute, however often its sensor asks — an access point that sees
the same radio sign in every few seconds starts turning it away, seen on a
probe with two jobs on one network, rejected with status 16 until nothing
got through. After every sign-in the radio rests ten seconds before it
takes the next network.

A round is what the jobs on the radio really take. The service keeps the
duration of the last three runs of every job and plans with the slowest,
plus the rest after it. A good sign-in takes 8 to 15 seconds, so two
healthy networks cost about 50 seconds a round. A failing one uses its
whole `--timeout`, 45 seconds by default, and lengthens the round for
everyone with its first long run — intended, because it has to stay
measured, and visible on **Round Time**. After a restart the service plans
with the worst case, the timeout plus the wait for the radio, until every
job has run once.

Where the jobs do not fit into their intervals, the service shares the
radio out evenly and all of them age together, instead of one network
starving while another stays fresh. A result counts as stale once it is
older than twice its interval, or twice the round where the round is
longer. Past that, `Auth Result` goes to 3, the failure code to 12, and
the message says what to look at:

```text
Office: measurement 9 min old, round currently 4 min, 4 jobs on wlan0
```

That is a hint at a real problem — a stuck service, a radio with too many
networks — not at a setting.

`--max-age` is still accepted, as an upper bound for sensors that need one
or were set up before. The network is then measured at least a round
before the bound. What the radio can keep is a round of waiting on top of
the once-a-minute floor, or two rounds where the round is longer. A value
below that is a warning first in the message, not a red sensor:

```text
--max-age 60 s cannot be kept, the radio needs 120 s for 2 jobs on wlan0; Office: OK; client 02:3f:a1:9c:44:e7; age 61 s
```

The measurement keeps its own result and failure code, and is judged
against what the radio can keep. Removing `--max-age` hands the schedule
to the probe. Failure code 13, which this case raised before, is retired.

> **A sensor at the one-minute interval is now measured every minute.**
> Before this change it was renewed every three to four minutes, whatever
> it asked. Set the scanning interval to how often the network should be
> measured. The background is in
> [ADR 0014](../../docs/architecture/decisions/0014-the-helper-plans-the-radio-from-what-it-measures.md).

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

It names every registered job with how often it is asked for, how often
it is measured, what a run is expected to take, the age it holds, when it
counts as stale and when it expires unasked — and the round over the
radio. Each job names its authentication method (`auth`), its variant
(`profile`) and the access point it is pinned to (`bssid`), so two
configurations of one SSID can be told apart; the platform shows the same
list on the sensor's page under **Configurations on the probe**. Never the
credentials it signs in with, and no hash over them.

Each job also carries the address of its last run and the DHCP server that
handed it out. On a probe with several networks on one radio that is the
question being debugged: an address from the wrong VLAN reads like a
healthy result, and only the server behind it tells the two apart.

All variable values go into the `wpa_supplicant` configuration as hex
strings. Quotation marks or line breaks in an SSID or a password therefore
cannot produce an additional directive. The WPA2 passphrase is converted to
the key up front and thus never sits in a file at any point.
