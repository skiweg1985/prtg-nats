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
- `wpa_supplicant`, `iw` and `dhcpcd` on the probe (present on Raspberry Pi
  OS and Debian).
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

Reserving takes `wlan0` out of NetworkManager permanently, removes its
address and route and powers the interface down. **An existing Wi-Fi
connection over this interface is lost in the process** — that is intended:
a test interface should do nothing outside the tests.

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
| 10 | Auth Result | 1 = succeeded, 2 = failed |
| 11 | Total Time | ms |
| 12 | Association Time | ms |
| 13 | Auth Time | ms |
| 14 | DHCP Time | ms, only with `--stage dhcp` |
| 15 | Signal | dBm |
| 16 | Frequency | MHz |
| 18 | Failure Code | see the table below |

**Auth Result** works well as the primary channel; it can be set in the
sensor settings. A failed sign-in does not set the sensor to *Down*; it sets
this channel — that keeps the history of the timing channels readable. The
alarm is set up through a limit on the channel.

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

If the test could not take place at all — missing interface, missing
privileges, invalid parameters — the sensor reports a sensor error instead,
with the cause in the message text. That separation is deliberate: such a
state says nothing about the Wi-Fi.

## Create the sensor in PRTG

1. Add a **Script v2 sensor** on the probe's device.
2. Select `wlan-auth.py` as the script.
3. Enter the parameters, see above.
4. Set the scanning interval to 5 minutes.
5. Set the timeout to at least `--timeout` plus 20 seconds, so 65 seconds
   with the defaults.

One sensor checks exactly one network. For several SSIDs, create several
sensors; on the same interface they run one after another, since the sensor
prevents parallel runs on one interface.

## Check without PRTG

On the probe, as root. The invocation reproduces how the MPP service starts
the script: as the service user and with its hardening. A plain
`sudo -u paessler_mpprobe` is **not** enough — it would have more privileges
than the service and mask failures.

```bash
echo '--interface wlan0 --ssid Guestnet --auth psk --psk secretpassphrase' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes --property=RestrictAddressFamilies="AF_INET AF_INET6 AF_UNIX AF_NETLINK" --property=ProtectSystem=full -- /opt/paessler/share/scripts/wlan-auth.py
```

Check only the ability to run, without radio traffic — the same thing
deployment does after installation:

```bash
echo '--self-check' | systemd-run --pipe --quiet --collect --wait --uid=paessler_mpprobe --gid=paessler_mpprobe --property=NoNewPrivileges=yes -- /opt/paessler/share/scripts/wlan-auth.py
```

Is the privileged service reachable?

```bash
systemctl status prtg-sensor-wlan-auth.socket
```

## How the test runs

The sensor script itself must not configure any Wi-Fi. It sends its task as
JSON to `/run/prtg-sensor-wlan-auth.sock`; a dedicated systemd service with
root privileges receives it there. Passwords therefore never appear in the
process list. Why no sudo: see
[the sensor guide](../../docs/guides/deploy-sensors.md#how-a-sensor-gets-root-privileges).

1. The target interface is checked: present, a radio interface, reserved,
   without a default route. A lock file prevents parallel runs.
2. A short-lived `wpa_supplicant` instance starts with a generated
   configuration under `/run` (mode `0600`).
3. Association and completed key negotiation are read off the event stream
   with timestamps, plus signal and frequency.
4. With `--stage dhcp`, a complete DHCP cycle follows in test mode.
5. Cleanup always happens: end the instance, power the interface down,
   delete generated files — even on timeout or abort.

All variable values go into the `wpa_supplicant` configuration as hex
strings. Quotation marks or line breaks in an SSID or a password therefore
cannot produce an additional directive. The WPA2 passphrase is converted to
the key up front and thus never sits in a file at any point.
