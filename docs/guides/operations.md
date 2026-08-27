---
title: Operations and maintenance
role: operator
updated: 2026-08-27
---

# Operations and maintenance

Every regular task runs through `./prtg-nats` or the web interface.

## The web interface instead of memory

Day-to-day administration - deploying sensors, rotating accounts, renewing
certificates, following jobs - happens in the web interface:

```bash
docker compose up -d
```

Then open `https://<NATS_FQDN>`. Details in
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
cd /opt/prtg-nats
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
`build --pull` and `up -d --force-recreate --remove-orphans`. After that it
waits for both health checks and verifies TLS, sign-in and reachability. The
persistent JetStream volume is not deleted.

The build is what carries a change to the web platform into the running
stack: those images come from this checkout rather than from a registry, so
recreating the containers without building leaves the old code in place.

Never use `docker compose down --volumes`. That deletes the persistent
JetStream volume.

`./prtg-nats verify --offline` includes the Compose configuration check. To
see it with variables substituted, call Compose directly - there is no
dedicated command for it any more, because it added nothing:

```bash
docker compose --project-directory /opt/prtg-nats config
```

## Monitoring the stack

Container health checks, the PRTG sensors that watch NATS and JetStream, the
fleet check and the log paths are collected in [Monitoring](monitoring.md).

## JetStream backup

```bash
sudo ./prtg-nats backup
```

The command briefly stops a running NATS, archives the volume consistently,
creates a SHA-256 checksum and starts the container again. Copy archive and
checksum from `backups/` into the protected backup storage afterwards. The web
interface offers the same backup as a job on the system page.

## Runtime export

The JetStream backup covers message data. It does not cover the part that
cannot be rebuilt: the CA and its key, the certificates, the NATS accounts,
the probe inventory, the management SSH key, the passwords of the iperf3
measurement endpoints and the platform database. That is the runtime export,
and it matters more than the JetStream backup - an endpoint stores only the
SHA-256 of its credentials, so if `runtime/iperf/` is lost the only way back
is a new password on every endpoint and every probe that uses it.

`runtime/` lives in the `prtg-nats-runtime` volume, not on a host path, so
"copy the directory" is not the answer any more:

```bash
curl -sS --cacert nats-ca.pem -b cookies https://HOST/api/v1/system/export -X POST
```

The system page offers the same as a job. Both write
`prtg-nats-runtime-<timestamp>.tar.gz` plus its checksum into the volume, and
`GET /api/v1/system/backups` lists what is there with a download link for
each. **Download it.** An export that only exists inside the volume it is
protecting protects nothing.

The download needs `system.restart`, not `system.read`: the archive contains
the CA key and every NATS password, so fetching one is disclosure and is
audited as such.

## Restore

A restore overwrites live key material, so it is deliberately manual - there
is no button for it. Into a replacement volume, never over a running one:

```bash
docker compose down
docker volume create prtg-nats-runtime-restored
docker run --rm -v prtg-nats-runtime-restored:/target -v "$PWD:/source:ro" \
  busybox:1.37.0-musl sh -c 'tar -xzf /source/ARCHIVE.tar.gz -C /tmp && cp -a /tmp/runtime/. /target/'
```

Then point the stack at it - either rename the volume or set the source in
`compose.yaml` - and bring it up. Verify the checksum against the `.sha256`
file before any of this, and keep the original volume untouched until the
restored installation has been tested end to end.

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

The web interface shows the same key on the probe's **Overview** tab behind a
**Reveal** button, for the case where the key has to go into PRTG and nobody
has a shell on the NATS host. Unlike the command above, that disclosure is
recorded in the audit trail.

## State of the whole fleet

```bash
sudo ./prtg-nats probe status --all
```

