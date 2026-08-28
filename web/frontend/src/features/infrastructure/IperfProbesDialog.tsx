import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useEndpointDeployment, useProbes } from '@/api/hooks'
import type { IperfEndpoint } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Badge, Banner, Button, Dialog } from '@/components/ui/primitives'

/**
 * Which probes measure against this endpoint.
 *
 * The list is the assignment, not a snapshot of the last rollout: a sensor
 * deployment reads the same record, so a probe taken out here stays out
 * instead of getting the credentials back with the next deployment.
 *
 * Adding and removing are separate jobs rather than one "save" of the whole
 * set. They are separate operations on the probes - one writes a credential,
 * the other takes one away - and a single button hiding both would report one
 * outcome for two things that can fail independently.
 */
export function IperfProbesDialog({
  endpoint,
  heldByProbe,
  onClose,
}: {
  endpoint: IperfEndpoint
  /** How many endpoints each probe holds in total, for the warning below. */
  heldByProbe: Map<string, number>
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: probes, isLoading } = useProbes()
  const deploy = useEndpointDeployment('deploy')
  const revoke = useEndpointDeployment('revoke')
  const [selected, setSelected] = useState<string[]>([])

  const holding = new Set(endpoint.holders.map((holder) => holder.probe))
  const running = deploy.isPending || revoke.isPending
  const chosen = new Set(selected)
  const toAdd = selected.filter((name) => !holding.has(name))
  const toRemove = selected.filter((name) => holding.has(name))

  // Crossing one endpoint takes the "default" alias off that probe, and this
  // dialog is where that happens. A probe at zero is not affected - it is
  // about to receive the alias, not lose it.
  const losingAlias = toAdd.filter((name) => (heldByProbe.get(name) ?? 0) === 1)
  const regainingAlias = toRemove.filter(
    (name) => (heldByProbe.get(name) ?? 0) === 2,
  )

  const toggle = (name: string) =>
    setSelected((current) =>
      current.includes(name)
        ? current.filter((entry) => entry !== name)
        : [...current, name],
    )

  const start = (mutation: typeof deploy, names: string[]) =>
    mutation.mutate(
      { name: endpoint.name, probes: names },
      {
        onSuccess: (accepted) => {
          onClose()
          navigate(`/jobs/${accepted.job_id}`)
        },
      },
    )

  return (
    <Dialog title={t('infrastructure.iperf.probesTitle')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-ink-2 text-sm">
          {t('infrastructure.iperf.probesBody', { name: endpoint.name })}
        </p>

        {isLoading ? (
          <p className="text-ink-3 text-sm">{t('common.loading')}</p>
        ) : !probes || probes.length === 0 ? (
          <p className="text-ink-3 text-sm">{t('infrastructure.iperf.probesEmpty')}</p>
        ) : (
          <ul className="divide-rule border-rule-2 rounded-control max-h-64 divide-y overflow-y-auto border">
            {probes.map((probe) => (
              <li key={probe.nats_username}>
                <label className="flex cursor-pointer items-center gap-2 px-2.5 py-2 text-sm">
                  <input
                    type="checkbox"
                    checked={chosen.has(probe.nats_username)}
                    onChange={() => toggle(probe.nats_username)}
                  />
                  <span className="text-ink min-w-0 flex-1 truncate">
                    {probe.display_name || probe.nats_username}
                  </span>
                  {holding.has(probe.nats_username) && (
                    <Badge tone="ok">{t('infrastructure.iperf.probeHolds')}</Badge>
                  )}
                </label>
              </li>
            ))}
          </ul>
        )}

        {/* Before the button, not after the job: this is the only place the
            change can still be reconsidered, and the sensor on the probe
            gives no warning of its own. */}
        {losingAlias.length > 0 && (
          <Banner tone="danger" title={t('infrastructure.iperf.aliasLostTitle')}>
            {t('infrastructure.iperf.aliasLostBody', {
              probes: losingAlias.join(', '),
            })}
          </Banner>
        )}
        {regainingAlias.length > 0 && (
          <Banner tone="neutral">
            {t('infrastructure.iperf.aliasReturnsBody', {
              probes: regainingAlias.join(', '),
            })}
          </Banner>
        )}

        {deploy.error != null && <ErrorDetails error={deploy.error} />}
        {revoke.error != null && <ErrorDetails error={revoke.error} />}

        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          {/* Reading who holds what is part of seeing the endpoint at all;
              changing it writes a credential to a probe, which is the
              deployer's decision rather than the endpoint owner's. */}
          <PermissionGate permission="sensor.deploy">
            <Button
              variant="danger"
              disabled={running || toRemove.length === 0}
              onClick={() => start(revoke, toRemove)}
            >
              {t('infrastructure.iperf.revokeFrom', { count: toRemove.length })}
            </Button>
            <Button
              variant="primary"
              disabled={running || toAdd.length === 0}
              onClick={() => start(deploy, toAdd)}
            >
              {t('infrastructure.iperf.deployTo', { count: toAdd.length })}
            </Button>
          </PermissionGate>
        </div>
      </div>
    </Dialog>
  )
}
