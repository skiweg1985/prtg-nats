import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useProbes,
  useSensorLog,
  useSensorLogStream,
  useSensorLogs,
  useSensors,
} from '@/api/hooks'
import type { SensorLogFilters, SensorLogRange, SensorLogResult } from '@/api/types'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Button,
  Card,
  DetailRow,
  Input,
  Mono,
  PageHeader,
  Select,
  Skeleton,
} from '@/components/ui/primitives'
import { formatDateTime } from '@/utils/format'

import { RecordBody, ResultBadge, RunTable } from './RunList'

const RANGES: SensorLogRange[] = ['1h', '24h', '7d', '14d']
const RESULTS: SensorLogResult[] = ['ok', 'error', 'gap']
const DEFAULT_RANGE: SensorLogRange = '24h'

/** One page of runs, and how far "load more" may take it - the API's cap. */
const PAGE_SIZE = 100
const MAX_LIMIT = 500

// Typing a search term should not send a request per keystroke.
const SEARCH_DELAY_MS = 300

function filtersFrom(params: URLSearchParams): SensorLogFilters {
  const range = RANGES.find((entry) => entry === params.get('range')) ?? DEFAULT_RANGE
  const result = RESULTS.find((entry) => entry === params.get('result'))
  return {
    range,
    probe: params.get('probe') || undefined,
    sensor: params.get('sensor') || undefined,
    ssid: params.get('ssid') || undefined,
    result,
    q: params.get('q') || undefined,
  }
}

/**
 * Every run every probe reported, filtered on the server.
 *
 * The filters live in the address, so a filtered view survives a reload and
 * can be sent to a colleague like a single run can.
 */
