import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { usePrtgSettings, useSavePrtgSettings } from '@/api/hooks'
import type { PrtgSettingsUpdate } from '@/api/types'
import { useAuth } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import { Button, Card, Field, Input, Textarea } from '@/components/ui/primitives'

/**
 * Where the PRTG core is and how to ask it for probe names.
 *
 * The token field always starts empty: the stored token never comes back from
 * the server, so an empty field means "keep it" and removing it is a button
 * of its own.
 */
export function PrtgCard() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { data, error, refetch } = usePrtgSettings()
  const save = useSavePrtgSettings()
  const editable = can('system.settings')

  const [coreUrl, setCoreUrl] = useState<string | null>(null)
  const [token, setToken] = useState('')
  const [caBundle, setCaBundle] = useState('')

  if (error) return <ErrorDetails error={error} onRetry={() => void refetch()} />

  const shownUrl = coreUrl ?? data?.core_url ?? ''

  function send(extra: Partial<PrtgSettingsUpdate> = {}) {
    const body: PrtgSettingsUpdate = {
      core_url: shownUrl.trim() || null,
      ...extra,
    }
    if (token.trim()) body.token = token.trim()
    if (caBundle.trim()) body.ca_bundle = caBundle
    save.mutate(body, {
      onSuccess: () => {
        setToken('')
        setCaBundle('')
        setCoreUrl(null)
      },
    })
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    send()
  }

  return (
    <Card title={t('settings.prtg.title')}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-ink-3 text-sm">{t('settings.prtg.intro')}</p>
        <Field label={t('settings.prtg.coreUrl')} hint={t('settings.prtg.coreUrlHint')}>
          <Input
            value={shownUrl}
            onChange={(event) => setCoreUrl(event.target.value)}
            placeholder="https://prtg.example.test:1616"
            disabled={!editable}
            autoComplete="off"
          />
        </Field>
        <Field
          label={t('settings.prtg.token')}
          hint={t('settings.prtg.tokenHint')}
          note={data?.token_configured ? t('settings.prtg.tokenStored') : undefined}
        >
          <Input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            disabled={!editable}
            autoComplete="new-password"
          />
        </Field>
        <Field
          label={t('settings.prtg.caBundle')}
          optional
          hint={t('settings.prtg.caBundleHint')}
          note={data?.ca_bundle_configured ? t('settings.prtg.caBundleStored') : undefined}
        >
          <Textarea
            rows={4}
            value={caBundle}
            onChange={(event) => setCaBundle(event.target.value)}
            placeholder="-----BEGIN CERTIFICATE-----"
            disabled={!editable}
          />
        </Field>
        {save.error && <ErrorDetails error={save.error} />}
        {editable && (
          <div className="flex flex-wrap justify-end gap-2">
            {data?.token_configured && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => send({ clear_token: true })}
                disabled={save.isPending}
              >
                {t('settings.prtg.removeToken')}
              </Button>
            )}
            {data?.ca_bundle_configured && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => send({ clear_ca_bundle: true })}
                disabled={save.isPending}
              >
                {t('settings.prtg.removeCaBundle')}
              </Button>
            )}
            <Button type="submit" variant="primary" disabled={save.isPending}>
              {t('common.save')}
            </Button>
          </div>
        )}
      </form>
    </Card>
  )
}
