---
title: Command reference
role: operator
updated: 2026-08-03
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
| `setup` | ask for site settings, prepare `runtime/`, initialise if possible, start the stack |
| `config` / `config --edit` | show or rewrite the site settings in `.env` |
| `status` | container state and certificate validity |
| `logs` | follow the NATS log |
| `start` / `stop` / `restart` | stack lifecycle |
| `update` | pull images and force-recreate the stack |

`setup` initialises the runtime when the Python backend is installed on the
machine; otherwise it starts the stack and the first visit to the web
interface finishes the job. Both paths run the same code.

## Probe rollout

Unchanged, and staying: these need an interactive terminal.

| Command | What it does |
| --- | --- |
| `install-mpp [ADMIN@HOST] --nats-user USER` | install or reconfigure a probe over SSH |
| `probe enroll/list/show/status/configure/install-ca/adopt/unenroll` | manage enrolled probes |
| `sensor list/show/deploy/prepare/status/remove/reserve/release/profile` | manage sensors from the shell |
| `iperf-server install/list/show/deploy/revoke/forget` | measurement endpoints |
| `mpp-info [USER]` | the values of the generated configuration |
| `ssh-key info/show` | the management public key |
| `ca-info` / `ca-show` / `ca-path` | the public CA |

Sensor deployments and probe maintenance are also - and preferably - done in
the web interface, where they run as jobs with a live log and an audit trail.

## Recovery

These delegate to `python -m app.ops`, which drives the same services the web
platform uses. They exist for machines without a running platform and for
scripting; they are not a second implementation.

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

**Migration note for existing installations:** the server configuration moved
from `runtime/nats-server.conf` to `runtime/conf/nats-server.conf`, because
Compose now mounts directories instead of single files. `setup` and `start`
move the file automatically; after that, recreate the NATS container once:

```bash
sudo ./prtg-nats restart
```

## What not to run

```bash
docker compose down --volumes
```

That deletes the JetStream data.
