import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ActionMenu, Badge, DetailRow } from '@/components/ui/primitives'

/**
 * The overflow menu every row and toolbar uses. What matters is that it acts
 * like a menu - keyboard included - and that a destructive item is never the
 * first thing in it.
 */

function menu(onSelect = vi.fn()) {
  render(
    <ActionMenu
      label="More actions"
      actions={[
        { key: 'remove', label: 'Remove', tone: 'danger', onSelect },
        { key: 'rotate', label: 'Change password', onSelect },
        { key: 'ca', label: 'Install CA', onSelect },
      ]}
    />,
  )
  return onSelect
}

describe('the action menu', () => {
  it('opens on its button and puts destructive items last, after a divider', async () => {
    const user = userEvent.setup()
    menu()

    const trigger = screen.getByRole('button', { name: 'More actions' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await user.click(trigger)

    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const items = screen.getAllByRole('menuitem').map((item) => item.textContent)
    expect(items).toEqual(['Change password', 'Install CA', 'Remove'])
    expect(screen.getByRole('separator')).toBeInTheDocument()
  })

  it('moves with the arrows, picks with Enter and closes behind it', async () => {
    const user = userEvent.setup()
    const onSelect = menu()

    await user.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: 'Change password' })).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('menuitem', { name: 'Install CA' })).toHaveFocus()
    await user.keyboard('{ArrowUp}{ArrowUp}')
    expect(screen.getByRole('menuitem', { name: 'Remove' })).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('closes on Escape and gives the focus back to its button', async () => {
    const user = userEvent.setup()
    menu()

    const trigger = screen.getByRole('button', { name: 'More actions' })
    await user.click(trigger)
    await user.keyboard('{Escape}')

    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('renders nothing when there is nothing to offer', () => {
    render(<ActionMenu label="More actions" actions={[]} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('rows and states', () => {
  it('keeps a row action in the row, beside its value', () => {
    render(
      <dl>
        <DetailRow label="Overlay" actions={<button type="button">Change mode</button>}>
          Auto
        </DetailRow>
      </dl>,
    )
    const row = screen.getByText('Overlay', { selector: 'dt' }).parentElement as HTMLElement
    expect(row).toContainElement(screen.getByRole('button', { name: 'Change mode' }))
  })

  it('draws a state as text, not as something to press', () => {
    render(<Badge tone="ok">Connected</Badge>)
    const badge = screen.getByText('Connected')
    expect(badge.tagName).toBe('SPAN')
    expect(badge.className).not.toMatch(/\bborder\b|uppercase|hover:/)
  })
})
