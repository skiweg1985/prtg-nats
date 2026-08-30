---
title: Documentation
role: everyone
updated: 2026-08-27
---

# PRTG-NATS documentation

Choose the task you need to complete. English is the source language for the
repository documentation and the web interface is available in English and
German.

## Start and operate the platform

| I want to... | Role | Page |
| --- | --- | --- |
| install the central stack | Operator | [Install the server](getting-started/install-the-server.md) |
| connect the PRTG core | Operator | [Connect the PRTG core](getting-started/connect-prtg-core.md) |
| enroll and approve a probe | Deployer | [Add your first probe](getting-started/add-your-first-probe.md) |
| deploy or configure sensors | Deployer | [Deploy sensors](guides/deploy-sensors.md) |
| set up an iperf3 endpoint | Deployer | [Measurement endpoints](guides/deploy-sensors.md#measurement-endpoints) |
| register one somebody else operates | Deployer | [An iperf endpoint somebody else operates](guides/foreign-iperf-endpoint.md) |
| update, back up, restore, or rotate credentials | Operator | [Operations and maintenance](guides/operations.md) |
| decide whether the installation is healthy | Operator | [Monitoring](guides/monitoring.md) |
| diagnose a failure | Everyone | [Troubleshooting](guides/troubleshooting.md) |

An **operator** owns the central NATS host and its runtime. A **deployer**
works with individual probes or measurement endpoints. The web interface is
the regular administration path; the CLI remains the bootstrap, recovery, and
automation path.

## Look up an exact contract

| I need... | Page |
| --- | --- |
| commands and supported options | [CLI reference](reference/cli.md) |
| site and backend settings | [Configuration reference](reference/configuration.md) |
| management endpoints and failure shapes | [REST API](reference/api.md) |
| PRTG lookup files | [PRTG lookups](reference/lookups.md) |
| web roles and permissions | [Roles and permissions](web/roles.md) |
| job states, locks, retries, and logs | [Jobs and deployments](web/jobs.md) |
| translation rules | [Languages and translation](web/i18n.md) |

Exact CLI flags come from `./prtg-nats help` and subcommand help. The OpenAPI
contract at `/api/openapi.json` owns the complete API schema; the human pages
explain supported workflows, boundaries, and recovery.

## Understand and review the design

- [Architecture overview](architecture/overview.md) maps components, data
  flows, and the bootstrap and management channels.
- [Architecture decisions](architecture/decisions/) preserve the reasons for
  costly-to-reverse choices.
- [Security model](security/model.md) covers the whole stack and the probes.
- [Threat model](security/threat-model.md) states the additional privileges and
  trust placed in the web platform.

Sensor-specific parameters and examples stay beside their implementation,
under [`sensors/`](../sensors/). This keeps detailed sensor behavior in one
place while [Deploy sensors](guides/deploy-sensors.md) remains the fleet-level
workflow.
