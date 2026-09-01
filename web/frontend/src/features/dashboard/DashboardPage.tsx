import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useDashboard, useSetupStack } from '@/api/hooks'
import type { Alert, Certificate, Dashboard } from '@/api/types'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Banner,
  Card,
  Dot,
  EmptyState,
  Label,
  Mono,
  PageHeader,
  Skeleton,
} from '@/components/ui/primitives'
import { PermissionGate } from '@/app/providers'
import { Button } from '@/components/ui/primitives'
import { CertificateStatusBadge, JobStatusBadge } from '@/components/ui/status'
import { formatBytes, formatNumber, formatRelative } from '@/utils/format'

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

  if (isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, index) => (
          <Skeleton key={index} className="h-28" />
        ))}
      </div>
    )
  }

  if (error) {
    return <ErrorDetails error={error} onRetry={() => void refetch()} />
  }
  if (!data) return null

  // Certificate warnings have their own card; showing them twice on one
  // screen reads as two problems rather than one.
  const probeAlerts = data.alerts.filter(
    (alert) => alert.object_type !== 'certificate',
  )

  const attention =
    data.probe_unreachable +
    data.probe_degraded +
    data.probe_pending +
    data.probe_prtg_missing +
    data.failed_jobs_24h +
    data.expiring_certificates.length +
    data.alerts.length

  return (
    <div className="space-y-4">
      <PageHeader title={t('dashboard.title')} subtitle={t('dashboard.question')} />

      {!data.system.site.is_configured && (
        <Banner tone="danger">{t('dashboard.notConfigured')}</Banner>
      )}
      {data.system.site.is_configured &&
        data.system.capabilities.runtime_state === 'missing' && (
          <Banner
            tone="warn"
            title={t('dashboard.setupTitle')}
            action={
              <PermissionGate permission="system.settings">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() =>
                    setup.mutate(undefined, {
                      onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                    })
                  }
                  disabled={setup.isPending}
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
      {attention === 0 && data.system.nats.healthy && (
        <Banner tone="ok">{t('dashboard.allClear')}</Banner>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <NatsCard dashboard={data} />
        <ProbeCard dashboard={data} />
        <JobCard dashboard={data} />
      </div>

      {(probeAlerts.length > 0 || data.expiring_certificates.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {/* What the card lists, not what the dashboard counts: with every
              warning a certificate one, this drew an empty card. */}
          {probeAlerts.length > 0 && (
            <Card title={t('dashboard.activeWarnings')} dense>
              <ul>
                {probeAlerts.map((alert) => (
                  <AlertRow key={alert.id} alert={alert} />
                ))}
              </ul>
            </Card>
          )}
          {data.expiring_certificates.length > 0 && (
            <Card title={t('dashboard.expiringCertificates')} dense>
              <ul>
                {data.expiring_certificates.map((certificate) => (
                  <CertificateRow key={certificate.kind} certificate={certificate} />
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('dashboard.recentJobs')} dense>
          {data.recent_jobs.length === 0 ? (
            <EmptyState title={t('jobs.empty')} />
          ) : (
            <ul>
              {data.recent_jobs.map((job) => (
                <li key={job.id} className="border-rule border-b last:border-0">
                  <Link
                    to={`/jobs/${job.id}`}
                    className="hover:bg-surface-2 flex items-center gap-3 px-4 py-2"
                  >
                    <JobStatusBadge status={job.status} />
                    <span className="text-ink min-w-0 flex-1 truncate text-sm">
                      {job.target_label ?? job.type}
                    </span>
                    <span className="text-ink-3 text-xs whitespace-nowrap">
                      {formatRelative(job.created_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title={t('dashboard.recentActivity')} dense>
          {data.recent_audit.length === 0 ? (
            <EmptyState title={t('audit.empty')} />
          ) : (
            <ul>
              {data.recent_audit.map((event) => (
                <li
                  key={event.id}
                  className="border-rule flex items-center gap-3 border-b px-4 py-2 last:border-0"
                >
                  <Dot tone={event.result === 'success' ? 'ok' : 'danger'} />
                  <span className="text-ink-2 text-sm whitespace-nowrap">
                    {t(`audit.actions.${event.action}`, event.action)}
                  </span>
                  <span className="text-ink min-w-0 flex-1 truncate text-sm">
                    {event.object_label ?? event.object_type}
                  </span>
                  <span className="text-ink-3 text-xs whitespace-nowrap">
                    {event.actor_name} · {formatRelative(event.ts)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

function Metric({
  label,
  value,
  tone,
  to,
}: {
  label: string
  value: string | number
  tone?: 'ok' | 'warn' | 'danger'
  /** Where the rows behind the number are. Omit it and the number is a
      number - which is what every one of these used to be, including the
      ones that were counting something worth going to look at. */
  to?: string
}) {
  const number = (
    <p
      className={
        tone === 'danger'
          ? 'text-danger text-xl font-semibold'
          : tone === 'warn'
            ? 'text-warn text-xl font-semibold'
            : tone === 'ok'
              ? 'text-ok text-xl font-semibold'
              : 'text-ink text-xl font-semibold'
      }
    >
      {value}
    </p>
  )
  return (
    <div>
      <Label>{label}</Label>
      {to ? (
        <Link to={to} className="rounded-inset block hover:underline">
          {number}
        </Link>
      ) : (
        number
      )}
    </div>
  )
}

function NatsCard({ dashboard }: { dashboard: Dashboard }) {
  const { t } = useTranslation()
  const { nats, site } = dashboard.system
  const jetstream = nats.jetstream

  return (
    <Card
      title={t('dashboard.natsServer')}
      action={
        <Badge tone={nats.healthy ? 'ok' : nats.available ? 'warn' : 'danger'}>
          <Dot tone={nats.healthy ? 'ok' : nats.available ? 'warn' : 'danger'} />
          {nats.healthy ? t('status.service.active') : t('status.service.inactive')}
        </Badge>
      }
    >
      <dl className="space-y-2.5">
        <div>
          <Label>{t('dashboard.endpoint')}</Label>
          <Mono truncate>{site.nats_endpoint ?? '—'}</Mono>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Metric label={t('dashboard.connections')} value={formatNumber(nats.connections)} />
          {jetstream?.enabled && (
            <Metric label={t('dashboard.streams')} value={formatNumber(jetstream.streams)} />
          )}
        </div>
        {jetstream?.enabled && (
          <div className="grid grid-cols-2 gap-3">
            <Metric
              label={t('dashboard.messages')}
              value={formatNumber(jetstream.messages)}
            />
            <Metric
              label={t('dashboard.storage')}
              value={formatBytes(jetstream.store_used)}
            />
          </div>
        )}
      </dl>
    </Card>
  )
}

function ProbeCard({ dashboard }: { dashboard: Dashboard }) {
  const { t } = useTranslation()
  return (
    <Card
      title={t('dashboard.probes')}
      action={
        <Link to="/probes" className="text-accent text-xs">
          {t('common.showAll')}
        </Link>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <Metric
          label={t('dashboard.healthy')}
          value={dashboard.probe_healthy}
          tone={dashboard.probe_healthy > 0 ? 'ok' : undefined}
        />
        <Metric
          label={t('dashboard.degraded')}
          value={dashboard.probe_degraded}
          tone={dashboard.probe_degraded > 0 ? 'warn' : undefined}
        />
        <Metric
          label={t('dashboard.unreachable')}
          value={dashboard.probe_unreachable}
          tone={dashboard.probe_unreachable > 0 ? 'danger' : undefined}
        />
        <Metric
          label={t('dashboard.withDeviations')}
          value={dashboard.probes_with_deviations}
          tone={dashboard.probes_with_deviations > 0 ? 'warn' : undefined}
          // Only when there is something to look at: a link onto an empty
          // filtered list is a worse answer than a nought.
          to={
            dashboard.probes_with_deviations > 0
              ? '/probes?filter=deviations'
              : undefined
          }
        />
        {/* The probes no other number counted: stuck mid-enrolment, or done
            here and never entered in PRTG - green on this side, invisible on
            the other. "All good" used to show over both. */}
        {dashboard.probe_pending > 0 && (
          <Metric
            label={t('dashboard.pendingEnrolled')}
            value={dashboard.probe_pending}
            tone="warn"
            to="/probes"
          />
        )}
        {dashboard.probe_prtg_missing > 0 && (
          <Metric
            label={t('dashboard.prtgMissing')}
            value={dashboard.probe_prtg_missing}
            tone="warn"
            to="/probes"
          />
        )}
      </div>
    </Card>
  )
}

function JobCard({ dashboard }: { dashboard: Dashboard }) {
  const { t } = useTranslation()
  return (
    <Card
      title={t('nav.jobs')}
      action={
        <Link to="/jobs" className="text-accent text-xs">
          {t('common.showAll')}
        </Link>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <Metric
          label={t('dashboard.runningJobs')}
          value={dashboard.running_jobs}
          to={dashboard.running_jobs > 0 ? '/jobs?status=running' : undefined}
        />
        <Metric
          label={t('dashboard.failedJobs')}
          value={dashboard.failed_jobs_24h}
          tone={dashboard.failed_jobs_24h > 0 ? 'danger' : undefined}
          to={dashboard.failed_jobs_24h > 0 ? '/jobs?status=failed' : undefined}
        />
      </div>
    </Card>
  )
}

function AlertRow({ alert }: { alert: Alert }) {
  const { t } = useTranslation()
  return (
    <li className="border-rule flex items-center gap-3 border-b px-4 py-2 last:border-0">
      <Dot tone={alert.severity === 'critical' ? 'danger' : 'warn'} />
      <span className="text-ink min-w-0 flex-1 truncate text-sm">
        {t(`alerts.${alert.kind}`, {
          ...alert.params,
          probe: alert.object_label,
          certificate: alert.object_label,
          defaultValue: alert.kind,
        })}
      </span>
      <span className="text-ink-3 text-xs whitespace-nowrap">
        {formatRelative(alert.first_seen_at)}
      </span>
    </li>
  )
}

function CertificateRow({ certificate }: { certificate: Certificate }) {
  const { t } = useTranslation()
  return (
    <li className="border-rule flex items-center gap-3 border-b px-4 py-2 last:border-0">
      <CertificateStatusBadge status={certificate.status} />
      <span className="text-ink flex-1 text-sm">{certificate.subject ?? certificate.kind}</span>
      <span className="text-ink-3 text-xs whitespace-nowrap">
        {certificate.days_remaining !== null && certificate.days_remaining >= 0
          ? t('infrastructure.expiresIn', { count: certificate.days_remaining })
          : t('infrastructure.expired')}
      </span>
    </li>
  )
}
