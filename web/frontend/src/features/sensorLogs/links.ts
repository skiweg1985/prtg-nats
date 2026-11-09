import type { SensorLogFilters } from '@/api/types'

/** A link into the sensor log with the given filters applied. */
export function sensorLogHref(filters: Partial<SensorLogFilters>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value)
  }
  return `/sensor-logs?${params.toString()}`
}
