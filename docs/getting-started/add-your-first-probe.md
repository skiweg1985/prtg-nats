---
title: Add your first probe
role: deployer
updated: 2026-08-04
---

# Roll out a new MPP in about 10 minutes

This guide covers a native PRTG Multi-Platform Probe on Ubuntu, Debian,
Raspberry Pi OS or RHEL 9. The central NATS server stays
`nats.example.com:23561`.

## Create a dedicated NATS account

With the central SSH push, `install-mpp` creates a missing account
automatically, so creating it separately up front is optional:

```bash
cd /opt/prtg-nats-server
sudo ./prtg-nats user add mpp-probe-01
```

Pick the account name to match the probe host. Whether created automatically
or by hand, the random password is stored in the runtime volume under
`credentials/mpp-probe-01.env` with mode `0600`. Show it again with
`sudo ./prtg-nats user show mpp-probe-01`.

## Recommended: clone the repository directly on the new MPP

Prerequisites:

- the new Linux host can reach `nats.example.com:23561`, and resolves that
  name - a probe in a network with its own resolver often does not, and then
  the address from `NATS_HOST_IP` stands in its place: the server certificate
  covers both;
- the host can reach `http://nats.example.com/nats-ca.pem`, or the CA can be
  copied over a secure administrative channel;
- the new host can reach the internal Gitea and `packages.paessler.com`;
- a sudo-capable user exists.

Directly on the new MPP:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone <REPOSITORY-URL>
cd prtg-nats-server
sudo ./install-mpp.sh
```

The repository deliberately contains no instance-specific CA. The installer
first asks for the NATS FQDN and then tries automatically:

```text
http://NATS-FQDN/nats-ca.pem
```

If that endpoint is not reachable, the script asks for the complete PEM
certificate to be pasted. On the NATS host it can be printed without any
extra output:

```bash
cd /opt/prtg-nats-server
sudo ./prtg-nats ca-show
```

Before installing the MPP package and the CA, the installer shows:

- the source it used;
- subject and issuer;
- the start and end of validity;
- the SHA-256 fingerprint;
- the intended NATS endpoint.

Compare the fingerprint over a trustworthy administrative channel:

```bash
sudo ./prtg-nats ca-info
```

HTTP only transports the public certificate here; it does not establish
trust. The installation continues only after the comparison, once the
`[y/N]` prompt is confirmed with `y` or `yes`. The script then installs the
signed `prtgmpprobe` package matching the CPU architecture from the official
Paessler repository.

A dry run that changes nothing:

```bash
./install-mpp.sh \
  --nats-host nats.example.com \
  --dry-run
```

For automation, a fingerprint obtained beforehand over a trustworthy channel
can be pinned:

```bash
sudo ./install-mpp.sh \
  --nats-host nats.example.com \
  --ca-sha256 EXPECTED_SHA256_WITHOUT_COLONS \
  --accept-ca
```

After a successful run, continue directly with
[adding the access key in PRTG](#add-the-access-key-in-prtg).

## Supported Git installation targets

| Target | Supported architectures |
| --- | --- |
| Ubuntu 20.04, 22.04, 24.04 | `amd64`, `armhf`, `arm64` |
| Debian 11, 12, 13 | `amd64`, `armhf`, `arm64` |
| Raspberry Pi OS Debian 11, 12, 13 | ARMv7/`armhf`, ARMv8/`arm64` |
| RHEL 9 | `x86_64` |

The same call works on Raspberry Pi OS:

```bash
git clone <REPOSITORY-URL>
cd prtg-nats-server
sudo ./install-mpp.sh
```

The installer recognises `raspbian` through `ID_LIKE=debian` and
automatically uses the matching Debian codename and the native package
architecture. Here too, the CA is fetched for the given NATS instance or
pasted interactively.

## Why the MPP package is not in Git

The repository deliberately contains no `.deb` or `.rpm` package:

- Paessler publishes separate packages for `amd64`, `armhf` and `arm64`;
- the current package is roughly 24 to 27 MB per architecture;
- a committed package would go stale quickly and receive no normal package
  updates;
- the Paessler package conditions refer to the
  [Paessler terms](https://www.paessler.com/company/terms), so the package is
  not redistributed internally without a separate approval.

The installer instead sets up the signed official repository, and APT or DNF
picks the right package automatically. For isolated networks, use an approved
internal APT/RPM proxy or a Gitea package registry mirror, not the Git
repository itself.

## Alternative: installation from the NATS host over SSH

If inbound SSH to the new MPP is allowed, this is the recommended path for
mass rollout.

Prerequisites:

- an existing `root` or sudo-capable bootstrap user can sign in by SSH key,
  SSH agent, or once by SSH password;
- the new host reaches `packages.paessler.com`;
- this repository is up to date on the NATS host.

On the NATS host:

```bash
cd /opt/prtg-nats-server
git pull --ff-only
./prtg-nats install-mpp ADMIN@NEW-MPP-HOST \
  --nats-user DEDICATED-NATS-USER
