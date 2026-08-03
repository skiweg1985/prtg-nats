---
title: Install the server
role: operator
updated: 2026-08-02
---

# Install the server

This sets up the Docker NATS server on `nats.example.com`. Day-to-day operation
only needs the single command `./prtg-nats`.

## 1. Prerequisites

- Ubuntu x86-64
- Docker Engine and Docker Compose v2
- OpenSSL and Git
- at least 10 GB of free local storage
- DNS resolution of `nats.example.com` to the host address
- TCP `23561` from the PRTG core and from every probe to the NATS host
- TCP `80` from the probe and administration networks, for the public CA
- TCP `22` from the NATS host to centrally managed probes

The monitoring port `8222` must not be reachable from the network. With
Docker-published ports, do not assume an existing UFW rule takes effect
automatically. Restrict the permitted source addresses at the network filter as
well, or through a persistent `DOCKER-USER` policy.

## 2. Get the repository

Replace `<REPOSITORY-URL>` with the actual address of this repository:

```bash
sudo git clone <REPOSITORY-URL> /opt/prtg-nats-server
cd /opt/prtg-nats-server
```

## 3. Site settings

Have these ready before you continue:

| Value | What it means |
| --- | --- |
| FQDN of the NATS server | has to resolve in DNS; it is the SAN in the server certificate, and the PRTG core and every probe connect through it |
| IP address for the container ports | the address of this host on which NATS and the CA endpoint are published |
| IP address of the PRTG core | for firewall rules and documentation |
| NATS port | default `23561` |
| Port for the CA download | default `80` |
| Organisation in the CA certificate | appears in the subject of the generated CA |

Setup asks for them - writing `.env` by hand is not necessary:

```bash
sudo ./prtg-nats config --edit
```

`./prtg-nats config` without an argument shows the effective values and whether
each comes from `.env` or is a default. The dialog suggests the FQDN and the
address from the system, checks every entry, and then writes `.env` with mode
`600`. A second run offers the existing values as defaults, and `.env` is
archived to `runtime/archive/` first. For automation, `.env` can still be
written by hand following `.env.example`.

`NATS_FQDN` and `NATS_PORT` are the single source for the NATS endpoint. They
apply at once to the server configuration, the Docker port binding, the check
commands and every generated probe configuration. Changing them is therefore
one operation, but it belongs in a maintenance window:

```bash
sudo ./prtg-nats config --edit
sudo ./prtg-nats update
sudo ./prtg-nats probe configure PROBE-USER   # per registered probe
```

The PRTG core has to be changed in the same operation.

The probe configuration always receives `NATS_FQDN`, never `NATS_HOST_IP`: the
server certificate carries only the FQDN as a SAN, and an IP address would make
the TLS check on the probe fail.

Port `80` has to be free on the host address:

```bash
sudo ss -ltnp '( sport = :80 )'
```

If it cannot be used operationally, set `CA_HTTP_PORT` to an approved
alternative. New probes are then installed with
`--ca-url http://FQDN:PORT/nats-ca.pem`. The NATS TLS port from `NATS_PORT` is
unaffected.

## 4. One-time setup

```bash
sudo ./prtg-nats setup
```

If site settings are missing, `setup` asks for them first. It then prepares
the runtime directories, initialises them when the Python backend is installed
on the machine, and starts the stack.

Initialisation creates:

1. a local CA and a NATS server certificate;
2. a random NATS password, of which the NATS configuration stores only the
   bcrypt hash;
3. a dedicated Ed25519 key for probe management;
4. the server configuration under `runtime/conf/`.

If the backend is not installed locally, the stack still starts - the NATS
container waits for its configuration - and the first visit to
[the web interface](../web/install.md) offers the same initialisation as a
job, with a live log. Both paths run the same code.

`setup` is repeatable: a complete runtime state is not overwritten.

## 5. Generated files

| Path | Contents | Hand out? |
| --- | --- | --- |
| `runtime/certs/ca.pem` | the public NATS CA | yes, to the core and to probes only |
| `runtime/public/nats-ca.pem` | the same CA, for the HTTP download | yes |
| `runtime/certs/server.pem` | the server certificate | no |
| `runtime/certs/server-key.pem` | the private server key | never |
| `runtime/private/ca-key.pem` | the private CA key | never |
| `runtime/credentials/prtg-nats.env` | the cleartext credential file | protected administrative access only |
| `runtime/credentials/USER.env` | the cleartext credentials of one probe | protected administrative access only |
| `runtime/auth-users/USER.auth` | account name and bcrypt hash | no |
| `runtime/private/ssh/prtg-nats-mpp-admin` | the private management SSH key | never |
| `runtime/private/ssh/prtg-nats-mpp-admin.pub` | the public management key | to managed probes only |
| `runtime/probes/USER.env` | the NATS account paired with its pinned SSH target | no |
| `runtime/conf/nats-server.conf` | the NATS configuration with the bcrypt hash | no |

`runtime/` and `backups/` are git-ignored. The CA is not versioned in the
repository because it belongs to this NATS instance and no other. Put the CA
key and the credential file into the approved secret and backup system. Do not
copy their contents into a chat, a ticket, an email or a shell history.

The container `prtg-nats-ca` publishes `runtime/public/nats-ca.pem` and nothing
else, as `http://nats.example.com/nats-ca.pem`. It has no access to private
keys, credentials or the NATS configuration.

Show the public key and its fingerprint:

```bash
sudo ./prtg-nats ssh-key info
sudo ./prtg-nats ssh-key show
```

The private key has no passphrase, so that a controlled rotation can run
unattended. It is therefore accessible to root only. On a probe the public key
grants no ordinary shell, only the installed credential and status helper.

## 6. Check the state

```bash
sudo ./prtg-nats status
sudo ./prtg-nats verify   # or the system page of the web interface
```

Expected:

- the containers `prtg-nats` and `prtg-nats-ca` are `healthy`;
- the local JetStream health check returns HTTP 200;
- the HTTP endpoint serves exactly the active public CA;
- the certificate chain and the SAN are valid;
- the authenticated TLS test succeeds.

An additional network check from the PRTG core:

```powershell
Resolve-DnsName nats.example.com
Test-NetConnection nats.example.com -Port 23561
Test-NetConnection nats.example.com -Port 80
Test-NetConnection nats.example.com -Port 8222
```

`23561` and `80` have to be reachable from the approved networks. `8222` has to
fail remotely.

## 7. Next steps

1. [Connect the PRTG core](connect-prtg-core.md), once.
2. [Add a probe](add-your-first-probe.md), preferably by cloning the repository
   and running `sudo ./install-mpp.sh` directly on the new probe.
3. [Set up backups and regular operation](../guides/operations.md).
4. Optionally, [install the web platform](../web/install.md).

Switching the PRTG core over requires a core restart and belongs in a
maintenance window.
