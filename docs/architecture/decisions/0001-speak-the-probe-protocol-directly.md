---
title: Speak the probe protocol directly instead of wrapping the CLI
role: developer
updated: 2026-08-02
status: accepted
---

# 1. Speak the probe protocol directly instead of wrapping the CLI

## Context

The brief suggested a legacy adapter: the web platform would call the existing
shell commands and parse their output. That is the usual way to put a web
interface on a shell tool.

Reading the existing code changed the answer. `libexec/prtg-nats-probe-helper`
is not a shell script that prints things for humans. It reads one tab-separated
request line and answers with a line-oriented, structured response - `OK <cmd>`
followed by `key=value` lines or TSV records. Twenty-one defined requests cover
status, configuration transactions, sensor rollout, credential profiles and
interface reservation.

It is already an RPC protocol. It just happens to be spoken over SSH by a bash
client.

## Decision

The backend speaks that protocol directly with `asyncssh`, using the same key
and the same pinned `known_hosts` file the shell tooling uses.

The legacy adapter still exists, but only for server-side work that has not
been ported: certificate generation, NATS account handling, backups,
verification runs. Everything a probe does goes through the protocol.

## Consequences

**Good.** No subprocess per request. Typed errors instead of strings to grep.
A protocol module that can be tested against captured fixtures without a probe
in sight. The probe side needs no change at all, so both tools stay
interchangeable during the migration.

**Good.** `HelperCommand` is a closed enum. Adding a request here without
adding it on the probe produces "Unsupported management request" immediately,
which is the failure mode we want: loud and at once.

**Cost.** The protocol is now implemented twice, in bash and in Python. If the
probe helper changes, both have to change. That is why the protocol module
carries the response shapes as test fixtures: those tests are the first thing
that fails when the two drift.

**Cost.** Bootstrap enrollment still needs an interactive SSH session and
therefore still goes through the shell. It is on the list.
