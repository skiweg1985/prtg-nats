import i18n from '@/i18n'

/**
 * Dates, durations and sizes go through Intl, never through translation
 * strings. A locale that formats "2 min" differently is a locale problem, not
 * a wording problem, and Intl already knows the answer.
 */

function locale(): string {
  return i18n.resolvedLanguage ?? 'en'
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(locale(), {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(locale(), {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

/** "3 minutes ago". Falls back to an absolute date beyond a week. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return '—'
  const then = new Date(value).getTime()
  const seconds = Math.round((then - Date.now()) / 1000)
  const absolute = Math.abs(seconds)

  if (absolute > 7 * 86_400) return formatDateTime(value)

  const format = new Intl.RelativeTimeFormat(locale(), { numeric: 'auto' })
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['second', 60],
    ['minute', 3600],
    ['hour', 86_400],
    ['day', 7 * 86_400],
  ]
  let previous = 1
  for (const [unit, limit] of units) {
    if (absolute < limit) return format.format(Math.round(seconds / previous), unit)
    previous = limit
  }
  return formatDateTime(value)
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 1) return '<1s'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return `${minutes}m ${rest}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  )
  const value = bytes / 1024 ** exponent
  return `${new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 }).format(value)} ${units[exponent]}`
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat(locale()).format(value)
}

/** A fingerprint, shortened for a table cell but never silently truncated. */
export function shortFingerprint(value: string | null | undefined): string {
  if (!value) return '—'
  return value.length <= 16 ? value : `${value.slice(0, 8)}…${value.slice(-8)}`
}
