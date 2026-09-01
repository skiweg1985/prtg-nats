---
title: Roles and permissions
role: operator
updated: 2026-09-01
---

# Roles and permissions

Permissions are fine-grained; roles are only bundles of them. That keeps the
door open for a custom role later without touching a single endpoint.

## The three roles

| Role | May |
| --- | --- |
| **Viewer** | read everything: status, probes, sensors, jobs, logs, audit records, the availability dashboard |
| **Operator** | also deploy sensors, apply configuration, run checks, retry and cancel jobs, manage measurement endpoints, edit the watch list |
| **Administrator** | also manage accounts, rotate credentials, renew certificates, add and remove probes, change system settings, restart services, and update the stack |

The line between operator and administrator is deliberate: an operator keeps
the fleet running, an administrator changes what the fleet is.

## The permissions behind them

```text
probe.read        probe.create     probe.update     probe.delete
probe.reconcile
sensor.read       sensor.deploy    sensor.remove    sensor.configure
deployment.read   deployment.create
job.read          job.retry        job.cancel
credential.read   credential.rotate
certificate.read  certificate.renew
iperf.read        iperf.manage
watch.read        watch.manage
system.read       system.restart   system.settings  system.update
audit.read        user.manage      role.manage
```

`watch.read` is the one permission worth handing out on its own. The
availability dashboard is written for people who never touch a probe - somebody
at a shop who needs to know whether the till printer is on - and a viewer
account gives them that and nothing else. `watch.manage` adds editing the watch
list, which is operating the fleet.

`iperf.manage` covers the endpoint itself - setting one up, rotating its
password, taking it away. Handing its credentials to a probe or taking them
back needs `sensor.deploy` instead: that writes to a probe, and who may change
what a probe holds is a different question from who may run a measurement
endpoint.

## How it is enforced

Every endpoint carries a permission dependency on the server. The interface
uses the same list only to hide controls the caller cannot use; hiding a button
is a courtesy, and the server refuses the request either way.

Two tests keep that true:

- one walks the router and fails if any endpoint has no permission dependency,
  with a short list of documented exceptions such as signing in;
- one signs in as each role in turn and checks that the boundaries hold end to
  end.

A refusal names the permission that was missing, so an operator can ask for the
right thing rather than reporting "access denied".

## The last administrator

An account cannot be stripped of the administrator role, deactivated or deleted
when it is the last one holding it. Losing it would mean editing the database
by hand to get back in, on a machine whose job is to keep monitoring alive.

## Sessions

Sign-in creates a session on the server and hands the browser an opaque
identifier in an HttpOnly, Secure, SameSite=Lax cookie. Nothing sensitive is
kept in JavaScript, and an administrator can end a session immediately.

- A session expires after twelve hours, or after sixty minutes of inactivity.
- Changing a password ends every other session of that account.
- Five failed attempts start an exponential back-off, capped at fifteen
  minutes. It is a delay, never a permanent lock-out that would need a second
  administrator to undo.

## What is recorded

Every action that changes something writes an audit record: who, what, which
object, the state before and after, the result, the job it started, and the
source address. Failed sign-ins are recorded too - they are the only trace an
attempted intrusion leaves.

Audit records cannot be changed or deleted. A database trigger refuses `UPDATE`
and `DELETE` on the table, so a maintenance script reaching past the
application fails as well.

Secrets never reach an audit record. Values are redacted by field name and by
shape - passwords, tokens, private keys, bcrypt hashes and generated hex
secrets - and a test throws a structure full of secrets at the writer and fails
if any of it survives.

## OIDC

Not in this version. Local accounts are the built-in way in because this
platform is also the recovery path for the NATS backbone: it has to work when
the identity provider is exactly what is broken. OIDC is planned beside local
accounts, never instead of them - see
[ADR 0003](../architecture/decisions/0003-local-accounts-first.md).
