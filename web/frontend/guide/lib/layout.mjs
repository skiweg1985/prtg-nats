/**
 * The frame around every page: head, header with the language switch, the
 * article list beside the content, and the footer.
 *
 * Nothing here may load from anywhere else or run inline: the proxy serves the
 * guide under the same Content-Security-Policy as the interface, which allows
 * scripts and fonts from this origin only.
 */

import { BASE, L, LANGS, articleUrl, escapeHtml, homeUrl, label } from './text.mjs'

const T = {
  guide: L('Anleitung', 'Guide'),
  toApp: L('Zur Oberfläche', 'Open the interface'),
  language: L('Sprache', 'Language'),
  contents: L('Inhalt', 'Contents'),
  allArticles: L('Alle Artikel', 'All articles'),
  skip: L('Zum Inhalt springen', 'Skip to content'),
  footer: L(
    'Diese Anleitung wird mit der Installation ausgeliefert und beschreibt den Stand dieses Servers.',
    'This guide ships with the installation and describes the version running on this server.',
  ),
  illustrations: L('Die Illustrationen sind mit KI erzeugt.', 'The illustrations are AI-generated.'),
}

/** Roles in the order the interface lists them; each includes the ones before it. */
export const ROLES = ['viewer', 'operator', 'administrator']

const EVERYONE = L('Alle Rollen', 'All roles')

/**
 * The roles an article is for: the lowest one it needs and every role above.
 * Three badges on every article anyone may read would only be noise.
 */
export function roleNames(role, lang) {
  if (role === 'viewer') return [EVERYONE[lang]]
  return ROLES.slice(ROLES.indexOf(role)).map((name) => label(`roles.${name}`, lang))
}

export function roleBadges(role, lang) {
  if (!ROLES.includes(role)) throw new Error(`unknown role ${role}`)
  return roleNames(role, lang)
    .map((name) => `<span class="badge">${escapeHtml(name)}</span>`)
    .join('')
}

function sidebar(groups, lang, currentSlug) {
  return groups
    .map((group) => {
      const items = group.articles
        .map((article) => {
          const current = article.slug === currentSlug ? ' aria-current="page"' : ''
          return `<li><a href="${articleUrl(article.slug, lang)}"${current}>${escapeHtml(article.title[lang])}</a></li>`
        })
        .join('')
      return `<p class="nav-group">${escapeHtml(group.title[lang])}</p><ul>${items}</ul>`
    })
    .join('')
}

/**
 * A complete page.
 *
 * `alternates` maps each language to the address of the same page in it, so
 * the switch keeps the reader where they are.
 */
export function page({ lang, title, description, body, groups, slug = null, alternates }) {
  const switcher = LANGS.map((other) => {
    const current = other === lang ? ' aria-current="true"' : ''
    return `<a href="${alternates[other]}" hreflang="${other}" data-lang="${other}"${current}>${other.toUpperCase()}</a>`
  }).join('')
  const links = LANGS.map(
    (other) => `<link rel="alternate" hreflang="${other}" href="${alternates[other]}">`,
  ).join('')
  const nav = sidebar(groups, lang, slug)
  const fullTitle = slug ? `${title} · ${T.guide[lang]} · PRTG-NATS` : `${T.guide[lang]} · PRTG-NATS`

  return `<!doctype html>
<html lang="${lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(fullTitle)}</title>
<meta name="description" content="${escapeHtml(description)}">
<link rel="icon" href="${BASE}/assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="${BASE}/assets/tokens.css">
<link rel="stylesheet" href="${BASE}/assets/guide.css">
${links}
<script src="${BASE}/assets/theme.js"></script>
<script src="${BASE}/assets/guide.js" defer></script>
</head>
<body>
<a class="skip" href="#content">${T.skip[lang]}</a>
<header class="top">
  <div class="top-inner">
    <a class="brand" href="${homeUrl(lang)}">PRTG-NATS <span class="brand-sub">${T.guide[lang]}</span></a>
    <nav class="top-actions" aria-label="${T.language[lang]}">
      <span class="lang">${switcher}</span>
      <a class="to-app" href="/">${T.toApp[lang]}</a>
    </nav>
  </div>
</header>
<div class="shell">
  <nav class="side" aria-label="${T.contents[lang]}">${nav}</nav>
  <main id="content">
    <details class="side-mobile"><summary>${T.allArticles[lang]}</summary><nav aria-label="${T.contents[lang]}">${nav}</nav></details>
    ${body}
  </main>
</div>
<footer class="foot"><p>${T.footer[lang]} ${T.illustrations[lang]}</p></footer>
</body>
</html>
`
}
