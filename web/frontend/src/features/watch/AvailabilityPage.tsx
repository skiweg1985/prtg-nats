import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useWatchOutages, useWatchOverview } from '@/api/hooks'
import type { WatchDevice, WatchState } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Button,
  Card,
  Dot,
  EmptyState,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { formatDuration, formatRelative } from '@/utils/format'

import { DeviceDialog } from './DeviceDialog'

/**
 * The availability dashboard.
 *
 * Written for the person who is not an administrator: a shop manager wanting
 * to know whether the till printer is on, a technician before driving out.
 * Hence a grid of tiles rather than a table, and a label filter that lives in
 * the URL so a site can bookmark its own devices.
 */

const STATE_TONE: Record<WatchState, 'ok' | 'danger' | 'neutral'> = {
  up: 'ok',
  down: 'danger',
  unknown: 'neutral',
}

/** A stale row is unknown, whatever it last said. */
function effectiveState(device: WatchDevice): WatchState {
  return device.stale ? 'unknown' : device.state
}

export function AvailabilityPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const [editing, setEditing] = useState<WatchDevice | 'new' | null>(null)

  const labels = useMemo(() => params.getAll('label'), [params])
  const { data, isLoading, error, refetch } = useWatchOverview(labels)

  function toggleLabel(pair: string) {
    const next = new URLSearchParams(params)
    const current = next.getAll('label')
    next.delete('label')
    // One value per key: picking "site:berlin" while "site:hamburg" is on
    // means switching sites, not asking for devices in both - which no
    // device could satisfy anyway.
    const key = pair.split(':')[0]
    const kept = current.filter(
      (entry) => entry !== pair && entry.split(':')[0] !== key,
    )
    for (const entry of kept) next.append('label', entry)
    if (!current.includes(pair)) next.append('label', pair)
    setParams(next, { replace: true })
  }

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-ink text-xl font-semibold">{t('watch.title')}</h1>
          <p className="text-ink-2 mt-1 text-sm">{t('watch.subtitle')}</p>
        </div>
        <PermissionGate permission="watch.manage">
          <Button variant="primary" onClick={() => setEditing('new')}>
            {t('watch.addDevice')}
          </Button>
        </PermissionGate>
      </header>

      {data && !data.receiving && (
        <Card>
          {/* A wall of unknown devices has two causes. This is the one an
              operator can act on, so it is said outright rather than left
              to be inferred from every tile at once. */}
          <p className="text-warn text-sm">{t('watch.notReceiving')}</p>
        </Card>
      )}

      <div className="grid grid-cols-3 gap-3">
        <Counter label={t('watch.counts.up')} value={data?.up} tone="ok" />
        <Counter label={t('watch.counts.down')} value={data?.down} tone="danger" />
        <Counter
          label={t('watch.counts.unknown')}
          value={data?.unknown}
          tone="neutral"
        />
      </div>

      {data && Object.keys(data.labels).length > 0 && (
        <div className="space-y-2">
          {Object.entries(data.labels).map(([key, values]) => (
            <div key={key} className="flex flex-wrap items-center gap-1.5">
              <span className="label-mono text-ink-3 mr-1">{key}</span>
              {values.map((value) => {
                const pair = `${key}:${value}`
                const active = labels.includes(pair)
                return (
                  <button
                    key={pair}
                    type="button"
                    onClick={() => toggleLabel(pair)}
                    className={
                      active
                        ? 'rounded-control bg-accent px-2 py-0.5 text-xs text-white'
                        : 'rounded-control border-rule-2 text-ink-2 hover:text-ink border px-2 py-0.5 text-xs'
                    }
                    aria-pressed={active}
                  >
                    {value}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}

      {isLoading && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((index) => (
            <Skeleton key={index} className="h-20" />
          ))}
        </div>
      )}

      {data && data.devices.length === 0 && (
        <EmptyState
          title={t(labels.length ? 'watch.empty.filtered' : 'watch.empty.title')}
          hint={t(labels.length ? 'watch.empty.filteredHint' : 'watch.empty.hint')}
        />
      )}

      {data && data.devices.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[...data.devices]
            // What is broken comes first: on a wall display nobody scrolls,
            // and a page of green tiles must not hide the one red one.
            .sort((left, right) => order(left) - order(right))
            .map((device) => (
              <DeviceTile
                key={device.id}
                device={device}
                onEdit={() => setEditing(device)}
              />
            ))}
        </div>
      )}

      <OutageList labels={labels} />

      {editing && (
        <DeviceDialog
          device={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

function order(device: WatchDevice): number {
  const state = effectiveState(device)
  if (!device.enabled) return 3
  return state === 'down' ? 0 : state === 'unknown' ? 1 : 2
}

function Counter({
  label,
  value,
  tone,
}: {
  label: string
  value: number | undefined
  tone: 'ok' | 'danger' | 'neutral'
}) {
  return (
    <Card>
      <div className="flex items-center gap-2">
        <Dot tone={tone} />
        <span className="text-ink-2 text-sm">{label}</span>
      </div>
      <p className="text-ink mt-1 text-2xl font-semibold tabular-nums">
        {value ?? '–'}
      </p>
    </Card>
  )
}

function DeviceTile({
  device,
  onEdit,
}: {
  device: WatchDevice
  onEdit: () => void
}) {
  const { t } = useTranslation()
  const state = effectiveState(device)

  return (
    <Card>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Dot tone={STATE_TONE[state]} />
            <p className="text-ink truncate font-medium">{device.display_name}</p>
          </div>
          <Mono truncate className="text-ink-3 mt-0.5 block text-xs">
            {device.address}
            {device.method === 'tcp' && device.port ? `:${device.port}` : ''}
          </Mono>
        </div>
        <PermissionGate permission="watch.manage">
          <Button size="sm" onClick={onEdit}>
            {t('common.edit')}
          </Button>
        </PermissionGate>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Badge tone={STATE_TONE[state]}>{t(`watch.state.${state}`)}</Badge>
        {!device.enabled && <Badge tone="neutral">{t('watch.paused')}</Badge>}
        {Object.entries(device.labels).map(([key, value]) => (
          <Badge key={key} tone="neutral">
            {key}: {value}
          </Badge>
        ))}
      </div>

      <p className="text-ink-3 mt-2 text-xs">
        {device.observed_at
          ? t('watch.lastSeen', {
              when: formatRelative(device.observed_at),
              probe: device.probe_name,
            })
          : t('watch.neverMeasured', { probe: device.probe_name })}
        {state === 'up' && device.rtt_ms !== null && ` · ${device.rtt_ms.toFixed(1)} ms`}
      </p>
      {device.error && state !== 'up' && (
        <p className="text-ink-3 mt-1 truncate text-xs">{device.error}</p>
      )}
    </Card>
  )
}

/** What was down, since when, for how long. The list support actually reads. */
function OutageList({ labels }: { labels: string[] }) {
  const { t } = useTranslation()
  const [days, setDays] = useState(7)
  const { data, isLoading } = useWatchOutages(days, labels)

  return (
    <Card
      title={t('watch.outages.title')}
      action={
        <div className="flex gap-1">
          {[1, 7, 30].map((option) => (
            <Button
              key={option}
              size="sm"
              variant={option === days ? 'primary' : 'secondary'}
              onClick={() => setDays(option)}
            >
              {t('watch.outages.days', { count: option })}
            </Button>
          ))}
        </div>
      }
    >
      {isLoading && <Skeleton className="h-16" />}
      {data && data.length === 0 && (
        <p className="text-ink-2 text-sm">{t('watch.outages.none')}</p>
      )}
      {data && data.length > 0 && (
        <ul className="divide-rule divide-y">
          {data.map((outage) => (
            <li
              key={`${outage.device_id}-${outage.started_at}`}
              className="flex flex-wrap items-baseline justify-between gap-2 py-2 text-sm"
            >
              <span className="text-ink font-medium">{outage.device_name}</span>
              <span className="text-ink-2">
                {formatRelative(outage.started_at)}
                {' · '}
                {outage.duration_seconds === null
                  ? t('watch.outages.ongoing')
                  : formatDuration(outage.duration_seconds)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
