import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  useCheckForUpdate,
  useJob,
  useJobLog,
  useStackVersion,
  useStartUpdate,
} from '@/api/hooks'
import type { StackVersion } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { JobSteps, LiveLog } from '@/components/ui/JobProgress'
import {
  Badge,
  Banner,
  Button,
  Card,
  DetailRow,
  EmptyState,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { formatDateTime, formatRelative } from '@/utils/format'

/**
 * Where the page keeps the job id across the gap.
 *
 * The update replaces the API, and an operator who reloads while waiting would
 * otherwise land on a page that has forgotten what it was watching - with the
 * update still running and nothing on screen saying so.
 */
const WATCHED_JOB_KEY = 'prtg-nats:update-job'

/** How often to knock while the API is away. */
const HEALTH_POLL_MS = 3000
/** After this long without an answer, stop reassuring and start explaining. */
const GIVE_UP_MS = 15 * 60 * 1000

type Phase = 'idle' | 'running' | 'waiting' | 'settled'

export function UpdatesPage() {
  const { t } = useTranslation()
  const version = useStackVersion()
  const check = useCheckForUpdate()
  const start = useStartUpdate()

  const [jobId, setJobId] = useState<string | null>(() =>
    sessionStorage.getItem(WATCHED_JOB_KEY),
  )
  const [phase, setPhase] = useState<Phase>(jobId ? 'running' : 'idle')
  const [awaySince, setAwaySince] = useState<number | null>(null)

  // While the API is gone every request fails, and React Query would turn that
  // into a page full of red. The job query is switched off for the duration
  // and the plain health poll below takes over.
  const job = useJob(phase === 'waiting' ? undefined : (jobId ?? undefined))
  const log = useJobLog(jobId ?? undefined)

  const onStart = useCallback(async () => {
    const accepted = await start.mutateAsync()
    sessionStorage.setItem(WATCHED_JOB_KEY, accepted.job_id)
    setJobId(accepted.job_id)
    setPhase('running')
  }, [start])

  const goAway = useCallback(() => {
    setPhase('waiting')
    setAwaySince(Date.now())
  }, [])

  // The handover: the server marks the job detached just before it stops
  // existing, which is the last thing this page hears from it.
  useEffect(() => {
    if (job.data?.status === 'detached' && phase === 'running') goAway()
  }, [job.data?.status, phase, goAway])

  // A dropped connection during the recreate is expected, not an error. If the
  // job query fails while we are watching one, assume the swap has begun.
  useEffect(() => {
    if (phase === 'running' && jobId && job.isError) goAway()
  }, [phase, jobId, job.isError, goAway])

  const onBack = useCallback(() => {
    setPhase('settled')
    setAwaySince(null)
  }, [])
  useHealthPoll(phase === 'waiting', onBack)

  // Once the outcome is on record the page has to reload, not just re-render:
  // the interface itself was replaced, and the code in this browser is the old
  // build talking to the new API.
  const reloaded = useRef(false)
  useEffect(() => {
    if (phase !== 'settled' || reloaded.current) return
    if (!job.data || !isTerminal(job.data.status)) return
    reloaded.current = true
    sessionStorage.removeItem(WATCHED_JOB_KEY)
    // A moment on the result first, so the reload does not read as a crash.
    const timer = window.setTimeout(() => window.location.reload(), 2500)
    return () => window.clearTimeout(timer)
  }, [phase, job.data])

  if (version.isLoading) return <Skeleton className="h-64" />
  if (version.error)
    return (
      <ErrorDetails error={version.error} onRetry={() => void version.refetch()} />
    )

  const data = version.data
  if (!data) return null

  const tooLong = awaySince !== null && Date.now() - awaySince > GIVE_UP_MS

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">{t('updates.title')}</h1>
        {data.available && phase === 'idle' && (
          <Button onClick={() => void check.mutateAsync()} disabled={check.isPending}>
            {t('updates.check_now')}
          </Button>
        )}
      </header>

      {!data.available && (
        <Banner tone="warn" title={t('updates.unavailable.title')}>
          {t(`updates.unavailable.${data.unavailable_reason ?? 'unknown'}`)}
        </Banner>
      )}

      {phase === 'waiting' && (
        <Banner
          tone={tooLong ? 'danger' : 'accent'}
          title={tooLong ? t('updates.away.stuck_title') : t('updates.away.title')}
        >
          {tooLong ? t('updates.away.stuck') : t('updates.away.body')}
        </Banner>
      )}

      <VersionCard data={data} />

      {data.available && phase === 'idle' && (
        <PermissionGate permission="system.update">
          <ActionCard
            data={data}
            onStart={() => void onStart()}
            busy={start.isPending}
          />
        </PermissionGate>
      )}

      {jobId && phase !== 'idle' && job.data && (
        <Card title={t('updates.progress')}>
          <JobSteps job={job.data} />
          {phase !== 'waiting' && (
            <LiveLog
              key={jobId}
              jobId={jobId}
              initialEvents={log.data ?? []}
              live={!isTerminal(job.data.status)}
            />
          )}
        </Card>
      )}
    </div>
  )
}

