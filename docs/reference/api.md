---
title: REST API
role: developer
updated: 2026-08-04
---

# REST API

Everything lives under `/api/v1`. The OpenAPI schema is served at
`/api/openapi.json`, and outside production there is a browsable version at
`/api/docs`.

## Authentication

A session cookie, set by `POST /auth/login`. It is HttpOnly, Secure and
SameSite=Lax; there is no token to attach to a request by hand.

```http
POST /api/v1/auth/login
Content-Type: application/json

{"username": "admin", "password": "…"}
```

`GET /auth/state` is the first call a client makes. It answers whether an
account exists at all, whether this caller is signed in, and who they are - so
the interface never guesses between the login screen, the setup wizard and the
application.

| | |
| --- | --- |
| `GET /auth/state` | account present, signed in, who |
| `POST /auth/setup` | create the first administrator - refused once one exists |
| `POST /auth/login` | sign in, sets the session cookie |
| `POST /auth/logout` | end the session |
| `GET /auth/me` | the signed-in principal and its permissions |
| `POST /auth/change-password` | change one's own password |

Repeated failures are throttled with a lockout that doubles per attempt; the
bounds are `login_max_attempts`, `login_lockout_base_seconds` and
`login_lockout_max_seconds` in
[the configuration reference](configuration.md#sign-in-throttling).

## Actions return a job

Anything that takes time answers `202` with a job to watch:

```json
{
  "job_id": "01JC…",
  "status": "queued",
  "events_url": "/api/v1/jobs/01JC…/events"
}
```

`events_url` is a server-sent event stream. It replays what has already
happened before switching to live, so a client that connects late loses
nothing. A client that lost the stream reconnects with `?after=<sequence>` of
its last line and gets the gap replayed the same way; the interface does this
with a backoff rather than leaving the log where it stopped.

## Endpoints

### System

| | |
| --- | --- |
| `GET /system` | site settings, NATS state, containers, certificates |
| `GET /system/capabilities` | what this installation can do |
| `GET /dashboard` | everything the landing page needs, in one request |
| `POST /system/setup` | initialise `runtime/` on a fresh installation → job |
| `POST /system/verify` | run the stack verification → job |
| `POST /system/backup` | JetStream backup → job |
| `POST /system/export` | archive `runtime/` → job |
| `GET /system/backups` | the archives in the volume, newest first |
| `GET /system/backups/{name}` | download one, audited |
| `POST /system/restart` | restart NATS → job |

`GET /dashboard` is one call on purpose. A dashboard assembled from eight
parallel requests is eight chances to show a half-drawn page.

The JetStream backup and the runtime export cover different things, and the
second one matters more: message data can be lost, a CA key cannot. The export
holds the CA and its key, the certificates, the accounts, the inventory, the
management SSH key and the database. Downloading one therefore needs
`system.restart` rather than `system.read` - the archive carries every NATS
password, so fetching it is a disclosure and is audited as one.

### Probes

| | |
| --- | --- |
| `GET /probes` | the table, entirely from cached state |
| `GET /probes/{id}` | detail with sensors and deviations |
| `PATCH /probes/{id}` | display name, notes, labels |
| `POST /probes/{id}/refresh` | ask this probe now, synchronously |
| `GET`/`PUT /probes/{id}/desired-state` | what should be true |
| `GET /probes/{id}/deviations` | what differs |
| `POST /probes/{id}/reconcile?dry_run=true` | the plan, before anything runs |
| `POST /probes/{id}/configure` | roll the configuration out → job |
| `POST /probes/{id}/install-ca` | → job |
| `POST /probes/{id}/validate` | → job |
| `POST /probes/{id}/helper-update` | renew the management helper → job |
| `POST /probes/{id}/sensors/{name}/remove` | remove one sensor → job |
| `DELETE /probes/{id}` | unenrol the probe → job |
| `GET /probes/{id}/access-key` | the PRTG access key, audited |

`GET /probes` never contacts a probe. An unreachable host must not make the
list slow, and every row reports its own freshness.

`GET /probes/{id}/access-key` answers with `nats_username` and `access_key`
and takes `credential.read`. It is the only endpoint that returns the value:
`GET /probes/{id}` reports `access_key_present` and nothing more, and job logs
mask it like any other secret. Every call is recorded as `credential.reveal`,
so the trail says who looked and when - never at what.

Every probe reports `helper_version` and `helper_outdated`. `POST
/probes/{id}/helper-update` sends the helper the platform ships, signed with
the key in `runtime/private/`; the probe verifies it before it replaces
anything. A probe that reports no version at all predates signed updates,
carries no key to verify against and answers this endpoint with
`probe.helper_outdated` - it has to be enrolled once more over the bootstrap
path. See
[ADR 0006](../architecture/decisions/0006-signed-helper-updates.md).

`DELETE /probes/{id}` removes the management access and the inventory; the
probe keeps running and stays connected. Three query parameters clear what it
otherwise leaves behind, each with its own permission and its own step in the
job:

| Parameter | What it adds | Permission |
| --- | --- | --- |
| `remove_sensors=true` | every sensor the inventory or the probe knows of | `sensor.remove` |
| `uninstall_mpp=true` | the `prtgmpprobe` package, its configuration with the NATS CA, and the Paessler package source | `probe.update` |
| `delete_account=true` | the NATS account, once no inventory names it | `credential.rotate` |

The order is fixed and not negotiable: sensors and the probe software both
need the management channel, so they run before it is revoked, and a failure
in either aborts the job with the access still in place - a probe that could
not be cleaned up has to stay reachable. The account goes last, because the
server refuses to delete one an inventory still points at. Deleting the last
remaining account is refused before the job is created, not once the probe has
already lost its access.

`uninstall_mpp=true` decides how the host comes back. Nothing but the
bootstrap script installs the package, so taking that host on again means a
fresh invitation and the one-liner on its console - see
[Enrolling a probe](#enrolling-a-probe) below.

### Enrolling a probe

A probe enrols itself. The platform mints a single-use invitation and returns
the command to run on the host; the host installs the restricted management
access and reports back. No administrator password of the target ever passes
through this API, and nothing outbound happens until the host has answered.

| | |
| --- | --- |
| `POST /probes/enrollment/tokens` | mint an invitation, returns the command |
| `GET /probes/enrollment/tokens` | invitations that could still be used |
| `GET /probes/enrollment/tokens/{id}` | one invitation, open or spent |
| `DELETE /probes/enrollment/tokens/{id}` | revoke one |

The token is in the creation response and nowhere else - only its SHA-256 is
stored, the same way sessions are kept. `expected_host` is optional: without
it the address the host reports from is used, which is right on a flat network
and wrong behind NAT. Either way an address that another probe already claims
is refused with `probe.host_already_enrolled`, because the management access
belongs to the host and retiring one entry would revoke it for both.

Watch a pending enrolment on the single invitation, not on the listing. The
callback below redeems the invitation and writes the `job_id` it started in
one request, so the record leaves the listing at the same moment it names the
job - and `job_id` is the signal that the host has reported in and there is a
job to follow. A spent invitation stays readable by id, with `redeemed_at`,
`revoked_at` and the job; the token never appears again either way.

These three are reached by the host being enrolled, which has no session:

| | |
| --- | --- |
| `GET /enroll/{token}/bootstrap.sh` | the rendered script, no auth |
| `GET /enroll/{token}/asset/{name}` | one of a fixed set of scripts, no auth |
| `POST /enroll/{token}/callback` | the host reports in, no auth |

The token is the whole authorisation, which is why it is single-use, expiring
and revocable. Fetching the script does not spend it - a run that fails
halfway has to be retryable - and the callback does. An invitation that is
unknown, expired, spent or revoked answers the same `enrollment.token_invalid`
either way: the caller is unauthenticated, and a distinct "expired" would
confirm that the token existed.

The callback reports `package_installed`, and `package_error` when that is
`false` - the tail of what the installer said, so the reason survives the
console it was printed on. The bootstrap reports back either way: the
management access is in place by then, and a host that stayed silent about a
failed package would be harder to help, not easier.

Enrolment installs no package. It arrives with the bootstrap script and with
nothing else, so a host without it is refused with `probe.package_missing`
before the inventory is written, carrying `package_error` as its detail. The
same refusal guards every configuration - enrol, configure, reconcile and
rotate alike - because without the package there is no `prtg.mpprobe.service`
to restart and the transaction can only fail. This is the state a retirement
with `uninstall_mpp=true` leaves behind: the way back is a fresh invitation,
not another run of the same job.

The reverse proxy publishes these under `/enroll/*` and rewrites them onto the
API prefix. That URL is typed into a one-liner and lands in runbooks, so it
carries no API version.

### Setting up an iperf measurement endpoint

Three ways in, because the topology decides which one is possible. All three
write the same record, so everything downstream treats the result identically.

| | |
| --- | --- |
| `POST /iperf-endpoints/host-keys` | read a host's SSH keys, without signing in |
| `POST /iperf-endpoints` | → job: sign in once and install the access |
| `POST /iperf-endpoints/register` | record one somebody else operates |
| `POST /iperf-endpoints/{name}/rotate` | → job: new password, carried to the probes |
| `DELETE /iperf-endpoints/{name}` | → job: take it off the probes and its host |

**Push** is the usual way and the only one that works when the endpoint cannot
reach this installation - which is most of the time for a host on a public
address while the platform sits on an internal network. It needs the reverse to
be true, and that is not an extra demand: the management channel is SSH from
here to there, so an endpoint this platform cannot reach could not be managed
at all.

The host keys come first and separately. `POST /iperf-endpoints/host-keys`
opens a connection without signing in - what `ssh-keyscan` does - and returns
the keys with their fingerprints. Those keys go into `POST /iperf-endpoints`
as `host_keys`, where they are pinned *before* the sign-in: that is what makes
the acceptance count for anything, and it is why the field is required. The
scan is the one route that makes this server talk to an address a caller names,
so it takes `iperf.manage` rather than `iperf.read`.

The administrator credentials are in the request body and go no further. They
never enter the job payload, which is a row in the database, and never the log;
they are handed to the worker out of band and taken once, when the job starts.
A password or a private key is required - one of the two, not both.

**Registration** covers an endpoint somebody else operates. No job, nothing
installed, nothing reached: the record is the whole of what this platform has.
Credentials are all or nothing - a user name needs both its password and the
endpoint's public key, or every sensor run would fail on credentials it cannot
use. Such an endpoint reports `managed: false`, cannot be rotated from here,
and `DELETE` only forgets it.

**Rotation** sets a new password and carries it to every probe holding this
endpoint, in one job. The second half is not a follow-up somebody might skip:
from the moment the endpoint accepts the new password, every probe still on the
old one is locked out, so refreshing them is the repair of the state the
rotation just created. The stored public key survives it - the endpoint's key
pair is untouched by a credential change, and losing the copy would cost every
probe the ability to encrypt what it sends.

**Removal** goes in the only order that strands nothing: the probes lose the
credentials, then the endpoint stops accepting them, then the access that did
the work removes itself, then the record goes. Reversed, a failure halfway
would leave sensors measuring against a host that refuses them with no way left
to tell the probes. Each step tolerates a host that has already gone - a
machine decommissioned last week still has a record here, and refusing to clean
that up would be a record nobody can remove. `?keep_service=true` forgets the
endpoint here and leaves the iperf3 service running. The package is never
uninstalled; something else on that host may be using it.

**Invitation** is the third way: the endpoint fetches a bootstrap script and
reports in, exactly as a probe does. It needs this platform to be reachable
from there, so it fits an endpoint on the same network - and in exchange no
administrator credential passes through this API at all.

| | |
| --- | --- |
| `POST /iperf-endpoints/enrollment/tokens` | mint an invitation, returns the command |
| `GET /iperf-endpoints/enrollment/tokens` | invitations that could still be used |
| `GET /iperf-endpoints/enrollment/tokens/{id}` | one invitation, open or spent |
| `DELETE /iperf-endpoints/enrollment/tokens/{id}` | revoke one |
| `GET /enroll/{token}/iperf-bootstrap.sh` | the rendered script, no auth |
| `POST /enroll/{token}/iperf-callback` | the endpoint reports in, no auth |

`name` is required and has to be free: it is also the profile name the
credentials carry on every probe, so two endpoints under one name would
overwrite each other's credentials on every probe measuring against both. A
name already registered is refused with `common.conflict`.

`ssh_source_cidr` names the network the endpoint will accept this platform
from. It falls back to `IPERF_SSH_SOURCE_CIDR`, and an invitation with neither
is refused - see
[Management channel](configuration.md#management-channel) for why there is no
default. A bare address gains its host prefix; host bits inside a prefix are
refused rather than masked away, because masking would widen the rule beyond
what was typed. Several networks are allowed, separated by commas.

Nothing secret is in the rendered script, unlike the probe's relationship to
its own: fetching it does not spend the invitation, so it stays readable for as
long as the token lives. The endpoint's password is generated when the job
runs and travels over the management channel.

Each kind of invitation serves only its own script. A probe's token is refused
at `iperf-bootstrap.sh` and an endpoint's at `bootstrap.sh` - the probe
bootstrap installs a management user with the probe's rights, which on a host
that only measures would be rights nobody decided to grant.

The job that follows asks the endpoint about itself, sets it up, and only then
writes `runtime/iperf/NAME.env` and `NAME.pem` - the same files
`./prtg-nats iperf-server` has always written, so an endpoint set up from the
browser is one the command line can deploy, show and revoke. A failure before
the record is written leaves nothing behind; the way back is to enrol again,
which sets a fresh password.

### Sensors and deployments

| | |
| --- | --- |
| `GET /sensors` | the catalogue read from `sensors/` |
| `GET /sensors/{name}` | files, checksums, which probes run it |
| `GET /sensors/{name}/parameter-schema` | what the sensor declares: parameters, settings, credentials, files |
| `POST /sensors/{name}/render-parameters` | the line to paste into PRTG |
| `GET`/`POST /deployments` | rollouts and their outcome per probe |
| `GET /deployments/{id}` | one rollout with its per-probe result |

#### Test interfaces

A sensor that measures Wi-Fi needs a radio interface of its own. Reserving
one takes it away from NetworkManager permanently and cuts whatever it was
carrying, so the choice is made against what the probe reports rather than
against a name typed from memory.

| | |
| --- | --- |
| `GET /probes/{id}/wireless-interfaces` | the probe's radio interfaces, asked live |
| `POST /probes/{id}/sensors/{name}/interfaces/{interface}/reserve` | hand one to a sensor - returns a job |
| `POST /probes/{id}/sensors/{name}/interfaces/{interface}/release` | give it back to normal management - returns a job |

The listing states facts and passes no judgement: `reserved_by` names the
sensor that already holds an interface, `carries_default_route` says whether
taking it would cut the probe off, `connection` names what it is on right
now. Refusing belongs to the probe, which rejects an interface that is not
wireless, does not exist, or carries the host's only default route - that
refusal arrives here as a failed job with its reason.

Reading needs `sensor.read`, reserving and releasing need `sensor.configure`.
Both require helper version 5; an older probe answers that it does not know
the request, and the reservation stays a shell command there.

#### Variants

A variant is the settings, credentials and files a sensor needs for one
purpose - one SSID, one site. It is stored as a profile under
`runtime/sensor-profiles/` and deployed to the probes it is assigned to.

| | |
| --- | --- |
| `GET /sensors/{name}/profiles` | the variants, with their probes and files |
| `GET /sensors/{name}/profiles/{profile}` | settings and the **names** of the stored credentials |
| `PUT /sensors/{name}/profiles/{profile}` | store one and deploy it - returns a job |
| `PUT /sensors/{name}/profiles/{profile}/files/{key}` | upload a certificate or key, base64 in `content_base64` |
| `DELETE /sensors/{name}/profiles/{profile}` | remove it here and from every probe - returns a job |

Writing needs `sensor.configure`; reading needs `sensor.read`. A sensor that
declares no settings, credentials or files answers `422` on all of these -
`aruba-uplink` takes its host and credentials from PRTG placeholders instead.

**A credential is never returned.** `GET` answers with the settings, plus
`secrets_set` naming which credentials are stored. An empty credential in a
`PUT` therefore means "leave it as it is", not "clear it" - otherwise every
edit of a variant would wipe its password.

**The values never reach the job.** The `PUT` writes them to the runtime
directory synchronously and the job it creates carries only the sensor, the
variant name and the probes; the handler reads the values back itself. Nothing
about a credential is written to SQLite, the job log or the audit trail - the
audit entry records the field names.

Uploaded files are described but never handed back, not even a public
certificate: the response carries name, size, fingerprint and the path the
file has on the probe. That path is what the platform writes into the profile,
and what the sensor script reads to find the file.

### Jobs

| | |
| --- | --- |
| `GET /jobs` | filter by `status`, `type`, `target_id` |
| `GET /jobs/{id}` | detail with steps and result |
| `GET /jobs/{id}/log` | the stored log |
| `GET /jobs/{id}/events` | server-sent events, `?after=<sequence>` |
| `POST /jobs/{id}/retry` | a new job with the same inputs |
| `POST /jobs/{id}/cancel` | ask it to stop |

### NATS accounts

| | |
| --- | --- |
| `GET /credentials` | the accounts, and what each one is used by |
| `POST /credentials` | create an account with a random password |
| `POST /credentials/{username}/rotate` | rotate server and probe together → job |
| `DELETE /credentials/{username}` | delete an account - refused while a probe is enrolled on it |
| `GET /credentials/{username}/reveal` | the cleartext password, audited |

### Infrastructure and audit

| | |
| --- | --- |
| `GET /certificates` | CA and server certificate |
| `POST /certificates/server/renew` | → job, restarts NATS |
| `GET /iperf-endpoints` | measurement endpoints and who holds credentials |
| `GET /audit-events` | filter by actor, action, object, result, time |

An endpoint carries `managed`. It is false for one somebody else operates,
registered here rather than set up from here: its password is not ours to
rotate, and removing it takes nothing off that host.

### Web accounts

`GET`/`POST /users`, `PATCH`/`DELETE /users/{id}`. The first administrator is
created through `POST /auth/setup` instead, which is refused once one exists.
Which role may call what is in [Roles and permissions](../web/roles.md).

## Errors

One envelope, always:

```json
{
  "error": {
    "code": "probe.unreachable",
    "message_key": "errors.probe.unreachable",
    "params": { "probe": "mpp-probe-01" },
    "fields": [],
    "details": "ssh: connect to host … port 22: Connection timed out",
    "correlation_id": "01JC…",
    "retryable": true
  }
}
```

The server never returns a translated sentence. `message_key` and `params` are
resolved by the client; `details` is technical output and is never translated.
See [Languages and translation](../web/i18n.md).

`correlation_id` is echoed in the `X-Correlation-ID` response header and
appears in the server logs, in the job the request started and in the audit
record - one identifier follows an action all the way down.

## Status codes

| | |
| --- | --- |
| `200` / `201` / `204` | done |
| `202` | accepted, a job is running |
| `401` | not signed in |
| `403` | signed in, permission missing - the response names which |
| `404` | not found |
| `409` | conflicts with the current state, including a busy resource |
| `422` | validation failed, with the offending fields named |
| `502` / `504` | a probe or the NATS server did not answer |

## Observability

`GET /health` is liveness and touches no dependency. `GET /ready` reports each
dependency separately, so a missing Docker socket is visible as what it is -
reduced capability, not a failure.
