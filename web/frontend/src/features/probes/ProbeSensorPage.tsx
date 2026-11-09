import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { ChevronDown, ChevronRight, Trash2, Upload } from 'lucide-react'

import {
  TIMELINE_WINDOWS,
  fetchRunAt,
  useHideSensorStream,
  useProbe,
  useProbePrtgName,
  useRemoveSensorFromProbe,
  useForgetSensorJob,
  useSensorJobs,
  useSensorLogTimeline,
  useSensorLogs,
  type TimelineWindow,
} from '@/api/hooks'
import type {
  SensorJob,
  SensorLogSummary,
  SensorTimelineBucket,
  SensorTimelineStream,
} from '@/api/types'
import { PermissionGate, useAuth } from '@/app/providers'
import { Breadcrumb } from '@/components/ui/Breadcrumb'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { useUrlDetails } from '@/components/ui/UrlDetails'
import {
  ActionMenu,
  Badge,
  Button,
  Card,
  CheckboxField,
  ConfirmDialog,
  DetailRow,
  EmptyState,
  FilterChip,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { SensorStatusBadge } from '@/components/ui/status'
import { DeployDialog } from '@/features/deployments/DeployDialog'
import { probeName } from '@/features/probes/probeName'
import { RUN_PARAM, RunViewer } from '@/features/sensorLogs/RunViewer'
import { SensorTimelineRows, streamLabel } from '@/features/sensorLogs/SensorTimeline'
import { useCodeLabel } from '@/features/sensorLogs/RunList'
import { sensorLogHref } from '@/features/sensorLogs/links'
import { formatDateTime, formatDuration, formatSpanEnd } from '@/utils/format'
import { useUrlChoice, useUrlState } from '@/utils/urlState'

/**
 * One sensor on one probe: how it is deployed and how its runs went.
 *
 * The probe page used to list the runs of every sensor below its sensor
 * table, where they read as if they belonged to the table. Here they belong
 * to the sensor they came from.
 */
export function ProbeSensorPage() {
  const { t } = useTranslation()
  const { probeId = '', sensorName = '' } = useParams<{
    probeId: string
    sensorName: string
  }>()
  const { data, isLoading, error, refetch } = useProbe(probeId)
  const { data: prtgName } = useProbePrtgName(probeId)
  const { can } = useAuth()
  const navigate = useNavigate()
  const removeSensor = useRemoveSensorFromProbe()
  const [dialog, setDialog] = useState<'deploy' | 'remove' | null>(null)

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const probe = probeName(
    data.summary,
    prtgName?.state === 'ok' ? prtgName.name_in_prtg : null,
  )
  const sensor = data.sensors.find((entry) => entry.name === sensorName)
  const present = sensor !== undefined && sensor.status !== 'absent'
  const stale = sensor?.status === 'outdated' || sensor?.status === 'drifted'

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <Breadcrumb
            trail={[
              { to: '/probes', label: t('probes.title') },
              { to: `/probes/${probeId}?tab=sensors`, label: probe },
            ]}
          />
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-lg">{sensorName}</h1>
            {sensor && <SensorStatusBadge status={sensor.status} />}
          </div>
          <p className="text-ink-2 text-sm">{t('probeSensor.onProbe', { probe })}</p>
        </div>
        {present && (
          <span className="flex flex-wrap gap-2">
            {stale && (
              <PermissionGate permission="deployment.create">
                <Button size="sm" icon={Upload} onClick={() => setDialog('deploy')}>
                  {t('sensors.updateOnProbe')}
                </Button>
              </PermissionGate>
            )}
            <ActionMenu
              label={t('common.moreActions')}
              variant="secondary"
              actions={
                can('sensor.remove')
                  ? [
                      {
                        key: 'remove',
                        label: t('sensors.remove'),
                        icon: Trash2,
                        tone: 'danger',
                        disabled: removeSensor.isPending,
                        onSelect: () => setDialog('remove'),
                      },
                    ]
                  : []
              }
            />
          </span>
        )}
      </header>

      {sensor ? (
        <Card title={t('probeSensor.deployment')}>
          <dl>
            <DetailRow label={t('probeSensor.version')}>
              <Mono>
                {t('probeSensor.versions', {
                  installed: sensor.installed_version ?? '—',
                  desired: sensor.desired_version ?? '—',
                })}
              </Mono>
            </DetailRow>
            {sensor.interfaces.length > 0 && (
              <DetailRow label={t('probes.interfaces.columns.name')}>
                <Mono>{sensor.interfaces.join(', ')}</Mono>
              </DetailRow>
            )}
            {sensor.helper_state !== null && sensor.helper_state !== 'none' && (
              <DetailRow label={t('probes.sensorHelper')}>
                {sensor.helper_state === 'listening' ? (
                  <Badge tone="ok">{t('sensors.helperState.listening')}</Badge>
                ) : (
                  <Badge tone="danger">{t('sensors.helperState.inactive')}</Badge>
                )}
              </DetailRow>
            )}
            {sensor.tool_name !== null && (
              <DetailRow label={t('probes.sensorTool.column')}>
                <span className="text-ink">{sensor.tool_name}</span>{' '}
                <Mono className="text-ink-3">{sensor.installed_tool_version ?? '—'}</Mono>
              </DetailRow>
            )}
          </dl>
        </Card>
      ) : (
        <Card>
          <EmptyState title={t('probeSensor.notOnProbe')} />
        </Card>
      )}

      <PermissionGate permission="sensor_log.read">
        <RunsCard probeId={probeId} probe={data.summary.nats_username} sensor={sensorName} />
      </PermissionGate>

      {present && SCHEDULING_SENSORS.includes(sensorName) && (
        <ConfigurationsCard probeId={probeId} sensor={sensorName} />
      )}

      {dialog === 'deploy' && (
        <DeployDialog
          sensorName={sensorName}
          probeIds={[probeId]}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'remove' && (
        <ConfirmDialog
          title={t('probes.removeSensorTitle', { sensor: sensorName })}
          body={t('probes.removeSensorConfirm', { sensor: sensorName })}
          confirmLabel={t('sensors.remove')}
          cancelLabel={t('common.cancel')}
          pending={removeSensor.isPending}
          error={removeSensor.error ? <ErrorDetails error={removeSensor.error} /> : undefined}
          onConfirm={() =>
            removeSensor.mutate(
              { probeId, sensor: sensorName },
              {
                onSuccess: (accepted) => {
                  setDialog(null)
                  navigate(`/jobs/${accepted.job_id}`)
                },
              },
            )
          }
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  )
}

const WINDOWS = Object.keys(TIMELINE_WINDOWS) as TimelineWindow[]

function RunsCard({ probeId, probe, sensor }: { probeId: string; probe: string; sensor: string }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const navigate = useNavigate()
  const { update } = useUrlState()
  // In the address, so a link opens the same window.
  const [window, choose] = useUrlChoice<TimelineWindow>('window', WINDOWS, '24h')
  const { data, isLoading, error } = useSensorLogTimeline(probe, sensor, window)
  const [hiding, setHiding] = useState<SensorTimelineStream | null>(null)

  async function openRun(stream: SensorTimelineStream, bucket: SensorTimelineBucket) {
    const start = Date.parse(bucket.start)
    const since = new Date(start).toISOString()
    const until = new Date(start + (data?.bucket_seconds ?? 900) * 1000).toISOString()
    try {
      const run = await fetchRunAt({ probe, sensor: stream.sensor, stream: stream.stream, since, until })
      // Pushed, unlike the steps inside the viewer: Back closes it again.
      update({ [RUN_PARAM]: run.id }, { push: true })
    } catch {
      // Nothing to open in the viewer: the log still shows the slice.
      navigate(
        sensorLogHref({
          probe,
          sensor: stream.sensor,
          ssid: stream.ssid ?? undefined,
          variant: stream.variant ?? undefined,
          since,
          until,
        }),
      )
    }
  }

  return (
    <Card
      title={window === '7d' ? t('probeSensor.timelineTitle7d') : t('probeSensor.timelineTitle')}
      action={
        <span className="flex items-center gap-3">
          <span className="flex gap-1" role="group" aria-label={t('sensorLogs.filters.range')}>
            {WINDOWS.map((entry) => (
              <FilterChip key={entry} pressed={entry === window} onClick={() => choose(entry)}>
                {t(`probeSensor.window.${entry}`)}
              </FilterChip>
            ))}
          </span>
          <Link
            to={sensorLogHref({ probe, sensor, range: TIMELINE_WINDOWS[window].range })}
            className="text-accent hidden text-xs hover:underline sm:inline"
          >
            {t('sensorLogs.all')}
          </Link>
        </span>
      }
    >
      {error ? (
        <ErrorDetails error={error} />
      ) : isLoading ? (
        <Skeleton className="h-24" />
      ) : !data || data.streams.length === 0 ? (
        <EmptyState title={t('sensorLogs.empty')} />
      ) : (
        <div className="space-y-4">
          <SensorTimelineRows
            timeline={data}
            onOpen={(stream, bucket) => void openRun(stream, bucket)}
            onHide={can('sensor.configure') ? setHiding : undefined}
          />
          <FailureList
            probe={probe}
            sensor={sensor}
            window={window}
            rows={data.streams.map(rowOf)}
          />
        </div>
      )}
      <RunViewer />
      {hiding && (
        <HideRowDialog
          probeId={probeId}
          probe={probe}
          stream={hiding}
          onClose={() => setHiding(null)}
        />
      )}
    </Card>
  )
}

/**
 * Takes a row off the timeline until its next run. Where the configuration
 * behind it is still on the probe, removing it there is offered with it and
 * preselected: a network configured by mistake is usually still measured.
 */
function HideRowDialog({
  probeId,
  probe,
  stream,
  onClose,
}: {
  probeId: string
  probe: string
  stream: SensorTimelineStream
  onClose: () => void
}) {
  const { t } = useTranslation()
  const scheduling = SCHEDULING_SENSORS.includes(stream.sensor)
  const jobs = useSensorJobs(probeId, stream.sensor, scheduling && stream.last_job !== null)
  const job = jobs.data?.find((entry) => entry.label === stream.last_job)
  const [removeToo, setRemoveToo] = useState(true)
  const hide = useHideSensorStream()
  const forget = useForgetSensorJob()
  const label = streamLabel(stream)
  const failure = hide.error ?? forget.error

  async function confirm() {
    try {
      await hide.mutateAsync({ probe, sensor: stream.sensor, stream: stream.stream })
      if (job && removeToo) {
        await forget.mutateAsync({ id: probeId, sensor: stream.sensor, label: job.label })
      }
      onClose()
    } catch {
      // Shown in the dialog from the mutation's error.
    }
  }

  return (
    <ConfirmDialog
      title={t('probeSensor.hide.title', { network: label })}
      tone="primary"
      body={
        <div className="space-y-3">
          <p className="text-ink-2 text-sm">{t('probeSensor.hide.body')}</p>
          {jobs.isLoading && <Skeleton className="h-10" />}
          {job && (
            <CheckboxField
              label={t('probeSensor.hide.removeConfiguration')}
              hint={t('probeSensor.hide.removeConfigurationHint')}
              checked={removeToo}
              onChange={setRemoveToo}
            />
          )}
        </div>
      }
      confirmLabel={t('probeSensor.hide.confirm')}
      cancelLabel={t('common.cancel')}
      pending={hide.isPending || forget.isPending}
      error={failure ? <ErrorDetails error={failure} /> : undefined}
      onConfirm={() => void confirm()}
      onClose={onClose}
    />
  )
}

/** The timeline row a run or a stream belongs to: its network and variant. */
function rowOf(item: { ssid: string | null; variant?: string | null }): string {
  return `${item.ssid ?? ''}|${item.variant ?? ''}`
}

interface FailureGroup {
  ssid: string | null
  code: string | null
  summary: string
  count: number
  first: string
  last: string
}

/** Failures of one stream with the same code, folded into one line. */
export function foldFailures(runs: SensorLogSummary[]): FailureGroup[] {
  const groups = new Map<string, FailureGroup>()
  for (const run of runs) {
    const key = `${run.ssid ?? ''}|${run.code ?? ''}`
    const group = groups.get(key)
    if (!group) {
      groups.set(key, {
        ssid: run.ssid,
        code: run.code,
        summary: run.summary,
        count: 1,
        first: run.time,
        last: run.time,
      })
      continue
    }
    group.count += 1
    if (run.time < group.first) group.first = run.time
    if (run.time > group.last) {
      group.last = run.time
      group.summary = run.summary
    }
  }
  return [...groups.values()].sort((left, right) => right.last.localeCompare(left.last))
}

// The API's page limit. A sensor failing every minute for a week has more,
// which the line says instead of folding a part as if it were the whole.
const FAILURE_LIMIT = 500

function FailureList({
  probe,
  sensor,
  window,
  rows,
}: {
  probe: string
  sensor: string
  window: TimelineWindow
  /** The rows shown, as rowOf spells them; a hidden row's failures go with it. */
  rows: string[]
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useUrlDetails('failures')
  const range = TIMELINE_WINDOWS[window].range
  const { data } = useSensorLogs({ probe, sensor, result: 'error', range }, FAILURE_LIMIT)
  if (!data) return null
  const items = data.items.filter((run) => rows.includes(rowOf(run)))
  const total = data.total - (data.items.length - items.length)
  if (total === 0) return null
  const groups = foldFailures(items)
  return (
    <section className="border-rule border-t pt-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="text-ink flex items-center gap-1.5 text-sm font-medium"
      >
        {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        {t('probeSensor.failures', { count: total })}
      </button>
      {open && (
        <ul className="mt-2 space-y-1">
          {groups.map((group) => (
            <li key={`${group.ssid ?? ''}|${group.code ?? ''}`}>
              <Link
                to={sensorLogHref({
                  probe,
                  sensor,
                  ssid: group.ssid ?? undefined,
                  result: 'error',
                  q: group.code ?? undefined,
                  since: group.first,
                  until: group.last,
                })}
                className="hover:bg-surface-2 -mx-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded px-2 py-1 text-xs"
              >
                <span className="text-danger font-medium tabular-nums">
                  {t('probeSensor.failureCount', { count: group.count })}
                </span>
                <span className="text-ink">{group.summary || group.code}</span>
                {group.ssid && <Mono className="text-ink-3">{group.ssid}</Mono>}
                <span className="text-ink-3 ml-auto tabular-nums">
                  {group.count === 1
                    ? formatDateTime(group.last)
                    : `${formatDateTime(group.first)} – ${formatSpanEnd(group.first, group.last)}`}
                </span>
              </Link>
            </li>
          ))}
          {total > items.length && (
            <li className="text-ink-3 text-xs">
              {t('probeSensor.failuresMore', { count: total - items.length })}
            </li>
          )}
        </ul>
      )}
    </section>
  )
}

// One template for the header and every row, so the columns line up.
// Below lg the card is too narrow for seven columns beside the sidebar, so a
// row falls back to label/value pairs there. The last column has a width of
// its own: it holds the remove button in a row and nothing in the header, and
// an auto column would share out the rest differently in each.
const JOB_COLUMNS =
  'lg:grid-cols-[minmax(8rem,1.3fr)_minmax(7rem,1fr)_5.5rem_6.5rem_7rem_minmax(10rem,1.5fr)_7rem] lg:items-center lg:gap-x-4'

// What a missing value shows, so a row keeps its shape.
const MISSING = '–'

const METHOD_KEYS: Record<string, string> = {
  psk: 'probeSensor.configurations.methods.psk',
  peap: 'probeSensor.configurations.methods.peap',
  'eap-tls': 'probeSensor.configurations.methods.eapTls',
}

/** One configuration as a row of labelled values. */
export function ConfigurationRow({
  job,
  sensor,
  removing,
  onRemove,
}: {
  job: SensorJob
  sensor: string
  removing: boolean
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const method = job.auth ? (METHOD_KEYS[job.auth] ? t(METHOD_KEYS[job.auth]) : job.auth) : null
  return (
    <li className="border-rule border-b px-4 py-2.5 text-sm last:border-0">
      <dl className={clsx(JOB_COLUMNS, 'grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 lg:gap-y-0')}>
        <Cell label={t('probeSensor.configurations.columns.ssid')}>
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-ink truncate font-mono text-xs font-medium">
              {job.ssid ?? MISSING}
            </span>
            {job.idle ? (
              <Badge tone="neutral">{t('probeSensor.configurations.idle')}</Badge>
            ) : (
              <Badge tone="ok">{t('probeSensor.configurations.active')}</Badge>
            )}
          </span>
          {(job.bssid || job.sensor_id) && (
            <span className="text-ink-3 block text-xs">
              {[
                job.bssid && t('probeSensor.configurations.bssid', { bssid: job.bssid }),
                job.sensor_id && t('probeSensor.configurations.sensorId', { id: job.sensor_id }),
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
          )}
        </Cell>
        <Cell label={t('probeSensor.configurations.columns.variant')}>
          <span className="font-mono text-xs">{job.profile || MISSING}</span>
        </Cell>
        <Cell label={t('probeSensor.configurations.columns.method')}>{method ?? MISSING}</Cell>
        <Cell label={t('probeSensor.configurations.columns.interval')}>
          <span className="tabular-nums">
            {job.asked_every === null ? MISSING : formatDuration(job.asked_every)}
          </span>
        </Cell>
        <Cell label={t('probeSensor.configurations.columns.askedAgo')}>
          <span className="tabular-nums">
            {job.asked_ago === null
              ? MISSING
              : t('probeSensor.configurations.ago', { ago: formatDuration(job.asked_ago) })}
          </span>
        </Cell>
        <Cell label={t('probeSensor.configurations.columns.lastMeasurement')}>
          <LastMeasurement job={job} sensor={sensor} />
        </Cell>
        <PermissionGate permission="sensor.configure">
          <div className="col-span-2 flex justify-end lg:col-span-1">
            <dt className="sr-only">{t('probeSensor.configurations.columns.actions')}</dt>
            <dd>
              <Button size="sm" variant="ghost" icon={Trash2} disabled={removing} onClick={onRemove}>
                {t('probeSensor.configurations.remove')}
              </Button>
            </dd>
          </div>
        </PermissionGate>
      </dl>
    </li>
  )
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents lg:block lg:min-w-0">
      <dt className="text-ink-3 text-xs lg:sr-only">{label}</dt>
      <dd className="text-ink-2 min-w-0 text-xs">{children}</dd>
    </div>
  )
}

/** The result of the last run as a state, with its reason when it failed. */
function LastMeasurement({ job, sensor }: { job: SensorJob; sensor: string }) {
  const { t } = useTranslation()
  const codeLabel = useCodeLabel()
  if (!job.result || job.result === 'pending' || job.age === null) {
    return <span>{MISSING}</span>
  }
  const ok = job.result === 'ok'
  const reason = ok ? null : (codeLabel(sensor, job.code) ?? job.code)
  return (
    <span className="block min-w-0">
      <span className="flex items-center gap-x-2">
        <Badge tone={ok ? 'ok' : 'danger'}>
          {t(ok ? 'probeSensor.configurations.ok' : 'probeSensor.configurations.failed')}
        </Badge>
        <span className="text-ink-3 whitespace-nowrap tabular-nums">
          {t('probeSensor.configurations.ago', { ago: formatDuration(job.age) })}
        </span>
      </span>
      {reason && (
        <span className="text-danger mt-0.5 block truncate" title={reason}>
          {reason}
        </span>
      )}
    </span>
  )
}

// Sensors whose privileged helper plans its own measurements and can say
// which configurations it holds.
const SCHEDULING_SENSORS = ['wlan-auth']

/**
 * What the sensor's helper on the probe measures right now - one entry per
 * configuration a sensor in PRTG asked for. A changed parameter makes a new
 * one; the old one goes idle after three intervals unasked and can be
 * removed here at once instead of waiting for it to expire.
 */
function ConfigurationsCard({ probeId, sensor }: { probeId: string; sensor: string }) {
  const { t } = useTranslation()
  const { data, isLoading, error } = useSensorJobs(probeId, sensor)
  const forget = useForgetSensorJob()
  const [removing, setRemoving] = useState<SensorJob | null>(null)
  return (
    <Card title={t('probeSensor.configurations.title')} dense>
      {error ? (
        <div className="p-3">
          <ErrorDetails error={error} />
        </div>
      ) : isLoading ? (
        <Skeleton className="m-3 h-16" />
      ) : !data || data.length === 0 ? (
        <EmptyState title={t('probeSensor.configurations.empty')} />
      ) : (
        <div>
          {/* The header names the columns once; on a narrow screen each row
              carries its own labels instead, because the columns stack. */}
          <div
            aria-hidden
            className={clsx(
              JOB_COLUMNS,
              'border-rule text-ink-3 hidden border-b px-4 py-2 text-xs font-medium lg:grid',
            )}
          >
            <span>{t('probeSensor.configurations.columns.ssid')}</span>
            <span>{t('probeSensor.configurations.columns.variant')}</span>
            <span>{t('probeSensor.configurations.columns.method')}</span>
            <span>{t('probeSensor.configurations.columns.interval')}</span>
            <span>{t('probeSensor.configurations.columns.askedAgo')}</span>
            <span>{t('probeSensor.configurations.columns.lastMeasurement')}</span>
            <span />
          </div>
          <ul>
            {data.map((job) => (
              <ConfigurationRow
                key={job.label}
                job={job}
                sensor={sensor}
                removing={forget.isPending}
                onRemove={() => setRemoving(job)}
              />
            ))}
          </ul>
        </div>
      )}
      {removing && (
        <ConfirmDialog
          title={t('probeSensor.configurations.removeTitle', { ssid: removing.ssid ?? '—' })}
          body={t('probeSensor.configurations.removeBody')}
          confirmLabel={t('probeSensor.configurations.remove')}
          cancelLabel={t('common.cancel')}
          pending={forget.isPending}
          error={forget.error ? <ErrorDetails error={forget.error} /> : undefined}
          onConfirm={() =>
            forget.mutate(
              { id: probeId, sensor, label: removing.label },
              { onSuccess: () => setRemoving(null) },
            )
          }
          onClose={() => setRemoving(null)}
        />
      )}
    </Card>
  )
}
