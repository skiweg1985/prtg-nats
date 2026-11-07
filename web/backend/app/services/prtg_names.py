"""What a probe is called in PRTG today, next to the name it was enrolled with.

The enrollment name is what a probe announced once; PRTG lets anyone rename it
afterwards and never says so. With a token and a link to the probe's PRTG
object id, this service reads the current name. Every way of not knowing it -
no token, no link, PRTG unreachable or refusing - is a state with a reason,
never an error page: the probe is fine, only the extra fact is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.errors import (
    PrtgObjectIdTakenError,
    PrtgObjectNotFoundError,
    PrtgRejectedError,
    PrtgUnreachableError,
)
from app.infrastructure.prtg_core import (
    PrtgCoreClient,
    PrtgCoreStore,
    PrtgProbe,
    validate_object_id,
)
from app.infrastructure.runtime_files import ProbeInventory, RuntimeFileStore

NameState = Literal[
    "ok", "not_configured", "not_linked", "unreachable", "rejected", "not_found"
]


@dataclass(frozen=True, slots=True)
class PrtgName:
    state: NameState
    object_id: str | None
    enrollment_name: str | None
    name_in_prtg: str | None = None

    @property
    def differs(self) -> bool:
        return (
            self.name_in_prtg is not None
            and self.enrollment_name is not None
            and self.name_in_prtg != self.enrollment_name
        )


@dataclass(frozen=True, slots=True)
class LinkCandidates:
    suggested_object_id: str | None
    candidates: tuple[PrtgProbe, ...]


class PrtgNameService:
    def __init__(
        self,
        store: PrtgCoreStore,
        runtime: RuntimeFileStore,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._transport = transport

    def _client(self) -> PrtgCoreClient | None:
        settings = self._store.read()
        if not settings.usable:
            return None
        return PrtgCoreClient(settings, transport=self._transport)

    def _require_client(self) -> PrtgCoreClient:
        """Raises prtg.not_configured where _client() would return None."""
        return PrtgCoreClient(self._store.read(), transport=self._transport)

    async def name_for(self, inventory: ProbeInventory) -> PrtgName:
        enrollment = inventory.probe_name
        client = self._client()
        if client is None:
            return PrtgName("not_configured", inventory.prtg_object_id, enrollment)
        if inventory.prtg_object_id is None:
            return PrtgName("not_linked", None, enrollment)
        object_id = inventory.prtg_object_id
        try:
            probe = await client.get_probe(object_id)
        except PrtgRejectedError:
            return PrtgName("rejected", object_id, enrollment)
        except PrtgUnreachableError:
            return PrtgName("unreachable", object_id, enrollment)
        if probe is None:
            return PrtgName("not_found", object_id, enrollment)
        return PrtgName("ok", object_id, enrollment, probe.name)

    async def names_for_all(
        self, inventories: list[ProbeInventory]
    ) -> tuple[NameState, list[tuple[str, PrtgName]]]:
        """One request for the whole list, however many probes are linked."""
        client = self._client()
        if client is None:
            return "not_configured", []
        linked = [entry for entry in inventories if entry.prtg_object_id]
        if not linked:
            return "ok", []
        try:
            probes = {probe.object_id: probe for probe in await client.list_probes()}
        except PrtgRejectedError:
            return "rejected", []
        except PrtgUnreachableError:
            return "unreachable", []
        names = []
        for entry in linked:
            object_id = entry.prtg_object_id
            found = probes.get(object_id) if object_id else None
            names.append(
                (
                    entry.nats_username,
                    PrtgName(
                        "ok" if found else "not_found",
                        object_id,
                        entry.probe_name,
                        found.name if found else None,
                    ),
                )
            )
        return "ok", names

    async def candidates(self, inventory: ProbeInventory) -> LinkCandidates:
        """PRTG probes this one could be, the likely one first.

        Probes already linked to another probe here are left out: one PRTG
        object is one probe. The suggestion is only a suggestion - the name
        matches, which is what the operator confirms or rejects.
        """
        client = self._require_client()
        taken = self._linked_elsewhere(inventory.nats_username)
        probes = [
            probe
            for probe in await client.list_probes()
            if probe.object_id not in taken
        ]
        matching = [p for p in probes if p.name == inventory.probe_name]
        suggested = matching[0].object_id if len(matching) == 1 else None
        probes.sort(
            key=lambda p: (p.object_id != suggested, p.name.casefold(), p.object_id)
        )
        return LinkCandidates(suggested, tuple(probes))

    async def link(self, inventory: ProbeInventory, object_id: str) -> PrtgProbe:
        """Check the id against PRTG and against the other links, then store it."""
        object_id = validate_object_id(object_id)
        owner = self._linked_elsewhere(inventory.nats_username).get(object_id)
        if owner is not None:
            raise PrtgObjectIdTakenError(
                params={"object_id": object_id, "probe": owner},
                fields=["object_id"],
            )
        probe = await self._require_client().get_probe(object_id)
        if probe is None:
            raise PrtgObjectNotFoundError(
                params={"object_id": object_id}, fields=["object_id"]
            )
        self._runtime.write_probe_prtg_object_id(inventory.nats_username, object_id)
        return probe

    def unlink(self, inventory: ProbeInventory) -> None:
        self._runtime.write_probe_prtg_object_id(inventory.nats_username, "")

    def _linked_elsewhere(self, nats_username: str) -> dict[str, str]:
        return {
            entry.prtg_object_id: entry.nats_username
            for entry in self._runtime.read_all_probes()
            if entry.prtg_object_id and entry.nats_username != nats_username
        }
