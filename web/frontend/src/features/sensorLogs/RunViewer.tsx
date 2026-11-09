import { Fragment, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  CircleDashed,
  CircleX,
} from 'lucide-react'

import { useSensorLog, useSensorLogNeighbours } from '@/api/hooks'
import type { SensorLogRecord } from '@/api/types'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Button, CheckboxField, Dialog, Mono, Skeleton } from '@/components/ui/primitives'
import { formatDateTime, formatNumber } from '@/utils/format'

import { PHASE_FIELDS, hasPhases, phasesOf, type Phase } from './phases'
import { RecordBody, ResultBadge, networkOf, runHref, useCodeLabel, useFields } from './RunList'

/**
 * One run of a timeline row in a dialog over the sensor page, with the runs
 * before and after it a key press away.
 *
 * The run's own page stays: it is what a link in PRTG opens. This is for
 * looking through a row's history without leaving the timeline. Which run
 * is open, and whether only failures are stepped through, is in the
 * address, so a reload or a link sent on opens the same run.
 */

/** The address parameters the viewer owns. */
export const RUN_PARAM = 'run'
export const FAILURES_PARAM = 'failures'

export function RunViewer() {
  const [params, setParams] = useSearchParams()
  // The router renders a new address a moment later. A key pressed in
  // between would otherwise build on the old one and drop what was just
  // set, such as failures only.
  const pending = useRef<URLSearchParams | null>(null)
  if (pending.current?.toString() === params.toString()) pending.current = null
  const id = params.get(RUN_PARAM)
  const failuresOnly = params.get(FAILURES_PARAM) === '1'
  if (!id) return null

  function update(change: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(pending.current ?? params)
    change(next)
    pending.current = next
    // Stepping is reading, not navigating: Back leaves the viewer instead
    // of walking back through every run it showed.
    setParams(next, { replace: true })
  }

  return (
    <RunDialog
      id={id}
      failuresOnly={failuresOnly}
      onStep={(target) => update((next) => next.set(RUN_PARAM, target))}
      onFailuresOnly={(on) =>
        update((next) => (on ? next.set(FAILURES_PARAM, '1') : next.delete(FAILURES_PARAM)))
      }
      onClose={() =>
        update((next) => {
          next.delete(RUN_PARAM)
          next.delete(FAILURES_PARAM)
        })
      }
    />
  )
}

