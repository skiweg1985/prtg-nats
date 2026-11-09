import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'

/**
 * Page state that lives in the address.
 *
 * Everything a user sets on a page - a tab, a filter, a search term, a sort
 * order, an expanded section - belongs in the URL, so a reload restores the
 * view and a copied link shows a colleague the same thing. Every page used to
 * hand-roll the same few lines around useSearchParams, and each copy had its
 * own idea of what an empty value or a stale one meant.
 *
 * Two rules hold for every write:
 *
 * - A value equal to its default leaves the address. `?q=` or `?tab=overview`
 *   would ride along into every link and say nothing.
 * - Writes replace the history entry instead of pushing one, so Back leaves
 *   the page rather than stepping through every keystroke and every click on
 *   a filter.
 */

/** One change per key: a value, several values, or null to remove it. */
export type ParamPatch = Record<string, string | readonly string[] | null | undefined>

/** The current parameters with the patch applied; the input is not touched. */
export function patchParams(current: URLSearchParams, patch: ParamPatch): URLSearchParams {
  const next = new URLSearchParams(current)
  for (const [key, value] of Object.entries(patch)) {
    next.delete(key)
    if (value === null || value === undefined) continue
    if (typeof value === 'string') {
      if (value !== '') next.set(key, value)
      continue
    }
    for (const entry of value) if (entry !== '') next.append(key, entry)
  }
  return next
}

/** The value if it is one of the allowed ones, otherwise the fallback. */
export function oneOf<T extends string>(
  value: string | null,
  allowed: readonly T[],
  fallback: T,
): T {
  return allowed.find((entry) => entry === value) ?? fallback
}

/** A positive page number from the address; anything else is page one. */
export function pageNumber(value: string | null): number {
  const parsed = Number.parseInt(value ?? '', 10)
  return Number.isFinite(parsed) && parsed > 1 ? parsed : 1
}

export interface UrlWriteOptions {
  /** Push a history entry instead of replacing the current one. */
  push?: boolean
}

/**
 * The parameters and a way to change several of them at once.
 *
 * The router's location state is passed on with every write: it is where
 * the way a page was reached is kept (see navigation/origin.ts), and a
 * filter change on a detail page must not forget it.
 */
export function useUrlState() {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const state: unknown = location.state

  const update = useCallback(
    (patch: ParamPatch, options: UrlWriteOptions = {}) => {
      setParams((current) => patchParams(current, patch), {
        replace: !options.push,
        state,
      })
    },
    [setParams, state],
  )

  const replaceAll = useCallback(
    (next: URLSearchParams, options: UrlWriteOptions = {}) => {
      setParams(next, { replace: !options.push, state })
    },
    [setParams, state],
  )

  return { params, update, replaceAll }
}

/** One free-text parameter; the empty string is its default. */
export function useUrlParam(key: string): [string, (value: string) => void] {
  const { params, update } = useUrlState()
  const value = params.get(key) ?? ''
  const set = useCallback((next: string) => update({ [key]: next }), [key, update])
  return [value, set]
}

/**
 * One parameter out of a fixed set, such as a tab. A value the page does not
 * know - an old link, a typo - reads as the default instead of breaking it.
 */
export function useUrlChoice<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): [T, (value: T) => void] {
  const { params, update } = useUrlState()
  const value = oneOf(params.get(key), allowed, fallback)
  const set = useCallback(
    (next: T) => update({ [key]: next === fallback ? null : next }),
    [key, fallback, update],
  )
  return [value, set]
}

/** An on/off parameter, written as `1` and absent when off. */
export function useUrlFlag(key: string): [boolean, (value: boolean) => void] {
  const { params, update } = useUrlState()
  const value = params.get(key) === '1'
  const set = useCallback(
    (next: boolean) => update({ [key]: next ? '1' : null }),
    [key, update],
  )
  return [value, set]
}

/** A set of values in one repeated parameter, such as the open rows. */
export function useUrlSet(
  key: string,
): [Set<string>, (next: Set<string>) => void, (value: string) => void] {
  const { params, update } = useUrlState()
  const joined = params.getAll(key).join('\u0000')
  // Keyed on the joined string, so the set keeps its identity while the
  // parameter does not change.
  const value = useMemo(() => new Set(joined ? joined.split('\u0000') : []), [joined])

  const set = useCallback((next: Set<string>) => update({ [key]: [...next] }), [key, update])
  const toggle = useCallback(
    (entry: string) => {
      const next = new Set(value)
      if (next.has(entry)) next.delete(entry)
      else next.add(entry)
      set(next)
    },
    [value, set],
  )
  return [value, set, toggle]
}

/**
 * A search box for a list filtered on the server.
 *
 * The field follows the keyboard at once; the address follows after a pause,
 * so a request goes out per word rather than per keystroke. A change of the
 * address from outside - Back, a reset - is taken over into the field.
 */
export function useDebouncedUrlParam(
  key: string,
  delayMs: number,
  onCommit?: (patch: ParamPatch) => ParamPatch,
): [string, (value: string) => void] {
  const { params, update } = useUrlState()
  const committed = params.get(key) ?? ''
  const [draft, setDraft] = useState(committed)
  // What this hook wrote last. Its own write coming back must not overwrite
  // what was typed since; only a change from elsewhere replaces the draft.
  const written = useRef(committed)

  useEffect(() => {
    if (committed === written.current) return
    written.current = committed
    setDraft(committed)
  }, [committed])

  useEffect(() => {
    if (draft.trim() === committed) return
    const timer = window.setTimeout(() => {
      written.current = draft.trim()
      const patch = { [key]: draft.trim() }
      update(onCommit ? onCommit(patch) : patch)
    }, delayMs)
    return () => window.clearTimeout(timer)
    // update and onCommit change with every render; the draft is what this
    // waits on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft])

  return [draft, setDraft]
}
