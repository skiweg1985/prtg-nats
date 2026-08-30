---
title: An iperf endpoint somebody else operates
role: deployer
updated: 2026-08-30
---

# An iperf endpoint somebody else operates

The three other ways to set up a measurement endpoint all end with this
platform having reached the host: it signs in over SSH, or the host
fetches an invitation, or `iperf-server install` does it from the command
line. Sometimes none of them is available - the endpoint stands in a
provider's network, a customer runs it themselves, nothing may be
installed on it and no key of ours may stay there.

Then only the record is created here, under *Infrastructure → iperf →
Register a foreign one*, and the setup happens on the far side. This page
is what to ask the operator over there for, and what they have to do to
produce it.

Everything below is also what
[`setup-iperf3-endpoint.sh`](../../sensors/iperf-throughput/endpoint/setup-iperf3-endpoint.sh)
does. If that script may run on the host, send it there instead - it is
self-contained, it takes the same steps, and it verifies itself
afterwards. The commands here exist for the case where it may not.

## What the record needs

| Field | Where it comes from |
| --- | --- |
| Address and measurement port | the operator; the probes measure against it |
| Measurement user | chosen on the endpoint, `prtg-probe` by convention |
| Password | generated on the endpoint, readable only once |
| Public key | `/etc/iperf3/public.pem` on the endpoint |

The measurement user is all or nothing: a user name without its password
and the endpoint's public key produces a sensor that reports
`credentials-unreadable` on every run, which is why the dialog refuses
the combination. An endpoint that measures unauthenticated is registered
with the user name left empty.

## Preflight the iperf3 version

Run this on the endpoint before creating credentials or registering it:

```bash
iperf3 --version
```

The first line must report version 3.17 or newer, and the optional features
must include `authentication`. Version 3.17 changed authenticated sessions to
RSA-OAEP. Older releases use incompatible legacy padding and cannot
authenticate either the managed or compatible system client used by probes.

Stop if either check fails. Upgrade the endpoint from a source its operator
trusts, then repeat the command. Do not work around an older release with
`--use-pkcs1-padding`: that re-enables the legacy padding the version boundary
is meant to remove. Registration stores credentials; it does not install or
upgrade software on a foreign host.

## On the endpoint, as root

iperf3 3.17 or newer built against OpenSSL is required - the preflight names
the version and authentication feature. The `iperf3` group comes from the
package and the service runs under it; without the group the service cannot
read its own private key.

```bash
DIR=/etc/iperf3; IPERF_USER=prtg-probe; PORT=5201

apt-get install -y iperf3 openssl
iperf3 --version
install -d -o root -g iperf3 -m 0750 "$DIR"

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -outform PEM -out "$DIR/private.pem"
openssl rsa -in "$DIR/private.pem" -pubout -out "$DIR/public.pem"

PASSWORD="$(openssl rand -hex 24)"
printf '%s,%s\n' "$IPERF_USER" "$(printf '%s' "{$IPERF_USER}$PASSWORD" | sha256sum | cut -d' ' -f1)" > "$DIR/credentials.csv"

chown root:iperf3 "$DIR"/private.pem "$DIR"/public.pem "$DIR"/credentials.csv
chmod 0640 "$DIR/private.pem" "$DIR/credentials.csv"
chmod 0644 "$DIR/public.pem"
```

The package name does not guarantee the version. Check the output immediately:
if the first line is older than 3.17, stop before creating or copying any
credentials and install a current build. Distribution releases can carry an
older iperf3 under the same package name.

**The password is never on disk**, only the SHA-256 over
`{user}password` - iperf3 demands the credentials file in exactly that
shape. That is also why the password cannot be read back later: what is
stored there does not contain it. Whoever runs this stores it the moment
it appears, or the endpoint has to be given new credentials.

The private key is the other half. iperf3 hands the probes their public
key, they encrypt user name and password with it, and the server
decrypts what arrives - which is why a registered endpoint without a
public key is one the probes cannot authenticate against at all.

## Start the service with them

The packaged unit stays untouched; the authentication sits next to it as
a drop-in. Deleting the file and running `daemon-reload` takes the whole
change back.

```bash
IPERF3_BIN=$(command -v iperf3)
install -d -m 0755 /etc/systemd/system/iperf3.service.d
cat > /etc/systemd/system/iperf3.service.d/auth.conf <<UNIT
[Service]
ExecStart=
ExecStart=$IPERF3_BIN --server --interval 0 --port $PORT --rsa-private-key-path $DIR/private.pem --authorized-users-path $DIR/credentials.csv
UNIT

systemctl daemon-reload
systemctl enable --now iperf3
systemctl restart iperf3
```

The empty `ExecStart=` is not decoration. A drop-in adds a second start
command otherwise, and systemd refuses a service unit with two - the
service would not come up at all.

## Counter-check, on the endpoint itself

```bash
iperf3 --client 127.0.0.1 --port 5201 --time 1

IPERF3_PASSWORD='THE-PASSWORD' iperf3 --client 127.0.0.1 --port 5201 \
  --time 1 --username prtg-probe --rsa-public-key-path /etc/iperf3/public.pem
```

The first command has to fail and the second one has to succeed. An
endpoint that still answers without credentials looks exactly like a
working one from every side - until somebody else finds it.

Because the executable passed the 3.17 preflight, the second command also
checks the RSA-OAEP authentication path that the probes use. A successful
test with `--use-pkcs1-padding` would not be an acceptable substitute.

If the second command fails with credentials that were just created,
check the clock. iperf3 puts a timestamp into what it encrypts and
rejects anything too far off, so a host without NTP rejects its own
password.

## Then, here

Register the endpoint with the address, the port, the user name, the
password and the contents of `public.pem`, pasted whole:

```text
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA…
-----END PUBLIC KEY-----
```

That is the PEM `openssl rsa -pubout` writes and the only shape iperf3
reads. An OpenSSH key (`ssh-rsa AAAAB3…`), a PKCS#1 key
(`-----BEGIN RSA PUBLIC KEY-----`) or a certificate does not work, and
neither does the private key.

Afterwards the endpoint is a record like any other: it is assigned to
probes on its own page, and the sensor is deployed to them - only then is
anything measured. What stays different is what nobody here can do:
rotating the password and removing the endpoint both need the operator on
the far side, because this platform has no access to that host. Removing
it here takes the credentials off the probes and forgets the record; the
service over there keeps running.

## Related

- [Deploy sensors](deploy-sensors.md#measurement-endpoints) - the other
  three ways, and what a credential profile is
- [The sensor's own README](../../sensors/iperf-throughput/README.md) -
  parameters, channels and the profile format
- [Troubleshooting](troubleshooting.md) - what a sensor reports when the
  credentials are missing or wrong
