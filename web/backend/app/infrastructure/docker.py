"""Container lifecycle over the Docker socket.

Scope on purpose: inspecting the two stack containers and starting, stopping or
restarting them. Nothing here takes a container name from a caller - the names
are fixed by compose.yaml, and a management UI has no business running
arbitrary containers on the host it manages.

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
