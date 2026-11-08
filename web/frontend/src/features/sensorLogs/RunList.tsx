import { Fragment, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useSensorLog, useSensorLogStream, useSensorLogs } from '@/api/hooks'
import type { SensorLogResult, SensorLogSummary } from '@/api/types'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Badge, Card, EmptyState, Mono, Skeleton } from '@/components/ui/primitives'
import { formatDateTime } from '@/utils/format'

const RESULT_TONE: Record<SensorLogResult, 'ok' | 'danger' | 'warn'> = {
  ok: 'ok',
  error: 'danger',
  gap: 'warn',
}

export function ResultBadge({ result }: { result: SensorLogResult }) {
  const { t } = useTranslation()
  return <Badge tone={RESULT_TONE[result]}>{t(`sensorLogs.result.${result}`)}</Badge>
}

/** Where a single run can be opened and sent to a colleague. */
export function runHref(id: string): string {
  return `/sensor-logs/${encodeURIComponent(id)}`
}

/**
 * Runs as rows, each one opening onto its raw output.
 *
 * The body is fetched only when a row is opened: a list of a hundred runs
 * would otherwise carry a hundred supplicant transcripts nobody reads.
 */
export function RunTable({
  runs,
  showProbe = true,
  emptyTitle,
  emptyAction,
}: {
  runs: SensorLogSummary[]
  showProbe?: boolean
  emptyTitle: string
  emptyAction?: ReactNode
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<Set<string>>(new Set())

  function toggle(id: string) {
    const next = new Set(open)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setOpen(next)
  }

  if (runs.length === 0) return <EmptyState title={emptyTitle} action={emptyAction} />

  const columnCount = showProbe ? 7 : 6

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead className="bg-surface-2">
          <tr className="text-ink-3 text-xs">
            <th className="w-8 px-3 py-2" />
            <th className="px-3 py-2 font-medium">{t('sensorLogs.columns.time')}</th>
            {showProbe && (
              <th className="px-3 py-2 font-medium">{t('sensorLogs.columns.probe')}</th>
            )}
            <th className="px-3 py-2 font-medium">{t('sensorLogs.columns.sensor')}</th>
            <th className="px-3 py-2 font-medium">{t('sensorLogs.columns.network')}</th>
            <th className="px-3 py-2 font-medium">{t('sensorLogs.columns.result')}</th>
            <th className="px-3 py-2 font-medium">{t('sensorLogs.columns.summary')}</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const isOpen = open.has(run.id)
            return (
              <Fragment key={run.id}>
                <tr className="border-rule border-t align-top">
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => toggle(run.id)}
                      aria-expanded={isOpen}
                      aria-label={t(isOpen ? 'common.hideDetails' : 'common.showDetails')}
                      className="text-ink-3 hover:text-ink text-xs"
                    >
                      {isOpen ? '▾' : '▸'}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-sm whitespace-nowrap">
                    <Link to={runHref(run.id)} className="text-accent hover:underline">
                      {formatDateTime(run.time)}
                    </Link>
                  </td>
                  {showProbe && (
                    <td className="px-3 py-2">
                      <Mono>{run.probe}</Mono>
                    </td>
                  )}
                  <td className="px-3 py-2">
                    <Mono>{run.sensor}</Mono>
                  </td>
                  <td className="px-3 py-2">
                    <Mono>{run.ssid ?? '—'}</Mono>
                  </td>
                  <td className="px-3 py-2">
                    <ResultBadge result={run.result} />
                  </td>
                  <td className="text-ink-2 px-3 py-2 text-sm">
                    <span>{run.summary || '—'}</span>
                    {run.code && <Mono className="text-ink-3 ml-2">{run.code}</Mono>}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="bg-surface-2">
                    <td colSpan={columnCount} className="px-4 py-3">
                      <RunBody id={run.id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The runs of one probe, one block per sensor and network.
 *
 * On the probe page an operator is looking at one network going red, and
 * the runs of another network in between would only be noise.
 */
export function ProbeRunsCard({ probe }: { probe: string }) {
  const { t } = useTranslation()
  const filters = { probe, range: '24h' as const }
  const { data, isLoading, error } = useSensorLogs(filters)
  useSensorLogStream({ probe })

  const groups = new Map<string, SensorLogSummary[]>()
  for (const run of data?.items ?? []) {
    const label = run.ssid ? `${run.sensor} · ${run.ssid}` : run.sensor
    groups.set(label, [...(groups.get(label) ?? []), run])
  }

  return (
    <Card
      title={t('sensorLogs.probeCard')}
      dense
      action={
        <Link
          to={`/sensor-logs?probe=${encodeURIComponent(probe)}`}
          className="text-accent text-xs hover:underline"
        >
          {t('sensorLogs.all')}
        </Link>
      }
    >
      {error ? (
        <div className="p-3">
          <ErrorDetails error={error} />
        </div>
      ) : isLoading ? (
        <Skeleton className="m-3 h-24" />
      ) : groups.size === 0 ? (
        <EmptyState title={t('sensorLogs.empty')} />
      ) : (
        [...groups.entries()]
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([label, runs]) => (
            <section key={label} className="border-rule border-b last:border-0">
              <h3 className="text-ink px-4 pt-3 pb-1 font-mono text-xs font-medium">
                {label}
              </h3>
              <RunTable
                runs={runs}
                showProbe={false}
                emptyTitle={t('sensorLogs.empty')}
              />
            </section>
          ))
      )}
    </Card>
  )
}

function RunBody({ id }: { id: string }) {
  const { data, isLoading, error } = useSensorLog(id)
  if (isLoading) return <Skeleton className="h-24" />
  if (error) return <ErrorDetails error={error} />
  if (!data) return null
  return <RecordBody body={data.body} sensor={data.sensor} />
}

/**
 * Readable names for the body fields of the sensors that ship with the
 * platform. A sensor not listed here keeps its own field names - the body is
 * whatever it sent, and a guessed label would be worse than the raw key.
 */
const FIELD_LABELS: Record<string, Record<string, string>> = {
  'wlan-auth': {
    ssid: 'sensorLogs.wlanAuth.ssid',
    auth: 'sensorLogs.wlanAuth.auth',
    interface: 'sensorLogs.wlanAuth.interface',
    stage: 'sensorLogs.wlanAuth.stage',
    identity: 'sensorLogs.wlanAuth.identity',
    profile: 'sensorLogs.wlanAuth.profile',
    bssid: 'sensorLogs.wlanAuth.bssid',
    client_mac: 'sensorLogs.wlanAuth.clientMac',
    client_mac_kind: 'sensorLogs.wlanAuth.clientMacKind',
    ip_address: 'sensorLogs.wlanAuth.ipAddress',
    dhcp_server: 'sensorLogs.wlanAuth.dhcpServer',
    gateway: 'sensorLogs.wlanAuth.gateway',
    gateway_kind: 'sensorLogs.wlanAuth.gatewayKind',
    frequency_mhz: 'sensorLogs.wlanAuth.frequency',
    signal_dbm: 'sensorLogs.wlanAuth.signal',
    timings: 'sensorLogs.wlanAuth.timings',
    assoc_ms: 'sensorLogs.wlanAuth.assocMs',
    auth_ms: 'sensorLogs.wlanAuth.authMs',
    dhcp_ms: 'sensorLogs.wlanAuth.dhcpMs',
    gateway_ms: 'sensorLogs.wlanAuth.gatewayMs',
    total_ms: 'sensorLogs.wlanAuth.totalMs',
    supplicant: 'sensorLogs.wlanAuth.supplicant',
    dhcp: 'sensorLogs.wlanAuth.dhcp',
    events: 'sensorLogs.wlanAuth.events',
  },
}

function useFieldLabel(sensor: string | undefined): (key: string) => string {
  const { t } = useTranslation()
  const labels = (sensor && FIELD_LABELS[sensor]) || {}
  return (key) => (labels[key] ? t(labels[key]) : key)
}

type Scalar = string | number | boolean | null

function isScalar(value: unknown): value is Scalar {
  return (
    value === null ||
    typeof value === 'number' ||
    typeof value === 'boolean' ||
    (typeof value === 'string' && !value.includes('\n'))
  )
}

function isLines(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string')
}

function isGroup(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

/**
 * A record's body, whatever the sensor put in it.
 *
 * The platform stores the body as the sensor sent it, so this cannot know the
 * fields. It lays them out by shape instead: single values as a list, groups
 * of values (stage timings, say) as a list of their own, and lines of raw
 * output as preformatted blocks - the part an operator came for.
 */
export function RecordBody({
  body,
  sensor,
}: {
  body: Record<string, unknown>
  sensor?: string
}) {
  const { t } = useTranslation()
  const label = useFieldLabel(sensor)
  const entries = Object.entries(body)
  const scalars = entries.filter(([, value]) => isScalar(value))
  const groups = entries.filter(
    (entry): entry is [string, Record<string, unknown>] => isGroup(entry[1]),
  )
  const raw = entries.filter(([, value]) => !isScalar(value) && !isGroup(value))

  if (entries.length === 0) {
    return <p className="text-ink-3 text-sm">{t('sensorLogs.noBody')}</p>
  }

  return (
    <div className="space-y-4">
      {scalars.length > 0 && <Values entries={scalars} label={label} />}
      {groups.map(([key, value]) => (
        <section key={key} className="space-y-1">
          <h3 className="label-mono">{label(key)}</h3>
          <Values entries={Object.entries(value)} label={label} />
        </section>
      ))}
      {raw.map(([key, value]) => (
        <section key={key} className="space-y-1">
          <h3 className="label-mono">{label(key)}</h3>
          <pre className="bg-surface border-rule rounded-control max-h-96 overflow-auto border p-3 font-mono text-xs whitespace-pre-wrap">
            {isLines(value)
              ? value.join('\n')
              : typeof value === 'string'
                ? value
                : JSON.stringify(value, null, 2)}
          </pre>
        </section>
      ))}
    </div>
  )
}

function Values({
  entries,
  label,
}: {
  entries: [string, unknown][]
  label: (key: string) => string
}) {
  return (
    <dl className="grid grid-cols-[minmax(8rem,14rem)_1fr] gap-x-3 gap-y-1">
      {entries.map(([key, value]) => (
        <Fragment key={key}>
          <dt className="text-ink-3 font-mono text-xs">{label(key)}</dt>
          <dd className="text-ink min-w-0 font-mono text-xs break-all">{show(value)}</dd>
        </Fragment>
      ))}
    </dl>
  )
}
