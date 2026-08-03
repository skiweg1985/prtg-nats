---
title: Manual probe install
role: deployer
updated: 2026-08-03
---

# Install a probe by hand

This path is the fallback for when `install-mpp.sh` does not run - say, the
host cannot reach the repository, or the package format changed. **The normal
path is the automated one** in
[Add your first probe](../getting-started/add-your-first-probe.md); it is
tested and a lot shorter.

The steps here lead up to a running probe. After that, continue in
[Add your first probe](../getting-started/add-your-first-probe.md) with
entering the access key and approving the probe in PRTG - those two steps are
identical for both paths.

## Manual fallback

The following steps document the same sequence in full, for when the
installer cannot be used.

## Have ready beforehand

| Value | Guideline |
| --- | --- |
| unique probe name | for example `multi-platform-probe@probe-01` |
| dedicated PRTG access key | is generated; add it to PRTG afterwards |
| NATS FQDN | `nats.example.com` |
| NATS port | `23561` |
| NATS user | a dedicated account, for example `mpp-probe-01` |
| NATS password | its protected hand-over file on the NATS host |
| CA target path | `/etc/paessler/mpprobe/certs/nats-docker-ca.pem` |

The **PRTG access key has to be unique per probe**. It is generated once,
stays stable afterwards and is added to PRTG. It is not the NATS password.

## 1. Test the network up front

On the new MPP system:

```bash
getent hosts nats.example.com
timeout 5 bash -c '</dev/tcp/nats.example.com/23561'
curl --fail --show-error \
  http://nats.example.com/nats-ca.pem \
  --output /tmp/nats-docker-ca.pem
```

DNS has to point at `192.0.2.10` and TCP `23561` has to be reachable. The CA
download uses TCP `80` by default. If it is not reachable, step 3 can be done
with copy/paste instead.

## 2. Install the MPP

If `prtgmpprobe` is already installed, continue with step 3.

### Debian/Ubuntu

```bash
curl --fail --silent https://packages.paessler.com/keys/paessler.asc \
  | sudo tee /usr/share/keyrings/paessler-archive-keyring.asc >/dev/null
curl --fail --silent \
  "https://packages.paessler.com/docs/apt-sources/$(. /etc/os-release && echo "${VERSION_CODENAME}").sources" \
  | sudo tee /etc/apt/sources.list.d/paessler.sources >/dev/null
sudo apt-get update
sudo apt-get install prtgmpprobe
```

### RHEL 9

```bash
sudo dnf config-manager --add-repo \
  https://packages.paessler.com/docs/rpm-sources/rhel-9.repo
sudo dnf install prtgmpprobe
```

Before rolling out on further distributions, check the currently supported
versions in the
[Paessler MPP manual](https://manuals.paessler.com/multiplatformprobemanual.pdf).

## 3. Fetch or paste the public NATS CA

The normal case, on the new MPP system:

```bash
curl --fail --show-error \
  http://nats.example.com/nats-ca.pem \
  --output /tmp/nats-docker-ca.pem
openssl x509 -in /tmp/nats-docker-ca.pem \
  -noout -subject -issuer -dates -fingerprint -sha256
```

Compare the fingerprint independently on the NATS host:

```bash
cd /opt/prtg-nats-server
sudo ./prtg-nats ca-info
```

If port `80` is not available, print only the public PEM on the NATS host and
copy it to the MPP over an approved administrative channel:

```bash
sudo ./prtg-nats ca-show
```

After a successful comparison, on the MPP:

```bash
sudo install -d -o root -g root -m 0755 /etc/paessler/mpprobe/certs
sudo install -o root -g root -m 0644 \
  /tmp/nats-docker-ca.pem \
  /etc/paessler/mpprobe/certs/nats-docker-ca.pem
openssl verify \
  -CAfile /etc/paessler/mpprobe/certs/nats-docker-ca.pem \
  /etc/paessler/mpprobe/certs/nats-docker-ca.pem
rm -f /tmp/nats-docker-ca.pem
```

The HTTP container only ever has access to the public copy. CA and server
keys as well as NATS credentials stay in the protected, git-ignored runtime
state on the NATS host.

## 4. Generate the configuration

The non-secret values can be shown on the NATS host at any time:

```bash
cd /opt/prtg-nats-server
./prtg-nats mpp-info mpp-probe-01
```

On the new MPP system, the installer generates the configuration itself:

```bash
sudo ./install-mpp.sh --nats-host nats.example.com \
  --nats-user mpp-probe-01
```

The password is prompted for without echo. What gets written is
`/etc/paessler/mpprobe/config.yaml` with these core values:

| Field | Value |
| --- | --- |
| `name` | unique probe name, default `multi-platform-probe@hostname` |
| `access_key` | unique value, add it to PRTG afterwards |
| `nats.url` | `tls://nats.example.com:23561` |
| `nats.server_ca` | `/etc/paessler/mpprobe/certs/nats-docker-ca.pem` |
| `nats.authentication.user` | the dedicated account, for example `mpp-probe-01` |
| `nats.authentication.password` | from `runtime/credentials/USER.env` |

For the TLS host, always enter the FQDN and never just `192.0.2.10`: the name
has to match the SAN of the server certificate.

To run the official wizard instead:

```bash
sudo ./install-mpp.sh --nats-host nats.example.com \
  --nats-user mpp-probe-01 \
  --wizard
```

## 5. Start and check the service

```bash
sudo systemctl enable --now prtg.mpprobe.service
sudo systemctl restart prtg.mpprobe.service
sudo systemctl status prtg.mpprobe.service --no-pager
sudo journalctl -u prtg.mpprobe.service -n 200 --no-pager
```

Expected:

- the service is `active (running)`;
- no `tls: unknown certificate authority`;
- no `Authorization Violation`;
- no persistent `nats: IO error`.

The runtime configuration MPP 3.10.0 uses lives at:

```text
/etc/paessler/mpprobe/config.yaml
```

It contains the NATS password and the probe access key and therefore must not
be copied into tickets or chat.

## Enable central management afterwards

If the MPP was installed locally, enroll it on the main NATS server with an
existing root-capable or sudo-capable bootstrap account:

```bash
sudo ./prtg-nats probe enroll \
  mpp-probe-01 \
  root@probe-01.example.com
```

Before installation, the displayed SSH host key fingerprint has to be checked
and confirmed over a trustworthy channel. The `[y/N]` prompt accepts `y` or
`yes` in any case. After that, management uses only the `prtg-nats-admin`
account, the pinned host key and the forced command restricted to the NATS
server address.

Check the state:

```bash
sudo ./prtg-nats probe status mpp-probe-01
```
