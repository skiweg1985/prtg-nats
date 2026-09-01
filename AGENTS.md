# AGENTS.md

Instructions for coding agents working in this repository. Documentation for
humans starts at [README.md](README.md) and [docs/README.md](docs/README.md).

## What this is

A production stack for the external NATS server used by PRTG multi-platform
probes (MPP), plus an optional web platform to administer it. The probes stay
native packages on their own Linux systems - this repository replaces only the
NATS server and the tooling around it.

## Layout

| Path | What lives there |
| --- | --- |
| `prtg-nats` | the only shell entry point; every command is dispatched here |
| `libexec/` | internal shell implementation, not called directly |
| `install-mpp.sh` | runs on the probe, so it has to stand alone |
| `sensors/` | monitoring scripts rolled out to probes, one directory each |
| `config/` | templates rendered into the runtime volume during setup |
| `http/` | the CGI endpoint that exposes NATS health as PRTG channels |
| `completions/` | the shell completion installed by `prtg-nats self install` |
| `web/backend/` | FastAPI management API - Python 3.11, SQLAlchemy, Alembic |
| `web/frontend/` | the interface - React 19, Vite, Tailwind, i18next |
| `tests/` | static checks, the end-to-end rollout, sensor checks |
| `docs/` | the documentation tree, organised by task |

## Ground rules

- The runtime is the source of truth for credentials, certificates, the probe
  inventory and the measurement endpoints. SQLite holds only what the
  filesystem has no place for - see
  [ADR 0002](docs/architecture/decisions/0002-runtime-stays-the-source-of-truth.md).
- It lives in the `prtg-nats-runtime` volume, not beside the checkout. Shell
  code reaches it through `RUNTIME_DIR` from `libexec/runtime-dir.sh`, never
  through a path built from `PROJECT_DIR`; the backend uses
  `PRTG_NATS_WEB_RUNTIME_DIR`.
- English is the source language for code, comments and documentation.
- Never commit `.env`, anything under `runtime/`, or real host names,
  addresses and credentials. `.env.example` is the template, and the checks
  use example values.
- `dev` is the default branch and takes the daily work, `main` holds the
  released state. Both are checked equally strictly.
- Files under `libexec/` are internal. A new user-facing command belongs in
  `prtg-nats` and is dispatched from there.
- The web platform and the shell tooling act on the same state and are used
  side by side. A change to one usually needs the other to keep up.

## Checks

Run what your change touched. Everything below also runs in CI, in pull
requests and on merges to `dev` or `main`, see
[.gitea/workflows/ci.yaml](.gitea/workflows/ci.yaml).

Shell syntax, templates and rendering - seconds, no Docker, no network:

```bash
./tests/check-static.sh
```

Every job log message the backend can emit, against the two locale files -
the direction `npm run i18n:check` does not cover:

```bash
./tests/check-job-messages.py
```

The full rollout against a real `prtgmpprobe` - minutes, needs Docker,
privileged containers and network access:

```bash
./tests/e2e-mpp.sh
```

That the container driving a stack update survives the stack being recreated
around it - seconds, needs Docker and the updater image:

```bash
docker build -f web/updater/Dockerfile -t prtg-nats-updater:current .
./tests/e2e-update.sh
```

The management API:

```bash
cd web/backend && ruff check . && ruff format --check . && mypy app && pytest -q
```

The interface:

```bash
cd web/frontend && npm run lint && npm run typecheck && npm run test && npm run i18n:check
```

Markdown:

```bash
npx --yes markdownlint-cli2 "**/*.md"
```

## Conventions

**Shell.** `set -Eeuo pipefail`, shellcheck-clean, shared helpers in
`libexec/common.sh`. The TUI needs `whiptail`; without the package
`check-static.sh` skips the dialog layer silently.

**Python.** ruff with line length 88, mypy in strict mode, pytest with
`asyncio_mode = auto`. A change to a model needs an Alembic migration in the
same commit - `alembic check` fails otherwise, and a missing migration is a
deployment that breaks on the customer's machine rather than here.

**TypeScript.** `eslint --max-warnings 0`, so a warning is a failure. Every
new user-facing string needs a key in both `src/i18n/locales/de.json` and
`en.json`, including messages for backend error codes; `npm run i18n:check`
catches a key that exists in one language only. Wording follows
[docs/web/terminology.md](docs/web/terminology.md) - one term per concept,
Du-Anrede, and the check enforces the retired terms.

**Markdown.** Documentation pages carry YAML front matter with `title`, `role`
and `updated`. Prose is wrapped by hand at 79 columns; tables and links are
not wrapped. A change to behaviour updates the page that documents it in the
same commit - a new setting belongs in
[docs/reference/configuration.md](docs/reference/configuration.md), a new
command in [docs/reference/cli.md](docs/reference/cli.md), a new endpoint in
[docs/reference/api.md](docs/reference/api.md).

**Comments.** Explain why something is the way it is, not what the line does.
The existing comments in `ci.yaml` and `prtg-nats` set the tone.
