---
title: REST API
role: developer
updated: 2026-08-30
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
| `GET /system/update` | which version is installed, and what the branch has |
| `POST /system/update/check` | ask the repository now instead of on the hour |
| `POST /system/update` | update to the tip of the branch, or rebuild in place → job |

`GET /dashboard` is one call on purpose. A dashboard assembled from eight
parallel requests is eight chances to show a half-drawn page.

The JetStream backup and the runtime export cover different things, and the
second one matters more: message data can be lost, a CA key cannot. The export
holds the CA and its key, the certificates, the accounts, the inventory, the
management SSH key and the database. Downloading one therefore needs
`system.restart` rather than `system.read` - the archive carries every NATS
password, so fetching it is a disclosure and is audited as one.

`GET /system/update` reports three commits rather than one version, because
they diverge in ways that matter: what the running image was built from, what
the checkout on the host is at, and what the branch has. A checkout ahead of
the running image is `rebuild_pending` - somebody pulled without rebuilding -
and a repository that did not answer is `unreachable`, never `current`. An
image built without the `GIT_COMMIT` build argument reports `unknown` instead
of guessing.

It also reports when this installation was last updated from the interface, to
what, and by which job - a successful update ends by reloading the page, which
takes the log the operator was watching with it, and that id is the way back
to it. That is the question still open once the state reads current - an
installation is up to date either because it was updated an hour ago or
because nothing has changed in months. Empty on one that has only ever been
updated from the host, because nothing here recorded those.

The answer comes from a cache the background check refreshes hourly; the check
endpoint forces a fresh look. Both need `system.read`, because reading it is
`git ls-remote` and writes nothing.

`POST /system/update` takes an optional `mode`. The default, `update`, fetches
and moves the checkout to the branch tip before building. `rebuild` builds and
replaces what the checkout already holds, fetching nothing and moving nothing -
for the state a `git pull` on the host leaves behind when nobody follows it
with a build. Its `fetch` and `checkout` steps are reported as skipped rather
than quietly counted as done.

Starting either needs `system.update`, held only by administrators: the
updater runs with the Docker socket, so whoever triggers one decides what runs
as root on the host. It is refused while any other job is queued or running -
a rollout interrupted by the restart comes back looking like a failure with no
way to tell where it stopped - and refused over a checkout with uncommitted
changes. The job detaches partway through and is completed by the process that
comes back up, so its status passes through `detached` on the way. See
[ADR 0007](../architecture/decisions/0007-update-the-stack-from-the-interface.md).

### Probes

| | |
| --- | --- |
| `GET /probes` | the table, entirely from cached state |
| `GET /probes/{id}` | detail with sensors and deviations |
| `PATCH /probes/{id}` | display name, notes, labels, the PRTG tick |
| `POST /probes/{id}/refresh` | ask this probe now, synchronously |
| `GET`/`PUT /probes/{id}/desired-state` | what should be true - no UI, for automation |

Two deviation kinds cover states a green job hides: `interface_missing` (a
sensor that needs a wireless interface is installed and the probe holds none
for it) and `helper_inactive` (the sensor's privileged helper socket is not
listening). Neither joins the automatic reconciliation plan - reserving an
interface is a decision about a specific interface, and an inactive helper
wants a look rather than a blind redeploy. Every deviation also carries
`remediation`, a token naming the fix.
| `POST /probes/{id}/reconcile?dry_run=true` | the plan, before anything runs |
| `POST /probes/{id}/sensors/{name}/remove` | remove one sensor → job |
| `DELETE /probes/{id}` | unenrol the probe → job |
| `GET /probes/{id}/access-key` | the PRTG access key, audited |

`prtg_registered` on the PATCH is the operator's own tick for the two steps
the platform cannot take or see: the access key entered in PRTG and the probe
approved there. `true` records who ticked and when, `false` clears it, absent
leaves it. The summary carries `prtg_registered`, the detail `..._at`/`.._by`,
and the dashboard counts `probe_pending` (stuck mid-enrolment) and
`probe_prtg_missing` (enrolled here, never registered over there).

Actions go through `POST /probes/actions/{action}` with a body of
`{"probe_ids": [...]}` - one job holding one lock per probe, the shape a
sensor rollout already uses. A single id is a legitimate selection; the
per-probe twin routes (`/probes/{id}/validate` and friends) are gone, they
did the same thing under a second path. **Breaking** for callers of the old
paths; already-queued jobs keep running, the worker reads both payload
shapes:

