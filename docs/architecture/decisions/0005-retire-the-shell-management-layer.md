---
title: Retire the shell management layer
role: developer
updated: 2026-08-03
status: accepted
---

# 5. Retire the shell management layer

## Context

The platform started as roughly 13,000 lines of bash. The web platform
reimplemented the probe-facing half natively by speaking the helper protocol
(ADR 0001); the server-facing half - certificates, NATS accounts, backups,
verification - still ran as shell scripts, called by the web platform through
a subprocess adapter.

That left every server-side operation implemented twice conceptually: once as
the script, once as the adapter that wraps, times out, parses and redacts it.
And it left the platform depending on tools inside its container - openssl,
docker-cli, a throwaway nats-box container for every bcrypt hash - that
existed only to run those scripts.

## Decision

The server-side operations are native Python, and the scripts are gone:

| Retired | Replaced by |
| --- | --- |
| `init-runtime.sh` | `ProvisioningService.initialise_runtime` (`cryptography`) |
| `renew-server-certificate.sh` | `Pki.issue_server_certificate` |
| `manage-users.sh` | `NatsRuntime` (`bcrypt`, same file formats) |
| `rotate-password.sh` | the rotation job: server side plus probe reconfigure |
| `verify.sh`, `smoke-test.sh` | `StackVerification`, ending in a real NATS login |
| `backup-jetstream.sh` | volume streaming over the Docker API |
| `verify-persistence.sh` | covered by verification and the backup checksum |
| `tui.sh` | the web interface |

The remaining shell is exactly what has a reason to be shell:

- `prtg-nats` - bootstrap (`.env` dialog, compose lifecycle) and the
  interactive SSH rollout (`install-mpp`, `probe`, `sensor`, `iperf-server`);
- `install-mpp.sh` and `prtg-nats-probe-helper` - they run **on the probe**;
- `libexec/common.sh` and `mpp-config.sh` - the shared library those need.

Recovery keeps a command line: `python -m app.ops` drives the same services,
and `prtg-nats init|user|verify|renew-certificate|backup` delegate to it. One
implementation, two entry points - the "CLI as a thin client" end state the
project brief asked for.

## Consequences

**Good.** One implementation of every server-side rule. The refusals the shell
made - never overwrite runtime state, never delete the last account, never
delete an account a probe depends on - are now functions with unit tests
instead of script behaviour nobody dared touch.

**Good.** No subprocess adapter, no docker-cli in the API container, no
nats-box container per password hash. `bcrypt` and `cryptography` produce the
same artefacts, byte-compatible, and the tests assert exactly that.

**Good.** `compose.yaml` mounts `runtime/conf/` and `runtime/certs/` as
directories. The stack can start before initialisation - the NATS container
simply retries until the setup job writes the configuration - which is what
makes first-run setup from the browser possible. A config reload is a SIGHUP
again instead of the inode dance the file mount forced.

**Cost.** A breaking change for existing installations: the server
configuration moves to `runtime/conf/nats-server.conf`. `prtg-nats setup` and
`start` migrate the file automatically; the note is in the operations guide.

**Cost.** The end-to-end test installs the Python backend in its admin
container, because `init` and `user rotate` now live there. That is also a
feature: the E2E now exercises the exact code the web platform runs.

**Cost.** The recovery CLI duplicates one small sequence - the probe
configuration transaction in `app.ops` mirrors the rotation job's - because a
job handler needs its job context and a CLI has none. Twenty lines, marked as
such in both places.
