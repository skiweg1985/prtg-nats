#!/usr/bin/env node
/**
 * Build the guide served at /docs.
 *
 *   node guide/build.mjs            pages into dist/docs, a missing image becomes a placeholder
 *   node guide/build.mjs --check    the same, but a missing image fails the build
 *   node guide/build.mjs --out DIR  somewhere else
 *
 * `npm run build` runs it with --check after Vite, so the guide is part of the
 * same image as the interface and ships with every installation and update.
 * The generated pages are not kept in the repository; only their sources are.
 */

import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve as resolvePath } from 'node:path'
import { fileURLToPath } from 'node:url'

import { groups, home } from './articles/index.mjs'
import { page, roleBadges } from './lib/layout.mjs'
import { BASE, L, LANGS, articleUrl, escapeHtml, homeUrl, plain, resolve } from './lib/text.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const frontend = join(here, '..')

const args = process.argv.slice(2)
const strict = args.includes('--check')
const outArg = args.indexOf('--out')
const out = resolvePath(outArg >= 0 ? args[outArg + 1] : join(frontend, 'dist', 'docs'))

const T = {
  for: L('Für', 'For'),
  notes: L('Gut zu wissen', 'Good to know'),
  related: L('Siehe auch', 'See also'),
  previous: L('Zurück', 'Previous'),
  next: L('Weiter', 'Next'),
  search: L('Artikel suchen', 'Search articles'),
  searchPlaceholder: L('Wonach suchst du?', 'What are you looking for?'),
  noResults: L('Kein Artikel passt zu dieser Suche.', 'No article matches this search.'),
  redirecting: L('Der passende Artikel wird geöffnet …', 'Opening the matching article …'),
  overview: L('Zur Übersicht', 'Go to the overview'),
}

const problems = []
const missingImages = []

// --- Validate the articles before writing anything ---------------------------

const articles = groups.flatMap((group) => group.articles.map((article) => ({ ...article, group })))
const slugs = new Set()
for (const article of articles) {
  if (!/^[a-z0-9-]+$/.test(article.slug)) problems.push(`slug ${article.slug} is not lower-case-with-dashes`)
  if (slugs.has(article.slug)) problems.push(`slug ${article.slug} is used twice`)
  slugs.add(article.slug)
  for (const field of ['title', 'short', 'lead']) {
    if (!article[field]) problems.push(`${article.slug} has no ${field}`)
  }
  for (const path of article.appPaths ?? []) {
    if (!path.startsWith('/')) problems.push(`${article.slug}: app path ${path} must start with /`)
  }
  for (const slug of article.related ?? []) {
    if (!slugs.has(slug) && !articles.some((other) => other.slug === slug)) {
      problems.push(`${article.slug}: related article ${slug} does not exist`)
    }
  }
}
const claimed = new Map()
for (const article of articles) {
  for (const path of article.appPaths ?? []) {
    if (claimed.has(path)) problems.push(`app path ${path} belongs to ${claimed.get(path)} and ${article.slug}`)
    claimed.set(path, article.slug)
  }
}
// Every page of the interface opens an article from its "Guide" link, and every
// app path names a page that exists. Read from the router itself, so a new
// page without an article fails here rather than opening the overview.
const routes = interfaceRoutes(readFileSync(join(frontend, 'src', 'app', 'router.tsx'), 'utf8'))
const covers = (claim, route) => {
  if (claim.endsWith('/*')) return route.startsWith(claim.slice(0, -1))
  return route === claim || (claim !== '/' && route.startsWith(`${claim}/`))
}
for (const claim of claimed.keys()) {
  const bare = claim.endsWith('/*') ? claim.slice(0, -2) : claim
  if (!routes.some((route) => route === bare || route.startsWith(`${bare}/`))) {
    problems.push(`app path ${claim} matches no page of the interface (src/app/router.tsx)`)
  }
}
for (const route of routes) {
  if (![...claimed.keys()].some((claim) => covers(claim, route))) {
    problems.push(`the interface page ${route} has no article - add it to an article's appPaths`)
  }
}
if (problems.length) fail(problems)

/**
 * The routes of the interface as full paths. Redirects and the catch-all are
 * left out: nobody reads a page there.
 */
function interfaceRoutes(source) {
  const found = []
  const parents = []
  let at = 0
  while (true) {
    const open = source.indexOf('<Route', at)
    const close = source.indexOf('</Route>', at)
    if (open < 0 && close < 0) break
    if (close >= 0 && (open < 0 || close < open)) {
      parents.pop()
      at = close + 8
      continue
    }
    // The tag ends at the first ">" outside braces: element={<Page />} holds
    // one of its own.
    let depth = 0
    let end = open
    for (; end < source.length; end += 1) {
      const char = source[end]
      if (char === '{') depth += 1
      else if (char === '}') depth -= 1
      else if (char === '>' && depth === 0) break
    }
    const tag = source.slice(open, end + 1)
    at = end + 1
    const selfClosing = tag.endsWith('/>')
    const path = tag.match(/\spath="([^"]*)"/)?.[1]
    const isPage = /\selement=/.test(tag) && !tag.includes('<Navigate') && path !== '*'
    const full = `/${[...parents, path ?? ''].filter(Boolean).join('/')}`
    if (isPage) found.push(full.length > 1 ? full.replace(/\/$/, '') : full)
    if (!selfClosing) parents.push(path ?? '')
  }
  return [...new Set(found)]
}

