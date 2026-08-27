import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { useRenderParameters, useSensor, useSensors } from '@/api/hooks'
import type { ParameterSchema, SensorSummary } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Button,
  Card,
  DetailRow,
  EmptyState,
  Field,
  Input,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { formatBytes, shortFingerprint } from '@/utils/format'

import { DeployDialog } from '../deployments/DeployDialog'
import { SensorVariants } from './SensorVariants'

export function SensorListPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useSensors()

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const columns: Column<SensorSummary>[] = [
    {
      key: 'name',
      header: t('sensors.columns.name'),
      sortValue: (row) => row.name,
      searchValue: (row) => `${row.name} ${row.description}`,
      cell: (row) => (
        <div className="min-w-0">
          <p className="text-ink font-medium">{row.name}</p>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {row.needs_interface && <Badge tone="neutral">iface</Badge>}
            {row.requires_privileged_helper && <Badge tone="neutral">helper</Badge>}
            {row.iperf_kind && <Badge tone="neutral">{row.iperf_kind}</Badge>}
          </div>
        </div>
      ),
    },
    {
      key: 'version',
      header: t('sensors.columns.version'),
      sortValue: (row) => row.version,
      cell: (row) => <Mono>v{row.version}</Mono>,
    },
    {
      key: 'description',
      header: t('sensors.columns.description'),
      searchValue: (row) => row.description,
      cell: (row) => <span className="text-ink-2 text-sm">{row.description}</span>,
    },
    {
      key: 'installed',
      header: t('sensors.columns.installed'),
      align: 'right',
      sortValue: (row) => row.installed_on,
      cell: (row) => <span className="text-sm">{row.installed_on}</span>,
    },
    {
      key: 'outdated',
      header: t('sensors.columns.outdated'),
      align: 'right',
      sortValue: (row) => row.outdated_on,
      cell: (row) =>
        row.outdated_on > 0 ? (
          <Badge tone="warn">{row.outdated_on}</Badge>
        ) : (
          <span className="text-ink-3 text-sm">0</span>
        ),
    },
  ]

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg">{t('sensors.title')}</h1>
        <p className="text-ink-3 text-sm">{t('sensors.subtitle')}</p>
      </header>

      <DataTable
        rows={data}
        columns={columns}
        rowKey={(row) => row.name}
        isLoading={isLoading}
        emptyTitle={t('sensors.empty')}
        rowHref={(row) => `/sensors/${row.name}`}
      />
    </div>
  )
}

