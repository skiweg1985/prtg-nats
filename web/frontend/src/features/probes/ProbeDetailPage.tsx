import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useOverlay,
  useConfigureProbe,
  useExecuteReconcile,
  useIperfEndpoints,
  useProbe,
  useProbeAction,
  useProbePlan,
  useRefreshProbe,
  useReleaseInterface,
  useRemoveSensorFromProbe,
  useReserveInterface,
  useRevealAccessKey,
  useSensorProfiles,
  useSensors,
  useUpdateProbe,
  useUnenrollProbe,
  useWirelessInterfaces,
  type UnenrollOptions,
} from '@/api/hooks'
import type {
  Deviation,
  DeviationSeverity,
  ProbeDetail,
  SensorState,
  WirelessInterface,
} from '@/api/types'
import { pathTone } from '@/features/infrastructure/overlayMode'
import { PermissionGate, useAuth } from '@/app/providers'
import { DeployDialog } from '@/features/deployments/DeployDialog'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Banner,
  Button,
  Card,
  CheckboxField,
  DetailRow,
  Dialog,
  Dot,
  EmptyState,
  Mono,
  Select,
  Skeleton,
  Tabs,
} from '@/components/ui/primitives'
import { CopyButton, InlineCode } from '@/components/ui/CopyBlock'
import { ProbeStatusBadge, SensorStatusBadge, StateCell } from '@/components/ui/status'
import { formatRelative, shortFingerprint } from '@/utils/format'

const TABS = ['overview', 'sensors', 'deviations', 'diagnostics'] as const
type Tab = (typeof TABS)[number]

/** Where the tab lives in the address; every other parameter is the list's. */
const TAB_PARAM = 'tab'

/**
 * How loudly a finding is drawn.
 *
 * An informational one has no remedy the platform is entitled to choose - an
 * adopted sensor is as likely to be wanted as removed - so it never clears by
 * itself. Drawn in the warning colour it becomes a mark nobody can get rid of,
 * which is how a colour stops meaning anything.
 */
const SEVERITY_TONE: Record<DeviationSeverity, 'danger' | 'warn' | 'neutral'> = {
  critical: 'danger',
  warning: 'warn',
  info: 'neutral',
}

function needsAttention(deviations: Deviation[]): boolean {
  return deviations.some((deviation) => deviation.severity !== 'info')
}

