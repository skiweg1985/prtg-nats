---
title: Install the web platform
role: operator
updated: 2026-08-27
---

# Install the web platform

The web platform is not a separate installation and not an optional add-on.
It is part of the one stack in [compose.yaml](../../compose.yaml), it reads the
same runtime volume as the shell tooling, and it speaks the same management
protocol to the same probes.

The shell tooling depends on it in one direction: the recovery commands - and
therefore `setup` - run `python -m app.ops` inside the `prtg-nats-web-api`
container, because that is where the backend and its dependencies live.

## Before you start

`.env` has to exist with the site settings, and the runtime has to be
initialised. Both are what `sudo ./prtg-nats setup` does, in that order, and
there is no way around doing it first: the reverse proxy that serves this
interface refuses to start without the certificate the initialisation issues,
so an uninitialised installation has no interface to be configured from.

Once it is up, the platform reports `runtime_state` on its status page, so a
partial installation is visible rather than mysterious.

## Start it

The whole stack, including NATS:

```bash
docker compose up -d
```

That builds two images and starts five services:

| Service | What it does |
| --- | --- |
| `prtg-nats-runtime-init` | creates the directories the others mount, then exits |
| `prtg-nats` | the NATS server itself |
| `prtg-nats-web-api` | the management API and the job workers |
| `prtg-nats-web-frontend` | builds the interface into a volume and exits |
| `prtg-nats-web-proxy` | Caddy: terminates TLS, serves the interface and the public CA |

The interface is then at `https://<NATS_FQDN>`. The port is configurable with
`WEB_HTTPS_PORT` in `.env`; whoever calls the host over plain HTTP is sent
there, so the port never has to be guessed.

## First sign-in

There is no default account and no default password. The first person to open
the interface is asked to create the administrator:

1. Open `https://<NATS_FQDN>`.
2. Enter a user name and a password of at least twelve characters.
3. That account holds the `administrator` role and can create the others.

The window closes with that first account. A second call to the setup endpoint
is refused, so the form cannot be used later to add an administrator.

## What the container is trusted with

The API container mounts the runtime volume read-write and the Docker socket.
That makes it a highly privileged component - the same privilege the shell
tooling has when an operator runs it under `sudo`.

This is stated plainly rather than worked around, and
[the threat model](../security/threat-model.md) explains what follows from it.
Two things are worth knowing here:

- **The Docker socket is optional.** Leave the mount out and the platform still
  manages every probe, every sensor and every certificate. Only the server
  lifecycle actions - restart, backup - disappear from the interface, and the
  interface says why rather than failing when they are pressed.
- **The API never listens on a routable address.** It joins the host network
  namespace and binds to loopback; Caddy is the only thing that opens a port.

## Certificates

Caddy serves a certificate issued by the installation's own CA - the same one
that signs the NATS server certificate - not one from Caddy's internal CA. That
is deliberate: an operator who compared the CA fingerprint once trusts the
interface, the NATS server and the enrolment channel with that single decision.
A browser will warn on first visit until the CA is trusted.

It follows that the proxy cannot start before the initialisation has issued
that certificate. `Error: … open /etc/caddy/certs/web.pem: no such file or
directory` in a restart loop means exactly that, and `sudo ./prtg-nats setup`
is the answer - not a certificate problem.

For a name that resolves publicly, replace the `tls` line in
[web/Caddyfile](../../web/Caddyfile) with an ACME email address and Caddy takes
care of the rest.

## Upgrading

From the interface, under *Updates*: it shows which commit is installed and
what the branch has, and the button runs the same sequence as a job with a log
and an audit trail. [Operations](../guides/operations.md#updating-the-repository)
covers what it refuses and why.

From the host, and the answer when the interface is what is broken:

```bash
git pull
sudo ./prtg-nats update
```

`prtg-nats update` rather than `docker compose up -d --build`, and not out of
habit: it stamps the commit of this checkout into the images it builds, and
compose on its own does not. An installation built the second way runs
perfectly well and cannot say which version it is - the *Updates* page reports
it as unknown, because inventing an answer would be worse.

The interface can only update an installation whose updater image exists, and
an installation being updated *to* this version does not have one yet. That
one update is the command line; every one after it is a button. Which case an
installation is in is what the *Updates* page says when the action is missing.

Database migrations run at start-up, before the job workers do. The schema is
owned by Alembic: a change without a matching migration fails in CI rather
than on your server, and a migration that never ran is not something the
service starts up around. A fresh installation is built by the migrations too,
so there is only ever one thing that has shaped the schema.

If the upgrade cannot be applied, `web-api` stops with the reason in its log
rather than serving requests against a schema it does not fit:

```bash
docker compose logs web-api
```

### A database from before migrations ran

Installations set up before this shipped have a schema nobody recorded a
version for: it was created from the models directly. The service takes such a
database over on the next start, as long as its schema still matches the
models - it is at the current revision, it just never said so.

If it does not match, it is behind by an unknown number of releases, and only
you know which version it last ran. The service refuses to start and names
what is missing. Stamp it with the revision that version shipped, then upgrade:

```bash
docker compose exec web-api alembic history
docker compose exec web-api alembic stamp REVISION
docker compose exec web-api alembic upgrade head
```

`alembic history` lists the revisions newest first, each with its description
and the date it was written; the version you were running is the newest one
older than the day you installed it. Stamping too new a revision skips the
migrations in between, which is the failure this whole mechanism exists to
prevent - when in doubt, stamp the older one and let the upgrade do the work.

## Turning the interface off

```bash
docker compose stop web-api web-proxy
```

NATS keeps serving; only the interface and the API go away. Nothing is lost -
the platform's own state lives in `web.db` in the runtime volume, and the
inventory it reads belongs to the installation, not to the interface.

Note that the recovery commands run in `web-api`. With it stopped, `init`,
`user`, `verify`, `renew-certificate` and `backup` need a local backend in
`web/backend/.venv` instead.

Never use `docker compose down --volumes`. That deletes the installation and
the JetStream data.

## Development

```bash
cd web/backend
uv venv && uv pip install -e ".[dev]"
PRTG_NATS_WEB_PROJECT_DIR=/path/to/installation \
PRTG_NATS_WEB_ENVIRONMENT=development \
PRTG_NATS_WEB_SESSION_COOKIE_SECURE=false \
  .venv/bin/uvicorn app.main:app --reload --port 8100
```

```bash
cd web/frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to port 8100, which keeps the session cookie
first-party and makes development behave like production.

`PRTG_NATS_WEB_DEV_AUTH_ENABLED=true` skips sign-in and acts as an
administrator. It refuses to combine with `PRTG_NATS_WEB_ENVIRONMENT=production`
and the interface carries a permanent banner while it is on.
