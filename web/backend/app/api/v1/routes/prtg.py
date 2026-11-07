"""The name a probe has in PRTG today, and the settings that let us ask.

Synchronous reads rather than jobs: each is one short request to the core,
somebody is looking at the page, and none of them changes anything - PRTG is
never written to. The probe list and the probe page do not wait for PRTG
either; the interface asks these endpoints alongside them, so a slow core
delays one column and not the page.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, status
from pydantic import Field

from app.api.deps.common import (
    AuditDep,
    PrincipalDep,
    ProbeServiceDep,
    RuntimeDep,
    SettingsDep,
    require_permission,
)
from app.api.schemas.common import ApiModel
from app.core.permissions import Permission
from app.infrastructure.prtg_core import PrtgCoreStore
from app.infrastructure.runtime_files import ProbeInventory, RuntimeFileStore
from app.persistence.models.inventory import ProbeRecord
from app.services.audit import AuditWriter
from app.services.probes import ProbeService
from app.services.prtg_names import NameState, PrtgName, PrtgNameService

router = APIRouter(prefix="/prtg", tags=["prtg"])
probe_router = APIRouter(prefix="/probes", tags=["prtg"])


def get_prtg_transport() -> httpx.AsyncBaseTransport | None:
    """None means the real network. Tests replace this with a fake core."""
    return None


def get_prtg_store(settings: SettingsDep) -> PrtgCoreStore:
    return PrtgCoreStore(settings)


def get_prtg_names(
    runtime: RuntimeDep,
    store: Annotated[PrtgCoreStore, Depends(get_prtg_store)],
    transport: Annotated[httpx.AsyncBaseTransport | None, Depends(get_prtg_transport)],
) -> PrtgNameService:
    return PrtgNameService(store, runtime, transport)


PrtgStoreDep = Annotated[PrtgCoreStore, Depends(get_prtg_store)]
PrtgNamesDep = Annotated[PrtgNameService, Depends(get_prtg_names)]


# --- Settings ---------------------------------------------------------------


class PrtgSettingsOut(ApiModel):
    core_url: str | None
    # Whether a token is stored - never the token. There is no endpoint that
    # returns it: it is entered in PRTG's own interface and pasted here, and
    # the place to see it again is PRTG.
    token_configured: bool
    ca_bundle_configured: bool


class PrtgSettingsIn(ApiModel):
    core_url: str | None = Field(default=None, max_length=512)
    # Left out or empty keeps the stored token; the form never shows it.
    token: str | None = Field(default=None, max_length=4096)
    clear_token: bool = False
    ca_bundle: str | None = Field(default=None, max_length=262144)
    clear_ca_bundle: bool = False


def _settings_out(store: PrtgCoreStore) -> PrtgSettingsOut:
    current = store.read()
    return PrtgSettingsOut(
        core_url=current.core_url,
        token_configured=current.token is not None,
        ca_bundle_configured=current.ca_bundle_path is not None,
    )


@router.get("/settings", response_model=PrtgSettingsOut)
async def read_prtg_settings(
    store: PrtgStoreDep,
    _: Annotated[object, Depends(require_permission(Permission.SYSTEM_READ))],
) -> PrtgSettingsOut:
    return _settings_out(store)


@router.put("/settings", response_model=PrtgSettingsOut)
async def write_prtg_settings(
    payload: PrtgSettingsIn,
    store: PrtgStoreDep,
    audit: AuditDep,
    _: Annotated[PrincipalDep, Depends(require_permission(Permission.SYSTEM_SETTINGS))],
) -> PrtgSettingsOut:
    before = _settings_out(store).model_dump()
    store.write(
        core_url=payload.core_url,
        token=payload.token,
        clear_token=payload.clear_token,
        ca_bundle=payload.ca_bundle,
        clear_ca_bundle=payload.clear_ca_bundle,
    )
    after = _settings_out(store)
    # Flags only. Whether a token is set is worth a record; its value is not
    # something an immutable audit trail may ever hold.
    audit.record(
        action="prtg.settings.update",
        object_type="system",
        object_label="prtg",
        before=before,
        after=after.model_dump(),
    )
    return after


# --- Names ------------------------------------------------------------------


class PrtgNameOut(ApiModel):
    state: NameState
    object_id: str | None
    enrollment_name: str | None
    name_in_prtg: str | None
    differs: bool


class PrtgListedNameOut(ApiModel):
    nats_username: str
    object_id: str | None
    name_in_prtg: str | None
    differs: bool


class PrtgNamesOut(ApiModel):
    # One state for the whole list: a single request answers it, so either
    # every name arrived or none did, for the same reason.
    state: NameState
    names: list[PrtgListedNameOut]


def _name_out(name: PrtgName) -> PrtgNameOut:
    return PrtgNameOut(
        state=name.state,
        object_id=name.object_id,
        enrollment_name=name.enrollment_name,
        name_in_prtg=name.name_in_prtg,
        differs=name.differs,
    )


@router.get("/probe-names", response_model=PrtgNamesOut)
async def read_prtg_probe_names(
    names: PrtgNamesDep,
    runtime: RuntimeDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> PrtgNamesOut:
    state, found = await names.names_for_all(runtime.read_all_probes())
    return PrtgNamesOut(
        state=state,
        names=[
            PrtgListedNameOut(
                nats_username=username,
                object_id=name.object_id,
                name_in_prtg=name.name_in_prtg,
                differs=name.differs,
            )
            for username, name in found
        ],
    )


async def _probe(
    probe_id: str, probes: ProbeService, runtime: RuntimeFileStore
) -> tuple[ProbeRecord, ProbeInventory]:
    """The probe's record and its inventory, which holds the link."""
    record = await probes.get_record(probe_id)
    return record, runtime.read_probe(record.nats_username)


