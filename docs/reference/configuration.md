---
title: Configuration reference
role: operator
updated: 2026-08-03
---

# Configuration reference

Every setting of the stack, in one place. Two files carry configuration, and
they are read by different things:

| File | Read by | Written by |
| --- | --- | --- |
| `.env` | Compose, the shell tooling, the web platform | `./prtg-nats config --edit` |
| `runtime/conf/nats-server.conf` | the NATS container | generated, never edited by hand |

The normal way to change `.env` is the dialog, which validates every entry and
copies the previous file to `.env.bak-<timestamp>` beside it - `.env` is host
state that Compose reads from the checkout, and the dialog runs before there is
necessarily a runtime volume to write into:

```bash
sudo ./prtg-nats config --edit
```

Show the effective values, and whether each one comes from `.env` or from a
default:

```bash
./prtg-nats config
```

Editing `.env` by hand is supported for automation; the template with the
same options is [.env.example](../../.env.example). `.env` is written with
mode `600` and is never committed.

## Site settings

These have no default because no default could be right. `setup` refuses to
finish without them.

| Name | Description | Type | Default | Required | Example |
| --- | --- | --- | --- | --- | --- |
| `NATS_FQDN` | Name of the NATS server. Has to resolve in DNS; it becomes the SAN of the server certificate, and the PRTG core and every probe connect through it | hostname | – | yes | `nats.example.com` |
| `NATS_HOST_IP` | Host address the containers publish their ports on | IPv4 address | – | yes | `192.0.2.10` |
| `PRTG_CORE_IP` | Address of the PRTG core, used for firewall rules and documentation | IPv4 address | – | yes | `192.0.2.20` |

