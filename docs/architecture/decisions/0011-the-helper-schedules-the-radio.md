---
title: The helper schedules the radio, the sensor only collects
role: developer
updated: 2026-11-06
status: accepted
---

# 11. The helper schedules the radio, the sensor only collects

## Context

`wlan-auth` checks one network per sensor. A probe that has to watch
several networks therefore carries several sensors on the one radio it
owns. The site at Wertheim does exactly that: a Raspberry Pi with `wlan0`,
one sensor for the internal network over PEAP, one for the guest network
over WPA2-PSK.

PRTG knows a scanning interval per sensor and nothing about the other
sensors on the same device. There is no way to express "these two share a
radio", so overlapping runs are not a misconfiguration somebody can fix
but the ordinary case.

The helper serialises them with a lock per interface, and that lock cannot
close the gap:

- A run waits `lock_budget(timeout)`, which is
  `min(15, max(1, timeout / 2))` seconds, for the radio.
- The ceiling is there because the sensor script gives the socket
  `timeout + 20` seconds. Waiting plus measuring has to stay inside that,
  or the script reports a sensor error for a measurement that was about to
  succeed.
- The waiting is therefore at most half the time budget, by the formula
  itself. A window of at least `timeout / 2` remains in which a second run
  gives up and reports `iface-busy`.

What makes this bite is the asymmetry between a good run and a bad one. A
successful sign-in holds the radio for a few seconds; a failing one holds
it for the whole budget, 45 seconds by default. A broken network therefore
turns the sensor of the working network red - and because two sensors with
the same interval keep their offset from round to round, it does so in
every round rather than now and then.

`iface-busy` is a sensor error today, which follows the rule the sensor
otherwise keeps: a run that did not measure says nothing about the Wi-Fi.
But it puts the alarm on the wrong object. The network is fine; the
schedule is not.

Separate sensors per network are a requirement, not a preference.
Availability is evaluated per network, the networks are paused
independently, and they do not belong to the same people. One sensor with
a channel range per network - the shape `aruba-uplink` uses for its two
uplinks - would collapse exactly what has to stay apart. Those two uplinks
are two paths of one service; two WLANs in two VLANs are two services.

## Decision

Collecting a result and producing one are separated. The sensor script
stops measuring and starts asking.

**The script hands over its parameters, the helper plans.** The helper is
the only party that knows every job on the radio, so it is the only one
that can order them. It measures on its own clock and never while another
job holds the interface. That removes the race instead of arbitrating it.

**The answer is the last measurement and its age.** The age becomes a
channel of its own. A sensor that asks more often than the radio can
deliver gets the same values again with a rising age, rather than a
failure.

**A job is registered by being asked for, and expires by not being asked
for.** No rollout, no file on the probe, no second register of what is
measured - PRTG stays the only place where the set of monitored networks
is defined. A sensor deleted or paused in PRTG stops asking and its job
falls out of the scheduler after a timeout. A lease with a reaper is how
[ADR 0004](0004-one-process-no-broker.md) answers the same question in the
job queue of the web platform.

**A job's identity is all of its parameters.** Two sensors asking for the
same network with the same credentials are one job and cost the radio one
run. A changed password is a different job, so the old one stops being
asked for and expires, instead of the scheduler going on measuring with
credentials nobody uses any more.

**`--max-age` states the freshness a sensor needs, and the same number
invalidates a stale result.** The scheduler plans against it and can
report that the radio does not allow it. Past it the script declares the
value invalid rather than serving it. A channel whose limit nobody
configures would let a sensor stay green on a value measured hours ago,
and that is a worse failure than today's wrong alarm, because nothing
about it looks wrong.

**`prtg.standardlookups.netapp.nichealth` carries the third state.** It
knows 1 = Healthy, 2 = Unhealthy and 3 = Not Available with
`state="None"`, so "registered, not measured yet" and "older than
`--max-age`" are neither an alarm nor a silent success. The lookup is
already in use for the uplink states of `aruba-uplink`.

**Over-subscription is shared out evenly.** Where the radio cannot serve
every job inside its `--max-age`, all of them age together. Whoever looks
sees it on every affected sensor at once, instead of one network quietly
starving while another stays fresh.

**Credentials live in the process and nowhere else.** A scheduler has to
keep them between runs, so it keeps them in memory and never on disk. A
restart empties it and the next request fills it again, which costs one
cycle and needs no recovery procedure. The sensors that already carry
state between runs - `aruba-uplink`, `internet-speed`,
`iperf-throughput` - keep it in `/tmp` under `PrivateTmp`, so it is just
as volatile. This is that model without a file.

**This is for `wlan-auth` alone.** `internet-speed` and
`iperf-throughput` make expensive measurements too and solve it with a
cached result and a minimum interval, which is enough while a measurement
competes only with itself. Nothing here argues against carrying the
pattern over later; that is not decided here.

## Consequences

**Good.** `iface-busy` stops existing as a condition, not merely as a
message. The radio serves one job at a time because one party decides the
order.

**Good.** Over-subscription becomes a measurement instead of a failure.
Two networks with a 45-second budget need 90 seconds of radio time per
round either way - the difference is that it now shows as rising age on
the channel built for it, where a limit can catch it.

**Good.** A broken network no longer takes the sensor of a working one
with it. Its own job is the one that occupies the radio for a full budget,
and that shows on its own age.

**Cost.** The helper becomes a long-running service instead of a process
per connection. It has to survive what a process living for seconds never
had to: a leaked supplicant, a wedged DHCP client, its own memory over
weeks. Socket activation starts it today; from now on it is up whether or
not anybody asks.

**Cost.** The credentials of every monitored network sit in the memory of
a root process for as long as it runs, where today they exist for the
seconds of one run. Core dumps have to be off and the process must not be
inspectable - and it is still a real widening of what one bug on the probe
can expose.

**Cost.** A red sensor changes meaning: not "this network is failing now"
but "it failed at the last run, and that was X seconds ago". The age
channel makes X visible, but every threshold and every notification text
anybody has configured has to be read that way from now on.

**Cost.** A parameter change takes effect one expiry later. The old job
goes on being measured until it has not been asked for long enough -
harmless in itself, but it costs radio time, and it is the reason a job's
identity has to cover every parameter instead of the SSID.
