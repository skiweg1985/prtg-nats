import type { SensorLogRecord } from '@/api/types'

/**
 * The steps of a run, read back from what the sensor stored.
 *
 * Only wlan-auth has steps worth naming today. Its helper times each stage
 * it got through - association, authentication, DHCP, the gateway - and
 * stops at the first one that fails, so the stage that failed is the first
 * one without a time, unless the result code names it outright. A run that
 * never reached the radio failed in the preparation before any of them.
 */

export type PhaseKey = 'prepare' | 'assoc' | 'auth' | 'dhcp' | 'gateway'
export type PhaseState = 'ok' | 'failed' | 'skipped'

export interface Phase {
  key: PhaseKey
  state: PhaseState
  /** How long the step took; null where it was not timed. */
  ms: number | null
}

const TIMING: Partial<Record<PhaseKey, string>> = {
  assoc: 'assoc_ms',
  auth: 'auth_ms',
  dhcp: 'dhcp_ms',
  gateway: 'gateway_ms',
}

// Codes that name their stage. Anything not here is placed by the timings.
const CODE_PHASE: Record<string, PhaseKey> = {
  'assoc-timeout': 'assoc',
  'assoc-rejected': 'assoc',
  'ssid-not-found': 'assoc',
  'auth-failed-psk': 'auth',
  'auth-failed-eap': 'auth',
  'server-cert-rejected': 'auth',
  'auth-rejected': 'auth',
  'auth-timeout': 'auth',
  'dhcp-timeout': 'dhcp',
  'dhcp-client-broken': 'dhcp',
  'gateway-unreachable': 'gateway',
  'iface-missing': 'prepare',
  'iface-down': 'prepare',
  'iface-busy': 'prepare',
  'iface-blocked': 'prepare',
  'iface-not-wireless': 'prepare',
  'iface-not-reserved': 'prepare',
  'iface-protected': 'prepare',
  'mac-not-settable': 'prepare',
  'profile-missing': 'prepare',
  'file-missing': 'prepare',
  'tool-missing': 'prepare',
  'ping-missing': 'prepare',
  'no-privileges': 'prepare',
  'too-many-jobs': 'prepare',
  'bad-request': 'prepare',
  'internal-error': 'prepare',
}

const PHASED_SENSORS = ['wlan-auth']

/** Whether a sensor's runs can be shown as steps at all. */
export function hasPhases(sensor: string): boolean {
  return PHASED_SENSORS.includes(sensor)
}

function timingsOf(body: Record<string, unknown>): Record<string, number> {
  const raw = body.timings
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return {}
  return Object.fromEntries(
    Object.entries(raw).filter((entry): entry is [string, number] => typeof entry[1] === 'number'),
  )
}

function timeOf(timings: Record<string, number>, key: PhaseKey): number | null {
  const field = TIMING[key]
  return field && field in timings ? timings[field] : null
}

/** A run's steps in order, or an empty list for a sensor without any. */
export function phasesOf(record: Pick<SensorLogRecord, 'sensor' | 'result' | 'code' | 'body'>): Phase[] {
  if (!hasPhases(record.sensor)) return []
  const body = record.body
  const timings = timingsOf(body)
  const failed = record.result === 'error'
  const named = record.code ? CODE_PHASE[record.code] : undefined

  const steps: PhaseKey[] = ['assoc', 'auth']
  if (body.stage === 'dhcp') {
    steps.push('dhcp')
    // The gateway is asked unless the configuration said not to, which the
    // record does not keep: it shows only where it was asked or timed.
    if (body.gateway || 'gateway_ms' in timings || named === 'gateway') steps.push('gateway')
  }

  if (!failed) {
    return steps.map((key) => ({ key, state: 'ok', ms: timeOf(timings, key) }))
  }

  if (named === 'prepare') {
    return [
      { key: 'prepare', state: 'failed', ms: null },
      ...steps.map((key): Phase => ({ key, state: 'skipped', ms: null })),
    ]
  }

  const culprit =
    (named && steps.includes(named) ? named : undefined) ??
    steps.find((key) => timeOf(timings, key) === null) ??
    steps[steps.length - 1]
  const at = steps.indexOf(culprit)
  return steps.map((key, index) => ({
    key,
    state: index < at ? 'ok' : index === at ? 'failed' : 'skipped',
    ms: index <= at ? timeOf(timings, key) : null,
  }))
}

/** The fields of a body that describe one step, shown when it is opened. */
export const PHASE_FIELDS: Record<PhaseKey, string[]> = {
  prepare: ['interface', 'client_mac', 'client_mac_kind'],
  assoc: ['bssid', 'frequency_mhz', 'signal_dbm'],
  auth: ['auth', 'identity'],
  dhcp: ['ip_address', 'dhcp_server'],
  gateway: ['gateway', 'gateway_kind'],
}
