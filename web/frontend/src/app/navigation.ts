import { useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import { useLocation, useNavigationType, type Location } from 'react-router-dom'

/**
 * What the browser tab remembers about the way it was navigated.
 *
 * Two things, both per browser tab and both gone when it closes:
 *
 * - Where every history entry was opened from. A detail page's breadcrumb
 *   follows the way the user came - a run opened from the filtered sensor log
 *   leads back to that filtered list, not to a fixed parent. Keyed by the
 *   router's entry key, which lives in history.state and so survives a
 *   reload, while a copied link carries none and shows the plain hierarchy.
 * - The last address of every page in the sidebar, so a click on it restores
 *   the filters, search and tab last used there.
 *
 * sessionStorage, because it is per tab: two tabs do not interfere. Where it
 * is unavailable - private mode, blocked storage - the same data lives in
 * memory until the next reload, and nothing throws.
 */

/** One history entry, as far as the way back needs it. */
export interface Visit {
  pathname: string
  search: string
  /** The router's key of that entry, to follow its own origin. */
  key: string
}

/**
 * Location state a breadcrumb link carries.
 *
 * A crumb leads back to a page, not on from the current one: the page it
 * opens gets the origin that page had (`crumb` is that entry's key), or no
 * origin at all for a crumb of the plain hierarchy (`null`). Without it,
 * stepping up from a run to its sensor would make the run the sensor page's
 * origin.
 */
export interface CrumbState {
  crumb: string | null
}

const ORIGINS_KEY = 'prtg-nats.origins'
const LAST_SEARCH_KEY = 'prtg-nats.last-search'
// A long session opens a few hundred pages; older origins are of no use.
const MAX_ORIGINS = 300
// Enough for any real way through the interface, and a guard against a loop.
const MAX_CHAIN = 8

function readStore<T>(key: string, fallback: T): T {
  try {
    const raw = window.sessionStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function writeStore(key: string, value: unknown) {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Unavailable or full: the in-memory copy carries on.
  }
}

let origins: Record<string, Visit> | null = null
let lastSearch: Record<string, string> | null = null
let version = 0
const listeners = new Set<() => void>()

function loadedOrigins(): Record<string, Visit> {
  origins ??= readStore<Record<string, Visit>>(ORIGINS_KEY, {})
  return origins
}

function loadedLastSearch(): Record<string, string> {
  lastSearch ??= readStore<Record<string, string>>(LAST_SEARCH_KEY, {})
  return lastSearch
}

function changed() {
  version += 1
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function setOrigin(key: string, visit: Visit | undefined) {
  const current = loadedOrigins()
  if (visit) {
    // Re-inserted, so the oldest entries are the first ones dropped.
    delete current[key]
    current[key] = visit
    const keys = Object.keys(current)
    for (const stale of keys.slice(0, Math.max(0, keys.length - MAX_ORIGINS))) {
      delete current[stale]
    }
  } else {
    delete current[key]
  }
  writeStore(ORIGINS_KEY, current)
}

function isCrumbState(state: unknown): state is CrumbState {
  return typeof state === 'object' && state !== null && 'crumb' in state
}

/**
 * Records one step of navigation. Exported for the tests; the app calls it
 * through useNavigationTracking.
 */
export function recordNavigation(
  previous: Location | null,
  next: Location,
  action: 'PUSH' | 'REPLACE' | 'POP',
  remember: (pathname: string) => boolean,
) {
  if (remember(next.pathname)) {
    const searches = loadedLastSearch()
    if (searches[next.pathname] !== next.search) {
      searches[next.pathname] = next.search
      writeStore(LAST_SEARCH_KEY, searches)
    }
  }

  // Back and Forward return to entries that already have their origin.
  if (previous && previous.key !== next.key && action !== 'POP') {
    const known = loadedOrigins()
    if (action === 'PUSH' && isCrumbState(next.state)) {
      setOrigin(next.key, next.state.crumb ? known[next.state.crumb] : undefined)
    } else if (action === 'REPLACE' || previous.pathname === next.pathname) {
      // The same page with other filters, or another page of the same list:
      // still opened from where the page was opened from.
      setOrigin(next.key, known[previous.key])
    } else {
      setOrigin(next.key, {
        pathname: previous.pathname,
        search: previous.search,
        key: previous.key,
      })
    }
  }
  changed()
}

/** Mounted once, in the layout every page renders in. */
export function useNavigationTracking(remember: (pathname: string) => boolean) {
  const location = useLocation()
  const action = useNavigationType()
  const previous = useRef<Location | null>(null)
  const rememberRef = useRef(remember)
  rememberRef.current = remember

  // A layout effect, so a breadcrumb that reads the new origin re-renders
  // before the page is painted with the old one.
  useLayoutEffect(() => {
    if (previous.current?.key === location.key) return
    recordNavigation(previous.current, location, action, rememberRef.current)
    previous.current = location
  }, [location, action])
}

function useVersion() {
  return useSyncExternalStore(subscribe, () => version)
}

/**
 * Where the current page was opened from, nearest first: the origin, the
 * origin's origin, and so on. Empty for a page reached by a link from
 * outside or by typing the address.
 */
export function useOriginChain(): Visit[] {
  useVersion()
  const location = useLocation()
  const known = loadedOrigins()
  const chain: Visit[] = []
  const seen = new Set<string>([location.key])
  let next = known[location.key]
  while (next && chain.length < MAX_CHAIN && !seen.has(next.key)) {
    chain.push(next)
    seen.add(next.key)
    next = known[next.key]
  }
  return chain
}

/** The address a sidebar entry opens: the page as it was last left. */
export function useLastSearch(pathname: string): string {
  useVersion()
  return loadedLastSearch()[pathname] ?? ''
}

/** For the tests: forget what this module loaded, as a new page load would. */
export function resetNavigationMemory() {
  origins = null
  lastSearch = null
  changed()
}
