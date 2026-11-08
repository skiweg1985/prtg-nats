import { Fragment, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight } from 'lucide-react'

import { useSensorLog, useSensorLogStream, useSensorLogStreams } from '@/api/hooks'
import type {
  SensorLogResult,
  SensorLogSegment,
  SensorLogStream,
  SensorLogSummary,
} from '@/api/types'
import { useIsPhone } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { ProbeLink } from '@/components/ui/ProbeLink'
import { Badge, Card, EmptyState, Mono, Skeleton } from '@/components/ui/primitives'
import { formatDateTime, formatNumber, formatSpanEnd } from '@/utils/format'

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
  const isPhone = useIsPhone()
  const [open, setOpen] = useState<Set<string>>(new Set())

  function toggle(id: string) {
    const next = new Set(open)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setOpen(next)
  }

  if (runs.length === 0) return <EmptyState title={emptyTitle} action={emptyAction} />

  if (isPhone) return <RunCards runs={runs} showProbe={showProbe} />

  const columnCount = showProbe ? 7 : 6
  // ink-2, not the label default: ink-3 on the grey header band falls under
  // 4.5:1 in both themes - the same call the fleet tables made.
  const header = 'label-mono text-ink-2 px-3 py-2 font-normal whitespace-nowrap'

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead className="bg-surface-2">
          <tr>
            <th className="w-11 p-0" />
            <th className={header}>{t('sensorLogs.columns.time')}</th>
            {showProbe && <th className={header}>{t('sensorLogs.columns.probe')}</th>}
            <th className={header}>{t('sensorLogs.columns.sensor')}</th>
            <th className={header}>{t('sensorLogs.columns.network')}</th>
            <th className={header}>{t('sensorLogs.columns.result')}</th>
            <th className={header}>{t('sensorLogs.columns.summary')}</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const isOpen = open.has(run.id)
            return (
              <Fragment key={run.id}>
                <tr className="border-rule border-t align-top">
                  <td className="p-0">
                    {/* A whole 44px square, not a 12px glyph: it is the
                        control used most on this page. */}
                    <button
                      type="button"
                      onClick={() => toggle(run.id)}
                      aria-expanded={isOpen}
                      aria-label={t(isOpen ? 'common.hideDetails' : 'common.showDetails')}
                      className="text-ink-2 hover:text-ink rounded-inset focus-visible:outline-accent inline-flex size-11 items-center justify-center focus-visible:outline-2 focus-visible:-outline-offset-2"
                    >
                      {isOpen ? (
                        <ChevronDown aria-hidden size={16} strokeWidth={1.75} />
                      ) : (
                        <ChevronRight aria-hidden size={16} strokeWidth={1.75} />
                      )}
                    </button>
                  </td>
                  <td className="px-3 py-3 text-sm whitespace-nowrap">
                    <Link to={runHref(run.id)} className="text-accent hover:underline">
                      {formatDateTime(run.time)}
                    </Link>
                  </td>
                  {showProbe && (
                    <td className="px-3 py-3 text-sm">
                      <ProbeLink username={run.probe} />
                    </td>
                  )}
                  {/* A name is one token: wrapped at its hyphen, "wlan-auth"
                      reads as two words. */}
                  <td className="px-3 py-3 text-sm whitespace-nowrap">
                    <Mono>{run.sensor}</Mono>
                  </td>
                  <td className="px-3 py-3 text-sm whitespace-nowrap">
                    <Mono>{run.ssid ?? '—'}</Mono>
                  </td>
                  <td className="px-3 py-3">
                    <ResultBadge result={run.result} />
                  </td>
                  <td className="text-ink-2 px-3 py-3 text-sm">
                    <RunSummary run={run} />
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

function RunSummary({ run }: { run: SensorLogSummary }) {
  const codeLabel = useCodeLabel()
  const words = codeLabel(run.sensor, run.code)
  return (
    <>
      <span>{run.summary || words || '—'}</span>
      {run.code && (
        <span className="ml-2" title={words ?? undefined}>
          <Mono className="text-ink-3">{run.code}</Mono>
        </span>
      )}
    </>
  )
}

/**
 * The runs as cards, below the md breakpoint.
 *
 * As a table the log scrolled sideways on a phone and wrapped a probe name
 * over three lines. A card opens the run's own page instead of unfolding in
 * place: the output is a wide transcript, and the page has the room for it.
 * The probe sits outside the card's link, so each destination has exactly
 * one control and no link holds another.
 */
function RunCards({ runs, showProbe }: { runs: SensorLogSummary[]; showProbe: boolean }) {
  return (
    <ul>
      {runs.map((run) => (
        <li key={run.id} className="border-rule border-t first:border-t-0">
          <Link
            to={runHref(run.id)}
            className="focus-visible:outline-accent active:bg-surface-2 block px-3 pt-3 pb-1.5 focus-visible:outline-2 focus-visible:-outline-offset-2"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-ink text-sm font-medium tabular-nums">
                {formatDateTime(run.time)}
              </span>
              <ResultBadge result={run.result} />
            </div>
            <p className="text-ink-2 mt-1 text-sm break-words">
              <RunSummary run={run} />
            </p>
          </Link>
          <div className="text-ink-2 flex min-h-9 flex-wrap items-center gap-x-3 gap-y-1 px-3 pb-2 text-xs">
            {showProbe && <ProbeLink username={run.probe} />}
            {run.ssid && <Mono>{run.ssid}</Mono>}
            {showProbe && <Mono className="text-ink-3">{run.sensor}</Mono>}
          </div>
        </li>
      ))}
    </ul>
  )
}

/**
 * The runs of one probe, one block per sensor and network.
 *
 * On the probe page an operator is looking at one network going red, and
 * the runs of another network in between would only be noise. A sensor that
 * runs every minute also made the block a thousand rows long, with the one
 * failure somewhere in the middle. Runs in a row that ended the same way
 * are one line now - "OK since 16:04, 74 runs" - so a failure stands out as
 * its own line, and every run is still one click away in the sensor log.
 */
export function ProbeRunsCard({ probe }: { probe: string }) {
  const { t } = useTranslation()
  const { data, isLoading, error } = useSensorLogStreams(probe)
  useSensorLogStream({ probe })

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
      ) : !data || data.length === 0 ? (
        <EmptyState title={t('sensorLogs.empty')} />
      ) : (
        data.map((stream) => (
          <StreamBlock
            key={`${stream.sensor}|${stream.ssid ?? ''}`}
            probe={probe}
            stream={stream}
          />
        ))
      )}
    </Card>
  )
}

