import type { ComponentPropsWithoutRef } from 'react'

import { useUrlChoice } from '@/utils/urlState'

const STATES = ['open', 'closed'] as const

/**
 * A `<details>` section whose open state lives in the address.
 *
 * Folded sections are where the detail somebody came for sits; a reload or a
 * link that closes them again loses exactly that. The parameter is written
 * only when the section differs from how the page opens it.
 */
export function UrlDetails({
  param,
  defaultOpen = false,
  children,
  ...rest
}: {
  param: string
  defaultOpen?: boolean
} & Omit<ComponentPropsWithoutRef<'details'>, 'open' | 'onToggle'>) {
  const [state, setState] = useUrlDetails(param, defaultOpen)
  return (
    <details
      {...rest}
      open={state}
      onToggle={(event) => {
        // The browser fires toggle for the prop change too; only a change
        // that differs from the address is written back.
        if (event.currentTarget.open !== state) setState(event.currentTarget.open)
      }}
    >
      {children}
    </details>
  )
}

/** The open state of a folded section, for a page that renders it itself. */
export function useUrlDetails(
  param: string,
  defaultOpen = false,
): [boolean, (open: boolean) => void] {
  const [state, setState] = useUrlChoice(param, STATES, defaultOpen ? 'open' : 'closed')
  return [state === 'open', (open) => setState(open ? 'open' : 'closed')]
}