`NATS_FQDN` and `NATS_PORT` are the single source for the NATS endpoint. They
apply at once to the server configuration, the Docker port binding, the check
commands and every generated probe configuration - changing them is one
operation, and it belongs in a maintenance window. The details are under
[Site settings](../getting-started/install-the-server.md#3-site-settings).

> [!NOTE]
> A generated probe configuration always receives `NATS_FQDN`, never
> `NATS_HOST_IP`. The server certificate carries only the FQDN as a SAN, so an
> address would make the TLS check on the probe fail.

## Ports and certificate

| Name | Description | Type | Default | Required | Example |
| --- | --- | --- | --- | --- | --- |
| `NATS_PORT` | TLS client port used by the PRTG core and every probe | port | `23561` | no | `23561` |
| `CA_HTTP_PORT` | Port the public CA is served on over HTTP | port | `80` | no | `8080` |
| `CA_ORGANIZATION` | Organisation in the subject of the generated CA. Affects a new CA only - changing it later does not reissue anything | string | `PRTG NATS` | no | `Example Ltd` |

If port `80` cannot be used operationally, set `CA_HTTP_PORT` and install new
probes with `--ca-url http://FQDN:PORT/nats-ca.pem`. The NATS TLS port is
unaffected.

## Management channel

| Name | Description | Type | Default | Required | Example |
| --- | --- | --- | --- | --- | --- |
| `MPP_SSH_SOURCE_CIDR` | Source range the restricted management key is accepted from, written into the `from="…"` restriction on every probe | CIDR | `NATS_HOST_IP/32` | no | `192.0.2.0/24` |

The default is the tightest one that works: the key is valid from the NATS
host and nowhere else. Widen it only when the outgoing address is not stable -
a NAT gateway, or a second management host - and then to the smallest range
that covers it. A change takes effect on a probe with its next configuration
rollout.

## Web platform

| Name | Description | Type | Default | Required | Example |
| --- | --- | --- | --- | --- | --- |
| `WEB_HTTPS_PORT` | Host port of the web interface; TLS terminates in the reverse proxy | port | `8443` | no | `8443` |
| `WEB_FQDN` | Name the certificate of the web interface is issued for | hostname | value of `NATS_FQDN` | no | `admin.example.com` |

The default for `WEB_FQDN` is right for a single-server installation. Set it
only when the interface is reached under a different name than the NATS
endpoint.

## Where the shell tooling looks for the runtime

The installation lives in the `prtg-nats-runtime` volume. `prtg-nats` and the
scripts under `libexec/` read the volume's mountpoint from Docker, so neither
of these is normally set. They are environment variables, not `.env` keys: they
change where a single command looks, which is not a property of the
installation.

| Name | Description | Type | Default | Required | Example |
| --- | --- | --- | --- | --- | --- |
| `PRTG_NATS_RUNTIME_DIR` | Use this directory as the runtime instead of looking the volume up | path | the volume's mountpoint | no | `/srv/prtg-nats/runtime` |
| `PRTG_NATS_RUNTIME_VOLUME` | Look up a different volume | string | `prtg-nats-runtime` | no | `prtg-nats-runtime-restored` |

`PRTG_NATS_RUNTIME_DIR` is what a nested environment needs: the end-to-end test
drives `prtg-nats` from inside a container that talks to the host's Docker
socket, where the host's mountpoint does not resolve. It also serves a restore,
together with `PRTG_NATS_RUNTIME_VOLUME` - see
[Operations](../guides/operations.md#restore).

When the volume does not exist yet, the tooling falls back to `runtime/` beside
the checkout, so the help and `config` still work on a machine that has not
been set up.

### Keys under `runtime/private/`

Nothing here is configured; the files are listed because a backup that misses
one of them is a backup that cannot manage the fleet afterwards.

| File | What it is |
| --- | --- |
| `ca-key.pem` | the CA that signs the NATS server and client certificates |
| `ssh/prtg-nats-mpp-admin` | the management key that opens the probe channel |
| `ssh/known_hosts` | the pinned host keys of every enrolled probe |
| `helper-signing-key.pem` | signs the probe helper before it is sent over the channel |
| `helper-signing.pub` | the public half, installed on a probe during enrollment |

The signing pair is created on first use, so an installation from before it
existed grows one the moment a helper update or an enrollment needs it. Losing
it means the probes reject every further helper update until they are enrolled
again - see
[ADR 0006](../architecture/decisions/0006-signed-helper-updates.md).

## Backend settings

The management API reads its own settings from the environment, prefixed with
`PRTG_NATS_WEB_`, and falls back to `.env`. Every one has a default that works;
the container image sets what it needs. They are listed here because they are
what a support case asks about, not because they normally need changing.

<details>
<summary>All <code>PRTG_NATS_WEB_*</code> settings</summary>

### Identity and paths

| Name | Description | Type | Default | Required |
| --- | --- | --- | --- | --- |
| `PRTG_NATS_WEB_ENVIRONMENT` | `production` or `development`. Production refuses the development sign-in | string | `production` | no |
| `PRTG_NATS_WEB_DEBUG` | Verbose error output | bool | `false` | no |
| `PRTG_NATS_WEB_PROJECT_DIR` | One tree holding both roots below. Setting this alone configures a whole installation, which is what local development and the tests want | path | `/opt/prtg-nats-server` | no |
| `PRTG_NATS_WEB_ASSET_DIR` | Files that ship with the release: templates, sensors, on-target scripts. Read-only, replaced wholesale by a new image | path | `PROJECT_DIR` | no |
| `PRTG_NATS_WEB_RUNTIME_DIR` | State this installation owns: keys, credentials, inventory, database. The only part worth backing up | path | `PROJECT_DIR/runtime` | no |
| `PRTG_NATS_WEB_DATABASE_URL` | Override for the database. Empty means `runtime/web.db` | string | derived | no |

Two roots, because they have different lifetimes. The container image carries
the assets at `/opt/prtg-nats` and mounts the runtime volume at
`/srv/prtg-nats/runtime`; `compose.yaml` sets both. They fall together under
`PROJECT_DIR` when neither is set, which is the shape a checkout has.

Paths below the runtime root - certificates, credentials, the probe inventory,
the management key - are **not** configurable. They are derived exactly the way
`libexec/common.sh` derives them, because a second, silently diverging truth is
worse than an inflexible one.

### HTTP and sessions

| Name | Description | Type | Default | Required |
| --- | --- | --- | --- | --- |
| `PRTG_NATS_WEB_HOST` | Bind address. Loopback, because Caddy is the only thing that opens a port | string | `127.0.0.1` | no |
| `PRTG_NATS_WEB_PORT` | Bind port | port | `8100` | no |
| `PRTG_NATS_WEB_CORS_ORIGINS` | Additional allowed origins | list | empty | no |
| `PRTG_NATS_WEB_SESSION_COOKIE_NAME` | Name of the session cookie | string | `prtg_nats_session` | no |
| `PRTG_NATS_WEB_SESSION_COOKIE_SECURE` | Send the cookie over HTTPS only. Off for plain-HTTP development | bool | `true` | no |
| `PRTG_NATS_WEB_SESSION_LIFETIME_HOURS` | Absolute session lifetime | int | `12` | no |
| `PRTG_NATS_WEB_SESSION_IDLE_TIMEOUT_MINUTES` | Idle timeout | int | `60` | no |

### Sign-in throttling

| Name | Description | Type | Default | Required |
| --- | --- | --- | --- | --- |
| `PRTG_NATS_WEB_LOGIN_MAX_ATTEMPTS` | Failed attempts before the lockout starts | int | `5` | no |
| `PRTG_NATS_WEB_LOGIN_LOCKOUT_BASE_SECONDS` | First lockout, doubled on each further failure | int | `5` | no |
| `PRTG_NATS_WEB_LOGIN_LOCKOUT_MAX_SECONDS` | Ceiling for the lockout | int | `900` | no |

### Adapters and background work

| Name | Description | Type | Default | Required |
| --- | --- | --- | --- | --- |
| `PRTG_NATS_WEB_NATS_MONITORING_URL` | Where the NATS monitoring endpoint is read from | URL | `http://127.0.0.1:8222` | no |
| `PRTG_NATS_WEB_DOCKER_SOCKET` | Docker socket for the lifecycle actions. Without it, restart and backup disappear from the interface and everything else keeps working | path | `/var/run/docker.sock` | no |
| `PRTG_NATS_WEB_SSH_CONNECT_TIMEOUT_SECONDS` | Timeout for reaching a probe | int | `10` | no |
| `PRTG_NATS_WEB_SSH_COMMAND_TIMEOUT_SECONDS` | Timeout for one management request | int | `120` | no |
| `PRTG_NATS_WEB_JOB_WORKER_COUNT` | Concurrent job workers | int | `4` | no |
| `PRTG_NATS_WEB_INVENTORY_SYNC_INTERVAL_SECONDS` | How often `runtime/` is re-read | int | `60` | no |
| `PRTG_NATS_WEB_OBSERVED_STATE_STALE_AFTER_SECONDS` | Age at which a probe's reported state counts as stale | int | `300` | no |
| `PRTG_NATS_WEB_CERTIFICATE_EXPIRY_WARNING_DAYS` | Lead time for the certificate warning | int | `30` | no |

### Development only

| Name | Description | Type | Default | Required |
| --- | --- | --- | --- | --- |
| `PRTG_NATS_WEB_DEV_AUTH_ENABLED` | Skip sign-in and act as an administrator | bool | `false` | no |

> [!WARNING]
> `PRTG_NATS_WEB_DEV_AUTH_ENABLED` disables authentication entirely. The
> application refuses to start when it is combined with
> `PRTG_NATS_WEB_ENVIRONMENT=production`, and the interface carries a permanent
> banner while it is on.

</details>

## Probe configuration

The runtime configuration of a probe is not configured here. It is rendered
from [config/mpprobe-config.yaml.template](../../config/mpprobe-config.yaml.template)
and rolled out over the management channel. Only these values are substituted;
everything else - scheduler, logging, publisher, observability port `23562` -
is a fixed, versioned default.

| Placeholder | Source |
| --- | --- |
| `PROBE_ID` | inventory, else the existing probe configuration, else a new UUID |
| `ACCESS_KEY` | inventory, else the existing configuration, else `UUID-hostname` |
| `PROBE_NAME` | inventory, else `multi-platform-probe@hostname` |
| `NATS_HOST`, `NATS_PORT` | `.env`, or `--nats-host`/`--nats-port` |
| `NATS_USER`, `NATS_PASSWORD` | `runtime/credentials/USER.env` |
| `SERVER_CA` | `/etc/paessler/mpprobe/certs/nats-docker-ca.pem` |
| `CLIENT_NAME` | `prtgmpprobe`, overridable with `--client-name` |

To change a fixed default, edit the template and roll it out again:

```bash
sudo ./prtg-nats probe configure mpp-probe-01
```

Preview the rendered result without changing any system:

```bash
./install-mpp.sh --render-config \
  --nats-host nats.example.com \
  --nats-user mpp-probe-01 \
  --probe-host probe-01.example.com \
  --nats-password-file runtime/credentials/mpp-probe-01.env
```

## Changing a setting safely

1. Back up first - `sudo ./prtg-nats backup`, and copy `runtime/` away.
2. Change the value with `sudo ./prtg-nats config --edit`.
3. Recreate the stack: `sudo ./prtg-nats update`.
4. For anything that reaches a probe - the endpoint, the management source
   range - roll the configuration out per probe:
   `sudo ./prtg-nats probe configure USER`.
5. Check with `sudo ./prtg-nats verify` and
   `sudo ./prtg-nats probe status --all`.

An endpoint change also has to be made on the PRTG core, in the same
maintenance window.

## Related pages

- [Install the server](../getting-started/install-the-server.md) - where these
  values are asked for the first time.
- [Command reference](cli.md) - the commands that read and apply them.
- [Operations and maintenance](../guides/operations.md) - rotation, backup,
  updates.
- [Security model](../security/model.md) - what the values expose, and to whom.
