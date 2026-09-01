---
title: A probe with no other path enrols over the tunnel
role: developer
updated: 2026-09-01
status: accepted
---

# 10. A probe with no other path enrols over the tunnel

## Context

[ADR 0009](0009-a-wireguard-overlay-to-the-probes.md) built the overlay for a
site whose path to this installation is not dependable. The case it names
first is a branch office reaching `NATS_FQDN` through a site-to-site tunnel.

A site with no such tunnel at all cannot get that far. Enrolment is a
callback: an operator pastes a command, the probe fetches the bootstrap
script, installs the management access and reports in. Every one of those
steps talks to this platform over HTTPS - and the platform answers on the
NATS address, because `WEB_BIND_IP` is `NATS_HOST_IP` in `compose.yaml`. A
probe that cannot reach that address fails at the first `curl`.

The overlay would solve it, and the bootstrap already builds a tunnel. But it
builds it in step 3, after fetching the helper that configures it - so the
tunnel arrives after the requests that needed it.

Reordering the script is not enough, and it is worth being precise about why.
The one-liner *downloads* the script, and that download is itself the first
request. A script that builds the tunnel cannot be fetched over the tunnel it
builds. So the script has to arrive by another route entirely.

There is a second knot behind that one:

`OverlayRuntime.peers()` renders the hub from the probe inventory, and a probe
gets an inventory entry when it reports in. The public key comes with the
callback. So the hub learns a peer only after the probe has spoken, and the
probe can only speak through the tunnel that peer would make. Neither side can
go first.

The probe generating its own key is what makes it impossible. That property is
deliberate and stated in the helper: *the private half of a probe's identity
has no reason to exist on the platform, so it never travels.*

## Decision

For this case, and only this case, the platform generates the probe's key
pair, reserves the peer before the probe exists, and hands the private half
over in the bootstrap script.

An invitation can be marked as a tunnel enrolment. When it is:

- the platform generates a key pair and writes it to
  `runtime/overlay/pending/<invitation id>`
- `peers()` reads that directory as well, so the peer is in the rendered hub
  configuration immediately. The hub's entrypoint polls the file and runs `wg
  syncconf`, so no tunnel already up is disturbed
- the command that builds the tunnel writes that key to
  `/etc/prtg-nats/overlay.key`, where `ensure_overlay_key()` finds and keeps
  it - so the helper adopts it in step 4 rather than generating a second one
  the hub knows nothing about
- the bootstrap script itself carries no key at all. It arrives over TLS
  through the tunnel that already exists, notes it, and hands it to the helper
- the callback turns the reservation into an ordinary inventory peer

**The operator builds the tunnel, then the ordinary one-liner runs.** The
interface shows two short commands before it: one installs `wireguard-tools`,
one brings `prtgnats0` up from the reserved key. Step three is the same
one-liner every other probe gets, only pointed at the address.

The first attempt handed over the whole rendered script as one block to paste.
That worked and was miserable: 19,000 characters through a browser console
that echoes every one of them took minutes, and it arrived as a wall of text
with its one warning lost at the top. Three commands of a few hundred
characters each paste instantly, carry their own heading, and fail one at a
time - an operator who sees an error knows which step it belongs to.

It also puts the CA ceremony back where it belongs. The block had to embed the
CA and guard itself with a digest, because a paste can arrive truncated. A
one-liner fetches the CA over HTTP, compares the fingerprint the operator saw
in the browser, and only then speaks TLS - which is what every runbook already
describes.

**Everything in it is addressed by IP.** A site with no route to this platform
has no name server that knows it either, so `NATS_FQDN` is a dead end there -
with the tunnel up or not. `BASE_URL`, the installer's `--nats-host` and the
probe's own configuration all use `NATS_HOST_IP`, which the server certificate
carries as a SAN (`infrastructure/pki.py`). The name is the only thing given
up; the verification is unchanged. The inventory records it as
`NATS_HOST_OVERRIDE`, so a later reconfiguration does not put the name back.

**No rotation afterwards.** Replacing the key would mean the hub entering the
new peer while the probe still holds the old one - and the management channel
that would coordinate it runs through the very tunnel being re-keyed. The
probe would have to switch on a timer, unobserved, with no way back if it went
wrong. The gain does not pay for that: anyone who can read this script already
has the CA, the management public key and a token that installs root access on
the probe. The WireGuard key is the smaller secret in that envelope.

**Reservations expire with their invitation.** A reservation is a working way
onto the overlay. An invitation stops being usable after an hour; the key it
handed out would not, so it is dropped when the invitation is revoked, when it
is redeemed, whenever a new invitation is issued, and once at API start for
the ones that ran out while nothing was running.

**It is opt-in, not automatic.** An installation with an overlay still enrols
the ordinary way by default. Probes that can reach the platform have no reason
to install `wireguard-tools` before anything else, and the option is where the
operator is told the script now carries a secret.

## Consequences

One of the three commands is a credential. An ordinary invitation is a token
that expires in an hour and can be revoked; this one also carries a key that
stays valid as long as the probe uses it. The warning sits on that command
alone rather than over the page, which is the only way anybody reads it.

`runtime/overlay/` now holds state that is not a rendering of something else,
which the module docstring used to say it never would. That is a real
exception to [ADR 0002](0002-runtime-stays-the-source-of-truth.md), kept
narrow: the files exist only between issuing an invitation and redeeming it,
and nothing reads them except the hub rendering and the script rendering.

The `check-static.sh` rule that forbade a private key placeholder in the
bootstrap template stands unchanged after all. The key travels in a command
the interface shows, never in the script - a script served over the enrolment
channel should not be a credential.

The invitation id travels to the job as `invitation_id`. Not
`enrollment_token_id`: `app/core/redaction.py` masks any key that reads like a
secret, `token` included, and the handler needs to use this one. Named that
way it arrived as `••••••••` and failed validation two steps later, which is a
puzzling way to learn about a naming rule.

The failure mode is different from the ordinary path. A normal enrolment whose
overlay step fails still finishes - the probe is reachable the ordinary way.
Here there is no ordinary way, so a failed tunnel aborts the script instead of
leaving a probe nobody can reach and no message saying why.

## Alternatives considered

**Two commands, the probe keeping its key.** Run something on the probe that
prints its public key, enter that on the platform, then run the real command.
It preserves the property fully. It also means three steps across two systems
for the operator, and the platform cannot verify the key it was handed
belongs to the host that will use it - so the property is preserved in form
more than in substance.

**Enrol over SSH from the platform.** `./prtg-nats install-mpp ADMIN@HOST`
already does this and needs no key to travel. It requires the platform to
reach the probe, which is exactly what a site behind NAT with no tunnel does
not offer. It stays the right answer whenever that reachability exists.