```

Example:

```bash
./prtg-nats install-mpp pi@probe-01.example.com \
  --nats-user mpp-probe-01
```

Show the complete sequence without changing the target system:

```bash
./prtg-nats install-mpp pi@probe-01.example.com \
  --nats-user mpp-probe-01 \
  --dry-run
```

### The probe name when connecting through an IP address

The probe name is derived from the host name: `probe-01.example.com` becomes
`multi-platform-probe@probe-01`. When the probe is connected through its
address, it reports its own host name and that is used - PRTG then shows
`multi-platform-probe@probe-01` instead of the address.

Only when the probe reports no usable host name does the command ask, and it
suggests a name built from the address. To set the name right away, pass it:

```bash
./prtg-nats install-mpp pi@192.0.2.18 \
  --nats-user mpp-probe-01 \
  --probe-name multi-platform-probe@site-north
```

It can be changed at any time later; the name is picked up on the next
configuration rollout:

```bash
./prtg-nats probe configure mpp-probe-01 \
  --probe-name multi-platform-probe@site-north
```

Probe id and access key stay unchanged, so PRTG keeps recognising the probe
as the same one.

### Order: key first, then the installation

The command always checks first whether the restricted access
`prtg-nats-admin@HOST` answers with the management key.

**The first run on a host** (bootstrap target required):

1. creates the NATS account given with `--nats-user`, if it does not exist
   yet;
2. opens a one-time interactive bootstrap SSH session;
3. **sets up key-based access first**: the `prtg-nats-admin` system user, the
   management public key with a forced command, restricted to the NATS server
   address, plus the probe helper at
   `/usr/local/sbin/prtg-nats-probe-helper`;
4. confirms and pins the SSH host key in the process and immediately tests
   the new access with a management request;
5. copies `install-mpp.sh`, the render library, the configuration template
   and only the public NATS CA into a temporary, protected directory;
6. passes the expected SHA-256 digest of the local CA and confirms it inside
   the central flow without a redundant second prompt;
7. installs the official Paessler repository and `prtgmpprobe`;
8. installs the CA at `/etc/paessler/mpprobe/certs/nats-docker-ca.pem`;
9. checks DNS and TCP `23561`;
10. closes the bootstrap session and removes temporary files;
11. generates the configuration centrally and rolls it out transactionally
    over the key channel;
12. enables and checks `prtg.mpprobe.service` and waits for the new NATS
    connection before the transaction completes;
13. prints the probe access key.

**Every further run** needs neither a bootstrap target nor a password:

```bash
./prtg-nats install-mpp --nats-user mpp-probe-01
```

If the package is still missing on the target, the command says so and
explicitly demands a bootstrap target - it does not install packages over the
restricted channel.

Enrollment is on by default. Only for a deliberate special case can it be
skipped with `--no-enroll`; the installer then writes the configuration
itself and receives the password as a temporary, protected file that is
removed together with the staging directory. Automatic enrollment requires a
dedicated `--nats-user USER`; the protected shared core/legacy-probe account
is not used for it.

### Generated configuration instead of the wizard

The runtime configuration is generated from
`config/mpprobe-config.yaml.template`. Only the core values are substituted:

| Placeholder | Source |
| --- | --- |
| `PROBE_ID` | inventory, else the existing probe configuration, else a new UUID |
| `ACCESS_KEY` | inventory, else the existing configuration, else `UUID-hostname` |
| `PROBE_NAME` | inventory, else `multi-platform-probe@hostname` |
| `NATS_HOST`, `NATS_PORT` | `.env`, or `--nats-host`/`--nats-port` |
| `NATS_USER`, `NATS_PASSWORD` | `credentials/USER.env` in the runtime volume |
| `SERVER_CA` | `/etc/paessler/mpprobe/certs/nats-docker-ca.pem` |
| `CLIENT_NAME` | `prtgmpprobe`, overridable with `--client-name` |

All other values - scheduler, logging, publisher, observability port
`23562` - are fixed, versioned defaults. To change them, adjust the template
and roll it out again:

```bash
sudo ./prtg-nats probe configure mpp-probe-01
```

The rendered configuration can be previewed without changing any system. The
credential file sits in the runtime volume, so ask Docker where that is:

```bash
runtime="$(docker volume inspect --format '{{.Mountpoint}}' prtg-nats-runtime)"
./install-mpp.sh --render-config \
  --nats-host nats.example.com \
  --nats-user mpp-probe-01 \
  --probe-host probe-01.example.com \
  --nats-password-file "${runtime}/credentials/mpp-probe-01.env"
