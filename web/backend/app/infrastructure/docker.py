"""Container lifecycle over the Docker socket.

Scope on purpose: inspecting the two stack containers and starting, stopping or
restarting them. Nothing here takes a container name from a caller - the names
are fixed by compose.yaml, and a management UI has no business running
arbitrary containers on the host it manages.

The updater below is the one thing that creates a container rather than
addressing an existing one, and it is written to keep that rule rather than to
be an exception to it: the image is a constant, the command comes from a closed
enum, and the mounts are derived from the labels Compose already wrote. No part
of a request reaches any of it.

If the socket is not mounted, every call reports it and the interface hides the
server lifecycle actions. Everything else in the platform keeps working.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from app.core.errors import DockerUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

DOCKER_API_VERSION = "v1.44"

# Pinned like every other image in this stack. Only used as a mount point for
# the volume-archive trick; it never runs.
BACKUP_HELPER_IMAGE = "alpine:3.21"

# The JetStream volume, named in compose.yaml.
JETSTREAM_VOLUME = "prtg-nats-data"

# The installation volume, and where the updater sees it. Read-only and for
# one thing only: the deploy key it needs to reach the repository.
RUNTIME_VOLUME = "prtg-nats-runtime"
UPDATER_RUNTIME_TARGET = "/srv/prtg-nats/runtime"

# The image the updater runs from, built by compose.yaml like the other two.
# Not pulled from a registry: it carries the git and compose versions this
# installation was tested with, and an update is the worst moment to discover
# that a floating tag moved underneath it.
UPDATER_IMAGE = "prtg-nats-updater:current"

# Containers to ask for the Compose labels, in order. The first one that
# answers decides; every service of a project carries the same project labels,
# so this is about which one exists rather than which one is right.
_LABEL_SOURCES = ("prtg-nats-web-api", "prtg-nats", "prtg-nats-web-proxy")

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
COMPOSE_CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"

# The project name compose.yaml declares. A container carrying a different one
# belongs to somebody else's stack and is not evidence about this checkout.
COMPOSE_PROJECT_NAME = "prtg-nats"


class UpdaterCommand(StrEnum):
    """What the updater may be asked to do. A closed set, like the containers.

    ``probe`` only reads - it is safe to run on a timer. ``apply`` is the one
    that changes the installation.
    """

    PROBE = "probe"
    APPLY = "apply"


@dataclass(frozen=True, slots=True)
class ComposeProject:
    """Where the installation lives on the host, as Compose recorded it.

    The API container cannot see the checkout it was built from - it mounts the
    runtime volume and the socket, nothing else. Compose writes the host path
    onto every container it creates, which makes the daemon the one component
    that can answer where the checkout is.
    """

    name: str
    working_dir: Path
    config_file: Path | None


@dataclass(frozen=True, slots=True)
class ContainerRun:
    """The outcome of a container that ran to completion."""

    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class StackContainer(StrEnum):
    """The containers compose.yaml defines. A closed set, by design."""

    NATS = "prtg-nats"
    # Serves the interface, and the public CA over plain HTTP - the separate
    # download container that used to do the latter is gone.
    WEB_PROXY = "prtg-nats-web-proxy"


@dataclass(frozen=True, slots=True)
class ContainerState:
    name: str
    exists: bool
    running: bool = False
    status: str | None = None
    health: str | None = None
    image: str | None = None
    started_at: str | None = None
    restart_count: int = 0


class DockerAdapter:
    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path

    @property
    def available(self) -> bool:
        return self._socket_path.exists()

    def _client(self) -> httpx.AsyncClient:
        if not self.available:
            raise DockerUnavailableError(
                params={"socket": str(self._socket_path)},
                details=(
                    "the Docker socket is not mounted; server lifecycle actions "
                    "are disabled"
                ),
            )
        transport = httpx.AsyncHTTPTransport(uds=str(self._socket_path))
        return httpx.AsyncClient(
            transport=transport,
            base_url=f"http://localhost/{DOCKER_API_VERSION}",
            timeout=30.0,
        )

    async def inspect(self, container: StackContainer) -> ContainerState:
        if not self.available:
            return ContainerState(name=container.value, exists=False)
        try:
            async with self._client() as client:
                response = await client.get(f"/containers/{container.value}/json")
                if response.status_code == 404:
                    return ContainerState(name=container.value, exists=False)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning(
                "container inspect failed",
                extra={"container": container.value, "reason": str(exc)},
            )
            return ContainerState(name=container.value, exists=False)

        state = payload.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        return ContainerState(
            name=container.value,
            exists=True,
            running=bool(state.get("Running")),
            status=state.get("Status"),
            health=health,
            image=(payload.get("Config") or {}).get("Image"),
            started_at=state.get("StartedAt"),
            restart_count=int(payload.get("RestartCount", 0)),
        )

    async def inspect_all(self) -> dict[str, ContainerState]:
        return {
            container.value: await self.inspect(container)
            for container in StackContainer
        }

    async def restart(self, container: StackContainer, *, timeout: int = 30) -> None:
        async with self._client() as client:
            response = await client.post(
                f"/containers/{container.value}/restart", params={"t": timeout}
            )
            response.raise_for_status()

    async def wait_healthy(
        self, container: StackContainer, *, attempts: int = 45, delay: float = 2.0
    ) -> ContainerState:
        """Poll until the container reports healthy, or return its last state.

        The caller decides whether an unhealthy result is fatal; a status page
        wants the state, a job wants to fail on it.
        """
        import asyncio

        state = await self.inspect(container)
        for _ in range(attempts):
            state = await self.inspect(container)
            if state.health == "healthy":
                return state
            # A container that does not exist will not become healthy by
            # waiting, and neither will one that has stopped.
            if not state.exists:
                break
            if not state.running and state.health != "starting":
                break
            await asyncio.sleep(delay)
        return state

    async def compose_project(self) -> ComposeProject | None:
        """Where this installation's checkout lives on the host.

        Read from the labels Compose writes onto every container it creates.
        A stack somebody started without Compose has none of them, and the
        honest answer is then that this cannot be determined - the interface
        turns that into a disabled feature with a reason rather than a guess.
        """
        if not self.available:
            return None
        for name in _LABEL_SOURCES:
            labels = await self._labels(name)
            if labels.get(COMPOSE_PROJECT_LABEL) != COMPOSE_PROJECT_NAME:
                continue
            working_dir = labels.get(COMPOSE_WORKING_DIR_LABEL)
            if not working_dir:
                continue
            # Compose writes every configuration file it read, separated by
            # commas. The first is the one this stack is described by.
            config_files = labels.get(COMPOSE_CONFIG_FILES_LABEL, "")
            first = config_files.split(",")[0].strip()
            return ComposeProject(
                name=COMPOSE_PROJECT_NAME,
                working_dir=Path(working_dir),
                config_file=Path(first) if first else None,
            )
        return None

    async def _labels(self, container_name: str) -> dict[str, str]:
        """The labels of one container, or nothing if it is not there."""
        try:
            async with self._client() as client:
                response = await client.get(f"/containers/{container_name}/json")
                if response.status_code == 404:
                    return {}
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning(
                "reading container labels failed",
                extra={"container": container_name, "reason": str(exc)},
            )
            return {}
        labels = (payload.get("Config") or {}).get("Labels") or {}
        return {str(key): str(value) for key, value in labels.items()}

    async def image_exists(self, image: str) -> bool:
        """Whether an image is present locally. Never pulls."""
        if not self.available:
            return False
        try:
            async with self._client() as client:
                response = await client.get(f"/images/{image}/json")
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def create_updater(
        self,
        command: UpdaterCommand,
        arguments: tuple[str, ...],
        *,
        project: ComposeProject,
        name: str,
    ) -> str:
        """Create the container that acts on the checkout, and return its id.

        Three things here are deliberate and load-bearing.

        **The checkout is mounted at its own host path.** Compose resolves a
        relative bind - ``./web/Caddyfile`` - against the project directory and
        hands the daemon an absolute path it reads as a host path. Mount the
        checkout anywhere else and the recreated proxy would bind a directory
        that does not exist on the host, and come up without its configuration.

        **No Compose labels.** ``docker compose up --remove-orphans`` collects
        candidates by the project label; a container without it is never in
        that list. That is what lets this container survive replacing the very
        process that started it.

        **Logging is pinned to json-file.** The caller reads the log back
        through the API after a restart, and a daemon whose default driver is
        syslog or none would leave it with nothing to read.
        """
        async with self._client() as client:
            response = await client.post(
                "/containers/create",
                params={"name": name},
                json={
                    "Image": UPDATER_IMAGE,
                    "Cmd": [command.value, *arguments],
                    "WorkingDir": str(project.working_dir),
                    "Env": [f"PRTG_NATS_CHECKOUT={project.working_dir}"],
                    "HostConfig": {
                        "Binds": [
                            f"{project.working_dir}:{project.working_dir}",
                            "/var/run/docker.sock:/var/run/docker.sock",
                            f"{RUNTIME_VOLUME}:{UPDATER_RUNTIME_TARGET}:ro",
                        ],
                        # Never AutoRemove: the run outlives the process that
                        # started it, and whoever picks the job back up needs
                        # the exit code and the log the container still holds.
                        "AutoRemove": False,
                        "LogConfig": {
                            "Type": "json-file",
                            "Config": {"max-size": "10m", "max-file": "3"},
                        },
                    },
                },
            )
            response.raise_for_status()
            container_id: str = response.json()["Id"]
            return container_id

    async def start_container(self, container_id: str) -> None:
        async with self._client() as client:
            response = await client.post(f"/containers/{container_id}/start")
            if response.status_code != 304:
                response.raise_for_status()

    async def wait_container(
        self, container_id: str, *, timeout: float | None = None
    ) -> int:
        """Block until the container exits and return its exit code."""
        async with self._client() as client:
            response = await client.post(
                f"/containers/{container_id}/wait",
                timeout=timeout,
            )
            response.raise_for_status()
            return int(response.json().get("StatusCode", 1))

    async def container_exit_code(self, container_id: str) -> int | None:
        """The exit code of a finished container, or None while it still runs."""
        async with self._client() as client:
            response = await client.get(f"/containers/{container_id}/json")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            state = response.json().get("State") or {}
            if state.get("Running"):
                return None
            return int(state.get("ExitCode", 1))

    async def container_logs(self, container_id: str) -> str:
        """The whole log of any container by id.

        Whole, rather than a slice: the API can only cut by timestamp or by a
        count from the end, and the caller here needs "everything after the
        line I already showed". Counting lines off the front is exact, and an
        updater log is kilobytes.
        """
        async with self._client() as client:
            params: dict[str, str] = {"stdout": "1", "stderr": "1"}
            response = await client.get(
                f"/containers/{container_id}/logs", params=params
            )
            if response.status_code == 404:
                return ""
            response.raise_for_status()
            return _strip_stream_header(response.content)

    async def remove_container(self, container_id: str) -> None:
        async with self._client() as client:
            response = await client.delete(
                f"/containers/{container_id}", params={"force": "true"}
            )
            if response.status_code not in (204, 404):
                response.raise_for_status()

    async def run_updater(
        self,
        command: UpdaterCommand,
        arguments: tuple[str, ...] = (),
        *,
        project: ComposeProject,
        name: str,
        timeout: float = 120.0,
    ) -> ContainerRun:
        """Run the updater to completion and return its output.

        For the short, read-only calls - asking the checkout what it is at,
        asking the remote what it has. The update itself does not use this: it
        has to outlive this process and is driven step by step instead.
        """
        container_id = await self.create_updater(
            command, arguments, project=project, name=name
        )
        try:
            await self.start_container(container_id)
            exit_code = await self.wait_container(container_id, timeout=timeout)
            output = await self.container_logs(container_id)
            return ContainerRun(exit_code=exit_code, output=output)
        finally:
            await self.remove_container(container_id)

    async def read_volume_archive(self, volume: str, target) -> int:  # type: ignore[no-untyped-def]
        """Stream the contents of a named volume as a tar into ``target``.

        Used by the JetStream backup. A helper container is created with the
        volume mounted read-only - it never even starts - and the Docker API's
        archive endpoint streams the files out. No host path is needed, which
        matters because this process runs in a container and does not know
        where the installation lives on the host.

        Returns the number of bytes written.
        """
        async with self._client() as client:
            created = await client.post(
                "/containers/create",
                json={
                    "Image": BACKUP_HELPER_IMAGE,
                    "Cmd": ["true"],
                    "HostConfig": {"Binds": [f"{volume}:/source:ro"]},
                },
            )
            if created.status_code == 404:
                # The helper image is not present yet; pull it once.
                pull = await client.post(
                    "/images/create", params={"fromImage": BACKUP_HELPER_IMAGE}
                )
                pull.raise_for_status()
                created = await client.post(
                    "/containers/create",
                    json={
                        "Image": BACKUP_HELPER_IMAGE,
                        "Cmd": ["true"],
                        "HostConfig": {"Binds": [f"{volume}:/source:ro"]},
                    },
                )
            created.raise_for_status()
            container_id = created.json()["Id"]

            written = 0
            try:
                async with client.stream(
                    "GET",
                    f"/containers/{container_id}/archive",
                    params={"path": "/source/."},
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        target.write(chunk)
                        written += len(chunk)
            finally:
                await client.delete(
                    f"/containers/{container_id}", params={"force": "true"}
                )
            return written

    async def start(self, container: StackContainer) -> None:
        async with self._client() as client:
            response = await client.post(f"/containers/{container.value}/start")
            # 304 means "already running", which is success for our purposes.
            if response.status_code != 304:
                response.raise_for_status()

    async def stop(self, container: StackContainer, *, timeout: int = 30) -> None:
        async with self._client() as client:
            response = await client.post(
                f"/containers/{container.value}/stop", params={"t": timeout}
            )
            if response.status_code != 304:
                response.raise_for_status()

    async def reload_config(self, container: StackContainer) -> None:
        """SIGHUP: NATS re-reads its configuration without dropping clients."""
        async with self._client() as client:
            response = await client.post(
                f"/containers/{container.value}/kill", params={"signal": "SIGHUP"}
            )
            response.raise_for_status()

    async def logs(self, container: StackContainer, *, tail: int = 200) -> str:
        async with self._client() as client:
            response = await client.get(
                f"/containers/{container.value}/logs",
                params={"stdout": "1", "stderr": "1", "tail": str(tail)},
            )
            response.raise_for_status()
            return _strip_stream_header(response.content)


def _strip_stream_header(payload: bytes) -> str:
    """Docker multiplexes stdout and stderr behind an 8-byte frame header."""
    lines: list[str] = []
    offset = 0
    while offset + 8 <= len(payload):
        length = int.from_bytes(payload[offset + 4 : offset + 8], "big")
        chunk = payload[offset + 8 : offset + 8 + length]
        lines.append(chunk.decode("utf-8", errors="replace"))
        offset += 8 + length
    if not lines:
        return payload.decode("utf-8", errors="replace")
    return "".join(lines)
