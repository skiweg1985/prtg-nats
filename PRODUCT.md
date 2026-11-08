# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One company's own IT and network team, running one installation for its
headquarters, sites and branch offices. Two roles share it: the **operator**
owns the central NATS host and its runtime; the **deployer** enrols probes,
rolls out sensors and keeps individual sites healthy
([docs/README.md](docs/README.md)).

They work at a desk, in a browser, and use a phone as a second device: to
check a probe's state, read a failure, or trigger a small action while away or
standing at the probe on site. A phone must handle checking and small actions
well; the full workflow does not have to be phone-first.

## Product Purpose

PRTG-NATS runs the external NATS and JetStream service that PRTG
Multi-Platform Probes connect to, and adds a web platform to operate it:
enrol probes, deploy and configure sensors, manage measurement endpoints and
the WireGuard overlay, rotate credentials, update the stack. The probes stay
native `prtgmpprobe` packages on their own Linux hosts.

Success means the fleet keeps reporting into PRTG, and when something breaks
the operator sees what, where and why without opening a shell on the probe.

## Positioning

An internal tool, not a product offered to others. It replaces the NATS
server and the hand-run administration around it; it does not replace PRTG,
and PRTG stays the place where probes are named and monitored.

## Operating Context

- PRTG core and its probe list, where access keys are entered and probes are
  approved by hand; the platform links each probe to its PRTG object once.
- Probes on Raspberry Pis and Linux hosts at sites with their own networks,
  DHCP and firewalls, reached over a restricted SSH helper and, where a site
  needs it, the overlay tunnel.
- The `prtg-nats` CLI as the bootstrap, recovery and automation path next to
  the web interface; both act on the same runtime state.
- Jobs with step logs for every change that touches a probe; an audit trail
  for reveals and administrative changes.

## Capabilities and Constraints

- Still a proof of concept: larger rebuilds are acceptable.
- The runtime volume is the source of truth; SQLite only caches and holds
  what the filesystem has no place for
  ([ADR 0002](docs/architecture/decisions/0002-runtime-stays-the-source-of-truth.md)).
- The interface is German and English, with "Du" in German; one term per
  concept, settled in [docs/web/terminology.md](docs/web/terminology.md).
- Permissions are role based; destructive and secret-revealing actions are
  explicit and audited.
- The public repository stays anonymous: no real site names, host names,
  addresses or credentials in code, docs or examples.

## Brand Commitments

The name is PRTG-NATS. No brand beyond the name: what it owes its users is
reliability and quick operation, not a public identity. PRTG and the
Multi-Platform Probe are Paessler products; the tool works with them and does
not present itself as part of them.

## Evidence on Hand

No customers, testimonials or public claims exist, and none may be invented.
Real usage is one company's test and production sites.

## Product Principles

1. The state of a probe is answered on one screen, without a shell.
2. Every change that reaches a probe runs as a visible job, and a failure
   says what to do next.
3. Secrets and destructive steps take a deliberate, confirmed action.
4. Web interface and CLI agree, because they act on the same runtime.
5. Each fact appears once: one name per probe, one place per setting.
