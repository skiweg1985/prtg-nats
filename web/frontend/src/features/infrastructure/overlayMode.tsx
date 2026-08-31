import { useTranslation } from 'react-i18next'

import type { OverlayMode } from '@/api/types'

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
            <span className="text-muted block">
              {t(`infrastructure.overlay.modes.${mode}.hint`)}
            </span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}

/**
 * How alarming the path a probe is on should look.
 *
 * "auto" on the tunnel is a warning rather than a success: it works, and it
 * means the ordinary route is down and nobody has noticed.
 */
export function pathTone(
  mode: OverlayMode,
  state: string,
): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (mode === 'off') return 'neutral'
  if (state === 'down' || state === 'no_handshake') {
    return mode === 'on' ? 'danger' : 'warn'
  }
  if (state === 'tunnel') return mode === 'auto' ? 'warn' : 'ok'
  if (state === 'direct') return 'ok'
  return 'neutral'
}
