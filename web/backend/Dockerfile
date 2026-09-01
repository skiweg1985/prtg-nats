# The management API.
#
# Runs as root and mounts the runtime volume plus the Docker socket, which
# makes it a highly privileged component by design - the same privilege the
# shell tooling had when an operator ran it under sudo.
# docs/security/threat-model.md says so plainly rather than pretending
# otherwise.
#
# Built from the repository root, not from this directory: the image carries
# the templates, sensors and on-target scripts the platform hands to a probe.
# Those used to arrive through a bind mount of the whole checkout, which is
# also how runtime state ended up pinned to a host path.

# One pinned Debian 11 amd64 builder produces all supported probe artifacts by
# cross-compiling. This keeps the glibc baseline at 2.31 and needs no QEMU.
ARG IPERF_BUILDER_PLATFORM=linux/amd64
FROM --platform=${IPERF_BUILDER_PLATFORM} debian:11.11-slim@sha256:70509c95d1857a3704c0a5d92ee2e0adac95f612a9386889d70760bfd7c1ebba AS iperf-builder

COPY tools/iperf3/bullseye-snapshot.list /etc/apt/sources.list
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --no-install-recommends -y \
        binutils \
        binutils-aarch64-linux-gnu \
        binutils-arm-linux-gnueabihf \
        ca-certificates \
        curl \
        findutils \
        gcc \
        gcc-aarch64-linux-gnu \
        gcc-arm-linux-gnueabihf \
        libc6-dev \
        libc6-dev-arm64-cross \
        libc6-dev-armhf-cross \
        make \
        perl \
        python3-minimal \
    && rm -rf /var/lib/apt/lists/*

# python3-minimal intentionally omits the JSON module used to produce and
# verify the manifest. Kept separate so the large, pinned cross-toolchain layer
# remains independently cacheable.
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install --no-install-recommends -y python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY tools/iperf3/build.sh \
    tools/iperf3/check-artifacts.sh \
    tools/iperf3/THIRD_PARTY_NOTICES.md \
    tools/iperf3/versions.env \
    /build/tools/iperf3/
RUN bash /build/tools/iperf3/build.sh \
    /build/output/tools/iperf3/artifacts

# A named export target lets CI verify the exact tree that the API image gets.
FROM scratch AS iperf-artifacts
COPY --from=iperf-builder /build/output/tools /tools

FROM python:3.11-slim-bookworm AS base

# openssh-client: the probe helper channel falls back to it for host-key
# handling; ca-certificates: outbound TLS for nothing today, for OIDC tomorrow;
# iproute2: this container shares the host network namespace, so "ip link show"
# is how it sees whether the overlay interface is up. Without it the check
# cannot run at all, and an absent tool reads the same as a downed interface.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        openssh-client ca-certificates iproute2 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY web/backend/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY web/backend/app ./app
COPY web/backend/alembic.ini ./
COPY web/backend/alembic ./alembic

# The assets: read-only at runtime, replaced wholesale by a new image. Kept
# apart from /srv/prtg-nats/runtime, which is the volume nobody can rebuild.
COPY config /opt/prtg-nats/config
COPY http /opt/prtg-nats/http
COPY sensors /opt/prtg-nats/sensors
COPY libexec /opt/prtg-nats/libexec
COPY bootstrap /opt/prtg-nats/bootstrap
COPY install-mpp.sh /opt/prtg-nats/
COPY --from=iperf-artifacts /tools/iperf3/artifacts \
    /opt/prtg-nats/tools/iperf3/artifacts

# Which commit produced this image. A container has no checkout to ask, so
# the answer has to be baked in - it is what lets the update page say "you
# are running this, the branch has that" instead of guessing from a version
# string nobody remembers to raise. Last, so a new commit does not invalidate
# the layers above it and every update rebuilds the world.
ARG GIT_COMMIT=""
ARG GIT_REF=""
ARG GIT_VERSION=""

ENV PRTG_NATS_WEB_ASSET_DIR=/opt/prtg-nats \
    PRTG_NATS_WEB_RUNTIME_DIR=/srv/prtg-nats/runtime \
    PRTG_NATS_WEB_GIT_COMMIT=${GIT_COMMIT} \
    PRTG_NATS_WEB_GIT_REF=${GIT_REF} \
    PRTG_NATS_WEB_GIT_VERSION=${GIT_VERSION}

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