```

The official wizard remains as a fallback:

```bash
./prtg-nats install-mpp pi@probe-01.example.com \
  --nats-user mpp-probe-01 \
  --wizard
```

Probe id and access key are kept per probe in `probes/USER.env` in the runtime
volume (mode `0600`) and reused on repeated runs. That way no second probe
appears in PRTG. Show them:

```bash
sudo ./prtg-nats probe show mpp-probe-01
```

If the probe installation fails after the account was created, the new NATS
account only survives when it already existed before the command, or when the
remote installer completed successfully. An account that was auto-created
just for this run is taken back out of the active NATS configuration on SSH
failure, a rejected CA, an aborted configuration or an installation error.

The configuration rollout is transactional: if `prtg.mpprobe.service` does
not start, or the new NATS connection does not appear in the monitoring, the
probe automatically restores its previous state. On a first-time
configuration, no half-finished `config.yaml` is left behind in that case.

## If the installer does not run

For the fallback of installing by hand there is a dedicated guide:
[Install a probe by hand](../guides/manual-probe-install.md). The two following
steps apply to both paths.

## Add the access key in PRTG

In PRTG:

`Setup > System Administration > Probes > Probe Connection Settings`

Add the **probe access key** printed at the end of `install-mpp` as its own
line in the **Access Keys** field and save. It can be shown again at any
time - with `./prtg-nats probe show USER` on the NATS host, or in the web
interface on the probe's **Overview** tab, where **PRTG access key** carries a
**Reveal** button. Both read the same inventory file; the web interface
records who looked in the audit trail. Do not overwrite existing keys. If PRTG
asks for a core restart, confirm it in a maintenance window.

The field holds all keys, one per line. So that each line can be attributed
to its probe, every generated key starts with a readable part taken from the
probe name, followed by the random part - `multi-platform-probe@site-north`
becomes `site-north-4cd483d8-…`. The random part is kept in full; the
readable part exists only for attribution.

Keys already handed out stay unchanged even if the probe name is changed
later - a new key would otherwise have to be updated here as well.

![PRTG access keys for a new MPP](../images/prtg-probe-access-key-settings.png)

The screenshot deliberately uses a masked example value. In the production
configuration, add the complete, unique access key of the new MPP as an
additional line.

Access keys belong neither in this repository nor in screenshots or tickets.

## Approve the probe in PRTG

A new probe connection request appears in PRTG:

1. Check the probe name and the expected host.
2. Select **Approve**.
3. Do **not** select `Approve and auto-discover`.
4. Create one test device and one supported sensor manually.
5. Check current readings and the connection health sensor.

If the probe was accidentally denied before, remove its GID from the
`Deny GIDs` list under the probe connection settings and then restart
`prtg.mpprobe.service`.

## Completion checklist

- [ ] unique probe name
- [ ] dedicated NATS account and dedicated NATS password
- [ ] dedicated probe access key generated and noted
- [ ] access key added to PRTG as an additional line
- [ ] DNS and TCP `23561` work
- [ ] CA fetched automatically over port `80` or pasted securely
- [ ] CA fingerprint compared over a trustworthy channel
- [ ] CA at the target path with mode `0644`
- [ ] generated `config.yaml` uses TLS, the FQDN and username/password
- [ ] probe enrolled centrally and SSH host key verified
- [ ] service is active and the logs are clean
- [ ] probe approved with **Approve** only
- [ ] test sensor delivers current readings
- [ ] new MPP address recorded in firewall and operations documentation

## If something does not work

The first measure is the endpoint test on the probe. It builds the connection
exactly the way the MPP does - DNS, TCP, NATS greeting, TLS upgrade - and
says in which phase it sticks, without changing anything:

```bash
sudo ./install-mpp.sh --nats-host nats.example.com --check-only
```

If it reports a reset during the TLS upgrade, the cause is a firewall along
the path, not the certificate or the configuration; the details are under
[the connection drops at the TLS upgrade](../guides/troubleshooting.md#the-connection-drops-at-the-tls-upgrade).

If `install-mpp` fails, it asks whether the created NATS account and the
management access should be rolled back. If you are still looking for the
cause, answer `n`: the next attempt then needs neither the bootstrap password
nor a new NATS password. For automation there are `--keep-on-failure` and
`--rollback-on-failure`.

Further symptoms, causes and measures are collected in
[Troubleshooting](../guides/troubleshooting.md).
