import clsx from 'clsx'
import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { EmptyState, Input, Skeleton } from './primitives'

/**
 * The table every object list uses.
 *
 * Deliberately not a generic grid library: search, sort and selection are the
 * three things an administrator actually needs on a fleet of a few dozen rows,
 * and each is a few lines here rather than a configuration surface.
 */

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

interface DataTableProps<T> {
  rows: T[] | undefined
  columns: Column<T>[]
  rowKey: (row: T) => string
  isLoading?: boolean
  emptyTitle: string
  emptyHint?: string
  emptyAction?: ReactNode
  onRowClick?: (row: T) => void
  /** Enables the checkbox column and the bulk action bar. */
  selection?: {
    selected: Set<string>
    onChange: (selected: Set<string>) => void
    actions: ReactNode
  }
  /** Rendered between the search box and the table. */
  filters?: ReactNode
  searchPlaceholder?: string
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  isLoading,
  emptyTitle,
  emptyHint,
  emptyAction,
  onRowClick,
  selection,
  filters,
  searchPlaceholder,
}: DataTableProps<T>) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<{ key: string; direction: 'asc' | 'desc' } | null>(
    null,
  )

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

  function toggleSort(key: string) {
    setSort((current) => {
      if (current?.key !== key) return { key, direction: 'asc' }
      if (current.direction === 'asc') return { key, direction: 'desc' }
      return null
    })
  }

  return (
    <div className="surface-card overflow-hidden">
      <div className="border-rule flex flex-wrap items-center gap-3 border-b px-3 py-2">
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={searchPlaceholder ?? t('common.search')}
          className="w-56"
          aria-label={t('common.search')}
        />
        {filters}
        <span className="text-ink-3 ml-auto text-xs">
          {visible.length} {t('common.of')} {rows?.length ?? 0}
        </span>
      </div>

      {selection && selection.selected.size > 0 && (
        <div className="border-rule bg-accent-soft flex items-center gap-3 border-b px-3 py-2">
          <span className="text-ink text-sm font-medium">
            {t('common.selected', { count: selection.selected.size })}
          </span>
          <div className="ml-auto flex items-center gap-2">{selection.actions}</div>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2 p-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-9" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          title={rows?.length ? t('common.search') : emptyTitle}
          hint={rows?.length ? undefined : emptyHint}
          action={rows?.length ? undefined : emptyAction}
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead className="bg-surface-2">
              <tr>
                {selection && (
                  <th className="w-10 px-3 py-2">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label={t('common.selected', { count: visible.length })}
                    />
                  </th>
                )}
                {columns.map((column) => (
                  <th
                    key={column.key}
                    style={column.width ? { width: column.width } : undefined}
                    className={clsx(
                      'label-mono px-3 py-2 font-normal',
                      column.align === 'right' && 'text-right',
                    )}
                  >
                    {column.sortValue ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(column.key)}
                        className="hover:text-ink inline-flex items-center gap-1"
                      >
                        {column.header}
                        <span aria-hidden className="text-[0.625rem]">
                          {sort?.key === column.key
                            ? sort.direction === 'asc'
                              ? '▲'
                              : '▼'
                            : ''}
                        </span>
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
                return (
                  <tr
                    key={id}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    className={clsx(
                      'border-rule border-t',
                      onRowClick && 'hover:bg-surface-2 cursor-pointer',
                    )}
                  >
                    {selection && (
                      <td className="px-3 py-2" onClick={(event) => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selection.selected.has(id)}
                          onChange={() => toggleOne(id)}
                          aria-label={id}
                        />
                      </td>
                    )}
                    {columns.map((column) => (
                      <td
                        key={column.key}
                        className={clsx(
                          'px-3 py-2 text-sm',
                          column.align === 'right' && 'text-right',
                        )}
                      >
                        {column.cell(row)}
                      </td>
                    ))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
