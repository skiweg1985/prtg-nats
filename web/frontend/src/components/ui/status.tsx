import clsx from 'clsx'
import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  CircleX,
  LoaderCircle,
  type LucideIcon,
} from 'lucide-react'
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

import { Badge, StatusText } from './primitives'

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
  // Still going, like running - the work just moved to a container that
  // outlives the API. Anything calmer would read as finished.
  detached: 'accent',
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

const JOB_ICON: Record<JobStatus, LucideIcon> = {
  queued: CircleDashed,
  running: LoaderCircle,
  detached: LoaderCircle,
  successful: CircleCheck,
  failed: CircleX,
  cancelled: CircleSlash,
  partially_successful: CircleAlert,
}

const ICON_TONE: Record<Tone, string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-danger',
  neutral: 'text-ink-3',
  accent: 'text-accent',
}

/**
 * A job's state as a single icon, for a compact list where a badge per row
 * turned the column into a stripe of colour. Same tone as the badge; the
 * label is the accessible name and the tooltip, and a running job's circle
 * turns, which is the one motion here that reports something.
 */
export function JobStatusIcon({ status }: { status: JobStatus }) {
  const { t } = useTranslation()
  const Icon = JOB_ICON[status]
  const label = t(`status.job.${status}`)
  return (
    <span role="img" aria-label={label} title={label} className="inline-flex shrink-0">
      <Icon
        aria-hidden
        size={16}
        strokeWidth={1.75}
        className={clsx(
          ICON_TONE[JOB_TONE[status]],
          (status === 'running' || status === 'detached') && 'animate-spin [animation-duration:2s]',
        )}
      />
    </span>
  )
}

/** A row's state in a table, after StatusText: dot and word. */
export function ProbeStatusText({ status }: { status: ProbeStatus }) {
  const { t } = useTranslation()
  return <StatusText tone={PROBE_TONE[status]}>{t(`status.probe.${status}`)}</StatusText>
}

export function SensorStatusText({ status }: { status: SensorInstallationStatus }) {
  const { t } = useTranslation()
  return <StatusText tone={SENSOR_TONE[status]}>{t(`status.sensor.${status}`)}</StatusText>
}

/** A job's state in a table: its icon, which turns while it runs, and the
    word. */
export function JobStatusText({ status }: { status: JobStatus }) {
  const { t } = useTranslation()
  const Icon = JOB_ICON[status]
  return (
    <span className="text-ink-2 inline-flex items-center gap-1.5 text-sm whitespace-nowrap">
      <Icon
        aria-hidden
        size={14}
        strokeWidth={1.75}
        className={clsx(
          'shrink-0',
          ICON_TONE[JOB_TONE[status]],
          (status === 'running' || status === 'detached') && 'animate-spin [animation-duration:2s]',
        )}
      />
      {t(`status.job.${status}`)}
    </span>
  )
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

  return <StatusText tone={tone}>{t(`status.${kind}.${value}`)}</StatusText>
}
