"""Version 1 of the return channel: records in, acknowledgements out."""

from __future__ import annotations

from cryptography import x509
from fastapi import APIRouter

from app.api.deps.common import BroadcasterDep, SensorLogStoreDep, SettingsDep
from app.api.probe.deps import ProbeIdentityDep
from app.api.schemas.probe_channel import CertificateRenewalIn, CertificateRenewalOut
from app.api.schemas.sensor_logs import SensorLogBatchIn, SensorLogBatchOut
from app.infrastructure.pki import Pki
from app.services.sensor_logs import format_time, ingest

probe_router = APIRouter(prefix="/probe/v1", tags=["probe channel"])


@probe_router.post("/records", response_model=SensorLogBatchOut)
async def submit_records(
    batch: SensorLogBatchIn,
    probe: ProbeIdentityDep,
    store: SensorLogStoreDep,
    broadcaster: BroadcasterDep,
) -> SensorLogBatchOut:
    """Store a batch of sensor log records from one probe.

    Idempotent on the record ID: a batch sent again after a lost answer comes
    back with its IDs under ``duplicates``, and the probe drops both lists
    from its buffer.
    """
    records = [
        {
            "id": str(item.id),
            "sensor": item.sensor,
            "job": item.job,
            "time": format_time(item.time),
            "result": item.result,
            "code": item.code,
            "summary": item.summary,
            "body": item.body,
        }
        for item in batch.records
    ]
    outcome = await ingest(store, broadcaster, probe, records)
    return SensorLogBatchOut(
        stored=[str(entry["id"]) for entry in outcome.stored],
        duplicates=outcome.duplicates,
    )


@probe_router.post("/certificate", response_model=CertificateRenewalOut)
async def renew_certificate(
    request: CertificateRenewalIn,
    probe: ProbeIdentityDep,
    settings: SettingsDep,
) -> CertificateRenewalOut:
    """Sign a fresh client certificate for the probe that is calling.

    The probe renews over the channel its current certificate opens, so a
    renewal needs no SSH round trip. It is named after the caller, never after
    the subject in the CSR; once the old certificate has expired, only the
    management channel can issue a new one (the install_return_channel job
    step).
    """
    pem = Pki(settings).sign_probe_csr(request.csr, probe)
    certificate = x509.load_pem_x509_certificate(pem.encode("ascii"))
    return CertificateRenewalOut(
        certificate=pem, not_after=certificate.not_valid_after_utc
    )
