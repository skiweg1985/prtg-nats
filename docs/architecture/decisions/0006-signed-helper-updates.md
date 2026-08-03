---
title: The probe helper is renewed over its own channel, against a signature
role: developer
updated: 2026-08-03
status: accepted
---

# 6. The probe helper is renewed over its own channel, against a signature

## Context

Every probe carries `libexec/prtg-nats-probe-helper` behind an SSH forced
command. It is the whole management surface, so a probe enrolled six months
ago answers `Unsupported management request` to everything added since - and
the operator finds out when a job fails, not before.

Until now the only way to renew it was the bootstrap path: `probe enroll USER
ADMIN@HOST --reenroll` on the console, or a fresh invitation someone runs on
the host. That was deliberate.
[ADR 0001](0001-speak-the-probe-protocol-directly.md) put it plainly: sending
the helper over the management channel would let anyone holding the management
key replace it with arbitrary code, running as root, on every probe at once.
The channel exists to be driven by that key, so the key cannot also be what
authorises new code on the far side.

What the constraint costs is a walk to a console for something the platform
otherwise does end to end, on every probe, for every helper change.

## Decision

The helper is sent over the management channel, and the probe verifies a
signature before it replaces anything.

- A separate key pair lives in `runtime/private/helper-signing-key.pem` and
  `helper-signing.pub`. Not the NATS CA: signing code is not what a TLS
  authority is for, and the CA has to stay rotatable without every probe
  losing the ability to accept an update.
- The public half reaches a probe over the bootstrap path only -
  `enroll-probe.sh --signing-key`, filled in by the invitation template or by
  `probe enroll`. It never travels over the channel it protects.
- `helper-update` carries the signature as its argument and the file as its
  payload. The probe checks the signature, then that the file is a bash script
  that parses (`bash -n`) and declares a `HELPER_VERSION`, and only then moves
  it into place with a rename - not a copy over the file the running helper is
  still reading.
- `probe-info` reports `helper_version` and `helper_sha256`. A helper from
  before this reports neither, which is precisely how the platform recognises
  one.

P-256 with SHA-256, because the probe verifies with `openssl dgst -verify`.
That works on every OpenSSL a probe might carry, while verifying raw ed25519
needs a flag OpenSSL 1.1.1 does not have.

## Consequences

**Good.** A helper change reaches the fleet from the interface or with
`probe helper-update --all`, with a job log and an audit trail, instead of one
console session per host.

**Good.** The interface says which probes are behind before a job runs into
it, and `Unsupported management request` now surfaces as
`probe.helper_outdated` - an error whose stated fix is the actual fix.

**Good.** A broken helper cannot strand a probe: the syntax check runs before
the rename, and a failed verification leaves the old file untouched.

**Cost.** Holding the management key is no longer enough to replace the
helper, but holding the server still is - the signing key lives in the same
runtime as the CA and the management key. This separates "the management key
leaked" from "the server was taken", and nothing more. Real code signing would
mean a key that never touches the server, and with it a release ceremony for
every helper change.

**Cost.** A chicken and egg for everything already installed. A helper that
does not know `helper-update` cannot be sent one, and one enrolled before the
signing key was distributed has nothing to verify against. Every existing
probe needs the bootstrap path exactly once; from then on the channel does it.
The probe page says which of the two cases it is in.

**Cost.** `HELPER_VERSION` now exists in two places - the helper and
`CURRENT_HELPER_VERSION` in the protocol module. A test asserts they agree,
the same way the command enum is asserted against the helper's dispatch.
