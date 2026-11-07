import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useRotateNatsAccount } from '@/api/hooks'
import { CopyButton } from '@/components/ui/CopyBlock'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Button, Dialog, Mono } from '@/components/ui/primitives'

/**
 * The two account dialogs, shared by the probe page and the account list.
 *
 * A probe's account lives with the probe now, the list keeps what belongs to
 * no probe - and both have to show a password and rotate an account the same
 * way, with the same friction.
 */

export function RevealPasswordDialog({
  username,
  password,
  onClose,
}: {
  username: string
  password: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  return (
    <Dialog
      title={t('credentials.revealTitle', { account: username })}
      onClose={onClose}
      size="md"
    >
      <p className="text-ink-2 mb-3 text-sm">{t('credentials.revealHint')}</p>
      <div className="bg-surface-2 rounded-inset flex items-center gap-2 p-3">
        <Mono className="min-w-0 flex-1 break-all">{password}</Mono>
        <CopyButton value={password} />
      </div>
      <div className="mt-4 flex justify-end">
        <Button onClick={onClose}>{t('common.close')}</Button>
      </div>
    </Dialog>
  )
}

/**
 * Rotating cuts a probe's current credentials and hands it new ones, so it
 * asks first. It runs as a job, and the job page is where it goes on.
 */
export function RotateAccountDialog({
  username,
  probeEnrolled,
  onClose,
}: {
  username: string
  probeEnrolled: boolean
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const rotate = useRotateNatsAccount()
  return (
    <Dialog title={t('confirm.title')} onClose={onClose}>
      <p className="text-ink-2 text-sm">
        {t('credentials.rotateWarning', { account: username })}
      </p>
      {probeEnrolled && (
        <p className="text-ink-2 mt-2 text-sm">{t('credentials.rotateProbeHint')}</p>
      )}
      {rotate.error && (
        <div className="mt-3">
          <ErrorDetails error={rotate.error} />
        </div>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          {t('common.cancel')}
        </Button>
        <Button
          variant="primary"
          onClick={() =>
            rotate.mutate(username, {
              onSuccess: (accepted) => {
                onClose()
                navigate(`/jobs/${accepted.job_id}`)
              },
            })
          }
          disabled={rotate.isPending}
        >
          {t('credentials.rotate')}
        </Button>
      </div>
    </Dialog>
  )
}
