---
title: Sensor tools are selected by userspace platform
role: developer
updated: 2026-08-30
status: accepted
---

# 8. Sensor tools are selected by userspace platform

## Context

`iperf-throughput` depends on authenticated behavior that changed across
iperf3 releases. A program called `iperf3` being present proves neither the
required version nor compatibility. At the same time, building and testing a
release-owned executable for every Linux CPU and ABI would make otherwise
usable probes wait for an artifact they do not need.

The architecture reported by the kernel is not a sufficient selector. A
Raspberry Pi can run an `aarch64` kernel above a 32-bit `armhf` userspace. An
AArch64 ELF selected from `uname -m` cannot run with that userspace's loader.
ARMv6 and ARMv7 also share the `armhf` package label even though an ARMv7
release artifact cannot run on ARMv6.

The management channel runs a constrained helper as root. Its existing sensor
slots accept Python scripts and wrappers that the service can execute with
privileges, so the management key is already a root-level trust boundary. A
new arbitrary ELF upload would still be a broader, less inspectable payload
channel. It must not turn the helper into a general binary or package
installation interface. [ADR 0006](0006-signed-helper-updates.md) already
established a separate bootstrap-pinned signing key. The managed-tool command
requires that additional signature for the exact ELF payload. This proves the
provenance and integrity of an official release artifact; it is not a defence
against a malicious holder of the existing management key.

## Decision

PRTG-NATS uses one of two explicit sources for the iperf3 client:

1. A **managed** release artifact is preferred when the release contains an
   exact userspace-platform match. Release 3.21 contains iperf3 3.21 for:

   - `linux-amd64-glibc`
   - `linux-arm64-glibc`
   - `linux-armhf-glibc`, for ARMv7 and newer

2. A **system** fallback is allowed for another userspace platform that the
   helper can identify, including `linux-armhf-v6-glibc`. It is always the
   literal `/usr/bin/iperf3`, never a lookup through `PATH`. The executable
   must report iperf3 3.18 or newer and list `authentication` among its
   optional features. The 3.18 floor matches the package shipped by the
   current Raspberry Pi OS release when this decision was made.

An unidentifiable userspace platform has no fallback. Neither does a platform
with a managed artifact whose transfer, signature, digest or version check
fails. Falling back in either case would turn a failed integrity check into a
different executable choice.

For a managed tool, each catalogue entry fixes the name, version, platform
and expected SHA-256 digest. Its signature covers the exact ELF bytes and is
created with the existing helper-signing key. The probe verifies the
signature with the public key installed during bootstrap, then verifies the
digest, selected platform and version. The payload is one ELF, not an archive,
package or installer.

Verified managed executables are stored under:

```text
/opt/prtg-nats/tools/iperf3/<version>/<platform>/iperf3
```

`/opt/prtg-nats/tools/iperf3/current` points to the selected directory.
Activation replaces that link atomically. It targets the versioned artifact
directory for a managed tool and `/usr/bin` for a system fallback, so the
sensor always invokes the stable `current/iperf3` path.

For a system fallback, the helper checks an already present
`/usr/bin/iperf3`, its version and its authentication feature. It never runs a
package manager. If the file is absent or incompatible, the rollout stops and
asks the operator to install or update it through the operating system. That
keeps package changes outside the restricted management channel.

The sensor receives the selected source and resolved absolute path as part of
its deployment state. It invokes only
`/opt/prtg-nats/tools/iperf3/current/iperf3`; the helper controls whether that
resolves to a managed artifact or the verified `/usr/bin/iperf3`. Neither side
invokes `iperf3` through `PATH`.

Tool activation and sensor activation are one rollback transaction. The helper
preserves the previous sensor state and `current` target, activates both
candidates, and runs the sensor self-check. A verification or self-check
failure restores both previous values. The system fallback still changes only
that link; it creates no package state to roll back.

Probe status reports source, absolute path, version, userspace platform,
SHA-256 digest and compatibility. Managed tools compare against an exact
version and digest. System tools compare against the version floor and feature
contract. A missing or incompatible tool remains a deployment deviation.

Authenticated foreign endpoints must run iperf3 3.17 or newer and support
RSA-OAEP. `--use-pkcs1-padding` is not a compatibility option because it uses
a different authentication mode than the clients deployed here.

## Alternatives

**Require a managed artifact for every userspace platform.** Rejected because
it excludes compatible probes until the project has built and tested every CPU
and ABI combination.

**Use any existing `iperf3` from `PATH`.** Rejected because neither its origin
nor its path is controlled. The helper accepts only `/usr/bin/iperf3` from the
operating-system package contract.

**Use the system package on every platform.** Rejected because supported
platforms can receive one exact, tested client with a release digest and an
atomic rollback path. The system fallback is for gaps in the artifact matrix,
not a replacement for it.

**Fall back after a managed artifact check fails.** Rejected because it would
hide release or transfer drift. A managed-platform failure must remain visible.

**Download a release directly from each probe.** Rejected because it adds
outbound network and remote availability requirements and moves provenance
verification away from the existing release and helper trust path.

**Transfer an archive or package.** Rejected because extraction and package
scripts create a larger root-level input surface than the one executable the
sensor needs.

## Consequences

**Good.** The common ARM and x86 platforms receive one exact iperf3 3.21
executable, including 32-bit ARMv7 userspaces under 64-bit kernels.

**Good.** Other identified platforms can still run the sensor when their
operating system provides a compatible `/usr/bin/iperf3`.

**Good.** The probe status distinguishes release-owned bytes from a system
package instead of presenting both as the same installation state.

**Cost.** A system fallback has a minimum-version contract rather than an
exact-version contract. Its bytes, installation and updates belong to the
operating system, so an operator must provide a compatible executable before
the sensor can be deployed.

**Cost.** Every managed userspace platform still needs a separately built,
signed and tested ELF. The glibc compatibility floor must remain suitable for
the oldest supported probe distribution.

**Cost.** An unknown platform, an incompatible system binary or a failed
managed-artifact check remains drifted until an operator corrects it.

**Cost.** The helper-signing key authorises both helper replacement and managed
sensor executables. Compromise has the same server-level trust consequence as
a malicious helper update. The additional signature prevents the managed-tool
command from accepting an unapproved ELF, but it does not reduce the existing
root-level trust placed in the SSH management key: that key can already stage
sensor scripts and privileged Python wrappers through the established sensor
deployment interface.

## Revisit when

Reconsider the artifact matrix when a fallback platform becomes common enough
to justify a release-owned build. Reconsider the source and trust model when
the probe platform provides its own verified sensor-tool runtime or when
release signing moves to a key that does not reside on the PRTG-NATS server.