function RunDialog({
  id,
  failuresOnly,
  onStep,
  onFailuresOnly,
  onClose,
}: {
  id: string
  failuresOnly: boolean
  onStep: (id: string) => void
  onFailuresOnly: (on: boolean) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const { data, error, isLoading } = useSensorLog(id)
  const neighbours = useSensorLogNeighbours(id, failuresOnly)
  // The run shown until the next one has loaded, so stepping does not
  // collapse the dialog to a skeleton between two runs.
  const last = useRef<SensorLogRecord | null>(null)
  if (data) last.current = data
  const record = data ?? last.current

  // Neighbours of another run, or without the filter just switched, would
  // step to the wrong run.
  const settled = neighbours.data?.record.id === id && !neighbours.isPlaceholderData
  const previous = settled ? (neighbours.data?.previous ?? null) : null
  const next = settled ? (neighbours.data?.next ?? null) : null

  // Read through a ref so the listener is added once and always steps from
  // the run shown now.
  const step = useRef({ previous, next, onStep })
  step.current = { previous, next, onStep }
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) {
        if ((target as HTMLInputElement).type !== 'checkbox') return
      }
      const { previous: before, next: after, onStep: go } = step.current
      if (event.key === 'ArrowLeft' && before) go(before.id)
      else if (event.key === 'ArrowRight' && after) go(after.id)
      else return
      event.preventDefault()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  const network = record ? networkOf(record) : null
  const title = record
    ? [record.sensor, network, record.variant].filter(Boolean).join(' · ')
    : t('runViewer.title')
  const ends = failuresOnly
    ? { older: t('runViewer.noOlderFailure'), newer: t('runViewer.noNewerFailure') }
    : { older: t('runViewer.noOlderRun'), newer: t('runViewer.noNewerRun') }

  return (
    <Dialog title={title} size="lg" onClose={onClose}>
      <div className="space-y-4">
        <nav
          aria-label={t('runViewer.navigation')}
          className="flex flex-wrap items-center gap-2"
        >
          {/* aria-disabled rather than disabled: a button that disables
              itself under the focus drops the focus out of the dialog. */}
          <Button
            size="sm"
            icon={ChevronLeft}
            aria-disabled={!previous}
            className={clsx(!previous && 'opacity-45')}
            aria-keyshortcuts="ArrowLeft"
            onClick={() => previous && onStep(previous.id)}
          >
            {t('runViewer.older')}
          </Button>
          <Button
            size="sm"
            aria-disabled={!next}
            className={clsx(!next && 'opacity-45')}
            aria-keyshortcuts="ArrowRight"
            onClick={() => next && onStep(next.id)}
          >
            {t('runViewer.newer')}
            <ChevronRight aria-hidden size={14} strokeWidth={1.75} />
          </Button>
          <span className="ml-auto">
            <CheckboxField
              label={t('runViewer.failuresOnly')}
              checked={failuresOnly}
              onChange={onFailuresOnly}
            />
          </span>
          <p role="status" className="text-ink-3 w-full text-xs">
            {settled && !previous && !next
              ? `${ends.older} · ${ends.newer}`
              : settled && !previous
                ? ends.older
                : settled && !next
                  ? ends.newer
                  : ''}
          </p>
        </nav>

        {error && !record ? (
          <ErrorDetails error={error} />
        ) : isLoading && !record ? (
          <Skeleton className="h-48" />
        ) : record ? (
          <RunContent key={record.id} record={record} />
        ) : null}

        <div className="border-rule flex flex-wrap items-center justify-between gap-2 border-t pt-3">
          <Link to={runHref(id)} className="text-accent text-xs hover:underline">
            {t('runViewer.openPage')}
          </Link>
          <Button variant="ghost" onClick={onClose}>
            {t('common.close')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function RunContent({ record }: { record: SensorLogRecord }) {
  const { t } = useTranslation()
  const codeLabel = useCodeLabel()
  const phases = phasesOf(record)
  const phased = hasPhases(record.sensor)
  const [rawOpen, setRawOpen] = useState(!phased)
  const words = codeLabel(record.sensor, record.code)
  const total = totalOf(record.body)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-ink text-sm font-medium tabular-nums">
          {formatDateTime(record.time)}
        </span>
        <ResultBadge result={record.result} />
        {total !== null && (
          <span className="text-ink-2 text-xs tabular-nums">
            {t('runViewer.total', { duration: duration(total) })}
          </span>
        )}
      </div>
      {record.result === 'error' && (words || record.summary) && (
        <p className="text-sm">
          <span className="text-ink">{words ?? record.summary}</span>
          {record.code && <Mono className="text-ink-3 ml-2">{record.code}</Mono>}
          {words && record.summary && record.summary !== words && (
            <span className="text-ink-2 block text-xs">{record.summary}</span>
          )}
        </p>
      )}

      {phases.length > 0 && <PhaseList record={record} phases={phases} />}

      <section>
        <button
          type="button"
          aria-expanded={rawOpen}
          onClick={() => setRawOpen((value) => !value)}
          className="text-ink flex items-center gap-1.5 text-sm font-medium"
        >
          {rawOpen ? (
            <ChevronDown aria-hidden className="size-4" />
          ) : (
            <ChevronRight aria-hidden className="size-4" />
          )}
          {t('runViewer.raw')}
        </button>
        {rawOpen && (
          <div className="mt-2">
            <RecordBody body={record.body} sensor={record.sensor} />
          </div>
        )}
      </section>
    </div>
  )
}

const STATE_ICON = {
  ok: CircleCheck,
  failed: CircleX,
  skipped: CircleDashed,
} as const

const STATE_TONE = {
  ok: 'text-ok',
  failed: 'text-danger',
  skipped: 'text-ink-3',
} as const

function PhaseList({ record, phases }: { record: SensorLogRecord; phases: Phase[] }) {
  const { t } = useTranslation()
  const field = useFields(record.sensor)
  const codeLabel = useCodeLabel()
  const failed = phases.find((phase) => phase.state === 'failed')?.key
  const [open, setOpen] = useState<Set<string>>(new Set(failed ? [failed] : []))

  function toggle(key: string) {
    const changed = new Set(open)
    if (changed.has(key)) changed.delete(key)
    else changed.add(key)
    setOpen(changed)
  }

  return (
    <ol aria-label={t('runViewer.phases')} className="border-rule rounded-control border">
      {phases.map((phase) => {
        const Icon = STATE_ICON[phase.state]
        const details = PHASE_FIELDS[phase.key].filter(
          (key) => record.body[key] !== undefined && record.body[key] !== '',
        )
        const isFailed = phase.state === 'failed'
        const expandable = details.length > 0 || isFailed
        const isOpen = expandable && open.has(phase.key)
        const label = t(`runViewer.phase.${phase.key}`, { method: methodOf(record.body) })
        const stateLabel = t(`runViewer.state.${phase.state}`)
        return (
          <li
            key={phase.key}
            className={clsx(
              'border-rule border-b last:border-0',
              isFailed && 'bg-danger/5 border-l-danger border-l-2',
            )}
          >
            <button
              type="button"
              aria-expanded={expandable ? isOpen : undefined}
              disabled={!expandable}
              onClick={() => toggle(phase.key)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm disabled:cursor-default"
            >
              <Icon
                aria-hidden
                size={16}
                strokeWidth={1.75}
                className={clsx('shrink-0', STATE_TONE[phase.state])}
              />
              <span className={clsx('flex-1', phase.state === 'skipped' ? 'text-ink-3' : 'text-ink')}>
                {label}
                <span className="sr-only">: {stateLabel}</span>
              </span>
              <span className="text-ink-2 text-xs tabular-nums">
                {phase.ms !== null ? duration(phase.ms) : phase.state === 'skipped' ? stateLabel : '—'}
              </span>
              {expandable ? (
                isOpen ? (
                  <ChevronDown aria-hidden className="text-ink-3 size-4" />
                ) : (
                  <ChevronRight aria-hidden className="text-ink-3 size-4" />
                )
              ) : (
                <span className="size-4" />
              )}
            </button>
            {isOpen && (
              <div className="space-y-2 px-3 pb-3 pl-9">
                {isFailed && (
                  <p className="text-danger text-sm">
                    {codeLabel(record.sensor, record.code) ?? record.summary ?? record.code}
                  </p>
                )}
                {details.length > 0 && (
                  <dl className="grid grid-cols-[minmax(7rem,12rem)_1fr] gap-x-3 gap-y-1">
                    {details.map((key) => (
                      <Fragment key={key}>
                        <dt className="text-ink-2 text-xs">{field(key).label ?? key}</dt>
                        <dd className="text-ink min-w-0 font-mono text-xs break-all">
                          {field(key).value(record.body[key])}
                        </dd>
                      </Fragment>
                    ))}
                  </dl>
                )}
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/** PSK or EAP, the word the authentication step goes by. */
function methodOf(body: Record<string, unknown>): string {
  return body.auth === 'psk' ? 'PSK' : 'EAP'
}

function totalOf(body: Record<string, unknown>): number | null {
  const timings = body.timings
  if (typeof timings !== 'object' || timings === null) return null
  const total = (timings as Record<string, unknown>).total_ms
  return typeof total === 'number' ? total : null
}

/** Under a second in milliseconds, anything longer in seconds. */
function duration(ms: number): string {
  if (ms < 1000) return `${formatNumber(ms)} ms`
  return `${formatNumber(Math.round(ms / 100) / 10)} s`
}
