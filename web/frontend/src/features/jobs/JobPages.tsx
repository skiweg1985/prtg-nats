import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { useCancelJob, useJob, useJobLog, useJobs, useRetryJob } from '@/api/hooks'
import type { JobSummary } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { JobSteps, LiveLog, ProgressBar } from '@/components/ui/JobProgress'
import {
  Badge,
  Banner,
  Button,
  Card,
  DetailRow,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { JobStatusBadge } from '@/components/ui/status'
import { formatDateTime, formatDuration, formatRelative } from '@/utils/format'

/**
 * The job states worth a button of their own.
 *
 * In the URL rather than in component state, because these are the two
 * questions the dashboard asks on somebody's behalf - "which three failed"
 * leads here, not to fifty rows to read through.
 */
const JOB_FILTERS = ['running', 'failed'] as const
type JobFilter = (typeof JOB_FILTERS)[number]

function asJobFilter(value: string | null): JobFilter | null {
  return JOB_FILTERS.find((entry) => entry === value) ?? null
}

export function JobListPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const status = asJobFilter(params.get('status'))
  // Filtered by the server, not in the browser: the list is the last fifty
  // jobs, so filtering what arrived would search a window rather than the
  // history - and "no failures" would mean "none among the last fifty".
  const { data, isLoading, error, refetch } = useJobs(
    status ? { status } : {},
  )

  function setStatus(next: JobFilter | null) {
    setParams(next ? { status: next } : {}, { replace: true })
  }

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const columns: Column<JobSummary>[] = [
    {
      key: 'type',
      header: t('jobs.columns.type'),
      sortValue: (row) => row.type,
      searchValue: (row) => `${row.type} ${row.target_label ?? ''}`,
      cell: (row) => (
        <div className="min-w-0">
          <Mono className="text-ink">{row.type}</Mono>
          {row.target_label && (
            <p className="text-ink-3 truncate text-xs">{row.target_label}</p>
          )}
        </div>
      ),
    },
    {
      key: 'status',
      header: t('jobs.columns.status'),
      sortValue: (row) => row.status,
      cell: (row) => (
        <div className="flex items-center gap-2">
          <JobStatusBadge status={row.status} />
          {row.blocked_reason_key && <Badge tone="neutral">⏸</Badge>}
        </div>
      ),
    },
    {
      key: 'progress',
      header: t('jobs.columns.progress'),
      width: '9rem',
      cell: (row) =>
        row.status === 'running' ? (
          <ProgressBar value={row.progress} />
        ) : (
          <span className="text-ink-3 text-xs">
            {row.current_step ? t(`jobs.steps.${row.current_step}`, row.current_step) : '—'}
          </span>
        ),
    },
    {
      key: 'started',
      header: t('jobs.columns.started'),
      sortValue: (row) => row.created_at,
      cell: (row) => (
        <span className="text-ink-2 text-xs">{formatRelative(row.created_at)}</span>
      ),
    },
    {
      key: 'duration',
      header: t('jobs.columns.duration'),
      align: 'right',
      sortValue: (row) => row.duration_seconds ?? 0,
      cell: (row) => (
        <span className="text-ink-2 text-xs">{formatDuration(row.duration_seconds)}</span>
      ),
    },
    {
      key: 'by',
      header: t('jobs.columns.by'),
      sortValue: (row) => row.requested_by_name ?? '',
      cell: (row) => <span className="text-ink-3 text-xs">{row.requested_by_name ?? '—'}</span>,
    },
  ]

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg">{t('jobs.title')}</h1>
        <p className="text-ink-3 text-sm">{t('jobs.subtitle')}</p>
      </header>

      <DataTable
        rows={data}
        columns={columns}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        emptyTitle={status ? t('jobs.emptyFiltered') : t('jobs.empty')}
        emptyAction={
          status ? (
            <Button variant="secondary" onClick={() => setStatus(null)}>
              {t('common.clearFilters')}
            </Button>
          ) : undefined
        }
        rowHref={(row) => `/jobs/${row.id}`}
        filters={
          <div className="flex flex-wrap items-center gap-2">
            {JOB_FILTERS.map((entry) => (
              <Button
                key={entry}
                size="sm"
                variant={status === entry ? 'primary' : 'ghost'}
                aria-pressed={status === entry}
                onClick={() => setStatus(status === entry ? null : entry)}
              >
                {t(`jobs.filters.${entry}`)}
              </Button>
            ))}
          </div>
        }
      />
    </div>
  )
}

