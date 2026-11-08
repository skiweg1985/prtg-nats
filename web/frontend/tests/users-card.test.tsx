import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { UsersCard } from '@/features/settings/UsersCard'
import { changeLanguage } from '@/i18n'

/**
 * Deleting a user ran on one click of a red button in the row. It is in the
 * row's menu now and asks first; nobody is offered actions on their own
 * account.
 */

const USERS = [
  {
    id: 'U1',
    username: 'admin',
    display_name: 'Admin',
    email: null,
    roles: ['administrator'],
    is_active: true,
    locale: 'en',
    last_login_at: null,
    locked_until: null,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'U2',
    username: 'deployer',
    display_name: 'Site Deployer',
    email: null,
    roles: ['deployer'],
    is_active: true,
    locale: 'en',
    last_login_at: null,
    locked_until: null,
    created_at: '2026-01-01T00:00:00Z',
  },
]

let deleted: string[] = []

const server = setupServer(
  http.get('/api/v1/auth/state', () =>
    HttpResponse.json({
      authenticated: true,
      setup_required: false,
      dev_auth: false,
      principal: {
        user_id: 'U1',
        username: 'admin',
        display_name: 'Admin',
        roles: ['administrator'],
        permissions: ['user.manage'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/users', () => HttpResponse.json(USERS)),
  http.delete('/api/v1/users/:id', ({ params }) => {
    deleted.push(String(params.id))
    return new HttpResponse(null, { status: 204 })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  deleted = []
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <UsersCard />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('deleting a user', () => {
  it('sits in the row menu and asks before it runs', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    // One menu: the other user's. Your own row offers nothing.
    await screen.findByText('Site Deployer')
    const menus = screen.getAllByRole('button', { name: 'More actions' })
    expect(menus).toHaveLength(1)

    await user.click(menus[0])
    await user.click(screen.getByRole('menuitem', { name: /delete/i }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Site Deployer is deleted/)).toBeInTheDocument()
    expect(deleted).toEqual([])

    await user.click(within(dialog).getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(deleted).toEqual(['U2']))
  })
})
