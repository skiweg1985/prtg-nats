import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'

import de from './locales/de.json'
import en from './locales/en.json'

/**
 * English is the source language and the fallback. German is a full
 * translation, not a partial one - a CI check compares the two key sets and
 * fails on any difference in either direction.
 *
 * The backend never sends prose. It sends a `message_key` and `params`, which
 * are resolved here. That is what makes the whole platform translatable rather
 * than only its static labels.
 */
export const SUPPORTED_LANGUAGES = ['en', 'de'] as const
export type Language = (typeof SUPPORTED_LANGUAGES)[number]

export const LANGUAGE_LABELS: Record<Language, string> = {
  en: 'English',
  de: 'Deutsch',
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      de: { translation: de },
    },
    fallbackLng: 'en',
    supportedLngs: SUPPORTED_LANGUAGES,
    // A missing German key falls back to English rather than showing the key
    // itself: an untranslated sentence is still usable, a raw key is not.
    nonExplicitSupportedLngs: true,
    interpolation: {
      // React escapes for us.
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'prtg-nats-language',
      caches: ['localStorage'],
    },
    returnNull: false,
  })

export function changeLanguage(language: Language): void {
  void i18n.changeLanguage(language)
  document.documentElement.lang = language
}

export function currentLanguage(): Language {
  const resolved = i18n.resolvedLanguage ?? 'en'
  return (SUPPORTED_LANGUAGES as readonly string[]).includes(resolved)
    ? (resolved as Language)
    : 'en'
}

export default i18n