export function JobDetailPage() {
  const { t } = useTranslation()
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()

  const { data: job, isLoading, error, refetch } = useJob(jobId, {
    // While a job runs its status is pushed over the event stream, but the
    // step list and the result come from here; a slow poll keeps them honest
    // without competing with the stream.
    refetchInterval: (query) =>
      query.state.data && !isTerminal(query.state.data.status) ? 5_000 : false,
  })
  const { data: log } = useJobLog(jobId)
  const retry = useRetryJob()
  const cancel = useCancelJob()

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!job || !jobId) return null

  const running = !isTerminal(job.status)

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <Link to="/jobs" className="text-ink-3 text-xs">
            ← {t('jobs.title')}
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-lg">
              <Mono className="text-base">{job.type}</Mono>
            </h1>
            <JobStatusBadge status={job.status} />
          </div>
          {job.target_label && <p className="text-ink-3 text-sm">{job.target_label}</p>}
        </div>

        <div className="flex gap-2">
          {running && (
            <PermissionGate permission="job.cancel">
              <Button
                size="sm"
                onClick={() => cancel.mutate(jobId)}
                disabled={cancel.isPending}
              >
                {t('jobs.cancel')}
              </Button>
            </PermissionGate>
          )}
          {!running && (
            <PermissionGate permission="job.retry">
              <Button
                size="sm"
                variant="primary"
                onClick={() =>
                  retry.mutate(jobId, {
                    onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                  })
                }
                disabled={retry.isPending}
              >
                {t('jobs.retry')}
              </Button>
            </PermissionGate>
          )}
        </div>
      </header>

      {job.blocked_reason_key && (
        <Banner tone="neutral">
          {t(job.blocked_reason_key.replace('jobs.blocked.', 'jobs.blocked.'), {
            jobId: job.blocked_by_job_id,
          })}
        </Banner>
      )}

      {job.retry_of_job_id && (
        <Banner tone="neutral">
          <Link to={`/jobs/${job.retry_of_job_id}`} className="text-accent">
            {t('jobs.retry')} · <Mono>{job.retry_of_job_id}</Mono>
          </Link>
        </Banner>
      )}

      <Card>
        <div className="space-y-3">
          <JobSteps job={job} />
          {running && <ProgressBar value={job.progress} />}
        </div>
      </Card>

      {job.error_code && (
        <ErrorDetails
          error={
            new ApiError(
              {
                code: job.error_code,
                message_key: `errors.${job.error_code}`,
                params: job.error_params ?? {},
                fields: [],
                details: job.error_details,
                correlation_id: null,
                retryable: true,
              },
              500,
            )
          }
          step={job.current_step}
          target={job.target_label}
          onRetry={() =>
            retry.mutate(jobId, {
              onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
            })
          }
        />
      )}

      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Card dense>
          <div className="p-4">
            {/* Keyed on the job: the log merges what it has with what it is
                given, and a different job is a different log, not more of
                this one. */}
            <LiveLog
              key={jobId}
              jobId={jobId}
              initialEvents={log ?? []}
              live={running}
            />
          </div>
        </Card>

        <Card title={t('jobs.result')}>
          <dl>
            <DetailRow label={t('jobs.columns.started')}>
              {formatDateTime(job.started_at ?? job.created_at)}
            </DetailRow>
            <DetailRow label={t('jobs.columns.duration')}>
              {formatDuration(job.duration_seconds)}
            </DetailRow>
            <DetailRow label={t('jobs.columns.by')}>
              {job.requested_by_name ?? job.trigger}
            </DetailRow>
          </dl>
          {job.result && (
            <pre className="bg-surface-2 rounded-inset text-ink-2 mt-3 max-h-64 overflow-auto p-3 font-mono text-xs whitespace-pre-wrap">
              {JSON.stringify(job.result, null, 2)}
            </pre>
          )}
        </Card>
      </div>
    </div>
  )
}

function isTerminal(status: JobSummary['status']): boolean {
  return ['successful', 'failed', 'cancelled', 'partially_successful'].includes(status)
}
