import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useInvitations, useProbes } from '@/api/hooks'
import type { ApiError } from '@/api/client'
import type { ProbeSummary } from '@/api/types'
import { PermissionGate, useAuth } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Badge, Button, Mono } from '@/components/ui/primitives'
import { ProbeStatusBadge } from '@/components/ui/status'
import { formatRelative } from '@/utils/format'

import { DeployDialog } from '../deployments/DeployDialog'
import { FleetActionBar } from './FleetActions'

/**
 * The fleet, one row per probe.
 *
 * Service, CA and NATS had columns of their own and mostly repeated what the
 * status badge already says - ten columns plus the checkbox, scrolling
 * sideways on a 1440px screen. They are on the probe's own page, which is
 * where somebody goes once the badge has told them there is something to look
 * at. What stays here is what tells rows apart.
 */
/**
 * Invitations that are out live on the wizard page; this is the one line that
 * says they exist. Without it, a closed tab meant an open invitation nobody
 * remembered.
 */
function OpenInvitationsHint() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { data } = useInvitations(can('probe.create'))

  if (!data || data.length === 0) return null
  return (
    <Link to="/probes/new" className="text-accent text-sm hover:underline">
      {t('probes.openInvitations', { count: data.length })}
    </Link>
  )
}

export function ProbeListPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useProbes()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deploying, setDeploying] = useState(false)
  // The two questions this list gets asked that search cannot answer: which
  // probes are due a helper, and which ones drifted. Both are columns already;
  // the filters only save clicking them together by hand.
  //
  // In the URL rather than in state, so the dashboard can ask one of them on
  // somebody's behalf - "probes with deviations: 3" is a link now, not a
  // label - and so the filtered list survives a reload and can be sent to a
  // colleague.
  const [params, setParams] = useSearchParams()
  const active = new Set(params.getAll('filter'))
  const onlyHelperOutdated = active.has('helper')
  const onlyDeviations = active.has('deviations')
  const [actionError, setActionError] = useState<ApiError | Error | null>(null)

  function toggleFilter(name: 'helper' | 'deviations') {
    const next = new Set(active)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    setParams(
      [...next].map((entry): [string, string] => ['filter', entry]),
      { replace: true },
    )
  }

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const rows = (data ?? []).filter(
    (row) =>
      (!onlyHelperOutdated || row.helper_outdated) &&
      (!onlyDeviations || row.deviation_count > 0),
  )
  // Out of the full list, not out of the filtered one: a probe that was picked
  // before a filter was set is still picked, and the confirmation names it.
  const selectedProbes = (data ?? []).filter((row) => selected.has(row.id))
  const filtered = onlyHelperOutdated || onlyDeviations

  function clearFilters() {
    setParams([], { replace: true })
  }

  // Carried into every row link and handed back by the detail page's own way
  // out. Without it, opening a probe from a filtered, searched list and
  // stepping back lands on the unfiltered fleet with the search box empty.
  const listSearch = params.toString()

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
          {/* Beside the status rather than in its own column: it decides
              whether the next job on this probe will run at all. */}
          {row.helper_outdated && <Badge tone="warn">{t('probes.helperOutdated')}</Badge>}
          {/* Green here and invisible in PRTG is exactly what the status
              colour hides; a pending probe has an earlier problem. */}
          {!row.prtg_registered &&
            row.status !== 'pending' &&
            row.status !== 'enrolled' && (
              <Badge tone="warn">{t('probes.prtg.missingBadge')}</Badge>
            )}
        </div>
      ),
    },
    {
      key: 'version',
      header: t('probes.columns.version'),
      sortValue: (row) => row.package_version ?? '',
      cell: (row) => <Mono>{row.package_version ?? '—'}</Mono>,
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
        <div className="ml-auto flex items-baseline gap-3">
          <OpenInvitationsHint />
          <PermissionGate permission="probe.create">
            <Button variant="primary" size="sm" onClick={() => navigate('/probes/new')}>
              {t('probes.enroll.action')}
            </Button>
          </PermissionGate>
        </div>
      </header>

      {/* No retry button: what failed was an action on a selection that is
          still selected, so pressing it again is the button in the bar. It
          clears itself when the next one starts. */}
      {actionError && <ErrorDetails error={actionError} />}

      <DataTable
        rows={data ? rows : undefined}
        columns={columns}
        rowKey={(row) => row.id}
        isLoading={isLoading}
        // A filter that matches nothing is not an empty fleet, and telling
        // somebody to enrol their first probe when they have twelve is how an
        // empty state stops being read at all.
        emptyTitle={filtered ? t('probes.filters.empty') : t('probes.empty')}
        emptyHint={filtered ? undefined : t('probes.emptyHint')}
        // An empty fleet is where someone is looking for exactly this.
        emptyAction={
          filtered ? (
            <Button variant="secondary" onClick={clearFilters}>
              {t('common.clearFilters')}
            </Button>
          ) : (
            <PermissionGate permission="probe.create">
              <Button variant="primary" onClick={() => navigate('/probes/new')}>
                {t('probes.enroll.action')}
              </Button>
            </PermissionGate>
          )
        }
        rowHref={(row) => `/probes/${row.id}${listSearch ? `?${listSearch}` : ''}`}
        searchParamKey="q"
        filters={
          <div className="flex flex-wrap items-center gap-2">
            <FilterToggle
              label={t('probes.filters.helperOutdated')}
              active={onlyHelperOutdated}
              onToggle={() => toggleFilter('helper')}
            />
            <FilterToggle
              label={t('probes.filters.deviations')}
              active={onlyDeviations}
              onToggle={() => toggleFilter('deviations')}
            />
            {filtered && (
              <Button size="sm" variant="ghost" onClick={clearFilters}>
                {t('common.clearFilters')}
              </Button>
            )}
          </div>
        }
        selection={{
          selected,
          onChange: setSelected,
          actions: (
            <>
              <PermissionGate permission="deployment.create">
                <Button size="sm" variant="primary" onClick={() => setDeploying(true)}>
                  {t('sensors.deploy')}
                </Button>
              </PermissionGate>
              <FleetActionBar
                probes={selectedProbes}
                onError={setActionError}
                onDone={() => setSelected(new Set())}
              />
            </>
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

function FilterToggle({
  label,
  active,
  onToggle,
}: {
  label: string
  active: boolean
  onToggle: () => void
}) {
  return (
    <Button
      size="sm"
      variant={active ? 'primary' : 'ghost'}
      aria-pressed={active}
      onClick={onToggle}
    >
      {label}
    </Button>
  )
}
