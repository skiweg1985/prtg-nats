# The management API.
#
# Runs as root and mounts runtime/ plus the Docker socket, which makes it a
# highly privileged component by design - the same privilege the shell tooling
# has when an operator runs it under sudo. docs/security/threat-model.md says
# so plainly rather than pretending otherwise.

FROM python:3.11-slim-bookworm AS base

# openssh-client: the probe helper channel falls back to it for host-key
# handling; ca-certificates: outbound TLS for nothing today, for OIDC tomorrow.
RUN apt-get update \
    && apt-get install --no-install-recommends -y openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=3).status == 200 else 1)"

# One worker: the job runner and the event broadcaster live in the process, and
# a second worker would run every job twice and see half the SSE subscribers.
# Concurrency comes from asyncio, which is what this workload actually needs.
# Loopback by default: the container joins the host network namespace, where
# anything else would expose the plain-HTTP API on a routable address. The
# exec keeps uvicorn as PID 1 so SIGTERM reaches it within the grace period.
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"${PRTG_NATS_WEB_HOST:-127.0.0.1}\" --port \"${PRTG_NATS_WEB_PORT:-8100}\" --workers 1"]
