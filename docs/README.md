---
title: Documentation
role: everyone
updated: 2026-08-03
---

# PRTG-NATS documentation

English is the source language of this documentation. Every page carries front
matter with a `title`, the `role` it is written for, and the date it was last
reviewed, so a translation can be added later without restructuring anything.

## Where do I start?

| I want to … | Role | Page |
| --- | --- | --- |
| set up the NATS server | Operator | [Install the server](getting-started/install-the-server.md) |
| connect the PRTG core | Operator | [Connect the PRTG core](getting-started/connect-prtg-core.md) |
| add a probe | Deployer | [Add your first probe](getting-started/add-your-first-probe.md) |
| run the web interface | Operator | [Install the web platform](web/install.md) |
| get monitoring scripts onto probes | Deployer | [Deploy sensors](guides/deploy-sensors.md) |
| set up an iperf3 measurement endpoint | Deployer | [Deploy sensors](guides/deploy-sensors.md#measurement-endpoints) |
| operate, back up, rotate passwords | Operator | [Operations](guides/operations.md) |
| watch the stack from PRTG | Operator | [Monitoring](guides/monitoring.md) |
| look up a setting | Operator | [Configuration reference](reference/configuration.md) |
| install a probe by hand | Deployer | [Manual probe install](guides/manual-probe-install.md) |
| work out what went wrong | Everyone | [Troubleshooting](guides/troubleshooting.md) |
| see how the parts fit together | Developer | [Architecture overview](architecture/overview.md) |
| read the security model | Everyone | [Security model](security/model.md) |
| understand what the web platform is trusted with | Operator | [Threat model](security/threat-model.md) |

**Operator** sets up the NATS server and keeps it running.
**Deployer** connects individual probes and does not touch the server.

## The two interfaces

The platform can be administered two ways. They act on the same state and can
be used side by side during the migration.

| | Web interface | Shell tooling |
| --- | --- | --- |
| Audience | every administrator | recovery and automation |
| Sign-in | account with a role | root shell on the server |
| Audit trail | every action, with the user | shell history |
| Long-term | the regular way | recovery and scripting only |

New functionality is built in the web platform. The shell tooling is not being
extended, and [the command reference](reference/cli.md) describes it as it
stands.

## Sections

- **[getting-started/](getting-started/)** - first installation, in order:
  [the server](getting-started/install-the-server.md),
  [the PRTG core](getting-started/connect-prtg-core.md),
  [the first probe](getting-started/add-your-first-probe.md).
- **[guides/](guides/)** - recurring tasks:
  [operations](guides/operations.md), [monitoring](guides/monitoring.md),
  [sensors](guides/deploy-sensors.md),
  [manual install](guides/manual-probe-install.md),
  [troubleshooting](guides/troubleshooting.md).
- **[reference/](reference/)** - [commands](reference/cli.md),
  [configuration](reference/configuration.md), [REST API](reference/api.md),
  [PRTG lookups](reference/lookups.md).
- **[security/](security/)** - [what is protected and how](security/model.md),
  and [what is trusted](security/threat-model.md).
- **[web/](web/)** - the management platform: [install](web/install.md),
  [roles](web/roles.md), [jobs](web/jobs.md), [languages](web/i18n.md).
- **[architecture/](architecture/)** - the [overview](architecture/overview.md)
  of how the parts fit together, and [decisions/](architecture/decisions/):
  why the platform is built the way it is. One record per decision, kept even
  when superseded.

Sensors document themselves next to their code, one `README.md` per directory
under [sensors/](../sensors/).

## A note on language

The repository is English - code, comments and documentation. The web interface
ships English and German and is built so a third language is a translation
file, not a code change; see [Languages and translation](web/i18n.md).
