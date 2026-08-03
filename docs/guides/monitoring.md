---
title: Monitoring
role: operator
updated: 2026-08-03
---

# Monitoring

What to watch, what healthy looks like, and what to do when it is not. The
stack monitors itself on three levels, and each one answers a different
question.

| Level | Question it answers | Where |
| --- | --- | --- |
| Container health checks | is the process serving? | Docker, automatic |
| `status` and `verify` | is the installation correct? | the shell, or the system page |
| PRTG sensors | is it still correct at three in the morning? | the PRTG core |

The first two run without anyone watching. The third is the one that wakes
somebody, and it is the reason the stack publishes a PRTG-shaped endpoint of
its own.

## Container health checks

NATS carries a health check, so Docker restarts what has genuinely stopped
serving rather than what merely looks idle
([compose.yaml](../../compose.yaml)).

| Container | Check | Interval | Healthy means |
| --- | --- | --- | --- |
| `prtg-nats` | `GET /healthz?js-enabled-only=true` on `127.0.0.1:8222` | 15 s, 5 retries | NATS is up **and** JetStream is enabled |

`prtg-nats-web-proxy` has none and needs none: it refuses to start without the
interface certificate, so a proxy that is running is a proxy that is serving.
Both containers restart in a loop on an uninitialised runtime, which is what
lets the stack come up before it is configured - `./prtg-nats setup` ends that
state.

`js-enabled-only=true` is deliberate. A NATS that answers but has lost
JetStream would pass a plain liveness check while every persistent stream is
gone.

```bash
sudo ./prtg-nats status
```

## The installation check

`verify` goes further than health: it checks that the installation is
*correct*, ending in a real authenticated TLS login.

```bash
sudo ./prtg-nats verify            # the full check
sudo ./prtg-nats verify --offline  # configuration only, no network
```

It covers the Compose configuration, the certificate chain and the SAN, that
the HTTP endpoint serves exactly the active runtime CA, the JetStream health
check, and the authenticated login. The same check runs as a job from the
system page of the web interface.

## The PRTG HTTP sensor

The CA container also serves a small CGI endpoint that turns the NATS
monitoring into PRTG channels, so JetStream is monitored by PRTG itself rather
than by a person running `status`:

```text
http://nats.example.com/cgi-bin/nats-health
```

On every call it queries `/healthz?js-enabled-only=true` and `/jsz` on the
NATS server and returns Script v2 JSON. The source is
[http/cgi-bin/nats-health](../../http/cgi-bin/nats-health).

### Channels

| Channel | Unit | What it means |
| --- | --- | --- |
| `NATS Health` | Count | Always `1`. A failure is an HTTP 503 with a PRTG error, not a `0` |
| `JetStream Streams` | Count | Configured streams |
| `JetStream Consumers` | Count | Consumers across all streams |
| `Stored Messages` | Count | Messages currently persisted |
| `JetStream Memory` | BytesFile | Memory-backed storage in use |
| `JetStream File Storage` | BytesDisk | Disk-backed storage in use |
| `JetStream API Errors` | Count, difference | Warning limit on any new error |

`JetStream API Errors` is a difference counter with a warning limit of `0`, so
the sensor reacts to *new* errors instead of to a total that never returns to
zero. On a NATS, JetStream or evaluation error the endpoint answers HTTP 503
and PRTG shows the sensor as down.

### Create the sensor

1. Add **HTTP Data (Advanced)** on the desired device.
2. Enter `/cgi-bin/nats-health` as the URL - the full URL works as well.
3. Select request method `GET` and the desired scanning interval.
4. After the first scan, `NATS Health` has to show `1` and PRTG has to have
   created all seven channels.

For RTT, CPU, general NATS memory, traffic, connections, subscriptions and slow
consumers, add the native PRTG sensor **NATS Server Overview** as well. The
HTTP sensor complements it with the JetStream object and API error counters
that the native one does not carry.

> [!WARNING]
> The unauthenticated NATS monitoring port `8222` is published on the container
> network and the host loopback only. The CA/HTTP port still has to be
> restricted by the host firewall to the MPP, PRTG and administration networks
> that need it - the endpoint is unauthenticated by design, because a PRTG
> sensor is the only thing meant to call it.

## The fleet

One command reports every enrolled probe, and its exit code is meant to be
consumed by a machine:

```bash
sudo ./prtg-nats probe status --all
sudo ./prtg-nats probe status --all --format json
```

```text
NATS USER                HOST                           SERVICE    PACKAGE    CA          NATS
mpp-probe-01           probe-01.example.com          active     3.10.0-1   ok          connected
mpp-probe-02           probe-02.example.com          inactive   3.10.0-1   ok          disconnected
mpp-probe-03           probe-03.example.com          -          -          -           - (unreachable)

2 of 3 probes without findings.
```

The exit code is `0` only when **every** probe is reachable and active, carries
the expected CA and is signed in to NATS - which makes the call usable
unchanged from cron or from a custom PRTG sensor. An unreachable probe does not
block the run; it appears as its own line with the reason.

If the NATS monitoring is not reachable, the output says so explicitly instead
of falsely reporting the whole fleet as disconnected.

## The web platform

| Endpoint | What it reports |
| --- | --- |
| `GET /health` | liveness; touches no dependency |
| `GET /ready` | each dependency separately |

`/ready` reports dependencies individually on purpose, so a missing Docker
socket shows up as what it is - reduced capability, not a failure. The full
API is in [the REST API reference](../reference/api.md#observability).

## Logs

| Component | Command |
| --- | --- |
| NATS | `sudo ./prtg-nats logs --since=30m` |
| The stack | `docker compose --project-directory /opt/prtg-nats-server logs` |
| MPP probe | `sudo journalctl -u prtg.mpprobe.service -n 300 --no-pager` |
| PRTG core | `C:\ProgramData\Paessler\PRTG Network Monitor\Logs\probeadapter` |

NATS, the API and the proxy cap their logs locally at 10 MB × 5 files each, so
a restart loop cannot fill the disk.

The web platform keeps its own history: every job carries a structured log with
the step that failed and the probe's own words, and every administrative action
lands in an audit trail that cannot be edited. See
[Jobs and deployments](../web/jobs.md).

## What to watch

The short list, for an installation that should page somebody:

| Watch | Warning | Why |
| --- | --- | --- |
| `NATS Health` | sensor down | NATS or JetStream is not serving |
| `JetStream API Errors` | any new error | the difference counter is already set up for this |
| Certificate validity | 30 days | `renew-certificate` needs a maintenance slot |
| `probe status --all` exit code | non-zero | a probe drifted, or fell off |
| `JetStream File Storage` | against the volume size | JetStream fills the persistent volume |

Certificate expiry is also surfaced by the web platform, which warns
`certificate_expiry_warning_days` ahead of time - 30 by default, see
[the configuration reference](../reference/configuration.md#adapters-and-background-work).

## When something is wrong

1. Classify the symptom in [Troubleshooting](troubleshooting.md).
2. For a connection problem, run the endpoint test first - it takes the same
   path the probe does and names the phase it fails in:
   `sudo ./install-mpp.sh --nats-host nats.example.com --check-only`.
3. For a state problem, run `sudo ./prtg-nats verify`.
4. Recovery steps - backup, restore, rollback - are in
   [Operations and maintenance](operations.md).
