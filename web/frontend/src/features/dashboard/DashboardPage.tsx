import clsx from 'clsx'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { CircleCheck, Wrench } from 'lucide-react'

import { useDashboard, useSetupStack } from '@/api/hooks'
import type { Dashboard } from '@/api/types'
import { auditIcon, OBJECT_ICONS } from '@/app/icons'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Banner,
  Button,
  Card,
  Dot,
  EmptyState,
  Label,
  MetricList,
  MetricRow,
  MoreLink,
  Mono,
  PageHeader,
  RelativeTime,
  Skeleton,
} from '@/components/ui/primitives'
import { PermissionGate } from '@/app/providers'
import { JobStatusIcon } from '@/components/ui/status'
import { formatBytes, formatNumber } from '@/utils/format'
import { AttentionList, useAttentionItems } from '@/features/dashboard/AttentionList'

/**
 * One question: is the platform operational, and is there anything to do?
 *
 * Everything on this page either answers it or links to the place that does.
 * It is not a second monitoring system - PRTG does that. This watches the
 * plumbing PRTG depends on.
 */
export function DashboardPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useDashboard()
  const setup = useSetupStack()
  const attention = useAttentionItems(data)

  if (isLoading) {
    // The page's own shape, so nothing jumps when the numbers arrive.
    return (
      <div className="space-y-4">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-24" />
        <div className="grid gap-4 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-52" />
          ))}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-80" />
          <Skeleton className="h-80" />
        </div>
      </div>
    )
  }

  if (error) {
    return <ErrorDetails error={error} onRetry={() => void refetch()} />
  }
  if (!data) return null

  return (
    <div className="space-y-4">
      <PageHeader title={t('dashboard.title')} subtitle={t('dashboard.question')} />

      {!data.system.site.is_configured && (
        <Banner tone="danger">{t('dashboard.notConfigured')}</Banner>
      )}
      {data.system.site.is_configured &&
        // Partial as well: the setup job completes what is missing, and the
        // certificates page sends anyone with a missing certificate here.
        ['missing', 'partial'].includes(data.system.capabilities.runtime_state) && (
          <Banner
            tone="warn"
            title={t('dashboard.setupTitle')}
            action={
              <PermissionGate permission="system.settings">
                <Button
                  icon={Wrench}
                  variant="primary"
                  size="sm"
                  onClick={() =>
                    setup.mutate(undefined, {
                      onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                    })
                  }
                  loading={setup.isPending}
                >
                  {t('dashboard.setupAction')}
                </Button>
              </PermissionGate>
            }
          >
            {t('dashboard.setupHint')}
          </Banner>
        )}
      {!data.system.nats.available && data.system.site.is_configured && (
        <Banner tone="danger">{t('dashboard.natsUnavailable')}</Banner>
      )}
      {attention.length === 0 && data.system.nats.healthy && (
        // One quiet line: a green banner across the page celebrated the
        // state that needs nobody, louder than any warning beside it.
        <p role="status" className="surface-card text-ink-2 flex h-11 items-center gap-2.5 px-4 text-sm">
          <CircleCheck aria-hidden size={16} strokeWidth={1.75} className="text-ok shrink-0" />
          {t('dashboard.allClear')}
        </p>
      )}
      {attention.length > 0 && <AttentionList items={attention} />}

      <div className="grid gap-4 lg:grid-cols-3">
        <NatsCard dashboard={data} />
        <ProbeCard dashboard={data} />
        <JobCard dashboard={data} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <RecentJobs dashboard={data} />
        <RecentActivity dashboard={data} />
      </div>
    </div>
  )
}

/** "Show all" in a card header, for those who may open the list. */
function ShowAll({ to, permission }: { to: string; permission: string }) {
  const { t } = useTranslation()
  return (
    <PermissionGate permission={permission}>
      <MoreLink to={to}>{t('common.showAll')}</MoreLink>
    </PermissionGate>
  )
}

/** The figure after a card title: how many things the card is about. */
function Count({ value }: { value: number }) {
  return <span className="text-ink-3 text-sm font-normal tabular-nums">{formatNumber(value)}</span>
}

