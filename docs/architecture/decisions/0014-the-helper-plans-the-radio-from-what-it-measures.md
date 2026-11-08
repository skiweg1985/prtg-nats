---
title: The helper plans the radio from what it measures
role: developer
updated: 2026-11-08
status: accepted
---

# 14. The helper plans the radio from what it measures

Supersedes the planning part of
[ADR 0011](0011-the-helper-schedules-the-radio.md): how often a job is
measured, when a result is stale, and what `--max-age` means. Everything
else in it stands - the helper schedules, the sensor collects, a job is
registered by being asked for.

## Context

Since ADR 0011 every `wlan-auth` sensor states its freshness with
`--max-age`, and the helper plans against that number. Two things made
this cost time on real rollouts.

The operator has to know what a round over the radio costs to pick a value
that can be met. Too low a value was failure code 13, a stale result code
12. Adding a third network on a probe raised the round and turned sensors
red that nobody had touched - on their own parameter, not on the Wi-Fi.

The helper budgeted every job with its worst case, the whole timeout plus
the wait for the lock. Two networks cost 120 seconds a round on paper,
although a successful sign-in takes 8 to 15 seconds on a probe. Code 13 was
therefore raised for settings the radio could have met easily.

And the PRTG scanning interval was a second number next to `--max-age`
that meant nothing for the radio, which had to be explained on every
rollout.

## Decision

**The scanning interval in PRTG is the only setting.** The helper reads how
often PRTG asks for a job off the requests themselves, as the median gap of
the last six, and measures the job about that often. A sensor polled every
five minutes is measured about every five minutes and leaves the airtime in
between to the others. A job asked for the first time is measured at once,
as before; until its second request it is planned with five minutes.

**Rounds are measured, not assumed.** The helper keeps how long the last
three runs of every job took, failed runs with their real, longer duration,
and expects the slowest of them. A round is the sum of those plus the ten
seconds the radio rests after every sign-in. A network that starts failing
lengthens the round with its first long run; one that recovers shortens it
three good runs later. The durations live in memory with the rest of the
schedule, so a restart begins from the worst case and converges within a
round.

**The floor stays.** A network is measured at most once a minute, however
often its sensor asks. That is the lesson from the access point that
answered back-to-back sign-ins with status 16, and nothing in this change
relaxes it.

**Fairness stays.** The next job is the one whose age is furthest past its
own interval. Where the radio cannot serve every job at its interval, all
of them are overdue and are measured in turn, so they age together instead
of one starving.

**Stale is relative to the schedule.** A result counts as stale once it is
older than twice its interval, or twice the round where the round is
longer. Past that, the age points at a real problem - a stuck helper, a
radio with too many networks - and the sensor says so in plain words:
`measurement 9 min old, round currently 4 min, 4 jobs on wlan0`.

**The schedule is visible instead of configured.** Every `wlan-auth`
sensor carries *Round Time* and *Jobs on Radio* next to *Measurement Age*.

**`--max-age` becomes an optional upper bound.** Unset is the default. A
sensor that sets it is measured at most its interval apart, renewed a round
before the bound. A value the radio cannot keep - a round of waiting on top
of the floor, or two rounds where the round is longer - is a warning first
in the message, no longer failure code 13, and the result is then judged
against what the radio can keep. Code 13 is retired; its number stays
reserved.

**One radio, one plan - for now.** The helper plans every job on the
probe as one round. A second test interface would need a round per
interface. Nothing here rules that out: the round is computed from the
jobs, and grouping them by interface is a change to which jobs are
summed.

## Consequences

**Good.** Adding a network changes how often the others are measured,
visible on *Round Time*, and turns nothing red.

**Good.** Rounds follow reality. Two healthy networks cost about 50
seconds a round instead of 120, so the same radio serves more networks at
the same interval.

**Good.** Nobody has to compute a value for `--max-age`. Existing sensors
that set one keep working.

**Cost.** Measuring follows asking again. A sensor left at the one-minute
interval ADR 0011 recommended is now measured every minute where its
network was renewed every three to four minutes before. That is what the
interval says, and it costs airtime the other networks on the radio lose.
The documentation now recommends the interval a network should be measured
at, five minutes by default.

**Cost.** Two sensors with identical parameters are one job, and their
requests interleave. The helper then reads a shorter interval than either
of them is set to. The median keeps it stable, and the floor keeps it from
hammering the access point, but the job is measured more often than either
sensor would suggest.

**Cost.** A failing network costs its full timeout per run and lengthens
the round for everyone. Intended - it has to stay measured - and visible on
*Round Time* of every sensor on the radio.
