import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useRemoveEndpoint, useRotateEndpoint } from '@/api/hooks'
import type { IperfEndpoint } from '@/api/types'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Banner, Button, Dialog } from '@/components/ui/primitives'

/**
 * Ask first, then say where it went.
 *
 * The button used to start the job on a single click and report neither its id
 * nor its failure: a new password went out to every probe measuring against
 * this endpoint and nothing on screen changed. What the tooltip said is what
 * this dialog says, at the moment it matters.
 */
export function RotateDialog({
  endpoint,
  onClose,
}: {
  endpoint: IperfEndpoint
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const rotate = useRotateEndpoint()

  return (
    <Dialog title={t('infrastructure.iperf.rotateTitle')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-ink-2 text-sm">
          {t('infrastructure.iperf.rotateBody', {
            name: endpoint.name,
            count: endpoint.holders.length,
          })}
        </p>

        {rotate.error != null && <ErrorDetails error={rotate.error} />}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            disabled={rotate.isPending}
            onClick={() =>
              rotate.mutate(endpoint.name, {
                onSuccess: (accepted) => {
                  onClose()
                  navigate(`/jobs/${accepted.job_id}`)
                },
              })
            }
          >
            {rotate.isPending
              ? t('infrastructure.iperf.rotating')
              : t('infrastructure.iperf.rotate')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

export function RemoveDialog({
  endpoint,
  onClose,
}: {
  endpoint: IperfEndpoint
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const remove = useRemoveEndpoint()
  const [keepService, setKeepService] = useState(false)

  return (
    <Dialog title={t('infrastructure.iperf.removeTitle')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-ink-2 text-sm">
          {t('infrastructure.iperf.removeBody', {
            name: endpoint.name,
            count: endpoint.holders.length,
          })}
        </p>

        {endpoint.managed ? (
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={keepService}
              onChange={(event) => setKeepService(event.target.checked)}
            />
            <span>
              <span className="text-ink">{t('infrastructure.iperf.keepService')}</span>
              <span className="text-ink-3 block text-xs">
                {t('infrastructure.iperf.keepServiceHint')}
              </span>
            </span>
          </label>
        ) : (
          <Banner tone="warn" title={t('infrastructure.iperf.foreign')}>
            {t('infrastructure.iperf.removeForeign')}
          </Banner>
        )}

        {/* The package is never uninstalled: something else on that host may
            be using it, and this platform did not always put it there. */}
        <p className="text-ink-3 text-xs">{t('infrastructure.iperf.removePackage')}</p>

        {remove.error != null && <ErrorDetails error={remove.error} />}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            disabled={remove.isPending}
            onClick={() =>
              // Taking an endpoint away runs on the host as a job like any
              // other, and the dialog closing was the only sign of it.
              remove.mutate(
                { name: endpoint.name, keepService },
                {
                  onSuccess: (accepted) => {
                    onClose()
                    navigate(`/jobs/${accepted.job_id}`)
                  },
                },
              )
            }
          >
            {t('common.remove')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
