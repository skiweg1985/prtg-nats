import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import {
  useLinkPrtgObject,
  useProbePrtgName,
  usePrtgLinkCandidates,
  useUnlinkPrtgObject,
} from '@/api/hooks'
import type { PrtgName } from '@/api/types'
import { PermissionGate } from '@/app/providers'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import {
  Badge,
  Button,
  DetailRow,
  Dialog,
  Field,
  Input,
  Mono,
  Spinner,
} from '@/components/ui/primitives'

/**
 * Which object in PRTG this probe is, and the actions to change that.
 *
 * The name read through the link heads the page; repeating it here, next to
 * the enrollment name and a note that the two differ, said one thing three
 * times. PRTG has no probe GID to look a probe up by, so the probe is linked
 * to its PRTG object id once and read by that id from then on.
 */
export function PrtgNameRow({ probeId }: { probeId: string }) {
  const { t } = useTranslation()
  const { data, isLoading } = useProbePrtgName(probeId)
  const unlink = useUnlinkPrtgObject()
  const [linking, setLinking] = useState(false)

  return (
    <DetailRow label={t('probes.prtgName.label')}>
      {isLoading || !data ? (
        <Spinner />
      ) : (
        <>
          <PrtgNameValue name={data} />
          <PermissionGate permission="probe.update">
            {data.state !== 'not_configured' && (
              <span className="mt-1 flex flex-wrap gap-2">
                <Button size="sm" variant="ghost" onClick={() => setLinking(true)}>
                  {data.object_id ? t('probes.prtgName.relink') : t('probes.prtgName.link')}
                </Button>
                {data.object_id && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => unlink.mutate(probeId)}
                    disabled={unlink.isPending}
                  >
                    {t('probes.prtgName.unlink')}
                  </Button>
                )}
              </span>
            )}
          </PermissionGate>
          {unlink.error && <ErrorDetails error={unlink.error} />}
        </>
      )}
      {linking && <PrtgLinkDialog probeId={probeId} onClose={() => setLinking(false)} />}
    </DetailRow>
  )
}

function PrtgNameValue({ name }: { name: PrtgName }) {
  const { t } = useTranslation()
  if (name.state === 'ok' && name.object_id) {
    return <span className="block">{t('probes.prtgName.objectId', { id: name.object_id })}</span>
  }
  return (
    <span className="text-ink-3 block text-xs">
      {t(`probes.prtgName.reasons.${name.state}`, { id: name.object_id ?? '' })}
    </span>
  )
}

/**
 * Pick the probe as PRTG lists it, or enter its object id by hand. The
 * matching name is preselected and nothing more: a probe renamed in PRTG
 * before it was linked has no match, and a match is not proof.
 */
function PrtgLinkDialog({ probeId, onClose }: { probeId: string; onClose: () => void }) {
  const { t } = useTranslation()
  const { data, isLoading, error } = usePrtgLinkCandidates(probeId, true)
  const link = useLinkPrtgObject()
  const [picked, setPicked] = useState<string | null>(null)
  const [manual, setManual] = useState('')

  const chosen = manual.trim() || picked || data?.suggested_object_id || ''

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!chosen) return
    link.mutate({ id: probeId, objectId: chosen }, { onSuccess: onClose })
  }

  return (
    <Dialog title={t('probes.prtgName.dialogTitle')} onClose={onClose} size="md">
      <form onSubmit={submit} className="space-y-4">
        <p className="text-ink-3 text-sm">{t('probes.prtgName.dialogIntro')}</p>
        {isLoading && <Spinner />}
        {error && <ErrorDetails error={error} />}
        {data && data.candidates.length === 0 && (
          <p className="text-ink-3 text-sm">{t('probes.prtgName.noCandidates')}</p>
        )}
        {data && data.candidates.length > 0 && (
          <fieldset className="max-h-64 space-y-1 overflow-auto">
            {data.candidates.map((candidate) => {
              const suggested = candidate.object_id === data.suggested_object_id
              return (
                <label key={candidate.object_id} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="prtg-object"
                    value={candidate.object_id}
                    checked={!manual.trim() && chosen === candidate.object_id}
                    onChange={() => {
                      setPicked(candidate.object_id)
                      setManual('')
                    }}
                  />
                  <span>{candidate.name}</span>
                  <Mono className="text-ink-3">{candidate.object_id}</Mono>
                  {candidate.status && (
                    <span className="text-ink-3 text-xs">{candidate.status}</span>
                  )}
                  {suggested && <Badge tone="accent">{t('probes.prtgName.suggested')}</Badge>}
                </label>
              )
            })}
          </fieldset>
        )}
        <Field label={t('probes.prtgName.manual')} hint={t('probes.prtgName.manualHint')}>
          <Input
            value={manual}
            onChange={(event) => setManual(event.target.value)}
            inputMode="numeric"
            autoComplete="off"
          />
        </Field>
        {link.error && <ErrorDetails error={link.error} />}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" variant="primary" disabled={!chosen || link.isPending}>
            {t('probes.prtgName.link')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
