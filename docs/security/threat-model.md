---
title: Threat model of the web platform
role: operator
updated: 2026-08-27
---

# Threat model of the web platform

[The security model](model.md) covers the NATS server, the management channel
and the probes. This page covers what the web platform adds, and it starts with
the uncomfortable part rather than burying it.

## The API container is highly privileged

It mounts the installation directory read-write and the Docker socket. With
those two it can read every NATS password, use the management key on every
probe, and start any container on the host.

That is the same privilege the shell tooling has when an operator runs
`sudo ./prtg-nats`. The platform does not create a new class of access; it
gives an existing one a web interface, an audit trail and a role model.

It is still worth being explicit about, because a web interface is reachable
from more places than a root shell is.

### What follows from it

- **Treat the container as you would treat root on the NATS host.** Anyone who
  can execute code in it owns the monitoring backbone.
- **The port is not published to the world.** The API joins the host network
  namespace and binds to loopback. Caddy is the only service that opens a port,
  and it opens it on the same host address the rest of the stack uses.
- **Put it behind the network controls the NATS port already has.** The
  interface deserves at least the restriction `23561/tcp` has.

### Updating the stack is a path to that privilege

The interface can replace the software this installation is made of. The
updater it starts holds the Docker socket and the checkout, so whoever can
trigger an update decides what runs as root on this host.

That is not a new privilege - the API container has it already - but it is a
new way to reach it, and one that does not need a shell. So it sits behind its
own permission, `system.update`, held only by administrators and deliberately
not part of the operator role. What it can install is whatever the configured
branch of the configured repository holds: write access to that repository is
therefore write access to this host, and the deploy key it uses only needs to
be able to read.

### The Docker socket is optional

Leave the mount out of `compose.yaml` and the platform still manages every
probe, sensor, certificate and measurement endpoint. What disappears is the
server lifecycle: restarting NATS, taking a JetStream backup.

The interface hides those actions rather than offering a button that fails, and
`GET /api/v1/system/capabilities` reports why. An installation that does not
want a container-controlling web service can have everything else.

## What the platform holds

| | Where | Protection |
| --- | --- | --- |
| Web accounts | `runtime/web.db` | Argon2id, never reversible |
| Sessions | `runtime/web.db` | only the SHA-256 of the token is stored |
| Audit records | `runtime/web.db` | append-only, enforced by trigger |
| NATS passwords | `runtime/credentials/` | unchanged, read only when deploying |
| Management key | `runtime/private/ssh/` | unchanged, used never exported |

`runtime/web.db` is as sensitive as the rest of `runtime/`. It lives in the
same git-ignored, root-owned directory and belongs in the same backup.

## What never leaves the platform

- Secrets are not returned by any endpoint. The PRTG access key is the one
  exception, behind an explicit action, its own permission, and an audit record
  of who looked.
- Secrets are not written to logs. Redaction happens in the logging formatter,
  so it also covers third-party libraries that log on our behalf.
- Secrets are not stored in the job payload. Transient credentials - an SSH
  bootstrap password, for instance - are handed to the running job in memory
  and never written down.
- Nothing sensitive is kept in the browser. The session is an HttpOnly cookie;
  there is no token in JavaScript to steal.

## Where the boundaries are

**Between the browser and the API.** Every endpoint authenticates and
authorises server-side. The permission list the interface receives is used to
hide controls, never to decide anything.

**Between the API and the probes.** The platform speaks the same restricted
protocol as the shell tooling, with the same key and the same pinned
`known_hosts`. It cannot get a shell on a probe, and an unknown host key is a
refusal rather than a prompt.

**Between the API and the shell tooling.** The remaining shell calls go through
one adapter with a closed set of actions. There is no path from an HTTP request
to a command line: argv is built from the action, arguments are validated
against the same patterns the shell validates, and `shell=True` appears
nowhere. A test enforces that.

## What is not covered yet

Stated plainly so nobody assumes otherwise:

- **No OIDC.** Local accounts only in this version. See
  [ADR 0003](../architecture/decisions/0003-local-accounts-first.md).
- **No API tokens.** Automation uses the shell tooling for now.
- **No rate limit on the API as a whole.** Sign-in is throttled per account;
  everything else relies on the network controls in front of it.
- **One instance.** Sessions, jobs and the event stream live in one process.
  Running two would run every job twice.
