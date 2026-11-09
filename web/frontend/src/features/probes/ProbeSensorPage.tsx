import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Trash2, Upload } from 'lucide-react'

import {
  TIMELINE_WINDOWS,
  useProbe,
  useProbePrtgName,
  useRemoveSensorFromProbe,
  useForgetSensorJob,
  useSensorJobs,
  useSensorLogTimeline,
  useSensorLogs,
  type TimelineWindow,
} from '@/api/hooks'
import type { SensorJob, SensorLogSummary } from '@/api/types'
import { PermissionGate, useAuth } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  ActionMenu,
  BackLink,
  Badge,
  Button,
  Card,
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
import { SensorTimelineRows } from '@/features/sensorLogs/SensorTimeline'
import { sensorLogHref } from '@/features/sensorLogs/links'
import { formatDateTime, formatDuration, formatSpanEnd } from '@/utils/format'

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
          <BackLink to={`/probes/${probeId}?tab=sensors`}>{probe}</BackLink>
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
        <RunsCard probe={data.summary.nats_username} sensor={sensorName} />
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

function RunsCard({ probe, sensor }: { probe: string; sensor: string }) {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  // In the address, so a link opens the same window.
  const window: TimelineWindow = params.get('window') === '7d' ? '7d' : '24h'
  const { data, isLoading, error } = useSensorLogTimeline(probe, sensor, window)

  function choose(next: TimelineWindow) {
    const changed = new URLSearchParams(params)
    if (next === '24h') changed.delete('window')
    else changed.set('window', next)
    setParams(changed, { replace: true })
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
          <SensorTimelineRows timeline={data} />
          <FailureList probe={probe} sensor={sensor} window={window} />
        </div>
      )}
    </Card>
  )
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
}: {
  probe: string
  sensor: string
  window: TimelineWindow
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const range = TIMELINE_WINDOWS[window].range
  const { data } = useSensorLogs({ probe, sensor, result: 'error', range }, FAILURE_LIMIT)
  if (!data || data.total === 0) return null
  const groups = foldFailures(data.items)
  return (
    <section className="border-rule border-t pt-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="text-ink flex items-center gap-1.5 text-sm font-medium"
      >
        {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        {t('probeSensor.failures', { count: data.total })}
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
          {data.total > data.items.length && (
            <li className="text-ink-3 text-xs">
              {t('probeSensor.failuresMore', { count: data.total - data.items.length })}
            </li>
          )}
        </ul>
      )}
    </section>
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
        <ul>
          {data.map((job) => (
            <li
              key={job.label}
              className="border-rule flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-2.5 text-sm last:border-0"
            >
              <span className="text-ink min-w-32 font-mono text-xs font-medium">
                {job.ssid ?? '—'}
              </span>
              {job.idle ? (
                <Badge tone="neutral">{t('probeSensor.configurations.idle')}</Badge>
              ) : (
                <Badge tone="ok">{t('probeSensor.configurations.active')}</Badge>
              )}
              <span className="text-ink-3 text-xs tabular-nums">
                {[
                  job.asked_every !== null &&
                    t('probeSensor.configurations.askedEvery', {
                      every: formatDuration(job.asked_every),
                    }),
                  job.asked_ago !== null &&
                    t('probeSensor.configurations.askedAgo', {
                      ago: formatDuration(job.asked_ago),
                    }),
                  job.result &&
                    t('probeSensor.configurations.last', {
                      result: job.code && job.code !== 'ok' ? job.code : job.result,
                      age: job.age === null ? '—' : formatDuration(job.age),
                    }),
                  job.sensor_id && t('probeSensor.configurations.sensorId', { id: job.sensor_id }),
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
              <PermissionGate permission="sensor.configure">
                <span className="ml-auto">
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={Trash2}
                    disabled={forget.isPending}
                    onClick={() => setRemoving(job)}
                  >
                    {t('probeSensor.configurations.remove')}
                  </Button>
                </span>
              </PermissionGate>
            </li>
          ))}
        </ul>
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