The columns come straight from the probes: `SERVICE` and `PACKAGE` from their
self-report, `CA` compares the fingerprint installed there with the active
runtime CA, `NATS` checks the actual sign-in against the NATS monitoring. The
output, the exit code and the JSON form are described in
[Monitoring](monitoring.md#the-fleet).

## One action across several probes

The probe list in the web interface takes a selection, and every action a
single probe's page offers can be applied to it: refresh, run a check, install
the CA, renew the helper, apply the configuration, fix deviations. Two filters
above the table build the selection that is usually wanted - the probes whose
helper is behind, and the ones that drifted from their desired state.

It runs as one job that takes one lock per probe, so it queues behind whatever
else is already working on one of them instead of racing it. A probe that does
not answer is recorded as failed and the job carries on with the rest; the job
log names every probe and what came of it.

An action is only offered when the selection can use it. Renewing the helper on
a probe that reports no helper version at all cannot work - it was enrolled
before updates were signed and carries no key to verify one against - so it is
left out of the selection and the confirmation says how many were.

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

This is the one call where `--nats-user` stays required: without `ADMIN@HOST`
there is no host whose inventory could name the account. Given a target, the
option can be left out for a host that is already enrolled.

## Retire a probe

Unenrolling ends the management relationship: the restricted key and the
inventory entry go, the probe itself keeps running and stays connected to
NATS. That default is deliberate - a host may leave this platform's care and
go on reporting to PRTG.

To clear what the platform put on the host as well, say so. In the web
interface the three checkboxes sit in the unenroll dialog on the probe's page;
in the shell they are options:

```bash
sudo ./prtg-nats probe unenroll mpp-probe-01 --remove-sensors --uninstall-mpp --remove-access
```

| Option | What it clears |
| --- | --- |
| `--remove-sensors` | scripts, wrappers, systemd units, sudo rules, configuration with credentials, virtual environments, interface reservations |
| `--uninstall-mpp` | the `prtgmpprobe` package, `/etc/paessler/mpprobe` with the NATS CA, the Paessler package source |
| `--remove-access` | the restricted key and the sudo rule of the management account |

The order matters and is not up to the caller: sensors and the probe software
are only reachable over the management channel, so they are cleared before it
is revoked. If either fails, the unenrollment stops with the access still in
place - a host that could not be cleaned up has to stay reachable, or nobody
gets back to it without a fresh enrollment.

Sensors are removed from both lists, the inventory's and the probe's own. The
two disagree after an interrupted rollout, and that is precisely when a sensor
would otherwise be left behind.

`--uninstall-mpp` decides how that host comes back. Only the bootstrap script
installs the package, so a plain re-enrollment from the interface finds no
`prtg.mpprobe.service` to start and stops with `probe.package_missing`. Take
the host on again with a fresh invitation and its one-liner, which installs
the package before the platform configures anything. Where the package is
already in place, the bootstrap keeps it.

The NATS account outlives all of this on purpose. Delete it separately once
the inventory is gone:

```bash
sudo ./prtg-nats user delete mpp-probe-01 --yes
```

In the web interface the same decision is the third checkbox, and it is
refused for the last remaining account.

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

**From the interface.** *Updates* shows which commit is installed, what the
branch has, and what lies between the two. The button does what the commands
below do, in that order, as a job with a log and an audit trail. The interface
is unavailable for a few minutes while the containers are replaced; the page
says so and comes back on its own.

Two things it refuses, both on purpose. A checkout with uncommitted changes -
an update would have to move over somebody's work. And any other job queued or
running - a rollout interrupted by the restart comes back looking like a
failure with no way to tell where it stopped.

An installation that has just been updated *to* the version introducing this
has no updater image yet, so that one update is still the command line. The
page says as much.

The same page also resolves the state a `git pull` on the host leaves behind -
the code is there, the images are not built from it. *Rebuild now* installs
what the checkout already holds, without fetching or moving anything.

**From the command line**, unchanged, and still the answer when the interface
is what is broken:

```bash
cd /opt/prtg-nats
sudo ./prtg-nats backup
git status --short
git pull --ff-only
sudo ./prtg-nats update
```

### When an update does not come back

Everything up to the build can be undone by putting the checkout back, and the
updater does that itself when a build fails - nothing has been replaced at
that point, and the running stack is untouched.

Once the containers have been recreated, the database has been migrated, and
moving the checkout back does **not** undo that. An older image against a
newer schema does not start at all: Alembic cannot find the revision the
database names, and the container restarts in a loop. Going back means
restoring the runtime archive the update took as its first step, from
`runtime/archive/`, and only then moving the checkout.

```bash
docker compose logs web-api
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
installed after `apt-get remove`.

An image update is its own reviewed change. Do not bump version and digest in
`compose.yaml` to `latest` unchecked.

## Classifying failures

Symptoms, causes and measures are collected in
[Troubleshooting](troubleshooting.md); the log paths of NATS, the probes and
the PRTG core are in [Monitoring](monitoring.md#logs).

## Rollback

1. Save the logs of NATS, the MPP and `probeadapter`.
2. Restore the backed-up MPP configuration and restart
   `prtg.mpprobe.service`.
3. Restore the PRTG configuration or NATS manager snapshot only in an
   approved maintenance window.
4. Stop the Docker NATS, but keep `runtime/` and `prtg-nats-data` for
   analysis.
