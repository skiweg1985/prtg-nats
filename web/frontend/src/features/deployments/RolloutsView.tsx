/**
 * The rollout history, lived inside the jobs page as its own view.
 *
 * This was a page of its own, which meant a second navigation entry for what
 * is a filtered look at the same history the jobs page shows - with the one
 * thing the jobs list lacks, the per-target outcome, hidden behind it. Now
 * "Rollouts" is a view on the jobs page and /deployments redirects here.
 */

import { Link } from 'react-router-dom'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useDeployments } from '@/api/hooks'
import type { Deployment, DeploymentTarget } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Badge, Button, Mono } from '@/components/ui/primitives'
import { JobStatusBadge } from '@/components/ui/status'
import { formatRelative } from '@/utils/format'

import { DeployDialog } from './DeployDialog'

// --- Deployments ------------------------------------------------------------

export function RolloutsView() {
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
      cell: (row) =>
        row.dry_run ? (
          // "Successful" on a dry run reads as a rollout that happened; what
          // succeeded was only the rehearsal.
          <Badge tone="neutral">{t('deployments.dryRunBadge')}</Badge>
        ) : (
          <JobStatusBadge status={row.status} />
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
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-ink-3 text-sm">{t('deployments.subtitle')}</p>
        <PermissionGate permission="deployment.create">
          <Button variant="primary" size="sm" onClick={() => setDeploying(true)}>
            {t('deployments.create')}
          </Button>
        </PermissionGate>
      </div>
      <DataTable
        rows={data}
        columns={columns}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        emptyTitle={t('deployments.empty')}
        rowHref={(row) => (row.job_id ? `/jobs/${row.job_id}` : null)}
        expandedContent={(row) => (
          <DeploymentTargets
            sensor={row.sensor_name}
            version={row.sensor_version}
            targets={row.targets}
          />
        )}
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
function DeploymentTargets({
  sensor,
  version,
  targets,
}: {
  sensor: string
  version: string
  targets: DeploymentTarget[]
}) {
  const { t } = useTranslation()

  if (targets.length === 0) {
    return <p className="text-ink-3 text-sm">{t('deployments.noTargets')}</p>
  }

  return (
    <div className="space-y-2">
      {/* The row itself leads to the job; the way to the sensor lives here,
          where somebody is already digging into one rollout. */}
      <Link
        to={`/sensors/${sensor}`}
        className="text-accent text-xs hover:underline"
      >
        {t('deployments.toSensor', { sensor })}
      </Link>
      <ul className="space-y-2">
        {targets.map((target) => (
          <li key={target.probe_id} className="text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <JobStatusBadge status={target.status} />
              <Mono className="text-ink-2">{target.probe_label}</Mono>
              {/* Was v3, is v4 - the column existed since the initial schema
                  and is finally written by the worker. */}
              {target.status === 'successful' && (
                <span className="text-ink-3 text-xs">
                  {target.previous_version
                    ? t('deployments.versionChange', {
                        from: target.previous_version,
                        to: version,
                      })
                    : `v${version}`}
                </span>
              )}
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
    </div>
  )
}