function streamHref(probe: string, stream: SensorLogStream): string {
  const params = new URLSearchParams({ probe, sensor: stream.sensor })
  if (stream.ssid) params.set('ssid', stream.ssid)
  return `/sensor-logs?${params.toString()}`
}

function StreamBlock({ probe, stream }: { probe: string; stream: SensorLogStream }) {
  const { t } = useTranslation()
  const label = stream.ssid ? `${stream.sensor} · ${stream.ssid}` : stream.sensor
  const failed = (stream.results.error ?? 0) + (stream.results.gap ?? 0)
  return (
    <section className="border-rule border-b last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 px-4 pt-3 pb-1">
        <h3 className="text-ink font-mono text-xs font-medium">{label}</h3>
        <span className="text-ink-3 text-xs">
          {t('sensorLogs.streams.runs', { count: stream.total })}
          {failed > 0 && ` · ${t('sensorLogs.streams.failed', { count: failed })}`}
        </span>
      </div>
      <ul>
        {stream.segments.map((segment, index) => (
          <SegmentRow
            key={segment.last.id}
            segment={segment}
            current={index === 0}
            sensor={stream.sensor}
          />
        ))}
      </ul>
      <div className="px-4 pt-1 pb-3">
        <Link to={streamHref(probe, stream)} className="text-accent text-xs hover:underline">
          {t('sensorLogs.streams.all', { count: stream.total })}
        </Link>
      </div>
    </section>
  )
}

/**
 * One segment: a single run opens onto itself, several read as a span.
 *
 * The newest segment is "since", because it is still going on; an older one
 * has a start and an end.
 */
