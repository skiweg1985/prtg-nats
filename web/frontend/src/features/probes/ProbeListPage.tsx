import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Plus, Upload } from 'lucide-react'

import { useInvitations, useProbes, usePrtgProbeNames } from '@/api/hooks'
import type { ApiError } from '@/api/client'
import type { ProbeStatus, ProbeSummary } from '@/api/types'
import { PermissionGate, useAuth } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Button,
  ButtonLink,
  FilterChip,
  Mono,
  PageHeader,
} from '@/components/ui/primitives'
import { ProbeStatusText } from '@/components/ui/status'
import { formatRelative } from '@/utils/format'
import { useUrlState } from '@/utils/urlState'

import { DeployDialog } from '../deployments/DeployDialog'
import { FleetActionBar } from './FleetActions'
import { PackageUpdateBadge } from './PackageUpdateBadge'
import { probeName } from './probeName'

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
    <ButtonLink to="/probes/new">
      {t('probes.openInvitations', { count: data.length })}
    </ButtonLink>
  )
}

/**
 * Worst first. The list opens in this order because the question it is opened
 * with is "what is broken", and an alphabetical fleet answers it only for
 * somebody who reads every row.
 */
// Cleared by the table's reset together with the search term.
const PROBE_FILTER_KEYS = ['filter', 'status'] as const

const STATUS_ORDER: ProbeStatus[] = [
  'unreachable',
  'degraded',
  'pending',
  'enrolled',
  'healthy',
]

// Only the two states somebody has to act on get a dot; colouring all five
// would make the chips a legend instead of a warning.
const STATUS_DOT: Partial<Record<ProbeStatus, string>> = {
  unreachable: 'bg-danger',
  degraded: 'bg-warn',
}

/** The same rule as the dashboard's count: a pending probe has an earlier
    problem than PRTG, so it is not counted here. */
function isPrtgMissing(row: ProbeSummary): boolean {
  return !row.prtg_registered && row.status !== 'pending' && row.status !== 'enrolled'
}

function isProbeStatus(value: string): value is ProbeStatus {
  return (STATUS_ORDER as string[]).includes(value)
}

