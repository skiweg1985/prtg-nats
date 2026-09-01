import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  useCreateWatchDevice,
  useDeleteWatchDevice,
  useProbes,
  useUpdateWatchDevice,
} from '@/api/hooks'
import type { WatchCheckMethod, WatchDevice } from '@/api/types'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Button,
  Dialog,
  Field,
  Input,
} from '@/components/ui/primitives'

/**
 * Adding a printer, or editing one.
 *
 * Labels are typed as `key: value` lines rather than picked from a list.
 * There is no label registry to keep - a label exists because a device
 * carries it - and a text area is the one editor that needs none.
 */
export function DeviceDialog({
  device,
  onClose,
}: {
  device: WatchDevice | null
  onClose: () => void
}) {
  const { t } = useTranslation()
  const { data: probes } = useProbes()
  const create = useCreateWatchDevice()
  const update = useUpdateWatchDevice()
  const remove = useDeleteWatchDevice()

  const [name, setName] = useState(device?.display_name ?? '')
  const [address, setAddress] = useState(device?.address ?? '')
  const [probeId, setProbeId] = useState(device?.probe_id ?? '')
  const [method, setMethod] = useState<WatchCheckMethod>(device?.method ?? 'icmp')
  const [port, setPort] = useState(device?.port ? String(device.port) : '')
  const [threshold, setThreshold] = useState(String(device?.failure_threshold ?? 3))
  const [enabled, setEnabled] = useState(device?.enabled ?? true)
  const [labelText, setLabelText] = useState(
    Object.entries(device?.labels ?? {})
      .map(([key, value]) => `${key}: ${value}`)
      .join('\n'),
  )
  const [confirmDelete, setConfirmDelete] = useState(false)

  const pending = create.isPending || update.isPending || remove.isPending
  const error = create.error ?? update.error ?? remove.error
  const valid =
    name.trim() !== '' &&
    address.trim() !== '' &&
    probeId !== '' &&
    (method !== 'tcp' || Number(port) > 0)

  function submit() {
    const body = {
      display_name: name.trim(),
      address: address.trim(),
      probe_id: probeId,
      method,
      port: method === 'tcp' ? Number(port) : null,
      labels: parseLabels(labelText),
      failure_threshold: Number(threshold) || 3,
      enabled,
      notes: device?.notes ?? null,
    }
    const done = { onSuccess: () => onClose() }
    if (device) update.mutate({ id: device.id, ...body }, done)
    else create.mutate(body, done)
  }

  return (
    <Dialog
      title={device ? t('watch.dialog.editTitle') : t('watch.dialog.addTitle')}
      onClose={onClose}
    >
      <div className="space-y-3">
        <Field label={t('watch.fields.name')}>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t('watch.fields.namePlaceholder')}
            autoFocus
          />
        </Field>

        <Field label={t('watch.fields.address')} hint={t('watch.fields.addressHint')}>
          <Input
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            placeholder="10.10.0.31"
          />
        </Field>

        <Field label={t('watch.fields.probe')} hint={t('watch.fields.probeHint')}>
          <select
            className="rounded-control border-rule-2 bg-surface text-ink w-full border px-2.5 py-1.5 text-sm"
            value={probeId}
            onChange={(event) => setProbeId(event.target.value)}
          >
            <option value="">{t('watch.fields.probePlaceholder')}</option>
            {probes?.map((probe) => (
              <option key={probe.id} value={probe.id}>
                {probe.display_name || probe.nats_username}
              </option>
            ))}
          </select>
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label={t('watch.fields.method')} hint={t('watch.fields.methodHint')}>
            <select
              className="rounded-control border-rule-2 bg-surface text-ink w-full border px-2.5 py-1.5 text-sm"
              value={method}
              onChange={(event) => setMethod(event.target.value as WatchCheckMethod)}
            >
              <option value="icmp">{t('watch.method.icmp')}</option>
              <option value="tcp">{t('watch.method.tcp')}</option>
            </select>
          </Field>
          {method === 'tcp' && (
            <Field label={t('watch.fields.port')}>
              <Input
                value={port}
                onChange={(event) => setPort(event.target.value)}
                inputMode="numeric"
                placeholder="9100"
              />
            </Field>
          )}
        </div>

        <Field
          label={t('watch.fields.threshold')}
          hint={t('watch.fields.thresholdHint')}
        >
          <Input
            value={threshold}
            onChange={(event) => setThreshold(event.target.value)}
            inputMode="numeric"
          />
        </Field>

        <Field label={t('watch.fields.labels')} hint={t('watch.fields.labelsHint')}>
          <textarea
            className="rounded-control border-rule-2 bg-surface text-ink w-full border px-2.5 py-1.5 font-mono text-sm"
            rows={3}
            value={labelText}
            onChange={(event) => setLabelText(event.target.value)}
            placeholder={'team: support\nsite: hamburg'}
          />
        </Field>

        <label className="text-ink-2 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          {t('watch.fields.enabled')}
        </label>

        {error && <ErrorDetails error={error} />}

        <div className="flex flex-wrap justify-between gap-2 pt-1">
          {device ? (
            confirmDelete ? (
              <div className="flex items-center gap-2">
                {/* The history goes with it, which is not obvious from a
                    button labelled "delete". */}
                <span className="text-ink-2 text-sm">
                  {t('watch.dialog.deleteConfirm')}
                </span>
                <Button
                  variant="danger"
                  disabled={pending}
                  onClick={() => remove.mutate(device.id, { onSuccess: onClose })}
                >
                  {t('common.delete')}
                </Button>
              </div>
            ) : (
              <Button onClick={() => setConfirmDelete(true)}>
                {t('common.delete')}
              </Button>
            )
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button onClick={onClose}>{t('common.cancel')}</Button>
            <Button variant="primary" disabled={!valid || pending} onClick={submit}>
              {t('common.save')}
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  )
}

/**
 * ``team: support`` per line, into an object.
 *
 * A line without a colon is dropped rather than rejected: somebody typing a
 * third label should not lose the two above it to a validation error.
 */
export function parseLabels(text: string): Record<string, string> {
  const labels: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const index = line.indexOf(':')
    if (index <= 0) continue
    const key = line.slice(0, index).trim()
    const value = line.slice(index + 1).trim()
    if (key && value) labels[key] = value
  }
  return labels
}
