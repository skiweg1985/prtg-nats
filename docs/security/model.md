---
title: Security model
role: everyone
updated: 2026-08-27
---

# Security model

This covers the whole stack - the NATS server, the management access and the
deployed probes. It is deliberately a document of its own so it can be handed
over on its own during an audit or a handover.

The web platform adds its own considerations; those are in
[the threat model](threat-model.md).

## Transport and credentials

- TLS with a local CA of our own; the server certificate carries the configured
  FQDN and host address as SANs.
- The NATS container only ever sees the bcrypt hash of a password.
- Every new probe gets its own NATS account name and password.
- Cleartext passwords, private keys, generated configuration and backups live
  in the root-owned `prtg-nats-runtime` volume and never in the checkout.

## The management channel

- The main NATS server holds a dedicated Ed25519 key for probe management. On
  the probes that key is restricted to a forced command, to the source address
  of the NATS server, and with forwarding disabled. The forced command accepts
  only the defined management requests; this key never yields a shell.
- The probe helper accepts a configuration only when it passes the helper's own
  structural check. A faulty or tampered caller cannot use it to place an
  arbitrary file or start a different service.
- SSH host keys of the probes are confirmed before enrollment and then pinned
  in a root-only `known_hosts` file of its own.

## Sensors on a probe

- Deployed sensor scripts run as the unprivileged service account of the probe.
  When a sensor needs more, it gets a program of its own under
  `/usr/local/sbin/prtg-sensor-NAME`, running as its own systemd service behind
  a Unix socket. Access is limited to members of the MPP service account's
  group (`0660`, owned by `root`).
- The hardening of `prtg.mpprobe.service` is left untouched. Sudo is
  ineffective there because of `NoNewPrivileges=yes`, which the vendor
  intends, so it is neither used nor weakened. The probe helper creates the
  socket units itself from the validated sensor name; only text travels over
  the management channel, never a target path and never a unit file.
- A sensor is adopted only once it runs after installation under the conditions
  of the MPP service - as the service account and with its hardening,
  reproduced with `systemd-run` - and produces valid Script v2 JSON. Otherwise
  the probe restores the previous state.
- Credentials for sensors belong in profile files on the probe
  (`/etc/prtg-nats/sensors/NAME/profiles/*.env`, mode `0600`, readable by root
  only). Centrally they live under `runtime/sensor-profiles/`, in the same
  git-ignored area as the NATS passwords. Passwords passed as sensor
  parameters, by contrast, are stored by PRTG in the core and shown in the
  sensor settings.

## Measurement endpoints

- The password of an iperf3 endpoint is created on the NATS server and sent
  there, not the other way round: the endpoint keeps only its SHA-256 and could
  not hand the password back. Centrally it lives in `runtime/iperf/NAME.env`
  with mode `0600`, in the same git-ignored area as the NATS passwords. On the
  way to the endpoint it travels as a file over the existing SSH session, never
  as an argument that would appear in a process list or a shell history. It
  reaches a probe as a sensor profile, over the same path as every other
  credential - a second channel for the same thing would be a second place to
  get permissions wrong.
- A measurement endpoint gets **no** permanent management access from the NATS
  server. It is measured, not managed; every intervention signs in afresh. The
  footprint on a machine that is only a counterpart therefore stays a systemd
  drop-in and a key pair.

## Sensors that touch the network

- A WLAN test runs only on an explicitly reserved radio interface, never on an
  interface carrying the default route. A mis-set sensor parameter therefore
  cannot separate the probe from NATS or the host from its administration.

## Identity

- Probe id and PRTG access key live per probe in the runtime inventory
  `runtime/probes/USER.env` with mode `0600`. That keeps repeated runs
  idempotent. The access key is not a login secret for NATS; it is the probe
  key PRTG checks.

## What is published

- The git repository contains no instance-specific CA. Caddy serves only the
  public CA and the PRTG-compatible NATS and JetStream status over HTTP, never
  keys and never credentials.
- HTTP carries the public trust anchor and the non-sensitive NATS health status
  and nothing else. The SHA-256 fingerprint it shows has to be compared through
  `./prtg-nats ca-info` or another trusted administrative channel.

## Network exposure

- Monitoring port `8222` is published on `127.0.0.1` only.
- Client port `23561` is bound to the configured host address.
- The CA download port `80` is bound to the same host address.
- The HTTPS interface port `443` is bound to the same host address; the API
  itself listens on loopback behind Caddy.
- JetStream lives in the persistent Docker volume `prtg-nats-data`.
- The network, or the host firewall, has to limit `23561/tcp` to the PRTG core
  and the approved probe source addresses. Port `80/tcp` likewise has to be
  reachable only from the probe and administration networks that need it.

## Accounts

The PRTG core and existing older probes can keep using the protected shared
account `prtg-nats`. Every new probe gets a NATS account of its own. In
addition, **every probe gets its own PRTG access key**; the NATS password and
the PRTG access key are two independent credentials.
