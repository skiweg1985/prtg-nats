#!/usr/bin/env node
/**
 * Compare the translation files against each other and against the backend.
 *
 * Three failures this catches, all of which reach a user as an empty label or
 * a raw key on screen:
 *
 *  1. a key that exists in one language and not the other;
 *  2. an interpolation placeholder that differs between the two, so one
 *     language renders "{{probe}}" literally;
 *  3. an error code the backend can emit for which no message exists.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const localesDir = join(here, '..', 'src', 'i18n', 'locales')
const backendErrors = join(here, '..', '..', 'backend', 'app', 'core', 'errors.py')

const SOURCE = 'en'

function flatten(value, prefix = '') {
  const out = new Map()
  for (const [key, entry] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
      for (const [nested, nestedValue] of flatten(entry, path)) out.set(nested, nestedValue)
    } else {
      out.set(path, String(entry))
    }
  }
  return out
}

function placeholders(text) {
  return new Set([...text.matchAll(/\{\{(\w+)\}\}/g)].map((match) => match[1]))
}

const languages = readdirSync(localesDir)
  .filter((name) => name.endsWith('.json'))
  .map((name) => name.replace(/\.json$/, ''))

const tables = new Map(
  languages.map((language) => [
    language,
    flatten(JSON.parse(readFileSync(join(localesDir, `${language}.json`), 'utf8'))),
  ]),
)

const problems = []
const source = tables.get(SOURCE)
if (!source) {
  console.error(`the source language ${SOURCE} has no file`)
  process.exit(1)
}

for (const [language, table] of tables) {
  if (language === SOURCE) continue

  for (const key of source.keys()) {
    if (!table.has(key)) problems.push(`${language}: missing key ${key}`)
  }
  for (const key of table.keys()) {
    // i18next plural suffixes exist per language, so a key the source does not
    // carry is only a problem when it is not one of those.
    if (!source.has(key) && !/_(one|other|zero|few|many)$/.test(key)) {
      problems.push(`${language}: key ${key} does not exist in ${SOURCE}`)
    }
  }
  for (const [key, text] of table) {
    const expected = source.get(key)
    if (expected === undefined) continue
    const left = placeholders(expected)
    const right = placeholders(text)
    for (const name of left) {
      if (!right.has(name)) problems.push(`${language}: ${key} is missing {{${name}}}`)
    }
    for (const name of right) {
      if (!left.has(name)) problems.push(`${language}: ${key} has an extra {{${name}}}`)
    }
  }
}

// Every error code the backend defines needs a message on this side, or the
// interface shows a raw code the moment that error happens for real.
try {
  const python = readFileSync(backendErrors, 'utf8')
  const codes = [...python.matchAll(/^\s{4}code\s*=\s*"([^"]+)"/gm)].map((match) => match[1])
  for (const code of codes) {
    for (const [language, table] of tables) {
      if (!table.has(`errors.${code}`)) {
        problems.push(`${language}: no message for backend error code ${code}`)
      }
    }
  }
} catch {
  console.warn('backend errors.py not readable; skipped the error-code check')
}

if (problems.length > 0) {
  console.error(`translation check failed with ${problems.length} problem(s):`)
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}

console.log(
  `translations are consistent: ${source.size} keys across ${languages.join(', ')}`,
)
