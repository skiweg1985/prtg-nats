/**
 * The building blocks every article is written with.
 *
 * A text is always both languages at once, so an article cannot be finished
 * in one and forgotten in the other. Inside a text, two markers are resolved
 * per language when the page is rendered:
 *
 *   {ui:nav.probes}        the label as the interface shows it, read from
 *                          src/i18n/locales - a renamed button renames itself
 *                          here too, and a removed key breaks the build
 *   href="@enroll-a-probe" a link to another article, by its slug
 */

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const LANGS = ['de', 'en']
export const BASE = '/docs'

const here = dirname(fileURLToPath(import.meta.url))
const localesDir = join(here, '..', '..', 'src', 'i18n', 'locales')

/** Flattened like scripts/check-translations.mjs, because some keys carry dots. */
function flatten(value, prefix = '', out = new Map()) {
  for (const [key, entry] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (entry && typeof entry === 'object' && !Array.isArray(entry)) flatten(entry, path, out)
    else out.set(path, String(entry))
  }
  return out
}

const locales = Object.fromEntries(
  LANGS.map((lang) => [
    lang,
    flatten(JSON.parse(readFileSync(join(localesDir, `${lang}.json`), 'utf8'))),
  ]),
)

/** A text in both languages. */
export function L(de, en) {
  if (typeof de !== 'string' || typeof en !== 'string') {
    throw new Error(`L() needs two strings, got ${JSON.stringify([de, en])}`)
  }
  return { de, en }
}

/** The interface label behind a translation key, in one language. */
export function label(key, lang) {
  const value = locales[lang].get(key)
  if (value === undefined) throw new Error(`unknown interface label ${key} (${lang})`)
  // Plural and interpolation forms are not labels anyone clicks.
  if (value.includes('{{')) throw new Error(`interface label ${key} has a placeholder`)
  return value
}

const QUOTES = { de: ['„', '“'], en: ['“', '”'] }

/** How a button, tab or field name is set in running text. */
export function uiName(name, lang) {
  const [open, close] = QUOTES[lang]
  return `<b class="ui">${open}${name}${close}</b>`
}

/**
 * Resolve the markers in a text for one language. Links are checked against
 * the known slugs so a renamed article cannot leave a dead link behind.
 */
export function resolve(text, lang, slugs) {
  return text
    .replace(/\{ui:([\w.-]+)\}/g, (_, key) => uiName(label(key, lang), lang))
    .replace(/href="@([\w-]+)(#[\w-]+)?"/g, (_, slug, anchor = '') => {
      if (!slugs.has(slug)) throw new Error(`link to unknown article @${slug}`)
      return `href="${articleUrl(slug, lang)}${anchor}"`
    })
}

export function homeUrl(lang) {
  return lang === 'de' ? `${BASE}/` : `${BASE}/${lang}/`
}

export function articleUrl(slug, lang) {
  return `${homeUrl(lang)}${slug}/`
}

export function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

/** Markup out of a text, for alt text, titles and the search field. */
export function plain(html) {
  return html
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}
