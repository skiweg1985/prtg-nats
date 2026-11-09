import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'

import {
  useSensorLog,
  useSensorLogStream,
  useSensorLogs,
  useSensors,
} from '@/api/hooks'
import type { SensorLogFilters, SensorLogRange, SensorLogResult } from '@/api/types'
import { Breadcrumb } from '@/components/ui/Breadcrumb'
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
import { probeSensorHref } from '@/features/probes/links'
import { useProbeNames } from '@/features/probes/probeName'
import { formatDateTime, formatNumber, formatSpanEnd } from '@/utils/format'
import { pageNumber, useDebouncedUrlParam, useUrlState, type ParamPatch } from '@/utils/urlState'

import { RecordBody, ResultBadge, RunTable, networkOf, useCodeLabel } from './RunList'

const RANGES: SensorLogRange[] = ['1h', '24h', '7d', '14d']
const RESULTS: SensorLogResult[] = ['ok', 'error', 'gap']
const DEFAULT_RANGE: SensorLogRange = '24h'

/**
 * One page of runs. Paged rather than loaded on and on: a sensor that runs
 * every minute writes over a thousand runs a day, more than "load more" up
 * to the API's cap of 500 could ever reach.
 */
const PAGE_SIZE = 100

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
    since: validTime(params.get('since')),
    until: validTime(params.get('until')),
    q: params.get('q') || undefined,
  }
}

/** An ISO time from the address, or nothing if it does not parse. */
function validTime(value: string | null): string | undefined {
  return value && !Number.isNaN(Date.parse(value)) ? value : undefined
}

// The range select's value while an exact window from a timeline applies.
const CUSTOM_RANGE = 'custom'


/**
 * Every run every probe reported, filtered on the server.
 *
 * The filters live in the address, so a filtered view survives a reload and
 * can be sent to a colleague like a single run can.
 */
