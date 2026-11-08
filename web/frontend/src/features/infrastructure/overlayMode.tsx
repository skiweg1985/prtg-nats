import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import type { OverlayMode } from '@/api/types'
import { Banner, Button, Dialog } from '@/components/ui/primitives'

const MODES: OverlayMode[] = ['off', 'auto', 'on']

/**
 * The three-way choice, with what each one means beside it.
 *
 * Spelled out rather than a select: the difference between "auto" and "on" is
 * the whole feature, and it is not something a reader should have to infer
 * from three short words in a dropdown.
 */
export function OverlayModeChoice({
  value,
  onChange,
}: {
  value: OverlayMode
  onChange: (mode: OverlayMode) => void
}) {
  const { t } = useTranslation()
  return (
    <fieldset className="space-y-2">
      <legend className="mb-1 text-sm font-medium">
        {t('infrastructure.overlay.modeLegend')}
      </legend>
      {MODES.map((mode) => (
        <label key={mode} className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name="overlay-mode"
            className="mt-1"
            checked={value === mode}
            onChange={() => onChange(mode)}
          />
          <span>
            <span className="font-medium">
              {t(`infrastructure.overlay.modes.${mode}.name`)}
            </span>
            <span className="text-ink-3 block">
              {t(`infrastructure.overlay.modes.${mode}.hint`)}
            </span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}

/**
 * The confirmation in front of a mode change.
 *
 * Shared by the overlay area and a probe's own page so that both ask the same
 * question and send the same request. Neither may offer a force flag: the
 * check that refuses to switch a probe off through the tunnel the switch takes
 * down is a command-line override, not a button.
 */
export function OverlayModeDialog({
  initial,
  pending,
  onApply,
  onClose,
}: {
  initial: OverlayMode
  pending: boolean
  onApply: (mode: OverlayMode) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<OverlayMode>(initial)
  return (
    <Dialog onClose={onClose} title={t('infrastructure.overlay.actions.mode')}>
      <OverlayModeChoice value={mode} onChange={setMode} />
      {mode === 'off' && (
        <div className="mt-3">
          <Banner tone="warn">{t('infrastructure.overlay.offWarning')}</Banner>
        </div>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          {t('common.cancel')}
        </Button>
        <Button disabled={pending} onClick={() => onApply(mode)}>
          {t('common.apply')}
        </Button>
      </div>
    </Dialog>
  )
}

/**
 * The confirmation in front of putting probes on the overlay.
 *
 * The overlay area passes its list of probes to pick from as children; a
 * probe's own page has nothing to pick and passes none.
 */
export function OverlayAttachDialog({
  title,
  initial,
  pending,
  canConfirm = true,
  onConfirm,
  onClose,
  children,
}: {
  title: string
  initial: OverlayMode
  pending: boolean
  canConfirm?: boolean
  onConfirm: (mode: OverlayMode) => void
  onClose: () => void
  children?: ReactNode
}) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<OverlayMode>(initial)
  return (
    <Dialog onClose={onClose} title={title}>
      <p className="text-ink-3 mb-3 text-sm">{t('infrastructure.overlay.add.dialog')}</p>
      {children}
      <OverlayModeChoice value={mode} onChange={setMode} />
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          {t('common.cancel')}
        </Button>
        <Button disabled={!canConfirm || pending} onClick={() => onConfirm(mode)}>
          {t('infrastructure.overlay.add.confirm')}
        </Button>
      </div>
    </Dialog>
  )
}

/**
 * Whether the path a probe is on is a fault, and how bad.
 *
 * The route a probe is meant to take is not news and carries no colour - a
 * table of green "Direkt" and "Tunnel" hid the one row that mattered. Colour
 * is kept for the faults: a tunnel that is down, and "auto" on the tunnel,
 * which works but means the ordinary route is down and nobody has noticed.
 */
export function pathTone(mode: OverlayMode, state: string): 'warn' | 'danger' | 'neutral' {
  if (mode === 'off') return 'neutral'
  if (state === 'down' || state === 'no_handshake') {
    return mode === 'on' ? 'danger' : 'warn'
  }
  if (state === 'tunnel' && mode === 'auto') return 'warn'
  return 'neutral'
}
