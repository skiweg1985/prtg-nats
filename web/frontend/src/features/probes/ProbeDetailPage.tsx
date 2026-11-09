import { useState } from 'react'
import clsx from 'clsx'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useOverlay,
  useOverlayAttach,
  useOverlayMode,
  useConfigureProbe,
  useExecuteReconcile,
  useIperfEndpoints,
  useProbe,
  useMppUpgrade,
  useProbeAction,
  useProbePlan,
  useProbePrtgName,
  useRefreshProbe,
  useReleaseInterface,
  useRemoveSensorFromProbe,
  useReserveInterface,
  useRevealAccessKey,
  useSensorLogTimeline,
  useSensorProfiles,
  useSensors,
  useUpdateProbe,
  useUnenrollProbe,
  useWirelessInterfaces,
  type UnenrollOptions,
} from '@/api/hooks'
import type {
  CaState,
  Deviation,
  DeviationSeverity,
  PlannedAction,
  ProbeDetail,
  SensorState,
  ServiceState,
  WirelessInterface,
} from '@/api/types'
import {
  RevealPasswordDialog,
  RotateAccountDialog,
} from '@/features/infrastructure/NatsAccountDialogs'
import {
  OverlayAttachDialog,
  OverlayModeDialog,
} from '@/features/infrastructure/overlayMode'
import {
  ArrowRight,
  ArrowUpCircle,
  Check,
  Eye,
  FileBadge,
  KeyRound,
  Lock,
  LockOpen,
  PackageCheck,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  Upload,
} from 'lucide-react'
import { PermissionGate, useAuth } from '@/app/providers'
import { DeployDialog } from '@/features/deployments/DeployDialog'
import { PrtgNameRow } from '@/features/probes/PrtgName'
import { PackageUpdateBadge } from '@/features/probes/PackageUpdateBadge'
import { probeName } from '@/features/probes/probeName'
import { ReturnChannelRow } from '@/features/probes/ReturnChannelRow'
import { MiniTimeline } from '@/features/sensorLogs/SensorTimeline'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  ActionMenu,
  BackLink,
  Badge,
  Banner,
  Button,
  ButtonLink,
  Card,
  CheckboxField,
  ConfirmDialog,
  DetailRow,
  Dialog,
  Dot,
  EmptyState,
  Mono,
  RowActions,
  Select,
  Skeleton,
  Tabs,
} from '@/components/ui/primitives'
import { CopyButton, InlineCode } from '@/components/ui/CopyBlock'
import { ProbeStatusBadge, SensorStatusBadge, StateCell } from '@/components/ui/status'
import { formatRelative, shortFingerprint } from '@/utils/format'

// Deviations are a section of the overview, not a tab: they are empty most
// of the time, and a tab that is usually empty is a click to learn nothing.
// An old link to ?tab=deviations lands on the overview, where they now are.
const TABS = ['overview', 'sensors', 'diagnostics'] as const
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