function NatsCard({ dashboard }: { dashboard: Dashboard }) {
  const { t } = useTranslation()
  const { nats, site } = dashboard.system
  const jetstream = nats.jetstream
  const tone = nats.healthy ? 'ok' : nats.available ? 'warn' : 'danger'

  return (
    <Card
      title={t('dashboard.natsServer')}
      icon={OBJECT_ICONS.nats}
      action={
        <span className="text-ink-2 inline-flex items-center gap-1.5 text-xs">
          <Dot tone={tone} />
          {nats.healthy ? t('status.service.active') : t('status.service.inactive')}
        </span>
      }
    >
      <div className="mb-3 space-y-0.5">
        <Label>{t('dashboard.endpoint')}</Label>
        <Mono truncate className="text-ink">
          {site.nats_endpoint ?? '—'}
        </Mono>
      </div>
      <MetricList>
        <MetricRow label={t('dashboard.connections')} value={formatNumber(nats.connections)} />
        {jetstream?.enabled && (
          <>
            <MetricRow label={t('dashboard.streams')} value={formatNumber(jetstream.streams)} />
            <MetricRow label={t('dashboard.messages')} value={formatNumber(jetstream.messages)} />
            <MetricRow label={t('dashboard.storage')} value={formatBytes(jetstream.store_used)} />
          </>
        )}
      </MetricList>
    </Card>
  )
}

/**
 * The fleet in one bar. Proportions are what the bar is for; the exact
 * figures stand in the rows below it, which is also what a screen reader
 * gets, so the bar itself is hidden from it.
 */
function StatusBar({
  segments,
  total,
}: {
  segments: { key: string; value: number; className: string }[]
  total: number
}) {
  return (
    <div aria-hidden className="bg-surface-2 flex h-1.5 gap-px overflow-hidden rounded-[2px]">
      {total > 0 &&
        segments
          .filter((segment) => segment.value > 0)
          .map((segment) => (
            <span
              key={segment.key}
              className={segment.className}
              style={{ width: `${(segment.value / total) * 100}%` }}
            />
          ))}
    </div>
  )
}

function ProbeCard({ dashboard }: { dashboard: Dashboard }) {
  const { t } = useTranslation()
  const total = dashboard.probe_total
  const {
    probe_healthy: healthy,
    probe_degraded: degraded,
    probe_unreachable: unreachable,
    probe_pending: pending,
  } = dashboard
  // Only when there is something to look at: a link onto an empty filtered
  // list is a worse answer than a nought.
  const linkTo = (count: number, to: string) => (count > 0 ? to : undefined)

  return (
    <Card
      title={
        <>
          {t('dashboard.probes')} <Count value={total} />
        </>
      }
      icon={OBJECT_ICONS.probe}
      action={<ShowAll to="/probes" permission="probe.read" />}
    >
      <StatusBar
        total={total}
        segments={[
          { key: 'healthy', value: healthy, className: 'bg-ok' },
          { key: 'degraded', value: degraded, className: 'bg-warn' },
          { key: 'unreachable', value: unreachable, className: 'bg-danger' },
          { key: 'pending', value: pending, className: 'bg-neutral' },
        ]}
      />
      <MetricList className="mt-3">
        <MetricRow
          tone="ok"
          label={t('dashboard.healthy')}
          value={healthy}
          to={linkTo(healthy, '/probes?status=healthy')}
        />
        <MetricRow
          tone="warn"
          label={t('dashboard.degraded')}
          value={degraded}
          to={linkTo(degraded, '/probes?status=degraded')}
        />
        <MetricRow
          tone="danger"
          label={t('dashboard.unreachable')}
          value={unreachable}
          to={linkTo(unreachable, '/probes?status=unreachable')}
        />
        {pending > 0 && (
          <MetricRow
            tone="neutral"
            label={t('dashboard.pending')}
            value={pending}
            to="/probes?status=pending&status=enrolled"
          />
        )}
      </MetricList>
      <MetricList className="border-rule mt-2 border-t pt-2">
        <MetricRow
          label={t('dashboard.withDeviations')}
          value={dashboard.probes_with_deviations}
          tone={dashboard.probes_with_deviations > 0 ? 'warn' : 'neutral'}
          to={linkTo(dashboard.probes_with_deviations, '/probes?filter=deviations')}
        />
      </MetricList>
    </Card>
  )
}

