import { useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import type {
  SensorTimeline,
  SensorTimelineBucket,
  SensorTimelineState,
  SensorTimelineStream,
} from '@/api/types'
import { Badge } from '@/components/ui/primitives'
import { formatDateTime, formatSpanEnd } from '@/utils/format'

import { sensorLogHref } from './links'

/**
 * A sensor's runs per network as bars on one shared time axis.
 *
 * Meant to answer "is it fine?" at a glance, so the colours carry the
 * weight: a single failed run between passing ones stays a quiet mark on a
 * green slice, and only a failure that lasted is red. Networks sit on the
 * same axis so an outage that hit all of them at once lines up.
 */

/** How a slice is drawn: its state, or that the retention has removed it. */
export type SliceLook = SensorTimelineState | 'expired'

const LOOK_CLASS: Record<SliceLook, string> = {
  ok: 'bg-ok/70',
  outlier: 'bg-ok/70',
  failing: 'bg-danger',
  none: 'bg-surface-2',
  expired: 'bg-[repeating-linear-gradient(135deg,var(--color-surface-2)_0_3px,transparent_3px_6px)]',
}

/** Whether the records behind a slice are older than the retention keeps. */
function expired(bucket: SensorTimelineBucket, timeline: SensorTimeline): boolean {
  const end = Date.parse(bucket.start) + timeline.bucket_seconds * 1000
  return bucket.state === 'none' && end <= Date.parse(timeline.retained_since)
}

function lookOf(bucket: SensorTimelineBucket, timeline: SensorTimeline): SliceLook {
  return expired(bucket, timeline) ? 'expired' : bucket.state
}

export function SensorTimelineRows({ timeline }: { timeline: SensorTimeline }) {
  const { t } = useTranslation()
  const signIns = timeline.sensor === 'wlan-auth'
  const buckets = timeline.streams[0]?.buckets ?? []
  const anyExpired = buckets.some((bucket) => expired(bucket, timeline))
  const looks: SliceLook[] = ['ok', 'outlier', 'failing', 'none']
  if (anyExpired) looks.push('expired')
  return (
    <div className="space-y-3">
      {/* Every bar spans the full width, so all of them share one axis
          whatever the figures above them need. */}
      {timeline.streams.map((stream) => (
        <TimelineRow
          key={`${stream.sensor}|${stream.ssid ?? ''}`}
          timeline={timeline}
          stream={stream}
          label={stream.ssid ?? stream.sensor}
          signIns={signIns}
        />
      ))}
      <Axis buckets={buckets} bucketSeconds={timeline.bucket_seconds} />
      <ul className="text-ink-3 flex flex-wrap gap-x-4 gap-y-1 pt-1 text-xs">
        {looks.map((look) => (
          <li key={look} className="flex items-center gap-1.5">
            <Slice look={look} className="h-3 w-2 rounded-[2px]" />
            {t(`probeSensor.legend.${look}`)}
          </li>
        ))}
      </ul>
      {anyExpired && (
        <p className="text-ink-3 text-xs">
          {t('probeSensor.retention', { date: formatDateTime(timeline.retained_since) })}
        </p>
      )}
    </div>
  )
}

function TimelineRow({
  timeline,
  stream,
  label,
  signIns,
}: {
  timeline: SensorTimeline
  stream: SensorTimelineStream
  label: string
  signIns: boolean
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  // The slice under the pointer or the keyboard; one tab stop per bar, the
  // arrow keys move along it.
  const [active, setActive] = useState<number | null>(null)
  const count = stream.buckets.length

  function hrefOf(index: number): string | null {
    const bucket = stream.buckets[index]
    if (!bucket || expired(bucket, timeline)) return null
    const start = Date.parse(bucket.start)
    return sensorLogHref({
      probe: timeline.probe,
      sensor: stream.sensor,
      ssid: stream.ssid ?? undefined,
      since: new Date(start).toISOString(),
      until: new Date(start + timeline.bucket_seconds * 1000).toISOString(),
    })
  }

  function open(index: number) {
    const href = hrefOf(index)
    if (href) navigate(href)
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const current = active ?? count - 1
    if (event.key === 'ArrowLeft') setActive(Math.max(0, current - 1))
    else if (event.key === 'ArrowRight') setActive(Math.min(count - 1, current + 1))
    else if (event.key === 'Home') setActive(0)
    else if (event.key === 'End') setActive(count - 1)
    else if (event.key === 'Enter') open(current)
    else return
    event.preventDefault()
  }

  const shown = active !== null ? stream.buckets[active] : undefined
  return (
    <div className={clsx('space-y-1', stream.ended && 'opacity-60')}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="text-ink truncate font-mono text-xs font-medium" title={label}>
            {label}
          </span>
          {/* A configuration PRTG no longer asks for: kept for its history,
              out of the way of the ones that run. */}
          {stream.ended && (
            <Badge tone="neutral">
              {t('probeSensor.ended', { time: formatSpanEnd(new Date().toISOString(), stream.last_run ?? '') })}
            </Badge>
          )}
        </span>
        <Figures stream={stream} signIns={signIns} />
      </div>
      <div className="relative">
        <div
          role="group"
          tabIndex={0}
          aria-label={t('probeSensor.rowLabel', {
            network: label,
            ok: stream.ok,
            error: stream.error,
          })}
          onKeyDown={onKeyDown}
          onFocus={() => setActive((value) => value ?? count - 1)}
          onBlur={() => setActive(null)}
          onMouseLeave={() => setActive(null)}
          className="focus-visible:ring-accent flex h-6 overflow-hidden rounded-[3px] outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
        >
          {stream.buckets.map((bucket, index) => (
            <Slice
              key={bucket.start}
              look={lookOf(bucket, timeline)}
              active={index === active}
              className={clsx('flex-1', hrefOf(index) && 'cursor-pointer')}
              onMouseEnter={() => setActive(index)}
              onClick={() => open(index)}
            />
          ))}
        </div>
        {shown && active !== null && (
          <SliceInfo
            bucket={shown}
            timeline={timeline}
            signIns={signIns}
            position={(active + 0.5) / count}
          />
        )}
      </div>
    </div>
  )
}

/** What the slice under the pointer holds, above the bar. */
function SliceInfo({
  bucket,
  timeline,
  signIns,
  position,
}: {
  bucket: SensorTimelineBucket
  timeline: SensorTimeline
  signIns: boolean
  position: number
}) {
  const { t } = useTranslation()
  const end = new Date(Date.parse(bucket.start) + timeline.bucket_seconds * 1000).toISOString()
  const runs = bucket.ok + bucket.error
  const look = lookOf(bucket, timeline)
  return (
    <div
      role="status"
      className="surface-card pointer-events-none absolute bottom-full z-10 mb-2 w-max max-w-64 px-2.5 py-1.5 text-xs shadow-md"
      style={{
        left: `${position * 100}%`,
        // Kept inside the bar near its ends instead of hanging off the card.
        transform: `translateX(-${Math.min(Math.max(position, 0.1), 0.9) * 100}%)`,
      }}
    >
      <div className="text-ink font-medium tabular-nums">
        {formatDateTime(bucket.start)} – {formatSpanEnd(bucket.start, end)}
      </div>
      <div className="text-ink-2">
        {look === 'expired'
          ? t('probeSensor.legend.expired')
          : runs === 0
            ? look === 'none'
              ? t('probeSensor.legend.none')
              : t('probeSensor.coveredSlice')
            : [
                t(signIns ? 'probeSensor.sliceSignIns' : 'probeSensor.sliceRuns', {
                  count: runs,
                }),
                bucket.error > 0 && t('probeSensor.sliceFailed', { count: bucket.error }),
              ]
                .filter(Boolean)
                .join(' · ')}
      </div>
      {bucket.code && <div className="text-danger font-mono">{bucket.code}</div>}
    </div>
  )
}

function Figures({ stream, signIns }: { stream: SensorTimelineStream; signIns: boolean }) {
  const { t, i18n } = useTranslation()
  const percent = new Intl.NumberFormat(i18n.resolvedLanguage, {
    style: 'percent',
    maximumFractionDigits: 1,
  })
  const failure = stream.last_failure
  return (
    <dl className="text-ink-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs tabular-nums">
      <div className="flex gap-1">
        <dt className="sr-only">{t('probeSensor.successRate')}</dt>
        <dd className="text-ink font-medium">
          {stream.success_rate === null ? '—' : percent.format(stream.success_rate)}
        </dd>
      </div>
      <div className="flex gap-1">
        <dt className="sr-only">
          {t(signIns ? 'probeSensor.signInsPerHour' : 'probeSensor.runsPerHour')}
        </dt>
        <dd>
          {stream.runs_per_hour === null
            ? '—'
            : t(signIns ? 'probeSensor.perHourSignIns' : 'probeSensor.perHourRuns', {
                count: Math.round(stream.runs_per_hour),
              })}
        </dd>
      </div>
      <div className="flex gap-1">
        <dt className="sr-only">{t('probeSensor.lastFailure')}</dt>
        <dd className={failure ? 'text-danger' : 'text-ink-3'}>
          {failure
            ? t('probeSensor.lastFailureAt', {
                time: formatSpanEnd(new Date().toISOString(), failure.time),
                code: failure.code ?? failure.summary,
              })
            : t('probeSensor.noFailure')}
        </dd>
      </div>
    </dl>
  )
}

export function Slice({
  look,
  active,
  className,
  onMouseEnter,
  onClick,
}: {
  look: SliceLook
  active?: boolean
  className?: string
  onMouseEnter?: () => void
  onClick?: () => void
}) {
  return (
    <span
      onMouseEnter={onMouseEnter}
      onClick={onClick}
      className={clsx(
        'relative block',
        LOOK_CLASS[look],
        active && 'brightness-90 dark:brightness-125',
        className,
      )}
    >
      {look === 'outlier' && <span className="bg-danger absolute inset-x-0 bottom-0 h-1/3" />}
      {active && <span className="bg-ink absolute inset-x-0 -top-px h-0.5" />}
    </span>
  )
}

/**
 * Labels under the bars: every six hours over a day, every midnight over a
 * week, at the slices where that moment starts.
 */
function Axis({
  buckets,
  bucketSeconds,
}: {
  buckets: SensorTimelineBucket[]
  bucketSeconds: number
}) {
  const { i18n } = useTranslation()
  if (buckets.length === 0) return null
  const days = buckets.length * bucketSeconds > 2 * 86_400
  const format = new Intl.DateTimeFormat(
    i18n.resolvedLanguage,
    days ? { weekday: 'short', day: '2-digit', month: '2-digit' } : { hour: '2-digit', minute: '2-digit' },
  )
  const marks = buckets.flatMap((bucket, index) => {
    const when = new Date(bucket.start)
    const labelled = days
      ? when.getHours() === 0 && when.getMinutes() === 0
      : when.getMinutes() === 0 && when.getHours() % 6 === 0
    return labelled && index > 0 ? [{ index, label: format.format(when) }] : []
  })
  return (
    <div className="text-ink-3 relative h-4 text-[11px] tabular-nums">
      {marks.map((mark) => (
        <span
          key={mark.index}
          className="absolute -translate-x-1/2 whitespace-nowrap"
          style={{ left: `${(mark.index / buckets.length) * 100}%` }}
        >
          {mark.label}
        </span>
      ))}
    </div>
  )
}

const STATE_RANK: Record<SensorTimelineState, number> = {
  none: 0,
  ok: 1,
  outlier: 2,
  failing: 3,
}

/**
 * One sensor's networks folded into a single bar: each slice as bad as its
 * worst network, the rate over every run. What the probe page's sensor table
 * shows per row; the networks one by one are on the sensor's page.
 */
export function summariseSensor(timeline: SensorTimeline, sensor: string) {
  const all = timeline.streams.filter((stream) => stream.sensor === sensor)
  if (all.length === 0) return null
  // Ended configurations are history, not the sensor's state now - unless
  // nothing else is left to show.
  const running = all.filter((stream) => !stream.ended)
  const streams = running.length > 0 ? running : all
  const states = streams[0].buckets.map((_, index) =>
    streams.reduce<SensorTimelineState>((worst, stream) => {
      const state = stream.buckets[index]?.state ?? 'none'
      return STATE_RANK[state] > STATE_RANK[worst] ? state : worst
    }, 'none'),
  )
  const ok = streams.reduce((sum, stream) => sum + stream.ok, 0)
  const error = streams.reduce((sum, stream) => sum + stream.error, 0)
  return {
    states,
    rate: ok + error ? ok / (ok + error) : null,
    networks: streams.filter((stream) => stream.ssid !== null).length,
  }
}

export function MiniTimeline({ timeline, sensor }: { timeline: SensorTimeline; sensor: string }) {
  const { t, i18n } = useTranslation()
  const summary = summariseSensor(timeline, sensor)
  if (!summary) return <span className="text-ink-3">—</span>
  const percent = new Intl.NumberFormat(i18n.resolvedLanguage, {
    style: 'percent',
    maximumFractionDigits: 1,
  })
  return (
    <span className="flex items-center gap-2 text-xs whitespace-nowrap tabular-nums">
      <span
        aria-hidden
        className="flex h-3 w-24 shrink-0 overflow-hidden rounded-[2px]"
      >
        {summary.states.map((state, index) => (
          <Slice key={index} look={state} className="flex-1" />
        ))}
      </span>
      <span className="text-ink font-medium">
        {summary.rate === null ? '—' : percent.format(summary.rate)}
      </span>
      {summary.networks > 1 && (
        <span className="text-ink-3 whitespace-nowrap">{t('probeSensor.networks', { count: summary.networks })}</span>
      )}
    </span>
  )
}
