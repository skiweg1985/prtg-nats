import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useCreateDeployment, useProbes, useSensors } from '@/api/hooks'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Button, Card, Field, Mono } from '@/components/ui/primitives'

/**
 * Start a rollout.
 *
 * One dialog, not a wizard: the choice is a sensor, a set of probes and
 * whether to do it for real. A three-step flow for three fields would be
 * ceremony.
 */
export function DeployDialog({
  probeIds,
  sensorName,
  onClose,
  onDone,
}: {
  probeIds?: string[]
  sensorName?: string
  onClose: () => void
  onDone?: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: sensors } = useSensors()
  const { data: probes } = useProbes()
  const create = useCreateDeployment()

  const [sensor, setSensor] = useState(sensorName ?? '')
  const [selected, setSelected] = useState<Set<string>>(new Set(probeIds ?? []))
  const [dryRun, setDryRun] = useState(false)

  const chosenSensor = sensors?.find((entry) => entry.name === sensor)

  function submit() {
    create.mutate(
      { sensor, probe_ids: [...selected], dry_run: dryRun },
      {
        onSuccess: (deployment) => {
          onDone?.()
          onClose()
          if (deployment.job_id) navigate(`/jobs/${deployment.job_id}`)
        },
      },
    )
  }

  return (
    <div
      className="fixed inset-0 z-(--z-dialog) flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="max-h-full w-full max-w-lg overflow-auto">
        <Card title={t('deployments.create')}>
          <div className="space-y-4">
            <Field label={t('deployments.selectSensor')}>
              <select
                value={sensor}
                onChange={(event) => setSensor(event.target.value)}
                className="rounded-control border-rule-2 bg-surface text-ink border px-2.5 py-1.5 text-sm"
                disabled={Boolean(sensorName)}
              >
                <option value="">—</option>
                {sensors?.map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.name} · v{entry.version}
                  </option>
                ))}
              </select>
            </Field>

            {chosenSensor && (
              <p className="text-ink-3 text-sm">{chosenSensor.description}</p>
            )}

            <fieldset className="space-y-1.5">
              <legend className="text-ink text-sm font-medium">
                {t('deployments.selectProbes')}
              </legend>
              <div className="rounded-control border-rule max-h-56 overflow-auto border">
                {probes?.map((probe) => (
                  <label
                    key={probe.id}
                    className="border-rule hover:bg-surface-2 flex items-center gap-2 border-b px-3 py-1.5 text-sm last:border-0"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(probe.id)}
                      onChange={() =>
                        setSelected((current) => {
                          const next = new Set(current)
                          if (next.has(probe.id)) next.delete(probe.id)
                          else next.add(probe.id)
                          return next
                        })
                      }
                    />
                    <span className="min-w-0 flex-1 truncate">
                      {probe.display_name ?? probe.nats_username}
                    </span>
                    <Mono className="text-ink-3">{probe.host}</Mono>
                  </label>
                ))}
              </div>
            </fieldset>

            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(event) => setDryRun(event.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="text-ink font-medium">{t('deployments.dryRun')}</span>
                <br />
                <span className="text-ink-3 text-xs">{t('deployments.dryRunHint')}</span>
              </span>
            </label>

            {create.error && <ErrorDetails error={create.error} />}

            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>
                {t('common.cancel')}
              </Button>
              <Button
                variant="primary"
                onClick={submit}
                disabled={!sensor || selected.size === 0 || create.isPending}
              >
                {t('deployments.start')}
              </Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}