@probe_router.get("/{probe_id}/prtg-name", response_model=PrtgNameOut)
async def read_prtg_name(
    probe_id: str,
    probes: ProbeServiceDep,
    runtime: RuntimeDep,
    names: PrtgNamesDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> PrtgNameOut:
    _record, inventory = await _probe(probe_id, probes, runtime)
    return _name_out(await names.name_for(inventory))


# --- Linking ----------------------------------------------------------------


class PrtgCandidateOut(ApiModel):
    object_id: str
    name: str
    status: str | None


class PrtgCandidatesOut(ApiModel):
    suggested_object_id: str | None
    candidates: list[PrtgCandidateOut]


class PrtgLinkIn(ApiModel):
    # A string, not an int: PRTG returns ids as strings, and the shape check
    # in the PRTG client gives one error for "abc" and "../1" alike.
    object_id: str = Field(min_length=1, max_length=16)


def _audit_link(
    audit: AuditWriter,
    action: str,
    record: ProbeRecord,
    before: str | None,
    after: str | None,
) -> None:
    audit.record(
        action=action,
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        before={"prtg_object_id": before},
        after={"prtg_object_id": after},
    )


@probe_router.get("/{probe_id}/prtg-link/candidates", response_model=PrtgCandidatesOut)
async def read_prtg_link_candidates(
    probe_id: str,
    probes: ProbeServiceDep,
    runtime: RuntimeDep,
    names: PrtgNamesDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_UPDATE))],
) -> PrtgCandidatesOut:
    _record, inventory = await _probe(probe_id, probes, runtime)
    offered = await names.candidates(inventory)
    return PrtgCandidatesOut(
        suggested_object_id=offered.suggested_object_id,
        candidates=[
            PrtgCandidateOut(
                object_id=probe.object_id, name=probe.name, status=probe.status
            )
            for probe in offered.candidates
        ],
    )


@probe_router.put("/{probe_id}/prtg-link", response_model=PrtgNameOut)
async def link_prtg_object(
    probe_id: str,
    payload: PrtgLinkIn,
    probes: ProbeServiceDep,
    runtime: RuntimeDep,
    names: PrtgNamesDep,
    audit: AuditDep,
    _: Annotated[PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))],
) -> PrtgNameOut:
    """Link the probe to its PRTG object, after PRTG confirmed the id."""
    record, inventory = await _probe(probe_id, probes, runtime)
    linked = await names.link(inventory, payload.object_id)
    _audit_link(
        audit, "probe.prtg_link", record, inventory.prtg_object_id, linked.object_id
    )
    return _name_out(linked)


@probe_router.delete("/{probe_id}/prtg-link", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_prtg_object(
    probe_id: str,
    probes: ProbeServiceDep,
    runtime: RuntimeDep,
    names: PrtgNamesDep,
    audit: AuditDep,
    _: Annotated[PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))],
) -> None:
    record, inventory = await _probe(probe_id, probes, runtime)
    names.unlink(inventory)
    _audit_link(audit, "probe.prtg_unlink", record, inventory.prtg_object_id, None)
