---
title: The stack updates itself from a container that outlives it
role: developer
updated: 2026-08-27
status: accepted
---

# 7. The stack updates itself from a container that outlives it

## Context

Updating an installation was four commands on the customer's console:
`backup`, `git status`, `git pull --ff-only`, `prtg-nats update`. That is not
a burden - it is four commands - but it has two consequences that are.

Nobody finds out there is anything to update. A new sensor version, a helper
version that fixes a rollback, a fix for the thing somebody reported last
week: all of it sits in the repository until an operator happens to look. The
platform already knows how to say "this probe is behind" - `SENSOR_OUTDATED`
and `probe.helper_outdated` are computed on every pass - but it had nothing to
say about itself.

And it could not have said it. `VERSION = "0.1.0"` was hardcoded from the
first commit and never raised; no `git describe` ran anywhere in the
production path. The stack did not know which version it was.

## Decision

The interface reports which version is installed and updates to the tip of the
branch on request. Three pieces make that possible, and each one is a
consequence of the container the platform runs in.

**The image carries its own commit.** `GIT_COMMIT` is a build argument, set by
compose and by `prtg-nats update`. A container has no checkout to ask, and the
version in `pyproject.toml` answers a different question - what the software
calls itself, not what is installed here.

Three commits are tracked, not one: what runs, what the checkout has, what the
branch has. They diverge, and the most common divergence has a name now -
`rebuild_pending`, a `git pull` without a rebuild. A single version number
would have reported that as up to date while the stack ran the older code.

**The checkout is found through Compose.** `web-api` mounts the runtime volume
and the Docker socket, and nothing else; the host path of the checkout it was
built from is not knowable from inside. Compose writes it onto every container
it creates as `com.docker.compose.project.working_dir`, which makes the daemon
the one component that can answer. `warn_about_foreign_containers()` in
`prtg-nats` already reads the same label.

An installation started without Compose has no such label. The feature reports
itself unavailable rather than guessing, the same way the missing Docker
socket already does.

**The update runs in a container outside the stack.** `docker compose up`
replaces `prtg-nats-web-api`, so whatever drives that cannot be running inside
it. The updater is created through the Docker API without Compose labels:
`--remove-orphans` collects candidates by the project label, so a container
without one is never in that list and survives the replacement of the process
that started it.

That last sentence is a claim about how somebody else's software behaves, and
a design resting on an unverified claim rests on nothing, so
[tests/e2e-update.sh](../../../tests/e2e-update.sh) proves it against a real
daemon: the unlabelled container is still there afterwards, its log still
holds what it wrote *after* the recreate, and - the control that keeps the
first two from being vacuous - a real orphan in the same sweep is collected.

It mounts the checkout **at its own host path**, not at a tidy `/checkout`.
Compose resolves a relative bind like `./web/Caddyfile` against the project
directory and hands the daemon an absolute path it reads as a host path.
Mounted anywhere else, the recreated proxy would bind a directory that does
not exist on the host and come up without its configuration.

## The job that outlives its own process

A job reports its outcome, and this one cannot: by the time the updater knows
whether it worked, the process that would write that down is gone.

The container is what survives. It is created without `AutoRemove` precisely
so that it still holds its exit code and its log, and a `stack_update` row
records which job is waiting on which container. On the way back up, before
the job runner starts, the outcome is read from the container and written to
the job.

Before the runner, and that order is the whole of it. `_recover_abandoned_jobs`
ends every job still marked running, on the correct assumption that nothing
survives a restart - and this is the one thing that does. Rather than carve an
exception into that routine, the job takes a status of its own:
`JobStatus.DETACHED`. The recovery filters on `running`, so a detached job
falls out by itself. It costs no migration: `EnumString` is a `VARCHAR`.

## Where the rollback stops

Everything up to and including the build can be undone by moving the checkout
back, because nothing has been replaced yet. A build that does not compile is
the common failure, and the updater puts the checkout back itself.

After `recreate` there is no way back that this code could take, and the
reason is worth stating exactly. The database has been migrated. Put the
checkout on an older commit and `ensure_schema` runs `alembic upgrade head`
against a revision the older scripts have never heard of; Alembic cannot
resolve it, the exception comes out of the lifespan, and the container does
not start at all. An automatic rollback would trade a running stack for one
that will not boot.

So there is none. The way back is the runtime export the job takes as its
first step, which is also why the backup is first and not last.

A rebuild is no gentler here, which is worth saying because it looks like it
should be. It exists for the state where the running image is older than the
checkout, so the image it builds is newer than what is running, and every
migration between those two commits runs when the new container starts. Only
the checkout is left alone; the database side is identical.
`tests/unit/test_schema_migrations.py` pins the behaviour so this stays a
statement about the code rather than about our memory of it.

## Consequences

**Good.** A new sensor version reaches the fleet without a console session,
and the existing deviation machinery does the rest: once the image is new, the
probes report `outdated` on the next pass and the fix is the button that was
already there.

**Good.** "Which version is this installation on" is now a question with an
answer, and so is "was it pulled but never rebuilt".

**Cost.** A new way to reach root on the host. Whoever can trigger an update
decides which code runs in a container holding the Docker socket. It is not a
new privilege - `web-api` has it already - but it is a new path to it, so it
sits behind its own permission, `system.update`, held only by administrators.
[The threat model](../../security/threat-model.md) says so plainly.

**Cost.** Bootstrapping once, exactly like
[ADR 0006](0006-signed-helper-updates.md). An installation updating *to* this
version has no updater image yet, so that
one update is still `./prtg-nats update` on the host. Every one after it is a
button, and the interface says which case it is in.

**Cost.** The interface is unavailable for the length of the recreate. The
proxy is not recreated - `up` without `--force-recreate` leaves a container
whose image and configuration did not change - so the page stays served and
switches to a waiting state instead of showing a connection error. It comes
back on its own, and reloads once, because the code in the browser is by then
the build that was replaced.
