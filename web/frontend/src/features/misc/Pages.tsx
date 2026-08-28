import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useAuditEvents,
  useCapabilities,
  useCertificates,
  useDeployments,
  useSystemStatus,
} from '@/api/hooks'
import type {
  AuditEvent,
  Certificate,
  Deployment,
  DeploymentTarget,
} from '@/api/types'
import { PermissionGate, useAuth, useTheme } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Button,
  Card,
  DetailRow,
  Dot,
  EmptyState,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { CertificateStatusBadge, JobStatusBadge } from '@/components/ui/status'
import {
  LANGUAGE_LABELS,
  SUPPORTED_LANGUAGES,
  changeLanguage,
  currentLanguage,
  type Language,
} from '@/i18n'
import { DeployDialog } from '@/features/deployments/DeployDialog'
import { UsersCard } from '@/features/settings/UsersCard'
import { formatBytes, formatDateTime, formatRelative } from '@/utils/format'

// --- Deployments ------------------------------------------------------------

export function DeploymentListPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useDeployments()
  const [deploying, setDeploying] = useState(false)

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const columns: Column<Deployment>[] = [
    {
      key: 'sensor',
      header: t('deployments.columns.sensor'),
      sortValue: (row) => row.sensor_name,
      searchValue: (row) => row.sensor_name,
      cell: (row) => (
        <span className="text-ink font-medium">
          {row.sensor_name} <Mono className="text-ink-3">v{row.sensor_version}</Mono>
        </span>
      ),
    },
    {
      key: 'status',
      header: t('deployments.columns.status'),
      sortValue: (row) => row.status,
      cell: (row) => (
        <div className="flex items-center gap-2">
          <JobStatusBadge status={row.status} />
          {row.dry_run && <Badge tone="neutral">{t('deployments.dryRun')}</Badge>}
        </div>
      ),
    },
    {
      key: 'targets',
      header: t('deployments.columns.targets'),
      cell: (row) => {
        const succeeded = row.targets.filter((target) => target.status === 'successful')
        return (
          <span className="text-ink-2 text-sm">
            {t('deployments.summary', {
              succeeded: succeeded.length,
              total: row.targets.length,
            })}
          </span>
        )
      },
    },
    {
      key: 'started',
      header: t('deployments.columns.started'),
      sortValue: (row) => row.created_at,
      cell: (row) => (
        <span className="text-ink-3 text-xs">{formatRelative(row.created_at)}</span>
      ),
    },
    {
      key: 'by',
      header: t('deployments.columns.by'),
      cell: (row) => (
        <span className="text-ink-3 text-xs">{row.requested_by_name ?? '—'}</span>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg">{t('deployments.title')}</h1>
        <p className="text-ink-3 text-sm">{t('deployments.subtitle')}</p>
        {/* A rollout used to be startable from a sensor's page or from the
            selection in the probe list, but not from the page that lists
            every rollout there has been. */}
        <div className="ml-auto">
          <PermissionGate permission="deployment.create">
            <Button variant="primary" size="sm" onClick={() => setDeploying(true)}>
              {t('deployments.create')}
            </Button>
          </PermissionGate>
        </div>
      </header>
      <DataTable
        rows={data}
        columns={columns}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        emptyTitle={t('deployments.empty')}
        rowHref={(row) => (row.job_id ? `/jobs/${row.job_id}` : null)}
        expandedContent={(row) => <DeploymentTargets targets={row.targets} />}
        emptyAction={
          <PermissionGate permission="deployment.create">
            <Button variant="primary" onClick={() => setDeploying(true)}>
              {t('deployments.create')}
            </Button>
          </PermissionGate>
        }
      />

      {deploying && (
        <DeployDialog
          onClose={() => setDeploying(false)}
          onDone={() => setDeploying(false)}
        />
      )}
    </div>
  )
}

/**
 * What happened on each probe of one rollout.
 *
 * The row above says "3 of 5 succeeded", which is the wrong half of the
 * answer: the two that did not are the reason anybody opened this page. Every
 * field here was recorded per target when the job ran and had nowhere to go.
 */
function DeploymentTargets({ targets }: { targets: DeploymentTarget[] }) {
  const { t } = useTranslation()

  if (targets.length === 0) {
    return <p className="text-ink-3 text-sm">{t('deployments.noTargets')}</p>
  }

  return (
    <ul className="space-y-2">
      {targets.map((target) => (
        <li key={target.probe_id} className="text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <JobStatusBadge status={target.status} />
            <Mono className="text-ink-2">{target.probe_label}</Mono>
            {target.error_code && (
              // The label fills the {{probe}} the probe-side messages carry -
              // the target row is the one thing this record knows for certain.
              // A code with no message of its own falls back to the code, which
              // is still something to search for.
              <span className="text-danger text-xs">
                {t(`errors.${target.error_code}`, {
                  probe: target.probe_label,
                  defaultValue: target.error_code,
                })}
              </span>
            )}
            {target.finished_at && (
              <span className="text-ink-3 ml-auto text-xs">
                {formatRelative(target.finished_at)}
              </span>
            )}
          </div>
          {/* The machine's own words, never translated - the same rule the
              error panel follows. */}
          {target.error_details && (
            <pre className="text-ink-3 mt-1 max-h-24 overflow-auto font-mono text-xs whitespace-pre-wrap">
              {target.error_details}
            </pre>
          )}
        </li>
      ))}
    </ul>
  )
}

// --- Audit ------------------------------------------------------------------

export function AuditPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useAuditEvents()

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const columns: Column<AuditEvent>[] = [
    {
      key: 'time',
      header: t('audit.columns.time'),
      sortValue: (row) => row.ts,
      cell: (row) => (
        <span className="text-ink-2 text-xs whitespace-nowrap">
          {formatDateTime(row.ts)}
        </span>
      ),
    },
    {
      key: 'actor',
      header: t('audit.columns.actor'),
      sortValue: (row) => row.actor_name,
      searchValue: (row) => row.actor_name,
      cell: (row) => <span className="text-ink text-sm">{row.actor_name}</span>,
    },
    {
      key: 'action',
      header: t('audit.columns.action'),
      sortValue: (row) => row.action,
      searchValue: (row) => row.action,
      cell: (row) => <Mono>{row.action}</Mono>,
    },
    {
      key: 'object',
      header: t('audit.columns.object'),
      searchValue: (row) => `${row.object_type} ${row.object_label ?? ''}`,
      cell: (row) => (
        <span className="text-ink-2 text-sm">
          {row.object_label ?? row.object_type}
        </span>
      ),
    },
    {
      key: 'result',
      header: t('audit.columns.result'),
      sortValue: (row) => row.result,
      cell: (row) => (
        <span className="inline-flex items-center gap-1.5 text-sm">
          <Dot tone={row.result === 'success' ? 'ok' : row.result === 'denied' ? 'warn' : 'danger'} />
          {t(`status.audit.${row.result}`)}
        </span>
      ),
    },
    {
      key: 'source',
      header: t('audit.columns.source'),
      cell: (row) => <Mono className="text-ink-3">{row.source_ip ?? '—'}</Mono>,
    },
  ]

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg">{t('audit.title')}</h1>
        <p className="text-ink-3 text-sm">{t('audit.subtitle')}</p>
      </header>
      <DataTable
        rows={data}
        columns={columns}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        emptyTitle={t('audit.empty')}
      />
      <p className="text-ink-3 text-xs">{t('audit.immutable')}</p>
    </div>
  )
}

