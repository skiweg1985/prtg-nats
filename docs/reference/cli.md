---
title: Command reference
role: operator
updated: 2026-08-04
---

# Command reference

What remains on the command line, and why. Regular administration happens in
[the web interface](../web/install.md); the shell keeps three jobs:

1. **Bootstrap** - the things that must work before the platform runs:
   site settings, starting the stack.
2. **Interactive SSH rollout** - installing a probe needs a bootstrap
   password prompt and a host-key confirmation, which belong in a terminal.
3. **Recovery** - a thin wrapper over the same Python services the web
   platform runs, for when the platform itself is what broke.

```bash
./prtg-nats help          # the full list, from the tool itself
```

The values these commands read and write are in
[the configuration reference](configuration.md).

## Bootstrap

| Command | What it does |
| --- | --- |
| `setup` | ask for site settings, start the stack, initialise the runtime |
| `config` / `config --edit` | show or rewrite the site settings in `.env` |
| `status` | container state and certificate validity |
| `logs` | follow the NATS log |
| `start` / `stop` / `restart` | stack lifecycle |
| `update` | pull images and force-recreate the stack; the API migrates its database when it comes back |

`setup` starts the stack first and initialises afterwards, because the
initialisation runs in the `prtg-nats-web-api` container. Nothing has to be
installed on the host for it, and it cannot be deferred to the web interface:
the proxy that serves the interface needs the certificate the initialisation
issues, so until it has run there is no interface to defer to. NATS and the
proxy restart against the missing state in the meantime and are restarted once
it is there.

## Probe rollout

Unchanged, and staying: these need an interactive terminal.

| Command | What it does |
| --- | --- |
| `install-mpp [ADMIN@HOST] [--nats-user USER]` | install or reconfigure a probe over SSH |
| `probe enroll [USER] ADMIN@HOST [--reenroll]` | enroll a probe, or renew the enrollment |
| `probe list/show/status/configure/install-ca/adopt/unenroll` | manage enrolled probes |
| `probe helper-update USER\|--all` | renew the management helper on the probe |
| `sensor list/show/deploy/prepare/status/remove/reserve/release/profile` | manage sensors from the shell |
| `iperf-server install/list/show/deploy/revoke/forget` | measurement endpoints |
| `mpp-info [USER]` | the values of the generated configuration |
| `ssh-key info/show` | the management public key |
| `ca-info` / `ca-show` / `ca-path` | the public CA |

Sensor deployments and probe maintenance are also - and preferably - done in
the web interface, where they run as jobs with a live log and an audit trail.

### The bootstrap login

`probe enroll` and the first `install-mpp` on a host log in as the
administrative account named in `ADMIN@HOST`, once, to install the restricted
key everything afterwards runs over. That login needs the password of the
account or an SSH key for it, and it opens a single session both commands
share - so the password is asked for once, not once per file copied. Without
either, the enrollment stops before it starts:

```text
ERROR: Could not open a bootstrap session as admin@probe-01.example.com; it
needs the password of that account or an SSH key for it
```

### Naming the NATS account

The account is named where it is new, and looked up where it is not: an
enrolled host has it in its inventory, and the host is part of `ADMIN@HOST`
anyway.

```bash
sudo ./prtg-nats probe enroll mpp-probe-01 admin@probe-01.example.com
sudo ./prtg-nats probe enroll admin@probe-01.example.com --reenroll
```

The second call reports which account it resolved to before it does anything
with it. Two probes enrolled at one host are a rollout that went wrong; the
command says so and asks for the account rather than picking one. The same
applies to `install-mpp --nats-user`, with one exception: reconfiguring over
the restricted key passes no `ADMIN@HOST`, so there is no host to look the
account up by and the option stays required.

### Renewing the probe helper

A probe answers `Unsupported management request` to anything its helper
predates. `probe helper-update` sends the helper this checkout ships, signed
with `runtime/private/helper-signing-key.pem`; the probe verifies it against
the public key it was given during enrollment and refuses anything else - see
[ADR 0006](../architecture/decisions/0006-signed-helper-updates.md).

```bash
sudo ./prtg-nats probe helper-update --all
```

`probe show USER` reports what a probe carries. Two answers mean the same
command does not help yet:

