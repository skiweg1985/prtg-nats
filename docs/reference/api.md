---
title: REST API
role: developer
updated: 2026-08-03
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
nothing.

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

The reverse proxy publishes these under `/enroll/*` and rewrites them onto the
API prefix. That URL is typed into a one-liner and lands in runbooks, so it
carries no API version.

### Sensors and deployments

| | |
| --- | --- |
| `GET /sensors` | the catalogue read from `sensors/` |
| `GET /sensors/{name}` | files, checksums, which probes run it |
| `GET /sensors/{name}/parameter-schema` | the form definition |
| `POST /sensors/{name}/render-parameters` | the line to paste into PRTG |
| `GET`/`POST /deployments` | rollouts and their outcome per probe |
| `GET /deployments/{id}` | one rollout with its per-probe result |

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