| | |
| --- | --- |
| `POST /probes/actions/refresh` | ask a selection now → job |
| `POST /probes/actions/validate` | → job |
| `POST /probes/actions/install-ca` | → job |
| `POST /probes/actions/helper-update` | renew the helper on a selection → job |
| `POST /probes/actions/configure` | roll the configuration out → job |
| `POST /probes/actions/reconcile` | execute the plan, never a preview → job |

Each takes the permission its single-probe route takes. Every id is resolved
before the job exists, so an unknown one fails the request with `404` and
nothing has run. One probe that cannot be reached does not take the rest of the
selection with it: the job records an outcome per probe, finishes
`partially_successful` and carries `succeeded` and `failed` in its result. A
selection of exactly one behaves like the single-probe route - it fails with
that probe's own error code rather than with a count.

`POST /probes/actions/refresh` is a job where `POST /probes/{id}/refresh` is
synchronous. One round trip is worth holding a request open for; a dozen over
SSH is not.

`GET /probes` never contacts a probe. An unreachable host must not make the
list slow, and every row reports its own freshness.

`GET /probes/{id}/access-key` answers with `nats_username` and `access_key`
and takes `credential.read`. It is the only endpoint that returns the value:
`GET /probes/{id}` reports `access_key_present` and nothing more, and job logs
mask it like any other secret. Every call is recorded as `credential.reveal`,
so the trail says who looked and when - never at what.

Every probe reports `helper_version` and `helper_outdated`. The latter is true
when the reported version is below `CURRENT_HELPER_VERSION`;
`MINIMUM_HELPER_VERSION` separately marks the protocol compatibility boundary.
`POST /probes/{id}/helper-update` sends the helper the platform ships, signed
with the key in `runtime/private/`; the probe verifies it before it replaces
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
stored, the same way sessions are kept. One open invitation per account:
minting a second one for the same `nats_username` is refused with a conflict
until the first is revoked or expires, because two of them redeemed on two
hosts would overwrite each other's inventory under one name. `expected_host` is optional: without
it the address the host reports from is used, which is right on a flat network
and wrong behind NAT. Either way an address that another probe already claims
is refused with `probe.host_already_enrolled`, because the management access
belongs to the host and retiring one entry would revoke it for both.

`overlay_bootstrap` is for a probe that cannot reach this platform at all - a
site with no site-to-site tunnel. It needs the overlay to be on, and is
refused otherwise. The rendered script then builds the tunnel before its first
request, and carries the probe's private WireGuard key to do it, so the script
is a credential rather than a command
([ADR 0010](../architecture/decisions/0010-enrolling-a-probe-over-the-tunnel.md)).
The creation response then carries the whole script in `command` and sets
`carries_secret`, instead of a one-liner that fetches it - the fetch would need
the tunnel the script builds. The peer is reserved when the invitation is
minted and given back when it is revoked, redeemed or expires. The mode is `on` regardless of the site default:
`auto` would leave NATS off the tunnel until three checks a minute apart have
failed, and this probe has no direct path for those checks to find.

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
| `PUT /iperf-endpoints/{name}/credentials` | → job: store a foreign password and refresh its current holders |
| `POST /iperf-endpoints/{name}/deploy` | → job: hand the credentials to named probes |
| `POST /iperf-endpoints/{name}/revoke` | → job: take them off named probes |
| `DELETE /iperf-endpoints/{name}` | → job: take it off the probes and its host |

`deploy` and `revoke` both take `{"probes": ["mpp-berlin", …]}` and need
`sensor.deploy`, not `iperf.manage`: they write a credential to a probe, or
take one away, rather than change the endpoint. A name that is not enrolled is
refused rather than skipped, so a typo does not become a job that quietly did
less than it was asked. What they write is the same assignment a sensor rollout
reads, which is why a probe revoked here does not get the credentials back with
the next deployment.

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
use. Such an endpoint reports `managed: false`; `DELETE` only forgets it.

The operator of a foreign endpoint changes its password on that host first.
The authenticated action below then replaces only the protected copy held by
this platform and refreshes every probe already assigned to the endpoint:

```http
PUT /api/v1/iperf-endpoints/provider/credentials
Content-Type: application/json

{"password": "…"}
```

The password must not be empty. Because runtime records and probe profiles are
line-based, leading or trailing whitespace and line, paragraph or control
characters are refused rather than stored in a changed form.

It needs `iperf.manage`, refuses a managed endpoint with `409`, and does not
contact the foreign host. The existing user name and public key are preserved.
The password is write-only: it is handed to the worker outside the persisted
job payload and appears in neither the response, audit record nor job log. The
job updates the endpoint profile and reconciles the `default` alias on every
current holder. Its result names `succeeded` and `failed` probes, so one
unreachable probe produces a partial success rather than hiding the rest.

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

