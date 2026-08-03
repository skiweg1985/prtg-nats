import { describe, expect, it } from 'vitest'

import de from '@/i18n/locales/de.json'
import en from '@/i18n/locales/en.json'

/**
 * The translation tables are data, and data can be checked.
 *
 * The CI script does the same thing against the backend as well; this test
 * keeps the fast feedback loop in the editor.
 */

function flatten(value: unknown, prefix = ''): Map<string, string> {
  const out = new Map<string, string>()
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
      for (const [nested, nestedValue] of flatten(entry, path)) out.set(nested, nestedValue)
    } else {
      out.set(path, String(entry))
    }
  }
  return out
}

const english = flatten(en)
const german = flatten(de)
const placeholder = /\{\{(\w+)\}\}/g

describe('translations', () => {
  it('carries every English key in German', () => {
    const missing = [...english.keys()].filter((key) => !german.has(key))
    expect(missing).toEqual([])
  })

  it('has no German key without an English source', () => {
    const orphaned = [...german.keys()].filter(
      (key) => !english.has(key) && !/_(one|other|zero|few|many)$/.test(key),
    )
    expect(orphaned).toEqual([])
  })

  it('uses the same interpolation placeholders in both languages', () => {
    const mismatches: string[] = []
    for (const [key, text] of english) {
      const germanText = german.get(key)
      if (germanText === undefined) continue
      const left = new Set([...text.matchAll(placeholder)].map((match) => match[1]))
      const right = new Set([...germanText.matchAll(placeholder)].map((m) => m[1]))
      if (left.size !== right.size || [...left].some((name) => !right.has(name))) {
        mismatches.push(key)
      }
    }
    expect(mismatches).toEqual([])
  })

  it('never leaves a value empty', () => {
    const empty = [...english.entries()]
      .concat([...german.entries()])
      .filter(([, value]) => value.trim() === '')
      .map(([key]) => key)
    expect(empty).toEqual([])
  })

  it('covers every status enum the API can return', () => {
    // A status without a label renders as a raw key inside a badge, which is
    // exactly the kind of thing nobody notices until it is in front of a
    // customer.
    const expected: Record<string, string[]> = {
      probe: ['pending', 'enrolled', 'healthy', 'degraded', 'unreachable', 'retired'],
      sensor: ['absent', 'current', 'outdated', 'drifted', 'failed', 'unmanaged'],
      job: [
        'queued',
        'running',
        'successful',
        'failed',
        'cancelled',
        'partially_successful',
      ],
      certificate: ['valid', 'expiring_soon', 'expired', 'mismatched', 'missing'],
      service: ['active', 'inactive', 'unknown'],
      ca: ['ok', 'missing', 'mismatched', 'unknown'],
      nats: ['connected', 'disconnected', 'unknown'],
      audit: ['success', 'failure', 'denied'],
    }

    for (const [group, values] of Object.entries(expected)) {
      for (const value of values) {
        expect(english.has(`status.${group}.${value}`), `en status.${group}.${value}`).toBe(
          true,
        )
        expect(german.has(`status.${group}.${value}`), `de status.${group}.${value}`).toBe(
          true,
        )
      }
    }
  })
})
