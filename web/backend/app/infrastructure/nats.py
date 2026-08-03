"""Read the NATS server through its monitoring endpoint.

The endpoint is published on 127.0.0.1:8222 only (see compose.yaml), which is
why this runs on the NATS host itself. The shell tooling parses the same JSON
with awk in libexec/common.sh; here it becomes typed data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class JetStreamState:
    enabled: bool
    streams: int = 0
    consumers: int = 0
    messages: int = 0
    bytes_used: int = 0
    memory_used: int = 0
    store_used: int = 0
    store_limit: int = 0

    @property
    def store_usage_ratio(self) -> float | None:
        if not self.store_limit:
            return None
        return self.store_used / self.store_limit


@dataclass(frozen=True, slots=True)
class NatsServerState:
    """What the dashboard needs to answer "is the backbone up?"."""

    available: bool
    healthy: bool = False
    server_name: str | None = None
    # When the running server last read its configuration. The only way to
    # tell a reload that was applied from one the server refused - it answers
    # the signal either way, and the refusal only reaches the container log.
    config_load_time: str | None = None
    version: str | None = None
    uptime: str | None = None
    connections: int = 0
    total_connections: int = 0
    slow_consumers: int = 0
    in_msgs: int = 0
    out_msgs: int = 0
    jetstream: JetStreamState | None = None
    connected_users: frozenset[str] = field(default_factory=frozenset)
    error_details: str | None = None


class NatsMonitoringClient:
    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def _get(self, client: httpx.AsyncClient, path: str) -> dict[str, Any] | None:
        try:
            response = await client.get(f"{self._base_url}{path}")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "NATS monitoring endpoint did not answer",
                extra={"path": path, "reason": str(exc)},
            )
            return None
        return payload if isinstance(payload, dict) else None

    async def fetch_state(self) -> NatsServerState:
        """One round trip per endpoint, all failures folded into `available`.

        A dashboard that throws when the backbone is down is a dashboard that
        is useless exactly when it is needed.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            varz = await self._get(client, "/varz")
            if varz is None:
                return NatsServerState(
                    available=False,
                    error_details=f"{self._base_url}/varz is not reachable",
                )

            healthz = await self._get(client, "/healthz?js-enabled-only=true")
            jsz = await self._get(client, "/jsz")
            connz = await self._get(client, "/connz?auth=1&state=open")

        return NatsServerState(
            available=True,
            healthy=bool(healthz and healthz.get("status") == "ok"),
            server_name=varz.get("server_name"),
            config_load_time=varz.get("config_load_time"),
            version=varz.get("version"),
            uptime=varz.get("uptime"),
            connections=int(varz.get("connections", 0)),
            total_connections=int(varz.get("total_connections", 0)),
            slow_consumers=int(varz.get("slow_consumers", 0)),
            in_msgs=int(varz.get("in_msgs", 0)),
            out_msgs=int(varz.get("out_msgs", 0)),
            jetstream=_parse_jetstream(jsz),
            connected_users=_parse_connected_users(connz),
        )

    async def connected_users(self) -> frozenset[str]:
        """Just the account names, for the probe list.

        Same source as nats_connected_users() in libexec/common.sh: the field
        is `authorized_user`, and an unreachable endpoint yields an empty set
        rather than an error - the caller marks the column unknown.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            connz = await self._get(client, "/connz?auth=1&state=open")
        return _parse_connected_users(connz)


def _parse_jetstream(jsz: dict[str, Any] | None) -> JetStreamState | None:
    if jsz is None:
        return None
    if not jsz.get("config") and not jsz.get("streams"):
        return JetStreamState(enabled=False)
    config = jsz.get("config") or {}
    return JetStreamState(
        enabled=True,
        streams=int(jsz.get("streams", 0)),
        consumers=int(jsz.get("consumers", 0)),
        messages=int(jsz.get("messages", 0)),
        bytes_used=int(jsz.get("bytes", 0)),
        memory_used=int(jsz.get("memory", 0)),
        store_used=int(jsz.get("store", 0)),
        store_limit=int(config.get("max_storage", 0)),
    )


def _parse_connected_users(connz: dict[str, Any] | None) -> frozenset[str]:
    if connz is None:
        return frozenset()
    users: set[str] = set()
    for connection in connz.get("connections", []) or []:
        user = connection.get("authorized_user")
        if isinstance(user, str) and user:
            users.add(user)
    return frozenset(users)
