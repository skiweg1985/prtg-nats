import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { useChangePassword, useLogin, useLogout, useSetup } from '@/api/hooks'
import { useAuth } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Button, Card, Field, Input, Skeleton } from '@/components/ui/primitives'
import { guideHomeHref } from '@/utils/guide'

/**
 * Decides between four screens: first-run setup, sign-in, the password change
 * an account still owes, or the application.
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
  // The server refuses everything else for such a session; this screen only
  // spares the user a page full of refusals.
  if (state.principal?.must_change_password) return <ChangePasswordScreen />
  return <>{children}</>
}

function CenteredCard({ title, children }: { title: string; children: ReactNode }) {
  const { t, i18n } = useTranslation()
  return (
    <div className="bg-paper flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <Card title={title}>{children}</Card>
        {/* Readable before signing in: the guide holds nothing an account
            would protect, and the first visit is when it helps most. */}
        <p className="mt-3 text-center text-sm">
          <a
            href={guideHomeHref(i18n.resolvedLanguage)}
            target="_blank"
            rel="noopener"
            className="text-ink-3 hover:text-ink underline-offset-2 hover:underline"
          >
            {t('nav.guide')} <span aria-hidden>↗</span>
          </a>
        </p>
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

function ChangePasswordScreen() {
  const { t } = useTranslation()
  const change = useChangePassword()
  const logout = useLogout()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')

  const error = change.error instanceof ApiError ? change.error : null
  const passwordProblems = (error?.body.params.problems as string[] | undefined) ?? []

  function submit(event: FormEvent) {
    event.preventDefault()
    change.mutate({ current_password: currentPassword, new_password: newPassword })
  }

  return (
    <CenteredCard title={t('auth.changePassword')}>
      <form onSubmit={submit} className="space-y-4">
        <p className="text-ink-2 text-sm">{t('auth.mustChangePassword')}</p>

        <Field label={t('auth.currentPassword')}>
          <Input
            type="password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
        </Field>
        <Field
          label={t('auth.newPassword')}
          hint={t('auth.passwordHint')}
          error={passwordProblems.map((problem) => t(`validation.${problem}`)).join(' ')}
        >
          <Input
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            autoComplete="new-password"
            minLength={12}
            required
          />
        </Field>

        {error && passwordProblems.length === 0 && <ErrorDetails error={error} />}

        <Button type="submit" variant="primary" className="w-full" disabled={change.isPending}>
          {change.isPending ? t('common.loading') : t('auth.changePassword')}
        </Button>
        <Button
          type="button"
          className="w-full"
          onClick={() => logout.mutate()}
          disabled={logout.isPending}
        >
          {t('auth.signOut')}
        </Button>
      </form>
    </CenteredCard>
  )
}