export function ProbeDetailPage() {
  const { t } = useTranslation()
  const { probeId } = useParams<{ probeId: string }>()
  const { data, isLoading, error, refetch } = useProbe(probeId)
  const navigate = useNavigate()
  const refresh = useRefreshProbe()
  const installCa = useProbeAction('install-ca')
  const validate = useProbeAction('validate')
  const updateHelper = useProbeAction('helper-update')
  const configure = useConfigureProbe()
  const unenroll = useUnenrollProbe()
  const updateProbe = useUpdateProbe()
  // In the address rather than in state. Every action on this page ends in a
  // job, and coming back from one used to put the overview in front of
  // somebody who had been reading the deviations. It also makes a tab
  // something that can be linked to and survives a reload.
  const [params, setParams] = useSearchParams()
  const tab: Tab = TABS.find((entry) => entry === params.get(TAB_PARAM)) ?? 'overview'

  function selectTab(next: Tab) {
    const updated = new URLSearchParams(params)
    if (next === 'overview') updated.delete(TAB_PARAM)
    else updated.set(TAB_PARAM, next)
    setParams(updated, { replace: true })
  }

  // What the list was showing when this page was opened: the row link brings
  // its filters and search term along, and the way out hands them back.
  const listParams = new URLSearchParams(params)
  listParams.delete(TAB_PARAM)
  const listSearch = listParams.toString()
  const backToList = `/probes${listSearch ? `?${listSearch}` : ''}`
  const [confirmUnenroll, setConfirmUnenroll] = useState(false)
  // Every option starts off. Retiring a probe is destructive enough on its
  // own; what else goes has to be chosen, not merely left checked.
  const [cleanup, setCleanup] = useState<Required<UnenrollOptions>>({
    removeSensors: false,
    uninstallMpp: false,
  })

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!data || !probeId) return null

  const { summary, observed } = data
  // Every button in the header reports its failure, and the most recent one is
  // the one on screen: a refresh that failed an hour ago must not stand in
  // front of the CA install that failed a second ago.
  const lastFailure = [refresh, validate, installCa, updateHelper, configure]
    .map((mutation) => ({ error: mutation.error, at: mutation.submittedAt }))
    .filter((entry) => entry.error !== null)
    .sort((left, right) => right.at - left.at)
    .at(0)?.error
  // A helper that reports no version at all predates signed updates, so it has
  // no key to check one against and the channel cannot reach it. Told apart
  // here because the two cases need different instructions, not a shared
  // "something is old".
  const helperUpdatable = observed?.helper_version != null

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <Link to={backToList} className="text-ink-3 text-xs">
            ← {t('probes.title')}
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-lg">
              {summary.display_name ?? summary.probe_name ?? summary.nats_username}
            </h1>
            <ProbeStatusBadge status={summary.status} />
            {summary.stale && summary.observed_at && (
              <span className="text-ink-3 text-xs">
                {t('common.stale', { time: formatRelative(summary.observed_at) })}
              </span>
            )}
          </div>
          <Mono className="text-ink-3">
            {summary.nats_username} · {summary.host}
          </Mono>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            onClick={() => refresh.mutate(probeId)}
            disabled={refresh.isPending}
          >
            {t('probes.refreshState')}
          </Button>
          <PermissionGate permission="probe.read">
            <Button
              size="sm"
              onClick={() =>
                validate.mutate(probeId, {
                  onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                })
              }
              disabled={validate.isPending}
            >
              {t('probes.validate')}
            </Button>
          </PermissionGate>
          <PermissionGate permission="probe.update">
            <Button
              size="sm"
              onClick={() =>
                installCa.mutate(probeId, {
                  onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                })
              }
              disabled={installCa.isPending}
            >
              {t('probes.installCa')}
            </Button>
            <Button
              size="sm"
              variant={summary.helper_outdated && helperUpdatable ? 'primary' : undefined}
              onClick={() =>
                updateHelper.mutate(probeId, {
                  onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                })
              }
              disabled={updateHelper.isPending || !helperUpdatable}
            >
              {t('probes.updateHelper')}
            </Button>
            <Button
              size="sm"
              onClick={() =>
                configure.mutate(probeId, {
                  onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                })
              }
              disabled={configure.isPending}
            >
              {t('probes.configure')}
            </Button>
          </PermissionGate>
          <PermissionGate permission="probe.delete">
            <Button size="sm" variant="danger" onClick={() => setConfirmUnenroll(true)}>
              {t('probes.unenroll')}
            </Button>
          </PermissionGate>
        </div>
      </header>

      {confirmUnenroll && (
        <Dialog title={t('confirm.title')} onClose={() => setConfirmUnenroll(false)}>
          <p className="text-ink-2 text-sm">
            {t('probes.unenrollWarning', { probe: summary.nats_username })}
          </p>

          <fieldset className="mt-4 space-y-3 border-t pt-3">
            <legend className="sr-only">{t('probes.cleanup.legend')}</legend>
            <PermissionGate permission="sensor.remove">
              <CleanupOption
                label={t('probes.cleanup.removeSensors')}
                hint={t('probes.cleanup.removeSensorsHint', {
                  count: data.sensors.length,
                })}
                checked={cleanup.removeSensors}
                onChange={(checked) =>
                  setCleanup({ ...cleanup, removeSensors: checked })
                }
              />
            </PermissionGate>
            <PermissionGate permission="probe.update">
              <CleanupOption
                label={t('probes.cleanup.uninstallMpp')}
                hint={t('probes.cleanup.uninstallMppHint')}
                checked={cleanup.uninstallMpp}
                onChange={(checked) =>
                  setCleanup({ ...cleanup, uninstallMpp: checked })
                }
              />
            </PermissionGate>
          </fieldset>

          {cleanup.uninstallMpp && !cleanup.removeSensors && data.sensors.length > 0 && (
            <p className="text-ink-3 mt-3 text-xs">
              {t('probes.cleanup.sensorsSurviveHint')}
            </p>
          )}

          {unenroll.error && (
            <div className="mt-3">
              <ErrorDetails error={unenroll.error} />
            </div>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmUnenroll(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() =>
                unenroll.mutate(
                  { id: probeId, ...cleanup },
                  {
                    onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                  },
                )
              }
              disabled={unenroll.isPending}
            >
              {t('probes.unenroll')}
            </Button>
          </div>
        </Dialog>
      )}

      {lastFailure && (
        <ErrorDetails error={lastFailure} target={summary.nats_username} />
      )}
      {observed && !observed.reachable && (
        <Banner tone="danger" title={t('status.probe.unreachable')}>
          {observed.error_details ?? t('errors.probe.unreachable', { probe: summary.nats_username })}
        </Banner>
      )}
      {summary.helper_outdated && (
        <Banner tone="warn" title={t('probes.helperOutdatedTitle')}>
          {helperUpdatable ? t('probes.helperOutdatedBody') : t('probes.helperUnsignedBody')}
        </Banner>
      )}

      {/* The two manual PRTG steps have no observer, only this tick. Until it
          is set, the probe is green here and invisible over there - the one
          state the status colour cannot show. */}
      {!summary.prtg_registered &&
        summary.status !== 'pending' &&
        summary.status !== 'enrolled' && (
          <Banner
            tone="warn"
            title={t('probes.prtg.bannerTitle')}
            action={
              <PermissionGate permission="probe.update">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={updateProbe.isPending}
                  onClick={() =>
                    updateProbe.mutate({ id: probeId, prtg_registered: true })
                  }
                >
                  {t('probes.prtg.markDone')}
                </Button>
              </PermissionGate>
            }
          >
            {t('probes.prtg.bannerBody')}
          </Banner>
        )}
      {summary.prtg_registered && data.prtg_registered_by && (
        <p className="text-ink-3 text-xs">
          {t('probes.prtg.markedBy', {
            by: data.prtg_registered_by,
            when: data.prtg_registered_at
              ? formatRelative(data.prtg_registered_at)
              : '—',
          })}{' '}
          <PermissionGate permission="probe.update">
            <button
              type="button"
              className="hover:text-ink underline"
              onClick={() =>
                updateProbe.mutate({ id: probeId, prtg_registered: false })
              }
            >
              {t('probes.prtg.markUndone')}
            </button>
          </PermissionGate>
        </p>
      )}

      <Tabs
        tabs={TABS}
        active={tab}
        onSelect={selectTab}
        renderLabel={(entry) => (
          <>
            {t(`probes.tabs.${entry}`)}
            {entry === 'deviations' && data.deviations.length > 0 && (
              <Badge
                tone={needsAttention(data.deviations) ? 'warn' : 'neutral'}
                className="ml-2"
              >
                {data.deviations.length}
              </Badge>
            )}
          </>
        )}
      />

      {tab === 'overview' && <OverviewTab detail={data} />}
      {tab === 'sensors' && <SensorsTab probeId={probeId} detail={data} />}
      {tab === 'deviations' && <DeviationsTab probeId={probeId} detail={data} />}
      {tab === 'diagnostics' && <DiagnosticsTab detail={data} />}
    </div>
  )
}

