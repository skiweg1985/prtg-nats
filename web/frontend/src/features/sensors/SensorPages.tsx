import { lazy, Suspense, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { fieldDescription, fieldLabel } from './parameterFields'

import { useRenderParameters, useSensor, useSensors } from '@/api/hooks'
import type { ParameterSchema, SensorDetail, SensorSummary } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { ProbeLink } from '@/components/ui/ProbeLink'
import {
  Badge,
  Button,
  Card,
  DetailRow,
  EmptyState,
  Field,
  Input,
  Mono,
  Select,
  Skeleton,
} from '@/components/ui/primitives'
import { CopyButton, InlineCode } from '@/components/ui/CopyBlock'
import { formatBytes, shortFingerprint } from '@/utils/format'

import { DeployDialog } from '../deployments/DeployDialog'
import { SensorVariants } from './SensorVariants'

const Markdown = lazy(async () => ({
  default: (await import('@/components/ui/Markdown')).Markdown,
}))

export function SensorListPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useSensors()
  const [updating, setUpdating] = useState<string | null>(null)

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
            {/* The kind is a token the catalog matches endpoints on. What it
                means to somebody reading the list is that this sensor needs
                one. */}
            {row.iperf_kind && (
              <Badge tone="neutral">{t('sensors.iperfBadge')}</Badge>
            )}
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
      // The number used to be a dead end: it said twelve and left finding
      // the twelve to the reader. Clicking it opens the rollout with exactly
      // those probes already chosen.
      cell: (row) =>
        row.outdated_on > 0 ? (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              setUpdating(row.name)
            }}
            title={t('sensors.updateOutdated')}
          >
            <Badge tone="warn">{row.outdated_on}</Badge>
          </button>
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

      {updating && (
        <DeployDialog
          sensorName={updating}
          preselect="outdated"
          onClose={() => setUpdating(null)}
        />
      )}
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

      {(data.needs_interface || data.requires_privileged_helper || data.iperf_kind) && (
        <Card title={t('sensors.requirements')}>
          <ul className="text-ink-2 space-y-1 text-sm">
            {data.needs_interface && <li>{t('sensors.needsInterface')}</li>}
            {data.requires_privileged_helper && (
              <li>{t('sensors.privilegedHelper')}</li>
            )}
            {data.iperf_kind && (
              <li>
                <Link
                  to="/infrastructure/iperf"
                  className="text-accent hover:underline"
                >
                  {t('sensors.iperfEndpoint')}
                </Link>
              </li>
            )}
          </ul>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <PrtgCard sensor={data} />

        <Card title={t('sensors.installedOn')}>
          {data.installations.length === 0 ? (
            <EmptyState title={t('common.none')} />
          ) : (
            <ul className="space-y-1">
              {data.installations.map((entry) => (
                <li key={entry.probe} className="flex items-center gap-2">
                  <ProbeLink username={entry.probe} />
                  <Mono className="text-ink-3 text-xs">v{entry.version}</Mono>
                  {!entry.current && (
                    <Badge tone="warn">{t('sensors.outdatedBadge')}</Badge>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

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
        </dl>
      </Card>

      <SensorVariants
        sensorName={name}
        schema={data.parameter_schema}
        needsInterface={data.needs_interface}
      />
      {/* The builder first: it is what somebody setting up a sensor came for,
          and the reference below lists the same parameters a second time. */}
      <ParameterCard sensorName={name} schema={data.parameter_schema} />

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
 * What to type on the PRTG side.
 *
 * The files card names repository paths, but PRTG's Script v2 dropdown shows
 * the installed basename - the one translation every setup has to make and
 * the interface never made. The README below it is the sensor's own manual,
 * shipped with every version and delivered to the browser all along.
 */
export function PrtgCard({ sensor }: { sensor: SensorDetail }) {
  const { t } = useTranslation()
  const [readmeOpen, setReadmeOpen] = useState(false)

  const script = sensor.files.find((file) => file.slot === 'script')
  const basename = script?.relative_path.split('/').pop() ?? null

  return (
    <Card title={t('sensors.prtg.title')}>
      <div className="space-y-3 text-sm">
        <p className="text-ink-2">{t('sensors.prtg.intro')}</p>
        {basename && (
          <div className="flex items-center gap-2">
            <InlineCode>
              {basename}
            </InlineCode>
            <CopyButton value={basename} />
          </div>
        )}
        <p className="text-ink-3 text-xs">{t('sensors.prtg.parametersHint')}</p>
        {sensor.readme && (
          <details onToggle={(event) => setReadmeOpen(event.currentTarget.open)}>
            <summary className="text-ink-3 cursor-pointer text-xs">
              {t('sensors.prtg.readme')}
            </summary>
            {readmeOpen && (
              <div className="border-rule bg-surface rounded-inset mt-2 max-h-96 overflow-auto border p-3">
                <Suspense
                  fallback={<div className="bg-surface h-20 animate-pulse rounded-inset" />}
                >
                  <Markdown resolveHref={(href) => sensorReadmeHref(sensor.name, href)}>
                    {sensor.readme}
                  </Markdown>
                </Suspense>
              </div>
            )}
          </details>
        )}
      </div>
    </Card>
  )
}

/** Repository-relative README links become routes inside the sensor catalog. */
export function sensorReadmeHref(sensorName: string, href: string) {
  const ownReadme = href.match(/^\.?\/?README\.md(#[^\s]*)?$/)
  if (ownReadme) return `/sensors/${encodeURIComponent(sensorName)}${ownReadme[1] ?? ''}`

  const otherReadme = href.match(/^\.\.\/([^/]+)\/README\.md(#[^\s]*)?$/)
  if (!otherReadme) return href

  return `/sensors/${encodeURIComponent(otherReadme[1])}${otherReadme[2] ?? ''}`
}

/**
 * The one surface for a sensor's parameters: fill in, look up, copy.
 *
 * This used to be two cards - a builder form and, one screen below it, a
 * reference table repeating every name, description, default and choice the
 * form had already shown. The source admitted it: "the reference below lists
 * the same parameters a second time". Now the form carries the reference
 * facts on its rows, and the two things the form never had - the parameters
 * PRTG substitutes itself, and the retired ones - live in collapsed sections
 * underneath.
 */
export function ParameterCard({
  sensorName,
  schema,
}: {
  sensorName: string
  schema: ParameterSchema | null
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, unknown>>({})
  const render = useRenderParameters(sensorName)

  // 'internal' is deployment plumbing (--self-check would build a line whose
  // sensor never measures); 'moved' produces a line the script rejects. The
  // sensor declares both - the filter only honours the declaration.
  const fields = (schema?.parameters ?? []).filter(
    (field) =>
      field.source !== 'prtg' &&
      field.group !== 'internal' &&
      field.group !== 'moved',
  )
  // Which parameters a variant can supply instead, so the row can say "you
  // may leave this out once a variant carries it".
  const suppliedBy = new Map<string, string>()
  for (const entry of schema
    ? [...schema.settings, ...schema.credentials, ...schema.files]
    : []) {
    if (entry.maps_to) suppliedBy.set(entry.maps_to, entry.name)
  }
  const fromPrtg = (schema?.parameters ?? []).filter(
    (field) => field.source === 'prtg',
  )
  const moved = (schema?.parameters ?? []).filter(
    (field) => field.group === 'moved',
  )
  // A sensor without parameters gets no card at all - a card that only says
  // "nothing here" is noise, not information.
  if (fields.length === 0) return null

  const missing = fields.filter(
    (field) => field.required && !values[field.name] && field.type !== 'boolean',
  )

  return (
    <Card title={t('sensors.parameters')}>
      <p className="text-ink-3 mb-3 text-sm">{t('sensors.parametersIntro')}</p>

      {schema?.default_parameter_line && (
        <div className="bg-surface-2 rounded-inset mb-4 p-3">
          <p className="text-ink-3 mb-1.5 text-xs">
            {t('sensors.reference.recommendedLine')}
          </p>
          <div className="flex items-start gap-2">
            <InlineCode className="flex-1">
              {schema.default_parameter_line}
            </InlineCode>
            <CopyButton value={schema.default_parameter_line} />
          </div>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <Field
            key={field.name}
            label={fieldLabel(t, field) + (field.required ? ' *' : '')}
            hint={
              [
                fieldDescription(t, field),
                suppliedBy.has(field.name)
                  ? t('sensors.reference.fromVariant', {
                      key: suppliedBy.get(field.name),
                    })
                  : '',
              ]
                .filter(Boolean)
                .join(' · ') || undefined
            }
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
              <Select
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
              </Select>
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
          <InlineCode className="flex-1">
            {render.data.parameters || '—'}
          </InlineCode>
          <CopyButton value={render.data.parameters} />
        </div>
      )}
      {render.error && <ErrorDetails error={render.error} />}

      {fromPrtg.length > 0 && (
        <details className="mt-4">
          <summary className="text-ink-3 cursor-pointer text-xs">
            {t('sensors.reference.fromPrtgSection')}
          </summary>
          <ul className="text-ink-2 mt-2 space-y-1.5 text-xs">
            {fromPrtg.map((field) => (
              <li key={field.name} className="flex flex-wrap items-baseline gap-2">
                <Mono>{fieldLabel(t, field)}</Mono>
                {field.prtg_placeholder && <Mono>{field.prtg_placeholder}</Mono>}
                <span>{fieldDescription(t, field)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {moved.length > 0 && (
        <details className="mt-3">
          <summary className="text-ink-3 cursor-pointer text-xs">
            {t('sensors.reference.movedSection')}
          </summary>
          <ul className="text-ink-3 mt-2 space-y-1 text-xs">
            {moved.map((field) => (
              <li key={field.name}>
                <Mono>{field.name}</Mono> · {fieldDescription(t, field)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  )
}
