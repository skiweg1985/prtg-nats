import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { Button, Dialog } from '@/components/ui/primitives'

/**
 * The three things eight hand-built overlays all got wrong.
 *
 * None of them is cosmetic: a dialog the keyboard can leave but not re-enter
 * is one an operator has to reach for the mouse to answer, and a dialog that
 * drops the focus on close leaves the next Tab starting from the top of the
 * page.
 */

function Harness() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open</Button>
      <Button>Behind</Button>
      {open && (
        <Dialog title="A question" onClose={() => setOpen(false)}>
          <Button>First</Button>
          <Button>Second</Button>
        </Dialog>
      )}
    </>
  )
}

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <Harness />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('Dialog', () => {
  it('takes the focus when it opens', async () => {
    const user = userEvent.setup()
    wrap()

    await user.click(screen.getByRole('button', { name: 'Open' }))
    // Not the page behind it: the first Tab has to start inside.
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    wrap()

    await user.click(screen.getByRole('button', { name: 'Open' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('keeps Tab inside', async () => {
    const user = userEvent.setup()
    wrap()

    await user.click(screen.getByRole('button', { name: 'Open' }))
    await user.tab()
    expect(screen.getByRole('button', { name: 'Second' })).toHaveFocus()

    // Past the last one, and round again - rather than on through the page
    // behind the overlay.
    await user.tab()
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus()

    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Second' })).toHaveFocus()
  })

  it('gives the focus back to whatever opened it', async () => {
    const user = userEvent.setup()
    wrap()

    const opener = screen.getByRole('button', { name: 'Open' })
    await user.click(opener)
    await user.keyboard('{Escape}')

    expect(opener).toHaveFocus()
  })

  it('closes on a click beside the panel, but not inside it', async () => {
    const user = userEvent.setup()
    wrap()

    await user.click(screen.getByRole('button', { name: 'Open' }))
    await user.click(screen.getByRole('button', { name: 'First' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByRole('dialog').parentElement!)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
