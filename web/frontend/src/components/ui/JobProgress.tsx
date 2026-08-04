import clsx from 'clsx'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { API_BASE } from '@/api/client'
import type { JobDetail, JobEvent, JobStep } from '@/api/types'
import { formatTime } from '@/utils/format'

import { Badge, Button, Dot } from './primitives'

/**
 * Live job output.
 *
 * The stored log is fetched first and the event stream then continues from the
 * last sequence number, so a page opened after a job finished shows exactly
 * what a page that watched it happen shows. A dropped stream is reported rather
 * than hidden - a log that silently stops is worse than one that says it did.
 */

export function JobSteps({ job }: { job: JobDetail }) {
  const { t } = useTranslation()
  const total = job.steps.length
  const current = job.steps.findIndex((step) => step.status === 'running')

  return (
    <ol className="flex flex-wrap gap-x-1 gap-y-2">
      {job.steps.map((step, index) => (
        <li key={step.name} className="flex items-center gap-1">
          <StepChip step={step} />
          {index < total - 1 && (
            <span aria-hidden className="text-ink-3 px-0.5">
              ›
            </span>
          )}
        </li>
      ))}
      {current >= 0 && (
        <li className="text-ink-3 basis-full text-xs">
          {t('jobs.step', {
            current: current + 1,
            total,
            name: t(`jobs.steps.${job.steps[current].name}`, job.steps[current].name),
          })}
        </li>
      )}
    </ol>
  )
}

function StepChip({ step }: { step: JobStep }) {
  const { t } = useTranslation()
  const tone =
    step.status === 'succeeded'
      ? 'ok'
      : step.status === 'failed'
        ? 'danger'
        : step.status === 'running'
          ? 'accent'
          : 'neutral'

  return (
    <Badge tone={tone} className={step.status === 'skipped' ? 'opacity-50' : undefined}>
      <Dot tone={tone} />
      {t(`jobs.steps.${step.name}`, step.name)}
    </Badge>
  )
}

/**
 * Merge stored and streamed lines, newest sequence wins, ordered by sequence.
 *
 * The log has two sources and they overlap: the query refetches on window
 * focus and on every job mutation, and the stream keeps running while it does.
 * Replacing the list with the refetched one - which is what this did - drops
 * the lines that arrived after the server built its answer, and the stream
 * never sends them again because it has moved past their sequence.
 */
function mergeEvents(current: JobEvent[], incoming: JobEvent[]): JobEvent[] {
  const bySequence = new Map(current.map((event) => [event.sequence, event]))
  for (const event of incoming) bySequence.set(event.sequence, event)
  return [...bySequence.values()].sort((a, b) => a.sequence - b.sequence)
}

const LEVEL_CLASS: Record<JobEvent['level'], string> = {
  debug: 'text-ink-3',
  info: 'text-ink-2',
  warning: 'text-warn',
  error: 'text-danger',
}

export function LiveLog({
  jobId,
  initialEvents,
  live,
}: {
  jobId: string
  initialEvents: JobEvent[]
  live: boolean
}) {
  const { t } = useTranslation()
  const [events, setEvents] = useState<JobEvent[]>(initialEvents)
  const [connection, setConnection] = useState<'idle' | 'open' | 'closed' | 'ended'>(
    'idle',
  )
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setEvents((current) => mergeEvents(current, initialEvents))
  }, [initialEvents])

  useEffect(() => {
    if (!live) return

    const after = events.at(-1)?.sequence ?? 0
    const source = new EventSource(`${API_BASE}/jobs/${jobId}/events?after=${after}`)
    setConnection('open')

    source.addEventListener('job.event', (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as JobEvent
      setEvents((current) => mergeEvents(current, [event]))
    })
    source.addEventListener('end', () => {
      // The job finished and said so; that is not a dropped connection.
      setConnection('ended')
      source.close()
    })
    source.onerror = () => {
      setConnection('closed')
      source.close()
    }

    return () => source.close()
    // Intentionally keyed on the job alone: re-subscribing on every new line
    // would tear the stream down and rebuild it for each event.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, live])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'nearest' })
  }, [events.length])

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">{t('jobs.log')}</h3>
        {live && connection === 'open' && (
          <Badge tone="accent">
            <Dot tone="accent" />
            {t('jobs.live')}
          </Badge>
        )}
      </div>

      {live && connection === 'closed' && (
        <p className="text-ink-3 text-xs">{t('jobs.disconnected')}</p>
      )}

      <div className="rounded-card border-rule bg-surface max-h-[28rem] overflow-auto border">
        <table className="w-full border-collapse text-left">
          <tbody>
            {events.map((event) => (
              <tr key={event.sequence} className="border-rule border-b last:border-0">
                <td className="text-ink-3 w-20 px-3 py-1 align-top font-mono text-[0.6875rem] whitespace-nowrap">
                  {formatTime(event.ts)}
                </td>
                <td className="w-28 px-2 py-1 align-top">
                  {event.step && (
                    <span className="label-mono">
                      {t(`jobs.steps.${event.step}`, event.step)}
                    </span>
                  )}
                </td>
                <td className={clsx('px-2 py-1 text-sm', LEVEL_CLASS[event.level])}>
                  {t(`jobs.events.${event.code.replace(/^jobs\./, '')}`, {
                    ...event.params,
                    defaultValue: event.code,
                  })}
                  {event.raw && (
                    <>
                      {' '}
                      <button
                        type="button"
                        className="text-accent text-xs underline underline-offset-2"
                        onClick={() =>
                          setExpanded((current) => {
                            const next = new Set(current)
                            if (next.has(event.sequence)) next.delete(event.sequence)
                            else next.add(event.sequence)
                            return next
                          })
                        }
                      >
                        {expanded.has(event.sequence)
                          ? t('common.hideTechnicalDetails')
                          : t('common.showTechnicalDetails')}
                      </button>
                      {expanded.has(event.sequence) && (
                        <pre className="bg-surface-2 rounded-inset text-ink-2 mt-1 max-h-48 overflow-auto p-2 font-mono text-xs whitespace-pre-wrap">
                          {event.raw}
                        </pre>
                      )}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div ref={bottom} />
      </div>
    </div>
  )
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div
      className="bg-surface-2 h-1.5 w-full overflow-hidden rounded-full"
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="bg-accent h-full transition-[width] duration-(--dur-short) ease-(--ease-out)"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  )
}

export function RetryButton({
  onRetry,
  disabled,
}: {
  onRetry: () => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  return (
    <Button variant="secondary" size="sm" onClick={onRetry} disabled={disabled}>
      {t('jobs.retry')}
    </Button>
  )
}
