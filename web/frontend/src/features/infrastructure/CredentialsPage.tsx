import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useCreateNatsAccount,
  useDeleteNatsAccount,
  useNatsAccounts,
  useRevealNatsPassword,
  useRotateNatsAccount,
} from '@/api/hooks'
import type { NatsAccount } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  Mono,
} from '@/components/ui/primitives'

/**
 * NATS account management - what `prtg-nats user …` used to be.
 *
 * The dangerous parts keep their friction: rotation runs as a job that also
 * reconfigures the enrolled probe, deletion is refused server-side while a
 * probe depends on the account, and revealing a password is an explicit,
 * audited action that the interface never does on its own.
 */
export function CredentialsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useNatsAccounts()
  const create = useCreateNatsAccount()
  const rotate = useRotateNatsAccount()
  const remove = useDeleteNatsAccount()
  const reveal = useRevealNatsPassword()

  const [newUsername, setNewUsername] = useState('')
  const [revealed, setRevealed] = useState<{ username: string; password: string } | null>(
    null,
  )
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  function submitCreate(event: FormEvent) {
    event.preventDefault()
    create.mutate(newUsername.trim(), { onSuccess: () => setNewUsername('') })
  }

  const columns: Column<NatsAccount>[] = [
    {
      key: 'username',
      header: t('credentials.columns.account'),
      sortValue: (row) => row.username,
      searchValue: (row) => row.username,
      cell: (row) => (
        <div className="flex items-center gap-2">
          <Mono className="text-ink">{row.username}</Mono>
          {row.is_shared && <Badge tone="accent">{t('credentials.shared')}</Badge>}
        </div>
      ),
    },
    {
      key: 'usage',
      header: t('credentials.columns.usage'),
      sortValue: (row) => (row.probe_enrolled ? 0 : 1),
      cell: (row) => (
        <span className="text-ink-2 text-sm">
          {row.is_shared
            ? t('credentials.usedByCore')
            : row.probe_enrolled
              ? t('credentials.usedByProbe')
              : t('credentials.unused')}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (row) => (
        <PermissionGate permission="credential.rotate">
          <span className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                reveal.mutate(row.username, {
                  onSuccess: (data) => setRevealed(data),
                })
              }
            >
              {t('common.reveal')}
            </Button>
            <Button
              size="sm"
              onClick={() =>
                rotate.mutate(row.username, {
                  onSuccess: (accepted) => navigate(`/jobs/${accepted.job_id}`),
                })
              }
              disabled={rotate.isPending}
            >
              {t('credentials.rotate')}
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => setConfirmDelete(row.username)}
              disabled={row.probe_enrolled || row.is_shared}
              title={
                row.probe_enrolled ? t('credentials.deleteBlockedProbe') : undefined
              }
            >
              {t('credentials.delete')}
            </Button>
          </span>
        </PermissionGate>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg">{t('credentials.title')}</h1>
        <p className="text-ink-3 text-sm">{t('credentials.subtitle')}</p>
      </header>

      <DataTable
        rows={data}
        columns={columns}
        rowKey={(row) => row.username}
        isLoading={isLoading}
        emptyTitle={t('credentials.empty')}
      />

      <PermissionGate permission="credential.rotate">
        <Card title={t('credentials.createTitle')}>
          <form onSubmit={submitCreate} className="flex flex-wrap items-end gap-3">
            <Field label={t('credentials.columns.account')}>
              <Input
                value={newUsername}
                onChange={(event) => setNewUsername(event.target.value)}
                placeholder="mpp-site-01"
                pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
                required
                className="w-64 font-mono"
              />
            </Field>
            <Button type="submit" variant="primary" disabled={create.isPending}>
              {t('credentials.create')}
            </Button>
          </form>
          <p className="text-ink-3 mt-2 text-xs">{t('credentials.createHint')}</p>
          {create.error && <ErrorDetails error={create.error} />}
        </Card>
      </PermissionGate>

      {rotate.error && <ErrorDetails error={rotate.error} />}
      {reveal.error && <ErrorDetails error={reveal.error} />}

      {revealed && (
        <div
          className="fixed inset-0 z-(--z-dialog) flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          onClick={(event) => {
            if (event.target === event.currentTarget) setRevealed(null)
          }}
        >
          <div className="w-full max-w-lg">
            <Card title={t('credentials.revealTitle', { account: revealed.username })}>
              <p className="text-ink-2 mb-3 text-sm">{t('credentials.revealHint')}</p>
              <div className="bg-surface-2 rounded-inset flex items-center gap-2 p-3">
                <Mono className="min-w-0 flex-1 break-all">{revealed.password}</Mono>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void navigator.clipboard.writeText(revealed.password)}
                >
                  {t('common.copy')}
                </Button>
              </div>
              <div className="mt-4 flex justify-end">
                <Button onClick={() => setRevealed(null)}>{t('common.close')}</Button>
              </div>
            </Card>
          </div>
        </div>
      )}

      {confirmDelete && (
        <div
          className="fixed inset-0 z-(--z-dialog) flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          onClick={(event) => {
            if (event.target === event.currentTarget) setConfirmDelete(null)
          }}
        >
          <div className="w-full max-w-md">
            <Card title={t('confirm.title')}>
              <p className="text-ink-2 text-sm">
                {t('credentials.deleteWarning', { account: confirmDelete })}
              </p>
              {remove.error && (
                <div className="mt-3">
                  <ErrorDetails error={remove.error} />
                </div>
              )}
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setConfirmDelete(null)}>
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="danger"
                  onClick={() =>
                    remove.mutate(confirmDelete, {
                      onSuccess: () => setConfirmDelete(null),
                    })
                  }
                  disabled={remove.isPending}
                >
                  {t('credentials.delete')}
                </Button>
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
