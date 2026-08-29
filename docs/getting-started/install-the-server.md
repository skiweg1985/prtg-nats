---
title: Install the server
role: operator
updated: 2026-08-29
---

# Install the server

This sets up the Docker NATS server on `nats.example.com`. Day-to-day operation
needs either the single command `./prtg-nats` or the web interface, which comes
up with the same stack.

## 1. Prerequisites

- Ubuntu x86-64
- Docker Engine and Docker Compose v2
- OpenSSL and Git
- at least 10 GB of free local storage
- DNS resolution of `nats.example.com` to the host address
- TCP `23561` from the PRTG core and from every probe to the NATS host
- TCP `80` from the probe and administration networks, for the public CA
- TCP `443` from the administration network, for the web interface
- TCP `22` from the NATS host to centrally managed probes

The monitoring port `8222` must not be reachable from the network. With
Docker-published ports, do not assume an existing UFW rule takes effect
automatically. Restrict the permitted source addresses at the network filter as
well, or through a persistent `DOCKER-USER` policy.

## 2. Get the repository

```bash
sudo git clone --branch dev git@github.com:skiweg1985/prtg-nats.git /opt/prtg-nats
cd /opt/prtg-nats
```

The production checkout follows `dev`, the repository's default branch.
Configure an authorized GitHub SSH key on the host before cloning; the updater
can use a separate read-only deploy key later.

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
`600`. It only suggests a name that is actually qualified - a host whose
`hostname -f` answers the short name is asked instead of being handed a
suggestion that would end up in the certificate - and it asks back before
accepting a name without a domain. A second run offers the existing values as
defaults and copies the previous file to `.env.bak-<timestamp>` beside it
first. For automation, `.env` can still be written by hand following
`.env.example`.

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

The generated probe configuration uses `NATS_FQDN` by default because a name
survives a move to another host. The server certificate also carries
`NATS_HOST_IP` as an IP SAN, so a probe without access to the internal DNS can
use the configured address without disabling TLS verification. An installation
created before the IP SAN was added needs one
`sudo ./prtg-nats renew-certificate` before using that fallback.

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

If site settings are missing, `setup` asks for them first. It then starts the
stack and initialises it - in that order, and both are part of the same
command.

The order matters. The installation lives in the `prtg-nats-runtime` volume
that the containers own, and the initialisation runs inside the
`prtg-nats-web-api` container, so a stack has to exist for it to run in.
Nothing has to be installed on the host for this.

Initialisation creates:

1. a local CA, a NATS server certificate and the interface certificate;
2. a random NATS password, of which the NATS configuration stores only the
   bcrypt hash;
3. a dedicated Ed25519 key for probe management;
4. the server configuration under `conf/` in the volume.

Until it has run, NATS and the reverse proxy restart in a loop: neither has a
configuration or a certificate yet. That is deliberate - it lets the stack come
up before it is configured - and `setup` restarts both once the files exist,
rather than leaving them to sit out their backoff.

This is also why the initialisation cannot be deferred to the browser: the
proxy that serves [the web interface](../web/install.md) needs the certificate
the initialisation issues, so until it has run there is no interface to defer
to. Both paths run the same code, but this one has to come first.

`setup` is repeatable: a complete runtime state is not overwritten.

## 5. Generated files

These live in the `prtg-nats-runtime` volume, not beside the checkout. The
volume is the installation: moving it is the whole migration, and a `git pull`
cannot touch it. Paths below are relative to the root of the volume.

| Path | Contents | Hand out? |
| --- | --- | --- |
| `certs/ca.pem` | the public NATS CA | yes, to the core and to probes only |
| `public/nats-ca.pem` | the same CA, for the HTTP download | yes |
| `certs/server.pem` | the server certificate | no |
| `certs/server-key.pem` | the private server key | never |
| `web-certs/web.pem` | the certificate of the web interface | no |
| `private/ca-key.pem` | the private CA key | never |
| `credentials/prtg-nats.env` | the cleartext credential file | protected administrative access only |
| `credentials/USER.env` | the cleartext credentials of one probe | protected administrative access only |
| `auth-users/USER.auth` | account name and bcrypt hash | no |
| `private/ssh/prtg-nats-mpp-admin` | the private management SSH key | never |
| `private/ssh/prtg-nats-mpp-admin.pub` | the public management key | to managed probes only |
| `probes/USER.env` | the NATS account paired with its pinned SSH target | no |
| `conf/nats-server.conf` | the NATS configuration with the bcrypt hash | no |

Ask for what you need rather than reaching into the volume - these work
wherever the volume happens to sit:

```bash
sudo ./prtg-nats ca-show              # the public CA as PEM
sudo ./prtg-nats ca-path              # where it is right now
sudo ./prtg-nats user show prtg-nats  # the shared account's password
```

The CA is not versioned in the repository because it belongs to this NATS
instance and no other. Put the CA key and the credential file into the approved
secret and backup system. Do not copy their contents into a chat, a ticket, an
email or a shell history. Backing the volume up is its own procedure - see
[the runtime export](../guides/operations.md#runtime-export). Copying a
directory is not it.

The reverse proxy `prtg-nats-web-proxy` publishes `public/nats-ca.pem` and the
health CGI over plain HTTP, as `http://nats.example.com/nats-ca.pem`, and the
interface over HTTPS on `443`. A plain-HTTP request for anything other than
those two files is redirected to the interface, so the host name alone is
enough to find it. It mounts `public/` and `web-certs/` read-only and has no
access to private keys, credentials or the NATS configuration.

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
sudo ./prtg-nats verify   # or the maintenance page of the web interface
```

Expected:

- `prtg-nats` is `healthy`, and `prtg-nats-web-api` and `prtg-nats-web-proxy`
  are up - a proxy that runs is a proxy that found its certificate;
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

1. Open the web interface at `https://nats.example.com`. The first visit
   creates the administrator account - see
   [the web platform](../web/install.md).
2. [Connect the PRTG core](connect-prtg-core.md), once.
3. [Add a probe](add-your-first-probe.md), preferably by cloning the repository
   and running `sudo ./install-mpp.sh` directly on the new probe.
4. [Set up backups and regular operation](../guides/operations.md).

Switching the PRTG core over requires a core restart and belongs in a
maintenance window.
