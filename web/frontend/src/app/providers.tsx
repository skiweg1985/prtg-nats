import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import { ApiError } from '@/api/client'
import { useAuthState } from '@/api/hooks'
import type { AuthState, Principal } from '@/api/types'

/** Retrying a 401 or a 403 only produces the same answer more slowly. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.httpStatus >= 400 && error.httpStatus < 500) {
    return false
  }
  return failureCount < 2
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        staleTime: 5_000,
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  })
}

// --- Authentication ---------------------------------------------------------

interface AuthContextValue {
  state: AuthState | undefined
  principal: Principal | null
  isLoading: boolean
  /** Server-side truth mirrored for gating; never the authorisation itself. */
  can: (permission: string) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const { data, isLoading } = useAuthState()
  const principal = data?.principal ?? null

  const value = useMemo<AuthContextValue>(() => {
    const granted = new Set(principal?.permissions ?? [])
    return {
      state: data,
      principal,
      isLoading,
      // Hiding a control the caller may not use is a courtesy. The server
      // refuses the request regardless, which is where the rule actually lives.
      can: (permission) => granted.has(permission),
    }
  }, [data, principal, isLoading])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}

/** Renders its children only when the caller holds the permission. */
export function PermissionGate({
  permission,
  children,
  fallback = null,
}: {
  permission: string
  children: ReactNode
  fallback?: ReactNode
}) {
  const { can } = useAuth()
  return <>{can(permission) ? children : fallback}</>
}

// --- Theme ------------------------------------------------------------------

export type ThemeChoice = 'light' | 'dark' | 'system'

interface ThemeContextValue {
  choice: ThemeChoice
  resolved: 'light' | 'dark'
  setChoice: (choice: ThemeChoice) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)
const THEME_STORAGE_KEY = 'prtg-nats-theme'

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState<ThemeChoice>(
    () => (localStorage.getItem(THEME_STORAGE_KEY) as ThemeChoice | null) ?? 'system',
  )
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  )

  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const listener = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    query.addEventListener('change', listener)
    return () => query.removeEventListener('change', listener)
  }, [])

  const resolved: 'light' | 'dark' =
    choice === 'system' ? (systemDark ? 'dark' : 'light') : choice

  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark')
    localStorage.setItem(THEME_STORAGE_KEY, choice)
  }, [choice, resolved])

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme must be used inside ThemeProvider')
  return value
}

export function AppProviders({
  children,
  client,
}: {
  children: ReactNode
  client?: QueryClient
}) {
  const [queryClient] = useState(() => client ?? createQueryClient())
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>{children}</AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
