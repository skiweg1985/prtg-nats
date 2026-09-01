import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  useBackups,
  useCreateBackup,
  useExportRuntime,
  useRestartNats,
  useVerifySystem,
} from '@/api/hooks'
import { PermissionGate, useAuth } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Mono,
  Skeleton,
} from '@/components/ui/primitives'
import { formatBytes, formatRelative, shortFingerprint } from '@/utils/format'

/**
 * The maintenance actions the documentation promised for years.
 *
 * Verify, backup, export and restart existed as endpoints, handlers and CLI
 * commands - the guides pointed at a page in the interface that did not
 * exist, and one error message recommended a restart button nobody could
 * find. This is that page.
 */
export function SystemPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { can } = useAuth()
  const verify = useVerifySystem()
  const backup = useCreateBackup()
  const exportRuntime = useExportRuntime()
  const restart = useRestartNats()
  const [confirming, setConfirming] = useState<'restart' | 'export' | null>(null)

  const toJob = (accepted: { job_id: string }) => navigate(`/jobs/${accepted.job_id}`)

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg">{t('system.title')}</h1>
        <p className="text-ink-3 text-sm">{t('system.subtitle')}</p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title={t('system.verify.title')}
          action={
            <Button
              size="sm"
              variant="primary"
              disabled={verify.isPending}
              onClick={() => verify.mutate(undefined, { onSuccess: toJob })}
            >
              {t('system.verify.run')}
            </Button>
          }
        >
          <p className="text-ink-2 text-sm">{t('system.verify.body')}</p>
          {verify.error != null && <ErrorDetails error={verify.error} />}
        </Card>

        <PermissionGate permission="system.restart">
          <Card
            title={t('system.restart.title')}
            action={
              <Button size="sm" onClick={() => setConfirming('restart')}>
                {t('system.restart.run')}
              </Button>
            }
          >
            <p className="text-ink-2 text-sm">{t('system.restart.body')}</p>
            {restart.error != null && <ErrorDetails error={restart.error} />}
          </Card>
        </PermissionGate>
      </div>

      <PermissionGate permission="system.restart">
        <Card
          title={t('system.backup.title')}
          action={
            <span className="flex gap-2">
              <Button size="sm" onClick={() => setConfirming('export')}>
                {t('system.backup.export')}
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={backup.isPending}
                onClick={() => backup.mutate(undefined, { onSuccess: toJob })}
              >
                {t('system.backup.jetstream')}
              </Button>
            </span>
          }
        >
          <p className="text-ink-2 text-sm">{t('system.backup.body')}</p>
          {backup.error != null && <ErrorDetails error={backup.error} />}
          {exportRuntime.error != null && <ErrorDetails error={exportRuntime.error} />}
        </Card>
      </PermissionGate>

      <BackupsCard downloadable={can('system.restart')} />

      {confirming === 'restart' && (
        <ConfirmDialog
          title={t('system.restart.confirmTitle')}
          body={t('system.restart.confirmBody')}
          confirmLabel={t('system.restart.run')}
          pending={restart.isPending}
          onConfirm={() =>
            restart.mutate(undefined, {
              onSuccess: (accepted) => {
                setConfirming(null)
                toJob(accepted)
              },
            })
          }
          cancelLabel={t('common.cancel')}
          onClose={() => setConfirming(null)}
        />
      )}
      {confirming === 'export' && (
        <ConfirmDialog
          title={t('system.backup.exportConfirmTitle')}
          body={t('system.backup.exportWarning')}
          confirmLabel={t('system.backup.export')}
          pending={exportRuntime.isPending}
          onConfirm={() =>
            exportRuntime.mutate(undefined, {
              onSuccess: (accepted) => {
                setConfirming(null)
                toJob(accepted)
              },
            })
          }
          cancelLabel={t('common.cancel')}
          onClose={() => setConfirming(null)}
        />
      )}
    </div>
  )
}

function BackupsCard({ downloadable }: { downloadable: boolean }) {
  const { t } = useTranslation()
  const { data, isLoading, error } = useBackups()

  return (
    <Card title={t('system.backups.title')}>
      {error ? (
        <ErrorDetails error={error} />
      ) : isLoading ? (
        <Skeleton className="h-24" />
      ) : !data || data.length === 0 ? (
        <EmptyState title={t('system.backups.empty')} />
      ) : (
        <ul className="divide-rule divide-y">
          {data.map((file) => (
            <li
              key={file.name}
              className="flex flex-wrap items-center gap-3 py-2 text-sm"
            >
              <div className="min-w-0 flex-1">
                <Mono truncate>{file.name}</Mono>
                <p className="text-ink-3 text-xs">
                  {t(`system.backups.kind.${file.kind}`, {
                    defaultValue: file.kind,
                  })}{' '}
                  · {formatBytes(file.size_bytes)} · {formatRelative(file.created_at)}
                  {file.sha256 && <> · {shortFingerprint(file.sha256)}</>}
                </p>
              </div>
              {/* A plain same-origin link: the session is an HttpOnly cookie
                  and travels with the navigation - no blob dance needed. */}
              {downloadable && (
                <a
                  href={file.download_url}
                  download
                  className="text-accent text-sm hover:underline"
                >
                  {t('system.backups.download')}
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="text-ink-3 mt-3 text-xs">{t('system.backups.hint')}</p>
    </Card>
  )
}