function OverviewTab({ detail }: { detail: ProbeDetail }) {
  const { t } = useTranslation()
  const { summary, inventory, observed } = detail
  const reveal = useRevealAccessKey()
  const updateProbe = useUpdateProbe()
  // A mutation, not a query: nothing refetches it, and closing the dialog
  // drops the value out of both this state and the mutation's own.
  const [accessKey, setAccessKey] = useState<string | null>(null)
  const probeName = observed?.probe_name ?? inventory.probe_name

  function hideAccessKey() {
    setAccessKey(null)
    reveal.reset()
  }

  return (
    <>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('probes.identity')}>
          <dl>
            <DetailRow label={t('probes.natsUser')}>
              <span className="flex items-center gap-3">
                <Mono>{summary.nats_username}</Mono>
                <Link
                  to="/infrastructure/credentials"
                  className="text-accent text-xs hover:underline"
                >
                  {t('probes.atAGlance.manage')}
                </Link>
              </span>
            </DetailRow>
            <DetailRow label={t('probes.probeName')}>{probeName ?? '—'}</DetailRow>
            <DetailRow label={t('probes.probeId')}>
              <Mono truncate>{inventory.probe_id ?? '—'}</Mono>
            </DetailRow>
            <DetailRow label={t('probes.sshHost')}>
              <Mono>
                {inventory.ssh_host}:{inventory.ssh_port}
              </Mono>
            </DetailRow>
            <OverlayRow username={summary.nats_username} />
            <DetailRow label={t('probes.accessKey')}>
              {/* Presence only. The value is behind an audited, explicit reveal. */}
              {inventory.access_key_present ? (
                <span className="flex items-center gap-2">
                  <Badge tone="ok">{t('common.hidden')}</Badge>
                  <PermissionGate permission="credential.read">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        reveal.mutate(summary.id, {
                          onSuccess: (revealed) => setAccessKey(revealed.access_key),
                        })
                      }
                      disabled={reveal.isPending}
                    >
                      {t('common.reveal')}
                    </Button>
                  </PermissionGate>
                </span>
              ) : (
                <Badge tone="warn">{t('common.none')}</Badge>
              )}
            </DetailRow>
          </dl>
        </Card>

        <Card title={t('probes.actualState')}>
          <dl>
            <DetailRow label={t('probes.columns.service')}>
              <StateCell kind="service" value={summary.service} />
            </DetailRow>
            <DetailRow label={t('probes.columns.version')}>
              <Mono>{summary.package_version ?? '—'}</Mono>
            </DetailRow>
            <DetailRow label={t('probes.columns.nats')}>
              <StateCell kind="nats" value={summary.nats_connection} />
            </DetailRow>
            <DetailRow label={t('probes.caFingerprint')}>
              <span className="flex items-center gap-2">
                <StateCell kind="ca" value={summary.ca_state} />
                <Mono className="text-ink-3">
                  {shortFingerprint(observed?.ca_sha256)}
                </Mono>
              </span>
            </DetailRow>
            <DetailRow label={t('probes.helper')}>
              <span className="flex items-center gap-2">
                {observed?.helper_version != null
                  ? t('probes.helperVersion', { version: observed.helper_version })
                  : t('probes.helperUnknown')}
                {summary.helper_outdated && (
                  <Badge tone="warn">{t('probes.helperOutdated')}</Badge>
                )}
              </span>
            </DetailRow>
            <DetailRow label={t('probes.columns.observed')}>
              {summary.observed_at
                ? formatRelative(summary.observed_at)
                : t('common.never')}
            </DetailRow>
          </dl>
        </Card>

        {reveal.error && (
          <div className="lg:col-span-2">
            <ErrorDetails error={reveal.error} />
          </div>
        )}

        {/* Where everything about this probe is managed. The probe is an
            aggregate of things owned by other pages - variants on the sensor
            page, endpoints on the iperf pages, the overlay on its own page -
            and until now nothing said so. One row per aspect: the current
            value, and the way to the page that changes it. */}
        <Card title={t('probes.atAGlance.title')} className="lg:col-span-2">
          <dl>
            <DetailRow label={t('probes.atAGlance.sensors')}>
              <span className="flex items-center gap-3">
                <span>
                  {t('probes.atAGlance.sensorsValue', {
                    count: inventory.assigned_sensors.length,
                  })}
                </span>
                <Link
                  to="?tab=sensors"
                  className="text-accent text-xs hover:underline"
                >
                  {t('probes.atAGlance.manage')}
                </Link>
              </span>
            </DetailRow>
            <DetailRow label={t('probes.atAGlance.endpoints')}>
              <span className="flex items-center gap-3">
                <span>
                  {inventory.known_iperf_endpoints.length > 0
                    ? inventory.known_iperf_endpoints.join(', ')
                    : t('probes.atAGlance.none')}
                </span>
                <Link
                  to="/infrastructure/iperf"
                  className="text-accent text-xs hover:underline"
                >
                  {t('probes.atAGlance.manage')}
                </Link>
              </span>
            </DetailRow>
          </dl>
        </Card>
      </div>

      {accessKey !== null && (
        <Dialog
          title={t('probes.accessKeyRevealTitle', {
            probe: probeName ?? summary.nats_username,
          })}
          onClose={hideAccessKey}
          size="md"
        >
          <p className="text-ink-2 mb-3 text-sm">
            {t('probes.accessKeyHint')} {t('probes.accessKeyAudited')}
          </p>
          <div className="bg-surface-2 rounded-inset flex items-center gap-2 p-3">
            <Mono className="min-w-0 flex-1 break-all">{accessKey}</Mono>
            <CopyButton value={accessKey} />
          </div>
          <p className="text-ink-3 mt-3 text-xs">{t('probes.accessKeyPrtgPath')}</p>
          <div className="mt-4 flex items-center justify-between gap-2">
            {/* The dialog is open because somebody is working in PRTG right
                now - the tick belongs where the work happens. */}
            {!summary.prtg_registered ? (
              <PermissionGate permission="probe.update">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={updateProbe.isPending}
                  onClick={() =>
                    updateProbe.mutate({ id: summary.id, prtg_registered: true })
                  }
                >
                  {t('probes.accessKeyMarkDone')}
                </Button>
              </PermissionGate>
            ) : (
              <span className="text-ink-3 text-xs">
                {t('probes.prtg.alreadyMarked')}
              </span>
            )}
            <Button onClick={hideAccessKey}>{t('common.close')}</Button>
          </div>
        </Dialog>
      )}
    </>
  )
}

