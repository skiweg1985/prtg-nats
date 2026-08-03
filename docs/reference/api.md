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
| `POST /system/restart` | restart NATS → job |

`GET /dashboard` is one call on purpose. A dashboard assembled from eight
parallel requests is eight chances to show a half-drawn page.

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
| `POST /probes/{id}/sensors/{name}/remove` | remove one sensor → job |
| `DELETE /probes/{id}` | unenrol the probe → job |
| `GET /probes/{id}/access-key` | the PRTG access key, audited |

`GET /probes` never contacts a probe. An unreachable host must not make the
list slow, and every row reports its own freshness.

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

> [!WARNING]
> These routes are implemented in `app/api/v1/routes/credentials.py` but the
> router is not registered in `app/api/v1/router.py`, so they currently answer
> `404`. The credentials page of the web interface depends on them.

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
