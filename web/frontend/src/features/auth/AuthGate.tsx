import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { useLogin, useSetup } from '@/api/hooks'
import { useAuth } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Button, Card, Field, Input, Skeleton } from '@/components/ui/primitives'

/**
 * Decides between three screens: first-run setup, sign-in, or the application.
 *
 * The server answers that question in one call, so the browser never guesses
 * and never flashes a login form at somebody who is already signed in.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { state, isLoading } = useAuth()

  if (isLoading || !state) {
    return (
      <div className="mx-auto max-w-sm space-y-3 p-10">
        <Skeleton className="h-8" />
        <Skeleton className="h-32" />
      </div>
    )
  }

  if (state.setup_required) return <SetupScreen />
  if (!state.authenticated) return <LoginScreen />
  return <>{children}</>
}

function CenteredCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="bg-paper flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <Card title={title}>{children}</Card>
      </div>
    </div>
  )
}

function LoginScreen() {
  const { t } = useTranslation()
  const login = useLogin()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    login.mutate({ username, password })
  }

  return (
    <CenteredCard title={t('auth.signInTitle', { name: t('app.name') })}>
      <form onSubmit={submit} className="space-y-4">
        <Field label={t('auth.username')}>
          <Input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </Field>
        <Field label={t('auth.password')}>
          <Input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </Field>

        {login.error && <ErrorDetails error={login.error as ApiError} />}

        <Button type="submit" variant="primary" className="w-full" disabled={login.isPending}>
          {login.isPending ? t('common.loading') : t('auth.signIn')}
        </Button>
      </form>
    </CenteredCard>
  )
}

function SetupScreen() {
  const { t } = useTranslation()
  const setup = useSetup()
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')

  const error = setup.error instanceof ApiError ? setup.error : null
  const passwordProblems = (error?.body.params.problems as string[] | undefined) ?? []

  function submit(event: FormEvent) {
    event.preventDefault()
    setup.mutate({ username, password, display_name: displayName })
  }

  return (
    <CenteredCard title={t('auth.setupTitle')}>
      <form onSubmit={submit} className="space-y-4">
        <p className="text-ink-2 text-sm">{t('auth.setupIntro')}</p>

        <Field label={t('auth.username')}>
          <Input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </Field>
        <Field label={t('auth.displayName')}>
          <Input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            autoComplete="name"
          />
        </Field>
        <Field
          label={t('auth.password')}
          hint={t('auth.passwordHint')}
          error={passwordProblems.map((problem) => t(`validation.${problem}`)).join(' ')}
        >
          <Input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
            minLength={12}
            required
          />
        </Field>

        {error && passwordProblems.length === 0 && <ErrorDetails error={error} />}

        <Button type="submit" variant="primary" className="w-full" disabled={setup.isPending}>
          {setup.isPending ? t('common.loading') : t('auth.createAccount')}
        </Button>
      </form>
    </CenteredCard>
  )
}
