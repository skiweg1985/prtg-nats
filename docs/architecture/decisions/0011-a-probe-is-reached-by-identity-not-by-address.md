---
title: A probe is reached by identity, not by the address it had yesterday
role: developer
updated: 2026-11-07
status: accepted
---

# 11. A probe is reached by identity, not by the address it had yesterday

## Context

A probe at a branch office moved from `198.51.100.29` to `198.51.100.32`
during a power cut, because the site runs DHCP and the lease did not survive.
PRTG kept showing it green throughout, and rightly so: the probe
dials the NATS server outbound and identifies itself with `PROBE_ID` and
`ACCESS_KEY`, neither of which has anything to do with an address. The
measurement path was never affected.

What broke was this project's own management path. `SSH_HOST` in the
inventory still named `.29`, and every verb that reaches a probe reads it -
`probe status`, `probe apply`, the sensor rollout, the helper update. The
operator notices nothing until a rollout fails, and the only way to repair
the entry today is to enrol the probe again.

Two things make the address worse than a stale field. The pinned host key is
stored in `known_hosts` against the address, so a probe that moves loses its
key verification along with its reachability - the two facts that should be
independent are tied together. And nothing reports the drift: a probe can sit
at the wrong address for weeks, answering PRTG, invisible to management.

Meanwhile the platform already hands every probe an address that cannot
drift. The overlay hub allocates the lowest free peer address in its subnet
and writes it to the peer, so it is assigned here, not by DHCP at the site.

Half of what follows is therefore already built, on one side only. The web
platform has `ProbeInventory.management_hosts`: the overlay address first
where the tunnel is meant to be up, the configured host as the fallback. The
shell tooling has nothing of the kind - `common.sh` and `manage-probes.sh`
read `SSH_HOST` directly, in nine places. The two halves of this project
disagree about how a probe is reached, and the half an administrator uses
from the command line is the one that cannot recover from a move.

## Decision

A probe's identity is its NATS user name, its `PROBE_ID` and its SSH host
key. An address is a route to it, and routes are allowed to change.

**The host key is pinned against the identity.** Every management SSH call
passes `HostKeyAlias=<nats-user>`, so `known_hosts` holds one entry per
probe instead of one per address. `StrictHostKeyChecking=yes` stays as it
is. A probe that moves keeps its verified key; a different machine at the
same address still fails the check.

**A request moves on to the next route only when the first does not
connect.** An exit code from the far side means the route worked and the
request did not; repeating it would run it twice. Both routes verify the same
pinned key, so a second attempt reaches the same machine.

The shell tooling has one more constraint the web platform does not. A shell
request travels on the caller's stdin, which may carry a password and can be
read only once. So read-only requests - `probe-info`, `status`,
`sensor-list` - are taken as arguments and rebuilt for every attempt, while
staging credentials and other transactions use the preferred route only,
which the read-only calls before them have already corrected. The web
platform holds every request in memory and retries any of them.

**Routes are derived, not stored.** The inventory format does not change.
`SSH_HOST` keeps its name and means what it already is in practice: the last
address the probe was reached at. The ordered list of routes is computed from
the fields that exist - overlay address first where the tunnel is meant to be
up, then `SSH_HOST` - and the shell tooling computes it the same way the web
platform's `management_hosts` already does. One function in `common.sh`, used
by the nine places that read the field today.

**A reached probe reports where it is.** `probe-info` returns the addresses the
probe currently holds on its own networks. Two kinds of interface are left out:
the overlay, whose address the platform assigned itself, and every interface a
sensor has reserved, as recorded by `sensor-reserve-interface`. A WLAN test can
hold a transient address on its radio, and reporting it beside the site address
would turn one candidate into two. Because that answer arrives over a
connection already authenticated by the pinned host key, it needs no new
endpoint, no new credential and no inbound path to the platform. The reported
address replaces the "last reached at" route, under two conditions. The answer
has to have come over the overlay: an answer over the cached address proves
that address works, even when the probe reports another - behind NAT it sees
its private address while the platform reaches the public one. And there has to
be exactly one candidate; a probe on two site networks gives no way to tell
which one is reachable from here. The overlay route is never overwritten.

**The overlay is what makes this work, so it is recommended for any probe
whose address the platform does not control.** It stays an explicit choice
per probe: the platform cannot tell from the outside whether a site runs
DHCP, and turning a tunnel on behind an operator's back is not a default.
Without it the cached address is the only route, and a probe that moves while
nobody manages it stays unreachable until it is re-enrolled.

**Enrolment no longer asks for an address.** The invitation takes the address
the probe reports from, and the first contact over the overlay corrects it if
it was the wrong one. The field, and the hint that it is wrong behind NAT,
are gone from the interface; `expected_host` stays in the API for scripts.

**Drift is shown, not hidden.** `probe list` shows every route in the order it
is tried, and a probe that reports a new address says so on the command line
and in the platform log. Replacing the "last reached at" route needs no
confirmation - it is a cache. Nothing in
PRTG raises an alarm for it: an address change inside a DHCP network is
normal, and a sensor channel for it would cry wolf.

## Consequences

**Good.** A probe that changes address stays manageable without an operator
touching anything. The next management call reaches it over the overlay and
corrects the cached route on its own.

**Good.** Host-key verification survives a move. Today it does not, and the
tempting repair - removing the `known_hosts` entry - throws away the one
check that would catch an impostor at the new address.

**Good.** A probe that has drifted is visible before a rollout fails:
`probe list` and `probe show` name its routes, and an adopted address is
announced on the command line and in the platform log.

**Good.** The shell tooling and the web platform reach a probe the same way.
Today only one of them has an ordered route list, which is why the same
probe can be manageable in the interface and unreachable from the command
line.

**Cost.** Nine call sites in `common.sh` and `manage-probes.sh` move from
reading a field to calling a function, and `probe-info` grows an answer the
helper does not give yet - a helper change means a signed helper update to
every probe, per
[ADR 0006](0006-signed-helper-updates.md). The inventory format itself is
untouched, so nothing has to be migrated.

**Cost.** Trying routes in order makes an unreachable probe slower to fail -
each route costs a connect timeout. The timeout per route has to be short
enough that the sum stays below the budget the web platform's jobs allow.

**Cost.** A site with neither an overlay nor a reserved address has exactly
one usable route, the cached one. If it changes while the probe is not being
managed, the probe is unreachable until someone looks. Making the overlay
the default is what keeps that case rare; it is not eliminated.

## Alternatives considered

**A reserved address per site.** Reliable where the network is ours, and no
code at all. It fails at exactly the sites this is about: a branch office
whose DHCP we do not run, where a reservation is somebody else's change
request.

**The probe reports its address to the platform over HTTPS**, the way the
enrolment callback does. It would also cover sites without an overlay, but
it needs a durable credential for a path the probe otherwise does not use
after enrolment, and it lets a probe influence where management connects.
Reading the address over a connection the platform opened keeps that
direction of trust intact. If a site without an overlay ever has to be
supported, this is the way to do it - as a reported candidate that is
verified against the pinned key before use, never as a value that is simply
written.

**Keep `SSH_HOST` and add a repair command.** Smallest change, and it does
make the entry fixable without a re-enrolment. It still requires somebody to
notice, which is the part that failed at the site above.
