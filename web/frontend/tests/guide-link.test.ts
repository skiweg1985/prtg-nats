import { describe, expect, it } from 'vitest'

import { guideHomeHref, guideHref } from '@/utils/guide'

describe('the link to the guide', () => {
  it('hands the current page and language to the context page', () => {
    expect(guideHref('/probes/new', 'de')).toBe('/docs/context/?path=%2Fprobes%2Fnew&lang=de')
    expect(guideHref('/probes/new', 'en-US')).toBe(
      '/docs/en/context/?path=%2Fprobes%2Fnew&lang=en',
    )
  })

  it('falls back to German, the language the guide opens in', () => {
    expect(guideHref('/', undefined)).toBe('/docs/context/?path=%2F&lang=de')
    expect(guideHomeHref('fr')).toBe('/docs/')
    expect(guideHomeHref('en')).toBe('/docs/en/')
  })
})
