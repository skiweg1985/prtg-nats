# Operations and maintenance

Every regular task runs through `./prtg-nats` or the web interface.

## The web interface instead of memory

Day-to-day administration - deploying sensors, rotating accounts, renewing
certificates, following jobs - happens in the web interface:

```bash
docker compose -f compose.yaml -f compose.web.yaml up -d
```

Then open `https://<NATS_FQDN>:8443`. Details in
[../web/install.md](../web/install.md). The former terminal menu (`tui`) was
replaced by the web interface; the commands that remain are listed in
[../reference/cli.md](../reference/cli.md).

## Set up the command line

`setup` offers this at the end on its own; it can be added or repeated at any
time with one command:

```bash
sudo ./prtg-nats self install
```

That sets up two things:

- calling `prtg-nats` from any directory, through a link in `/usr/local/bin`;
- completion of commands, subcommands and options - and above all of the
  created NATS accounts and enrolled probes, so names no longer have to be
  typed out.

Remove both together:

```bash
sudo ./prtg-nats self uninstall
```

For one half only, append `--link-only` or `--completion-only`. If one half
fails - say, because `/usr/local/bin` already holds a file of that name - the
other is still set up and the failure is reported at the end.

Where it writes: the link to `/usr/local/bin/prtg-nats`, never overwriting or
removing a file that is already there. The bash completion to
`/etc/bash_completion.d/prtg-nats` if that directory exists - Debian stopped
creating it with Bookworm, RHEL still has it. Otherwise, and for zsh, into the
user's shell startup file, as a block between two markers. A second run
replaces that block instead of appending it again, and `uninstall` takes it
out and cleans up both locations.

Under `sudo` the command deliberately targets the startup file of the
**calling** user, not root's - otherwise the setup would land where nobody
types. If the shell cannot be determined, it is asked for explicitly:

```bash
sudo ./prtg-nats self install zsh
```

What gets linked and sourced is always the repository, never a copy: a
`git pull` takes effect without re-running the setup. To manage the completion
yourself, `./prtg-nats completion` prints it to standard output.

Then open a new shell:

```text
sudo ./prtg-nats probe status <TAB>
--all         mpp-probe-01  pi-amsti
```

The completion reads the names from `runtime/credentials/` and
`runtime/probes/`. Both directories belong to root with mode `0700` - without
root privileges the commands still complete, but the names stay empty.

## Status, logs and restart

```bash
cd /opt/prtg-nats-server
sudo ./prtg-nats status
sudo ./prtg-nats verify
sudo ./prtg-nats logs
```

Restart the containers in a controlled way:

```bash
sudo ./prtg-nats restart
```

Stop only, and start again later:

```bash
sudo ./prtg-nats stop
sudo ./prtg-nats start
```

Pull images, rebuild the local services on fresh base images and recreate the
whole stack:

```bash
sudo ./prtg-nats update
```

The command validates the Compose configuration first, then runs `pull`,
`build --pull` and `up -d --build --force-recreate --remove-orphans`. After
that it waits for both health checks and verifies TLS, sign-in and
reachability. The persistent JetStream volume is not deleted.

Never use `docker compose down --volumes`. That deletes the persistent
JetStream volume.

`./prtg-nats verify --offline` includes the Compose configuration check. To
see it with variables substituted, call Compose directly - there is no
dedicated command for it any more, because it added nothing:

```bash
docker compose --project-directory /opt/prtg-nats-server config
```

## PRTG HTTP sensor

The existing HTTP container provides an endpoint for the PRTG sensor type
**HTTP Data (Advanced)** at

```text
http://nats.example.com/cgi-bin/nats-health
```

On every call the endpoint internally queries `/healthz?js-enabled-only=true`
and `/jsz` of the NATS server. It delivers these channels:

- `NATS Health`
- `JetStream Streams`
- `JetStream Consumers`
- `Stored Messages`
- `JetStream Memory`
- `JetStream File Storage`
- `JetStream API Errors`