// --- Infrastructure ---------------------------------------------------------

export function NatsPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useSystemStatus()

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const { nats, site, containers } = data

  return (
    <div className="space-y-4">
      <h1 className="text-lg">{t('infrastructure.natsTitle')}</h1>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('dashboard.natsServer')}>
          <dl>
            <DetailRow label={t('dashboard.endpoint')}>
              <Mono truncate>{site.nats_endpoint ?? '—'}</Mono>
            </DetailRow>
            <DetailRow label="server">{nats.server_name ?? '—'}</DetailRow>
            <DetailRow label="version">
              <Mono>{nats.version ?? '—'}</Mono>
            </DetailRow>
            <DetailRow label="uptime">{nats.uptime ?? '—'}</DetailRow>
            <DetailRow label={t('dashboard.connections')}>{nats.connections}</DetailRow>
            <DetailRow label={t('dashboard.coreAddress')}>
              <Mono>{site.prtg_core_ip ?? '—'}</Mono>
            </DetailRow>
          </dl>
        </Card>

        <Card title={t('dashboard.jetstream')}>
          {nats.jetstream?.enabled ? (
            <dl>
              <DetailRow label={t('dashboard.streams')}>
                {nats.jetstream.streams}
              </DetailRow>
              <DetailRow label="consumers">{nats.jetstream.consumers}</DetailRow>
              <DetailRow label={t('dashboard.messages')}>
                {nats.jetstream.messages}
              </DetailRow>
              <DetailRow label={t('dashboard.storage')}>
                {formatBytes(nats.jetstream.store_used)}
              </DetailRow>
            </dl>
          ) : (
            <EmptyState title={t('common.none')} />
          )}
        </Card>
      </div>

      <Card title={t('infrastructure.containers')} dense>
        {containers.length === 0 && (
          <div className="px-4 py-3">
            <EmptyState title={t('infrastructure.containersEmpty')} />
          </div>
        )}
        <ul>
          {containers.map((container) => (
            <li
              key={container.name}
              className="border-rule flex items-center gap-3 border-b px-4 py-2 last:border-0"
            >
              <Dot tone={container.running ? 'ok' : 'danger'} />
              <Mono className="text-ink flex-1">{container.name}</Mono>
              <span className="text-ink-3 text-xs">
                {container.status ?? t('common.unknown')}
                {container.health ? ` · ${container.health}` : ''}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

export function CertificatesPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useCertificates()

  if (isLoading) return <Skeleton className="h-48" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  return (
    <div className="space-y-4">
      <h1 className="text-lg">{t('infrastructure.certificatesTitle')}</h1>
      {data && data.length === 0 ? (
        <Card>
          <EmptyState title={t('infrastructure.certificatesEmpty')} />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {data?.map((certificate) => (
            <CertificateCard key={certificate.kind} certificate={certificate} />
          ))}
        </div>
      )}
    </div>
  )
}

function CertificateCard({ certificate }: { certificate: Certificate }) {
  const { t } = useTranslation()
  return (
    <Card
      title={certificate.kind.toUpperCase()}
      action={<CertificateStatusBadge status={certificate.status} />}
    >
      <dl>
        <DetailRow label="subject">
          <Mono truncate>{certificate.subject ?? '—'}</Mono>
        </DetailRow>
        <DetailRow label="issuer">
          <Mono truncate>{certificate.issuer ?? '—'}</Mono>
        </DetailRow>
        <DetailRow label="not after">
          {formatDateTime(certificate.not_after)}
          {certificate.days_remaining !== null && (
            <span className="text-ink-3 ml-2 text-xs">
              {certificate.days_remaining >= 0
                ? t('infrastructure.expiresIn', { count: certificate.days_remaining })
                : t('infrastructure.expired')}
            </span>
          )}
        </DetailRow>
        <DetailRow label={t('infrastructure.fingerprint')}>
          <Mono truncate>{certificate.sha256 ?? '—'}</Mono>
        </DetailRow>
        {certificate.subject_alt_names.length > 0 && (
          <DetailRow label={t('infrastructure.subjectAltNames')}>
            <Mono>{certificate.subject_alt_names.join(', ')}</Mono>
          </DetailRow>
        )}
      </dl>
      {certificate.key_matches === false && (
        <p className="text-danger mt-3 text-sm">{t('infrastructure.keyMismatch')}</p>
      )}
    </Card>
  )
}

// --- Settings ---------------------------------------------------------------

export function SettingsPage() {
  const { t } = useTranslation()
  const { principal } = useAuth()
  const { choice, setChoice } = useTheme()
  const { data: capabilities } = useCapabilities()

  return (
    <div className="space-y-4">
      <h1 className="text-lg">{t('settings.title')}</h1>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('settings.general')}>
          <dl>
            <DetailRow label={t('settings.language')}>
              <select
                value={currentLanguage()}
                onChange={(event) => changeLanguage(event.target.value as Language)}
                className="rounded-control border-rule-2 bg-surface text-ink border px-2 py-1 text-sm"
              >
                {SUPPORTED_LANGUAGES.map((language) => (
                  <option key={language} value={language}>
                    {LANGUAGE_LABELS[language]}
                  </option>
                ))}
              </select>
              <p className="text-ink-3 mt-1 text-xs">{t('settings.languageHint')}</p>
            </DetailRow>
            <DetailRow label={t('settings.theme')}>
              <select
                value={choice}
                onChange={(event) =>
                  setChoice(event.target.value as 'light' | 'dark' | 'system')
                }
                className="rounded-control border-rule-2 bg-surface text-ink border px-2 py-1 text-sm"
              >
                <option value="system">{t('settings.themeSystem')}</option>
                <option value="light">{t('settings.themeLight')}</option>
                <option value="dark">{t('settings.themeDark')}</option>
              </select>
            </DetailRow>
          </dl>
        </Card>

        <Card title={t('settings.capabilities')}>
          <dl>
            <DetailRow label="Docker">
              <span className="inline-flex items-center gap-2">
                <Dot tone={capabilities?.docker ? 'ok' : 'neutral'} />
                {capabilities?.docker
                  ? t('settings.dockerAvailable')
                  : t('settings.dockerUnavailable')}
              </span>
            </DetailRow>
            <DetailRow label={t('settings.runtimeState')}>
              <Mono>{capabilities?.runtime_state ?? '—'}</Mono>
            </DetailRow>
          </dl>
        </Card>
      </div>

      <PermissionGate permission="user.manage">
        <UsersCard />
      </PermissionGate>

      {principal && (
        <Card title={t('settings.roles')}>
          <dl>
            <DetailRow label={t('auth.username')}>
              <Mono>{principal.username}</Mono>
            </DetailRow>
            <DetailRow label={t('settings.role')}>
              <span className="flex flex-wrap gap-1">
                {principal.roles.map((role) => (
                  <Badge key={role} tone="accent">
                    {t(`roles.${role}`, role)}
                  </Badge>
                ))}
              </span>
            </DetailRow>
          </dl>
          <ul className="mt-3 space-y-1">
            {principal.roles.map((role) => (
              <li key={role} className="text-ink-3 text-sm">
                {t(`roles.${role}.description`, '')}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

export function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <Card>
      <EmptyState
        title={t('errors.common.not_found', { resource: 'Page', id: location.pathname })}
        action={
          <Link to="/" className="text-accent text-sm">
            {t('nav.dashboard')}
          </Link>
        }
      />
    </Card>
  )
}