function JobCard({ dashboard }: { dashboard: Dashboard }) {
  const { t } = useTranslation()
  return (
    <Card
      title={t('nav.jobs')}
      icon={OBJECT_ICONS.job}
      action={<ShowAll to="/jobs" permission="job.read" />}
    >
      <MetricList>
        <MetricRow
          label={t('dashboard.runningJobs')}
          value={dashboard.running_jobs}
          tone={dashboard.running_jobs > 0 ? 'accent' : 'neutral'}
          to={dashboard.running_jobs > 0 ? '/jobs?status=running' : undefined}
        />
        <MetricRow
          label={t('dashboard.failedJobs')}
          value={dashboard.failed_jobs_24h}
          tone={dashboard.failed_jobs_24h > 0 ? 'danger' : 'neutral'}
          to={dashboard.failed_jobs_24h > 0 ? '/jobs?status=failed' : undefined}
        />
      </MetricList>
    </Card>
  )
}

/** One line of a compact list in a card: icon, what, when. */
const FEED_ROW = 'flex min-h-10 items-center gap-3 px-4 py-2'

/** What happened and to what: one line where there is room, the object
    under the event on a phone, where one line left room for neither. */
function FeedText({ primary, secondary }: { primary: string; secondary: string | null }) {
  return (
    <span className="min-w-0 flex-1 text-sm sm:truncate">
      <span className="text-ink block truncate sm:inline">{primary}</span>
      {secondary && (
        <span className="text-ink-3 block truncate text-xs sm:ml-2 sm:inline sm:text-sm">
          {secondary}
        </span>
      )}
    </span>
  )
}

function RecentJobs({ dashboard }: { dashboard: Dashboard }) {
  const { t } = useTranslation()
  return (
    <Card
      title={t('dashboard.recentJobs')}
      icon={OBJECT_ICONS.job}
      action={<ShowAll to="/jobs" permission="job.read" />}
      dense
    >
      {dashboard.recent_jobs.length === 0 ? (
        <EmptyState title={t('jobs.empty')} />
      ) : (
        <ul className="divide-rule divide-y">
          {dashboard.recent_jobs.map((job) => (
            <li key={job.id}>
              <Link
                to={`/jobs/${job.id}`}
                className={clsx(FEED_ROW, 'hover:bg-hover transition-colors duration-(--dur-instant)')}
              >
                <JobStatusIcon status={job.status} />
                <FeedText
                  primary={t(`jobs.types.${job.type}`, job.type)}
                  secondary={job.target_label}
                />
                <RelativeTime value={job.created_at} className="w-28 shrink-0 text-right" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function RecentActivity({ dashboard }: { dashboard: Dashboard }) {
  const { t } = useTranslation()
  return (
    <Card
      title={t('dashboard.recentActivity')}
      icon={OBJECT_ICONS.audit}
      action={<ShowAll to="/audit" permission="audit.read" />}
      dense
    >
      {dashboard.recent_audit.length === 0 ? (
        <EmptyState title={t('audit.empty')} />
      ) : (
        <ul className="divide-rule divide-y">
          {dashboard.recent_audit.map((event) => {
            const Icon = auditIcon(event.object_type, event.action)
            const failed = event.result !== 'success'
            return (
              <li key={event.id} className={FEED_ROW}>
                <Icon
                  aria-hidden
                  size={16}
                  strokeWidth={1.75}
                  className={clsx('shrink-0', failed ? 'text-danger' : 'text-ink-3')}
                />
                <FeedText
                  primary={t(`audit.actions.${event.action}`, event.action)}
                  secondary={event.object_label ?? event.object_type}
                />
                {failed && (
                  <Badge tone={event.result === 'denied' ? 'warn' : 'danger'}>
                    {t(`status.audit.${event.result}`)}
                  </Badge>
                )}
                <span className="text-ink-3 hidden w-20 shrink-0 truncate text-right text-xs sm:inline">
                  {event.actor_name}
                </span>
                <RelativeTime value={event.ts} className="w-28 shrink-0 text-right" />
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