// --- Rendering -----------------------------------------------------------------

function text(value, lang) {
  return resolve(value[lang], lang, slugs)
}

function image(name, lang) {
  for (const candidate of [lang, 'de']) {
    if (existsSync(join(here, 'images', candidate, `${name}.webp`))) {
      return `${BASE}/images/${candidate}/${name}.webp`
    }
  }
  missingImages.push(`images/de/${name}.webp`)
  return null
}

function figure({ image: name, alt, caption, narrow }, lang) {
  const src = image(name, lang)
  const cls = narrow ? 'shot narrow' : 'shot'
  const cap = caption ? `<figcaption>${text(caption, lang)}</figcaption>` : ''
  if (!src) {
    return `<figure class="${cls} missing"><div class="placeholder">${escapeHtml(name)}</div>${cap}</figure>`
  }
  const altText = alt ? escapeHtml(plain(text(alt, lang))) : ''
  return `<figure class="${cls}"><img src="${src}" alt="${altText}" loading="lazy" decoding="async">${cap}</figure>`
}

function illustration(name, alt, lang, cls) {
  const file = join(here, 'static', 'illustrations', `${name}.webp`)
  if (!existsSync(file)) {
    missingImages.push(`static/illustrations/${name}.webp`)
    return ''
  }
  return `<img class="${cls}" src="${BASE}/illustrations/${name}.webp" alt="${escapeHtml(plain(text(alt, lang)))}" loading="lazy" decoding="async">`
}

function renderArticle(article, lang, index) {
  const parts = []
  parts.push(
    `<p class="crumbs"><a href="${homeUrl(lang)}">${escapeHtml(home.title[lang])}</a> <span aria-hidden="true">›</span> ${escapeHtml(article.group.title[lang])}</p>`,
  )
  parts.push(`<h1>${escapeHtml(article.title[lang])}</h1>`)
  parts.push(`<p class="lead">${text(article.lead, lang)}</p>`)
  parts.push(
    `<p class="roles"><span class="roles-label">${T.for[lang]}</span>${roleBadges(article.role, lang)}</p>`,
  )
  if (article.illustration) {
    parts.push(
      `<figure class="illustration">${illustration(article.illustration, article.illustrationAlt, lang, 'illustration-img')}</figure>`,
    )
  }
  if (article.body) parts.push(`<div class="prose">${text(article.body, lang)}</div>`)

  if (article.steps?.length) {
    const tag = article.unnumbered ? 'ul' : 'ol'
    const items = article.steps
      .map((step) => {
        const heading = step.title ? `<h2>${escapeHtml(plain(text(step.title, lang)))}</h2>` : ''
        const shot = step.image ? figure(step, lang) : ''
        return `<li>${heading}<div class="prose">${text(step.text, lang)}</div>${shot}</li>`
      })
      .join('')
    parts.push(`<${tag} class="steps${article.unnumbered ? ' unnumbered' : ''}">${items}</${tag}>`)
  }

  if (article.after) parts.push(`<div class="prose">${text(article.after, lang)}</div>`)

  if (article.notes?.length) {
    const items = article.notes.map((note) => `<li>${text(note, lang)}</li>`).join('')
    parts.push(`<aside class="notes"><h2>${T.notes[lang]}</h2><ul>${items}</ul></aside>`)
  }

  if (article.related?.length) {
    const items = article.related
      .map((slug) => {
        const other = articles.find((candidate) => candidate.slug === slug)
        return `<li><a href="${articleUrl(slug, lang)}">${escapeHtml(other.title[lang])}</a> <span>${text(other.short, lang)}</span></li>`
      })
      .join('')
    parts.push(`<section class="related"><h2>${T.related[lang]}</h2><ul>${items}</ul></section>`)
  }

  const previous = articles[index - 1]
  const next = articles[index + 1]
  const pager = [
    previous
      ? `<a class="prev" href="${articleUrl(previous.slug, lang)}"><span>${T.previous[lang]}</span>${escapeHtml(previous.title[lang])}</a>`
      : '<span></span>',
    next
      ? `<a class="next" href="${articleUrl(next.slug, lang)}"><span>${T.next[lang]}</span>${escapeHtml(next.title[lang])}</a>`
      : '<span></span>',
  ].join('')
  parts.push(`<nav class="pager">${pager}</nav>`)

  return `<article class="article">${parts.join('\n')}</article>`
}

