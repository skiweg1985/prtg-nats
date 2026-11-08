// Three small jobs, none of which the guide needs to be readable without
// JavaScript: filtering the overview, keeping the language in step with the
// interface, and sending a link from the interface to the matching article.

const BASE = '/docs'
const LANGUAGE_KEY = 'prtg-nats-language'

function storedLanguage() {
  try {
    return localStorage.getItem(LANGUAGE_KEY)
  } catch {
    return null
  }
}

// The switch remembers the choice where the interface keeps it, so the next
// visit to either opens in the same language.
document.querySelectorAll('.lang a[data-lang]').forEach((link) => {
  link.addEventListener('click', () => {
    try {
      localStorage.setItem(LANGUAGE_KEY, link.dataset.lang)
    } catch {
      // Storage blocked: the link still works.
    }
  })
})

// Somebody who types /docs/ and reads the interface in English lands on the
// English overview.
if (window.location.pathname === `${BASE}/` && storedLanguage()?.startsWith('en')) {
  window.location.replace(`${BASE}/en/`)
}

const search = document.getElementById('search')
if (search) {
  const cards = [...document.querySelectorAll('.cards li[data-search]')]
  const groups = [...document.querySelectorAll('.group')]
  const empty = document.querySelector('.no-results')
  search.addEventListener('input', () => {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean)
    let shown = 0
    for (const card of cards) {
      const match = words.every((word) => card.dataset.search.includes(word))
      card.hidden = !match
      if (match) shown += 1
    }
    for (const group of groups) {
      group.hidden = !group.querySelector('.cards li:not([hidden])')
    }
    if (empty) empty.hidden = shown > 0
  })
}

// /docs/context/?path=/probes/new&lang=en - the interface's "Guide" link.
if (document.querySelector('[data-context]')) {
  const params = new URLSearchParams(window.location.search)
  const path = params.get('path') || '/'
  const lang = params.get('lang')?.startsWith('en') ? 'en' : 'de'
  const home = lang === 'de' ? `${BASE}/` : `${BASE}/${lang}/`
  fetch(`${BASE}/context.json`)
    .then((response) => (response.ok ? response.json() : []))
    .then((entries) => {
      // Sorted longest first by the build; a path matches itself and
      // everything below it, never a sibling that merely starts the same.
      // "/probes/*" stands for every page below /probes, not /probes itself.
      const hit = entries.find((entry) => {
        if (entry.path.endsWith('/*')) return path.startsWith(entry.path.slice(0, -1))
        return path === entry.path || (entry.path !== '/' && path.startsWith(`${entry.path}/`))
      })
      window.location.replace(hit ? `${home}${hit.slug}/` : home)
    })
    .catch(() => window.location.replace(home))
}
