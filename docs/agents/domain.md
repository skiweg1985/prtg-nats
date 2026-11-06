---
title: Domain docs for agent skills
role: developer
updated: 2026-11-06
---

# Domain docs

How the engineering skills consume this repository's domain documentation when
exploring the codebase. The layout is single-context: one set of decisions and
one vocabulary for the whole stack.

## Before exploring, read these

- **[`docs/architecture/decisions/`](../architecture/decisions/)**: the ADRs.
  Read the ones that touch the area you are about to work in. This repository
  keeps them here rather than under `docs/adr/`, which is what the skills name
  by default.
- **[`docs/web/terminology.md`](../web/terminology.md)**: the binding wording
  for the web platform - one term per concept, Du-Anrede, and the retired
  terms a check enforces. It stands in for a root `GLOSSARY.md`, which this
  repository does not have.
- **[`docs/architecture/overview.md`](../architecture/overview.md)** when the
  change crosses components, since it maps the data flows and the bootstrap
  and management channels.

If one of these files is missing, **proceed silently**. Do not flag its
absence and do not suggest creating it upfront. The `/domain-modeling` skill,
reached through `/grill-with-docs` and `/improve-codebase-architecture`,
creates a `GLOSSARY.md` or an ADR lazily, once a term or a decision actually
gets resolved.

## File structure

```text
/
├── docs/
│   ├── architecture/
│   │   ├── overview.md
│   │   └── decisions/
│   │       ├── 0001-speak-the-probe-protocol-directly.md
│   │       └── 0002-runtime-stays-the-source-of-truth.md
│   └── web/
│       └── terminology.md
├── libexec/
├── sensors/
└── web/
```

A new ADR joins `docs/architecture/decisions/` with the next free number and
carries the front matter the other pages use, `status` included.

## Use the established vocabulary

When your output names a domain concept - an issue title, a refactor proposal,
a hypothesis, a test name - use the term as the terminology page and the
existing ADRs define it. Do not drift to synonyms those pages avoid. User
facing strings follow the same page and need a key in both
`web/frontend/src/i18n/locales/de.json` and `en.json`.

If the concept you need is not written down anywhere yet, that is a signal:
either you are inventing language the project does not use - reconsider - or
there is a real gap, worth noting for `/domain-modeling`.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than
silently overriding it:

> _Contradicts
> [ADR 0002](../architecture/decisions/0002-runtime-stays-the-source-of-truth.md)
> (the runtime stays the source of truth), but worth reopening because…_
