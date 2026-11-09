import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import type { TFunction } from 'i18next'
import { beforeEach, describe, expect, it } from 'vitest'

import i18n, { changeLanguage } from '@/i18n'
import {
  choiceLabel,
  fieldDescription,
  fieldKey,
  fieldLabel,
} from '@/features/sensors/parameterFields'

/**
 * Every field of every shipped sensor has a human label and help in both
 * languages. The schemas ship in English; without an entry here the German
 * interface would show the raw parameter name and an English sentence.
 */

const SENSORS_DIR = join(__dirname, '..', '..', '..', 'sensors')

interface SchemaField {
  name: string
  choices?: string[]
  description?: string
}

function shippedSensors() {
  return readdirSync(SENSORS_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) => {
      try {
        const schema = JSON.parse(
          readFileSync(join(SENSORS_DIR, entry.name, 'parameters.json'), 'utf8'),
        ) as Record<string, SchemaField[] | undefined>
        const fields = ['parameters', 'settings', 'credentials', 'files'].flatMap(
          (section) => schema[section] ?? [],
        )
        return [{ sensor: entry.name, fields }]
      } catch {
        return []
      }
    })
}

/** In that language itself - not through the English fallback. */
function has(lng: string, key: string) {
  return typeof i18n.getResource(lng, 'translation', key) === 'string'
}

const t = ((...args: Parameters<TFunction>) => i18n.t(...args)) as TFunction

describe('sensor field labels', () => {
  beforeEach(async () => {
    await changeLanguage('de')
  })

  it('finds the shipped sensors at all', () => {
    expect(shippedSensors().map((entry) => entry.sensor)).toContain('wlan-auth')
  })

  it.each(['de', 'en'])('has a label, help and choice names for every field in %s', (lng) => {
    const gaps: string[] = []
    for (const { sensor, fields } of shippedSensors()) {
      for (const field of fields) {
        const base = `sensorFields.${sensor}.${fieldKey(field.name)}`
        for (const leaf of ['label', 'hint']) {
          if (!has(lng, `${base}.${leaf}`)) {
            gaps.push(`${base}.${leaf}`)
          }
        }
        for (const choice of field.choices ?? []) {
          if (!has(lng, `${base}.choices.${choice}`)) {
            gaps.push(`${base}.choices.${choice}`)
          }
        }
      }
    }
    expect(gaps).toEqual([])
  })

  it('folds a parameter and its profile key into one entry', () => {
    expect(fieldKey('--domain-suffix-match')).toBe('domain_suffix_match')
    expect(fieldKey('DOMAIN_SUFFIX_MATCH')).toBe('domain_suffix_match')
  })

  it('resolves label, help and choice per language', async () => {
    const auth = { name: 'AUTH', description: 'The authentication method.' }
    expect(fieldLabel(t, auth, 'wlan-auth')).toBe('Anmeldeverfahren')
    expect(fieldDescription(t, auth, 'wlan-auth')).not.toBe(auth.description)
    expect(choiceLabel(t, auth, 'peap', 'wlan-auth')).toBe('PEAP/MSCHAPv2')

    await changeLanguage('en')
    expect(fieldLabel(t, { name: '--ssid' }, 'wlan-auth')).toBe('Network name')
  })

  it('falls back to the field name and the schema text for an unknown field', () => {
    const field = { name: '--brand-new', description: 'Plain English from the schema.' }
    expect(fieldLabel(t, field, 'wlan-auth')).toBe('--brand-new')
    expect(fieldDescription(t, field, 'wlan-auth')).toBe('Plain English from the schema.')
    expect(choiceLabel(t, field, 'fast', 'wlan-auth')).toBe('fast')
  })
})