export function SensorLogPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const filters = filtersFrom(params)
  const [limit, setLimit] = useState(PAGE_SIZE)
  const { data, isLoading, error, refetch } = useSensorLogs(filters, limit)
  const { data: probes } = useProbes()
  const { data: sensors } = useSensors()
  useSensorLogStream()

  const [term, setTerm] = useState(filters.q ?? '')

  function setFilter(key: keyof SensorLogFilters, value: string) {
    const next = new URLSearchParams(params)
    // The default range is no filter, so it stays out of the address.
    if (value && !(key === 'range' && value === DEFAULT_RANGE)) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
    setLimit(PAGE_SIZE)
  }

  useEffect(() => {
    if (term === (filters.q ?? '')) return
    const timer = window.setTimeout(() => setFilter('q', term.trim()), SEARCH_DELAY_MS)
    return () => window.clearTimeout(timer)
    // setFilter is rebuilt every render; the term is what this waits on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [term])

  const filtered = Boolean(
    filters.probe ||
      filters.sensor ||
      filters.ssid ||
      filters.result ||
      filters.q ||
      filters.range !== DEFAULT_RANGE,
  )

  function clearFilters() {
    setTerm('')
    setParams(new URLSearchParams(), { replace: true })
    setLimit(PAGE_SIZE)
  }

  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <div className="space-y-4">
      <PageHeader title={t('sensorLogs.title')} />

      <div className="surface-card overflow-hidden">
        <div className="border-rule flex flex-wrap items-center gap-2 border-b px-3 py-2">
          <Input
            type="search"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            placeholder={t('sensorLogs.filters.search')}
            aria-label={t('sensorLogs.filters.search')}
            className="w-56"
          />
          <Select
            aria-label={t('sensorLogs.filters.range')}
            value={filters.range}
            onChange={(event) => setFilter('range', event.target.value)}
          >
            {RANGES.map((range) => (
              <option key={range} value={range}>
                {t(`sensorLogs.range.${range}`)}
              </option>
            ))}
          </Select>
          <Select
            aria-label={t('sensorLogs.filters.result')}
            value={filters.result ?? ''}
            onChange={(event) => setFilter('result', event.target.value)}
          >
            <option value="">{t('sensorLogs.filters.anyResult')}</option>
            {RESULTS.map((result) => (
              <option key={result} value={result}>
                {t(`sensorLogs.result.${result}`)}
              </option>
            ))}
          </Select>
          <Select
            aria-label={t('sensorLogs.filters.probe')}
            value={filters.probe ?? ''}
            onChange={(event) => setFilter('probe', event.target.value)}
          >
            <option value="">{t('sensorLogs.filters.anyProbe')}</option>
            {withCurrent(
              (probes ?? []).map((probe) => probe.nats_username),
              filters.probe,
            ).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
          <Select
            aria-label={t('sensorLogs.filters.sensor')}
            value={filters.sensor ?? ''}
            onChange={(event) => setFilter('sensor', event.target.value)}
          >
            <option value="">{t('sensorLogs.filters.anySensor')}</option>
            {withCurrent(
              (sensors ?? []).map((sensor) => sensor.name),
              filters.sensor,
            ).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
          <NetworkFilter
            value={filters.ssid ?? ''}
            onCommit={(value) => setFilter('ssid', value)}
          />
          <span className="text-ink-3 ml-auto text-xs">
            {t('sensorLogs.count', { shown: items.length, total })}
          </span>
        </div>

        {error ? (
          <div className="p-3">
            <ErrorDetails error={error} onRetry={() => void refetch()} />
          </div>
        ) : isLoading ? (
          <div className="space-y-2 p-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-9" />
            ))}
          </div>
        ) : (
          <RunTable
            runs={items}
            emptyTitle={filtered ? t('sensorLogs.emptyFiltered') : t('sensorLogs.empty')}
            emptyAction={
              filtered ? (
                <Button variant="secondary" onClick={clearFilters}>
                  {t('common.clearFilters')}
                </Button>
              ) : undefined
            }
          />
        )}

        {items.length < total && limit < MAX_LIMIT && (
          <div className="border-rule flex justify-center border-t px-3 py-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setLimit(Math.min(limit + PAGE_SIZE, MAX_LIMIT))}
            >
              {t('sensorLogs.loadMore')}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

/** A filter value from a link may name something the list does not offer. */
function withCurrent(options: string[], current: string | undefined): string[] {
  const unique = [...new Set(options)].sort()
  return current && !unique.includes(current) ? [current, ...unique] : unique
}

/** Free text, because no list of networks exists outside the records. */
function NetworkFilter({
  value,
  onCommit,
}: {
  value: string
  onCommit: (value: string) => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  return (
    <Input
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => draft.trim() !== value && onCommit(draft.trim())}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onCommit(draft.trim())
      }}
      placeholder={t('sensorLogs.filters.network')}
      aria-label={t('sensorLogs.filters.network')}
      className="w-40"
    />
  )
}

/** One run on a page of its own, for the link sent to a colleague. */
export function SensorLogRecordPage() {
  const { t } = useTranslation()
  const { recordId } = useParams<{ recordId: string }>()
  const { data, isLoading, error, refetch } = useSensorLog(recordId)
  const { data: probes } = useProbes()

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const probe = probes?.find((entry) => entry.nats_username === data.probe)

  return (
    <div className="space-y-4">
      <PageHeader
        title={`${data.sensor} · ${data.probe}`}
        subtitle={formatDateTime(data.time)}
        backTo="/sensor-logs"
        backLabel={t('sensorLogs.title')}
      />
      <Card>
        <dl>
          <DetailRow label={t('sensorLogs.columns.result')}>
            <ResultBadge result={data.result} />
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.summary')}>
            {data.summary || '—'}
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.code')}>
            <Mono>{data.code ?? '—'}</Mono>
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.probe')}>
            {probe ? (
              <Link
                to={`/probes/${probe.id}?tab=sensors`}
                className="text-accent hover:underline"
              >
                <Mono>{data.probe}</Mono>
              </Link>
            ) : (
              <Mono>{data.probe}</Mono>
            )}
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.sensor')}>
            <Mono>{data.sensor}</Mono>
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.network')}>
            <Mono>{data.ssid ?? '—'}</Mono>
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.time')}>
            {formatDateTime(data.time)}
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.id')}>
            <Mono>{data.id}</Mono>
          </DetailRow>
        </dl>
      </Card>
      <Card title={t('sensorLogs.output')}>
        <RecordBody body={data.body} />
      </Card>
    </div>
  )
}
