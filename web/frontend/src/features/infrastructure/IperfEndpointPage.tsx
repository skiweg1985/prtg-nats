import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useIperfEndpoint, useProbes, useSensor, useSensors } from '@/api/hooks'
import type { IperfEndpoint, IperfHolder } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { ProbeLink } from '@/components/ui/ProbeLink'
import {
  Badge,
  Banner,
  Button,
  Card,
  DetailRow,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { DeployDialog } from '@/features/deployments/DeployDialog'
import { formatDateTime } from '@/utils/format'

import { RemoveDialog, RotateDialog } from './IperfEndpointDialogs'
import { IperfProbesDialog } from './IperfProbesDialog'

/**
 * One measurement endpoint, and what it takes to actually measure against it.
 *
 * The chain is registry -> credentials on a probe -> sensor on that probe ->
 * a sensor object in PRTG, and each link used to live on a different page with
 * nothing saying which one was missing. This page is that answer: the state,
 * who holds the credentials, whether the sensor that reads them is even
 * installed, and the parameter the object in PRTG needs - per probe, because
 * that is what the answer depends on.
 */
export function IperfEndpointPage() {
  const { t } = useTranslation()
  const { name } = useParams<{ name: string }>()
  const { data, isLoading, error, refetch } = useIperfEndpoint(name)
  const [dialog, setDialog] = useState<
    'assign' | 'rotate' | 'remove' | 'deploy' | null
  >(null)

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const held = new Map(data.holders.map((holder) => [holder.probe, holder.endpoints_held]))

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <Link to="/infrastructure/iperf" className="text-ink-3 text-xs">
            ← {t('infrastructure.iperfTitle')}
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-lg">{data.name}</h1>
            {!data.managed && (
              <Badge tone="neutral">{t('infrastructure.iperf.foreign')}</Badge>
            )}
            {data.holders.length === 0 && (
              <Badge tone="warn">{t('infrastructure.iperf.notDeployed')}</Badge>
            )}
          </div>
          <p className="text-ink-2 text-sm">
            <Mono>
              {data.host}:{data.port}
            </Mono>
          </p>
        </div>
        <span className="flex flex-wrap gap-2">
          <PermissionGate permission="sensor.deploy">
            <Button size="sm" variant="primary" onClick={() => setDialog('assign')}>
              {t('infrastructure.iperf.assign')}
            </Button>
          </PermissionGate>
          <PermissionGate permission="iperf.manage">
            {data.managed && (
              <Button size="sm" onClick={() => setDialog('rotate')}>
                {t('infrastructure.iperf.rotate')}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setDialog('remove')}>
              {t('common.remove')}
            </Button>
          </PermissionGate>
        </span>
      </header>

      {data.holders.length === 0 && (
        <Banner
          tone="warn"
          title={t('infrastructure.iperf.notDeployedTitle')}
          action={
            <PermissionGate permission="sensor.deploy">
              <Button size="sm" variant="primary" onClick={() => setDialog('assign')}>
                {t('infrastructure.iperf.assign')}
              </Button>
            </PermissionGate>
          }
        >
          {t('infrastructure.iperf.notDeployedBody')}
        </Banner>
      )}

      <SensorMissingBanner endpoint={data} onDeploy={() => setDialog('deploy')} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('infrastructure.iperf.stateTitle')}>
          <dl>
            <DetailRow label={t('infrastructure.iperf.host')}>
              <Mono>{data.host}</Mono>
            </DetailRow>
            <DetailRow label={t('infrastructure.iperf.iperfPort')}>
              <Mono>{data.port}</Mono>
            </DetailRow>
            <DetailRow label={t('infrastructure.iperf.measureUser')}>
              {data.username ? (
                <Mono>{data.username}</Mono>
              ) : (
                <span className="text-ink-3">
                  {t('infrastructure.iperf.unauthenticated')}
                </span>
              )}
            </DetailRow>
            <DetailRow label={t('infrastructure.iperf.operator')}>
              {data.managed
                ? t('infrastructure.iperf.operatorOwn')
                : t('infrastructure.iperf.foreign')}
            </DetailRow>
            <DetailRow label={t('infrastructure.iperf.publicKey')}>
              {data.has_public_key
                ? t('infrastructure.iperf.publicKeyPresent')
                : t('infrastructure.iperf.publicKeyMissing')}
            </DetailRow>
            <DetailRow label={t('common.updated')}>
              <span className="text-ink-3 text-xs">
                {data.updated_at ? formatDateTime(data.updated_at) : '—'}
              </span>
            </DetailRow>
          </dl>
        </Card>

        <Card title={t('infrastructure.iperf.prtgTitle')}>
          <div className="space-y-3">
            <p className="text-ink-2 text-sm">{t('infrastructure.iperf.prtgIntro')}</p>
            {data.holders.length === 0 ? (
              <p className="text-ink-3 text-sm">
                {t('infrastructure.iperf.manualHint')}
              </p>
            ) : (
              <ul className="divide-rule divide-y">
                {data.holders.map((holder) => (
                  <HolderRow key={holder.probe} holder={holder} />
                ))}
              </ul>
            )}
          </div>
        </Card>
      </div>

      {dialog === 'assign' && (
        <IperfProbesDialog
          endpoint={data}
          heldByProbe={held}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'rotate' && (
        <RotateDialog endpoint={data} onClose={() => setDialog(null)} />
      )}
      {dialog === 'remove' && (
        <RemoveDialog endpoint={data} onClose={() => setDialog(null)} />
      )}
      {dialog === 'deploy' && (
        <DeployDialogForEndpoint endpoint={data} onClose={() => setDialog(null)} />
      )}
    </div>
  )
}

/**
 * One probe, and the parameter its PRTG objects need for this endpoint.
 *
 * An empty line is not a missing answer but the whole answer: that probe holds
 * this endpoint alone, so it carries the "default" alias and the sensor reads
 * address, port and user out of it without being told which one to take.
 */
function HolderRow({ holder }: { holder: IperfHolder }) {
  const { t } = useTranslation()

  return (
    <li className="flex flex-wrap items-start justify-between gap-2 py-2">
      <div className="min-w-0">
        <ProbeLink username={holder.probe} />
        <p className="text-ink-3 mt-0.5 text-xs">
          {t('infrastructure.iperf.endpointsHeld', { count: holder.endpoints_held })}
        </p>
      </div>
      {holder.parameter_line === '' ? (
        <div className="text-right">
          <Badge tone="ok">{t('infrastructure.iperf.noParameterNeeded')}</Badge>
          <p className="text-ink-3 mt-0.5 max-w-xs text-xs">
            {t('infrastructure.iperf.defaultAliasHint')}
          </p>
        </div>
      ) : (
        <div className="flex items-start gap-2">
          <code className="bg-surface-2 rounded-inset text-ink px-2 py-1 font-mono text-xs">
            {holder.parameter_line}
          </code>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void navigator.clipboard.writeText(holder.parameter_line)}
          >
            {t('common.copy')}
          </Button>
        </div>
      )}
    </li>
  )
}