export function ProbeDetailPage() {
  const { t } = useTranslation()
  const { probeId } = useParams<{ probeId: string }>()
  const { data, isLoading, error, refetch } = useProbe(probeId)
  const { data: prtgName } = useProbePrtgName(probeId)
  const navigate = useNavigate()
  const { can } = useAuth()
  const refresh = useRefreshProbe()
  const installCa = useProbeAction('install-ca')
  const validate = useProbeAction('validate')
  const updateHelper = useProbeAction('helper-update')
  const upgradeMpp = useMppUpgrade()
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
  const [confirmUpgrade, setConfirmUpgrade] = useState(false)
  const [finishPending, setFinishPending] = useState(false)
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
  const lastFailure = [refresh, validate, installCa, updateHelper, upgradeMpp, configure]
    .map((mutation) => ({ error: mutation.error, at: mutation.submittedAt }))
    .filter((entry) => entry.error !== null)
    .sort((left, right) => right.at - left.at)
    .at(0)?.error
  // A helper that reports no version at all predates signed updates, so it has
  // no key to check one against and the channel cannot reach it. Told apart
  // here because the two cases need different instructions, not a shared
  // "something is old".
  const helperUpdatable = observed?.helper_version != null
  const canUpdate = can('probe.update')
  const toJob = {
    onSuccess: (accepted: { job_id: string }) => navigate(`/jobs/${accepted.job_id}`),
  }
  const name = probeName(
    summary,
    prtgName?.state === 'ok' ? prtgName.name_in_prtg : null,
  )
  // Two different kinds of old. A probe that did not answer has nothing
  // left to show: the failed attempt replaced what it said before, so its
  // service and CA read "unknown" on their own. A probe that answered, but
  // longer ago than the staleness window, still shows what it said then -
  // and that is what must not pass for the present.
  const unreachable = observed != null && !observed.reachable
  const dated = observed != null && observed.reachable && summary.stale
  // Asking again is the next step for both, so it takes the one primary.
  const refreshFirst = unreachable || dated
  const helperVisible = canUpdate && summary.helper_outdated && helperUpdatable

  return (
    <div className="space-y-4">
      {/* The title keeps its line; the toolbar moves under it when both do
          not fit, instead of squeezing the name into two lines. */}
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-64 flex-1">
          <BackLink to={backToList}>{t('probes.title')}</BackLink>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-lg">{name}</h1>
            <ProbeStatusBadge status={summary.status} />
          </div>
          {/* The account is what the shell, the logs and NATS call the
              probe; it stays readable, second to the name. */}
          {name !== summary.nats_username && (
            <Mono className="text-ink-3">{summary.nats_username}</Mono>
          )}
        </div>

        {/* Two visible actions and the menu, always the same two: checking
            the state and applying the configuration. Checking is the primary
            when what the page shows is old or unanswered. Everything rare -
            the check report, the helper, the CA, unenrolling - waits in the
            menu, unenrolling last, in danger text, behind its own
            confirmation; an outdated helper also gets its button in its own
            banner. On a phone the buttons grow to a finger's size, and the
            check keeps only its icon unless it is the primary, so the row -
            with the menu - still fits one line; its name stays for screen
            readers. */}
        <div className="flex flex-wrap items-center gap-2 max-md:[&_button]:h-11 max-md:[&_button]:min-w-11 max-md:[&_button]:justify-center">
          <Button
            size="sm"
            variant={refreshFirst ? 'primary' : 'ghost'}
            icon={RefreshCw}
            onClick={() => refresh.mutate(probeId)}
            disabled={refresh.isPending}
          >
            <span className={refreshFirst ? undefined : 'max-md:sr-only'}>
              {t('probes.refreshState')}
            </span>
          </Button>
          <PermissionGate permission="probe.update">
            <Button
              size="sm"
              icon={Settings2}
              onClick={() => configure.mutate(probeId, toJob)}
              disabled={configure.isPending}
            >
              {t('probes.configure')}
            </Button>
          </PermissionGate>
          <ActionMenu
            label={t('common.moreActions')}
            variant="secondary"
            actions={[
              ...(can('probe.read')
                ? [
                    {
                      key: 'validate',
                      label: t('probes.validate'),
                      icon: ShieldCheck,
                      disabled: validate.isPending,
                      onSelect: () => validate.mutate(probeId, toJob),
                    },
                  ]
                : []),
              ...(canUpdate
                ? [
                    {
                      key: 'install-ca',
                      label: t('probes.installCa'),
                      icon: FileBadge,
                      disabled: installCa.isPending,
                      onSelect: () => installCa.mutate(probeId, toJob),
                    },
                    {
                      key: 'update-helper',
                      label: t('probes.updateHelper'),
                      icon: ArrowUpCircle,
                      disabled: updateHelper.isPending || !helperUpdatable,
                      onSelect: () => updateHelper.mutate(probeId, toJob),
                    },
                    // Offered without a known newer version too: the fleet
                    // only knows what its probes' caches have seen, and the
                    // upgrade asks the repository itself.
                    ...(summary.package_version
                      ? [
                          {
                            key: 'mpp-upgrade',
                            label: t('probes.mppUpgrade.action'),
                            icon: PackageCheck,
                            disabled: upgradeMpp.isPending || !helperUpdatable,
                            onSelect: () => setConfirmUpgrade(true),
                          },
                        ]
                      : []),
                  ]
                : []),
              ...(can('probe.delete')
                ? [
                    {
                      key: 'unenroll',
                      label: t('probes.unenroll'),
                      icon: Trash2,
                      tone: 'danger' as const,
                      onSelect: () => setConfirmUnenroll(true),
                    },
                  ]
                : []),
            ]}
          />
        </div>
      </header>

      {confirmUnenroll && (
        <Dialog
          title={t('probes.unenrollTitle', { probe: name })}
          onClose={() => setConfirmUnenroll(false)}
        >
          <p className="text-ink-2 text-sm">
            {t('probes.unenrollWarning', { probe: name })}
          </p>

          <fieldset className="mt-4 space-y-3 border-t pt-3">
            <legend className="sr-only">{t('probes.cleanup.legend')}</legend>
            {data.sensors.length > 0 && (
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
            )}
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

      {confirmUpgrade && (
        <Dialog
          title={t('probes.mppUpgrade.confirmTitle', { probe: name })}
          onClose={() => {
            setConfirmUpgrade(false)
            setFinishPending(false)
          }}
        >
          <p className="text-ink-2 text-sm">
            {summary.package_update
              ? t('probes.mppUpgrade.confirmBody', {
                  current: summary.package_version,
                  target: summary.package_update,
                })
              : t('probes.mppUpgrade.confirmBodyLatest', {
                  current: summary.package_version,
                })}
          </p>
          <p className="text-ink-2 mt-3 text-sm">{t('probes.mppUpgrade.restartNote')}</p>
          <div className="mt-4 border-t pt-3">
            <CheckboxField
              label={t('probes.mppUpgrade.finishPending')}
              hint={t('probes.mppUpgrade.finishPendingHint')}
              checked={finishPending}
              onChange={setFinishPending}
            />
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button
              variant="ghost"
              onClick={() => {
                setConfirmUpgrade(false)
                setFinishPending(false)
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="primary"
              onClick={() =>
                upgradeMpp.mutate(
                  { id: probeId, finishPending },
                  {
                    onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                    onSettled: () => {
                      setConfirmUpgrade(false)
                      setFinishPending(false)
                    },
                  },
                )
              }
              disabled={upgradeMpp.isPending}
            >
              {t('probes.mppUpgrade.action')}
            </Button>
          </div>
        </Dialog>
      )}

      {lastFailure && <ErrorDetails error={lastFailure} target={name} />}
      {unreachable && observed && (
        <UnreachableBanner
          detail={data}
          attemptAt={observed.observed_at}
          onDetails={
            observed.error_details ? () => selectTab('diagnostics') : undefined
          }
        />
      )}
      {dated && summary.observed_at && (
        <Banner tone="neutral" title={t('probes.fault.datedTitle')}>
          {t('probes.fault.datedBody', {
            time: formatRelative(summary.observed_at),
          })}
        </Banner>
      )}
      {summary.helper_outdated && (
        <Banner
          tone="warn"
          title={t('probes.helperOutdatedTitle')}
          action={
            helperVisible && (
              // Secondary for the same reason as the PRTG banner below: the
              // toolbar keeps the page's one primary.
              <Button
                size="sm"
                icon={ArrowUpCircle}
                onClick={() => updateHelper.mutate(probeId, toJob)}
                disabled={updateHelper.isPending}
              >
                {t('probes.updateHelper')}
              </Button>
            )
          }
        >
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
                {/* Secondary: a blue fill inside an amber banner shouts twice,
                    and the page keeps its one primary for the toolbar. */}
                <Button
                  size="sm"
                  icon={Check}
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
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <p className="text-ink-3 text-xs">
            {t('probes.prtg.markedBy', {
              by: data.prtg_registered_by,
              when: data.prtg_registered_at
                ? formatRelative(data.prtg_registered_at)
                : '—',
            })}
          </p>
          <PermissionGate permission="probe.update">
            <Button
              size="sm"
              variant="ghost"
              disabled={updateProbe.isPending}
              onClick={() =>
                updateProbe.mutate({ id: probeId, prtg_registered: false })
              }
            >
              {t('probes.prtg.markUndone')}
            </Button>
          </PermissionGate>
        </div>
      )}

      {/* The strip scrolls sideways on a phone rather than squeezing a
          label, and each tab is a finger's height there. */}
      <div className="max-md:[&_[role=tab]]:min-h-11">
        <Tabs
          tabs={TABS}
          active={tab}
          onSelect={selectTab}
          renderLabel={(entry) => t(`probes.tabs.${entry}`)}
        />
      </div>

      {tab === 'overview' && (
        <>
          {data.deviations.length > 0 && (
            <DeviationsSection
              probeId={probeId}
              detail={data}
              headerHasPrimary={refreshFirst}
            />
          )}
          <OverviewTab detail={data} dated={dated} />
        </>
      )}
      {tab === 'sensors' && <SensorsTab probeId={probeId} detail={data} />}
      {tab === 'diagnostics' && <DiagnosticsTab detail={data} />}
    </div>
  )
}

function OverviewTab({ detail, dated }: { detail: ProbeDetail; dated: boolean }) {
  const { t } = useTranslation()
  const { summary, inventory, observed } = detail
  const reveal = useRevealAccessKey()
  const updateProbe = useUpdateProbe()
  const { can } = useAuth()
  // A mutation, not a query: nothing refetches it, and closing the dialog
  // drops the value out of both this state and the mutation's own.
  const [accessKey, setAccessKey] = useState<string | null>(null)
  const [accessKeyOpen, setAccessKeyOpen] = useState(false)
  const [revealingPassword, setRevealingPassword] = useState(false)
  const [rotating, setRotating] = useState(false)
  const announcedName = observed?.probe_name ?? inventory.probe_name

  function hideAccessKey() {
    setAccessKeyOpen(false)
    setAccessKey(null)
    reveal.reset()
  }

  return (
    <>
      {/* Two columns only where a half-width card still holds a row with its
          actions on one line. */}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title={t('probes.identity')}>
          <dl>
            {/* The account belongs to this probe and nothing else, so it is
                managed here rather than in a list of its own. */}
            <DetailRow
              label={t('probes.natsUser')}
              actions={
                can('credential.rotate') && (
                  <RowActions>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={Eye}
                      onClick={() => setRevealingPassword(true)}
                    >
                      {t('probes.account.showPassword')}
                    </Button>
                    {/* Rotation cuts the probe's current credentials: rare,
                        consequential, and confirmed in its own dialog. */}
                    <ActionMenu
                      label={t('common.moreActions')}
                      actions={[
                        {
                          key: 'rotate',
                          label: t('credentials.rotate'),
                          icon: KeyRound,
                          onSelect: () => setRotating(true),
                        },
                      ]}
                    />
                  </RowActions>
                )
              }
            >
              <Mono>{summary.nats_username}</Mono>
            </DetailRow>
            <PrtgNameRow probeId={summary.id} />
            <DetailRow label={t('probes.probeId')}>
              <Mono truncate>{inventory.probe_id ?? '—'}</Mono>
            </DetailRow>
            <RouteRows detail={detail} />
            <OverlayRow probeId={summary.id} username={summary.nats_username} />
            {/* Presence only. The value is behind an audited, explicit reveal. */}
            <DetailRow
              label={t('probes.accessKey')}
              actions={
                inventory.access_key_present &&
                can('credential.read') && (
                  <RowActions>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={Eye}
                      onClick={() => setAccessKeyOpen(true)}
                    >
                      {t('common.reveal')}
                    </Button>
                  </RowActions>
                )
              }
            >
              {inventory.access_key_present ? (
                <span className="text-ink-3">{t('common.hidden')}</span>
              ) : (
                <Badge tone="warn">{t('common.none')}</Badge>
              )}
            </DetailRow>
          </dl>
        </Card>

        {/* On a phone the state comes first: that is what somebody opens
            the page for at night, and the identity rarely changes. */}
        <Card
          title={t('probes.actualState')}
          className="max-md:order-first"
          action={
            dated && summary.observed_at ? (
              <span className="text-ink-3 text-xs">
                {t('common.stale', { time: formatRelative(summary.observed_at) })}
              </span>
            ) : undefined
          }
        >
          <dl>
            <DetailRow label={t('probes.columns.service')}>
              <ObservedStateCell kind="service" value={summary.service} dated={dated} />
            </DetailRow>
            <DetailRow label={t('probes.columns.version')}>
              <span className="flex flex-wrap items-center gap-2">
                <Mono className={dated ? 'text-ink-3' : undefined}>
                  {summary.package_version ?? '—'}
                </Mono>
                <PackageUpdateBadge version={summary.package_update} />
              </span>
            </DetailRow>
            {/* Asked of the NATS server on every load, not of the probe, so
                it stays current - and green beside an unreachable management
                route is the useful news that the probe still measures. */}
            <DetailRow label={t('probes.columns.nats')}>
              <StateCell kind="nats" value={summary.nats_connection} />
            </DetailRow>
            <DetailRow label={t('probes.caFingerprint')}>
              <span className="flex items-center gap-2">
                <ObservedStateCell kind="ca" value={summary.ca_state} dated={dated} />
                <Mono className="text-ink-3">
                  {shortFingerprint(observed?.ca_sha256)}
                </Mono>
              </span>
            </DetailRow>
            <DetailRow label={t('probes.helper')}>
              <span className={clsx('flex items-center gap-2', dated && 'text-ink-3')}>
                {observed?.helper_version != null
                  ? t('probes.helperVersion', { version: observed.helper_version })
                  : t('probes.helperUnknown')}
                {summary.helper_outdated && (
                  <Badge tone="warn">{t('probes.helperOutdated')}</Badge>
                )}
              </span>
            </DetailRow>
            <ReturnChannelRow probeId={summary.id} channel={detail.return_channel} />
            <DetailRow label={t('probes.columns.observed')}>
              {summary.observed_at
                ? formatRelative(summary.observed_at)
                : t('common.never')}
            </DetailRow>
          </dl>
        </Card>

        {reveal.error && (
          <div className="xl:col-span-2">
            <ErrorDetails error={reveal.error} />
          </div>
        )}

        {/* Where everything about this probe is managed. The probe is an
            aggregate of things owned by other pages - variants on the sensor
            page, endpoints on the iperf pages, the overlay on its own page -
            and until now nothing said so. One row per aspect: the current
            value, and the way to the page that changes it. */}
        <Card title={t('probes.atAGlance.title')} className="xl:col-span-2">
          <dl>
            <DetailRow
              label={t('probes.atAGlance.sensors')}
              actions={
                <RowActions>
                  <ButtonLink to="?tab=sensors" icon={ArrowRight}>
                    {t('probes.atAGlance.manage')}
                  </ButtonLink>
                </RowActions>
              }
            >
              {t('probes.atAGlance.sensorsValue', {
                count: inventory.assigned_sensors.length,
              })}
            </DetailRow>
            <DetailRow
              label={t('probes.atAGlance.endpoints')}
              actions={
                <RowActions>
                  <ButtonLink to="/infrastructure/iperf" icon={ArrowRight}>
                    {t('probes.atAGlance.manage')}
                  </ButtonLink>
                </RowActions>
              }
            >
              {inventory.known_iperf_endpoints.length > 0
                ? inventory.known_iperf_endpoints.join(', ')
                : t('probes.atAGlance.none')}
            </DetailRow>
          </dl>
        </Card>
      </div>

      {revealingPassword && (
        <RevealPasswordDialog
          username={summary.nats_username}
          onClose={() => setRevealingPassword(false)}
        />
      )}
      {rotating && (
        <RotateAccountDialog
          username={summary.nats_username}
          probeEnrolled
          onClose={() => setRotating(false)}
        />
      )}

      {accessKeyOpen && (
        <Dialog
          title={t('probes.accessKeyRevealTitle', {
            probe: announcedName ?? summary.nats_username,
          })}
          onClose={hideAccessKey}
          size="md"
        >
          {accessKey === null ? (
            <>
              <p className="text-ink-2 text-sm">{t('probes.accessKeyIntro')}</p>
              {reveal.error && (
                <div className="mt-3">
                  <ErrorDetails error={reveal.error} />
                </div>
              )}
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={hideAccessKey}>
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="primary"
                  icon={Eye}
                  disabled={reveal.isPending}
                  onClick={() =>
                    reveal.mutate(summary.id, {
                      onSuccess: (revealed) => setAccessKey(revealed.access_key),
                    })
                  }
                >
                  {t('probes.accessKeyShow')}
                </Button>
              </div>
            </>
          ) : (
            <>
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
            </>
          )}
        </Dialog>
      )}
    </>
  )
}

/**
 * A state the probe reported, drawn without its colour once it is old.
 *
 * A green dot says "running" in the present tense; on a value from hours ago
 * it says something nobody has checked. The label stays, so the last known
 * answer is still there to read.
 */
function ObservedStateCell({
  kind,
  value,
  dated,
}: {
  kind: 'service' | 'ca'
  value: ServiceState | CaState
  dated: boolean
}) {
  const { t } = useTranslation()
  if (!dated) return <StateCell kind={kind} value={value} />
  return (
    <span className="text-ink-3 inline-flex items-center gap-1.5 text-sm">
      <Dot tone="neutral" />
      {t(`status.${kind}.${value}`)}
    </span>
  )
}

/**
 * What an operator needs when the probe stops answering: since when it has
 * been silent, which way management last reached it, and what to do.
 *
 * The last contact is the last answer on the management route, not the time
 * of the failed attempt - that one is when the platform last asked.
 */
function UnreachableBanner({
  detail,
  attemptAt,
  onDetails,
}: {
  detail: ProbeDetail
  attemptAt: string
  onDetails?: () => void
}) {
  const { t } = useTranslation()
  const route = detail.management_route
  const host = route?.host ?? detail.inventory.ssh_host
  // NATS is asked on every load, not the probe. A probe still connected
  // there measures and reports to PRTG; only management lost it, which is a
  // warning, not an outage - and the banner says so first.
  const measuring = detail.summary.nats_connection === 'connected'
  return (
    <Banner
      tone={measuring ? 'warn' : 'danger'}
      title={t(measuring ? 'probes.fault.managementOnlyTitle' : 'probes.fault.unreachableTitle')}
      action={
        onDetails && (
          <Button size="sm" variant="ghost" onClick={onDetails}>
            {t('probes.fault.details')}
          </Button>
        )
      }
    >
      {measuring && <p className="mb-1">{t('probes.fault.stillMeasuring')}</p>}
      <p>
        {route
          ? t('probes.fault.lastContact', {
              time: formatRelative(route.observed_at),
              route: t(`probes.routes.${route.via}`),
              host: route.host,
            })
          : t('probes.fault.neverAnswered')}{' '}
        {t('probes.fault.lastAttempt', { time: formatRelative(attemptAt) })}
      </p>
      <p className="mt-1">{t('probes.fault.nextStep', { host })}</p>
    </Banner>
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
          <Badge tone="accent" quiet>{row.reserved_by}</Badge>
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
            <Badge tone="warn" quiet>{row.connection}</Badge>
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
              icon={LockOpen}
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
              icon={Lock}
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
  const { can } = useAuth()
  const [removing, setRemoving] = useState<string | null>(null)
  const { t } = useTranslation()
  const sensors = detail.sensors
  const navigate = useNavigate()
  const removeSensor = useRemoveSensorFromProbe()
  // Which sensor the dialog opens preselected on - null closed, '' free pick.
  const [deploying, setDeploying] = useState<string | null>(null)
  // Every sensor's last day in one request; a row shows its own part. The
  // details per network are on the sensor's page a click away.
  const { data: timeline } = useSensorLogTimeline(
    can('sensor_log.read') ? detail.summary.nats_username : undefined,
    undefined,
  )

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
      key: 'runs',
      header: t('probeSensor.last24h'),
      cell: (row) =>
        timeline ? (
          <MiniTimeline timeline={timeline} sensor={row.name} />
        ) : (
          <span className="text-ink-3">—</span>
        ),
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
          <RowActions>
            {/* The row already names the cure's target: outdated and drifted
                are fixed by rolling the sensor out again, and until now the
                only button in reach was "remove". */}
            {(row.status === 'outdated' || row.status === 'drifted') && (
              <PermissionGate permission="deployment.create">
                <Button size="sm" icon={Upload} onClick={() => setDeploying(row.name)}>
                  {t('sensors.updateOnProbe')}
                </Button>
              </PermissionGate>
            )}
            {/* Removing takes the sensor off the probe: in the menu, and
                confirmed before it runs. */}
            <ActionMenu
              label={t('common.moreActions')}
              actions={
                can('sensor.remove')
                  ? [
                      {
                        key: 'remove',
                        label: t('sensors.remove'),
                        icon: Trash2,
                        tone: 'danger',
                        disabled: removeSensor.isPending,
                        onSelect: () => setRemoving(row.name),
                      },
                    ]
                  : []
              }
            />
          </RowActions>
        ),
    },
  ]

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <PermissionGate permission="deployment.create">
          <Button size="sm" variant="primary" icon={Upload} onClick={() => setDeploying('')}>
            {t('probes.deploySensor')}
          </Button>
        </PermissionGate>
      </div>
      <DataTable
        rows={sensors}
        columns={columns}
        rowKey={(row) => row.name}
        rowHref={(row) => `/probes/${probeId}/sensors/${encodeURIComponent(row.name)}`}
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
      {removing !== null && (
        <ConfirmDialog
          title={t('probes.removeSensorTitle', { sensor: removing })}
          body={t('probes.removeSensorConfirm', { sensor: removing })}
          confirmLabel={t('sensors.remove')}
          cancelLabel={t('common.cancel')}
          pending={removeSensor.isPending}
          error={removeSensor.error ? <ErrorDetails error={removeSensor.error} /> : undefined}
          onConfirm={() =>
            removeSensor.mutate(
              { probeId, sensor: removing },
              {
                onSuccess: (accepted) => {
                  setRemoving(null)
                  navigate(`/jobs/${accepted.job_id}`)
                },
              },
            )
          }
          onClose={() => setRemoving(null)}
        />
      )}
    </div>
  )
}

/**
 * What differs from the wanted state, at the top of the overview. Shown only
 * when there is something; a probe that matches says nothing here.
 */
function DeviationsSection({
  probeId,
  detail,
  headerHasPrimary,
}: {
  probeId: string
  detail: ProbeDetail
  /** The header already carries the page's one primary action. */
  headerHasPrimary: boolean
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [preview, setPreview] = useState(false)
  const plan = useProbePlan(probeId, preview)
  const execute = useExecuteReconcile()

  return (
    <div className="space-y-4">
      <Card
        title={t('probes.deviationsTitle')}
        action={
          // Opens the plan, changes nothing: secondary, and gone once the
          // plan is on screen with its own button to run it.
          !preview && (
            <PermissionGate permission="probe.reconcile">
              <Button size="sm" onClick={() => setPreview(true)}>
                {t('probes.fixDeviations')}
              </Button>
            </PermissionGate>
          )
        }
        dense
      >
        <ul>
          {detail.deviations.map((deviation, index) => (
            <DeviationRow key={index} deviation={deviation} sensors={detail.sensors} />
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
                      <PlannedActionText action={action} sensors={detail.sensors} />
                      {action.risk_key && (
                        <span className="text-warn block text-xs">
                          {t(action.risk_key)}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setPreview(false)}>
                  {t('common.cancel')}
                </Button>
                <Button
                  variant={headerHasPrimary ? 'secondary' : 'primary'}
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

/** The installed and wanted version of a sensor, when both are plain. */
function versionStep(
  sensors: SensorState[],
  name: string,
): { from: string | null; to: string | null } {
  const sensor = sensors.find((entry) => entry.name === name)
  return {
    from: sensor?.installed_version ?? null,
    to: sensor?.desired_version ?? null,
  }
}

/**
 * One planned step as a sentence. A rollout names its versions from the
 * sensor table: the plan's own version field holds a checksum for a sensor
 * that was changed on the probe, which says nothing to the person confirming.
 */
function PlannedActionText({
  action,
  sensors,
}: {
  action: PlannedAction
  sensors: SensorState[]
}) {
  const { t } = useTranslation()
  if (action.kind === 'deploy_sensor') {
    const sensor = action.params.sensor ?? action.target
    const { from, to } = versionStep(sensors, sensor)
    if (to) {
      if (!from) return <>{t('probes.planStep.install', { sensor, to })}</>
      if (from !== to) return <>{t('probes.planStep.update', { sensor, from, to })}</>
      return <>{t('probes.planStep.redeploy', { sensor, to })}</>
    }
  }
  return <>{t(action.description_key, action.params)}</>
}

function DeviationRow({
  deviation,
  sensors,
}: {
  deviation: Deviation
  sensors: SensorState[]
}) {
  const { t, i18n } = useTranslation()
  // A finding newer than this interface gets a plain sentence; its raw kind
  // follows as the detail, not as the headline.
  const known = i18n.exists(`deviations.${deviation.kind}`)
  const remediationKey = `deviations.remediation.${deviation.remediation}`
  const step =
    deviation.kind === 'sensor_outdated'
      ? versionStep(sensors, deviation.object_ref)
      : null
  // An outdated sensor reads best with its versions in the sentence. Where
  // the versions agree, it is the sensor's tool that is behind, and the
  // comparison below says which.
  const inSentence = step?.from && step.to && step.from !== step.to ? step : null
  return (
    <li className="border-rule flex items-start gap-3 border-b px-4 py-2.5 last:border-0">
      <span className="pt-1.5">
        <Dot tone={SEVERITY_TONE[deviation.severity]} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-ink text-sm">
          {known
            ? t(`deviations.${deviation.kind}`, {
                ...deviation.params,
                sensor: deviation.object_ref,
              })
            : t('deviations.unknown')}
          {inSentence &&
            ` ${t('probes.deviationVersions', { from: inSentence.from, to: inSentence.to })}`}
        </p>
        {!known && (
          <p className="text-ink-3 mt-0.5 font-mono text-xs break-all">
            {deviation.object_ref ? `${deviation.kind} · ${deviation.object_ref}` : deviation.kind}
          </p>
        )}
        {/* The raw comparison stays for whoever has to look closer: a
            checksum or a tool path, second to the sentence. */}
        {!inSentence && (deviation.expected || deviation.actual) && (
          <p className="text-ink-3 mt-0.5 font-mono text-xs break-all">
            {shortFingerprint(deviation.actual) ?? '—'} → {shortFingerprint(deviation.expected) ?? '—'}
          </p>
        )}
        {/* Set for every finding since the beginning, shown nowhere: what to
            do about it stayed the reader's own conclusion. */}
        {deviation.remediation && i18n.exists(remediationKey) && (
          <p className="text-ink-3 mt-0.5 text-xs">{t(remediationKey)}</p>
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
        <DetailRow label={t('probes.diag.hostname')}>
          <Mono>{observed?.hostname ?? '—'}</Mono>
        </DetailRow>
        <DetailRow label={t('probes.diag.configPath')}>
          <Mono truncate>{observed?.config_path ?? '—'}</Mono>
        </DetailRow>
        <DetailRow label={t('probes.diag.caSha256')}>
          <Mono truncate>{observed?.ca_sha256 ?? '—'}</Mono>
        </DetailRow>
        <DetailRow label={t('probes.pendingTransaction')}>
          <Mono>{inventory.pending_transaction || '—'}</Mono>
        </DetailRow>
      </dl>
      {observed?.error_details && (
        <>
          <p className="text-ink-3 mt-4 text-xs">{t('probes.diag.errorDetails')}</p>
          <pre className="bg-surface-2 rounded-inset text-ink-2 mt-1 max-h-48 overflow-auto p-3 font-mono text-xs whitespace-pre-wrap">
            {observed.error_details}
          </pre>
        </>
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
          <ButtonLink to="/infrastructure/iperf" size="sm" variant="primary">
              {t('probes.iperf.toEndpoints')}
          </ButtonLink>
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
 * Which way management and NATS currently reach the probe.
 *
 * Separate lines rather than part of the overlay row: a probe off the overlay
 * still has a management route worth naming, and after a probe moves the
 * question is exactly which of the two routes still works. The management
 * route is the last answer, so it dims with the rest of what the probe last
 * said once that is older than the staleness window.
 */
/**
 * How the probe is reached, each fact once.
 *
 * The route that answered last, with its address and age. The site address
 * is shown here too - as the fallback when the tunnel answered - rather than
 * in a row of its own, so neither address appears twice on the card.
 */
function RouteRows({ detail }: { detail: ProbeDetail }) {
  const { t } = useTranslation()
  const route = detail.management_route
  const site = detail.inventory.ssh_host
  return (
    <>
      <DetailRow label={t('probes.routes.management')}>
        {route ? (
          <span className={route.stale ? 'text-ink-3' : undefined}>
            <span className="flex flex-wrap items-center gap-2">
              <span>{t(`probes.routes.${route.via}`)}</span>
              <Mono>{route.host}</Mono>
              <span className="text-xs">{formatRelative(route.observed_at)}</span>
            </span>
            {route.via === 'tunnel' && site && site !== route.host && (
              <span className="text-ink-3 block text-xs">
                {t('probes.routes.fallback', { address: site })}
              </span>
            )}
          </span>
        ) : (
          <span className="flex flex-wrap items-center gap-2">
            <Mono>{site}</Mono>
            <span className="text-ink-3 text-xs">{t('probes.routes.never')}</span>
          </span>
        )}
      </DetailRow>
      <DetailRow label={t('probes.routes.nats')}>
        {t(`probes.routes.${detail.nats_route ?? 'unknown'}`)}
      </DetailRow>
    </>
  )
}

/**
 * The probe's overlay address, its mode, and the switch for it.
 *
 * On the identity card rather than a card of its own: for a probe on the
 * overlay this is a second address it answers on, which is what the rest of
 * that list is about. A probe that is not on it says so in one line instead
 * of taking up a panel to say nothing.
 *
 * The switch starts the overlay area's own job through its own confirmation:
 * a second way of changing the mode would be a second set of rules about when
 * it is safe. The hub stays in the overlay area.
 */
function OverlayRow({ probeId, username }: { probeId: string; username: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { can } = useAuth()
  const { data } = useOverlay()
  const attach = useOverlayAttach()
  const changeMode = useOverlayMode()
  const [dialog, setDialog] = useState<'add' | 'mode' | null>(null)
  const peer = data?.peers.find((entry) => entry.nats_username === username)

  if (!data?.enabled) return null
  const toJob = {
    onSuccess: (job: { job_id: string }) => navigate(`/jobs/${job.job_id}`),
  }
  const manage = can('overlay.manage')
  return (
    <DetailRow
      label={t('probes.overlay')}
      actions={
        manage && (
          <RowActions>
            {peer ? (
              <Button
                size="sm"
                variant="ghost"
                icon={SlidersHorizontal}
                onClick={() => setDialog('mode')}
              >
                {t('infrastructure.overlay.actions.mode')}
              </Button>
            ) : (
              <Button size="sm" variant="ghost" icon={Plus} onClick={() => setDialog('add')}>
                {t('probes.overlayAdd')}
              </Button>
            )}
          </RowActions>
        )
      }
    >
      {/* Mode only: the tunnel address and whether NATS takes it are in the
          route rows above. */}
      {t(`infrastructure.overlay.modes.${peer?.mode ?? 'off'}.name`)}
      {dialog === 'add' && (
        <OverlayAttachDialog
          title={t('probes.overlayAddTitle')}
          initial={data.default_mode}
          pending={attach.isPending}
          onConfirm={(mode) => attach.mutate({ probe_ids: [probeId], mode }, toJob)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'mode' && peer && (
        <OverlayModeDialog
          initial={peer.mode}
          pending={changeMode.isPending}
          onApply={(mode) => changeMode.mutate({ probe_ids: [probeId], mode }, toJob)}
          onClose={() => setDialog(null)}
        />
      )}
      {attach.error && <ErrorDetails error={attach.error} />}
      {changeMode.error && <ErrorDetails error={changeMode.error} />}
    </DetailRow>
  )
}
