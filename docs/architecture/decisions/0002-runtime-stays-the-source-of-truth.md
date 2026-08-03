---
title: runtime/ stays the source of truth, SQLite holds only what it cannot
role: developer
updated: 2026-08-02
status: accepted
---

# 2. runtime/ stays the source of truth, SQLite holds only what it cannot

## Context

The platform needs a database for jobs, audit records and accounts. The
tempting next step is to put the probe inventory there too - it is relational,
it wants indexes, and querying files is unglamorous.

But `runtime/` is read by the NATS container, by the CA download container and
by every shell script. Copying it into a database creates a second truth, and a
second truth drifts within a week.

## Decision

The filesystem stays authoritative for credentials, certificates, the probe
inventory and the measurement endpoints. SQLite holds only what the filesystem
has no place for:

- web accounts, roles and sessions;
- jobs, their steps, their events and their resource locks;
- audit records;
- desired state per probe;
- the last observed state, explicitly as a cache with a timestamp;
- alerts, saved views and settings.

A probe that appears in `runtime/probes/` gets a stable id in the database on
first sight. That id is what URLs use, so a rename does not break a bookmark.

## Consequences

**Good.** The shell tooling and the web platform cannot disagree about which
probes exist. Deleting a probe with the shell removes it from the interface on
the next sync.

**Good.** Observed state is visibly a cache. Every list row reports when it was
last checked and dims when it goes stale, instead of presenting a value from
twenty minutes ago as current.

**Cost.** Listing probes reads a directory rather than running a query. At the
scale this manages - dozens of probes, not thousands - that is not a cost worth
optimising away.

**Cost.** The database can hold a desired state for a probe that no longer
exists. The inventory sync is what reconciles that, and it runs on a timer.
