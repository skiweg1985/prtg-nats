"""System status and the dashboard.

Assembles one answer to "is the platform operational, and is there anything to
do?" from the sources that can each fail independently - and keeps failing
gracefully, because a status page has to render when things are wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.enums import CertificateKind, CertificateStatus, JobStatus, ProbeStatus
from app.domain.models import ProbeSummary
from app.infrastructure.certificates import CertificateInfo, read_certificate
from app.infrastructure.docker import ContainerState, DockerAdapter
from app.infrastructure.nats import NatsMonitoringClient, NatsServerState
from app.infrastructure.runtime_files import RuntimeFileStore, SiteSettings
from app.persistence.models.audit import AuditEvent
from app.persistence.models.inventory import Alert
from app.persistence.models.jobs import Job


@dataclass(frozen=True, slots=True)
class SystemStatus:
    site: SiteSettings
    nats: NatsServerState
    containers: dict[str, ContainerState]
    certificates: list[CertificateInfo]
    runtime_state: str
    runtime_missing: tuple[str, ...]
    docker_available: bool


@dataclass(frozen=True, slots=True)
class DashboardData:
    system: SystemStatus
    probe_total: int
    probe_healthy: int
    probe_degraded: int
    probe_unreachable: int
    # Probes stuck between the states the three counters above cover: half
    # enrolled, or enrolled and never registered in PRTG. Both used to be in
    # no number at all, so "all good" could show over a probe nobody finished.
    probe_pending: int
    probe_prtg_missing: int
    probes_with_deviations: int
    failed_jobs_24h: int
    running_jobs: int
    expiring_certificates: list[CertificateInfo]
    alerts: list[Alert]
    recent_jobs: list[Job]
    recent_audit: list[AuditEvent]


class SystemService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        runtime: RuntimeFileStore,
        nats: NatsMonitoringClient,
        docker: DockerAdapter,
    ) -> None:
        self._db = session
        self._settings = settings
        self._runtime = runtime
        self._nats = nats
        self._docker = docker

    async def status(self) -> SystemStatus:
        health = self._runtime.health()
        return SystemStatus(
            site=self._runtime.site_settings(),
            nats=await self._nats.fetch_state(),
            containers=await self._docker.inspect_all(),
            certificates=self.certificates(),
            runtime_state=health.state,
            runtime_missing=health.missing,
            docker_available=self._docker.available,
        )

    def certificates(self) -> list[CertificateInfo]:
        warning_days = self._settings.certificate_expiry_warning_days
        return [
            read_certificate(
                self._settings.cert_dir / "ca.pem",
                CertificateKind.CA,
                warning_days=warning_days,
            ),
            read_certificate(
                self._settings.cert_dir / "server.pem",
                CertificateKind.SERVER,
                key_path=self._settings.cert_dir / "server-key.pem",
                warning_days=warning_days,
            ),
        ]

    def expected_ca_fingerprint(self) -> str | None:
        """What every probe's ``ca_sha256`` should equal."""
        info = read_certificate(self._settings.cert_dir / "ca.pem", CertificateKind.CA)
        return info.sha256

    async def dashboard(self, summaries: list[ProbeSummary]) -> DashboardData:
        status = await self.status()
        since = datetime.now(UTC) - timedelta(hours=24)

        failed_jobs = await self._db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == JobStatus.FAILED, Job.created_at >= since)
        )
        running_jobs = await self._db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_([JobStatus.RUNNING, JobStatus.QUEUED]))
        )
        recent_jobs = list(
            await self._db.scalars(select(Job).order_by(Job.id.desc()).limit(10))
        )
        recent_audit = list(
            await self._db.scalars(
                select(AuditEvent).order_by(AuditEvent.ts.desc()).limit(10)
            )
        )
        alerts = list(
            await self._db.scalars(
                select(Alert)
                .where(Alert.acknowledged_at.is_(None))
                .order_by(Alert.severity, Alert.last_seen_at.desc())
                .limit(20)
            )
        )

        return DashboardData(
            system=status,
            probe_total=len(summaries),
            probe_healthy=sum(1 for s in summaries if s.status is ProbeStatus.HEALTHY),
            probe_degraded=sum(
                1 for s in summaries if s.status is ProbeStatus.DEGRADED
            ),
            probe_unreachable=sum(
                1 for s in summaries if s.status is ProbeStatus.UNREACHABLE
            ),
            probe_pending=sum(
                1
                for s in summaries
                if s.status in (ProbeStatus.PENDING, ProbeStatus.ENROLLED)
            ),
            probe_prtg_missing=sum(
                1
                for s in summaries
                if not s.prtg_registered
                and s.status not in (ProbeStatus.PENDING, ProbeStatus.ENROLLED)
            ),
            probes_with_deviations=sum(1 for s in summaries if s.deviation_count),
            failed_jobs_24h=int(failed_jobs or 0),
            running_jobs=int(running_jobs or 0),
            expiring_certificates=[
                certificate
                for certificate in status.certificates
                if certificate.status
                in {CertificateStatus.EXPIRING_SOON, CertificateStatus.EXPIRED}
            ],
            alerts=alerts,
            recent_jobs=recent_jobs,
            recent_audit=recent_audit,
        )