`JetStream API Errors` is emitted as a difference counter and carries a PRTG
warning limit on new errors. On a NATS, JetStream or evaluation error the
endpoint returns a PRTG error message and HTTP 503. The unauthenticated NATS
monitoring port `8222` stays reachable only on the container network and the
host loopback.

Create the sensor in PRTG:

1. Add **HTTP Data (Advanced)** on the desired device.
2. Enter `/cgi-bin/nats-health` as the URL. The full URL from above works as
   well.
3. Select request method `GET` and the desired scanning interval.
4. After the first scan, `NATS Health` has to show `1` and PRTG has to have
   created all seven channels.

For RTT, CPU, general NATS memory usage, traffic, connections, subscriptions
and slow consumers, additionally use the native PRTG sensor
**NATS Server Overview**. The HTTP sensor complements it with JetStream object
and API error counters.

The HTTP port still has to be restricted by the host firewall to the required
MPP, PRTG and administration networks.

## JetStream backup

```bash
sudo ./prtg-nats backup
```

The command briefly stops a running NATS, archives the volume consistently,
creates a SHA-256 checksum and starts the container again. Copy archive and
checksum from `backups/` into the protected backup storage afterwards. The web
interface offers the same backup as a job on the system page.

**`runtime/` has to be backed up as well**, independently: it holds the NATS
passwords, the CA, the probe inventory and the passwords of the iperf3
measurement endpoints (`runtime/iperf/`). An endpoint stores only the SHA-256
of its credentials - if `runtime/iperf/` is lost, the only way back is to set
a new password with
`./prtg-nats iperf-server install ADMIN@HOST --name NAME --rotate` and update
every probe that uses it.

A restore overwrites live state and is therefore deliberately not automated:

1. Confirm the maintenance window and a current backup.
2. Stop NATS.
3. Keep the existing volume untouched.
4. Validate the archive checksum.
5. Restore into a newly created replacement volume.
6. Start NATS on the replacement volume and test it completely.

The former `test-persistence` command was retired without replacement: the
container health check already proves JetStream is serving, and the backup
verifies the stored data. `verify` covers the authenticated connection.

## Renew the server certificate

Check at least monthly:

```bash
sudo ./prtg-nats status
```

Renew and activate immediately - on the certificates page of the web
interface, or from the shell:

```bash
sudo ./prtg-nats renew-certificate
```

The new server certificate is signed by the same CA, so neither the core nor
the MPPs need a new CA. The previous pair is archived under `runtime/archive/`.

`./prtg-nats verify` also checks that the HTTP endpoint serves exactly the
active runtime CA. A deliberate CA change requires updating the PRTG core and
every MPP in the same maintenance operation.

CA information, and the plain PEM for a manual copy/paste rollout:

```bash
sudo ./prtg-nats ca-info
sudo ./prtg-nats ca-show
```

## Rotate the password of a single MPP

The prerequisite is the probe's one-time enrollment:

```bash
sudo ./prtg-nats probe enroll \
  mpp-probe-01 \
  root@probe-01.example.com
```

Then rotate server and probe together - on the credentials page of the web
interface, or from the shell:

```bash
sudo ./prtg-nats user rotate mpp-probe-01
```

The sequence checks the restricted SSH access first, prepares the new probe
configuration, loads the new bcrypt hash without a NATS restart and restarts
only `prtg.mpprobe.service` on that probe. The rotation completes only when
the new connection is visible in the NATS monitoring. On failure, both sides
roll back.

Only when a probe is deliberately not enrolled:

```bash
sudo ./prtg-nats user rotate mpp-probe-01 --server-only
```

That disconnects the probe immediately. The new password it prints has to be
entered on the MPP by hand.

Show enrolled probes and their state:

```bash
sudo ./prtg-nats probe list
sudo ./prtg-nats probe status mpp-probe-01
```

Show the probe identity including the PRTG access key, plus the state the
probe reports about itself:

```bash
sudo ./prtg-nats probe show mpp-probe-01
```

## State of the whole fleet

```bash
sudo ./prtg-nats probe status --all
```

