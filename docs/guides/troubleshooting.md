---
title: Troubleshooting
role: everyone
updated: 2026-08-03
---

# Troubleshooting

Sorted by symptom - when something is broken, nobody goes looking by
responsibility. The log paths are in [Monitoring](monitoring.md#logs).

If the web platform is running, its job log answers most of this for you: every
failure names the step it happened in, the likely cause and what to do, with
the probe's own words behind a disclosure control. See
[Jobs and deployments](../web/jobs.md#reading-a-failure).

## Quick lookup

| Message | Usual cause | What to do |
| --- | --- | --- |
| CA download unreachable | port `80`, DNS, or the proxy is not running | copy the PEM with `./prtg-nats ca-show`, or check `prtg-nats-web-proxy` |
| `tls: unknown certificate authority` | wrong or missing CA, or a wrong `server_ca` path | `./prtg-nats probe install-ca USER`, then `probe configure USER` |
| Certificate name does not match | an IP instead of the FQDN, or a wrong DNS record | use `nats.example.com` and correct DNS |
| `Failed to read config file: Permission denied` | `config.yaml` is not readable by the service account | `prtg.mpprobe.service` runs as `paessler_mpprobe`; the file has to belong to that group and be at least `0640` |
| `Authorization Violation` | wrong NATS account or password | compare the protected credential file; do not use PRTG login details |
| `connection refused` or a timeout | firewall, DNS, or the container is not ready | check the network path; on the NATS host run `./prtg-nats status` |
| `nats: IO error` in a loop while a port test says "reachable" | the firewall resets the session at the TLS upgrade | [The connection drops at the TLS upgrade](#the-connection-drops-at-the-tls-upgrade) |
| `dpkg process was interrupted` | an earlier package run was aborted | on the probe run `sudo dpkg --configure -a`, `sudo apt-get -f install` and `sudo dpkg --audit` |
| The probe does not appear | wrong PRTG access key, or the GID was denied | check the access key and `Deny GIDs`, then restart the service |
| `result_evaluation was not available` | an old, incompatible ping v2 sensor | recreate the sensor in PRTG; this is not a NATS connection error |
| A job stays on `running` and cancel does nothing | its worker is gone, usually the API container was restarted mid-job | [A job stays on running](#a-job-stays-on-running-and-cancel-does-nothing) |

The official installation and wizard details are in the
[Paessler MPP manual](https://manuals.paessler.com/multiplatformprobemanual.pdf).

## In detail

### Test the endpoint the way the probe uses it

The first thing to do for any connection problem. The test takes the same path
the probe does - DNS, TCP, the NATS greeting, the TLS upgrade - and names the
phase it fails in. It changes nothing and can be repeated freely:

```bash
sudo ./install-mpp.sh --nats-host nats.example.com --check-only
```

A plain port scan is not enough: NATS starts in cleartext and only then
switches to TLS. A fault that strikes at the switch is only visible to a test
that switches too.

### The connection drops at the TLS upgrade

Symptom: the probe logs `nats: IO error` continuously, the NATS server shows
`TLS handshake error: … connection reset by peer`, and port tests still report
the far side as reachable. The endpoint test says:

```text
ERROR: The connection was reset while upgrading to TLS
```

The cause is almost always a firewall with application detection. NATS opens
the session in cleartext with an `INFO` line; on an unusual port that cannot be
classified as TLS. The session is categorised as `unknown-tcp` and reset at the
switch - while a plain connection attempt is still allowed and therefore
reports "successful".

You can recognise it by a hard `RST` **without** a preceding TLS alert: a
client that rejects a certificate sends an alert first and closes cleanly.

Look in the firewall log for the session between probe and NATS server on the
NATS port. If a deny rule with application `unknown-tcp` matches, allow the
session or add an application definition for NATS. Certificates, credentials
and configuration are fine in this case and need no change.

### TLS `unknown certificate authority`

The client rejects the server certificate:

- on the probe the correct CA has to be at
  `/etc/paessler/mpprobe/certs/nats-docker-ca.pem`;
- in `config.yaml`, `nats.server_ca` has to point at exactly that path;
- in the PRTG core the same CA has to be a PEM file with a `.crt` extension in
  the `cert` directory of the program folder.

Both points on the probe are fixed in one go:

```bash
sudo ./prtg-nats probe install-ca mpp-probe-01
sudo ./prtg-nats probe configure mpp-probe-01
```

### `Authorization Violation`

Check the NATS account and password. Do not confuse them with the PRTG web
login or with the PRTG access key.

### The probe runs, but creating a sensor fails

Errors such as `ping_group.result_evaluation was not available` only occur
after a message has been delivered successfully. That is an incompatible old
sensor, not a rejected duplicate and not a NATS error. Recreate the affected
sensor in PRTG.

### A job stays on running and cancel does nothing

Cancel asks the running job to stop at its next step - it does not end the
job from outside. A job whose worker no longer exists, because the API
container was restarted or the process was killed while it ran, has nobody
left to read that request. The row keeps saying `running` and holds its
probe against every job queued behind it.

The runner clears those out on its own. Jobs left behind by a previous
process are ended while the API starts, and every minute after that the
reaper does the same for a job whose worker disappeared underneath a
process that kept running. Such a job is reported as cancelled if somebody
had asked for that, and as failed with `jobs.orphaned` otherwise; its locks
are released either way.

Restarting the API container is therefore the whole fix:

```bash
docker restart prtg-nats-web-api
```

A job whose worker is genuinely still working is a different case, and a
restart there aborts real work: `mpp uninstall` waits up to fifteen minutes
for the package manager on the probe. As long as the job log still gains
lines, the cancel takes effect once the current step returns - wait for it.
