import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import {
  useDeleteSensorProfile,
  useProbes,
  useSensorProfile,
  useSensorProfiles,
  useWriteSensorProfile,
} from '@/api/hooks'
import type { FileField, ParameterSchema, ProfileField } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Input,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { formatBytes, formatDateTime, shortFingerprint } from '@/utils/format'

/** A variant name has to survive as a file name and as a PRTG parameter. */
const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

function label(t: TFunction, field: { name: string; label_key?: string }) {
  return field.label_key ? t(field.label_key, field.name) : field.name
}

function hint(t: TFunction, field: { description?: string; description_key?: string }) {
  if (field.description_key) return t(field.description_key, field.description ?? '')
  return field.description || undefined
}

/**
 * The variants of one sensor: one SSID, one measurement endpoint, one site.
 *
 * A variant is the profile the probe has always known, reachable without a
 * terminal. In PRTG it becomes one more sensor object carrying a single
 * parameter - which is what makes several of them practical in the first place.
 */
export function SensorVariants({
  sensorName,
  schema,
}: {
  sensorName: string
  schema: ParameterSchema | null
}) {
  const { t } = useTranslation()
  const supported = Boolean(schema?.supports_profiles)
  const { data, isLoading, error, refetch } = useSensorProfiles(sensorName, supported)
  const remove = useDeleteSensorProfile(sensorName)
  const navigate = useNavigate()
  const [editing, setEditing] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  if (!supported || !schema) return null
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  return (
    <Card
      title={t('sensors.variants.title')}
      action={
        <PermissionGate permission="sensor.configure">
          <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
            {t('sensors.variants.add')}
          </Button>
        </PermissionGate>
      }
    >
      <p className="text-ink-3 mb-3 text-sm">{t('sensors.variants.intro')}</p>

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : !data || data.length === 0 ? (
        <EmptyState title={t('sensors.variants.empty')} />
      ) : (
        <ul className="divide-rule divide-y">
          {data.map((variant) => (
            <li key={variant.name} className="flex flex-wrap gap-3 py-3 first:pt-0">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-ink font-medium">{variant.name}</span>
                  {variant.probes.length === 0 && (
                    <Badge tone="neutral">{t('sensors.variants.notDeployed')}</Badge>
                  )}
                </div>
                <p className="text-ink-3 mt-0.5 text-xs">
                  {variant.updated_at ? formatDateTime(variant.updated_at) : '—'}
                  {variant.probes.length > 0 && ` · ${variant.probes.join(', ')}`}
                </p>
                {variant.files.length > 0 && (
                  <ul className="mt-1 space-y-0.5">
                    {variant.files.map((file) => (
                      <li key={file.key} className="text-ink-3 text-xs">
                        <Mono>{file.filename}</Mono> · {formatBytes(file.size_bytes)} ·{' '}
                        {shortFingerprint(file.sha256)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="flex items-start gap-2">
                {/* What to paste into PRTG - the point where the variant
                    actually arrives at the sensor. */}
                <code className="bg-surface-2 rounded-inset text-ink px-2 py-1 font-mono text-xs">
                  {variant.parameter_line}
                </code>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void navigator.clipboard.writeText(variant.parameter_line)
                  }
                >
                  {t('common.copy')}
                </Button>
                <PermissionGate permission="sensor.configure">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(variant.name)}>
                    {t('common.edit')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (!window.confirm(t('sensors.variants.confirmDelete', {
                        name: variant.name,
                      }))) {
                        return
                      }
                      remove.mutate(variant.name, {
                        onSuccess: (job) => {
                          if (job.job_id) navigate(`/jobs/${job.job_id}`)
                        },
                      })
                    }}
                  >
                    {t('common.remove')}
                  </Button>
                </PermissionGate>
              </div>
            </li>
          ))}
        </ul>
      )}

      {remove.error && <ErrorDetails error={remove.error} />}

      {(creating || editing) && (
        <VariantDialog
          sensorName={sensorName}
          schema={schema}
          existing={editing}
          onClose={() => {
            setCreating(false)
            setEditing(null)
          }}
        />
      )}
    </Card>
  )
}

function VariantDialog({
  sensorName,
  schema,
  existing,
  onClose,
}: {
  sensorName: string
  schema: ParameterSchema
  existing: string | null
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: probes } = useProbes()
  const { data: stored, isLoading } = useSensorProfile(
    sensorName,
    existing ?? undefined,
  )
  const write = useWriteSensorProfile(sensorName)

  const [name, setName] = useState(existing ?? '')
  const [values, setValues] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<Set<string> | null>(null)
  const [files, setFiles] = useState<Record<string, { name: string; base64: string }>>({})
  const [touched, setTouched] = useState(false)

  // The stored settings fill the form once they arrive; credentials never do,
  // because the API does not return them.
  if (existing && stored && !touched) {
    if (Object.keys(values).length === 0 && Object.keys(stored.values).length > 0) {
      setValues(stored.values)
    }
    if (selected === null) {
      const ids = (probes ?? [])
        .filter((probe) => stored.probes.includes(probe.nats_username))
        .map((probe) => probe.id)
      setSelected(new Set(ids))
    }
  }

  const chosen = selected ?? new Set<string>()
  const nameIsValid = NAME_PATTERN.test(name)
  const missing = schema.settings
    .filter((field) => field.required && !values[field.name]?.trim())
    .map((field) => field.name)

  function update(key: string, value: string) {
    setTouched(true)
    setValues((current) => ({ ...current, [key]: value }))
  }

  /**
   * Read the upload as base64 - the encoding it keeps all the way to the
   * probe. FileReader rather than arrayBuffer(): it hands the base64 over
   * directly, so a certificate never passes through a byte-by-byte loop here.
   */
  function readFile(field: FileField, file: File) {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result)
      setFiles((current) => ({
        ...current,
        [field.name]: { name: file.name, base64: result.slice(result.indexOf(',') + 1) },
      }))
    }
    reader.readAsDataURL(file)
  }

  function submit() {
    write.mutate(
      {
        profile: name,
        values,
        probeIds: [...chosen],
        files: Object.entries(files).map(([key, file]) => ({
          key,
          contentBase64: file.base64,
        })),
      },
      {
        onSuccess: (job) => {
          onClose()
          if (job.job_id) navigate(`/jobs/${job.job_id}`)
        },
      },
    )
  }

  return (
    <Dialog
      title={
        existing
          ? t('sensors.variants.editTitle', { name: existing })
          : t('sensors.variants.addTitle')
      }
      onClose={onClose}
      size="lg"
    >
      {existing && isLoading ? (
        <Skeleton className="h-48" />
      ) : (
        <div className="space-y-4">
          <Field
            label={t('sensors.variants.name')}
            hint={t('sensors.variants.nameHint')}
            error={
              name && !nameIsValid ? t('sensors.variants.nameInvalid') : undefined
            }
          >
            <Input
              value={name}
              disabled={Boolean(existing)}
              placeholder="standort-nord"
              onChange={(event) => setName(event.target.value)}
            />
          </Field>

          {schema.settings.length > 0 && (
            <FieldGroup title={t('sensors.variants.settings')}>
              {schema.settings.map((field) => (
                <ProfileInput
                  key={field.name}
                  field={field}
                  value={values[field.name] ?? ''}
                  onChange={(value) => update(field.name, value)}
                />
              ))}
            </FieldGroup>
          )}

          {schema.credentials.length > 0 && (
            <FieldGroup title={t('sensors.variants.credentials')}>
              {schema.credentials.map((field) => (
                <Field
                  key={field.name}
                  label={label(t, field)}
                  hint={
                    stored?.secrets_set.includes(field.name)
                      ? t('sensors.variants.secretStored')
                      : hint(t, field)
                  }
                >
                  <Input
                    type="password"
                    autoComplete="new-password"
                    value={values[field.name] ?? ''}
                    placeholder={
                      stored?.secrets_set.includes(field.name)
                        ? '••••••••'
                        : undefined
                    }
                    onChange={(event) => update(field.name, event.target.value)}
                  />
                </Field>
              ))}
            </FieldGroup>
          )}

          {schema.files.length > 0 && (
            <FieldGroup title={t('sensors.variants.files')}>
              {schema.files.map((field) => {
                const uploaded = stored?.files.find(
                  (entry) => entry.key === field.name,
                )
                return (
                  <Field
                    key={field.name}
                    label={label(t, field)}
                    hint={
                      files[field.name]
                        ? files[field.name].name
                        : uploaded
                          ? t('sensors.variants.fileStored', {
                              fingerprint: shortFingerprint(uploaded.sha256),
                            })
                          : hint(t, field)
                    }
                  >
                    <input
                      type="file"
                      className="text-ink-2 text-sm"
                      onChange={(event) => {
                        const file = event.target.files?.[0]
                        if (file) readFile(field, file)
                      }}
                    />
                  </Field>
                )
              })}
            </FieldGroup>
          )}

          <FieldGroup title={t('sensors.variants.probes')}>
            <p className="text-ink-3 text-xs">{t('sensors.variants.probesHint')}</p>
            <div className="max-h-40 space-y-1 overflow-auto">
              {(probes ?? []).map((probe) => (
                <label key={probe.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={chosen.has(probe.id)}
                    onChange={(event) => {
                      const next = new Set(chosen)
                      if (event.target.checked) next.add(probe.id)
                      else next.delete(probe.id)
                      setSelected(next)
                    }}
                  />
                  <Mono>{probe.nats_username}</Mono>
                </label>
              ))}
            </div>
          </FieldGroup>

          {write.error && <ErrorDetails error={write.error} />}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              disabled={!nameIsValid || missing.length > 0 || write.isPending}
              onClick={submit}
            >
              {t('common.save')}
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            {missing.length > 0 && (
              <span className="text-ink-3 text-xs">
                {t('sensors.variants.missing', { fields: missing.join(', ') })}
              </span>
            )}
          </div>
        </div>
      )}
    </Dialog>
  )
}

function FieldGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-ink-2 text-xs font-semibold tracking-wide uppercase">
        {title}
      </h3>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </section>
  )
}

function ProfileInput({
  field,
  value,
  onChange,
}: {
  field: ProfileField
  value: string
  onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  return (
    <Field
      label={label(t, field) + (field.required ? ' *' : '')}
      hint={hint(t, field)}
    >
      {field.type === 'choice' ? (
        <select
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="rounded-control border-rule-2 bg-surface text-ink border px-2.5 py-1.5 text-sm"
        >
          <option value="">—</option>
          {field.choices?.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      ) : (
        <Input
          type={field.type === 'integer' ? 'number' : 'text'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  )
}
