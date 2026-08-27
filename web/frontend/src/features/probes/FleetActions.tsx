import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useFleetAction, type FleetAction } from '@/api/hooks'
import type { ApiError } from '@/api/client'
import type { ProbeSummary } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { Button, Dialog, Mono } from '@/components/ui/primitives'

/**
 * The six probe actions, applied to a selection.
 *
 * Each one exists on the detail page as a single button. What is new here is
 * only the reach: the list already knows which rows need a helper and which
 * carry deviations, and this is what lets that knowledge be acted on without
 * one visit per row.
 */

interface Spec {
  action: FleetAction
  labelKey: string
  permission: string
  /**
   * Whether this action can do anything for that probe. An action nothing in
   * the selection matches is not offered at all - a bulk button that queues
   * twelve jobs which fail on arrival is worse than no button.
   */
  appliesTo: (probe: ProbeSummary) => boolean
}

const SPECS: Spec[] = [
  {
    action: 'refresh',
    labelKey: 'probes.refreshState',
    permission: 'probe.read',
    appliesTo: () => true,
  },
  {
    action: 'validate',
    labelKey: 'probes.validate',
    permission: 'probe.read',
    appliesTo: () => true,
  },
  {
    action: 'install-ca',
    labelKey: 'probes.installCa',
    permission: 'probe.update',
    appliesTo: () => true,
  },
  {
    action: 'helper-update',
    labelKey: 'probes.updateHelper',
    permission: 'probe.update',
    // A helper that reports no version at all predates signed updates, so the
    // management channel cannot reach it - the same test the detail page makes
    // before it enables the button.
    appliesTo: (probe) => probe.helper_version != null,
  },
  {
    action: 'configure',
    labelKey: 'probes.configure',
    permission: 'probe.update',
    appliesTo: () => true,
  },
  {
    action: 'reconcile',
    labelKey: 'probes.fixDeviations',
    permission: 'probe.reconcile',
    // Nothing to reconcile is a job that plans, finds nothing and stops.
    appliesTo: (probe) => probe.deviation_count > 0,
  },
]

export function FleetActionBar({
  probes,
  onError,
  onDone,
}: {
  /** The selected probes, as objects - the buttons decide by what they say. */
  probes: ProbeSummary[]
  onError: (error: ApiError | Error | null) => void
  onDone: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const run = useFleetAction()
  const [confirming, setConfirming] = useState<Spec | null>(null)

  function targetsOf(spec: Spec): ProbeSummary[] {
    return probes.filter(spec.appliesTo)
  }

  function execute(spec: Spec) {
    const targets = targetsOf(spec)
    onError(null)
    run.mutate(
      { action: spec.action, probeIds: targets.map((probe) => probe.id) },
      {
        onSuccess: (accepted) => {
          setConfirming(null)
          onDone()
          // Into the job, because that is the only place the fan-out reports
          // from: which probe answered, which one did not, and why.
          navigate(`/jobs/${accepted.job_id}`)
        },
        onError: (error) => {
          setConfirming(null)
          onError(error)
        },
      },
    )
  }

  function start(spec: Spec) {
    // One probe is what the detail page does on a single click, so it happens
    // on a single click here too. More than one is a decision worth showing
    // the targets for first.
    if (targetsOf(spec).length > 1) setConfirming(spec)
    else execute(spec)
  }

  const offered = SPECS.filter((spec) => targetsOf(spec).length > 0)

  return (
    <>
      {offered.map((spec) => (
        <PermissionGate key={spec.action} permission={spec.permission}>
          <Button size="sm" onClick={() => start(spec)} disabled={run.isPending}>
            {t(spec.labelKey)}
          </Button>
        </PermissionGate>
      ))}

      {confirming && (
        <ConfirmFleetAction
          label={t(confirming.labelKey)}
          targets={targetsOf(confirming)}
          skipped={probes.length - targetsOf(confirming).length}
          pending={run.isPending}
          onCancel={() => setConfirming(null)}
          onConfirm={() => execute(confirming)}
        />
      )}
    </>
  )
}

function ConfirmFleetAction({
  label,
  targets,
  skipped,
  pending,
  onCancel,
  onConfirm,
}: {
  label: string
  targets: ProbeSummary[]
  skipped: number
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const { t } = useTranslation()

  return (
    <Dialog
      title={t('probes.fleet.confirmTitle', { action: label })}
      onClose={onCancel}
    >
      <p className="text-ink-2 text-sm">
        {t('probes.fleet.confirmBody', { count: targets.length })}
      </p>
      {/* Named, not counted: "twelve probes" is not something anyone can
          check, and this is the last screen before the job starts. */}
      <ul className="border-rule mt-3 max-h-48 space-y-1 overflow-y-auto border-t pt-3">
        {targets.map((probe) => (
          <li key={probe.id}>
            <Mono className="text-ink-2">{probe.nats_username}</Mono>
          </li>
        ))}
      </ul>
      {skipped > 0 && (
        <p className="text-ink-3 mt-3 text-xs">
          {t('probes.fleet.skipped', { count: skipped })}
        </p>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button variant="primary" onClick={onConfirm} disabled={pending}>
          {t('common.confirm')}
        </Button>
      </div>
    </Dialog>
  )
}