function CleanupOption({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string
  hint: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <CheckboxField
            label={label}
            hint={hint}
            checked={checked}
            onChange={(checked) => onChange(checked)}
          />
  )
}

/**
 * The radio interfaces of a probe, and the one decision to make about them.
 *
 * Reserving takes an interface away from NetworkManager for good and cuts
 * whatever it was carrying - so the list shows what would be lost, not just
 * the names. An interface on the default route is shown like the rest with
 * the fact attached; the probe is the one that refuses it, and repeating that
 * judgement here would only let the two drift apart.
 */
function InterfacesCard({
  probeId,
  sensors,
}: {
  probeId: string
  sensors: SensorState[]
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const reserve = useReserveInterface()
  const release = useReleaseInterface()
  const { data: catalogue } = useSensors()

  // Only sensors that take an interface can hold one. The comment used to
  // claim this filter while the code checked only the status - a probe with
  // nothing but link-quality got offered reservations that do nothing.
  const takesInterface = new Set(
    (catalogue ?? [])
      .filter((entry) => entry.needs_interface)
      .map((entry) => entry.name),
  )
  const candidates = sensors.filter(
    (entry) => entry.status !== 'absent' && takesInterface.has(entry.name),
  )
  const { data, isLoading, error } = useWirelessInterfaces(probeId, candidates.length > 0)
  const [sensorName, setSensorName] = useState('')
  const target = sensorName || candidates[0]?.name || ''

  if (!candidates.length) return null

  // The rollout is green without a reservation, the sensor refuses every run
  // with one missing, and only PRTG shows the refusal. This card is the one
  // place the platform can say it first.
  const unserved = candidates.filter(
    (entry) => entry.interfaces.length === 0,
  )

  const columns: Column<WirelessInterface>[] = [
    {
      key: 'name',
      header: t('probes.interfaces.columns.name'),
      sortValue: (row) => row.name,
      cell: (row) => <Mono>{row.name}</Mono>,
    },
    {
      key: 'reserved',
      header: t('probes.interfaces.columns.reserved'),
      cell: (row) =>
        row.reserved_by ? (
          <Badge tone="accent">{row.reserved_by}</Badge>
        ) : (
          <span className="text-ink-3">{t('probes.interfaces.free')}</span>
        ),
    },
    {
      key: 'inUse',
      header: t('probes.interfaces.columns.inUse'),
      cell: (row) => (
        <div className="flex flex-wrap items-center gap-1.5">
          {row.carries_default_route ? (
            <Badge tone="danger">{t('probes.interfaces.defaultRoute')}</Badge>
          ) : null}
          {row.connection ? (
            <Badge tone="warn">{row.connection}</Badge>
          ) : null}
          {!row.carries_default_route && !row.connection ? (
            <span className="text-ink-3">—</span>
          ) : null}
        </div>
      ),
    },
    {
      key: 'state',
      header: t('probes.interfaces.columns.state'),
      cell: (row) => (
        <Mono>{[row.operstate, row.nm_state].filter(Boolean).join(' · ') || '—'}</Mono>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (row) => (
        <PermissionGate permission="sensor.configure">
          {row.reserved_by ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={release.isPending}
              onClick={() =>
                release.mutate(
                  { probeId, sensor: row.reserved_by as string, iface: row.name },
                  { onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`) },
                )
              }
            >
              {t('probes.interfaces.release')}
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              // The probe would refuse anyway; a button that works until it
              // does not, with a red badge beside it, reads like a dare.
              disabled={reserve.isPending || !target || row.carries_default_route}
              title={
                row.carries_default_route
                  ? t('probes.interfaces.defaultRouteHint')
                  : undefined
              }
              onClick={() =>
                reserve.mutate(
                  { probeId, sensor: target, iface: row.name },
                  { onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`) },
                )
              }
            >
              {t('probes.interfaces.reserve')}
            </Button>
          )}
        </PermissionGate>
      ),
    },
  ]

  return (
    <Card
      title={t('probes.interfaces.title')}
      action={
        candidates.length > 1 ? (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-ink-3">{t('probes.interfaces.forSensor')}</span>
            <Select className="rounded"
              value={target}
              onChange={(event) => setSensorName(event.target.value)}
            >
              {candidates.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.name}
                </option>
              ))}
            </Select>
          </label>
        ) : null
      }
    >
      <p className="text-ink-3 mb-3 text-sm">{t('probes.interfaces.hint')}</p>
      {unserved.length > 0 && (
        <div className="mb-3">
          <Banner tone="warn" title={t('probes.interfaces.neededTitle')}>
            {t('probes.interfaces.neededBody', {
              sensors: unserved.map((entry) => entry.name).join(', '),
            })}
          </Banner>
        </div>
      )}
      {error ? (
        <ErrorDetails error={error} />
      ) : isLoading ? (
        <Skeleton />
      ) : (
        <DataTable
          rows={data ?? []}
          columns={columns}
          rowKey={(row) => row.name}
          emptyTitle={t('probes.interfaces.empty')}
        />
      )}
    </Card>
  )
}