function VersionCard({ data }: { data: StackVersion }) {
  const { t } = useTranslation()
  return (
    <Card title={t('updates.state.title')} action={<StateBadge state={data.state} />}>
      <dl className="space-y-1">
        <DetailRow label={t('updates.running')}>
          {data.running_commit ? (
            <Mono>{short(data.running_commit)}</Mono>
          ) : (
            <span className="text-ink-3">{t('updates.running_unknown')}</span>
          )}
        </DetailRow>
        <DetailRow label={t('updates.checkout')}>
          <Mono>{short(data.checkout_commit) || '—'}</Mono>
          {data.checkout_dirty && (
            <Badge tone="warn" className="ml-2">
              {t('updates.dirty')}
            </Badge>
          )}
        </DetailRow>
        <DetailRow label={t('updates.branch')}>
          <Mono>{data.branch}</Mono>
          {data.remote_commit && (
            <Mono className="text-ink-3 ml-2">{short(data.remote_commit)}</Mono>
          )}
        </DetailRow>
        {data.checkout_dir && (
          <DetailRow label={t('updates.checkout_dir')}>
            <Mono className="text-ink-3">{data.checkout_dir}</Mono>
          </DetailRow>
        )}
        <DetailRow label={t('updates.checked_at')}>
          {data.checked_at ? (
            <span title={formatDateTime(data.checked_at)}>
              {formatRelative(data.checked_at)}
            </span>
          ) : (
            '—'
          )}
        </DetailRow>
      </dl>

      {!data.reachable && data.error && (
        <Banner tone="danger" title={t('updates.unreachable')}>
          <Mono className="text-xs">{data.error}</Mono>
        </Banner>
      )}
    </Card>
  )
}

function ActionCard({
  data,
  onStart,
  busy,
}: {
  data: StackVersion
  onStart: () => void
  busy: boolean
}) {
  const { t } = useTranslation()

  if (data.state === 'current')
    return (
      <Card title={t('updates.changes')}>
        <EmptyState title={t('updates.no_changes')} />
      </Card>
    )

  if (data.state === 'rebuild_pending')
    return (
      <Card title={t('updates.changes')}>
        <Banner tone="warn" title={t('updates.rebuild_pending.title')}>
          {t('updates.rebuild_pending.body')}
        </Banner>
      </Card>
    )

  if (data.state !== 'update_available') return null

  return (
    <Card
      title={t('updates.changes')}
      action={
        <Button
          variant="primary"
          onClick={onStart}
          disabled={busy || data.checkout_dirty}
        >
          {t('updates.install')}
        </Button>
      }
    >
      {data.checkout_dirty && (
        <Banner tone="warn" title={t('updates.dirty_blocks.title')}>
          {t('updates.dirty_blocks.body')}
        </Banner>
      )}
      <ul className="divide-rule divide-y">
        {data.commits.map((commit) => (
          <li key={commit.sha} className="flex items-baseline gap-3 py-1.5">
            <Mono className="text-ink-3 shrink-0 text-xs">{short(commit.sha)}</Mono>
            <span className="min-w-0 flex-1 truncate text-sm">{commit.subject}</span>
            <span className="text-ink-3 shrink-0 text-xs">
              {formatRelative(commit.date)}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-ink-3 mt-3 text-xs">{t('updates.what_happens')}</p>
    </Card>
  )
}

function StateBadge({ state }: { state: StackVersion['state'] }) {
  const { t } = useTranslation()
  const tone =
    state === 'current'
      ? 'ok'
      : state === 'update_available'
        ? 'accent'
        : state === 'unknown'
          ? 'neutral'
          : 'warn'
  return <Badge tone={tone}>{t(`updates.states.${state}`)}</Badge>
}

/**
 * Knock on /health until it answers.
 *
 * Deliberately a plain fetch rather than a query: this runs while every other
 * request fails, and the point is to be the one thing that treats a failure as
 * normal. /health is served by the proxy without a session, so it answers as
 * soon as the API is back rather than after a sign-in.
 */
function useHealthPoll(active: boolean, onBack: () => void) {
  useEffect(() => {
    if (!active) return
    let cancelled = false

    const knock = async () => {
      try {
        const response = await fetch('/health', { cache: 'no-store' })
        if (!cancelled && response.ok) onBack()
      } catch {
        // Expected while the container is being replaced.
      }
    }

    const timer = window.setInterval(() => void knock(), HEALTH_POLL_MS)
    // A tab coming back to the front is the most likely moment for good news.
    const onVisible = () => {
      if (document.visibilityState === 'visible') void knock()
    }
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [active, onBack])
}

function isTerminal(status: string) {
  return ['successful', 'failed', 'cancelled', 'partially_successful'].includes(status)
}

function short(sha: string) {
  return sha ? sha.slice(0, 12) : ''
}