```text
NATS USER                HOST                           SERVICE    PACKAGE    CA          NATS
mpp-probe-01           probe-01.example.com          active     3.10.0-1   ok          connected
mpp-probe-02           probe-02.example.com          inactive   3.10.0-1   ok          disconnected
mpp-probe-03           probe-03.example.com          -          -          -           - (unreachable)

2 of 3 probes without findings.
```

The columns come straight from the probes: `SERVICE` and `PACKAGE` from their
self-report, `CA` compares the fingerprint installed there with the active
runtime CA, `NATS` checks the actual sign-in against the NATS monitoring.

An unreachable probe does not block the run; it appears as its own line with
the reason. The exit code is `0` only when **every** probe is reachable and
active, carries the expected CA and is signed in to NATS. That makes the call
usable unchanged for cron or a custom PRTG sensor:

```bash
sudo ./prtg-nats probe status --all --format json
```

If the NATS monitoring is not reachable, the output says explicitly that the
`NATS` column is unusable - instead of falsely reporting the whole fleet as
disconnected.

## Roll out the MPP configuration centrally

The probes' runtime configuration is generated from
`config/mpprobe-config.yaml.template`. After a change to the template or after
a CA change, it is rolled out again over the restricted management channel -
from a probe's page in the web interface, or from the shell:

```bash
sudo ./prtg-nats probe configure mpp-probe-01
```

The sequence is transactional: the configuration is rendered and structurally
checked centrally, staged on the probe as a candidate, activated, and only
committed once `prtg.mpprobe.service` is running **and** the NATS connection
is visible in the monitoring. Otherwise the probe restores its previous state
on its own.

Probe id and access key stay unchanged, so no second probe appears in PRTG.

Renew only the public CA, without touching the configuration:

```bash
sudo ./prtg-nats probe install-ca mpp-probe-01
```

Reconfigure an already installed probe, without bootstrap access:

```bash
sudo ./prtg-nats install-mpp --nats-user mpp-probe-01
```

## Rotate the shared core/legacy-probe password

The protected account `prtg-nats` remains for the core and any legacy probes.
Its rotation still has to be coordinated in a maintenance window, because the
new password takes effect on the server immediately - every client that still
uses the old one is disconnected until it is updated:

```bash
sudo ./prtg-nats user rotate prtg-nats --server-only
```

All individual MPP accounts are untouched. Then, inside the window:

1. Enter the new password on the PRTG core.
2. Enter the new password on every remaining legacy probe.
3. Restart the core and those probes.
4. Check `sudo ./prtg-nats verify` and the PRTG sensors.

The previous state is archived for a controlled rollback under
`runtime/archive/`.

## Updating the repository

Back up runtime and JetStream first:

```bash
cd /opt/prtg-nats-server
sudo ./prtg-nats backup
git status --short
git pull --ff-only
sudo ./prtg-nats update
```

Changes to the rollout scripts or to `config/mpprobe-config.yaml.template`
should be tested before they reach real probes:

```bash
./tests/check-static.sh
./tests/e2e-mpp.sh
```

The second test rolls out a complete probe in throwaway containers and covers
exactly the failures that only show up against the real package - file
permissions for the service user, say, or a package that still counts as
installed after `apt-get remove`. In Gitea Actions it runs on every pull
request and on merges to `dev` or `main`.

An image update is its own reviewed change. Do not bump version and digest in
`compose.yaml` to `latest` unchecked.

## Classifying failures

Symptoms, causes and measures are collected in
[TROUBLESHOOTING.md](troubleshooting.md).

### Relevant logs

NATS:

```bash
sudo ./prtg-nats logs --since=30m
```

MPP:

```bash
sudo journalctl -u prtg.mpprobe.service -n 300 --no-pager
```

PRTG core:

```text
C:\ProgramData\Paessler\PRTG Network Monitor\Logs\probeadapter
```

## Rollback

1. Save the logs of NATS, the MPP and `probeadapter`.
2. Restore the backed-up MPP configuration and restart
   `prtg.mpprobe.service`.
3. Restore the PRTG configuration or NATS manager snapshot only in an
   approved maintenance window.
4. Stop the Docker NATS, but keep `runtime/` and `prtg-nats-data` for
   analysis.
