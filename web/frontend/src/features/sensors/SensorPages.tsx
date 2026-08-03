import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useRenderParameters, useSensor, useSensors } from '@/api/hooks'
import type { ParameterField, SensorSummary } from '@/api/types'
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

export function SensorListPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
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
        onRowClick={(row) => navigate(`/sensors/${row.name}`)}
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

      <ParameterBuilder sensorName={name} fields={data.parameter_schema?.fields ?? null} />

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

/**
 * Builds the parameter line for PRTG from a form.
 *
 * The sensor scripts parse their options with argparse and an operator types
 * them into a PRTG text field by hand. A form that produces the exact string
 * removes the two ways that goes wrong: a misremembered flag name and a typo.
 */
function ParameterBuilder({
  sensorName,
  fields,
}: {
  sensorName: string
  fields: ParameterField[] | null
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, unknown>>({})
  const render = useRenderParameters(sensorName)

  if (!fields || fields.length === 0) {
    return (
      <Card title={t('sensors.parameters')}>
        <p className="text-ink-3 text-sm">{t('sensors.noParameterSchema')}</p>
      </Card>
    )
  }

  return (
    <Card title={t('sensors.parameters')}>
      <p className="text-ink-3 mb-3 text-sm">{t('sensors.parametersIntro')}</p>

      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <Field
            key={field.name}
            label={field.label_key ? t(field.label_key, field.name) : field.name}
            hint={field.description_key ? t(field.description_key) : undefined}
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
