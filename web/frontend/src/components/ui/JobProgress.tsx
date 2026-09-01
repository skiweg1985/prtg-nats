import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
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
 * what a page that watched it happen shows. A dropped stream is picked up
 * again from the last line the client holds, and only reported once picking it
 * up has stopped working - a log that silently stops is worse than one that
 * says it did.
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
    <Badge
      tone={tone}
      quiet
      className={step.status === 'skipped' ? 'opacity-50' : undefined}
    >
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

type Connection = 'idle' | 'open' | 'reconnecting' | 'closed' | 'ended'

/**
 * How long to wait before each attempt at picking the stream up again.
 *
 * The length of the list is also the point at which the log gives up and says
 * so: retrying forever at an interval nobody watches is not better than a
 * control the operator can press.
 */
const RECONNECT_DELAYS_MS = [1_000, 2_000, 5_000, 10_000, 30_000]

/**
 * How long a stream has to hold before the backoff starts over.
 *
 * Resetting on connect alone would be enough for a proxy that drops the
 * connection once, and wrong for one that accepts it and drops it again: every
 * attempt would look like a recovery and the retries would settle at one a
 * second. A job runs for minutes, so a connection that survives this long has
 * recovered, not flapped.
 */
const RECONNECT_RESET_MS = 30_000

export function LiveLog({
  jobId,
  initialEvents,
  live,
  onStatusChange,
}: {
  jobId: string
  initialEvents: JobEvent[]
  live: boolean
  /**
   * Called when the server reports a status or step change on this job.
   *
   * The stream already carries these; nobody was listening, so the step list
   * could only move as fast as whatever was polling for it. Steps that take
   * less time than the poll interval - the preflight and the backup, usually -
   * were over before anything asked, and appeared never to have run.
   */
  onStatusChange?: () => void
}) {
  const { t } = useTranslation()
  const [events, setEvents] = useState<JobEvent[]>(initialEvents)
  const [connection, setConnection] = useState<Connection>('idle')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  // Bumping this opens a fresh stream. It is the only thing besides the job
  // itself that re-runs the subscription, which is what keeps the retry count
  // out of the dependencies: a counter that resets while a stream is healthy
  // would tear that healthy stream down to do it.
  const [attempt, setAttempt] = useState(0)
  const failures = useRef(0)
  // Where a reconnect resumes. The endpoint replays everything after it, so it
  // has to follow the merged list rather than either of its two sources.
  const lastSequence = useRef(initialEvents.at(-1)?.sequence ?? 0)
  const bottom = useRef<HTMLDivElement>(null)
  // Held in a ref so a caller passing an inline function does not tear the
  // stream down and rebuild it on every render.
  const notify = useRef(onStatusChange)
  useEffect(() => {
    notify.current = onStatusChange
  }, [onStatusChange])

  useEffect(() => {
    setEvents((current) => mergeEvents(current, initialEvents))
  }, [initialEvents])

  useEffect(() => {
    lastSequence.current = events.at(-1)?.sequence ?? lastSequence.current
  }, [events])

  // Declared before the subscription so it runs first on the commit that
  // changes either: a different job, or a job that has only now gone live,
  // starts counting from zero.
  useEffect(() => {
    failures.current = 0
  }, [jobId, live])

  const reconnectNow = useCallback(() => {
    failures.current = 0
    setConnection('reconnecting')
    setAttempt((current) => current + 1)
  }, [])

  useEffect(() => {
    if (!live) return

    const source = new EventSource(
      `${API_BASE}/jobs/${jobId}/events?after=${lastSequence.current}`,
    )
    let settled: ReturnType<typeof setTimeout> | undefined
    let retry: ReturnType<typeof setTimeout> | undefined

    source.onopen = () => {
      setConnection('open')
      settled = setTimeout(() => {
        failures.current = 0
      }, RECONNECT_RESET_MS)
    }
    source.addEventListener('job.event', (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as JobEvent
      setEvents((current) => mergeEvents(current, [event]))
    })
    source.addEventListener('job.status', () => {
      notify.current?.()
    })
    source.addEventListener('end', () => {
      // The job finished and said so; that is not a dropped connection, and
      // nothing is left to pick up.
      setConnection('ended')
      source.close()
    })
    source.onerror = () => {
      // Closing suppresses the browser's own reconnect on purpose: it would
      // ask for the same URL again, with the `after` this stream started from,
      // and replay every line since instead of continuing where we are.
      source.close()
      const delay = RECONNECT_DELAYS_MS[failures.current]
      if (delay === undefined) {
        setConnection('closed')
        return
      }
      failures.current += 1
      setConnection('reconnecting')
      retry = setTimeout(() => setAttempt((current) => current + 1), delay)
    }

    return () => {
      clearTimeout(settled)
      clearTimeout(retry)
      source.close()
    }
    // Keyed on the job and the attempt, never on the log: re-subscribing on
    // every new line would tear the stream down and rebuild it for each event.
  }, [jobId, live, attempt])

  // A network coming back or a tab coming forward is better news than the
  // backoff has. A laptop that slept through the last attempt would otherwise
  // wake to a log that has given up, with the job still running.
  useEffect(() => {
    if (!live) return
    if (connection !== 'reconnecting' && connection !== 'closed') return

    const wake = () => {
      if (document.visibilityState === 'visible') reconnectNow()
    }
    window.addEventListener('online', wake)
    document.addEventListener('visibilitychange', wake)
    return () => {
      window.removeEventListener('online', wake)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [live, connection, reconnectNow])

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

      {live && connection === 'reconnecting' && (
        <p className="text-ink-3 text-xs" role="status">
          {t('jobs.reconnecting')}
        </p>
      )}

      {live && connection === 'closed' && (
        <div className="flex flex-wrap items-center gap-2" role="status">
          <p className="text-ink-3 text-xs">{t('jobs.disconnected')}</p>
          <Button variant="secondary" size="sm" onClick={reconnectNow}>
            {t('jobs.reconnect')}
          </Button>
        </div>
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