function SegmentRow({
  segment,
  current,
  sensor,
}: {
  segment: SensorLogSegment
  current: boolean
  sensor: string
}) {
  const { t } = useTranslation()
  const codeLabel = useCodeLabel()
  const words = segment.last.summary || codeLabel(sensor, segment.code)
  const when =
    segment.count === 1
      ? formatDateTime(segment.last.time)
      : current
        ? t('sensorLogs.streams.since', { time: formatDateTime(segment.first.time) })
        : t('sensorLogs.streams.span', {
            from: formatDateTime(segment.first.time),
            to: formatSpanEnd(segment.first.time, segment.last.time),
          })
  return (
    <li className="border-rule flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-4 py-2 first:border-t-0">
      <ResultBadge result={segment.result} />
      <Link
        to={runHref(segment.last.id)}
        className="text-accent text-sm whitespace-nowrap tabular-nums hover:underline"
      >
        {when}
      </Link>
      {segment.count > 1 && (
        <span className="text-ink-2 text-sm whitespace-nowrap">
          {t('sensorLogs.streams.runs', { count: segment.count })}
        </span>
      )}
      {segment.result !== 'ok' && words && (
        // Its own line on a phone: squeezed beside the time and the count,
        // the sentence broke after every word.
        <span className="text-ink-2 min-w-0 text-sm max-md:basis-full md:flex-1">
          {words}
          {segment.code && (
            <Mono className="text-ink-3 ml-2 whitespace-nowrap">{segment.code}</Mono>
          )}
        </span>
      )}
    </li>
  )
}

function RunBody({ id }: { id: string }) {
  const { data, isLoading, error } = useSensorLog(id)
  if (isLoading) return <Skeleton className="h-24" />
  if (error) return <ErrorDetails error={error} />
  if (!data) return null
  return <RecordBody body={data.body} sensor={data.sensor} />
}

/** The unit a body field is measured in, shown with its value. */
type Unit = 'ms' | 'mhz' | 'dbm'

interface FieldInfo {
  label: string
  unit?: Unit
  /** Words for the fixed values a field takes; anything else stays raw. */
  values?: Record<string, string>
}

/**
 * Readable names for the body fields of the sensors that ship with the
 * platform. A sensor not listed here keeps its own field names - the body is
 * whatever it sent, and a guessed label would be worse than the raw key.
 *
 * The unit belongs to the value, not the label: "41 ms" next to "2,1 s"
 * reads at a glance, a column of bare numbers under "(ms)" does not.
 */
const FIELDS: Record<string, Record<string, FieldInfo>> = {
  'wlan-auth': {
    ssid: { label: 'sensorLogs.wlanAuth.ssid' },
    auth: { label: 'sensorLogs.wlanAuth.auth' },
    interface: { label: 'sensorLogs.wlanAuth.interface' },
    stage: {
      label: 'sensorLogs.wlanAuth.stage',
      values: {
        assoc: 'sensorLogs.wlanAuth.values.stage.assoc',
        dhcp: 'sensorLogs.wlanAuth.values.stage.dhcp',
      },
    },
    identity: { label: 'sensorLogs.wlanAuth.identity' },
    profile: { label: 'sensorLogs.wlanAuth.profile' },
    bssid: { label: 'sensorLogs.wlanAuth.bssid' },
    client_mac: { label: 'sensorLogs.wlanAuth.clientMac' },
    client_mac_kind: {
      label: 'sensorLogs.wlanAuth.clientMacKind',
      values: {
        auto: 'sensorLogs.wlanAuth.values.clientMacKind.auto',
        permanent: 'sensorLogs.wlanAuth.values.clientMacKind.permanent',
        fixed: 'sensorLogs.wlanAuth.values.clientMacKind.fixed',
      },
    },
    ip_address: { label: 'sensorLogs.wlanAuth.ipAddress' },
    dhcp_server: { label: 'sensorLogs.wlanAuth.dhcpServer' },
    gateway: { label: 'sensorLogs.wlanAuth.gateway' },
    gateway_kind: {
      label: 'sensorLogs.wlanAuth.gatewayKind',
      values: {
        gateway: 'sensorLogs.wlanAuth.values.gatewayKind.gateway',
        router: 'sensorLogs.wlanAuth.values.gatewayKind.router',
        'dhcp server': 'sensorLogs.wlanAuth.values.gatewayKind.dhcpServer',
      },
    },
    frequency_mhz: { label: 'sensorLogs.wlanAuth.frequency', unit: 'mhz' },
    signal_dbm: { label: 'sensorLogs.wlanAuth.signal', unit: 'dbm' },
    timings: { label: 'sensorLogs.wlanAuth.timings' },
    assoc_ms: { label: 'sensorLogs.wlanAuth.assocMs', unit: 'ms' },
    auth_ms: { label: 'sensorLogs.wlanAuth.authMs', unit: 'ms' },
    dhcp_ms: { label: 'sensorLogs.wlanAuth.dhcpMs', unit: 'ms' },
    gateway_ms: { label: 'sensorLogs.wlanAuth.gatewayMs', unit: 'ms' },
    total_ms: { label: 'sensorLogs.wlanAuth.totalMs', unit: 'ms' },
    supplicant: { label: 'sensorLogs.wlanAuth.supplicant' },
    dhcp: { label: 'sensorLogs.wlanAuth.dhcp' },
    events: { label: 'sensorLogs.wlanAuth.events' },
  },
}