function renderHome(lang) {
  const sections = groups
    .map((group) => {
      const cards = group.articles
        .map((article) => {
          const search = [article.title[lang], plain(text(article.short, lang)), article.keywords?.[lang] ?? '']
            .join(' ')
            .toLowerCase()
          return `<li data-search="${escapeHtml(search)}"><a href="${articleUrl(article.slug, lang)}"><span class="card-title">${escapeHtml(article.title[lang])}</span><span class="card-short">${text(article.short, lang)}</span><span class="card-roles">${roleBadges(article.role, lang)}</span></a></li>`
        })
        .join('')
      const art = group.illustration
        ? illustration(group.illustration, group.illustrationAlt, lang, 'group-img')
        : ''
      return `<section class="group" id="${group.id}"><div class="group-head">${art}<div><h2>${escapeHtml(group.title[lang])}</h2><p>${text(group.who, lang)}</p></div></div><ul class="cards">${cards}</ul></section>`
    })
    .join('\n')

  return `<section class="hero">
<h1>${escapeHtml(home.heading[lang])}</h1>
<div class="prose">${text(home.intro, lang)}</div>
<label class="search"><span class="sr-only">${T.search[lang]}</span><input type="search" id="search" placeholder="${T.searchPlaceholder[lang]}" autocomplete="off"></label>
</section>
${sections}
<p class="no-results" hidden>${T.noResults[lang]}</p>`
}

function renderContext(lang) {
  return `<section class="hero redirect" data-context>
<p>${T.redirecting[lang]}</p>
<p><a href="${homeUrl(lang)}">${T.overview[lang]}</a></p>
</section>`
}

function write(path, content) {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, content)
}

function pagePath(lang, slug) {
  const base = lang === 'de' ? out : join(out, lang)
  return slug ? join(base, slug, 'index.html') : join(base, 'index.html')
}

// --- Write ---------------------------------------------------------------------

rmSync(out, { recursive: true, force: true })

for (const lang of LANGS) {
  const homeAlternates = Object.fromEntries(LANGS.map((other) => [other, homeUrl(other)]))
  write(
    pagePath(lang),
    page({
      lang,
      title: home.title[lang],
      description: plain(text(home.intro, lang)),
      body: renderHome(lang),
      groups,
      alternates: homeAlternates,
    }),
  )

  articles.forEach((article, index) => {
    write(
      pagePath(lang, article.slug),
      page({
        lang,
        title: article.title[lang],
        description: plain(text(article.short, lang)),
        body: renderArticle(article, lang, index),
        groups,
        slug: article.slug,
        alternates: Object.fromEntries(LANGS.map((other) => [other, articleUrl(article.slug, other)])),
      }),
    )
  })

  // The interface links here with its own path and language; guide.js picks
  // the article from context.json and replaces the address.
  write(
    pagePath(lang, 'context'),
    page({
      lang,
      title: home.title[lang],
      description: plain(text(home.intro, lang)),
      body: renderContext(lang),
      groups,
      alternates: Object.fromEntries(LANGS.map((other) => [other, `${homeUrl(other)}context/`])),
    }),
  )
}

// Longest path first, so /probes/new wins over /probes.
const context = [...claimed.entries()]
  .map(([path, slug]) => ({ path, slug }))
  .sort((a, b) => b.path.length - a.path.length)
write(join(out, 'context.json'), `${JSON.stringify(context, null, 2)}\n`)

cpSync(join(here, 'static'), join(out, 'assets'), {
  recursive: true,
  filter: (source) => !source.includes(`${join('static', 'illustrations')}`),
})
cpSync(join(frontend, 'src', 'styles', 'tokens.css'), join(out, 'assets', 'tokens.css'))
if (existsSync(join(here, 'static', 'illustrations'))) {
  cpSync(join(here, 'static', 'illustrations'), join(out, 'illustrations'), { recursive: true })
}
if (existsSync(join(here, 'images'))) {
  cpSync(join(here, 'images'), join(out, 'images'), { recursive: true })
}

// The interface's own typefaces, from the packages it already depends on.
const fonts = {
  'inter.woff2': '@fontsource-variable/inter/files/inter-latin-wght-normal.woff2',
  'space-grotesk.woff2': '@fontsource-variable/space-grotesk/files/space-grotesk-latin-wght-normal.woff2',
  'jetbrains-mono.woff2': '@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2',
}
for (const [name, source] of Object.entries(fonts)) {
  const from = join(frontend, 'node_modules', source)
  if (!existsSync(from)) fail([`font ${source} is missing - run npm ci first`])
  cpSync(from, join(out, 'assets', 'fonts', name))
}

const unique = [...new Set(missingImages)].sort()
if (unique.length) {
  const message = `${unique.length} image(s) missing:\n  ${unique.join('\n  ')}`
  if (strict) fail([message])
  console.warn(`guide: ${message}`)
}

const pages = readdirSync(out, { recursive: true }).filter((file) => String(file).endsWith('.html')).length
console.log(`guide: ${articles.length} articles, ${pages} pages in ${out}`)

function fail(messages) {
  console.error(`guide: build failed\n  ${messages.join('\n  ')}`)
  process.exit(1)
}
