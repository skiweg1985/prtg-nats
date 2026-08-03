import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useConfigureProbe,
  useExecuteReconcile,
  useProbe,
  useProbeAction,
  useProbePlan,
  useRefreshProbe,
  useRemoveSensorFromProbe,
  useUnenrollProbe,
  type UnenrollOptions,
} from '@/api/hooks'
import type { Deviation, ProbeDetail, SensorState } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Banner,
  Button,
  Card,
  DetailRow,
  Dot,
  EmptyState,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { ProbeStatusBadge, SensorStatusBadge, StateCell } from '@/components/ui/status'
import { formatRelative, shortFingerprint } from '@/utils/format'

const TABS = ['overview', 'sensors', 'deviations', 'diagnostics'] as const
type Tab = (typeof TABS)[number]

export function ProbeDetailPage() {
  const { t } = useTranslation()
  const { probeId } = useParams<{ probeId: string }>()
  const { data, isLoading, error, refetch } = useProbe(probeId)
  const navigate = useNavigate()
  const refresh = useRefreshProbe()
  const installCa = useProbeAction('install-ca')
  const validate = useProbeAction('validate')
  const configure = useConfigureProbe()
  const unenroll = useUnenrollProbe()
  const [tab, setTab] = useState<Tab>('overview')
  const [confirmUnenroll, setConfirmUnenroll] = useState(false)
  // Every option starts off. Retiring a probe is destructive enough on its
  // own; what else goes has to be chosen, not merely left checked.
  const [cleanup, setCleanup] = useState<Required<UnenrollOptions>>({
    removeSensors: false,
    uninstallMpp: false,
    deleteAccount: false,
  })

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!data || !probeId) return null

  const { summary, observed } = data

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <Link to="/probes" className="text-ink-3 text-xs">
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
              onClick={() => validate.mutate(probeId)}
              disabled={validate.isPending}
            >
              {t('probes.validate')}
            </Button>
          </PermissionGate>
          <PermissionGate permission="probe.update">
            <Button
              size="sm"
              onClick={() => installCa.mutate(probeId)}
              disabled={installCa.isPending}
            >
              {t('probes.installCa')}
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
        <div
          className="fixed inset-0 z-(--z-dialog) flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          onClick={(event) => {
            if (event.target === event.currentTarget) setConfirmUnenroll(false)
          }}
        >
          <div className="w-full max-w-md">
            <Card title={t('confirm.title')}>
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
                <PermissionGate permission="credential.rotate">
                  <CleanupOption
                    label={t('probes.cleanup.deleteAccount')}
                    hint={t('probes.cleanup.deleteAccountHint')}
                    checked={cleanup.deleteAccount}
                    onChange={(checked) =>
                      setCleanup({ ...cleanup, deleteAccount: checked })
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
            </Card>
          </div>
        </div>
      )}

      {refresh.error && <ErrorDetails error={refresh.error} target={summary.nats_username} />}
      {observed && !observed.reachable && (
        <Banner tone="danger" title={t('status.probe.unreachable')}>
          {observed.error_details ?? t('errors.probe.unreachable', { probe: summary.nats_username })}
        </Banner>
      )}

      <nav className="border-rule flex gap-1 border-b">
        {TABS.map((entry) => (
          <button
            key={entry}
            type="button"
            onClick={() => setTab(entry)}
            className={
              tab === entry
                ? 'border-accent text-ink -mb-px border-b-2 px-3 py-2 text-sm font-medium'
                : 'text-ink-3 hover:text-ink -mb-px border-b-2 border-transparent px-3 py-2 text-sm'
            }
          >
            {t(`probes.tabs.${entry === 'deviations' ? 'configuration' : entry}`)}
            {entry === 'deviations' && data.deviations.length > 0 && (
              <Badge tone="warn" className="ml-2">
                {data.deviations.length}
              </Badge>
            )}
          </button>
        ))}
      </nav>

      {tab === 'overview' && <OverviewTab detail={data} />}
      {tab === 'sensors' && <SensorsTab probeId={probeId} sensors={data.sensors} />}
      {tab === 'deviations' && <DeviationsTab probeId={probeId} detail={data} />}
      {tab === 'diagnostics' && <DiagnosticsTab detail={data} />}
    </div>
  )
}

function OverviewTab({ detail }: { detail: ProbeDetail }) {
  const { t } = useTranslation()
  const { summary, inventory, observed } = detail

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title={t('probes.identity')}>
        <dl>
          <DetailRow label={t('probes.natsUser')}>
            <Mono>{summary.nats_username}</Mono>
          </DetailRow>
          <DetailRow label={t('probes.probeName')}>
            {observed?.probe_name ?? inventory.probe_name ?? '—'}
          </DetailRow>
          <DetailRow label={t('probes.probeId')}>
            <Mono truncate>{inventory.probe_id ?? '—'}</Mono>
          </DetailRow>
          <DetailRow label={t('probes.sshHost')}>
            <Mono>
              {inventory.ssh_host}:{inventory.ssh_port}
            </Mono>
          </DetailRow>
          <DetailRow label={t('probes.accessKey')}>
            {/* Presence only. The value is behind an audited, explicit reveal. */}
            {inventory.access_key_present ? (
              <Badge tone="ok">{t('common.hidden')}</Badge>
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
              <Mono className="text-ink-3">{shortFingerprint(observed?.ca_sha256)}</Mono>
            </span>
          </DetailRow>
          <DetailRow label={t('probes.columns.observed')}>
            {summary.observed_at ? formatRelative(summary.observed_at) : t('common.never')}
          </DetailRow>
        </dl>
      </Card>
    </div>
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
    <label className="flex items-start gap-2 text-sm">
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        <span className="text-ink">{label}</span>
        <span className="text-ink-3 block text-xs">{hint}</span>
      </span>
    </label>
  )
}

function SensorsTab({ probeId, sensors }: { probeId: string; sensors: SensorState[] }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const removeSensor = useRemoveSensorFromProbe()

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
      key: 'interfaces',
      header: 'Interfaces',
      cell: (row) =>
        row.interfaces.length ? <Mono>{row.interfaces.join(', ')}</Mono> : '—',
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (row) =>
        row.status === 'absent' ? null : (
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
        ),
    },
  ]

  return (
    <DataTable
      rows={sensors}
      columns={columns}
      rowKey={(row) => row.name}
      emptyTitle={t('sensors.empty')}
    />
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
      <Dot tone={deviation.severity === 'critical' ? 'danger' : 'warn'} />
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
        <DetailRow label="pending transaction">
          <Mono>{inventory.pending_transaction || '—'}</Mono>
        </DetailRow>
        <DetailRow label={t('nav.iperf')}>
          {inventory.known_iperf_endpoints.length ? (
            <Mono>{inventory.known_iperf_endpoints.join(', ')}</Mono>
          ) : (
            '—'
          )}
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