The invitation path is the third way onto the endpoint list, for a host this
platform cannot reach over SSH - behind NAT or a firewall. The interface
offers it as *By invitation* on the iperf page; the host only has to reach
this platform once, with the command from the creation response.

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
| `GET /sensors/{name}` | files, checksums, who runs it at which version |
| `POST /sensors/{name}/render-parameters` | the line to paste into PRTG |
| `GET`/`POST /deployments` | rollouts and their outcome per probe |
| `GET /deployments/{id}` | one rollout with its per-probe result |

The sensor detail carries `installations`, one entry per probe reporting the
sensor: `{"probe", "version", "current"}`. It replaced the bare
`probes: ["..."]` list - "outdated on twelve" is only useful together with
which twelve. Each deployment target records `previous_version`, so a rollout
answers "was v3, is now v4" per probe.

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

Retrying a failed or partial `sensor.deploy` is the one exception to "same
inputs": the retry gets a deployment row of its own and targets only the
probes that failed. Re-running into the original row would overwrite the
finished half of the history while its link kept pointing at the first job.
A cancelled rollout marks the probes it never reached as `cancelled` instead
of leaving them queued forever.

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
| `GET /iperf-endpoints/{name}` | one of them, same shape as a list entry |
| `GET /overlay` | the hub, its peers and the path each one is on |
| `POST /overlay/enable` | turn the overlay on and start the hub |
| `POST /overlay/disable` | stop it; every peer keeps its address |
| `POST /overlay/peers` | → job, put probes on the overlay |
| `POST /overlay/peers/mode` | → job, change when their NATS traffic takes the tunnel |
| `POST /overlay/peers/remove` | → job, take them off again |
| `POST /overlay/peers/refresh` | → job, ask which path they are on now |
| `GET /audit-events` | filter by actor, action, object, result, time |

`enable` and `disable` are synchronous rather than jobs: they are a settings
form, and the mistakes they can make - an endpoint that is the NATS address, a
subnet probes already hold addresses from - are worth refusing in front of the
person typing rather than in a job log. Both need `overlay.enable`, which only
the administrator role carries; they create and remove a container with
network-admin rights in this host's network namespace.

Every peer carries both a `mode` and a `last_state`, and they answer
different questions. The mode is what the probe was told: `off`, `auto` or
`on`. The state is what it last reported doing - `direct`, `tunnel`, `down`,
`no_handshake`. A probe in `auto` reporting `tunnel` is working, and it also
means its ordinary route is down.

The four actions all take `probe_ids`, a list, because moving a site onto the
tunnel is the realistic operation. `POST /overlay/peers/mode` additionally
takes `force`: a switch to `off` goes over the probe's ordinary address, and
without `force` it is refused when the probe does not answer there.

An endpoint carries `managed`. It is false for one somebody else operates,
registered here rather than set up from here: its password is not ours to
rotate, and removing it takes nothing off that host.

`holders` says who holds the credentials, and what a sensor object in PRTG has
to say to use them. It is a list rather than the bare names it replaced because
the answer belongs to the pair, not to the endpoint:

```json
{"probe": "mpp-berlin", "endpoints_held": 1,
 "uses_default_alias": true, "parameter_line": ""}
```

`endpoints_held` counts the registered endpoints that probe holds in total. At
one it also carries the `default` profile alias, so the sensor reads address,
port and user out of it and `parameter_line` is empty - which is the answer,
not a missing one. From two on the alias is gone and the line names the
profile, `--profile berlin`. The selector comes from the sensor's own parameter
declaration, so a sensor calling it `--variant` is quoted with `--variant`.

### Web accounts

`GET`/`POST /users`, `PATCH`/`DELETE /users/{id}`. The first administrator is
created through `POST /auth/setup` instead, which is refused once one exists.
Which role may call what is in [Roles and permissions](../web/roles.md).

### Removed endpoints

**Breaking, deliberately:** `GET /auth/me` (a strict subset of
`GET /auth/state`), `GET /probes/{id}/deviations` (the detail carries the
same list), `GET /sensors/{name}/parameter-schema` (the sensor detail
carries the schema), the per-probe action twins (`/probes/{id}/validate`
and friends - the `/probes/actions/*` routes with a one-id selection do the
same), and the `stack_update` field of `GET /system/capabilities` (it cost
Docker calls on every page load; `GET /system/update` answers the question
when asked). Each had no caller in the interface or the tooling.

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
