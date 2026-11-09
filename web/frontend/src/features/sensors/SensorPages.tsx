import { lazy, Suspense, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronRight, FileCode, Upload } from 'lucide-react'

import { fieldDescription, fieldLabel } from './parameterFields'

import { useRenderParameters, useSensor, useSensors } from '@/api/hooks'
import type { ParameterSchema, SensorDetail, SensorSummary } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Breadcrumb } from '@/components/ui/Breadcrumb'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { UrlDetails, useUrlDetails } from '@/components/ui/UrlDetails'
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
  PageHeader,
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
          <SensorTraits sensor={row} />
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
      cell: (row) => (
        // The manifest ships English only; marked so a screen reader
        // reading the German interface pronounces it as English.
        <span lang="en" className="text-ink-2 text-sm">
          {row.description}
        </span>
      ),
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
          // A control that looks like one: the count used to be an amber badge
          // that happened to be clickable, which is a state pretending.
          <Button
            size="sm"
            icon={Upload}
            onClick={(event) => {
              event.stopPropagation()
              setUpdating(row.name)
            }}
            title={t('sensors.updateOutdated')}
            aria-label={`${t('sensors.updateOutdated')}: ${row.outdated_on}`}
          >
            {row.outdated_on}
          </Button>
        ) : (
          <span className="text-ink-3 text-sm">0</span>
        ),
    },
  ]

  return (
    <div className="space-y-4">
      <PageHeader title={t('sensors.title')} subtitle={t('sensors.subtitle')} />

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

/**
 * What a sensor needs from a probe, as a quiet line under its name.
 *
 * These are facts about the sensor, not states of anything: as badges they
 * stood level with the outdated count, the one thing in the row that does
 * ask for attention.
 */
function SensorTraits({ sensor }: { sensor: SensorSummary }) {
  const { t } = useTranslation()
  const traits = [
    sensor.needs_interface && t('sensors.metaInterface'),
    sensor.requires_privileged_helper && t('sensors.metaHelper'),
    // The kind is a token the catalog matches endpoints on. What it means to
    // somebody reading the list is that this sensor needs one.
    sensor.iperf_kind && t('sensors.metaEndpoint'),
  ].filter(Boolean)
  if (traits.length === 0) return null
  return <p className="text-ink-3 mt-0.5 text-xs">{traits.join(' · ')}</p>
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
          <Breadcrumb trail={[{ to: '/sensors', label: t('sensors.title') }]} />
          <div className="mt-1 flex items-center gap-2">
            <h1 className="text-lg">{data.name}</h1>
            <Badge tone="accent">v{data.version}</Badge>
          </div>
          <p lang="en" className="text-ink-2 text-sm">{data.description}</p>
        </div>
        <PermissionGate permission="deployment.create">
          <Button variant="primary" size="sm" icon={Upload} onClick={() => setDeploying(true)}>
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
            <EmptyState
              title={t('sensors.installedOnEmpty')}
              hint={t('sensors.installedOnEmptyHint')}
            />
          ) : (
            <ul className="space-y-1">
              {data.installations.map((entry) => (
                <li key={entry.probe} className="flex items-center gap-2">
                  <ProbeLink username={entry.probe} sensor={data.name} />
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

      <SensorVariants
        sensorName={name}
        schema={data.parameter_schema}
        needsInterface={data.needs_interface}
      />
      <ParameterCard sensorName={name} schema={data.parameter_schema} />

      {/* Reference, last and folded: paths and checksums are what somebody
          comparing a probe against the catalog looks up, not what somebody
          rolling the sensor out reads on the way to the button. */}
      <UrlDetails param="files" className="surface-card group overflow-hidden">
        <summary className="hover:bg-surface-2 flex min-h-11 cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm font-semibold [&::-webkit-details-marker]:hidden">
          <ChevronRight
            aria-hidden
            size={16}
            strokeWidth={1.75}
            className="text-ink-3 shrink-0 transition-transform group-open:rotate-90"
          />
          {t('sensors.files')}
          <span className="text-ink-3 font-normal tabular-nums">{data.files.length}</span>
        </summary>
        <dl className="border-rule border-t p-4">
          {data.files.map((file) => (
            <DetailRow key={file.slot} label={file.slot}>
              <Mono truncate>{file.relative_path}</Mono>
              <p className="text-ink-3 mt-0.5 font-mono text-[0.6875rem]">
                {formatBytes(file.size_bytes)} · {shortFingerprint(file.sha256)}
              </p>
            </DetailRow>
          ))}
        </dl>
      </UrlDetails>

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
  const [readmeOpen, setReadmeOpen] = useUrlDetails('readme')

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
          <details
            open={readmeOpen}
            onToggle={(event) => {
              if (event.currentTarget.open !== readmeOpen) setReadmeOpen(event.currentTarget.open)
            }}
          >
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
  // Folded when there is a shorter way: a recommended line to copy, or
  // variants, whose single parameter replaces most of the form - wlan-auth
  // opened on twenty-one fields although its variants exist for exactly
  // that. A sensor with neither has nothing else to offer, so it starts open.
  const hasVariants = Boolean(
    schema &&
      schema.settings.length + schema.credentials.length + schema.files.length > 0,
  )
  const [formOpen, setFormOpen] = useUrlDetails(
    'form',
    !schema?.default_parameter_line && !hasVariants,
  )

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
      <p className="text-ink-3 mb-3 text-sm">
        {t(
          hasVariants && !schema?.default_parameter_line
            ? 'sensors.parametersIntroVariants'
            : 'sensors.parametersIntro',
        )}
      </p>

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

      <details
        open={formOpen}
        onToggle={(event) => {
          if (event.currentTarget.open !== formOpen) setFormOpen(event.currentTarget.open)
        }}
        className="group"
      >
        <summary className="text-ink-2 hover:text-ink -mx-1 flex min-h-11 cursor-pointer list-none items-center gap-2 px-1 text-sm [&::-webkit-details-marker]:hidden">
          <ChevronRight
            aria-hidden
            size={16}
            strokeWidth={1.75}
            className="text-ink-3 shrink-0 transition-transform group-open:rotate-90"
          />
          {t('sensors.parametersBuild', { count: fields.length })}
        </summary>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
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
          {/* Secondary: the page's primary is rolling the sensor out. */}
          <Button size="sm" icon={FileCode} onClick={() => render.mutate(values)}>
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
      </details>

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