function SensorsTab({ probeId, detail }: { probeId: string; detail: ProbeDetail }) {
  const { t } = useTranslation()
  const sensors = detail.sensors
  const navigate = useNavigate()
  const removeSensor = useRemoveSensorFromProbe()
  // Which sensor the dialog opens preselected on - null closed, '' free pick.
  const [deploying, setDeploying] = useState<string | null>(null)

  const columns: Column<SensorState>[] = [
    {
      key: 'name',
      header: t('sensors.columns.name'),
      sortValue: (row) => row.name,
      searchValue: (row) => row.name,
      cell: (row) => <span className="text-ink font-medium">{row.name}</span>,
    },
    {
      key: 'status',
      header: t('probes.columns.status'),
      sortValue: (row) => row.status,
      cell: (row) => <SensorStatusBadge status={row.status} />,
    },
    {
      key: 'installed',
      header: t('probes.actualState'),
      cell: (row) => <Mono>{row.installed_version ?? '—'}</Mono>,
    },
    {
      key: 'desired',
      header: t('probes.desiredState'),
      cell: (row) => <Mono>{row.desired_version ?? '—'}</Mono>,
    },
    {
      key: 'tool',
      header: t('probes.sensorTool.column'),
      searchValue: (row) =>
        [row.tool_name, row.tool_platform, row.tool_source, row.tool_path]
          .filter(Boolean)
          .join(' '),
      cell: (row) =>
        row.tool_name === null ? (
          '—'
        ) : (
          <div className="space-y-0.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-ink text-xs font-medium">{row.tool_name}</span>
              <Badge tone={row.tool_source === 'managed' ? 'accent' : 'neutral'}>
                {row.tool_source === null
                  ? t('probes.sensorTool.sourceUnknown')
                  : t(`probes.sensorTool.source.${row.tool_source}`)}
              </Badge>
              {row.tool_compatible === true && (
                <Badge tone="ok">{t('probes.sensorTool.compatible')}</Badge>
              )}
              {row.tool_compatible === false && (
                <Badge tone="danger">{t('probes.sensorTool.incompatible')}</Badge>
              )}
              {row.tool_compatible === null && (
                <Badge tone="warn">{t('probes.sensorTool.notVerified')}</Badge>
              )}
              <Mono className="text-ink-3">{row.tool_platform ?? '—'}</Mono>
            </div>
            <Mono className="text-ink-3 block break-all">
              {row.tool_path ?? '—'}
            </Mono>
            <div className="text-ink-3 text-xs">
              {t(
                row.tool_source === 'system'
                  ? 'probes.sensorTool.systemVersions'
                  : 'probes.sensorTool.managedVersions',
                {
                  installed: row.installed_tool_version ?? '—',
                  expected: row.expected_tool_version ?? '—',
                },
              )}
            </div>
            <Mono className="text-ink-3 block">
              {row.tool_source === 'system'
                ? t('probes.sensorTool.systemSha256', {
                    installed: shortFingerprint(row.installed_tool_sha256),
                  })
                : t('probes.sensorTool.managedSha256', {
                    installed: shortFingerprint(row.installed_tool_sha256),
                    expected: shortFingerprint(row.expected_tool_sha256),
                  })}
            </Mono>
          </div>
        ),
    },
    {
      key: 'interfaces',
      header: t('probes.interfaces.columns.name'),
      cell: (row) =>
        row.interfaces.length ? <Mono>{row.interfaces.join(', ')}</Mono> : '—',
    },
    {
      key: 'helper',
      header: t('probes.sensorHelper'),
      cell: (row) =>
        row.helper_state === null || row.helper_state === 'none' ? (
          '—'
        ) : row.helper_state === 'listening' ? (
          <Badge tone="ok">{t('sensors.helperState.listening')}</Badge>
        ) : (
          // In PRTG this sensor reports "helper unreachable" on every scan;
          // here it used to look exactly like a healthy one.
          <Badge tone="danger">{t('sensors.helperState.inactive')}</Badge>
        ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (row) =>
        row.status === 'absent' ? null : (
          <span className="flex justify-end gap-2">
            {/* The row already names the cure's target: outdated and drifted
                are fixed by rolling the sensor out again, and until now the
                only button in reach was "remove". */}
            {(row.status === 'outdated' || row.status === 'drifted') && (
              <PermissionGate permission="deployment.create">
                <Button size="sm" onClick={() => setDeploying(row.name)}>
                  {t('sensors.updateOnProbe')}
                </Button>
              </PermissionGate>
            )}
            <PermissionGate permission="sensor.remove">
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  removeSensor.mutate(
                    { probeId, sensor: row.name },
                    {
                      onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                    },
                  )
                }
                disabled={removeSensor.isPending}
              >
                {t('sensors.remove')}
              </Button>
            </PermissionGate>
          </span>
        ),
    },
  ]

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <PermissionGate permission="deployment.create">
          <Button size="sm" variant="primary" onClick={() => setDeploying('')}>
            {t('probes.deploySensor')}
          </Button>
        </PermissionGate>
      </div>
      <DataTable
        rows={sensors}
        columns={columns}
        rowKey={(row) => row.name}
        emptyTitle={t('probes.sensorsEmpty')}
        emptyAction={
          <PermissionGate permission="deployment.create">
            <Button size="sm" variant="primary" onClick={() => setDeploying('')}>
              {t('probes.deploySensor')}
            </Button>
          </PermissionGate>
        }
      />
      <VariantsCard detail={detail} />
      <EndpointsCard detail={detail} />
      <InterfacesCard probeId={probeId} sensors={sensors} />
      {deploying !== null && (
        <DeployDialog
          sensorName={deploying || undefined}
          probeIds={[probeId]}
          onClose={() => setDeploying(null)}
        />
      )}
    </div>
  )
}

