import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useDeleteSensorProfile,
  useProbes,
  useSensorProfile,
  useSensorProfiles,
  useWriteSensorProfile,
} from '@/api/hooks'
import type { FileField, ParameterSchema, ProfileField, SensorProfile } from '@/api/types'
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
  Select,
  Skeleton,
} from '@/components/ui/primitives'
import { CopyButton, InlineCode } from '@/components/ui/CopyBlock'
import { formatBytes, formatDateTime, shortFingerprint } from '@/utils/format'

import { fieldDescription, fieldLabel } from './parameterFields'

/** fieldDescription returns '' where this dialog wants undefined. */
function hintOf(
  t: Parameters<typeof fieldDescription>[0],
  field: Parameters<typeof fieldDescription>[1],
) {
  return fieldDescription(t, field) || undefined
}

/** A variant name has to survive as a file name and as a PRTG parameter. */
const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

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
  needsInterface = false,
}: {
  sensorName: string
  schema: ParameterSchema | null
  /** The sensor also takes --interface, which is per probe - the copied
   *  profile line alone is not the whole PRTG line then. */
  needsInterface?: boolean
}) {
  const { t } = useTranslation()
  const supported = Boolean(schema?.supports_profiles)
  const { data, isLoading, error, refetch } = useSensorProfiles(sensorName, supported)
  const remove = useDeleteSensorProfile(sensorName)
  const navigate = useNavigate()
  const [editing, setEditing] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [removing, setRemoving] = useState<SensorProfile | null>(null)

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
                    // The state and its cure are the same click: deploying a
                    // variant means ticking probes in the edit dialog.
                    <button type="button" onClick={() => setEditing(variant.name)}>
                      <Badge tone="warn">
                        {t('sensors.variants.notDeployed')}
                      </Badge>
                    </button>
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
                <span>
                  <InlineCode>
                    {variant.parameter_line}
                  </InlineCode>
                  {needsInterface && (
                    <span className="text-ink-3 block text-right text-xs">
                      {t('sensors.variants.interfaceSuffix')}
                    </span>
                  )}
                </span>
                <CopyButton value={variant.parameter_line} />
                <PermissionGate permission="sensor.configure">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(variant.name)}>
                    {t('common.edit')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setRemoving(variant)}
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

      {removing && (
        <Dialog
          title={t('sensors.variants.confirmDeleteTitle', { name: removing.name })}
          onClose={() => setRemoving(null)}
        >
          <div className="space-y-4">
            {/* Named, not counted: the deletion reaches into every probe that
                holds the variant, and this is the last moment to see which. */}
            <p className="text-ink-2 text-sm">
              {removing.probes.length > 0
                ? t('sensors.variants.confirmDeleteBody', {
                    probes: removing.probes.join(', '),
                  })
                : t('sensors.variants.confirmDeleteUnused')}
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setRemoving(null)}>
                {t('common.cancel')}
              </Button>
              <Button
                variant="danger"
                disabled={remove.isPending}
                onClick={() =>
                  remove.mutate(removing.name, {
                    onSuccess: (job) => {
                      setRemoving(null)
                      if (job.job_id) navigate(`/jobs/${job.job_id}`)
                    },
                  })
                }
              >
                {t('common.remove')}
              </Button>
            </div>
          </div>
        </Dialog>
      )}

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

  /**
   * The schema's own conditionality. A choice setting whose options are the
   * group names used by other fields (wlan-auth's AUTH: psk/peap/eap-tls) is
   * the switch that decides which of them apply - showing a PSK variant the
   * three certificate fields only invites filling in the wrong half.
   */
  const groups = new Set(
    [...schema.settings, ...schema.credentials, ...schema.files]
      .map((field) => field.group)
      .filter((group): group is string => Boolean(group)),
  )
  const groupSelector =
    groups.size > 0
      ? schema.settings.find(
          (field) =>
            field.type === 'choice' &&
            [...groups].every((group) => field.choices?.includes(group)),
        )
      : undefined
  const activeGroup = groupSelector
    ? values[groupSelector.name]?.trim() || String(groupSelector.default ?? '')
    : null

  function applies(field: { group?: string }): boolean {
    if (!field.group || !groupSelector) return true
    return field.group === activeGroup
  }

  const visibleSettings = schema.settings.filter(applies)
  const visibleCredentials = schema.credentials.filter(applies)
  const visibleFiles = schema.files.filter(applies)

  // Required means "required while its group applies" - and a credential the
  // server already holds, or a file already uploaded, counts as filled in.
  const missing = [
    ...visibleSettings.filter(
      (field) => field.required && !values[field.name]?.trim(),
    ),
    ...visibleCredentials.filter(
      (field) =>
        field.required &&
        !values[field.name]?.trim() &&
        !stored?.secrets_set.includes(field.name),
    ),
    ...visibleFiles.filter(
      (field) =>
        field.required &&
        !files[field.name] &&
        !stored?.files.some((entry) => entry.key === field.name),
    ),
  ].map((field) => field.name)

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

          {visibleSettings.length > 0 && (
            <FieldGroup title={t('sensors.variants.settings')}>
              {visibleSettings.map((field) => (
                <ProfileInput
                  key={field.name}
                  field={field}
                  value={values[field.name] ?? ''}
                  onChange={(value) => update(field.name, value)}
                />
              ))}
            </FieldGroup>
          )}

          {visibleCredentials.length > 0 && (
            <FieldGroup title={t('sensors.variants.credentials')}>
              {visibleCredentials.map((field) => (
                <Field
                  key={field.name}
                  label={fieldLabel(t, field)}
                  hint={
                    stored?.secrets_set.includes(field.name)
                      ? t('sensors.variants.secretStored')
                      : hintOf(t, field)
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

          {visibleFiles.length > 0 && (
            <FieldGroup title={t('sensors.variants.files')}>
              {visibleFiles.map((field) => {
                const uploaded = stored?.files.find(
                  (entry) => entry.key === field.name,
                )
                return (
                  <Field
                    key={field.name}
                    label={fieldLabel(t, field)}
                    hint={
                      files[field.name]
                        ? files[field.name].name
                        : uploaded
                          ? t('sensors.variants.fileStored', {
                              fingerprint: shortFingerprint(uploaded.sha256),
                            })
                          : hintOf(t, field)
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
      label={fieldLabel(t, field) + (field.required ? ' *' : '')}
      hint={hintOf(t, field)}
    >
      {field.type === 'choice' ? (
        <Select
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
        </Select>
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
