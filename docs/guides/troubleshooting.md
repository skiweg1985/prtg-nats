---
title: Troubleshooting
role: everyone
updated: 2026-08-30
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
| An iperf3 rollout cannot identify the userspace platform | the helper cannot choose a managed artifact or a controlled system fallback | [Check the iperf3 tool selection](#an-iperf3-rollout-rejects-the-tool); do not select from `uname -m` |
| An iperf3 rollout rejects a managed signature or SHA-256 digest | the artifact is incomplete, altered or from a different release | update the central stack and redeploy; a failed managed check never becomes a system fallback |
| An iperf3 rollout requests `/usr/bin/iperf3` | this identified platform has no managed artifact and the system fallback is absent or incompatible | install or update the operating-system package manually, then redeploy |
| `test authorization failed (auth-failed)` against a foreign iperf endpoint | the endpoint is older than 3.17 or the credentials, public key or clock do not match | [Check the endpoint's OAEP support](#iperf3-reports-auth-failed) |
| `connection refused` or a timeout | firewall, DNS, or the container is not ready | check the network path; on the NATS host run `./prtg-nats status` |
| `nats: IO error` in a loop while a port test says "reachable" | the firewall resets the session at the TLS upgrade | [The connection drops at the TLS upgrade](#the-connection-drops-at-the-tls-upgrade) |
| `dpkg process was interrupted` | an earlier package run was aborted | on the probe run `sudo dpkg --configure -a`, `sudo apt-get -f install` and `sudo dpkg --audit` |
| The probe does not appear | wrong PRTG access key, or the GID was denied | check the access key and `Deny GIDs`, then restart the service |
| `result_evaluation was not available` | an old, incompatible ping v2 sensor | recreate the sensor in PRTG; this is not a NATS connection error |
| A job stays on `running` and cancel does nothing | its worker is gone, usually the API container was restarted mid-job | [A job stays on running](#a-job-stays-on-running-and-cancel-does-nothing) |
| `active_transaction=TRANSACTION` after a sensor activation refusal | an earlier sensor job stopped after activation and left its guarded snapshot | use the exact transaction-bound command from the failed job; see [Recover an interrupted transaction](deploy-sensors.md#recover-an-interrupted-transaction) |
| `probe.package_missing`, or `Unit prtg.mpprobe.service not found` while configuring | the probe carries no `prtgmpprobe`, usually after an unenrollment with `--uninstall-mpp` | [Re-enrolling a probe whose package was removed](#re-enrolling-a-probe-whose-package-was-removed) |
| `Sensor NAME was modified on the probe`, and redeploying does not clear it | a probe helper older than version 2 reports the digest of the rewritten shebang | [A sensor reports as modified right after deployment](#a-sensor-reports-as-modified-right-after-deployment) |
| `bind: address already in use` after an update, and the proxy restarts in a loop | a container from an older checkout still holds the port | [A container from an older checkout holds a port](#a-container-from-an-older-checkout-holds-a-port) |

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

### The overlay is up but nothing passes through it

Look at the handshake age first: `./prtg-nats overlay show USER`. An
interface that is up with no recent handshake means the probe's packets are
not reaching the hub at all - almost always the UDP port, which is the one
port the overlay needs and the one a firewall between the two is most likely
to be dropping.

In mode `auto` this is already handled: the probe will not move NATS traffic
onto a tunnel without a fresh handshake, so it keeps using the direct path.
In mode `on` it is not handled, deliberately - `on` means the tunnel and
nothing else, and the platform reports the problem rather than quietly
undoing the choice. Put the probe back to `auto` while you fix the port.

### A probe has been on the fallback path for a while

A probe in mode `auto` that reports `tunnel` is working. It also means its
ordinary route to NATS is down and the overlay is the only reason
measurements are still arriving - usually a site-to-site tunnel nobody has
noticed, because everything downstream still looks green.

The overlay page is the only place this shows. Fix the site's own path; the
probe moves back on its own after three successful checks.

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

### An iperf3 rollout rejects the tool

The `iperf-throughput` rollout chooses its source from the probe's userspace
platform. It prefers a signed iperf3 3.21 release artifact. An identified
platform without an artifact may use an already installed
`/usr/bin/iperf3`, provided it is version 3.18 or newer and reports
`authentication`. A failure leaves the previous sensor and managed tool active.

Start on the NATS host:

```bash
./prtg-nats sensor status mpp-probe-01
```

The status names the source, absolute path, active version, platform, SHA-256
digest and compatibility. The managed platform values are:

- `linux-amd64-glibc`
- `linux-arm64-glibc`
- `linux-armhf-glibc`, for ARMv7 and newer

If platform detection is the failure, check the **userspace** on the probe:

```bash
dpkg --print-architecture
getconf LONG_BIT
file -L /bin/sh
```

Use the equivalent package-architecture command on a non-Debian system. Do
not decide from `uname -m` alone: a Raspberry Pi can report an `aarch64`
kernel while its programs and dynamic loader are 32-bit `armhf`.

An identified ABI outside those three values is not automatically unsupported.
For example, `linux-armhf-v6-glibc` uses the controlled system fallback because
the ARMv7 managed artifact cannot run there. An unidentifiable userspace still
fails closed. Do not copy another platform's executable or create `current` by
hand.

For a managed platform, update the central stack so the catalogue, signatures
and artifacts come from the same release, then deploy again:

```bash
./prtg-nats update
./prtg-nats sensor deploy iperf-throughput mpp-probe-01
```

The rollout updates an outdated helper before it sends the tool. If the probe
was enrolled before signed helper updates and has no verification key, follow
the one-time bootstrap path in
[Deploy sensors](deploy-sensors.md#prerequisite-a-current-probe-helper).

If a signature, digest or version mismatch repeats after a clean stack update,
stop. Keep the old `current` target in place and investigate the release
artifact or transfer. That failure must not select `/usr/bin/iperf3`; the
system fallback exists only when no artifact matches the identified platform.

If the status says `source=system`, check the exact path on the probe:

```bash
/usr/bin/iperf3 --version
```

The first line must be version 3.18 or newer and the optional features must
include `authentication`. If the file is absent or fails either check, install
or update the distribution's `iperf3` package manually and redeploy. The helper
does not run `apt`, `dnf` or another package manager, and it does not accept a
binary from a different path.

### iperf3 reports `auth-failed`

The sensor reports this shape when the endpoint rejects its authenticated
measurement:

```text
The endpoint refused the download measurement: test authorization failed
(auth-failed)
```

First confirm that the probe status reports a compatible tool. A managed
source must name iperf3 3.21 and its release path; a system source must name
`/usr/bin/iperf3` and version 3.18 or newer. A missing or incompatible tool
status is a rollout problem and belongs to the previous section.

For a foreign endpoint, ask its operator to run:

```bash
iperf3 --version
```

It must report 3.17 or newer and list `authentication` in its optional
features. Version 3.17 introduced the RSA-OAEP authentication path used by the
managed client. An older endpoint uses incompatible legacy padding and must be
upgraded before the credentials can work.

Do not add `--use-pkcs1-padding` to the client or server. It uses a different
authentication mode and is not a supported compatibility mode. Do not point
the sensor at an older system iperf3 either.

If the endpoint passes the version preflight, continue with the public-key,
credential and clock checks in
[the foreign-endpoint guide](foreign-iperf-endpoint.md#counter-check-on-the-endpoint-itself).
The public key must belong to that endpoint, the user and password must be the
pair used to build its authorization hash, and both clocks must be
synchronized. None of those values should be printed in a job log.

### The probe runs, but creating a sensor fails

Errors such as `ping_group.result_evaluation was not available` only occur
after a message has been delivered successfully. That is an incompatible old
sensor, not a rejected duplicate and not a NATS error. Recreate the affected
sensor in PRTG.

### Re-enrolling a probe whose package was removed

An unenrollment with `--uninstall-mpp`, or the "Uninstall MPP" checkbox in the
dialog, purges `prtgmpprobe` and the Paessler package source. Taking that host
on again from the interface alone does not bring either back: the package
arrives with the bootstrap script and with nothing else. Without it there is no
`prtg.mpprobe.service` for a configuration to start, and the job stops with
`probe.package_missing`.

Mint a fresh invitation and run its one-liner on the probe. The bootstrap
installs the package and only then does the platform configure anything; where
the package is already in place, it is kept. Alternatively install it directly
on the probe and start the job again afterwards:

```bash
sudo ./install-mpp.sh --nats-host nats.example.com --nats-port 23561 --no-config
```

If the bootstrap itself could not install the package, it reports back anyway
and the job carries the installer's own words in its technical details - a
`dpkg` lock, an unreachable `packages.paessler.com`, an interrupted package
run. Fix what it names, then enrol again.

### A sensor reports as modified right after deployment

"Modified" means the script on the probe and the one in the catalogue have
the same version but different bytes. Usually somebody edited the file, and
`sensor deploy` puts it back. A sensor that returns to that state
immediately, without anyone having touched it, is a different case.

A sensor that ships a `requirements.txt` gets its own virtual environment,
and the helper points the installed script's shebang at it - one line that
differs from the catalogue file by design. Helper version 1 hashed the file
as it lay, so it answered with a digest that could never match. Every
redeployment ended the same way, because the next install wrote the same
interpreter back in. `internet-speed` is the only sensor in this repository
with dependencies, so it is the one this shows up on.

Helper version 2 records the line the catalogue shipped and puts it back
before it hashes; an edit anywhere else in the file, and a shebang the helper
did not write itself, both still show. Update the helper:

```bash
sudo ./prtg-nats probe helper-update mpp-probe-01
```

The probe page offers the same under "Update helper". The deviation
disappears with the next status query - the sensor itself needs no
redeployment, because nothing was ever wrong with the installed file.

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

### A container from an older checkout holds a port

The compose project and the container names are fixed, so two checkouts of
this repository address the same containers. After an update the older one
is usually still there as the way back - and a `docker compose up -d` in it
starts a service the current version has since dropped, under a name nothing
here claims any more.

The CA download is the case this happened to: the reverse proxy took the job
over, and the container that used to do it came back and kept port `80`.
Caddy could not bind it and restarted in a loop, while the only error anyone
saw named the port rather than what was holding it:

```text
Error: loading initial config: http app module: start:
listening on 192.0.2.10:80: bind: address already in use
```

`./prtg-nats start`, `restart`, `setup` and `update` say so before they try:

```text
Containers of this stack are running from another checkout:
  prtg-nats-ca, started from /opt/prtg-nats-server
```

Take them down where they came from, or remove them by name:

```bash
docker rm -f prtg-nats-ca
```

Which checkout a container came from is in its labels:

```bash
docker inspect prtg-nats-ca \
  --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```
