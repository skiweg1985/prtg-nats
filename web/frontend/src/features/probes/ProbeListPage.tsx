import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useProbes } from '@/api/hooks'
import type { ProbeSummary } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Badge, Button, Mono } from '@/components/ui/primitives'
import { ProbeStatusBadge, StateCell } from '@/components/ui/status'
import { formatRelative } from '@/utils/format'

import { DeployDialog } from '../deployments/DeployDialog'

export function ProbeListPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useProbes()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deploying, setDeploying] = useState(false)

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const columns: Column<ProbeSummary>[] = [
    {
      key: 'name',
      header: t('probes.columns.name'),
      sortValue: (row) => row.display_name ?? row.nats_username,
      searchValue: (row) =>
        [row.nats_username, row.display_name, row.probe_name, row.host]
          .filter(Boolean)
          .join(' '),
      cell: (row) => (
        <div className="min-w-0">
          <p className="text-ink truncate font-medium">
            {row.display_name ?? row.probe_name ?? row.nats_username}
          </p>
          <Mono className="text-ink-3">{row.nats_username}</Mono>
        </div>
      ),
    },
    {
      key: 'host',
      header: t('probes.columns.host'),
      sortValue: (row) => row.host,
      searchValue: (row) => row.host,
      cell: (row) => <Mono truncate>{row.host}</Mono>,
    },
    {
      key: 'status',
      header: t('probes.columns.status'),
      sortValue: (row) => row.status,
      cell: (row) => (
        <div className="flex items-center gap-2">
          <ProbeStatusBadge status={row.status} />
          {row.running_job_id && <Badge tone="accent">{t('status.job.running')}</Badge>}
        </div>
      ),
    },
    {
      key: 'service',
      header: t('probes.columns.service'),
      sortValue: (row) => row.service,
      cell: (row) => <StateCell kind="service" value={row.service} />,
    },
    {
      key: 'version',
      header: t('probes.columns.version'),
      sortValue: (row) => row.package_version ?? '',
      cell: (row) => <Mono>{row.package_version ?? '—'}</Mono>,
    },
    {
      key: 'ca',
      header: t('probes.columns.ca'),
      sortValue: (row) => row.ca_state,
      cell: (row) => <StateCell kind="ca" value={row.ca_state} />,
    },
    {
      key: 'nats',
      header: t('probes.columns.nats'),
      sortValue: (row) => row.nats_connection,
      cell: (row) => <StateCell kind="nats" value={row.nats_connection} />,
    },
    {
      key: 'sensors',
      header: t('probes.columns.sensors'),
      align: 'right',
      sortValue: (row) => row.sensor_count,
      cell: (row) => <span className="text-sm">{row.sensor_count}</span>,
    },
    {
      key: 'deviations',
      header: t('probes.columns.deviations'),
      align: 'right',
      sortValue: (row) => row.deviation_count,
      cell: (row) =>
        row.deviation_count > 0 ? (
          <Badge tone="warn">{row.deviation_count}</Badge>
        ) : (
          <span className="text-ink-3 text-sm">0</span>
        ),
    },
    {
      key: 'observed',
      header: t('probes.columns.observed'),
      align: 'right',
      sortValue: (row) => row.observed_at ?? '',
      cell: (row) => (
        // A cached value is shown as cached. The alternative is a table that
        // looks current and is not, which is the failure this platform exists
        // to prevent.
        <span className={row.stale ? 'text-ink-3 text-xs' : 'text-ink-2 text-xs'}>
          {row.observed_at ? formatRelative(row.observed_at) : t('common.never')}
        </span>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg">{t('probes.title')}</h1>
        <p className="text-ink-3 text-sm">{t('probes.subtitle')}</p>
      </header>

      <DataTable
        rows={data}
        columns={columns}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        emptyTitle={t('probes.empty')}
        emptyHint={t('probes.emptyHint')}
        onRowClick={(row) => navigate(`/probes/${row.id}`)}
        selection={{
          selected,
          onChange: setSelected,
          actions: (
            <PermissionGate permission="deployment.create">
              <Button size="sm" variant="primary" onClick={() => setDeploying(true)}>
                {t('sensors.deploy')}
              </Button>
            </PermissionGate>
          ),
        }}
      />

      {deploying && (
        <DeployDialog
          probeIds={[...selected]}
          onClose={() => setDeploying(false)}
          onDone={() => {
            setDeploying(false)
            setSelected(new Set())
          }}
        />
      )}
    </div>
  )
}
