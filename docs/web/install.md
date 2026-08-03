---
title: Install the web platform
role: operator
updated: 2026-08-02
---

# Install the web platform

The web platform is an addition to the existing stack, not a replacement for
it. The NATS server keeps running exactly as before; the platform reads the
same `runtime/` directory and speaks the same management protocol to the same
probes.

It is optional. Nothing in the shell tooling depends on it.

## Before you start

`.env` has to exist with the site settings - `sudo ./prtg-nats setup` asks for
them, or write it following `.env.example`.

That is all. If `runtime/` has never been initialised, the dashboard offers
the initialisation as a job: CA, server certificate, management key, shared
account and server configuration are created in under a minute, with a live
log, and the NATS container comes up the moment the files exist. The platform
reports `runtime_state` on its status page, so a partial installation is
visible rather than mysterious.

## Start it

```bash
docker compose up -d
```

That builds two images and starts three services:

| Service | What it does |
| --- | --- |
| `prtg-nats-web-api` | the management API and the job workers |
| `prtg-nats-web-frontend` | builds the interface into a volume and exits |
| `prtg-nats-web-proxy` | Caddy: terminates TLS and serves the interface |

The interface is then at `https://<NATS_FQDN>:8443`. The port is configurable
with `WEB_HTTPS_PORT` in `.env`.

## First sign-in

There is no default account and no default password. The first person to open
the interface is asked to create the administrator:

1. Open `https://<NATS_FQDN>:8443`.
2. Enter a user name and a password of at least twelve characters.
3. That account holds the `administrator` role and can create the others.

The window closes with that first account. A second call to the setup endpoint
is refused, so the form cannot be used later to add an administrator.

## What the container is trusted with

The API container mounts the installation directory read-write and the Docker
socket. That makes it a highly privileged component - the same privilege the
shell tooling has when an operator runs it under `sudo`.

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

Caddy issues a certificate from its own internal CA by default, which matches
how the rest of this stack handles TLS. A browser will warn on first visit.

For a name that resolves publicly, replace the `tls internal` line in
[web/Caddyfile](../../web/Caddyfile) with an ACME email address and Caddy takes
care of the rest.

## Upgrading

```bash
git pull
docker compose up -d --build
```

Database migrations run at start-up. The schema is owned by Alembic; a change
without a matching migration fails in CI rather than on your server.

## Turning it off

```bash
docker compose down web-api web-proxy web-frontend
```

Nothing is lost. The platform's own state lives in `runtime/web.db`; the
inventory it reads belongs to the shell tooling and is untouched.

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
