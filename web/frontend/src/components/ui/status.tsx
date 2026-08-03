import { useTranslation } from 'react-i18next'

import type {
  CaState,
  CertificateStatus,
  JobStatus,
  NatsConnectionState,
  ProbeStatus,
  SensorInstallationStatus,
  ServiceState,
} from '@/api/types'

import { Badge, Dot } from './primitives'

/**
 * Status rendering, in one place.
 *
 * Two rules the whole interface depends on: a given state always gets the same
 * colour, and the label always comes from the translation table. A badge that
 * says "healthy" in an otherwise German page is a badge somebody hard-coded.
 */

type Tone = 'ok' | 'warn' | 'danger' | 'neutral' | 'accent'

const PROBE_TONE: Record<ProbeStatus, Tone> = {
  pending: 'neutral',
  enrolled: 'accent',
  healthy: 'ok',
  degraded: 'warn',
  unreachable: 'danger',
  retired: 'neutral',
}

const SENSOR_TONE: Record<SensorInstallationStatus, Tone> = {
  absent: 'warn',
  current: 'ok',
  outdated: 'warn',
  drifted: 'warn',
  failed: 'danger',
  unmanaged: 'neutral',
}

const JOB_TONE: Record<JobStatus, Tone> = {
  queued: 'neutral',
  running: 'accent',
  successful: 'ok',
  failed: 'danger',
  cancelled: 'neutral',
  partially_successful: 'warn',
}

const CERTIFICATE_TONE: Record<CertificateStatus, Tone> = {
  valid: 'ok',
  expiring_soon: 'warn',
  expired: 'danger',
  mismatched: 'danger',
  missing: 'danger',
}

const SERVICE_TONE: Record<ServiceState, Tone> = {
  active: 'ok',
  inactive: 'danger',
  unknown: 'neutral',
}

const CA_TONE: Record<CaState, Tone> = {
  ok: 'ok',
  missing: 'danger',
  mismatched: 'danger',
  unknown: 'neutral',
}

const NATS_TONE: Record<NatsConnectionState, Tone> = {
  connected: 'ok',
  disconnected: 'danger',
  unknown: 'neutral',
}

export function ProbeStatusBadge({ status }: { status: ProbeStatus }) {
  const { t } = useTranslation()
  return <Badge tone={PROBE_TONE[status]}>{t(`status.probe.${status}`)}</Badge>
}

export function SensorStatusBadge({ status }: { status: SensorInstallationStatus }) {
  const { t } = useTranslation()
  return <Badge tone={SENSOR_TONE[status]}>{t(`status.sensor.${status}`)}</Badge>
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const { t } = useTranslation()
  return <Badge tone={JOB_TONE[status]}>{t(`status.job.${status}`)}</Badge>
}

export function CertificateStatusBadge({ status }: { status: CertificateStatus }) {
  const { t } = useTranslation()
  return (
    <Badge tone={CERTIFICATE_TONE[status]}>{t(`status.certificate.${status}`)}</Badge>
  )
}

/** Compact dot plus label, for a dense table cell. */
export function StateCell({
  kind,
  value,
}: {
  kind: 'service' | 'ca' | 'nats'
  value: ServiceState | CaState | NatsConnectionState
}) {
  const { t } = useTranslation()
  const tone =
    kind === 'service'
      ? SERVICE_TONE[value as ServiceState]
      : kind === 'ca'
        ? CA_TONE[value as CaState]
        : NATS_TONE[value as NatsConnectionState]

  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <Dot tone={tone} />
      {t(`status.${kind}.${value}`)}
    </span>
  )
}

/** The overall health of one thing, as a dot with an accessible name. */
export function HealthIndicator({
  ok,
  label,
}: {
  ok: boolean | null
  label: string
}) {
  return (
    <span className="inline-flex items-center gap-2" title={label}>
      <Dot tone={ok === null ? 'neutral' : ok ? 'ok' : 'danger'} />
      <span className="sr-only">{label}</span>
    </span>
  )
}