/**
 * Credentials on a probe that has no sensor to use them.
 *
 * The state is silent otherwise: the assignment succeeded, the endpoint lists
 * the probe, and nothing measures because the script was never rolled out. It
 * is the last open link in the chain, so the banner carries the button that
 * closes it rather than a sentence pointing at another page.
 */
function SensorMissingBanner({
  endpoint,
  onDeploy,
}: {
  endpoint: IperfEndpoint
  onDeploy: () => void
}) {
  const { t } = useTranslation()
  const { data: sensors } = useSensors()
  const reader = sensors?.find((sensor) => sensor.iperf_kind === endpoint.kind)
  // Which probes report it installed is on the sensor, not on the listing.
  const { data: detail } = useSensor(reader?.name)

  if (!detail) return null
  const installed = new Set(detail.probes)
  const missing = endpoint.holders
    .map((holder) => holder.probe)
    .filter((probe) => !installed.has(probe))
  if (missing.length === 0) return null

  return (
    <Banner
      tone="warn"
      title={t('infrastructure.iperf.sensorMissingTitle')}
      action={
        <PermissionGate permission="deployment.create">
          <Button size="sm" variant="primary" onClick={onDeploy}>
            {t('sensors.deployTo')}
          </Button>
        </PermissionGate>
      }
    >
      {t('infrastructure.iperf.sensorMissingBody', {
        sensor: detail.name,
        probes: missing.join(', '),
      })}
    </Banner>
  )
}

/**
 * The rollout dialog, preselected with the probes that hold this endpoint.
 *
 * It takes record ids while everything around an endpoint speaks NATS account
 * names, so the fleet listing translates - and a name it cannot place is left
 * out rather than guessed at.
 */
function DeployDialogForEndpoint({
  endpoint,
  onClose,
}: {
  endpoint: IperfEndpoint
  onClose: () => void
}) {
  const { data: sensors } = useSensors()
  const { data: probes } = useProbes()

  const reader = sensors?.find((sensor) => sensor.iperf_kind === endpoint.kind)
  const holders = new Set(endpoint.holders.map((holder) => holder.probe))
  const probeIds = (probes ?? [])
    .filter((probe) => holders.has(probe.nats_username))
    .map((probe) => probe.id)

  return (
    <DeployDialog sensorName={reader?.name} probeIds={probeIds} onClose={onClose} />
  )
}
