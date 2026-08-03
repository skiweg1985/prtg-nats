---
title: One process, no broker
role: developer
updated: 2026-08-02
status: accepted
---

# 4. One process, no broker

## Context

Jobs, live output and background synchronisation are the kind of thing that
usually arrives with Celery and Redis.

This platform manages one server. A broker would be a second daemon to keep
alive, monitor and back up, in order to solve a problem this deployment does
not have.

## Decision

The job queue is a table. The workers are asyncio tasks in the API process.
The event broadcaster is a dictionary of queues in memory.

Claiming a job is a conditional `UPDATE ... WHERE status = 'queued'`, so two
workers cannot both take it. Resource locks are a table with a unique
constraint, so taking one is an `INSERT` and the database settles the race.

## Consequences

**Good.** One service to run, one thing to back up, one place to look when
something is stuck.

**Good.** The whole path - API, job, lock, probe protocol, deployment record -
runs in one process, so it can be tested end to end in-process with a scripted
transport standing in for the probe.

**Cost.** Exactly one instance. Two would run every job twice and each would
see half the event-stream subscribers. The container therefore runs a single
worker, and the threat model says so.

**Cost.** A crashed process leaves jobs marked running and locks held. A reaper
handles both: locks have a lease, and a job still running long past its lease
is ended with a reason.

**The seam.** If this ever needs several nodes, `EventBroadcaster` is the one
interface that has to grow a backend. Nothing else changes.
