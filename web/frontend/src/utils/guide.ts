/**
 * Where the guide opens from the interface.
 *
 * The guide is static pages under /docs, built from web/frontend/guide. Which
 * article belongs to which page is decided there (each article's appPaths),
 * not here: this only hands over where the reader is and in which language,
 * and /docs/context/ picks the article. A new page of the interface therefore
 * needs an entry in guide/articles, not a change in this file.
 */
export function guideHref(pathname: string, language: string | undefined): string {
  const lang = guideLanguage(language)
  const params = new URLSearchParams({ path: pathname, lang })
  return `${guideHomeHref(lang)}context/?${params.toString()}`
}

/** The guide's overview, for places that belong to no single article. */
export function guideHomeHref(language: string | undefined): string {
  const lang = guideLanguage(language)
  return lang === 'de' ? '/docs/' : `/docs/${lang}/`
}

function guideLanguage(language: string | undefined): 'de' | 'en' {
  return language?.startsWith('en') ? 'en' : 'de'
}
