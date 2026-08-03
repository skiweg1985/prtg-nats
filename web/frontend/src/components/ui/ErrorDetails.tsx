import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'

import { Button, Mono } from './primitives'

/**
 * The six questions an administrator has when something fails.
 *
 * 1. What failed?  2. Which target?  3. At which step?  4. Likely cause?
 * 5. What can I do?  6. What are the technical details?
 *
 * Cause and action come from optional `<code>.cause` and `<code>.action`
 * translation keys. A code without them still renders the first three answers,
 * so adding an error never produces an empty panel.
 */
export function ErrorDetails({
  error,
  step,
  target,
  onRetry,
}: {
  error: ApiError | Error
  step?: string | null
  target?: string | null
  onRetry?: () => void
}) {
  const { t, i18n } = useTranslation()
  const [showRaw, setShowRaw] = useState(false)

  const api = error instanceof ApiError ? error : null
  const messageKey = api?.body.message_key ?? 'errors.internal.unexpected'
  const params = api?.body.params ?? {}
  const details = api?.body.details ?? (error instanceof Error ? error.message : null)

  const causeKey = api ? `errors.${api.code}.cause` : null
  const actionKey = api ? `errors.${api.code}.action` : null
  const hasCause = causeKey ? i18n.exists(causeKey) : false
  const hasAction = actionKey ? i18n.exists(actionKey) : false

  return (
    <div className="rounded-card border-danger/30 bg-danger-soft overflow-hidden border">
      <div className="space-y-3 p-4">
        <div>
          <p className="label-mono text-danger">{t('errors.whatFailed')}</p>
          <p className="text-ink mt-0.5 text-sm font-medium">{t(messageKey, params)}</p>
        </div>

        {target && (
          <div>
            <p className="label-mono">{t('errors.affected')}</p>
            <Mono>{target}</Mono>
          </div>
        )}

        {step && (
          <div>
            <p className="label-mono">{t('errors.atStep')}</p>
            <p className="text-ink-2 text-sm">{t(`jobs.steps.${step}`, step)}</p>
          </div>
        )}

        {hasCause && causeKey && (
          <div>
            <p className="label-mono">{t('errors.likelyCause')}</p>
            <p className="text-ink-2 text-sm">{t(causeKey, params)}</p>
          </div>
        )}

        {hasAction && actionKey && (
          <div>
            <p className="label-mono">{t('errors.recommendedAction')}</p>
            <p className="text-ink-2 text-sm">{t(actionKey, params)}</p>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          {onRetry && api?.retryable !== false && (
            <Button size="sm" variant="secondary" onClick={onRetry}>
              {t('common.retry')}
            </Button>
          )}
          {details && (
            <Button size="sm" variant="ghost" onClick={() => setShowRaw(!showRaw)}>
              {showRaw
                ? t('common.hideTechnicalDetails')
                : t('common.showTechnicalDetails')}
            </Button>
          )}
        </div>
      </div>

      {showRaw && details && (
        <div className="border-danger/20 bg-surface-2 border-t px-4 py-3">
          {/* Never translated: this is the machine's own words. */}
          <pre className="text-ink-2 max-h-64 overflow-auto font-mono text-xs whitespace-pre-wrap">
            {details}
          </pre>
          {api?.body.correlation_id && (
            <p className="text-ink-3 mt-2 font-mono text-[0.6875rem]">
              {t('errors.correlationId')}: {api.body.correlation_id}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