/**
 * Words for the result codes of the shipped sensors, from their READMEs.
 * The raw code stays beside the words: it is what the sensor's own channel
 * and documentation use, and what a colleague will search for.
 */
const CODES: Record<string, Record<string, string>> = {
  'wlan-auth': {
    'ok': 'sensorLogs.wlanAuth.codes.ok',
    'assoc-timeout': 'sensorLogs.wlanAuth.codes.assocTimeout',
    'auth-failed-psk': 'sensorLogs.wlanAuth.codes.authFailedPsk',
    'auth-failed-eap': 'sensorLogs.wlanAuth.codes.authFailedEap',
    'server-cert-rejected': 'sensorLogs.wlanAuth.codes.serverCertRejected',
    'auth-rejected': 'sensorLogs.wlanAuth.codes.authRejected',
    'assoc-rejected': 'sensorLogs.wlanAuth.codes.assocRejected',
    'ssid-not-found': 'sensorLogs.wlanAuth.codes.ssidNotFound',
    'dhcp-timeout': 'sensorLogs.wlanAuth.codes.dhcpTimeout',
    'auth-timeout': 'sensorLogs.wlanAuth.codes.authTimeout',
    'assoc-lost': 'sensorLogs.wlanAuth.codes.assocLost',
    'not-measured': 'sensorLogs.wlanAuth.codes.notMeasured',
    'result-stale': 'sensorLogs.wlanAuth.codes.resultStale',
    'max-age-too-low': 'sensorLogs.wlanAuth.codes.maxAgeTooLow',
    'gateway-unreachable': 'sensorLogs.wlanAuth.codes.gatewayUnreachable',
    'iface-missing': 'sensorLogs.wlanAuth.codes.ifaceMissing',
    'iface-down': 'sensorLogs.wlanAuth.codes.ifaceDown',
    'iface-busy': 'sensorLogs.wlanAuth.codes.ifaceBusy',
    'iface-blocked': 'sensorLogs.wlanAuth.codes.ifaceBlocked',
    'iface-not-wireless': 'sensorLogs.wlanAuth.codes.ifaceNotWireless',
    'iface-not-reserved': 'sensorLogs.wlanAuth.codes.ifaceNotReserved',
    'iface-protected': 'sensorLogs.wlanAuth.codes.ifaceProtected',
    'mac-not-settable': 'sensorLogs.wlanAuth.codes.macNotSettable',
    'dhcp-client-broken': 'sensorLogs.wlanAuth.codes.dhcpClientBroken',
    'profile-missing': 'sensorLogs.wlanAuth.codes.profileMissing',
    'file-missing': 'sensorLogs.wlanAuth.codes.fileMissing',
    'tool-missing': 'sensorLogs.wlanAuth.codes.toolMissing',
    'ping-missing': 'sensorLogs.wlanAuth.codes.pingMissing',
    'no-privileges': 'sensorLogs.wlanAuth.codes.noPrivileges',
    'too-many-jobs': 'sensorLogs.wlanAuth.codes.tooManyJobs',
    'bad-request': 'sensorLogs.wlanAuth.codes.badRequest',
    'internal-error': 'sensorLogs.wlanAuth.codes.internalError',
  },
}

/** The words for a run's result code, or null where there are none. */
export function useCodeLabel(): (sensor: string, code: string | null | undefined) => string | null {
  const { t } = useTranslation()
  return (sensor, code) => {
    const key = code ? CODES[sensor]?.[code] : undefined
    return key ? t(key) : null
  }
}