export function SensorDetailPage() {
  const { t } = useTranslation()
  const { name } = useParams<{ name: string }>()
  const { data, isLoading, error, refetch } = useSensor(name)
  const [deploying, setDeploying] = useState(false)

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!data || !name) return null

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <Link to="/sensors" className="text-ink-3 text-xs">
            ← {t('sensors.title')}
          </Link>
          <div className="mt-1 flex items-center gap-2">
            <h1 className="text-lg">{data.name}</h1>
            <Badge tone="accent">v{data.version}</Badge>
          </div>
          <p className="text-ink-2 text-sm">{data.description}</p>
        </div>
        <PermissionGate permission="deployment.create">
          <Button variant="primary" size="sm" onClick={() => setDeploying(true)}>
            {t('sensors.deployTo')}
          </Button>
        </PermissionGate>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('sensors.files')}>
          <dl>
            {data.files.map((file) => (
              <DetailRow key={file.slot} label={file.slot}>
                <Mono truncate>{file.relative_path}</Mono>
                <p className="text-ink-3 mt-0.5 font-mono text-[0.6875rem]">
                  {formatBytes(file.size_bytes)} · {shortFingerprint(file.sha256)}
                </p>
              </DetailRow>
            ))}
            {data.needs_interface && (
              <DetailRow label="">{t('sensors.needsInterface')}</DetailRow>
            )}
            {data.requires_privileged_helper && (
              <DetailRow label="">{t('sensors.privilegedHelper')}</DetailRow>
            )}
            {data.iperf_kind && (
              <DetailRow label="">{t('sensors.iperfEndpoint')}</DetailRow>
            )}
          </dl>
        </Card>

        <Card title={t('sensors.installedOn')}>
          {data.probes.length === 0 ? (
            <EmptyState title={t('common.none')} />
          ) : (
            <ul className="space-y-1">
              {data.probes.map((probe) => (
                <li key={probe}>
                  <Mono>{probe}</Mono>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <SensorVariants sensorName={name} schema={data.parameter_schema} />
      <ParameterReference schema={data.parameter_schema} />
      <ParameterBuilder sensorName={name} schema={data.parameter_schema} />

      {deploying && (
        <DeployDialog
          sensorName={name}
          onClose={() => setDeploying(false)}
          onDone={() => setDeploying(false)}
        />
      )}
    </div>
  )
}

/** The label of a field: its translation if there is one, else its own name. */
function fieldLabel(t: TFunction, field: { name: string; label_key?: string }) {
  return field.label_key ? t(field.label_key, field.name) : field.name
}

/**
 * The description of a field.
 *
 * The English plain text ships with the sensor and is kept in step with the
 * script's own argparse help by tests/sensor-checks.py. A translation key is
 * optional on top - a reference that shows nothing until every sensor is
 * translated would be a reference nobody can use yet.
 */
function fieldDescription(
  t: TFunction,
  field: { description?: string; description_key?: string },
) {
  if (field.description_key) return t(field.description_key, field.description ?? '')
  return field.description ?? ''
}

/**
 * What each parameter of a sensor means, to look up rather than to fill in.
 *
 * Until now this lived in the sensor's README: whoever wanted to know whether
 * --ssid is required, or what --stage accepts, read prose or the argparse of
 * the script. Both are in the repository, neither is in the interface where
 * the sensor is being set up.
 */
export function ParameterReference({ schema }: { schema: ParameterSchema | null }) {
  const { t } = useTranslation()

  if (!schema || schema.parameters.length === 0) {
    return (
      <Card title={t('sensors.reference.title')}>
        <p className="text-ink-3 text-sm">{t('sensors.noParameterSchema')}</p>
      </Card>
    )
  }

  // Which parameters a variant can supply instead. Collected from the profile
  // side so the row can say "you may leave this out once a variant carries it".
  const suppliedBy = new Map<string, string>()
  for (const entry of [...schema.settings, ...schema.credentials, ...schema.files]) {
    if (entry.maps_to) suppliedBy.set(entry.maps_to, entry.name)
  }

  return (
    <Card title={t('sensors.reference.title')}>
      <p className="text-ink-3 mb-3 text-sm">{t('sensors.reference.intro')}</p>

      {schema.default_parameter_line && (
        <div className="bg-surface-2 rounded-inset mb-4 p-3">
          <p className="text-ink-3 mb-1.5 text-xs">
            {t('sensors.reference.recommendedLine')}
          </p>
          <div className="flex items-start gap-2">
            <code className="text-ink flex-1 font-mono text-xs break-all">
              {schema.default_parameter_line}
            </code>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                void navigator.clipboard.writeText(schema.default_parameter_line)
              }
            >
              {t('common.copy')}
            </Button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-rule text-ink-3 border-b text-left">
              <th className="py-1.5 pr-3 font-medium">
                {t('sensors.reference.columns.name')}
              </th>
              <th className="py-1.5 pr-3 font-medium">
                {t('sensors.reference.columns.type')}
              </th>
              <th className="py-1.5 pr-3 font-medium">
                {t('sensors.reference.columns.default')}
              </th>
              <th className="py-1.5 font-medium">
                {t('sensors.reference.columns.description')}
              </th>
            </tr>
          </thead>
          <tbody>
            {schema.parameters.map((field) => (
              <tr key={field.name} className="border-rule border-b last:border-0">
                <td className="py-2 pr-3 align-top">
                  <Mono>{fieldLabel(t, field)}</Mono>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {field.required && (
                      <Badge tone="warn">{t('sensors.reference.required')}</Badge>
                    )}
                    {field.repeatable && (
                      <Badge tone="neutral">{t('sensors.reference.repeatable')}</Badge>
                    )}
                    {suppliedBy.has(field.name) && (
                      <Badge tone="neutral">
                        {t('sensors.reference.fromVariant', {
                          key: suppliedBy.get(field.name),
                        })}
                      </Badge>
                    )}
                  </div>
                </td>
                <td className="text-ink-2 py-2 pr-3 align-top">
                  {field.choices && field.choices.length > 0 ? (
                    <Mono>{field.choices.join(' | ')}</Mono>
                  ) : (
                    t(`sensors.reference.types.${field.type}`)
                  )}
                </td>
                <td className="text-ink-2 py-2 pr-3 align-top">
                  {field.source === 'prtg' ? (
                    <Mono>{field.prtg_placeholder}</Mono>
                  ) : field.default !== undefined && field.default !== null ? (
                    <Mono>{String(field.default)}</Mono>
                  ) : (
                    <span className="text-ink-3">—</span>
                  )}
                </td>
                <td className="text-ink-2 py-2 align-top">
                  {fieldDescription(t, field)}
                  {field.source === 'prtg' && (
                    <p className="text-ink-3 mt-1 text-xs">
                      {t('sensors.reference.fromPrtg')}
                    </p>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/**
 * Builds the parameter line for PRTG from a form.
 *
 * The sensor scripts parse their options with argparse and an operator types
 * them into a PRTG text field by hand. A form that produces the exact string
 * removes the two ways that goes wrong: a misremembered flag name and a typo.
 *
 * Parameters PRTG substitutes itself are left out: asking for a password here
 * that the device settings already hold would invite someone to type it into
 * the sensor configuration, which is the one place it is not supposed to be.
 */
function ParameterBuilder({
  sensorName,
  schema,
}: {
  sensorName: string
  schema: ParameterSchema | null
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, unknown>>({})
  const render = useRenderParameters(sensorName)

  const fields = (schema?.parameters ?? []).filter((field) => field.source !== 'prtg')
  if (fields.length === 0) return null

  const missing = fields.filter(
    (field) => field.required && !values[field.name] && field.type !== 'boolean',
  )

  return (
    <Card title={t('sensors.parameters')}>
      <p className="text-ink-3 mb-3 text-sm">{t('sensors.parametersIntro')}</p>

      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <Field
            key={field.name}
            label={fieldLabel(t, field) + (field.required ? ' *' : '')}
            hint={fieldDescription(t, field) || undefined}
          >
            {field.type === 'boolean' ? (
              <input
                type="checkbox"
                checked={Boolean(values[field.name])}
                onChange={(event) =>
                  setValues({ ...values, [field.name]: event.target.checked })
                }
              />
            ) : field.type === 'choice' ? (
              <select
                value={String(values[field.name] ?? '')}
                onChange={(event) =>
                  setValues({ ...values, [field.name]: event.target.value })
                }
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
                value={String(values[field.name] ?? '')}
                min={field.minimum}
                max={field.maximum}
                placeholder={field.placeholder ?? String(field.default ?? '')}
                onChange={(event) =>
                  setValues({ ...values, [field.name]: event.target.value })
                }
              />
            )}
          </Field>
        ))}
      </div>

      <div className="mt-4 flex items-center gap-2">
        <Button variant="primary" size="sm" onClick={() => render.mutate(values)}>
          {t('sensors.parametersResult')}
        </Button>
        {missing.length > 0 && (
          <span className="text-ink-3 text-xs">
            {t('sensors.parametersMissing', {
              fields: missing.map((field) => field.name).join(', '),
            })}
          </span>
        )}
      </div>

      {render.data && (
        <div className="bg-surface-2 rounded-inset mt-3 flex items-start gap-2 p-3">
          <code className="text-ink flex-1 font-mono text-xs break-all">
            {render.data.parameters || '—'}
          </code>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void navigator.clipboard.writeText(render.data.parameters)}
          >
            {t('common.copy')}
          </Button>
        </div>
      )}
      {render.error && <ErrorDetails error={render.error} />}
    </Card>
  )
}
