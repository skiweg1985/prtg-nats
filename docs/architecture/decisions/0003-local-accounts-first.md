---
title: Local accounts first, OIDC beside them later
role: operator
updated: 2026-08-02
status: accepted
---

# 3. Local accounts first, OIDC beside them later

## Context

The brief asked for OIDC. It is the right answer for a platform inside a
company that already runs an identity provider.

This platform is also the recovery path for the monitoring backbone. When NATS
is down, when a certificate has expired, when a probe fleet has gone silent,
this is the interface an administrator opens. If it depends on an identity
provider, it is unavailable in exactly the situations it exists for.

## Decision

Local accounts are the built-in way in and ship first. OIDC is planned beside
them, never instead of them.

Local accounts use Argon2id, a first-run wizard that closes after the first
account, exponential back-off after failed attempts, and server-side sessions
in an HttpOnly cookie.

## Consequences

**Good.** The platform works on a fresh machine with no external dependency,
which is also what makes the development setup trivial.

**Good.** Sessions are server-side, so an administrator can end one
immediately - which a signed token would not allow.

**Cost.** Another set of passwords to manage until OIDC lands. Mitigated by
the role model: most people need Viewer or Operator, and those accounts are
cheap to create and to remove.

**Cost.** No single sign-on yet. Stated in the threat model rather than
implied.
