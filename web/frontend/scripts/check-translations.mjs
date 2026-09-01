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
 *  3. an error code the backend can emit for which no message exists;
 *  4. a key no line of code asks for, which is the direction comparing the
 *     two languages against each other can never catch: a string translated
 *     twice and used nowhere reads as a feature that exists.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const localesDir = join(here, '..', 'src', 'i18n', 'locales')
const sourceDir = join(here, '..', 'src')
const backendErrors = join(here, '..', '..', 'backend', 'app', 'core', 'errors.py')

/**
 * Key spaces the backend fills in, one code at a time.
 *
 * The interface never writes these out: it is handed a code and looks it up,
 * so no line of source mentions the individual key. Their completeness is
 * checked from the other side - by the error-code check below, and by
 * tests/check-job-messages.py for the job log.
 */
const SERVED_BY_THE_BACKEND = [
  'errors.',
  'jobs.events.',
  'jobs.steps.',
  'deviations.',
  'roles.',
  'audit.actions.',
  // A reconciliation plan names its own actions and risks, and a blocked job
  // names its own reason - both arrive as keys inside the payload.
  'plan.',
  'jobs.blocked.',
]

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

// --- 4. Keys nothing asks for --------------------------------------------

function sourceFiles(directory) {
  const out = []
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) out.push(...sourceFiles(path))
    else if (/\.tsx?$/.test(entry)) out.push(path)
  }
  return out
}

const code = sourceFiles(sourceDir)
  .map((path) => readFileSync(path, 'utf8'))
  .join('\n')

// Every string literal in the source, quoted any of the three ways. A key
// reaches t() as a literal, through a `labelKey: 'nav.probes'` entry in a
// table, or through i18n.exists() - all of them are literals here.
const literals = new Set(
  [...code.matchAll(/['"`]([\w.]+)['"`]/g)].map((match) => match[1]),
)

// And the prefixes of the ones built from a code: t(`jobs.filters.${entry}`).
const prefixes = [
  ...SERVED_BY_THE_BACKEND,
  ...[...code.matchAll(/[`'"]([\w.]*\.)\$\{/g)].map((match) => match[1]),
]

function isUsed(key) {
  // i18next picks the plural form itself, so the base key standing in the
  // source keeps every suffix of it alive.
  const base = key.replace(/_(one|other|zero|few|many)$/, '')
  if (literals.has(key) || literals.has(base)) return true
  // An error message can carry .cause and .action beside it; ErrorDetails
  // looks those up by building them from the code.
  const parent = base.replace(/\.(cause|action)$/, '')
  if (parent !== base && literals.has(parent)) return true
  return prefixes.some((prefix) => key.startsWith(prefix))
}

/**
 * Translated ahead of the screen that will use it.
 *
 * Each of these is a server-side flow whose interface does not exist yet, so
 * deleting the strings would only mean writing them again. Anything not on
 * this list and not asked for is a leftover: translate what exists.
 */
const AHEAD_OF_THE_INTERFACE = new Set([
  // POST /auth/change-password is implemented and reachable, and UsersCard
  // can set must_change_password on an account - but nothing in the interface
  // lets that account change it.
  'auth.currentPassword',
  'auth.newPassword',
  'auth.changePassword',
  'auth.mustChangePassword',
  // PATCH /probes/{id} takes both, and useUpdateProbe calls it. The list
  // shows display_name as the title of every row with no way to set it.
  'probes.displayName',
  'probes.notes',
])

// One term per concept, per language - docs/web/terminology.md is the
// glossary. A banned word creeping back in is cheaper to catch here than in
// a review, because the reviewer sees one string and the user sees all of
// them. Checked as substrings of the VALUES, per language; a key listed in
// the exceptions may keep the word (quoting a CLI flag, naming PRTG's UI).
const FORBIDDEN_TERMS = {
  de: [
    ['Kennwort', []],
    ['Anmeldedaten', []],
    ['Gegenstelle', []],
    ['enrollier', []],
    ['Ausrollvorgang', []],
    [/\bSonde\b/, []],
  ],
  en: [
    [/\benrol(?!l)/, []],
    ['counterpart', []],
  ],
}

for (const [language, rules] of Object.entries(FORBIDDEN_TERMS)) {
  const flat = tables.get(language)
  if (!flat) continue
  for (const [needle, exceptions] of rules) {
    for (const [key, value] of flat) {
      if (exceptions.includes(key)) continue
      const hit =
        needle instanceof RegExp ? needle.test(value) : value.includes(needle)
      if (hit) {
        problems.push(
          `${language}: "${key}" uses the retired term ${needle} - see docs/web/terminology.md`,
        )
      }
    }
  }
}

const unused = [...source.keys()].filter(
  (key) => !isUsed(key) && !AHEAD_OF_THE_INTERFACE.has(key),
)
for (const key of unused) {
  problems.push(`${key} is translated but nothing asks for it`)
}

if (problems.length > 0) {
  console.error(`translation check failed with ${problems.length} problem(s):`)
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}

console.log(
  `translations are consistent: ${source.size} keys across ${languages.join(', ')}`,
)
