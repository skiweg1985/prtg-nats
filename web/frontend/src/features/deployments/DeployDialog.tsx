import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useCreateDeployment,
  useIperfEndpoints,
  useProbes,
  useSensor,
  useSensors,
} from '@/api/hooks'
import { useAuth } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Banner,
  Button,
  Dialog,
  Field,
  Input,
  Mono,
  Select,
  Skeleton,
} from '@/components/ui/primitives'

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
  preselect,
  onClose,
  onDone,
}: {
  probeIds?: string[]
  sensorName?: string
  /** 'outdated' ticks the probes running an older version once they are known. */
  preselect?: 'outdated'
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
  const [query, setQuery] = useState('')

  const chosenSensor = sensors?.find((entry) => entry.name === sensor)
  // Who runs which version - the badge beside each probe and the preselection
  // both come from here, so the dialog can answer "update the ones behind"
  // without the reader collecting them by hand.
  const { data: chosenDetail } = useSensor(sensor || undefined)
  const installations = useMemo(
    () => new Map(chosenDetail?.installations.map((entry) => [entry.probe, entry])),
    [chosenDetail],
  )

  const [preselected, setPreselected] = useState(false)
  useEffect(() => {
    if (preselect !== 'outdated' || preselected || !chosenDetail || !probes) return
    const behind = new Set(
      chosenDetail.installations
        .filter((entry) => !entry.current)
        .map((entry) => entry.probe),
    )
    setSelected(
      new Set(
        probes
          .filter((probe) => behind.has(probe.nats_username))
          .map((probe) => probe.id),
      ),
    )
    setPreselected(true)
  }, [preselect, preselected, chosenDetail, probes])

  // Opened from the rollout page with nothing preselected, this box is the
  // whole fleet. Picking three out of forty by scrolling is what the list next
  // door has a search box for.
  const matching = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return probes ?? []
    return (probes ?? []).filter((probe) =>
      [probe.display_name, probe.nats_username, probe.host]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(needle),
    )
  }, [probes, query])

  const allMatchingSelected =
    matching.length > 0 && matching.every((probe) => selected.has(probe.id))

  function toggleAllMatching() {
    setSelected((current) => {
      const next = new Set(current)
      for (const probe of matching) {
        if (allMatchingSelected) next.delete(probe.id)
        else next.add(probe.id)
      }
      return next
    })
  }

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
    <Dialog title={t('deployments.create')} onClose={onClose} size="md">
      <div className="space-y-4">
        <Field label={t('deployments.selectSensor')}>
          <Select
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
          </Select>
        </Field>

        {chosenSensor && (
          <p className="text-ink-3 text-sm">{chosenSensor.description}</p>
        )}

        <fieldset className="space-y-1.5">
          <legend className="text-ink text-sm font-medium">
            {t('deployments.selectProbes')}
            {selected.size > 0 && (
              <span className="text-ink-3 ml-2 font-normal">
                {t('common.selected', { count: selected.size })}
              </span>
            )}
          </legend>

          {probes && probes.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <Input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t('common.search')}
                aria-label={t('common.search')}
                className="min-w-0 flex-1"
              />
              <Button size="sm" variant="ghost" onClick={toggleAllMatching}>
                {allMatchingSelected
                  ? t('deployments.selectNone')
                  : t('deployments.selectAll')}
              </Button>
            </div>
          )}

          <div className="rounded-control border-rule max-h-56 overflow-auto border">
            {/* An empty box says nothing about why it is empty: still loading,
                no probes at all, or none matching the term. */}
            {!probes && <Skeleton className="h-24" />}
            {probes && matching.length === 0 && (
              <p className="text-ink-3 px-3 py-4 text-sm">
                {probes.length === 0
                  ? t('probes.empty')
                  : t('common.noMatches', { query: query.trim() })}
              </p>
            )}
            {matching.map((probe) => (
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
                {(() => {
                  const entry = installations.get(probe.nats_username)
                  if (!entry || entry.current || !chosenSensor) return null
                  return (
                    <Badge tone="warn">
                      {t('deployments.versionChange', {
                        from: entry.version,
                        to: chosenSensor.version,
                      })}
                    </Badge>
                  )
                })()}
                <Mono className="text-ink-3">{probe.host}</Mono>
              </label>
            ))}
          </div>
        </fieldset>

        {(() => {
          const behindHelpers = (probes ?? [])
            .filter((probe) => selected.has(probe.id) && probe.helper_outdated)
            .map((probe) => probe.display_name ?? probe.nats_username)
          if (behindHelpers.length === 0) return null
          // The rollout would only fail in the job, one probe at a time, with
          // "unsupported management request" - the fix is one click away here.
          return (
            <Banner tone="warn">
              {t('deployments.helperOutdatedWarning', {
                probes: behindHelpers.join(', '),
              })}
            </Banner>
          )
        })()}

        {chosenSensor?.needs_interface && (
          // The rollout succeeds without a reservation and the sensor then
          // refuses every run - the probe page is where the reservation
          // happens, after the rollout.
          <Banner tone="neutral">{t('deployments.needsInterfaceHint')}</Banner>
        )}

        {chosenSensor?.iperf_kind && (
          <IperfReadiness
            kind={chosenSensor.iperf_kind}
            sensor={chosenSensor.name}
            selected={selected}
          />
        )}

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
    </Dialog>
  )
}

/**
 * Whether the sensor about to go out will find anything to measure against.
 *
 * A rollout writes the script and, on a probe that holds none yet, seeds it
 * with every registered endpoint - which is why the warning only counts probes
 * that already have the sensor. Without that limit it would fire on every
 * first rollout, and a warning that is always on is not read.
 */
function IperfReadiness({
  kind,
  sensor,
  selected,
}: {
  kind: string
  sensor: string
  selected: Set<string>
}) {
  const { t } = useTranslation()
  const { can } = useAuth()
  // Asking at all is the point of the gate: without the permission the answer
  // would be a 403 on a page that never mentioned endpoints.
  const { data: endpoints } = useIperfEndpoints(can('iperf.read'))
  const { data: detail } = useSensor(sensor)
  const { data: probes } = useProbes()

  if (!can('iperf.read') || !endpoints) return null

  const matching = endpoints.filter((endpoint) => endpoint.kind === kind)
  if (matching.length === 0) {
    return (
      <Banner tone="warn">{t('deployments.iperf.noEndpoints')}</Banner>
    )
  }

  if (!detail || !probes) return null
  const installed = new Set(detail.installations.map((entry) => entry.probe))
  const holding = new Set(
    matching.flatMap((endpoint) => endpoint.holders.map((holder) => holder.probe)),
  )
  const without = (probes ?? [])
    .filter((probe) => selected.has(probe.id))
    .map((probe) => probe.nats_username)
    .filter((username) => installed.has(username) && !holding.has(username))

  if (without.length === 0) return null
  return (
    <Banner tone="warn">
      {t('deployments.iperf.probesWithout', { probes: without.join(', ') })}
    </Banner>
  )
}