function DeviationsTab({ probeId, detail }: { probeId: string; detail: ProbeDetail }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [preview, setPreview] = useState(false)
  const plan = useProbePlan(probeId, preview)
  const execute = useExecuteReconcile()

  if (detail.deviations.length === 0) {
    return (
      <Card>
        <EmptyState title={t('probes.noDeviations')} />
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card
        title={t('probes.deviationsTitle')}
        action={
          <PermissionGate permission="probe.reconcile">
            <Button size="sm" variant="primary" onClick={() => setPreview(true)}>
              {t('probes.fixDeviations')}
            </Button>
          </PermissionGate>
        }
        dense
      >
        <ul>
          {detail.deviations.map((deviation, index) => (
            <DeviationRow key={index} deviation={deviation} />
          ))}
        </ul>
      </Card>

      {/* Pressing the button changed nothing until the plan came back, and
          nothing at all when it failed. */}
      {preview && plan.isLoading && <Skeleton className="h-32" />}
      {preview && plan.error && <ErrorDetails error={plan.error} />}

      {preview && plan.data && (
        <Card title={t('probes.planPreview')}>
          {plan.data.is_empty ? (
            <EmptyState title={t('probes.planEmpty')} />
          ) : (
            <>
              {plan.data.restarts_service && (
                <Banner tone="warn">{t('probes.planRestartWarning')}</Banner>
              )}
              <ol className="mt-3 space-y-2">
                {plan.data.actions.map((action, index) => (
                  <li key={index} className="flex items-start gap-2 text-sm">
                    <span className="text-ink-3 font-mono text-xs">{index + 1}.</span>
                    <span>
                      {t(action.description_key, action.params)}
                      {action.risk_key && (
                        <span className="text-warn block text-xs">
                          {t(action.risk_key)}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
              <div className="mt-4 flex justify-end">
                <Button
                  variant="primary"
                  onClick={() =>
                    execute.mutate(probeId, {
                      onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                    })
                  }
                  disabled={execute.isPending}
                >
                  {t('probes.executePlan')}
                </Button>
              </div>
              {execute.error && (
                <div className="mt-3">
                  <ErrorDetails error={execute.error} />
                </div>
              )}
            </>
          )}
        </Card>
      )}
    </div>
  )
}

function DeviationRow({ deviation }: { deviation: Deviation }) {
  const { t } = useTranslation()
  return (
    <li className="border-rule flex items-start gap-3 border-b px-4 py-2.5 last:border-0">
      <Dot tone={SEVERITY_TONE[deviation.severity]} />
      <div className="min-w-0 flex-1">
        <p className="text-ink text-sm">
          {t(`deviations.${deviation.kind}`, {
            ...deviation.params,
            sensor: deviation.object_ref,
            defaultValue: deviation.kind,
          })}
        </p>
        {(deviation.expected || deviation.actual) && (
          <p className="text-ink-3 mt-0.5 font-mono text-xs">
            {shortFingerprint(deviation.actual) ?? '—'} → {shortFingerprint(deviation.expected) ?? '—'}
          </p>
        )}
        {/* Set for every finding since the beginning, shown nowhere: what to
            do about it stayed the reader's own conclusion. */}
        {deviation.remediation && (
          <p className="text-ink-3 mt-0.5 text-xs">
            {t(`deviations.remediation.${deviation.remediation}`, {
              defaultValue: deviation.remediation,
            })}
          </p>
        )}
      </div>
    </li>
  )
}

function DiagnosticsTab({ detail }: { detail: ProbeDetail }) {
  const { t } = useTranslation()
  const { observed, inventory } = detail

  return (
    <Card title={t('probes.tabs.diagnostics')}>
      <dl>
        <DetailRow label="hostname">
          <Mono>{observed?.hostname ?? '—'}</Mono>
        </DetailRow>
        <DetailRow label="config">
          <Mono truncate>{observed?.config_path ?? '—'}</Mono>
        </DetailRow>
        <DetailRow label="ca_sha256">
          <Mono truncate>{observed?.ca_sha256 ?? '—'}</Mono>
        </DetailRow>
        <DetailRow label={t('probes.pendingTransaction')}>
          <Mono>{inventory.pending_transaction || '—'}</Mono>
        </DetailRow>
      </dl>
      {observed?.error_details && (
        <pre className="bg-surface-2 rounded-inset text-ink-2 mt-3 max-h-48 overflow-auto p-3 font-mono text-xs whitespace-pre-wrap">
          {observed.error_details}
        </pre>
      )}
    </Card>
  )
}

/**
 * The measurement endpoints this probe holds credentials for.
 *
 * A held endpoint is a resource a probe carries for a sensor, the same as a
 * reserved interface - which is why it sits here rather than under diagnostics
 * as a list of bare names. The parameter beside each name is the one that
 * probe's PRTG objects need, and it depends on how many the probe holds: alone
 * it needs none, from two on it needs the profile.
 *
 * A name the registry no longer knows still shows: the file on the probe says
 * it, and a sensor reading it there is a real state rather than a display bug.
 */
/**
 * The variants this probe holds, next to the sensors that read them.
 *
 * Assignment happens on the sensor page; this card only shows and links. A
 * variant is a resource the probe carries for a sensor, like a reserved
 * interface or an iperf endpoint - and it was the one of the three that the
 * probe page did not know about.
 */
function VariantsCard({ detail }: { detail: ProbeDetail }) {
  const { t } = useTranslation()
  const { data: catalogue } = useSensors()

  const withProfiles = (catalogue ?? []).filter(
    (entry) =>
      entry.supports_profiles &&
      detail.sensors.some(
        (sensor) => sensor.name === entry.name && sensor.status !== 'absent',
      ),
  )
  if (withProfiles.length === 0) return null

  return (
    <Card title={t('probes.variants.title')}>
      <div className="space-y-2">
        {withProfiles.map((entry) => (
          <SensorVariantRows
            key={entry.name}
            sensor={entry.name}
            username={detail.summary.nats_username}
          />
        ))}
      </div>
    </Card>
  )
}

function SensorVariantRows({
  sensor,
  username,
}: {
  sensor: string
  username: string
}) {
  const { t } = useTranslation()
  const { data } = useSensorProfiles(sensor)

  const held = (data ?? []).filter((variant) => variant.probes.includes(username))

  return (
    <div className="text-sm">
      <Link to={`/sensors/${sensor}`} className="text-accent hover:underline">
        {sensor}
      </Link>
      {held.length === 0 ? (
        <p className="text-ink-3 text-xs">{t('probes.variants.empty')}</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {held.map((variant) => (
            <li key={variant.name} className="flex flex-wrap items-center gap-2">
              <Mono>{variant.name}</Mono>
              <InlineCode>
                {variant.parameter_line}
              </InlineCode>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EndpointsCard({ detail }: { detail: ProbeDetail }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { data: endpoints } = useIperfEndpoints(can('iperf.read'))
  const { data: catalogue } = useSensors()

  const held = detail.inventory.known_iperf_endpoints
  // An iperf sensor with nothing to measure against is exactly the state the
  // rollout dialog warns about - but the warning has to live where the state
  // does, not only in the dialog that caused it.
  const iperfSensors = new Set(
    (catalogue ?? [])
      .filter((entry) => entry.iperf_kind)
      .map((entry) => entry.name),
  )
  const hasIperfSensor = detail.sensors.some(
    (entry) => entry.status !== 'absent' && iperfSensors.has(entry.name),
  )
  if (held.length === 0) {
    if (!hasIperfSensor) return null
    return (
      <Banner
        tone="warn"
        title={t('probes.iperf.noneHeldTitle')}
        action={
          <Link to="/infrastructure/iperf">
            <Button size="sm" variant="primary">
              {t('probes.iperf.toEndpoints')}
            </Button>
          </Link>
        }
      >
        {t('probes.iperf.noneHeldBody')}
      </Banner>
    )
  }

  const known = new Map((endpoints ?? []).map((entry) => [entry.name, entry]))

  return (
    <Card title={t('probes.iperf.title')}>
      <ul className="divide-rule divide-y">
        {held.map((name) => {
          const endpoint = known.get(name)
          const holder = endpoint?.holders.find(
            (entry) => entry.probe === detail.summary.nats_username,
          )
          return (
            <li
              key={name}
              className="flex flex-wrap items-center justify-between gap-2 py-2"
            >
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                {endpoint ? (
                  <Link
                    to={`/infrastructure/iperf/${name}`}
                    className="text-ink hover:underline"
                  >
                    <Mono>{name}</Mono>
                  </Link>
                ) : (
                  <Mono>{name}</Mono>
                )}
                {endpoint ? (
                  <span className="text-ink-3 text-xs">
                    {endpoint.host}:{endpoint.port}
                  </span>
                ) : (
                  <Badge tone="warn">
                    {t('infrastructure.iperf.unknownEndpoint')}
                  </Badge>
                )}
              </span>
              {holder &&
                (holder.uses_default_alias ? (
                  <Badge tone="ok">
                    {t('infrastructure.iperf.noParameterNeeded')}
                  </Badge>
                ) : (
                  <InlineCode>
                    {holder.parameter_line}
                  </InlineCode>
                ))}
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

/**
 * The probe's overlay address and what it is doing with it.
 *
 * On the identity card rather than a card of its own: for a probe on the
 * overlay this is a second address it answers on, which is what the rest of
 * that list is about. A probe that is not on it says so in one line instead
 * of taking up a panel to say nothing.
 */
function OverlayRow({ username }: { username: string }) {
  const { t } = useTranslation()
  const { data } = useOverlay()
  const peer = data?.peers.find((entry) => entry.nats_username === username)

  if (!data?.enabled) return null
  if (!peer) {
    return (
      <DetailRow label={t('probes.overlay')}>
        <Badge tone="neutral">{t('infrastructure.overlay.modes.off.name')}</Badge>
      </DetailRow>
    )
  }
  const state = peer.last_state ?? 'unknown'
  return (
    <DetailRow label={t('probes.overlay')}>
      <span className="flex flex-wrap items-center gap-2">
        <Mono>{peer.address}</Mono>
        <Badge tone="accent">
          {t(`infrastructure.overlay.modes.${peer.mode}.name`)}
        </Badge>
        <Badge tone={pathTone(peer.mode, state)}>
          {t(`infrastructure.overlay.paths.${state}`, { defaultValue: state })}
        </Badge>
        <Link
          to="/infrastructure/overlay"
          className="text-accent text-xs hover:underline"
        >
          {t('probes.atAGlance.manage')}
        </Link>
      </span>
    </DetailRow>
  )
}
