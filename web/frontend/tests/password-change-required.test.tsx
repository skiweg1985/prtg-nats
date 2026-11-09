import { FIELD } from './field-query'
import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { AuthGate } from '@/features/auth/AuthGate'
import { changeLanguage } from '@/i18n'

/**
 * An account created with an initial password has to replace it before it
 * sees anything else. The server enforces that; the gate follows the flag the
 * server reports on every state call, not only in the sign-in answer.
 */

function state(mustChange: boolean) {
  return {
    authenticated: true,
    setup_required: false,
    dev_auth: false,
    principal: {
      user_id: 'U2',
      username: 'operator',
      display_name: 'operator',
      roles: ['operator'],
      permissions: ['job.read'],
      locale: 'en',
      is_development: false,
      must_change_password: mustChange,
    },
  }
}

let mustChange = true
let submitted: unknown = null

const server = setupServer(
  http.get('/api/v1/auth/state', () => HttpResponse.json(state(mustChange))),
  http.post('/api/v1/auth/change-password', async ({ request }) => {
    submitted = await request.json()
    mustChange = false
    return new HttpResponse(null, { status: 204 })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  mustChange = true
  submitted = null
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <AuthGate>
        <p>application</p>
      </AuthGate>
    </AppProviders>,
  )
}

describe('a session that still owes a password change', () => {
  it('shows only the password change', async () => {
    await changeLanguage('en')
    wrap()

    expect(
      await screen.findByText('Your password has to be changed before you continue.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('application')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('reaches the application once the password is changed', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(await screen.findByLabelText('Current password', FIELD), 'initial-password')
    await user.type(screen.getByLabelText(/New password/, FIELD), 'a-much-better-passphrase')
    await user.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByText('application')).toBeInTheDocument()
    expect(submitted).toEqual({
      current_password: 'initial-password',
      new_password: 'a-much-better-passphrase',
    })
  })

  it('leaves an account without the flag alone', async () => {
    await changeLanguage('en')
    mustChange = false
    wrap()

    expect(await screen.findByText('application')).toBeInTheDocument()
  })
})