export function SensorLogPage() {
  const { t } = useTranslation()
  const { params, update, replaceAll } = useUrlState()
  const filters = filtersFrom(params)
  // In the address with the filters, so a page can be reloaded and sent on.
  const page = pageNumber(params.get('page'))
  const offset = (page - 1) * PAGE_SIZE
  const listTop = useRef<HTMLDivElement>(null)
  const { data, isLoading, error, refetch } = useSensorLogs(filters, PAGE_SIZE, offset)
  const { probes, nameOf } = useProbeNames()
  const { data: sensors } = useSensors()
  useSensorLogStream()

  // Other filters, other runs: page 3 of the old list means nothing here.
  const [term, setTerm] = useDebouncedUrlParam(
    'q',
    SEARCH_DELAY_MS,
    (patch: ParamPatch) => ({ ...patch, page: null }),
  )

  function setFilter(key: keyof SensorLogFilters, value: string) {
    const patch: ParamPatch = {
      // The default range is no filter, so it stays out of the address.
      [key]: key === 'range' && value === DEFAULT_RANGE ? null : value,
      page: null,
    }
    // A chosen range ends the exact window a timeline link brought along.
    if (key === 'range') Object.assign(patch, { since: null, until: null })
    update(patch)
  }

  function goTo(target: number) {
    // Pushed, unlike a filter: Back from page 3 goes to page 2.
    update({ page: target > 1 ? String(target) : null }, { push: true })
    // The buttons sit under the list; the next page starts at its top.
    listTop.current?.scrollIntoView?.({ block: 'start' })
  }

  const filtered = Boolean(
    filters.probe ||
      filters.sensor ||
      filters.ssid ||
      filters.result ||
      filters.q ||
      filters.since ||
      filters.range !== DEFAULT_RANGE,
  )

  function clearFilters() {
    setTerm('')
    replaceAll(new URLSearchParams())
  }

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-4">
      <PageHeader title={t('sensorLogs.title')} subtitle={t('sensorLogs.subtitle')} />

      <div ref={listTop} className="surface-card scroll-mt-4 overflow-hidden">
        <div className="border-rule flex flex-wrap items-center gap-2 border-b px-3 py-2">
          <Input
            type="search"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            placeholder={t('sensorLogs.filters.search')}
            aria-label={t('sensorLogs.filters.search')}
            className="w-full sm:w-56"
          />
          <Select
            aria-label={t('sensorLogs.filters.range')}
            value={filters.since ? CUSTOM_RANGE : filters.range}
            onChange={(event) => setFilter('range', event.target.value)}
          >
            {filters.since && (
              <option value={CUSTOM_RANGE}>
                {t('sensorLogs.range.custom', {
                  from: formatDateTime(filters.since),
                  to: filters.until
                    ? formatSpanEnd(filters.since, filters.until)
                    : t('sensorLogs.range.now'),
                })}
              </option>
            )}
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
            {/* Chosen by the name the fleet shows; the address keeps the
                account, because that is what the records carry. */}
            {withCurrent(
              probes.map((probe) => probe.nats_username),
              filters.probe,
            )
              .map((account) => ({ account, name: nameOf(account) }))
              .sort((left, right) => left.name.localeCompare(right.name))
              .map(({ account, name }) => (
                <option key={account} value={account}>
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
          {filtered && (
            <Button size="sm" variant="ghost" icon={X} onClick={clearFilters}>
              {t('common.clearFilters')}
            </Button>
          )}
          <span className="text-ink-3 ml-auto text-xs">
            {items.length === 0
              ? t('sensorLogs.count', { from: 0, to: 0, total: formatNumber(total) })
              : t('sensorLogs.count', {
                  from: formatNumber(offset + 1),
                  to: formatNumber(offset + items.length),
                  total: formatNumber(total),
                })}
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

        {pages > 1 && (
          <nav
            aria-label={t('sensorLogs.pagination')}
            className="border-rule flex items-center justify-between gap-3 border-t px-3 py-2"
          >
            <Button
              size="sm"
              variant="ghost"
              icon={ChevronLeft}
              disabled={page <= 1}
              onClick={() => goTo(page - 1)}
            >
              {t('sensorLogs.previous')}
            </Button>
            <span className="text-ink-2 text-xs tabular-nums">
              {t('sensorLogs.page', { page: Math.min(page, pages), pages })}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={page >= pages}
              onClick={() => goTo(page + 1)}
            >
              {t('sensorLogs.next')}
              <ChevronRight aria-hidden size={14} />
            </Button>
          </nav>
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
  const { probeOf, nameOf } = useProbeNames()
  const codeLabel = useCodeLabel()

  if (isLoading) return <Skeleton className="h-64" />
  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const probe = probeOf(data.probe)
  const probeLabel = nameOf(data.probe)
  const network = networkOf(data)

  return (
    <div className="space-y-4">
      {/* The network leads where there is one: on a probe testing four
          SSIDs, it is what tells two runs of the same sensor apart. */}
      <PageHeader
        title={network ? `${data.sensor} · ${network}` : data.sensor}
        subtitle={`${probeLabel} · ${formatDateTime(data.time)}`}
        breadcrumb={
          <Breadcrumb
            trail={
              probe
                ? [
                    { to: '/probes', label: t('probes.title') },
                    { to: `/probes/${probe.id}?tab=sensors`, label: probeLabel },
                    { to: probeSensorHref(probe.id, data.sensor), label: data.sensor },
                  ]
                : [{ to: '/sensor-logs', label: t('sensorLogs.title') }]
            }
            current={t('breadcrumb.runAt', { time: formatDateTime(data.time) })}
          />
        }
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
            {data.code ? (
              <span className="flex flex-wrap items-baseline gap-x-2">
                {codeLabel(data.sensor, data.code)}
                <Mono className="text-ink-3">{data.code}</Mono>
              </span>
            ) : (
              '—'
            )}
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.probe')}>
            {probe ? (
              <Link
                to={probeSensorHref(probe.id, data.sensor)}
                className="text-accent hover:underline"
              >
                {probeLabel}
              </Link>
            ) : (
              <Mono>{data.probe}</Mono>
            )}
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.sensor')}>
            {probe ? (
              <Link
                to={probeSensorHref(probe.id, data.sensor)}
                className="text-accent hover:underline"
              >
                <Mono>{data.sensor}</Mono>
              </Link>
            ) : (
              <Mono>{data.sensor}</Mono>
            )}
          </DetailRow>
          <DetailRow label={t('sensorLogs.columns.network')}>
            <Mono>{network ?? '—'}</Mono>
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
        <RecordBody body={data.body} sensor={data.sensor} />
      </Card>
    </div>
  )
}