export function ProbeListPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useProbes()
  // Asked beside the list, not with it: a slow PRTG core must not hold up
  // the fleet. Without a token the answer is empty and the list unchanged.
  const { data: prtgNames } = usePrtgProbeNames()
  const prtgNameOf = new Map(
    (prtgNames?.names ?? []).map((entry) => [entry.nats_username, entry]),
  )
  // One name per probe, by the same rule as the probe page.
  const nameOf = (row: ProbeSummary) =>
    probeName(row, prtgNameOf.get(row.nats_username)?.name_in_prtg)
  const prtgMissing =
    prtgNames && ['unreachable', 'rejected'].includes(prtgNames.state)
      ? prtgNames.state
      : null
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
  const { params, update } = useUrlState()
  const active = new Set(params.getAll('filter'))
  const onlyHelperOutdated = active.has('helper')
  const onlyDeviations = active.has('deviations')
  const onlyPrtgMissing = active.has('prtg')
  const statuses = new Set(params.getAll('status').filter(isProbeStatus))
  const [actionError, setActionError] = useState<ApiError | Error | null>(null)

  // Every filter writes only its own parameter. Rebuilding the query from the
  // filters alone used to drop the search term the moment a chip was pressed.
  function setParamValues(key: string, values: Iterable<string>) {
    update({ [key]: [...values] })
  }

  function toggleIn<V extends string>(set: Set<V>, value: V): Set<V> {
    const next = new Set(set)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    return next
  }

  function toggleFilter(name: 'helper' | 'deviations' | 'prtg') {
    setParamValues('filter', toggleIn(active, name))
  }

  function toggleStatus(status: ProbeStatus) {
    setParamValues('status', toggleIn(statuses, status))
  }

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const rows = (data ?? []).filter(
    (row) =>
      (!onlyHelperOutdated || row.helper_outdated) &&
      (!onlyDeviations || row.deviation_count > 0) &&
      (!onlyPrtgMissing || isPrtgMissing(row)) &&
      (statuses.size === 0 || statuses.has(row.status)),
  )
  // Counted over the whole fleet, so a chip keeps its number while another
  // filter is on and says what pressing it would add.
  const statusCounts = new Map<ProbeStatus, number>()
  for (const row of data ?? []) {
    statusCounts.set(row.status, (statusCounts.get(row.status) ?? 0) + 1)
  }
  // Out of the full list, not out of the filtered one: a probe that was picked
  // before a filter was set is still picked, and the confirmation names it.
  const selectedProbes = (data ?? []).filter((row) => selected.has(row.id))
  const filtered =
    onlyHelperOutdated || onlyDeviations || onlyPrtgMissing || statuses.size > 0

  // The empty state's way out; the toolbar has the table's own reset, which
  // takes the search term along.
  function clearFilters() {
    update({ filter: null, status: null })
  }

  const columns: Column<ProbeSummary>[] = [
    {
      key: 'name',
      header: t('probes.columns.name'),
      sortValue: (row) => nameOf(row),
      searchValue: (row) =>
        [
          row.nats_username,
          row.display_name,
          row.probe_name,
          prtgNameOf.get(row.nats_username)?.name_in_prtg,
          row.host,
        ]
          .filter(Boolean)
          .join(' '),
      cell: (row) => (
        <div className="min-w-0">
          <p className="text-ink truncate font-medium">{nameOf(row)}</p>
          <span className="flex items-center gap-2">
            {/* The account only when it says something the name does not -
                for a probe without any other name it is the name. */}
            {nameOf(row) !== row.nats_username && (
              <Mono className="text-ink-2">{row.nats_username}</Mono>
            )}
            {/* Only the tunnel is marked: the site address is the default
                route, and marking every probe would mark none. */}
            {row.managed_over_tunnel && (
              <span title={t('probes.routes.managedOverTunnel')}>
                <Badge tone="neutral">{t('probes.routes.tunnel')}</Badge>
              </span>
            )}
          </span>
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
      // By severity, not by the alphabet of the translated label; the name
      // keeps probes of one status in a stable order.
      sortValue: (row) =>
        `${STATUS_ORDER.indexOf(row.status)} ${nameOf(row).toLowerCase()}`,
      cell: (row) => <StatusBadges row={row} />,
    },
    {
      key: 'version',
      header: t('probes.columns.version'),
      sortValue: (row) => row.package_version ?? '',
      cell: (row) => (
        <span className="flex flex-wrap items-center gap-2">
          <Mono className="whitespace-nowrap">{row.package_version ?? '—'}</Mono>
          <PackageUpdateBadge version={row.package_update} />
        </span>
      ),
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
      <PageHeader
        title={t('probes.title')}
        subtitle={t('probes.subtitle')}
        action={
          <>
            <OpenInvitationsHint />
            <PermissionGate permission="probe.create">
              <Button
                variant="primary"
                size="sm"
                icon={Plus}
                onClick={() => navigate('/probes/new')}
              >
                {t('probes.enroll.action')}
              </Button>
            </PermissionGate>
          </>
        }
      />

      {/* No retry button: what failed was an action on a selection that is
          still selected, so pressing it again is the button in the bar. It
          clears itself when the next one starts. */}
      {actionError && <ErrorDetails error={actionError} />}

      {prtgMissing && (
        <p className="text-ink-3 text-sm">
          {t('probes.prtgName.listMissing', {
            reason: t(`probes.prtgName.reasons.${prtgMissing}`),
          })}
        </p>
      )}

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
              {/* Secondary: the page header already carries the primary. */}
              <Button icon={Plus} onClick={() => navigate('/probes/new')}>
                {t('probes.enroll.action')}
              </Button>
            </PermissionGate>
          )
        }
        rowHref={(row) => `/probes/${row.id}`}
        filterKeys={PROBE_FILTER_KEYS}
        defaultSort={{ key: 'status', direction: 'asc' }}
        rowLabel={nameOf}
        mobileCard={(row) => (
          <div className="min-w-0 space-y-1.5">
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-ink truncate font-medium">{nameOf(row)}</p>
              <span className={row.stale ? 'text-ink-3 shrink-0 text-xs' : 'text-ink-2 shrink-0 text-xs'}>
                {row.observed_at ? formatRelative(row.observed_at) : t('common.never')}
              </span>
            </div>
            <StatusBadges row={row} />
            <div className="text-ink-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <Mono truncate>{row.host}</Mono>
              {row.package_version && <Mono>{row.package_version}</Mono>}
              <PackageUpdateBadge version={row.package_update} />
              <span>
                {t('probes.columns.sensors')} {row.sensor_count}
              </span>
              {row.deviation_count > 0 && (
                <Badge tone="warn">
                  {t('probes.columns.deviations')} {row.deviation_count}
                </Badge>
              )}
            </div>
          </div>
        )}
        filters={
          // One scrolling row on a phone: wrapped, seven chips pushed the
          // first probe below the fold.
          <div className="-mx-3 flex w-[calc(100%+1.5rem)] items-center gap-2 overflow-x-auto px-3 [scrollbar-width:none] md:mx-0 md:w-auto md:flex-wrap md:overflow-visible md:px-0">
            {STATUS_ORDER.filter((status) => statusCounts.has(status)).map(
              (status) => (
                <FilterChip
                  key={status}
                  pressed={statuses.has(status)}
                  onClick={() => toggleStatus(status)}
                >
                  {STATUS_DOT[status] && (
                    <span
                      aria-hidden
                      className={`mr-1.5 size-1.5 rounded-full ${STATUS_DOT[status]}`}
                    />
                  )}
                  {t(`status.probe.${status}`)}
                  <span className="ml-1.5 tabular-nums">{statusCounts.get(status)}</span>
                </FilterChip>
              ),
            )}
            <span aria-hidden className="bg-rule mx-1 h-4 w-px shrink-0" />
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
            <FilterToggle
              label={t('probes.filters.prtgMissing')}
              active={onlyPrtgMissing}
              onToggle={() => toggleFilter('prtg')}
            />
          </div>
        }
        selection={{
          selected,
          onChange: setSelected,
          actions: (
            <>
              <PermissionGate permission="deployment.create">
                <Button size="sm" icon={Upload} onClick={() => setDeploying(true)}>
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

function StatusBadges({ row }: { row: ProbeSummary }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
      <ProbeStatusText status={row.status} />
      {row.running_job_id && <Badge tone="accent">{t('status.job.running')}</Badge>}
      {/* Beside the status rather than in its own column: it decides
          whether the next job on this probe will run at all. */}
      {row.helper_outdated && <Badge tone="warn">{t('probes.helperOutdated')}</Badge>}
      {/* Green here and invisible in PRTG is exactly what the status
          colour hides; a pending probe has an earlier problem. */}
      {isPrtgMissing(row) && <Badge tone="warn">{t('probes.prtg.missingBadge')}</Badge>}
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
    <FilterChip pressed={active} onClick={onToggle}>
      {label}
    </FilterChip>
  )
}
