import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { useCreateUser, useDeleteUser, useUpdateUser, useUsers } from '@/api/hooks'
import type { WebUser } from '@/api/types'
import { useAuth } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Button,
  Card,
  Dot,
  EmptyState,
  Field,
  Input,
  Mono,
  Select,
} from '@/components/ui/primitives'
import { formatRelative } from '@/utils/format'

const ROLES = ['viewer', 'operator', 'administrator'] as const

/** Web account administration, on the settings page. */
export function UsersCard() {
  const { t } = useTranslation()
  const { principal } = useAuth()
  const { data: users, isLoading, error, refetch } = useUsers()
  const create = useCreateUser()
  const update = useUpdateUser()
  const remove = useDeleteUser()

  const [adding, setAdding] = useState(false)
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<string>('viewer')

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  function submit(event: FormEvent) {
    event.preventDefault()
    create.mutate(
      {
        username: username.trim(),
        password,
        display_name: displayName.trim(),
        roles: [role],
        must_change_password: true,
      },
      {
        onSuccess: () => {
          setAdding(false)
          setUsername('')
          setDisplayName('')
          setPassword('')
          setRole('viewer')
        },
      },
    )
  }

  return (
    <Card
      title={t('settings.users')}
      action={
        <Button size="sm" variant="primary" onClick={() => setAdding(!adding)}>
          {t('settings.addUser')}
        </Button>
      }
      dense
    >
      {adding && (
        <form onSubmit={submit} className="border-rule grid gap-3 border-b p-4 sm:grid-cols-2">
          <Field label={t('auth.username')}>
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
              autoComplete="off"
            />
          </Field>
          <Field label={t('auth.displayName')}>
            <Input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <Field label={t('auth.password')} hint={t('settings.initialPasswordHint')}>
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={12}
              required
              autoComplete="new-password"
            />
          </Field>
          <Field label={t('settings.role')}>
            <Select
              value={role}
              onChange={(event) => setRole(event.target.value)}
              className="rounded-control border-rule-2 bg-surface text-ink border px-2.5 py-1.5 text-sm"
            >
              {ROLES.map((name) => (
                <option key={name} value={name}>
                  {t(`roles.${name}`)}
                </option>
              ))}
            </Select>
          </Field>
          {create.error && (
            <div className="sm:col-span-2">
              <ErrorDetails error={create.error} />
            </div>
          )}
          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" variant="primary" disabled={create.isPending}>
              {t('auth.createAccount')}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setAdding(false)}>
              {t('common.cancel')}
            </Button>
          </div>
        </form>
      )}

      {isLoading ? null : !users?.length ? (
        <EmptyState title={t('common.none')} />
      ) : (
        <ul>
          {users.map((user) => (
            <UserRow
              key={user.id}
              user={user}
              isSelf={user.id === principal?.user_id}
              onRoleChange={(value) =>
                update.mutate({ id: user.id, roles: [value] })
              }
              onToggleActive={() =>
                update.mutate({ id: user.id, is_active: !user.is_active })
              }
              onDelete={() => remove.mutate(user.id)}
            />
          ))}
        </ul>
      )}
      {(update.error || remove.error) && (
        <div className="p-4">
          <ErrorDetails error={(update.error ?? remove.error)!} />
        </div>
      )}
    </Card>
  )
}

function UserRow({
  user,
  isSelf,
  onRoleChange,
  onToggleActive,
  onDelete,
}: {
  user: WebUser
  isSelf: boolean
  onRoleChange: (role: string) => void
  onToggleActive: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  return (
    <li className="border-rule flex flex-wrap items-center gap-3 border-b px-4 py-2.5 last:border-0">
      <Dot tone={user.is_active ? 'ok' : 'neutral'} />
      <div className="min-w-0 flex-1">
        <p className="text-ink text-sm font-medium">
          {user.display_name}
          {isSelf && (
            <Badge tone="accent" className="ml-2">
              {t('settings.you')}
            </Badge>
          )}
        </p>
        <Mono className="text-ink-3">{user.username}</Mono>
      </div>
      <span className="text-ink-3 text-xs">
        {user.last_login_at
          ? t('settings.lastLogin') + ': ' + formatRelative(user.last_login_at)
          : t('common.never')}
      </span>
      <Select
        value={user.roles[0] ?? 'viewer'}
        onChange={(event) => onRoleChange(event.target.value)}
        disabled={isSelf}
        className="rounded-control border-rule-2 bg-surface text-ink border px-2 py-1 text-xs"
        aria-label={t('settings.role')}
      >
        {ROLES.map((name) => (
          <option key={name} value={name}>
            {t(`roles.${name}`)}
          </option>
        ))}
      </Select>
      <Button size="sm" variant="ghost" onClick={onToggleActive} disabled={isSelf}>
        {user.is_active ? t('settings.deactivate') : t('settings.activate')}
      </Button>
      <Button size="sm" variant="danger" onClick={onDelete} disabled={isSelf}>
        {t('credentials.delete')}
      </Button>
    </li>
  )
}
