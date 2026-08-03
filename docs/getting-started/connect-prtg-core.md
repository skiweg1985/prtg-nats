---
title: Connect the PRTG core
role: operator
updated: 2026-08-02
---

# Connect the PRTG core

This NATS configuration is done once. It stays unchanged for further probes;
only the individual probe access key is added to the probe settings, and the
new probe is then approved.

## 1. Put the CA on the core

On the NATS host the public CA is here:

```text
/opt/prtg-nats-server/runtime/certs/ca.pem
```

Copy the file safely to the PRTG core as a PEM file with a `.crt` extension:

```text
C:\Program Files (x86)\PRTG Network Monitor\cert\nats-docker-ca.crt
```

Copy `ca.pem` only. Neither `ca-key.pem` nor `server-key.pem` may leave the
NATS host.

## 2. Check the network path

```powershell
Resolve-DnsName nats.example.com
Test-NetConnection nats.example.com -Port 23561
Test-NetConnection nats.example.com -Port 8222
```

Port `23561` has to succeed. Port `8222` must not be reachable remotely.

## 3. Set the PRTG fields

In PRTG:

`Setup > System Administration > Probes > Settings for NATS Connections`

| PRTG field | Value |
| --- | --- |
| NATS connections | Allow connections through a remote NATS server |
| Connection security for NATS | TLS |
| Hostname of the NATS server | `nats.example.com:23561` |
| NATS authentication | User name and password |
| User name for NATS | `prtg-nats` |
| Password for NATS | from the protected `runtime/credentials/prtg-nats.env` |
| CA handling | Provide the root certificate of the certificate authority |
| CA root certificate | `nats-docker-ca.crt`, or whichever file name you used |
| Log level | Info; Debug temporarily and only for diagnosis |

The file name of the CA certificate is free. What matters is that the file
contains the CA that signed the NATS server certificate.

Save, and confirm the PRTG core restart it asks for.

## 4. Acceptance

- The **Multi-Platform Probe Connection Health (Autonomous)** sensor is up.
- `C:\ProgramData\Paessler\PRTG Network Monitor\Logs\probeadapter` shows no
  TLS errors, no authentication errors and no continuous reconnects.
- The NATS container logs successful clients and no repeating TLS handshake
  failures.

For a remote NATS server PRTG supports user name and password, an NKey, or NATS
credentials. This stack deliberately uses user name and password. Details are
in the [official PRTG manual](https://manuals.paessler.com/probes.htm).
