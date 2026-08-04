---
title: Jobs and deployments
role: operator
updated: 2026-08-04
---

# Jobs and deployments

Anything that takes time or can fail becomes a job. That is what lets the
interface show progress instead of a spinner, and what lets an operator answer
"who deployed that, when, and what did the probe say" a week later.

## What a job carries

| | |
| --- | --- |
| Type and target | `sensor.deploy`, `internet-speed → 3 probes` |
| Status | queued, running, successful, failed, cancelled, partly successful |
| Steps | a named list; the interface shows "step 4 of 7" |
| Log | one line per event, with time, level, step, target |
| Result | what the job produced, per target |
| Error | a code, parameters, and the untranslated technical output |

## Steps

A job declares its steps before it starts. A sensor rollout, for example:

```text
resolve_targets → check_reachable → prepare → stage_files
                → activate → commit → verify
```

Starting a step finishes the previous one, and a failure marks the running step
failed and the remaining ones skipped. The step list is therefore a truthful
picture of where a rollout stopped, not a decoration.

## Why a rollout cannot go half-wrong quietly

The probe side implements the hard part, and the platform drives it:

1. every file is staged into a transaction;
2. activation installs the sensor and runs it once under the MPP service's own
   hardening, checking that it produces valid Script v2 JSON;
3. only then is the transaction committed.

If activation fails, the probe restores the previous state itself. The platform
reports what the probe said, verbatim, in the technical details.

Probes are handled one after another on purpose. A parallel rollout that half
succeeds is harder to reason about than a sequential one, and the slow part is
the self-test on the probe rather than anything on the server.

## Locks

A job declares what it needs exclusive use of - a probe, the NATS server, a
certificate - and takes all of them or none. A job that cannot get its
resources stays queued and says which job it is waiting for, rather than
racing it or failing.

That prevents the situations that used to need a written rule:

- two deployments writing the same sensor directory on one probe;
- a credential rotation during a probe onboarding;
- a probe being removed while a job is working on it.

A lock has a lease. If a worker dies, the lease expires and a reaper frees the
resource - a crashed process cannot lock a probe out for the rest of the day.

## Live output

The job detail page fetches the stored log, then subscribes to a server-sent
event stream from the last line it has. A page opened after a job finished
shows exactly what a page that watched it happen shows.

A stream that drops is picked up again from the last line the page holds, so a
proxy timeout, a laptop waking up or a short network outage costs a moment of
"reconnecting" rather than the rest of the job. The retries wait a little
longer each time - one second, then two, five, ten, thirty - and a network
coming back or the tab coming forward tries again straight away instead of
sitting out the wait. Once those attempts are used up the page says the
connection is gone and offers a button, which is the honest version of a log
that has stopped moving. Nothing is lost in the gap: the stream replays
everything after the line the page resumed from.

Each line carries a code and parameters, which the browser turns into a
sentence in the operator's language, plus optional raw output that is never
translated and sits behind a disclosure control.

## Retrying

A retry is a new job with the same inputs, pointing back at the one it repeats.
The failed run stays in the history, which is what makes "it failed twice for
the same reason" visible.

Only a finished job can be retried. A running one can be cancelled: a queued
job stops immediately, and a running one is asked to stop and checks between
targets, so a rollout stops after the current probe rather than in the middle
of an SSH transaction.

## Reading a failure

The interface answers six questions, in this order:

1. What failed?
2. Which target?
3. At which step?
4. What is the likely cause?
5. What can be done about it?
6. What are the technical details?

Questions four and five come from the error code, so adding a new error means
adding a cause and an action alongside the message. An error without them still
renders the first three answers rather than an empty panel.