/** How a field is shown: its words, if known, and its value with its unit. */
interface Field {
  /** Null for a key this page has no words for. */
  label: string | null
  value: (value: unknown) => string
  /** True where the value is shown as words rather than as sent. */
  worded: (value: unknown) => boolean
}

function useFields(sensor: string | undefined): (key: string) => Field {
  const { t } = useTranslation()
  const fields = (sensor && FIELDS[sensor]) || {}
  return (key) => {
    const info = fields[key] as FieldInfo | undefined
    return {
      label: info ? t(info.label) : null,
      value: (value) => {
        const word = typeof value === 'string' ? info?.values?.[value] : undefined
        return word ? t(word) : withUnit(value, info?.unit)
      },
      worded: (value) => typeof value === 'string' && !!info?.values?.[value],
    }
  }
}

/** A field's heading: its words, or the raw key when there are none. */
function useLabel(sensor: string | undefined): (key: string) => string {
  const field = useFields(sensor)
  return (key) => field(key).label ?? key
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

function isEmptyBlock(value: unknown): boolean {
  if (Array.isArray(value)) return value.length === 0
  if (isGroup(value)) return Object.keys(value).length === 0
  return typeof value === 'string' && value.includes('\n') && value.trim() === ''
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number') return formatNumber(value)
  return JSON.stringify(value)
}

/** A stage under a second in milliseconds, anything longer in seconds. */
function withUnit(value: unknown, unit: Unit | undefined): string {
  if (typeof value !== 'number' || !unit) return show(value)
  if (unit === 'mhz') return `${formatNumber(value)} MHz`
  if (unit === 'dbm') return `${formatNumber(value)} dBm`
  if (value < 1000) return `${formatNumber(value)} ms`
  return `${formatNumber(Math.round(value / 100) / 10)} s`
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
  const field = useFields(sensor)
  const label = useLabel(sensor)
  // An empty transcript or an empty group is a heading over nothing - a run
  // that stopped before DHCP has no DHCP output to show.
  const entries = Object.entries(body).filter(([, value]) => !isEmptyBlock(value))
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
      {scalars.length > 0 && <Values entries={scalars} field={field} />}
      {groups.map(([key, value]) => (
        <section key={key} className="space-y-1">
          <h3 className="label-mono">{label(key)}</h3>
          <Values entries={Object.entries(value)} field={field} />
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

/**
 * Values with words first, then the keys this page has no words for.
 *
 * An unknown key stays as the sensor sent it - but after the named fields
 * and quieter, so the readable part of the record is what the eye meets.
 */
function Values({
  entries,
  field,
}: {
  entries: [string, unknown][]
  field: (key: string) => Field
}) {
  const known = entries.filter(([key]) => field(key).label !== null)
  const unknown = entries.filter(([key]) => field(key).label === null)
  return (
    <dl className="grid grid-cols-[minmax(8rem,14rem)_1fr] gap-x-3 gap-y-1">
      {known.map(([key, value]) => (
        <Fragment key={key}>
          <dt className="text-ink-2 text-xs">{field(key).label}</dt>
          {/* A sentence in mono reads like a machine value; only what the
              sensor sent as it is stays in the code face. */}
          <dd
            className={
              field(key).worded(value)
                ? 'text-ink min-w-0 text-sm'
                : 'text-ink min-w-0 font-mono text-xs break-all'
            }
          >
            {field(key).value(value)}
          </dd>
        </Fragment>
      ))}
      {unknown.map(([key, value]) => (
        <Fragment key={key}>
          <dt className="text-ink-3 font-mono text-xs">{key}</dt>
          <dd className="text-ink-2 min-w-0 font-mono text-xs break-all">{show(value)}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

/**
 * The network a run was for: the summary's, or else the one in its body.
 *
 * Older records carry the SSID only in the body, and a header that says
 * "network —" above a body that names it reads like a fault.
 */
export function networkOf(run: { ssid?: string | null; body?: Record<string, unknown> }) {
  if (run.ssid) return run.ssid
  const fromBody = run.body?.ssid
  return typeof fromBody === 'string' && fromBody ? fromBody : null
}