| What the probe says | What it needs |
| --- | --- |
| a `helper_version` older than this checkout | this command, or "Update helper" in the interface |
| no `helper_version` at all | one bootstrap run: `probe enroll ADMIN@HOST --reenroll`, or a fresh invitation from the interface |

The second case is every probe enrolled before signed updates existed: it
carries no key to check a signature against, and the only path that installs
one is the bootstrap.

### Retiring a probe

`probe unenroll USER` removes the management access and the inventory. The
probe keeps running and stays connected to NATS; three options clear what is
otherwise left on the host:

| Option | What it adds |
| --- | --- |
| `--remove-access` | revoke the restricted key on the probe |
| `--remove-sensors` | remove every sensor the inventory or the probe knows of |
| `--uninstall-mpp` | remove `prtgmpprobe`, its configuration with the NATS CA, and the Paessler package source |

The first two need the management channel, so they run before it is revoked,
and a failure stops the unenrollment rather than stranding a host nobody can
reach any more. The NATS account is not touched: `user delete USER` does that,
and only works once no inventory names the probe - which is what the
unenrollment has just ended.

```bash
sudo ./prtg-nats probe unenroll mpp-berlin-01 --remove-sensors --uninstall-mpp --remove-access
```

## Recovery

These delegate to `python -m app.ops`, which drives the same services the web
platform uses. They exist for the situations the interface cannot cover -
setting a machine up, scripting, and recovery when the platform itself is what
broke; they are not a second implementation.

They run in the `prtg-nats-web-api` container whenever it is up, which is where
the backend and its dependencies live. A checkout that has `web/backend`
installed into a local virtual environment is the fallback, and what the
end-to-end test uses.

| Command | What it does |
| --- | --- |
| `init` | initialise `runtime/`: CA, server certificate, management key, shared account |
| `user add/list/show/rotate/delete` | NATS accounts |
| `verify [--offline]` | the stack checks, ending in an authenticated NATS login |
| `renew-certificate` | renew the server certificate and restart NATS |
| `backup` | consistent JetStream backup with checksum |

The delegation needs the backend installed once:

```bash
python3 -m venv web/backend/.venv
web/backend/.venv/bin/pip install -e web/backend
```

Without it, every recovery command explains exactly that instead of failing
cryptically.

## Shell integration

| Command | What it does |
| --- | --- |
| `self install [SHELL]` | link `prtg-nats` into `/usr/local/bin` and set up completion |
| `self uninstall` | remove both again |
| `completion [bash\|zsh]` | print the completion script to standard output |

`setup` offers the installation at the end on its own. Restrict it to one half
with `--link-only` or `--completion-only`; if one half fails, the other is
still set up and the failure is reported at the end.

Completion covers commands, subcommands and options - and the created NATS
accounts and enrolled probes, read from `runtime/credentials/` and
`runtime/probes/`. Both directories belong to root with mode `0700`, so without
root privileges the commands still complete but the names stay empty.

What gets linked and sourced is always the repository, never a copy, so a
`git pull` takes effect without re-running the setup. Under `sudo` the command
deliberately targets the startup file of the **calling** user, not root's. The
full behaviour, including where the completion is written on which
distribution, is in
[Operations](../guides/operations.md#set-up-the-command-line).

## Retired

`tui` and `rotate-password` moved into the web interface entirely and now say
so when called. `test-persistence` was retired without replacement: the
container health check proves JetStream is serving, and `verify` covers the
authenticated login. The `check` alias is gone.

**Migration note for existing installations:** `runtime/` moved out of the
checkout and into the `prtg-nats-runtime` volume. Every command reads the
volume now; a `runtime/` directory left beside the repository is no longer the
installation, and `status` and `setup` say so when they find one. It still
holds the old CA key, so put it somewhere safe or remove it - do not leave it
lying next to the checkout.

To work on a runtime somewhere else - the end-to-end test does, from inside a
container where the host's mountpoint does not exist - set
`PRTG_NATS_RUNTIME_DIR` to the path, or `PRTG_NATS_RUNTIME_VOLUME` to a
different volume name.

## What not to run

```bash
docker compose down --volumes
```

That deletes the JetStream data.
