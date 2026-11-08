import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, KeyRound, Trash2 } from 'lucide-react'

import {
  useCreateNatsAccount,
  useDeleteNatsAccount,
  useNatsAccounts,
  useRevealNatsPassword,
} from '@/api/hooks'
import type { NatsAccount } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  ActionMenu,
  Badge,
  Button,
  Card,
  Dialog,
  Field,
  Input,
  Mono,
  PageHeader,
  RowActions,
} from '@/components/ui/primitives'

import { RevealPasswordDialog, RotateAccountDialog } from './NatsAccountDialogs'

/**
 * NATS accounts that belong to no probe: the Core's shared account, and
 * accounts created by hand or left behind by a retired probe. A probe's own
 * account is shown and rotated on the probe's page, where it is looked for.
 *
 * The dangerous parts keep their friction: rotation runs as a job that also
 * reconfigures the enrolled probe, deletion is refused server-side while a
 * probe depends on the account, and revealing a password is an explicit,
 * audited action that the interface never does on its own.
 */
export function CredentialsPage() {
  const { t } = useTranslation()
  const { data, isLoading, error, refetch } = useNatsAccounts()
  const create = useCreateNatsAccount()
  const remove = useDeleteNatsAccount()
  const reveal = useRevealNatsPassword()

  const [newUsername, setNewUsername] = useState('')
  const [revealed, setRevealed] = useState<{ username: string; password: string } | null>(
    null,
  )
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  // Rotating cuts a probe's current credentials and hands it new ones. It ran
  // on a single click, while deleting - the one the server refuses outright
  // while a probe depends on the account - asked first.
  const [confirmRotate, setConfirmRotate] = useState<NatsAccount | null>(null)

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
          <RowActions>
            <Button
              size="sm"
              variant="ghost"
              icon={Eye}
              onClick={() =>
                reveal.mutate(row.username, {
                  onSuccess: (data) => setRevealed(data),
                })
              }
            >
              {t('common.reveal')}
            </Button>
            {/* Rotating and deleting are rare and consequential; both ask
                first. The shared Core account cannot be deleted at all. */}
            <ActionMenu
              label={t('common.moreActions')}
              actions={[
                {
                  key: 'rotate',
                  label: t('credentials.rotate'),
                  icon: KeyRound,
                  onSelect: () => setConfirmRotate(row),
                },
                ...(row.is_shared || row.probe_enrolled
                  ? []
                  : [
                      {
                        key: 'delete',
                        label: t('credentials.delete'),
                        icon: Trash2,
                        tone: 'danger' as const,
                        onSelect: () => setConfirmDelete(row.username),
                      },
                    ]),
              ]}
            />
          </RowActions>
        </PermissionGate>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      {/* Probe accounts are managed by enrolment and retirement; the subtitle
          says where they are, which keeps people from deleting one by hand
          and stranding a probe. Once is enough - title, subtitle and a
          paragraph used to say the same thing three times. */}
      <PageHeader title={t('credentials.title')} subtitle={t('credentials.subtitle')} />

      <DataTable
        rows={data?.filter((account) => !account.probe_enrolled)}
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

      {reveal.error && <ErrorDetails error={reveal.error} />}

      {revealed && (
        <RevealPasswordDialog
          username={revealed.username}
          password={revealed.password}
          onClose={() => setRevealed(null)}
        />
      )}

      {confirmRotate && (
        <RotateAccountDialog
          username={confirmRotate.username}
          probeEnrolled={confirmRotate.probe_enrolled}
          onClose={() => setConfirmRotate(null)}
        />
      )}

      {confirmDelete && (
        <Dialog title={t('confirm.title')} onClose={() => setConfirmDelete(null)}>
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
        </Dialog>
      )}
    </div>
  )
}
