import clsx from 'clsx'
import { Fragment, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, ChevronUp, X } from 'lucide-react'

import { useUrlState } from '@/utils/urlState'

import { Button, EmptyState, Input, Skeleton } from './primitives'

/**
 * The table every object list uses.
 *
 * Deliberately not a generic grid library: search, sort and selection are the
 * three things an administrator actually needs on a fleet of a few dozen rows,
 * and each is a few lines here rather than a configuration surface.
 */

// Below Tailwind's md breakpoint. Asked in script rather than left to CSS so
// only one of the two views is in the document: hidden by CSS, the other one
// would still hold a second checkbox and a second link for every row.
const PHONE_QUERY = '(max-width: 767px)'

function subscribePhone(onChange: () => void) {
  const query = window.matchMedia(PHONE_QUERY)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

export function useIsPhone() {
  return useSyncExternalStore(
    subscribePhone,
    () => window.matchMedia(PHONE_QUERY).matches,
    () => false,
  )
}

export interface Column<T> {
  key: string
  header: string
  /** Rendered cell. */
  cell: (row: T) => ReactNode
  /** Value used for sorting; omit to make the column unsortable. */
  sortValue?: (row: T) => string | number
  /** Text the search box matches against, in addition to the sort value. */
  searchValue?: (row: T) => string
  align?: 'left' | 'right'
  width?: string
}

type SortState = { key: string; direction: 'asc' | 'desc' }

/**
 * The sort order from the address: `name` ascending, `-name` descending,
 * `none` for the unsorted order of a list that has a default. A column that
 * does not exist or cannot be sorted - an old link - is the default.
 */
export function sortFrom<T>(
  value: string | null,
  columns: Column<T>[],
  defaultSort: SortState | undefined,
): SortState | null {
  if (value === null) return defaultSort ?? null
  if (value === 'none') return null
  const direction = value.startsWith('-') ? 'desc' : 'asc'
  const key = direction === 'desc' ? value.slice(1) : value
  const column = columns.find((entry) => entry.key === key)
  return column?.sortValue ? { key, direction } : (defaultSort ?? null)
}

interface DataTableProps<T> {
  rows: T[] | undefined
  columns: Column<T>[]
  rowKey: (row: T) => string
  isLoading?: boolean
  emptyTitle: string
  emptyHint?: string
  emptyAction?: ReactNode
  /**
   * Where a row leads, or null for one that leads nowhere.
   *
   * A path rather than a click handler, because with it the first cell becomes
   * a real link: reachable by keyboard, openable in a new tab, and something
   * the browser shows the destination of. The row keeps a click of its own for
   * the mouse, but the link is what makes the list usable without one.
   */
  rowHref?: (row: T) => string | null
  /** Enables the checkbox column and the bulk action bar. */
  selection?: {
    selected: Set<string>
    onChange: (selected: Set<string>) => void
    actions: ReactNode
  }
  /** Rendered between the search box and the table. */
  filters?: ReactNode
  searchPlaceholder?: string
  /**
   * Where the table keeps its search term, sort order and open rows.
   *
   * In the address by default, under `q`, `sort` and `open`: the term has to
   * survive opening a row and stepping back - which is most of what searching
   * a fleet is for - as well as a reload and a link sent to a colleague. Two
   * tables on one screen give each other a prefix (`interfaces` reads and
   * writes `interfaces-q`), or they would fight over the same parameters.
   * `null` keeps the state in the component, for a table inside a dialog.
   */
  urlKey?: string | null
  /**
   * The page's own filter parameters, cleared together with the search term
   * by the reset action the table shows while any of them is set.
   */
  filterKeys?: readonly string[]
  /**
   * Rendered under a row while it is open, with a toggle in its own column.
   *
   * For the detail a row summarises into a number - "3 of 5 probes succeeded"
   * is not an answer while two of them are broken. Omit it and rows stay flat.
   */
  expandedContent?: (row: T) => ReactNode
  /**
   * How a selected row is announced, for the checkbox label. Without it a
   * screen reader reads out the row key, which for most lists is a ULID.
   */
  rowLabel?: (row: T) => string
  /**
   * The row as a card, for screens below the md breakpoint.
   *
   * A table of seven columns on a phone either scrolls sideways - and the
   * column that matters, the status, is the one off screen - or squeezes every
   * cell into a word per line. Lists that are opened on a phone pass a card;
   * the others keep the scrolling table.
   */
  mobileCard?: (row: T) => ReactNode
  /** The order before anybody clicks a header. */
  defaultSort?: SortState
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  isLoading,
  emptyTitle,
  emptyHint,
  emptyAction,
  rowHref,
  selection,
  filters,
  searchPlaceholder,
  urlKey = '',
  filterKeys = [],
  expandedContent,
  rowLabel,
  mobileCard,
  defaultSort,
}: DataTableProps<T>) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const showCards = useIsPhone() && mobileCard !== undefined
  const { params, update } = useUrlState()
  const inUrl = urlKey !== null
  const keyOf = (name: string) => (urlKey ? `${urlKey}-${name}` : name)
  const queryKey = keyOf('q')
  const sortKey = keyOf('sort')
  const openKey = keyOf('open')

  const [localQuery, setLocalQuery] = useState('')
  const [localExpanded, setLocalExpanded] = useState<Set<string>>(new Set())
  const [localSort, setLocalSort] = useState<SortState | null>(defaultSort ?? null)

  const query = inUrl ? (params.get(queryKey) ?? '') : localQuery
  const expanded = inUrl ? new Set(params.getAll(openKey)) : localExpanded
  const sort = inUrl ? sortFrom(params.get(sortKey), columns, defaultSort) : localSort

  function setQuery(next: string) {
    if (inUrl) update({ [queryKey]: next })
    else setLocalQuery(next)
  }

  function setExpanded(next: Set<string>) {
    if (inUrl) update({ [openKey]: [...next] })
    else setLocalExpanded(next)
  }

  function setSort(next: SortState | null) {
    if (!inUrl) {
      setLocalSort(next)
      return
    }
    const isDefault =
      next === null
        ? defaultSort === undefined
        : next.key === defaultSort?.key && next.direction === defaultSort.direction
    update({
      [sortKey]: isDefault
        ? null
        : next === null
          ? 'none'
          : `${next.direction === 'desc' ? '-' : ''}${next.key}`,
    })
  }

  const filtered =
    query !== '' || (inUrl && filterKeys.some((key) => params.has(key)))

  function resetFilters() {
    if (!inUrl) {
      setLocalQuery('')
      return
    }
    update(Object.fromEntries([queryKey, ...filterKeys].map((key) => [key, null])))
  }

  const visible = useMemo(() => {
    if (!rows) return []
    const needle = query.trim().toLowerCase()
    let result = rows

    if (needle) {
      result = rows.filter((row) =>
        columns.some((column) => {
          const text =
            column.searchValue?.(row) ??
            (column.sortValue ? String(column.sortValue(row)) : '')
          return text.toLowerCase().includes(needle)
        }),
      )
    }

    if (sort) {
      const column = columns.find((entry) => entry.key === sort.key)
      if (column?.sortValue) {
        result = [...result].sort((left, right) => {
          const a = column.sortValue!(left)
          const b = column.sortValue!(right)
          const comparison = a < b ? -1 : a > b ? 1 : 0
          return sort.direction === 'asc' ? comparison : -comparison
        })
      }
    }

    return result
  }, [rows, columns, query, sort])

  const allSelected =
    selection !== undefined &&
    visible.length > 0 &&
    visible.every((row) => selection.selected.has(rowKey(row)))

  // Selected and then filtered or searched out of sight. The bulk action still
  // runs on them - a filter is for finding probes, not for silently changing
  // what a confirmed action does - so the bar has to say they are there.
  const hiddenSelected = selection
    ? selection.selected.size -
      visible.filter((row) => selection.selected.has(rowKey(row))).length
    : 0

  function toggleAll() {
    if (!selection) return
    const next = new Set(selection.selected)
    if (allSelected) {
      visible.forEach((row) => next.delete(rowKey(row)))
    } else {
      visible.forEach((row) => next.add(rowKey(row)))
    }
    selection.onChange(next)
  }

  function toggleOne(id: string) {
    if (!selection) return
    const next = new Set(selection.selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selection.onChange(next)
  }

  function toggleExpanded(id: string) {
    const next = new Set(expanded)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setExpanded(next)
  }

  function toggleSort(key: string) {
    if (sort?.key !== key) setSort({ key, direction: 'asc' })
    else if (sort.direction === 'asc') setSort({ key, direction: 'desc' })
    // Back to the order the list opened in, not to no order at all: for a
    // list with a default, "unsorted" is just the backend's insertion order.
    else setSort(defaultSort ?? null)
  }

  return (
    <div className="surface-card flex flex-col">
      <div className="border-rule flex flex-wrap items-center gap-3 border-b px-3 py-2">
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={searchPlaceholder ?? t('common.search')}
          className="w-full sm:w-56"
          aria-label={t('common.search')}
        />
        {filters}
        {filtered && (
          <Button size="sm" variant="ghost" icon={X} onClick={resetFilters}>
            {t('common.clearFilters')}
          </Button>
        )}
        <span className="text-ink-2 ml-auto text-xs tabular-nums">
          {visible.length} {t('common.of')} {rows?.length ?? 0}
        </span>
      </div>

      {selection && selection.selected.size > 0 && (
        // Last in the card and sticky on a phone: with the list longer than
        // the screen, the actions for what was just ticked stay in reach
        // instead of scrolling away above it.
        <div
          className={clsx(
            'border-rule bg-accent-soft flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2',
            'max-md:rounded-b-card max-md:sticky max-md:bottom-0 max-md:z-10 max-md:order-last max-md:border-t',
            'md:border-b',
          )}
        >
          <span className="text-ink text-sm font-medium">
            {hiddenSelected > 0
              ? t('common.selectedHidden', {
                  count: selection.selected.size,
                  hidden: hiddenSelected,
                })
              : t('common.selected', { count: selection.selected.size })}
          </span>
          <Button
            size="sm"
            variant="ghost"
            icon={X}
            onClick={() => selection.onChange(new Set())}
          >
            {t('common.clearSelection')}
          </Button>
          <div className="flex flex-wrap items-center gap-2 md:ml-auto">
            {selection.actions}
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2 p-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-9" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        // A search with no hits used to render "Search" as its heading - the
        // translated value of common.search, never meant as a title. It said
        // nothing about what was searched for and offered no way back.
        query ? (
          <EmptyState
            title={t('common.noMatches', { query: query.trim() })}
            action={
              <Button variant="secondary" onClick={() => setQuery('')}>
                {t('common.clearSearch')}
              </Button>
            }
          />
        ) : (
          <EmptyState title={emptyTitle} hint={emptyHint} action={emptyAction} />
        )
      ) : (
        <>
        {showCards && mobileCard ? (
          <div>
            {selection && (
              <label className="border-rule text-ink-2 flex min-h-11 items-center gap-3 border-b px-3 text-sm">
                <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                {t('common.selectAll')}
              </label>
            )}
            <ul>
              {visible.map((row) => {
                const id = rowKey(row)
                const href = rowHref?.(row) ?? null
                return (
                  <li key={id} className="border-rule flex items-stretch border-t first:border-t-0">
                    {selection && (
                      // The whole left strip is the checkbox: a 16px box is
                      // not something a thumb hits on the first try.
                      <label className="flex w-11 shrink-0 items-start justify-center pt-3.5">
                        <input
                          type="checkbox"
                          checked={selection.selected.has(id)}
                          onChange={() => toggleOne(id)}
                          aria-label={rowLabel?.(row) ?? id}
                        />
                      </label>
                    )}
                    {href ? (
                      <Link
                        to={href}
                        className={clsx(
                          'focus-visible:outline-accent active:bg-surface-2 block min-w-0 flex-1 py-3 pr-3 focus-visible:outline-2 focus-visible:-outline-offset-2',
                          !selection && 'pl-3',
                        )}
                      >
                        {mobileCard(row)}
                      </Link>
                    ) : (
                      <div className={clsx('min-w-0 flex-1 py-3 pr-3', !selection && 'pl-3')}>
                        {mobileCard(row)}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr>
                {selection && (
                  <th className="w-10 px-3 py-2">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label={t('common.selectAll')}
                    />
                  </th>
                )}
                {expandedContent && <th className="w-8 px-3 py-2" />}
                {columns.map((column) => (
                  <th
                    key={column.key}
                    style={column.width ? { width: column.width } : undefined}
                    aria-sort={
                      sort?.key === column.key
                        ? sort.direction === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : undefined
                    }
                    className={clsx(
                      'table-head h-9 px-3 whitespace-nowrap',
                      column.align === 'right' && 'text-right',
                    )}
                  >
                    {column.sortValue ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(column.key)}
                        className="hover:text-ink rounded-inset focus-visible:outline-accent -mx-1 inline-flex min-h-6 items-center gap-1 px-1 focus-visible:outline-2"
                      >
                        {column.header}
                        {sort?.key === column.key &&
                          (sort.direction === 'asc' ? (
                            <ChevronUp aria-hidden size={12} strokeWidth={1.75} />
                          ) : (
                            <ChevronDown aria-hidden size={12} strokeWidth={1.75} />
                          ))}
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const id = rowKey(row)
                const isOpen = expanded.has(id)
                const href = rowHref?.(row) ?? null
                return (
                  <Fragment key={id}>
                    <tr
                      // The mouse gets the whole row; the keyboard gets the
                      // link in the first cell. Giving the row a tabindex of
                      // its own as well would be a second stop on every row
                      // that leads exactly where the first one does.
                      onClick={href ? () => navigate(href) : undefined}
                      className={clsx(
                        'border-rule border-t',
                        href && 'hover:bg-hover cursor-pointer',
                      )}
                    >
                      {selection && (
                        <td
                          className="px-3 py-2"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={selection.selected.has(id)}
                            onChange={() => toggleOne(id)}
                            aria-label={rowLabel?.(row) ?? id}
                          />
                        </td>
                      )}
                      {expandedContent && (
                        <td
                          className="px-3 py-2"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <button
                            type="button"
                            onClick={() => toggleExpanded(id)}
                            aria-expanded={isOpen}
                            aria-label={t(isOpen ? 'common.hideDetails' : 'common.showDetails')}
                            className="text-ink-2 hover:text-ink inline-flex size-6 items-center justify-center"
                          >
                            {isOpen ? (
                              <ChevronDown aria-hidden size={14} strokeWidth={1.75} />
                            ) : (
                              <ChevronRight aria-hidden size={14} strokeWidth={1.75} />
                            )}
                          </button>
                        </td>
                      )}
                      {columns.map((column, index) => (
                        <td
                          key={column.key}
                          className={clsx(
                            'px-3 py-2 text-sm',
                            column.align === 'right' && 'text-right',
                          )}
                        >
                          {index === 0 && href ? (
                            // Stops the row's own handler from navigating a
                            // second time, which would push two entries onto
                            // the history for one click.
                            <Link
                              to={href}
                              onClick={(event) => event.stopPropagation()}
                              className="rounded-inset focus-visible:outline-accent block focus-visible:outline-2 focus-visible:outline-offset-2"
                            >
                              {column.cell(row)}
                            </Link>
                          ) : (
                            column.cell(row)
                          )}
                        </td>
                      ))}
                    </tr>
                    {expandedContent && isOpen && (
                      <tr className="border-rule bg-surface-2 border-t">
                        <td
                          colSpan={columns.length + (selection ? 1 : 0) + 1}
                          className="px-3 py-3"
                        >
                          {expandedContent(row)}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
        )}
        </>
      )}
    </div>
  )
}
